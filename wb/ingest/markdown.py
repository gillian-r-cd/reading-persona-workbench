"""Plain text and Markdown parsers."""
from __future__ import annotations

import re
from pathlib import Path

from .base import add_block, add_section, detect_language, ensure_root, new_doc
from ..util import canonical

MD_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
CHAPTER = re.compile(r"^(第[一二三四五六七八九十百零〇]+[章篇节部]|Chapter\s+\d+\b|Part\s+[IVX]+|\d+[.)]\s+\S|\d+(\.\d+)+\s+\S|Abstract\s*$)")


def _front_matter(lines: list[str]) -> tuple[dict, int]:
    meta: dict = {}
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                return meta, i + 1
            if ":" in lines[i]:
                k, v = lines[i].split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"')
    return meta, 0


def parse_markdown(path: str | Path, doc_type: str = "book") -> dict:
    text = Path(path).read_text("utf-8", errors="replace")
    lines = text.replace("\r\n", "\n").split("\n")
    meta, start = _front_matter(lines)
    body_lines = lines[start:]

    title = meta.get("title") or ""
    author = meta.get("author") or ""
    if not title:
        for ln in body_lines:
            m = MD_HEAD.match(ln)
            if m and len(m.group(1)) == 1:
                title = m.group(2)
                break
    if not author:
        for ln in body_lines[:12]:
            m = re.match(r"^\s*(作者|Author)[:：]\s*(.+)$", ln)
            if m:
                author = m.group(2)
                break

    doc = new_doc(title=title or Path(path).stem, author=author, doc_type=doc_type,
                  language="", source=str(path))
    stack: list[tuple[int, int]] = []
    cur = add_section(doc, title or Path(path).stem, level=0, kind="root")

    buf: list[str] = []
    kind = "para"

    def flush() -> None:
        nonlocal buf
        if buf:
            add_block(doc, cur, "\n".join(buf) if kind == "code" else " ".join(buf), kind=kind)
        buf = []

    in_code = False
    for ln in body_lines:
        if ln.strip().startswith("```"):
            flush()
            in_code = not in_code
            kind = "code" if in_code else "para"
            continue
        if in_code:
            buf.append(ln)
            continue
        m = MD_HEAD.match(ln)
        if m:
            flush()
            level = len(m.group(1))
            if level == 1 and m.group(2).strip() == title and not doc["blocks"]:
                add_block(doc, cur, m.group(2), kind="heading", attrs={"level": 1})
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            cur = add_section(doc, m.group(2), level=level, kind="chapter")
            stack.append((level, cur))
            add_block(doc, cur, m.group(2), kind="heading", attrs={"level": level})
            continue
        if not ln.strip():
            flush()
            continue
        lm = LIST.match(ln)
        if lm:
            flush()
            add_block(doc, cur, lm.group(2), kind="list_item")
            continue
        qm = QUOTE.match(ln)
        if qm:
            flush()
            add_block(doc, cur, qm.group(1), kind="quote")
            continue
        if ln.strip().startswith("|") and ln.strip().endswith("|"):
            flush()
            add_block(doc, cur, ln.strip(), kind="table_row")
            continue
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", ln):
            flush()
            continue
        if not buf:
            kind = "para"
        buf.append(ln.strip())
    flush()
    if not doc["sections"]:
        ensure_root(doc, title)
    full = " ".join(b["text"] for b in doc["blocks"])
    doc["language"] = detect_language(full)
    return doc


def parse_text(path: str | Path, doc_type: str = "report") -> dict:
    text = Path(path).read_text("utf-8", errors="replace")
    lines = text.replace("\r\n", "\n").split("\n")
    title = Path(path).stem
    for ln in lines[:5]:
        if ln.strip():
            title = ln.strip()[:80]
            break
    author = ""
    for ln in lines[:10]:
        m = re.match(r"^\s*(作者|Author)[:：]\s*(.+)$", ln)
        if m:
            author = m.group(2)
    doc = new_doc(title=title, author=author, doc_type=doc_type, language="", source=str(path))
    cur = add_section(doc, title, level=0, kind="root")
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            add_block(doc, cur, " ".join(buf))
        buf = []

    for ln in lines:
        s = ln.strip()
        if not s:
            flush()
            continue
        if CHAPTER.match(s) and len(s) < 80:
            flush()
            cur = add_section(doc, s, level=1, kind="chapter")
            add_block(doc, cur, s, kind="heading", attrs={"level": 1})
            continue
        buf.append(s)
    flush()
    full = " ".join(b["text"] for b in doc["blocks"])
    doc["language"] = detect_language(full)
    return doc
