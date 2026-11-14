"""EPUB parser: zip -> container.xml -> OPF spine -> XHTML items."""
from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path

from lxml import etree

from .base import add_block, add_section, detect_language, new_doc
from .html import parse_html_text

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def parse_epub(path: str | Path) -> dict:
    p = Path(path)
    with zipfile.ZipFile(p) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", NS)
        opf_path = rootfile.get("full-path") if rootfile is not None else ""
        if not opf_path:
            raise ValueError("EPUB 缺少 OPF")
        opf = etree.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        title = (opf.findtext(".//dc:title", namespaces=NS) or "").strip()
        author = (opf.findtext(".//dc:creator", namespaces=NS) or "").strip()
        language = (opf.findtext(".//dc:language", namespaces=NS) or "").strip()
        manifest = {}
        for item in opf.findall(".//opf:manifest/opf:item", NS):
            manifest[item.get("id")] = item.get("href")
        spine = [ref.get("idref") for ref in opf.findall(".//opf:spine/opf:itemref", NS)]

        doc = new_doc(title=title or p.stem, author=author, doc_type="book",
                      language=language or "", source=str(p))
        root = add_section(doc, title or p.stem, level=0, kind="root")
        cur = root
        for idref in spine:
            href = manifest.get(idref)
            if not href:
                continue
            zip_path = posixpath.normpath(posixpath.join(base, href))
            try:
                raw = z.read(zip_path)
            except KeyError:
                continue
            sub = parse_html_text(raw.decode("utf-8", "replace"), doc_type="book")
            subs = [s for s in sub["sections"] if s["level"] > 0]
            if not subs:
                # no headings: one section per spine item
                head = next((b["text"] for b in sub["blocks"] if b["kind"] == "heading"), "")
                cur = add_section(doc, head or f"第 {idref} 节", level=1, kind="chapter")
                for b in sub["blocks"]:
                    add_block(doc, cur, b["text"], b["kind"], b.get("page_from"), b.get("page_to"),
                              b.get("attrs"))
                continue
            mapping: dict[int, int] = {}
            for s in subs:
                mapping[s["index"]] = add_section(doc, s["title"], level=s["level"], kind="chapter")
            for b in sub["blocks"]:
                target = mapping.get(b["section_index"], cur)
                add_block(doc, target, b["text"], b["kind"], b.get("page_from"), b.get("page_to"),
                          b.get("attrs"))
            cur = mapping[subs[-1]["index"]]

    full = " ".join(b["text"] for b in doc["blocks"])
    doc["language"] = detect_language(full) if not doc["language"] else doc["language"]
    return doc
