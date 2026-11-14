"""Shared utilities: hashing, normalization, tokens, embeddings, sentence splitting."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
import unicodedata

CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-']*|\d+(?:[.,]\d+)?%?")
WS = re.compile(r"[ \t\u00a0]+")
MULTI_NL = re.compile(r"\n{3,}")

QUOTE_MAP = {
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
    "\u300c": '"', "\u300d": '"', "\u300e": '"', "\u300f": '"',
    "\u2014": "-", "\u2013": "-", "\u00a0": " ",
}


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str, n: int | None = None) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return h[:n] if n else h


def sha256_bytes(data: bytes, n: int | None = None) -> str:
    h = hashlib.sha256(data).hexdigest()
    return h[:n] if n else h


def canonical(text: str) -> str:
    """Normalize for storage/display while preserving characters needed for exact quotes."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = WS.sub(" ", text)
    text = MULTI_NL.sub("\n\n", text)
    return text.strip()


def match_key(text: str) -> str:
    """Aggressive normalization used only for *matching* quotes, never for display."""
    text = canonical(text)
    text = "".join(QUOTE_MAP.get(ch, ch) for ch in text)
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 per CJK char, ~4 latin chars per token."""
    if not text:
        return 0
    cjk = len(CJK_RUN.findall(text))
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def split_sentences(text: str) -> list[dict]:
    """Return [{'start','end','text'}] with offsets into the original text."""
    out: list[dict] = []
    n = len(text)
    start = 0
    i = 0
    closers = "\u201d\"'\u300d\u300f)"
    while i < n:
        ch = text[i]
        cut = False
        if ch in "\u3002\uff01\uff1f!?\uff1b;":
            cut = True
        elif ch == ".":
            nxt = text[i + 1] if i + 1 < n else ""
            prev = text[i - 1] if i > 0 else ""
            if not (prev.isdigit() and nxt.isdigit()):
                if nxt == "" or nxt.isspace() or nxt in closers:
                    cut = True
        if cut:
            end = i + 1
            while end < n and text[end] in closers:
                end += 1
            seg = text[start:end]
            if seg.strip():
                out.append({"start": start, "end": end, "text": seg.strip()})
            start = end
            i = end
            continue
        i += 1
    if start < n:
        seg = text[start:]
        if seg.strip():
            out.append({"start": start, "end": n, "text": seg.strip()})
    return out


def tokenize(text: str) -> list[str]:
    """Bilingual tokenizer: latin words + CJK bigrams (unigrams for single-char runs)."""
    text = canonical(text)
    toks: list[str] = []
    for m in WORD.finditer(text):
        w = m.group(0).lower()
        toks.append(w)
        if len(w) > 4 and w.endswith("s"):
            toks.append(w[:-1])
    for m in CJK_RUN.finditer(text):
        run = m.group(0)
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def fts_text(text: str) -> str:
    return " ".join(tokenize(text))


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    v = int.from_bytes(h, "big")
    return v % dim, 1.0 if (v >> 63) & 1 else -1.0


def embed(text: str, dim: int = 384) -> list[float]:
    """Deterministic offline embedding: signed feature hashing over words/CJK n-grams."""
    vec = [0.0] * dim
    text = canonical(text)
    words = [m.group(0).lower() for m in WORD.finditer(text)]
    for w in words:
        i, s = _bucket("w:" + w, dim)
        vec[i] += s
    for a, b in zip(words, words[1:]):
        i, s = _bucket(f"b:{a}_{b}", dim)
        vec[i] += 0.6 * s
    for m in CJK_RUN.finditer(text):
        run = m.group(0)
        for ch in run:
            i, s = _bucket("c:" + ch, dim)
            vec[i] += 0.5 * s
        for k in (2, 3):
            for j in range(len(run) - k + 1):
                i, s = _bucket(f"g{k}:" + run[j:j + k], dim)
                vec[i] += 0.8 * s
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(s: str, default=None):
    try:
        return json.loads(s)
    except Exception:
        return default


def stable_id(prefix: str, *parts: str, n: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(parts), n)}"


def sentences_with_offsets(text: str) -> list[dict]:
    return split_sentences(text)


def overlap_ratio(query_tokens: set[str], text_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
