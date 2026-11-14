"""Hybrid retrieval: FTS + embeddings + RRF + heuristic rerank, with evidence radius."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from ..util import embed, fts_text, split_sentences, tokenize
from .chunker import DIM, load_chunks

COUNTER_CUES = ["然而", "但是", "尽管", "相反", "批评", "局限", "不适用", "仅适用",
                "质疑", "例外", "however", "although", "limitation", "critic", "but "]


@dataclass
class Scope:
    doc_ids: list[str]
    allowed_blocks: set[str] | None = None
    name: str = ""

    def allows(self, block_ids: list[str]) -> bool:
        if self.allowed_blocks is None:
            return True
        return any(b in self.allowed_blocks for b in block_ids)


def _fts_query(tokens: list[str]) -> str:
    seen = []
    for t in tokens:
        if t and t not in seen:
            seen.append(t)
    seen = seen[:24]
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in seen)


def search(db, scope: Scope, query: str, k: int = 10, candidates: int = 40) -> list[dict]:
    chunks = [c for c in load_chunks(db, scope.doc_ids) if scope.allows(c["block_ids"])]
    if not chunks:
        return []
    tokens = tokenize(query)
    tok_set = set(tokens)
    paths: dict[str, set[str]] = {}
    ranks: dict[str, float] = {}

    # dense
    qv = np.array(embed(query, DIM), dtype=np.float32)
    mat = np.stack([c["vec"] for c in chunks])
    dense_scores = mat @ qv
    dense_idx = np.argsort(-dense_scores)[:candidates]
    for rank, i in enumerate(dense_idx):
        cid = chunks[int(i)]["id"]
        paths.setdefault(cid, set()).add("dense")
        ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (60 + rank)

    # sparse
    if tokens:
        q = _fts_query(tokens)
        ph = ",".join("?" for _ in scope.doc_ids)
        try:
            rows = db.q(
                f"SELECT chunk_id, bm25(chunks_fts) AS s FROM chunks_fts "
                f"WHERE chunks_fts MATCH ? AND doc_id IN ({ph}) ORDER BY s LIMIT ?",
                (q, *scope.doc_ids, candidates))
        except Exception:
            rows = []
        for rank, r in enumerate(rows):
            cid = r["chunk_id"]
            paths.setdefault(cid, set()).add("sparse")
            ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (60 + rank)

    if not ranks:
        return []

    fused = sorted(ranks.items(), key=lambda kv: -kv[1])[:candidates]
    by_id = {c["id"]: c for c in chunks}
    out = []
    for cid, rrf in fused:
        c = by_id.get(cid)
        if c is None:
            continue
        ctoks = set(tokenize(c["text"]))
        lex = len(tok_set & ctoks) / max(1, len(tok_set))
        cos = float(c["vec"] @ qv)
        phrase = 1.0 if any(t in c["text"] for t in tokens if len(t) >= 2) else 0.0
        score = 0.45 * cos + 0.35 * lex + 0.2 * phrase + 0.02 * rrf * 60
        out.append({
            "chunk_id": cid, "doc_id": c["doc_id"], "section_id": c["section_id"],
            "block_ids": c["block_ids"], "text": c["text"], "score": round(score, 4),
            "paths": sorted(paths.get(cid, [])),
        })
        c["_score"] = score
    out.sort(key=lambda x: -x["score"])
    return out[:k]


def multi_search(db, scope: Scope, queries: list[str], k: int = 10) -> list[dict]:
    ranks: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for q in queries:
        for rank, c in enumerate(search(db, scope, q, k=max(k * 2, 10))):
            cid = c["chunk_id"]
            ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (60 + rank)
            by_id[cid] = c
    out = []
    for cid in sorted(ranks, key=lambda x: -ranks[x])[:k]:
        c = dict(by_id[cid])
        c["paths"] = sorted(set(c.get("paths", [])) | {"multi"})
        out.append(c)
    return out


def counter_search(db, scope: Scope, query: str, k: int = 4) -> list[dict]:
    """Find qualifiers / counter-arguments relevant to the query (mandatory in strict mode)."""
    qt = set(tokenize(query))
    out = []
    ph = ",".join("?" for _ in scope.doc_ids)
    rows = db.q(
        f"SELECT c.*, b.text AS block_text, b.page_from FROM claims c JOIN blocks b ON b.id=c.block_id "
        f"WHERE c.doc_id IN ({ph}) AND c.role IN ('counter','qualifier')", tuple(scope.doc_ids))
    for r in rows:
        text = r["block_text"]
        ctoks = set(tokenize(text))
        overlap = len(qt & ctoks)
        cue = any(cue in text for cue in COUNTER_CUES)
        if overlap == 0 and not cue:
            continue
        if scope.allowed_blocks is not None and r["block_id"] not in scope.allowed_blocks:
            continue
        out.append({
            "block_id": r["block_id"], "doc_id": r["doc_id"], "section_id": r["section_id"],
            "text": text, "page": r["page_from"], "role": r["role"],
            "score": round(overlap * 0.5 + (0.6 if cue else 0), 4),
        })
    seen = set()
    deduped = []
    for o in sorted(out, key=lambda x: -x["score"]):
        if o["block_id"] in seen:
            continue
        seen.add(o["block_id"])
        deduped.append(o)
        if len(deduped) >= k:
            break
    return deduped


def to_snippets(db, chunks: list[dict], max_blocks: int = 3, query: str = "") -> list[dict]:
    """Expand chunks into block-level citations, ranked by relevance to the query."""
    out = []
    seen = set()
    qt = set(tokenize(query))
    for c in chunks:
        ranked = []
        for pos, bid in enumerate(c["block_ids"]):
            row = db.one("SELECT * FROM blocks WHERE id=?", (bid,))
            if not row or row["kind"] == "heading":
                continue
            bt = set(tokenize(row["text"]))
            rel = len(qt & bt) / max(1, len(qt)) if qt else 0.0
            ranked.append((rel + 0.02 * (1 if pos == 0 else 0), pos, bid))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        for _, pos, bid in ranked[:max_blocks]:
            if bid in seen:
                continue
            row = db.one("SELECT * FROM blocks WHERE id=?", (bid,))
            if not row:
                continue
            seen.add(bid)
            out.append({
                "block_id": bid, "doc_id": row["doc_id"], "section_id": row["section_id"],
                "text": row["text"], "page": row["page_from"],
                "score": c.get("score", 0.0), "paths": c.get("paths", []),
                "role": c.get("role", ""),
            })
    return out


def best_sentence(text: str, query: str) -> str:
    sents = split_sentences(text)
    if not sents:
        return text
    qt = set(tokenize(query))
    best = max(sents, key=lambda s: len(qt & set(tokenize(s["text"]))))
    return best["text"]


def not_found(db, scope: Scope, query: str, threshold: float = 0.15) -> bool:
    res = search(db, scope, query, k=3)
    return (not res) or res[0]["score"] < threshold
