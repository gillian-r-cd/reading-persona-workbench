"""DOCX parser using python-docx."""
from __future__ import annotations

from pathlib import Path

import docx

from .base import add_block, add_section, detect_language, new_doc


def _heading_level(style_name: str) -> int:
    s = (style_name or "").lower()
    if s.startswith("heading"):
        parts = s.split()
        if parts and parts[-1].isdigit():
            return max(1, min(9, int(parts[-1])))
        return 1
    if s == "title":
        return 0
    return 0


def parse_docx(path: str | Path) -> dict:
    p = Path(path)
    d = docx.Document(str(p))
    core = d.core_properties
    title = (core.title or "").strip() or p.stem
    author = (core.author or "").strip()
    doc = new_doc(title=title, author=author, doc_type="report", source=str(p))
    cur = add_section(doc, title, level=0, kind="root")
    for para in d.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        lvl = _heading_level(para.style.name if para.style else "")
        if lvl > 0:
            cur = add_section(doc, text, level=lvl, kind="chapter")
            add_block(doc, cur, text, kind="heading", attrs={"level": lvl})
            continue
        if lvl == 0 and (para.style and para.style.name.lower() == "title"):
            continue
        style = (para.style.name if para.style else "").lower()
        kind = "list_item" if "list" in style else "para"
        add_block(doc, cur, text, kind=kind)
    for ti, table in enumerate(d.tables):
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            add_block(doc, cur, "\n".join(rows), kind="table", attrs={"table_index": ti})
    doc["language"] = detect_language(" ".join(b["text"] for b in doc["blocks"]))
    return doc
