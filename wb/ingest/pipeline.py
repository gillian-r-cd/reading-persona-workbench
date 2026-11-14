"""Ingestion pipeline: parse -> stable IDs -> store -> apply overrides."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import Config
from ..db import DB
from ..util import canonical, match_key, sha256_bytes, stable_id
from . import docx_parser, epub, html as html_parser, markdown, pdf_extract, text as text_parser

EXT_PARSERS = {
    ".md": lambda p: markdown.parse_markdown(p),
    ".markdown": lambda p: markdown.parse_markdown(p),
    ".txt": lambda p: text_parser.parse_text(p),
    ".text": lambda p: text_parser.parse_text(p),
    ".html": lambda p: html_parser.parse_html(p),
    ".htm": lambda p: html_parser.parse_html(p),
    ".xhtml": lambda p: html_parser.parse_html(p),
    ".epub": lambda p: epub.parse_epub(p),
    ".docx": lambda p: docx_parser.parse_docx(p),
    ".pdf": lambda p: pdf_extract.extract(p),
}


class IngestError(Exception):
    """A user-actionable ingestion failure."""


def supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in EXT_PARSERS


def parse_file(path: str | Path) -> dict:
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in EXT_PARSERS:
        raise IngestError(f"不支持的格式: {ext}")
    try:
        parsed = EXT_PARSERS[ext](p)
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"{ext} 解析失败: {exc}") from exc
    if not parsed.get("blocks"):
        raise IngestError(f"{ext} 未能提取到任何文本（可能是扫描件或损坏文件）")
    return parsed


def _section_paths(sections: list[dict]) -> list[str]:
    out: list[str] = []
    counters: dict[int, int] = {}
    for s in sections:
        lvl = s["level"]
        counters[lvl] = counters.get(lvl, 0) + 1
        for deeper in [k for k in counters if k > lvl]:
            counters.pop(deeper, None)
        parts = [str(counters[k]) for k in sorted(counters) if k <= lvl]
        out.append(".".join(parts) if parts else "0")
    return out


def ingest(cfg: Config, db: DB, path: str | Path, doc_type: str | None = None) -> str:
    p = Path(path)
    data = p.read_bytes()
    src_sha = sha256_bytes(data)
    existing = db.one("SELECT id FROM documents WHERE source_sha=?", (src_sha,))
    if existing:
        return existing["id"]
    parsed = parse_file(p)
    if doc_type:
        parsed["doc_type"] = doc_type
    doc_id = stable_id("d", src_sha, n=12)
    title = parsed.get("title") or p.stem
    author = parsed.get("author") or ""
    language = parsed.get("language") or "zh"

    dest = cfg.path("originals", f"{src_sha}{p.suffix.lower()}")
    if not dest.exists():
        shutil.copyfile(p, dest)

    db.insert("documents", {
        "id": doc_id, "title": title, "author": author, "doc_type": parsed.get("doc_type", "article"),
        "language": language, "source_path": str(dest), "source_sha": src_sha, "version": 1,
        "created_at": __import__("wb.util", fromlist=["now_iso"]).now_iso(),
        "meta": json.dumps({"original_name": p.name}, ensure_ascii=False), "status": "ingesting",
    })

    paths = _section_paths(parsed["sections"])
    sec_ids: list[str] = []
    for s, path_str in zip(parsed["sections"], paths):
        sid = stable_id("s", src_sha, path_str, n=12)
        sec_ids.append(sid)
        db.insert("sections", {
            "id": sid, "doc_id": doc_id, "parent_id": "",
            "kind": s.get("kind", "section"), "title": s["title"], "ordinal": s["index"],
            "path": path_str, "level": s["level"],
        })

    for i, b in enumerate(parsed["blocks"]):
        sec_idx = b.get("section_index", 0)
        sid = sec_ids[sec_idx] if 0 <= sec_idx < len(sec_ids) else (sec_ids[0] if sec_ids else "")
        path_str = paths[sec_idx] if 0 <= sec_idx < len(paths) else "0"
        text = canonical(b["text"])
        bid = stable_id("b", src_sha, path_str, str(i), match_key(text)[:80], n=14)
        db.insert("blocks", {
            "id": bid, "doc_id": doc_id, "section_id": sid, "kind": b.get("kind", "para"),
            "ordinal": i, "text": text, "norm": match_key(text),
            "page_from": b.get("page_from"), "page_to": b.get("page_to"),
            "attrs": json.dumps(b.get("attrs", {}), ensure_ascii=False), "hash": src_sha,
        })
    apply_overrides(db, doc_id)
    db.x("UPDATE documents SET status='ready' WHERE id=?", (doc_id,))
    return doc_id


def apply_overrides(db: DB, doc_id: str) -> None:
    rows = db.q("SELECT * FROM overrides WHERE doc_id=? ORDER BY created_at", (doc_id,))
    for r in rows:
        patch = json.loads(r["patch"] or "{}")
        if patch.get("text"):
            text = canonical(patch["text"])
            db.x("UPDATE blocks SET text=?, norm=? WHERE id=? AND doc_id=?",
                 (text, match_key(text), r["target_id"], doc_id))
        if patch.get("kind"):
            db.x("UPDATE blocks SET kind=? WHERE id=? AND doc_id=?", (patch["kind"], r["target_id"], doc_id))
        if patch.get("section_id"):
            db.x("UPDATE blocks SET section_id=? WHERE id=? AND doc_id=?",
                 (patch["section_id"], r["target_id"], doc_id))


def blocks_of(db: DB, doc_id: str) -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM blocks WHERE doc_id=? ORDER BY ordinal", (doc_id,))]


def block_map(db: DB, doc_id: str) -> dict[str, dict]:
    return {b["id"]: b for b in blocks_of(db, doc_id)}


def text_map(db: DB, doc_id: str) -> dict[str, str]:
    return {b["id"]: b["text"] for b in blocks_of(db, doc_id)}
