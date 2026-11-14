"""Common parser output contract."""
from __future__ import annotations

import re

from ..util import canonical

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def detect_language(text: str) -> str:
    cjk = len(CJK.findall(text[:20000]))
    latin = len(re.findall(r"[A-Za-z]", text[:20000]))
    if cjk > latin * 0.6:
        return "zh"
    if latin > cjk * 4:
        return "en"
    return "mixed"


def new_doc(title: str = "", author: str = "", doc_type: str = "article",
            language: str = "", source: str = "") -> dict:
    return {
        "title": title.strip() or "未命名",
        "author": author.strip(),
        "doc_type": doc_type,
        "language": language or "zh",
        "source": source,
        "sections": [],
        "blocks": [],
    }


def add_section(doc: dict, title: str, level: int = 1, kind: str = "section",
                page: int | None = None) -> int:
    idx = len(doc["sections"])
    doc["sections"].append({
        "index": idx,
        "title": canonical(title) or f"第 {idx + 1} 节",
        "level": max(0, level),
        "kind": kind,
        "page": page,
    })
    return idx


def add_block(doc: dict, section_index: int, text: str, kind: str = "para",
              page_from: int | None = None, page_to: int | None = None,
              attrs: dict | None = None) -> None:
    text = canonical(text)
    if not text:
        return
    doc["blocks"].append({
        "section_index": section_index,
        "kind": kind,
        "text": text,
        "page_from": page_from,
        "page_to": page_to if page_to is not None else page_from,
        "attrs": attrs or {},
    })


def ensure_root(doc: dict, title: str | None = None) -> int:
    if doc["sections"]:
        return 0
    return add_section(doc, title or doc["title"], level=0, kind="root")
