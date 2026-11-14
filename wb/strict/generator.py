"""Claim generation: extractive (offline, default) or LLM (BYOK)."""
from __future__ import annotations

import json
import re

from ..contracts import new_claim
from ..util import split_sentences, stable_id, tokenize

NUMERIC_Q = re.compile(r"多少|几|比例|百分|percent|how many|how much|rate|number", re.I)
THESIS_Q = re.compile(r"核心|主张|论点|主要观点|thesis|argument|argue|point", re.I)
YESNO_Q = re.compile(r"是否|吗|是不是|适用于所有|always|all teams|every", re.I)


def _claim_from_sentence(sid: str, block: dict, sent: dict, conf: float, **meta) -> dict:
    return new_claim(
        stable_id("c", sid, block["id"], str(sent["start"]), n=14),
        sent["text"], "FACT_EXPLICIT",
        [{"block_id": block["id"], "start": sent["start"], "end": sent["end"],
          "quote": sent["text"], "relation": "supports"}],
        qualifiers=_extract_qualifiers(sent["text"]), confidence=conf, **meta)


def _extract_qualifiers(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"(仅适用于[^。；;]{0,20}|只适用于[^。；;]{0,20}|不适用于[^。；;]{0,20}|"
                         r"在[^。；;]{0,20}条件下|除非[^。；;]{0,20}|仅限于[^。；;]{0,20})", text):
        out.append(m.group(1))
    return out[:3]


def _keyword_overlap(question: str, text: str) -> float:
    qt = set(tokenize(question))
    if not qt:
        return 0.0
    return len(qt & set(tokenize(text))) / len(qt)


def generate_extractive(db, question: str, snippets: list[dict], counter: list[dict],
                        doc_id: str, max_claims: int = 5) -> list[dict]:
    numeric = bool(NUMERIC_Q.search(question))
    thesis_q = bool(THESIS_Q.search(question))
    yesno = bool(YESNO_Q.search(question))
    quote_q = bool(re.search(r"说了什么|说过什么|原话|引述|quote|said", question, re.I))
    boundary_q = bool(re.search(r"适用|边界|条件|所有|scope|boundary|always", question, re.I))
    future_q = bool(re.search(r"未来|前景|目标|前途|出路|愿景|才能有", question))
    claims: list[dict] = []

    if thesis_q:
        row = db.one("SELECT * FROM claims WHERE doc_id=? AND role='thesis'", (doc_id,))
        if row:
            blk = db.one("SELECT * FROM blocks WHERE id=?", (row["block_id"],))
            if blk:
                sent = next((s for s in split_sentences(blk["text"]) if s["text"] == row["text"]), None)
                if sent:
                    claims.append(_claim_from_sentence("thesis", dict(blk), sent, 0.95, role="thesis",
                                                       generator="extractive"))

    scored = []
    for sn in snippets:
        block = {"id": sn["block_id"], "text": sn["text"]}
        sents = split_sentences(sn["text"])
        for idx, sent in enumerate(sents):
            text = sent["text"]
            if len(text) < 6 or text.startswith(("问：", "问:", "Q:", "Q：", "访谈者", "访谈时间")):
                continue
            score = _keyword_overlap(question, text)
            if numeric and re.search(r"\d", text):
                score += 0.25
            if quote_q and ("\u201c" in text or "\u201d" in text):
                score += 0.6
            if boundary_q and re.search(r"仅适用|不适用|除非|条件|边界|局限", text):
                score += 0.45
            if future_q and re.search(r"才能|必须|要建立|目的|前途|新中国|独立|解放|胜利|联合|共和", text):
                score += 0.35
            score += 0.08 * float(sn.get("score", 0))
            scored.append((score, sn, block, sent, idx, sents))
    scored.sort(key=lambda x: -x[0])

    cap = 2 if thesis_q else (4 if yesno or boundary_q else max_claims)
    picked_blocks: dict[str, int] = {}
    for score, sn, block, sent, idx, sents in scored:
        if len(claims) >= cap:
            break
        if score < (0.25 if thesis_q and claims else 0.06):
            continue
        bid = block["id"]
        if picked_blocks.get(bid, 0) >= 2:
            continue
        if any(c["text"] == sent["text"] for c in claims):
            continue
        picked_blocks[bid] = picked_blocks.get(bid, 0) + 1
        # for quote questions, keep the lead-in sentence that names the speaker
        if quote_q and idx > 0 and len(claims) < cap:
            prev = sents[idx - 1]
            if any(n in prev["text"] for n in ("说", "指出", "表示", "称")) or len(prev["text"]) < 60:
                if not any(c["text"] == prev["text"] for c in claims):
                    claims.append(_claim_from_sentence(sn["block_id"], block, prev, 0.8, role="answer",
                                                       generator="extractive"))
        claims.append(_claim_from_sentence(sn["block_id"], block, sent, 0.85, role="answer",
                                           generator="extractive"))

    if yesno or boundary_q:
        for c in counter[:2]:
            block = {"id": c["block_id"], "text": c["text"]}
            for sent in split_sentences(c["text"]):
                if len(sent["text"]) < 6:
                    continue
                if _keyword_overlap(question, sent["text"]) < 0.15:
                    continue
                if not re.search(r"仅适用|不适用|除非|条件|边界|局限|然而|但是|批评|相反", sent["text"]):
                    continue
                if any(x["text"] == sent["text"] for x in claims):
                    continue
                claims.append(_claim_from_sentence(c["block_id"], block, sent, 0.8,
                                                   role="counter", source_role=c.get("role", ""),
                                                   generator="extractive"))
                break
    return claims[:max_claims]


