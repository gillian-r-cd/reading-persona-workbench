"""Context ledger: the single source of truth for what the model saw this turn."""
from __future__ import annotations

import json

from ..util import dumps, estimate_tokens, sha256_text


def make_ledger(turn_id: str, persona_id: str, budget_total: int,
                sections: list[dict], citations: list[str] | None = None,
                dropped: list[dict] | None = None, mode: str = "strict") -> dict:
    norm = []
    for s in sections:
        body = s.get("body", "")
        norm.append({
            "name": s["name"],
            "tokens": int(s.get("tokens", estimate_tokens(body))),
            "body": body,
            "items": s.get("items", []),
            "source": s.get("source", ""),
        })
    used = sum(s["tokens"] for s in norm)
    return {
        "turn_id": turn_id,
        "persona_id": persona_id,
        "mode": mode,
        "budget": {"total": budget_total, "used": used},
        "sections": norm,
        "citations": citations or [],
        "dropped": dropped or [],
    }


def render(led: dict) -> str:
    lines = ["LEDGER v1",
             f"turn={led['turn_id']}",
             f"persona={led.get('persona_id', '')}",
             f"mode={led.get('mode', 'strict')}",
             f"total={led['budget']['total']}",
             f"used={led['budget']['used']}"]
    for s in led.get("sections", []):
        lines.append("[SECTION]")
        lines.append(f"name={s['name']}")
        lines.append(f"tokens={s['tokens']}")
        if s.get("source"):
            lines.append(f"source={s['source']}")
        for it in s.get("items", []):
            lines.append(f"item={it}")
        lines.append("[BODY]")
        for ln in (s.get("body") or "").split("\n"):
            lines.append("|" + ln)
        lines.append("[/SECTION]")
    lines.append("[CITATIONS]")
    lines.extend(led.get("citations", []))
    lines.append("[/CITATIONS]")
    for d in led.get("dropped", []):
        lines.append(f"[DROPPED] {d.get('id', '')} {d.get('reason', '')}")
    return "\n".join(lines)


def parse(text: str) -> dict:
    lines = text.split("\n")
    if not lines or lines[0] != "LEDGER v1":
        raise ValueError("not a ledger")
    head: dict[str, str] = {}
    sections: list[dict] = []
    citations: list[str] = []
    dropped: list[dict] = []
    i = 1
    cur: dict | None = None
    body: list[str] | None = None
    in_cit = False
    while i < len(lines):
        ln = lines[i]
        if ln == "[SECTION]":
            cur = {"name": "", "tokens": 0, "body": "", "items": [], "source": ""}
            body = None
        elif ln == "[BODY]":
            body = []
        elif ln == "[/SECTION]":
            if cur is not None and body is not None:
                cur["body"] = "\n".join(body)
            if cur is not None:
                sections.append(cur)
            cur, body = None, None
        elif ln == "[CITATIONS]":
            in_cit = True
        elif ln == "[/CITATIONS]":
            in_cit = False
        elif ln.startswith("[DROPPED]"):
            parts = ln.split(" ", 2)
            dropped.append({"id": parts[1] if len(parts) > 1 else "",
                            "reason": parts[2] if len(parts) > 2 else ""})
        elif in_cit:
            if ln:
                citations.append(ln)
        elif body is not None:
            body.append(ln[1:] if ln.startswith("|") else ln)
        elif cur is not None:
            if ln.startswith("name="):
                cur["name"] = ln[5:]
            elif ln.startswith("tokens="):
                cur["tokens"] = int(ln[7:] or 0)
            elif ln.startswith("source="):
                cur["source"] = ln[7:]
            elif ln.startswith("item="):
                cur["items"].append(ln[5:])
        elif "=" in ln:
            k, v = ln.split("=", 1)
            head[k] = v
        i += 1
    return {
        "turn_id": head.get("turn", ""),
        "persona_id": head.get("persona", ""),
        "mode": head.get("mode", "strict"),
        "budget": {"total": int(head.get("total", 0)), "used": int(head.get("used", 0))},
        "sections": sections,
        "citations": citations,
        "dropped": dropped,
    }


def structural_hash(led: dict) -> str:
    return sha256_text(dumps(led), 32)


def to_prompt_text(led: dict) -> str:
    """Flatten the ledger into the text actually sent to the model (excluding system rules)."""
    parts = []
    for s in led.get("sections", []):
        parts.append(f"## {s['name']}\n{s.get('body', '')}")
    if led.get("citations"):
        parts.append("## citations\n" + "\n".join(led["citations"]))
    return "\n\n".join(parts)


def body_of(led: dict, name: str) -> str:
    for s in led.get("sections", []):
        if s["name"] == name:
            return s.get("body", "")
    return ""


def items_of(led: dict, name: str) -> list:
    for s in led.get("sections", []):
        if s["name"] == name:
            return s.get("items", [])
    return []


def ledger_json(led: dict) -> str:
    return json.dumps(led, ensure_ascii=False, sort_keys=True)
