"""Build binary fixtures (docx/epub/pdf) from the markdown corpus."""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from wb.ingest.pdf_writer import PDFBuilder, TextItem, write_pdf

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"
GEN = CORPUS / "generated"


def split_md(path: Path) -> tuple[str, str, list[tuple[int, str, list[str]]]]:
    lines = path.read_text("utf-8").split("\n")
    title = author = ""
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            if ":" in lines[i]:
                k, v = lines[i].split(":", 1)
                if k.strip() == "title":
                    title = v.strip()
                elif k.strip() == "author":
                    author = v.strip()
            i += 1
        i += 1
    sections: list[tuple[int, str, list[str]]] = []
    cur = None
    buf: list[str] = []
    for ln in lines[i:]:
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            if cur is not None:
                sections.append((cur[0], cur[1], buf))
            cur = (len(m.group(1)), m.group(2).strip())
            buf = []
        elif ln.strip():
            buf.append(ln.strip())
    if cur is not None:
        sections.append((cur[0], cur[1], buf))
    return title, author, sections


def build_docx(src: Path, out: Path) -> None:
    import docx
    title, author, sections = split_md(src)
    d = docx.Document()
    d.core_properties.title = title
    d.core_properties.author = author
    d.add_heading(title, level=0)
    for level, heading, paras in sections:
        if level > 0:
            d.add_heading(heading, level=min(level, 4))
        for p in paras:
            d.add_paragraph(p)
    d.save(str(out))


XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh">
<head><title>{title}</title></head>
<body>
{body}
</body>
</html>
"""


def build_epub(src: Path, out: Path) -> None:
    title, author, sections = split_md(src)
    chapters = []
    for idx, (level, heading, paras) in enumerate(sections):
        if level == 0:
            continue
        body = [f"<h1>{heading}</h1>"] + [f"<p>{p}</p>" for p in paras]
        chapters.append((heading, "\n".join(body)))
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
        manifest = []
        spine = []
        for i, (heading, body) in enumerate(chapters):
            name = f"chap{i:02d}.xhtml"
            z.writestr(f"OEBPS/{name}", XHTML.format(title=heading, body=body))
            manifest.append(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
        z.writestr("OEBPS/content.opf", f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:uuid:test-remote-work</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>{''.join(manifest)}</manifest>
  <spine>{''.join(spine)}</spine>
</package>""")


def _wrap(text: str, width: float, size: float) -> list[str]:
    cpl = max(10, int(width / (size * 0.52)))
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) <= cpl:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def build_pdf(src: Path, out: Path, expected_order_path: Path) -> None:
    text = src.read_text("utf-8")
    # split into paragraphs by blank lines
    paras = [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", text) if p.strip()]
    title = paras[0]
    bodies = paras[1:]
    pages: list[list[TextItem]] = []
    page: list[TextItem] = []
    order: list[str] = []

    PW, PH = 595, 400
    margin = 55
    col_w = (PW - 2 * margin - 24) / 2
    left_x = margin
    right_x = margin + col_w + 24
    top = 360
    bottom = 70
    body_size = 9.5
    leading = 12.5

    # title + abstract full width on page 1
    y = top
    for seg in _wrap(title, PW - 2 * margin, 17):
        page.append(TextItem(margin, y, 17, seg, bold=True))
        y -= 22
    y -= 8

    def new_page() -> None:
        nonlocal page, y
        pages.append(page)
        page = []
        y = top

    col_idx = 0
    x = left_x
    y = y

    def next_col() -> None:
        nonlocal col_idx, x, y
        if col_idx == 0:
            col_idx = 1
            x = right_x
            y = top
        else:
            new_page()
            col_idx = 0
            x = left_x
            y = top

    for p in bodies:
        is_heading = bool(re.match(r"^(\d+\.|Abstract|References)", p)) and len(p) < 80
        size = 12 if is_heading else body_size
        bold = is_heading
        lines = _wrap(p, col_w, size)
        need = len(lines) * (leading + (2 if is_heading else 0)) + (8 if is_heading else 4)
        if y - need < bottom:
            next_col()
        for ln in lines:
            page.append(TextItem(x, y, size, ln, bold=bold))
            y -= leading + (2 if is_heading else 0)
        y -= 8 if is_heading else 5
        order.append(p)
    pages.append(page)

    # footnote on page 1
    pages[0].append(TextItem(left_x, 40, 7.5, "* We thank the anonymous reviewers for helpful comments."))

    write_pdf(out, pages, title=title.split(":")[0][:60], author="A. Researcher", page_size=(PW, PH))
    expected_order_path.write_text(json.dumps(order, ensure_ascii=False, indent=1), "utf-8")


def build_all(force: bool = False) -> dict[str, Path]:
    GEN.mkdir(parents=True, exist_ok=True)
    src_md = CORPUS / "remote_work.md"
    outs = {
        "docx": GEN / "remote_work.docx",
        "epub": GEN / "remote_work.epub",
        "pdf": GEN / "platform_economics.pdf",
    }
    if force or not outs["docx"].exists():
        build_docx(src_md, outs["docx"])
    if force or not outs["epub"].exists():
        build_epub(src_md, outs["epub"])
    if force or not outs["pdf"].exists():
        build_pdf(CORPUS / "platform_economics.txt", outs["pdf"], GEN / "pdf_expected_order.json")
    return outs


if __name__ == "__main__":
    print(build_all(force=True))