def generate_llm(provider, question: str, snippets: list[dict], doc_id: str,
                 max_claims: int = 5) -> list[dict]:
    listing = "\n\n".join(
        f"[{i}] block={s['block_id']} page={s.get('page')}\n{s['text']}"
        for i, s in enumerate(snippets, start=1))
    prompt = (
        "你是严格的事实抽取器。只能使用下面给出的原文片段，逐字引用，不得改写数字。\n"
        "输出 JSON：{\"claims\":[{\"text\":\"...\",\"block_id\":\"...\",\"quote\":\"...\"}]}\n"
        "规则：quote 必须是片段中的逐字子串；text 必须是 quote 的原句或原句的逐字子串；"
        "没有证据就返回空数组。\n\n"
        f"问题：{question}\n\n原文片段：\n{listing}")
    raw = provider.chat([{"role": "user", "content": prompt}], temperature=0.0,
                        json_schema={"type": "json_object"})
    data = _parse_json(raw)
    out = []
    block_text = {s["block_id"]: s["text"] for s in snippets}
    for item in (data.get("claims") or [])[:max_claims]:
        bid = item.get("block_id", "")
        quote = (item.get("quote") or "").strip()
        text = (item.get("text") or "").strip()
        if bid not in block_text or not quote or not text:
            continue
        bt = block_text[bid]
        if quote not in bt or text not in quote and text not in bt:
            continue
        start = bt.find(text if text in bt else quote)
        end = start + len(text if text in bt else quote)
        out.append(new_claim(stable_id("c", "llm", bid, str(start), n=14), text,
                             "FACT_EXPLICIT",
                             [{"block_id": bid, "start": start, "end": end,
                               "quote": bt[start:end], "relation": "supports"}],
                             confidence=0.8, role="answer", generator="llm"))
    return out


def _parse_json(raw: str) -> dict:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            return json.loads(s[i:j + 1])
        raise


def generate(db, provider, question: str, snippets: list[dict], counter: list[dict],
             doc_id: str) -> list[dict]:
    if getattr(provider, "name", "offline") != "offline":
        try:
            claims = generate_llm(provider, question, snippets, doc_id)
            if claims:
                return claims
        except Exception:
            pass
    return generate_extractive(db, question, snippets, counter, doc_id)
