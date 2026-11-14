"""Anchors: stable, re-resolvable references into block text."""
from __future__ import annotations

import difflib

from .util import match_key, sha256_text


def make_anchor(block: dict, start: int, end: int, doc_version: int = 1,
                prefix_len: int = 24, suffix_len: int = 24) -> dict:
    text = block["text"]
    start = max(0, min(start, len(text)))
    end = max(start + 1, min(end, len(text)))
    return {
        "block_id": block["id"],
        "start": start,
        "end": end,
        "exact": text[start:end],
        "prefix": text[max(0, start - prefix_len):start],
        "suffix": text[end:end + suffix_len],
        "text_hash": sha256_text(match_key(text[start:end]), 32),
        "page": block.get("page_from"),
        "doc_version": doc_version,
        "status": "ok",
    }


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """Char-normalized string plus index map back to the original text."""
    chars: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        k = match_key(ch)
        if not k:
            continue
        for c in k:
            chars.append(c)
            idx.append(i)
    return "".join(chars), idx


def _locate(block_text: str, quote: str) -> tuple[int, int] | None:
    if not quote:
        return None
    idx = block_text.find(quote)
    if idx >= 0:
        return idx, idx + len(quote)
    qk = match_key(quote)
    if not qk:
        return None
    norm, idx_map = _norm_with_map(block_text)
    pos = norm.find(qk)
    if pos < 0:
        return None
    start = idx_map[pos]
    end = idx_map[pos + len(qk) - 1] + 1
    return start, end


def resolve_anchor(anchor: dict, block_lookup: dict[str, str]) -> dict:
    """Return a copy with status ok/pending/orphaned. Never silently drops."""
    out = dict(anchor)
    text = block_lookup.get(anchor.get("block_id", ""))
    if text is None:
        out["status"] = "orphaned"
        return out
    s, e = anchor.get("start", 0), anchor.get("end", 0)
    if isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text):
        if match_key(text[s:e]) == match_key(anchor.get("exact", "")):
            out["status"] = "ok"
            return out
    hit = _locate(text, anchor.get("exact", ""))
    if hit:
        out.update(start=hit[0], end=hit[1], status="ok")
        return out
    ctx = (anchor.get("prefix", "") or "") + (anchor.get("exact", "") or "") + (anchor.get("suffix", "") or "")
    cand = difflib.get_close_matches(ctx, list(block_lookup.values()), n=1, cutoff=0.72)
    if cand:
        hit = _locate(cand[0], anchor.get("exact", ""))
        if hit:
            out.update(start=hit[0], end=hit[1], status="ok")
            return out
    out["status"] = "pending"
    return out


def reanchor_all(anchors: list[dict], block_lookup: dict[str, str]) -> list[dict]:
    return [resolve_anchor(a, block_lookup) for a in anchors]


def find_span(text: str, quote: str) -> tuple[int, int] | None:
    return _locate(text, quote)
