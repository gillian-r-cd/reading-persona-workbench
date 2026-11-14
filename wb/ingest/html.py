"""HTML parser (also used for EPUB spine items)."""
from __future__ import annotations

from pathlib import Path

from lxml import html as LH

from .base import add_block, add_section, detect_language, new_doc
from ..util import canonical

SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg", "iframe"}
HANDLED = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "figcaption", "caption", "pre"}


def _clean(el) -> str:
    return canonical(el.text_content() or "")


def _has_handled_ancestor(el) -> bool:
    p = el.getparent()
    while p is not None:
        if p.tag in HANDLED:
            return True
        p = p.getparent()
    return False


def _remove_noise(root) -> None:
    for el in list(root.iter()):
        if isinstance(el.tag, str) and el.tag.lower() in SKIP:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)


def parse_html_text(text: str, title: str = "", author: str = "",
                    doc_type: str = "article", source: str = "",
                    start_section: str | None = None) -> dict:
    try:
        root = LH.fromstring(text)
    except Exception:
        root = LH.fromstring("<html><body><p>" + text + "</p></body></html>")
    _remove_noise(root)
    if not title:
        t = root.find(".//title")
        title = canonical(t.text_content()) if t is not None else ""
    h1 = root.find(".//h1")
    if not title and h1 is not None:
        title = _clean(h1)
    doc = new_doc(title=title or "未命名", author=author, doc_type=doc_type, source=source)
    cur = add_section(doc, title or "未命名", level=0, kind="root")
    if start_section:
        cur = add_section(doc, start_section, level=1, kind="chapter")

    body = root.find(".//body")
    scope = body if body is not None else root
    for el in scope.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        tag = tag.lower()
        if tag not in HANDLED:
            continue
        if _has_handled_ancestor(el):
            continue
        txt = _clean(el)
        if not txt:
            continue
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            if level == 1 and txt == doc["title"] and not doc["blocks"]:
                add_block(doc, cur, txt, kind="heading", attrs={"level": 1})
                continue
            cur = add_section(doc, txt, level=level, kind="chapter")
            add_block(doc, cur, txt, kind="heading", attrs={"level": level})
        elif tag == "li":
            add_block(doc, cur, txt, kind="list_item")
        elif tag == "blockquote":
            add_block(doc, cur, txt, kind="quote")
        elif tag in ("figcaption", "caption"):
            add_block(doc, cur, txt, kind="caption")
        elif tag == "pre":
            add_block(doc, cur, txt, kind="code")
        else:
            add_block(doc, cur, txt, kind="para")
    doc["language"] = detect_language(" ".join(b["text"] for b in doc["blocks"]))
    return doc


def parse_html(path: str | Path, doc_type: str = "article") -> dict:
    p = Path(path)
    text = p.read_text("utf-8", errors="replace")
    return parse_html_text(text, doc_type=doc_type, source=str(p))
