"""The five contracts (M0). Everything downstream validates against these."""
from __future__ import annotations

import re
from typing import Any

from .util import clamp, match_key

CLAIM_TYPES = {"FACT_EXPLICIT", "FACT_ENTAILED", "FACT_SYNTH", "FACT_EXTERNAL", "INTERPRETATION"}
FACT_TYPES = {"FACT_EXPLICIT", "FACT_ENTAILED", "FACT_SYNTH", "FACT_EXTERNAL"}
PERSONA_KINDS = {"AUTHOR", "QUOTED_PERSON", "SUBJECT", "SYNTHETIC"}
RELATIONS = {"supports", "qualifies", "contradicts", "context"}
ANCHOR_STATUS = {"ok", "pending", "orphaned"}
MODES = {"strict", "labeled", "off"}

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?%?")


def _err(errors: list[str], cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)


def validate_claim(c: dict, block_texts: dict[str, str] | None = None) -> list[str]:
    e: list[str] = []
    _err(e, isinstance(c.get("id"), str) and c["id"], "claim.id required")
    _err(e, isinstance(c.get("text"), str) and c["text"].strip(), "claim.text required")
    t = c.get("type")
    _err(e, t in CLAIM_TYPES, f"claim.type invalid: {t!r}")
    ev = c.get("evidence") or []
    _err(e, isinstance(ev, list), "claim.evidence must be a list")
    if t in FACT_TYPES:
        _err(e, len(ev) >= (2 if t == "FACT_SYNTH" else 1), f"{t} needs evidence")
    _err(e, isinstance(c.get("qualifiers", []), list), "claim.qualifiers must be a list")
    conf = c.get("confidence", 0.0)
    _err(e, isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0, "claim.confidence out of range")
    for i, item in enumerate(ev):
        p = f"claim.evidence[{i}]"
        _err(e, isinstance(item, dict), f"{p} must be object")
        if not isinstance(item, dict):
            continue
        _err(e, bool(item.get("block_id")), f"{p}.block_id required")
        _err(e, isinstance(item.get("start"), int) and isinstance(item.get("end"), int)
             and item["start"] < item["end"], f"{p} bad offsets")
        _err(e, bool(item.get("quote")), f"{p}.quote required")
        _err(e, item.get("relation", "supports") in RELATIONS, f"{p}.relation invalid")
        if t == "FACT_EXPLICIT" and item.get("quote"):
            _err(e, match_key(item["quote"]) in match_key(c.get("text", "")),
                 f"{p}.quote must be a verbatim substring of claim text")
    if block_texts is not None:
        for i, item in enumerate(ev):
            if not isinstance(item, dict):
                continue
            bt = block_texts.get(item.get("block_id", ""))
            if bt is None:
                e.append(f"claim.evidence[{i}] references unknown block")
                continue
            q = item.get("quote", "")
            s, en = item.get("start"), item.get("end")
            if isinstance(s, int) and isinstance(en, int) and 0 <= s < en <= len(bt):
                _err(e, match_key(bt[s:en]) == match_key(q),
                     f"claim.evidence[{i}] offsets do not match quote")
            _err(e, match_key(q) in match_key(bt), f"claim.evidence[{i}] quote not found in block")
    return e


def validate_persona(p: dict) -> list[str]:
    e: list[str] = []
    _err(e, bool(p.get("id")), "persona.id required")
    _err(e, p.get("kind") in PERSONA_KINDS, f"persona.kind invalid: {p.get('kind')!r}")
    _err(e, bool(p.get("name")), "persona.name required")
    _err(e, isinstance(p.get("evidence_radius"), dict), "persona.evidence_radius required")
    if p.get("kind") == "AUTHOR":
        modes = p.get("modes") or {}
        _err(e, "textual_author" in modes and "historical_author" in modes,
             "AUTHOR must define both modes")
        for m in ("textual_author", "historical_author"):
            _err(e, isinstance(modes.get(m, {}).get("scope"), list) and modes[m]["scope"],
                 f"AUTHOR.modes.{m}.scope required")
        _err(e, isinstance(p.get("positions", []), list), "AUTHOR.positions must be list")
    radius = p.get("evidence_radius") or {}
    _err(e, radius.get("spans") in ("all_in_doc", "explicit_set", "block_set"),
         "evidence_radius.spans must be all_in_doc|explicit_set|block_set")
    _err(e, isinstance(radius.get("external_corpora", []), list), "external_corpora must be list")
    _err(e, isinstance(p.get("voice", {}), dict), "voice must be object")
    _err(e, isinstance(p.get("boundaries", {}), dict), "boundaries must be object")
    return e


