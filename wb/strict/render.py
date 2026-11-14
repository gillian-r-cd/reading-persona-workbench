"""Renderer: claims -> prose. Cannot introduce new factual sentences."""
from __future__ import annotations

from ..contracts import FACT_TYPES

OPENERS = {
    ("AUTHOR", "textual_author"): "在这本书里，我的判断是：",
    ("AUTHOR", "historical_author"): "以我当时的想法来说：",
    ("QUOTED_PERSON", ""): "就书中记录的那段话来说：",
    ("SUBJECT", ""): "就书中与这件事有关的记录来说：",
    ("SYNTHETIC", ""): "作为审稿人，我的质疑是：",
}


def render(claims: list[dict], citations: list[dict], persona: dict, mode: str,
           block_docs: dict[str, str] | None = None, main_doc: str = "",
           doc_titles: dict[str, str] | None = None,
           opener_override: str | None = None) -> tuple[str, list[str]]:
    block_docs = block_docs or {}
    doc_titles = doc_titles or {}
    by_block: dict[str, str] = {}
    for c in citations:
        by_block.setdefault(c["block_id"], c["sid"])
    kind = persona.get("kind", "AUTHOR")
    opener = opener_override if opener_override is not None else (
        OPENERS.get((kind, mode)) or OPENERS.get((kind, "")) or "")
    lines: list[str] = []
    used: list[str] = []
    for c in claims:
        if c["type"] not in FACT_TYPES and mode == "strict":
            continue
        sid = ""
        for e in c.get("evidence", []):
            if e["block_id"] in by_block:
                sid = by_block[e["block_id"]]
                break
        if not sid:
            continue
        text = c["text"].strip()
        if c.get("meta", {}).get("role") == "counter":
            text = "不过，书中也明确指出了边界：" + text
        elif c["type"] == "INTERPRETATION":
            text = "（解读）" + text
        tag = ""
        doc = block_docs.get(c["evidence"][0]["block_id"]) if c.get("evidence") else None
        if main_doc and doc and doc != main_doc:
            title = doc_titles.get(doc, "")
            what = "访谈" if "访谈" in title else ("外部研究" if ("评论" in title or "研究" in title) else "其他来源")
            tag = f"（{what}）"
        lines.append(f"{text} [{sid}]{tag}")
        used.append(sid)
    if not lines:
        return "", []
    body = "".join(lines)
    return (opener + body if opener else body), used


def abstain_text(persona: dict, near: list[dict]) -> str:
    kind = persona.get("kind", "AUTHOR")
    if kind == "QUOTED_PERSON":
        head = "书中没有记录我对这个问题的看法。"
    elif kind == "SYNTHETIC":
        head = "文本没有提供足以回答这个问题的证据。"
    else:
        head = "在这本书里，我没有找到能直接回答这个问题的证据。"
    if near:
        head += "以下是最接近的原文摘录，供你判断（它们并不构成回答）："
        for i, n in enumerate(near, start=1):
            head += f"\n[{n['sid']}] {n['text'][:160]}"
    return head
