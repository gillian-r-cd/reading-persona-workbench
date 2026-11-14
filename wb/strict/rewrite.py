"""Prompt-driven grounded rewrite: coherent prose, still tied to snippets."""
from __future__ import annotations

import re

CITE_RE = re.compile(r"\[(S\d+)\]")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?%?")

SYSTEM = """你是一个严格受原文约束的写作者。你只被允许使用给定的编号片段，禁止使用片段之外的任何知识。
你可以改写、合并、调整语序、补充连接词，但不能改变事实、数字、限定条件，也不能引入片段里没有的专有名词。
每个事实性句子末尾必须标注来源，如 [S1]；一句话可以由多个片段支持，写作 [S1][S2]。
不要写“根据你提供的片段”“根据资料”这类元话术，直接陈述内容。
如果片段来自其他作者/评论（标题与你的作品不同），必须写成“有评论指出/外部研究认为”，不能当作你本人的判断。
如果片段没有覆盖问题的某一部分，直接写“书中没有直接谈到这一点”。"""


def build_prompt(persona: dict, card: str, title: str, question: str,
                 snippets: list[dict], counter: list[dict], external: str = "",
                 doc_titles: dict[str, str] | None = None) -> str:
    doc_titles = doc_titles or {}
    lines = [SYSTEM, "", f"你的身份：{persona.get('name', '作者')}，以《{title}》作者/相关人物的身份回答。",
             "语气要求：" + str(persona.get("voice", {}).get("register", "克制、书面"))]
    if persona.get("kind") == "SYNTHETIC":
        lines.append("你的任务是质疑，不是复述：优先引用限定、反例、样本与因果问题；"
                     "如果没有可引用的反证，就直接说明“书中没有给出可引用的反证”。")
    lines.append("\n可用片段：")
    for s in snippets:
        kind = "（反方/限定）" if s.get("role") in ("counter", "qualifier") else ""
        src = doc_titles.get(s.get("doc_id", ""), "")
        label = f"（《{src}》）" if src and src != title else ""
        lines.append(f"[{s['sid']}]{label}{kind} {s['text']}")
    if external:
        lines.append("\n[外部研究]（只能作为背景，不得当作书中事实；使用外部信息时必须写明“外部研究显示/报道”）")
        lines.append(external)
    if "未来" in question or "目标" in question:
        lines.append("\n注意：如果问题涉及未来，先说明书中没有直接预测，再陈述文本中明确表达过的目标。")
    lines.append(f"\n问题：{question}")
    lines.append("\n请直接输出回答正文（不要 JSON、不要标题），3–5 段，总长 200–500 字。")
    return "\n".join(lines)


def _clean(text: str) -> str:
    return CITE_RE.sub("", text)


def validate(text: str, snippets: list[dict], external: str = "") -> list[str]:
    errs: list[str] = []
    if len(text.strip()) < 40:
        errs.append("回答过短")
    sids = {s["sid"] for s in snippets}
    used = set(CITE_RE.findall(text))
    unknown = sorted(used - sids)
    if unknown:
        errs.append(f"引用了不存在的编号: {unknown}")
    if not used:
        errs.append("没有任何引用")
    pool = " ".join(s["text"] for s in snippets if s["sid"] in used)
    clean = _clean(text)
    nums = set(NUM_RE.findall(clean))
    missing = sorted(n for n in nums if n not in pool and n not in (external or ""))
    if missing:
        errs.append(f"数字无法回溯到所引片段: {missing}")
    for sentence in re.split(r"[。！？\n]", clean):
        ext_only = [n for n in NUM_RE.findall(sentence)
                    if n not in pool and n and n in (external or "")]
        if ext_only and not re.search(r"外部|研究|报道|资料|调查", sentence):
            errs.append(f"外部数字未标注来源: {ext_only}")
            break
    paras = [p for p in text.split("\n\n") if p.strip()]
    bad = [p[:40] for p in paras if NUM_RE.search(_clean(p)) and not CITE_RE.search(p)]
    if bad:
        errs.append(f"含数字但无引用的段落: {bad[:2]}")
    return errs


def run(provider, persona: dict, card: str, title: str, question: str,
        snippets: list[dict], counter: list[dict], max_attempts: int = 2,
        external: str = "", doc_titles: dict[str, str] | None = None) -> dict:
    all_snips = list(snippets)
    prompt = build_prompt(persona, card, title, question, all_snips, counter, external, doc_titles)
    errors: list[str] = []
    text = ""
    for attempt in range(max_attempts):
        try:
            text = provider.chat([{"role": "user", "content": prompt}], temperature=0.2)
        except Exception as exc:
            return {"ok": False, "text": "", "errors": [f"provider: {exc}"], "attempts": attempt + 1}
        text = (text or "").strip()
        errors = validate(text, all_snips, external)
        if not errors:
            return {"ok": True, "text": text, "errors": [], "attempts": attempt + 1}
        prompt = (build_prompt(persona, card, title, question, all_snips, counter, external, doc_titles)
                  + "\n\n上一版存在以下问题，请修正并重新输出：\n- " + "\n- ".join(errors))
    return {"ok": False, "text": text, "errors": errors, "attempts": max_attempts}
