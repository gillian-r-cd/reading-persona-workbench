"""Budgeted context assembly -> ledger + citation registry."""
from __future__ import annotations

from ..util import estimate_tokens
from . import ledger as L


def _fmt_snippet(sid: str, sn: dict, title: str = "") -> str:
    page = f"p.{sn['page']} " if sn.get("page") else ""
    head = f"[{sid}] {page}{title}".strip()
    return f"{head}\n{sn['text']}"


def build(turn_id: str, persona_id: str, card_text: str,
          snippets: list[dict], counter: list[dict] | None = None,
          pins: list[dict] | None = None, facts: list[str] | None = None,
          research: str = "", history: str = "", titles: dict[str, str] | None = None,
          total: int = 16000, mode: str = "strict") -> tuple[dict, list[dict]]:
    titles = titles or {}
    counter = counter or []
    pins = pins or []
    facts = facts or []
    citations: list[dict] = []

    ev_lines = []
    for i, sn in enumerate(snippets, start=1):
        sid = f"S{i}"
        ev_lines.append(_fmt_snippet(sid, sn, titles.get(sn.get("doc_id", ""), "")))
        citations.append({
            "sid": sid, "block_id": sn["block_id"], "doc_id": sn["doc_id"],
            "quote": sn["text"], "page": sn.get("page"), "kind": "canon",
            "start": 0, "end": len(sn["text"]),
        })
    ce_lines = []
    for j, sn in enumerate(counter, start=len(snippets) + 1):
        sid = f"S{j}"
        role = {"counter": "反方", "qualifier": "限定"}.get(sn.get("role", ""), "对照")
        ce_lines.append(_fmt_snippet(sid, sn, titles.get(sn.get("doc_id", ""))) + f"\n（{role}）")
        citations.append({
            "sid": sid, "block_id": sn["block_id"], "doc_id": sn["doc_id"],
            "quote": sn["text"], "page": sn.get("page"), "kind": "counter",
            "start": 0, "end": len(sn["text"]),
        })

    sections = [
        {"name": "persona_card", "tokens": estimate_tokens(card_text), "body": card_text},
        {"name": "pins", "tokens": estimate_tokens("\n".join(p["text"] for p in pins)),
         "body": "\n".join(f"- {p['text']}" for p in pins)},
        {"name": "ledger", "tokens": estimate_tokens("\n".join(facts)),
         "body": "\n".join(f"- {f}" for f in facts)},
        {"name": "evidence", "tokens": estimate_tokens("\n\n".join(ev_lines)),
         "body": "\n\n".join(ev_lines)},
        {"name": "counter_evidence", "tokens": estimate_tokens("\n\n".join(ce_lines)),
         "body": "\n\n".join(ce_lines)},
        {"name": "research", "tokens": estimate_tokens(research), "body": research},
        {"name": "history", "tokens": estimate_tokens(history), "body": history},
    ]
    sections = [s for s in sections if s["body"]]

    dropped: list[dict] = []
    # trim evidence from the tail until within budget
    def total_tokens() -> int:
        return sum(s["tokens"] for s in sections)

    while total_tokens() > total:
        ev = next((s for s in sections if s["name"] == "counter_evidence"), None)
        if ev and ev["body"]:
            parts = ev["body"].split("\n\n")
            if len(parts) > 1:
                dropped.append({"id": parts[-1][:40], "reason": "budget"})
                ev["body"] = "\n\n".join(parts[:-1])
                ev["tokens"] = estimate_tokens(ev["body"])
                continue
        ev = next((s for s in sections if s["name"] == "evidence"), None)
        if ev and "\n\n" in ev["body"]:
            parts = ev["body"].split("\n\n")
            dropped.append({"id": parts[-1][:40], "reason": "budget"})
            ev["body"] = "\n\n".join(parts[:-1])
            ev["tokens"] = estimate_tokens(ev["body"])
            continue
        hist = next((s for s in sections if s["name"] == "history"), None)
        if hist and len(hist["body"]) > 200:
            hist["body"] = hist["body"][-200:]
            hist["tokens"] = estimate_tokens(hist["body"])
            continue
        break

    led = L.make_ledger(turn_id, persona_id, total, sections,
                        citations=[c["sid"] for c in citations], dropped=dropped, mode=mode)
    return led, citations


def card_text(card: dict) -> str:
    lines = [f"你是{card.get('name', '作者')}。"]
    modes = card.get("modes", {})
    if card.get("kind") == "AUTHOR" and modes:
        lines.append("你可以以两种身份说话：文本作者（只依据本书）与历史作者（可引用书外语料）。")
    voice = card.get("voice", {})
    if voice:
        lines.append("语气：" + "；".join(str(v) for v in voice.values() if isinstance(v, str))[:300])
    boundary = card.get("boundaries", {})
    if boundary.get("cannot_know"):
        lines.append("你不知道：" + "、".join(boundary["cannot_know"]))
    lines.append("规则：只有证据支持的事实才能断言；不确定就说不确定。")
    return "\n".join(lines)