def validate_anchor(a: dict) -> list[str]:
    e: list[str] = []
    _err(e, bool(a.get("block_id")), "anchor.block_id required")
    _err(e, isinstance(a.get("start"), int) and isinstance(a.get("end"), int)
         and 0 <= a["start"] < a["end"], "anchor offsets invalid")
    _err(e, bool(a.get("exact")), "anchor.exact required")
    _err(e, bool(a.get("text_hash")), "anchor.text_hash required")
    _err(e, a.get("status", "ok") in ANCHOR_STATUS, "anchor.status invalid")
    return e


def validate_ledger(led: dict) -> list[str]:
    e: list[str] = []
    _err(e, bool(led.get("turn_id")), "ledger.turn_id required")
    b = led.get("budget") or {}
    _err(e, isinstance(b.get("total"), int) and b["total"] > 0, "ledger.budget.total required")
    _err(e, isinstance(b.get("used"), int) and b["used"] >= 0, "ledger.budget.used required")
    _err(e, b.get("used", 0) <= b.get("total", 0), "ledger budget exceeded")
    secs = led.get("sections") or []
    _err(e, isinstance(secs, list) and len(secs) > 0, "ledger.sections required")
    names = set()
    total = 0
    for i, s in enumerate(secs):
        _err(e, bool(s.get("name")), f"ledger.sections[{i}].name required")
        _err(e, isinstance(s.get("tokens"), int) and s["tokens"] >= 0,
             f"ledger.sections[{i}].tokens invalid")
        if s.get("name"):
            _err(e, s["name"] not in names, f"duplicate ledger section {s['name']}")
            names.add(s["name"])
        total += s.get("tokens", 0)
    _err(e, total == b.get("used", -1), "ledger.sections tokens must sum to budget.used")
    _err(e, isinstance(led.get("citations", []), list), "ledger.citations must be list")
    return e


def validate_document(d: dict) -> list[str]:
    e: list[str] = []
    _err(e, bool(d.get("id")), "document.id required")
    _err(e, bool(d.get("title")), "document.title required")
    _err(e, isinstance(d.get("version", 1), int), "document.version must be int")
    return e


def validate_trace(t: dict) -> list[str]:
    e: list[str] = []
    for k in ("turn_id", "session_id", "stages"):
        _err(e, k in t, f"trace.{k} required")
    return e


def new_claim(claim_id: str, text: str, ctype: str, evidence: list[dict],
              qualifiers: list[str] | None = None, confidence: float = 0.9,
              **meta) -> dict:
    return {
        "id": claim_id,
        "text": text,
        "type": ctype,
        "evidence": evidence,
        "qualifiers": qualifiers or [],
        "confidence": clamp(confidence),
        "meta": meta,
    }


def is_fact(claim: dict) -> bool:
    return claim.get("type") in FACT_TYPES


def claim_numbers(text: str) -> list[str]:
    return [m.group(0) for m in NUM_RE.finditer(text or "")]


def evidence_block_ids(claim: dict) -> set[str]:
    return {ev.get("block_id") for ev in claim.get("evidence", []) if ev.get("block_id")}


def summarize_claims(claims: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in claims:
        out[c.get("type", "?")] = out.get(c.get("type", "?"), 0) + 1
    return out


def ensure_claim_dict(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("claim must be an object")
    return obj
