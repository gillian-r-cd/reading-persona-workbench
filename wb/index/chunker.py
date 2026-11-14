"""Chunking + index building (FTS5 + deterministic embeddings)."""
from __future__ import annotations

import json
import struct

import numpy as np

from ..ingest.pipeline import blocks_of
from ..util import embed, estimate_tokens, fts_text, stable_id

DIM = 384
MAX_TOKENS = 260


def _section_map(db, doc_id: str) -> dict[str, dict]:
    return {r["id"]: dict(r) for r in db.q("SELECT * FROM sections WHERE doc_id=?", (doc_id,))}


def build_index(db, doc_id: str) -> int:
    blocks = blocks_of(db, doc_id)
    secs = _section_map(db, doc_id)
    ref_sections = {sid for sid, s in secs.items()
                    if ("参考文献" in s["title"] or "references" in s["title"].lower())}
    blocks = [b for b in blocks if b["section_id"] not in ref_sections]
    db.x("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    db.x("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))

    groups: list[dict] = []
    cur: list[dict] = []
    cur_tokens = 0
    for b in blocks:
        if b["kind"] in ("footnote", "table_row") and not cur:
            continue
        t = estimate_tokens(b["text"])
        if cur and (cur_tokens + t > MAX_TOKENS or b["section_id"] != cur[0]["section_id"]):
            groups.append(_make_group(doc_id, cur, secs))
            if b["section_id"] == cur[0]["section_id"]:
                # one block overlap for continuity, but never across sections
                cur = [cur[-1]]
                cur_tokens = estimate_tokens(cur[0]["text"])
            else:
                cur = []
                cur_tokens = 0
        cur.append(b)
        cur_tokens += t
    if cur:
        groups.append(_make_group(doc_id, cur, secs))

    for g in groups:
        vec = embed(g["text"], DIM)
        blob = struct.pack(f"<{DIM}f", *vec)
        db.insert("chunks", {
            "id": g["id"], "doc_id": doc_id, "section_id": g["section_id"], "level": "unit",
            "block_ids": json.dumps(g["block_ids"]), "text": g["text"],
            "token_count": g["token_count"], "embedding": blob, "meta": "{}",
        })
        db.insert("chunks_fts", {"text": fts_text(g["text"]), "chunk_id": g["id"], "doc_id": doc_id})

    # section-level parents
    for sid, s in secs.items():
        text = " ".join(b["text"] for b in blocks if b["section_id"] == sid)
        if not text:
            continue
        cid = stable_id("chk_s", doc_id, sid, n=14)
        db.insert("chunks", {
            "id": cid, "doc_id": doc_id, "section_id": sid, "level": "section",
            "block_ids": json.dumps([b["id"] for b in blocks if b["section_id"] == sid]),
            "text": text[:4000], "token_count": estimate_tokens(text[:4000]),
            "embedding": struct.pack(f"<{DIM}f", *embed(text[:4000], DIM)), "meta": "{}",
        })
    return len(groups)


def _make_group(doc_id: str, blocks: list[dict], secs: dict) -> dict:
    text = "\n".join(b["text"] for b in blocks)
    return {
        "id": stable_id("chk", doc_id, blocks[0]["id"], blocks[-1]["id"], n=14),
        "section_id": blocks[0]["section_id"],
        "block_ids": [b["id"] for b in blocks],
        "text": text,
        "token_count": estimate_tokens(text),
        "title": secs.get(blocks[0]["section_id"], {}).get("title", ""),
    }


def load_chunks(db, doc_ids: list[str]) -> list[dict]:
    if not doc_ids:
        return []
    ph = ",".join("?" for _ in doc_ids)
    rows = db.q(f"SELECT * FROM chunks WHERE doc_id IN ({ph}) AND level='unit'", tuple(doc_ids))
    out = []
    for r in rows:
        d = dict(r)
        d["block_ids"] = json.loads(d["block_ids"])
        d["vec"] = np.frombuffer(d["embedding"], dtype=np.float32)
        out.append(d)
    return out
