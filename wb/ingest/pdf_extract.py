"""PDF layout reconstruction: lines -> columns -> paragraphs -> sections."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from .base import add_block, add_section, detect_language, new_doc
from .pdf_reader import PDFDoc, _content_runs

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PAGE_NO_RE = re.compile(r"^[\-\u2013\u2014\s]*\d{1,4}[\-\u2013\u2014\s]*$")
HEADING_NUM_RE = re.compile(r"^((第[一二三四五六七八九十百]+[章节篇])|(\d+(\.\d+){0,3})\s|(Chapter\s+\d+))", re.I)


@dataclass
class Line:
    page: int
    x0: float
    x1: float
    y: float
    size: float
    text: str
    col: int = 0

    @property
    def width(self) -> float:
        return self.x1 - self.x0


def _mediabox(doc: PDFDoc, page: dict) -> tuple[float, float, float, float]:
    node = page
    for _ in range(8):
        mb = doc.get(node.get("/MediaBox")) if isinstance(node, dict) else None
        if isinstance(mb, list) and len(mb) == 4:
            try:
                return tuple(float(v) for v in mb)  # type: ignore[return-value]
            except Exception:
                break
        node = doc.get(node.get("/Parent")) if isinstance(node, dict) else None
    return 0.0, 0.0, 595.0, 842.0


def _is_cjk(ch: str) -> bool:
    return bool(ch) and bool(CJK_RE.match(ch))


def _join(a: str, b: str, gap: float, size: float) -> str:
    if not a:
        return b
    if not b:
        return a
    if gap > max(size * 0.25, 1.2):
        if _is_cjk(a[-1]) or _is_cjk(b[0]):
            return a + b
        return a + " " + b
    if _is_cjk(a[-1]) or _is_cjk(b[0]):
        return a + b
    return a + " " + b


def _runs_to_lines(runs, page_no: int) -> list[Line]:
    if not runs:
        return []
    runs = sorted(runs, key=lambda r: (-round(r.y, 1), r.x))
    groups: list[list] = []
    cur = [runs[0]]
    for r in runs[1:]:
        tol = max(2.0, cur[-1].size * 0.55)
        if abs(r.y - cur[-1].y) <= tol:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)

    lines: list[Line] = []
    for g in groups:
        g.sort(key=lambda r: r.x)
        chunks: list[list] = [[g[0]]]
        for r in g[1:]:
            prev = chunks[-1][-1]
            if r.x - prev.x > max(prev.size * 2.2, 18):
                chunks.append([r])
            else:
                chunks[-1].append(r)
        for ch in chunks:
            text = ""
            for i, r in enumerate(ch):
                if i == 0:
                    text = r.text
                else:
                    text = _join(text, r.text, r.x - ch[i - 1].x, r.size)
            text = text.strip()
            if not text:
                continue
            lines.append(Line(
                page=page_no,
                x0=min(r.x for r in ch),
                x1=max(r.x for r in ch) + max(r.size * 0.5, 2),
                y=max(r.y for r in ch),
                size=max(r.size for r in ch),
                text=text,
            ))
    return lines


def _body_size(lines: list[Line]) -> float:
    sizes: list[float] = []
    for ln in lines:
        sizes.extend([ln.size] * max(1, min(len(ln.text), 80)))
    if not sizes:
        return 10.0
    return statistics.median(sizes)


def _drop_noise(pages: list[list[Line]], boxes) -> list[list[Line]]:
    counts: dict[str, int] = {}
    for lines in pages:
        for ln in lines:
            key = re.sub(r"\d+", "#", ln.text.strip().lower())
            if len(key) > 60:
                continue
            counts[key] = counts.get(key, 0) + 1
    npages = max(1, len(pages))
    out = []
    for pi, lines in enumerate(pages):
        top = boxes[pi][3]
        height = top - boxes[pi][1]
        kept = []
        for ln in lines:
            near_edge = ln.y < height * 0.07 or ln.y > height * 0.94
            if near_edge and PAGE_NO_RE.match(ln.text.strip()):
                continue
            key = re.sub(r"\d+", "#", ln.text.strip().lower())
            if len(key) <= 60 and counts.get(key, 0) >= max(3, npages * 0.6) and near_edge:
                continue
            kept.append(ln)
        out.append(kept)
    return out


def _order_page(lines: list[Line], width: float) -> list[Line]:
    if len(lines) < 4:
        return sorted(lines, key=lambda l: (-l.y, l.x0))
    mid = width * 0.5
    left = [l for l in lines if l.x1 <= mid + 4]
    right = [l for l in lines if l.x0 >= mid - 4]
    two_col = len(left) >= 3 and len(right) >= 3
    if two_col:
        crossing = [l for l in lines if not (l.x1 <= mid + 4) and not (l.x0 >= mid - 4)]
        if len(crossing) > len(lines) * 0.25:
            two_col = False
    if not two_col:
        for l in lines:
            l.col = 0
        return sorted(lines, key=lambda l: (-l.y, l.x0))

    out: list[Line] = []
    band: list[Line] = []

    def flush() -> None:
        if not band:
            return
        lefts = sorted([l for l in band if l.x1 <= mid + 4], key=lambda l: (-l.y, l.x0))
        rights = sorted([l for l in band if l.x1 > mid + 4], key=lambda l: (-l.y, l.x0))
        for l in lefts:
            l.col = 0
        for l in rights:
            l.col = 1
        out.extend(lefts)
        out.extend(rights)
        band.clear()

    for ln in sorted(lines, key=lambda l: (-l.y, l.x0)):
        if ln.x0 < mid - 4 and ln.x1 > mid + 4:
            flush()
            ln.col = -1
            out.append(ln)
        else:
            band.append(ln)
    flush()
    return out


def _heading_level(size: float, body: float, text: str) -> int:
    ratio = size / body if body else 1.0
    if ratio >= 1.45:
        return 1
    if ratio >= 1.18:
        return 2
    if ratio >= 1.08:
        return 3
    if HEADING_NUM_RE.match(text) and len(text) < 40:
        return 2
    return 0


def _decode_info(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace") if v[:2] not in (b"\xfe\xff",) else v.decode("utf-16", "replace")
    if isinstance(v, str):
        return v
    return ""


def extract(path: str | Path) -> dict:
    data = Path(path).read_bytes()
    pdf = PDFDoc(data)
    pages = pdf.pages()
    raw_pages: list[list[Line]] = []
    boxes = []
    for i, page in enumerate(pages):
        boxes.append(_mediabox(pdf, page))
        raw_pages.append(_runs_to_lines(_content_runs(pdf, page, i), i))
    raw_pages = _drop_noise(raw_pages, boxes)

    all_runs_text = " ".join(ln.text for lines in raw_pages for ln in lines)
    lang = detect_language(all_runs_text)

    info: dict = {}
    for num in sorted(pdf.objs):
        o = pdf.objs[num]
        if isinstance(o, dict) and "/Title" in o and "/Producer" in o:
            info = o
    title = _decode_info(pdf.get(info.get("/Title"))).strip()
    author = _decode_info(pdf.get(info.get("/Author"))).strip()
    title = title or Path(path).stem
    doc = new_doc(title=title, author=author, doc_type="report", language=lang, source=str(path))
    root = add_section(doc, title, level=0, kind="root")

    body = _body_size([ln for lines in raw_pages for ln in lines])
    cur_sec = root
    para: list[str] = []
    para_page: tuple[int, int] | None = None
    para_size = body
    prev: Line | None = None
    title_done = False

    def flush_para() -> None:
        nonlocal para, para_page, para_size
        if para:
            sep = "" if lang == "zh" else " "
            add_block(doc, cur_sec, sep.join(para), kind="para",
                      page_from=para_page[0] if para_page else None,
                      page_to=para_page[1] if para_page else None,
                      attrs={"size": round(para_size, 1)})
        para = []
        para_page = None

    for pi, page in enumerate(pages):
        height = boxes[pi][3] - boxes[pi][1]
        lines = raw_pages[pi]
        body_lines = [l for l in lines if not (l.size < body * 0.88 and l.y < height * 0.25)]
        foot_lines = [l for l in lines if (l.size < body * 0.88 and l.y < height * 0.25)]
        ordered = _order_page(body_lines, boxes[pi][2] - boxes[pi][0])

        # document title: leading large-type lines on page 1
        if pi == 0 and not title_done and ordered:
            idx = 0
            title_parts = []
            while idx < len(ordered) and ordered[idx].size >= body * 1.5:
                title_parts.append(ordered[idx].text)
                idx += 1
            if title_parts:
                merged = (" " if lang != "zh" else "").join(title_parts)
                add_block(doc, root, merged, kind="title", page_from=1,
                          attrs={"size": round(ordered[0].size, 1)})
                if not doc["title"] or doc["title"] == Path(path).stem:
                    doc["title"] = merged
                ordered = ordered[idx:]
            title_done = True

        for ln in ordered:
            level = _heading_level(ln.size, body, ln.text)
            if level:
                flush_para()
                cur_sec = add_section(doc, ln.text, level=level, kind="chapter", page=pi + 1)
                add_block(doc, cur_sec, ln.text, kind="heading", page_from=pi + 1,
                          attrs={"level": level, "size": round(ln.size, 1)})
                prev = ln
                continue
            if PAGE_NO_RE.match(ln.text.strip()) and len(ln.text.strip()) <= 5:
                continue
            new_para = False
            if para and prev is not None:
                if prev.page != ln.page or prev.col != ln.col:
                    new_para = True
                elif abs(ln.size - para_size) > 0.6:
                    new_para = True
                elif prev.y - ln.y > body * 1.75:
                    new_para = True
            if new_para:
                flush_para()
            if not para:
                para_page = (pi + 1, pi + 1)
                para_size = ln.size
            else:
                para_page = (para_page[0], pi + 1)
            para.append(ln.text)
            prev = ln
        flush_para()
        prev = None
        if foot_lines:
            ftext = " ".join(l.text for l in sorted(foot_lines, key=lambda l: -l.y))
            add_block(doc, cur_sec, ftext, kind="footnote", page_from=pi + 1,
                      attrs={"size": round(min(l.size for l in foot_lines), 1)})
    if doc["title"] and doc["sections"][0]["title"] in (Path(path).stem, ""):
        doc["sections"][0]["title"] = doc["title"]
    return doc
