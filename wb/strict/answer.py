"""End-to-end strict answering pipeline."""
from __future__ import annotations

import json
import re
import time

from ..context import assembler
from ..context import ledger as L
from ..index import retrieve
from ..personas import registry
from ..trace import Trace, new_turn_id
from ..util import estimate_tokens, now_iso, split_sentences, stable_id
from ..contracts import new_claim
from .. import devlog
from ..research import dossier as research_mod
from ..index import expand as expand_mod
from . import gate as gate_mod
from . import generator, render, rewrite

YESNO = re.compile(r"是否|吗|是不是|有没有|能否|whether|does |is it|are they", re.I)
POLAR = ("不是", "并非", "不等于", "不适用", "仅适用", "没有", "未必", "不能", "无法", "确实", "正是", "就是")
IDENTITY_Q = re.compile(r"你是谁|你叫什么|自我介绍|介绍一下你自己|你能做什么|who are you|what can you do", re.I)
INTENT_Q = re.compile(r"为什么写|写作意图|意图是|想表达|为什么这样写|why did you write|your intent|写作目的")
REVISION_Q = re.compile(r"改过|改成|修正|后来改|改口|重写|revised|change your mind|changed your mind")
CHALLENGE_Q = re.compile(r"批评|质疑|漏洞|回应|反驳|critic|challenge|rebut")
THESIS_Q = re.compile(r"核心|主张|论点|主要观点|thesis|argument")
POST_PUBLICATION_Q = re.compile(r"出版(之)?后|书出(版)?后|成书之后|after publication|after the book")
FUTURE_Q = re.compile(r"未来|前景|愿景|理想(情况|状态|社会)?|目标|出路|前途|才能有|往哪里走")


def _history(db, session_id: str, limit: int = 4) -> str:
    rows = db.q("SELECT role,content FROM messages WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (session_id, limit * 2))
    parts = []
    for r in reversed(rows):
        parts.append(f"{'用户' if r['role'] == 'user' else '助手'}：{r['content'][:400]}")
    return "\n".join(parts)


def _facts(db, session_id: str) -> list[str]:
    return [r["fact"] for r in db.q("SELECT fact FROM ledger WHERE session_id=? ORDER BY created_at LIMIT 12",
                                    (session_id,))]


IDENTITY_ROLE = {
    "AUTHOR": "作者",
    "QUOTED_PERSON": "被引述者",
    "SUBJECT": "书中记录的对象",
    "SYNTHETIC": "合成审稿人",
}


def _identity_text(persona: dict, persona_mode: str, mode: str, title: str) -> str:
    name = persona.get("name", "无名")
    kind = persona.get("kind", "AUTHOR")
    role = IDENTITY_ROLE.get(kind, "角色")
    if kind == "SYNTHETIC":
        return (f"我是{name}，一个合成的审稿人——不是书中的真实人物，也不代表作者。\n"
                "我的职责是从方法、证据和论证结构上提出质疑，而不是替作者辩护。\n"
                "我同样受严格事实约束：质疑必须有原文依据，没有就直说没有。")
    if kind == "QUOTED_PERSON":
        return (f"我是{name}，在《{title}》里被引述过。\n"
                "我只能就书中记录的那段话发言；书中没有记录的看法，我会直接说没有记录。")
    if kind == "SUBJECT":
        return (f"我是{name}，是《{title}》里被记录的对象。\n"
                "我只依据书中与这件事有关的记录说话，超出记录范围的我不会编。")
    if persona_mode == "historical_author":
        mode_line = "当前是历史作者模式：可以引用本书，也可以引用访谈与外部研究，并标注来源。"
    else:
        mode_line = "当前是文本作者模式：只依据《" + title + "》本身说话，不引用书外语料。"
    strict_line = ("严格模式下，我只说原文能支持的话：有证据才下判断，没有证据就明确说没有。"
                   if mode == "strict" else
                   "标注解读模式下，事实仍然要有出处，解读会单独标出来。")
    return (f"我是{name}，以《{title}》{role}的身份和你对话。\n{mode_line}\n{strict_line}\n"
            "你可以问我：这本书的核心论点、某个概念的边界条件、某段原文的意思，或者以第一人称回应批评。")


def _position_claims(db, persona: dict, question: str) -> list[dict]:
    if persona.get("kind") != "AUTHOR":
        return []
    statuses: set[str] = set()
    if REVISION_Q.search(question):
        statuses.add("revised")
    if CHALLENGE_Q.search(question):
        statuses.update({"acknowledged", "maintained"})
    if not statuses:
        return []
    cands = [p for p in persona.get("positions", [])
             if p.get("status") in statuses and not (CHALLENGE_Q.search(question) and p.get("kind") == "book")]
    priority = {"acknowledged": 0, "revised": 0, "maintained": 1}
    cands.sort(key=lambda p: priority.get(p.get("status"), 2))
    out: list[dict] = []
    for pos in cands:
        row = db.one("SELECT * FROM claims WHERE doc_id=? AND text=? ORDER BY rowid LIMIT 1",
                     (pos["source"], pos["stmt"]))
        if not row:
            continue
        ev = db.one("SELECT * FROM claim_evidence WHERE claim_id=?", (row["id"],))
        if not ev:
            continue
        out.append(new_claim(
            stable_id("c", "pos", row["id"], n=14), row["text"], "FACT_EXPLICIT",
            [{"block_id": ev["block_id"], "start": ev["start"], "end": ev["end"],
              "quote": ev["quote"], "relation": "supports"}],
            qualifiers=json.loads(row["qualifiers"] or "[]"), confidence=0.9,
            role="position", position_status=pos.get("status")))
    return out


def _pins(db, session_id: str) -> list[dict]:
    rows = db.q(
        "SELECT p.target_id, h.anchor FROM pins p LEFT JOIN highlights h ON h.id=p.target_id "
        "WHERE p.session_id=? AND p.target_type='highlight'", (session_id,))
    out = []
    for r in rows:
        if not r["anchor"]:
            continue
        a = json.loads(r["anchor"])
        blk = db.one("SELECT text FROM blocks WHERE id=?", (a.get("block_id"),))
        if blk:
            out.append({"text": blk["text"][a.get("start", 0):a.get("end", 0)] or blk["text"][:120]})
    return out


def answer_question(cfg, db, session_id: str, question: str, provider,
                    budget: int = 8000, persona_override: dict | None = None,
                    store_user: bool = True, scope_docs: list[str] | None = None) -> dict:
    sess = db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not sess:
        raise ValueError("session not found")
    doc_ids = json.loads(sess["doc_ids"])
    mode = sess["mode"] or "strict"
    settings = json.loads(sess["settings"] or "{}")
    persona_mode = settings.get("persona_mode", "textual_author")
    persona = persona_override or (
        registry.get_persona(db, sess["persona_id"]) if sess["persona_id"] else None)
    if not persona:
        persona = registry.author_card(db, doc_ids[0])
    scope = registry.scope_for(db, persona, persona_mode)
    if scope_docs:
        allowed = scope.allowed_blocks if persona.get("kind") != "AUTHOR" else None
        scope = retrieve.Scope(doc_ids=scope_docs, allowed_blocks=allowed, name="project")
    main_doc = doc_ids[0] if doc_ids else (scope_docs or [""])[0]
    if persona.get("kind") == "AUTHOR" and THESIS_Q.search(question):
        scope = registry.Scope([main_doc], allowed_blocks=scope.allowed_blocks, name="thesis_scope")
    turn_id = new_turn_id()
    trace = Trace(cfg.vault, session_id, turn_id)
    trace.request = {"question": question, "mode": mode, "persona": persona_mode,
                     "persona_id": persona["id"]}
    card = assembler.card_text(persona)
    titles = {r["id"]: r["title"] for r in db.q("SELECT id,title FROM documents")}
    block_docs = {r["id"]: r["doc_id"] for r in db.q("SELECT id,doc_id FROM blocks")}
    history = _history(db, session_id)
    facts = _facts(db, session_id)
    pins = _pins(db, session_id)
    external = research_mod.external_text(db, session_id)

    if store_user:
        db.insert("messages", {
            "id": stable_id("m", session_id, turn_id, "user", n=14), "session_id": session_id,
            "role": "user", "content": question, "claims": "[]", "citations": "[]",
            "meta": "{}", "created_at": now_iso(),
        })

    def log(kind: str, t0: float, req: dict, resp: dict, status: str = "ok", error: str = "") -> None:
        try:
            devlog.record(db, session_id, turn_id, kind,
                          provider=getattr(provider, "name", ""),
                          model=getattr(provider, "model", ""),
                          latency_ms=(time.perf_counter() - t0) * 1000,
                          request=req, response=resp, status=status, error=error)
        except Exception:
            pass

    def finish(text: str, claims: list[dict], cites: list[dict], led: dict,
               abstained: bool, gate_report: dict, dropped: int, near=None) -> dict:
        trace.stage("render", abstained=abstained, chars=len(text))
        trace.set_ledger(led)
        trace.response = {"text": text, "abstained": abstained}
        path = trace.save()
        used = set(re.findall(r"\[(S\d+)\]", text))
        cites = [c for c in cites if c["sid"] in used]
        row = db.one("SELECT title FROM sessions WHERE id=?", (session_id,))
        if row and (row["title"] or "") in ("会话", "新会话", ""):
            db.x("UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                 ((question or "")[:24], now_iso(), session_id))
        else:
            db.x("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), session_id))
        db.insert("messages", {
            "id": stable_id("m", session_id, turn_id, "assistant", n=14), "session_id": session_id,
            "role": "assistant", "content": text,
            "claims": json.dumps(claims, ensure_ascii=False),
            "citations": json.dumps(cites, ensure_ascii=False),
            "meta": json.dumps({"turn_id": turn_id, "abstained": abstained,
                                "gate": gate_report, "dropped": dropped,
                                "near": near or [], "ledger": led,
                                "persona_id": persona.get("id", ""),
                                "persona_name": persona.get("name", "")},
                               ensure_ascii=False),
            "created_at": now_iso(),
        })
        return {"text": text, "claims": claims, "citations": cites, "ledger": led,
                "turn_id": turn_id, "trace_path": path, "abstained": abstained,
                "gate": gate_report, "dropped": dropped, "near": near or [],
                "persona_id": persona.get("id", ""), "persona_name": persona.get("name", "")}

    def abstain(reason: str) -> dict:
        near_snips = retrieve.to_snippets(db, retrieve.search(db, scope, question, k=3),
                                          max_blocks=1, query=question)[:3]
        if scope.allowed_blocks is not None:
            near_snips = [s for s in near_snips if s["block_id"] in scope.allowed_blocks]
            if not near_snips:
                for bid in list(scope.allowed_blocks)[:2]:
                    row = db.one("SELECT * FROM blocks WHERE id=?", (bid,))
                    if row:
                        near_snips.append({"block_id": row["id"], "doc_id": row["doc_id"],
                                           "section_id": row["section_id"], "text": row["text"],
                                           "page": row["page_from"], "score": 1.0})
        led, cites = assembler.build(turn_id, persona["id"], card, near_snips, [], pins, facts,
                                     external, history, titles, total=budget, mode=mode)
        sid_map = {c["block_id"]: c["sid"] for c in cites}
        near = [{"sid": sid_map.get(s["block_id"], ""), "text": s["text"]} for s in near_snips]
        text = render.abstain_text(persona, near)
        trace.stage("abstain", reason=reason)
        return finish(text, [], cites, led, True,
                      {"checked": 0, "passed": [], "failures": [], "ok": True, "reason": reason}, 0, near)

    if IDENTITY_Q.search(question):
        text = _identity_text(persona, persona_mode, mode, titles.get(main_doc, ""))
        led = L.make_ledger(turn_id, persona["id"], budget, [
            {"name": "persona_card", "tokens": estimate_tokens(card), "body": card},
            {"name": "identity", "tokens": estimate_tokens(text), "body": text},
        ], citations=[], mode=mode)
        trace.stage("identity", kind=persona.get("kind"), persona_mode=persona_mode)
        return finish(text, [], [], led, False,
                      {"checked": 0, "passed": [], "failures": [], "ok": True}, 0)

    if POST_PUBLICATION_Q.search(question):
        return abstain("outside_evidence_radius")
    future = bool(FUTURE_Q.search(question))
    if retrieve.not_found(db, scope, question):
        return abstain("no_relevant_evidence")

    search_q = question + (" 目标 前途 建立 独立 解放 胜利 新中国" if future else "")
    provider_name = getattr(provider, "name", "offline")
    queries = [search_q]
    if provider_name != "offline":
        t_exp = time.perf_counter()
        extra_q = expand_mod.expand(provider, question)
        queries += extra_q
        log("expand", t_exp, {"question": question}, {"queries": extra_q})
    t_ret = time.perf_counter()
    chunks = retrieve.multi_search(db, scope, queries, k=10 if future else 8)
    snippets = retrieve.to_snippets(db, chunks, max_blocks=3, query=search_q)
    counter = retrieve.counter_search(db, scope, question, k=3)
    if scope.allowed_blocks is not None:
        snippets = [s for s in snippets if s["block_id"] in scope.allowed_blocks]
        counter = [c for c in counter if c["block_id"] in scope.allowed_blocks]
    log("retrieve", t_ret, {"queries": queries, "scope": scope.doc_ids},
        {"chunks": [{"id": c["chunk_id"], "score": c["score"], "paths": c["paths"],
                     "text": c["text"][:80]} for c in chunks[:10]],
         "snippets": [s["block_id"] for s in snippets],
         "counter": [c["block_id"] for c in counter]})
    if persona.get("kind") == "SYNTHETIC":
        if not counter:
            return abstain("synthetic_no_counter")
        snippets = []
    trace.stage("retrieve", chunks=len(chunks), snippets=len(snippets), counter=len(counter),
                queries=len(queries))

    if mode == "rewrite" and provider_name != "offline" and snippets:
        all_snips = [dict(s, sid=f"S{i}") for i, s in enumerate(snippets, 1)]
        all_ctr = [dict(c, sid=f"S{len(snippets) + j}") for j, c in enumerate(counter, 1)]
        t_rw = time.perf_counter()
        rw = rewrite.run(provider, persona, card, titles.get(main_doc, ""), question,
                         all_snips + all_ctr, [], external=external, doc_titles=titles)
        log("rewrite", t_rw, {"question": question, "snippets": len(all_snips)},
            {"ok": rw["ok"], "attempts": rw["attempts"], "errors": rw["errors"][:5],
             "chars": len(rw["text"])})
        trace.stage("rewrite", ok=rw["ok"], attempts=rw["attempts"], errors=rw["errors"][:3])
        if rw["ok"]:
            led, cites = assembler.build(turn_id, persona["id"], card, snippets, counter, pins, facts,
                                         external, history, titles, total=budget, mode=mode)
            used_sids = set(re.findall(r"\[(S\d+)\]", rw["text"]))
            cites_used = [c for c in cites if c["sid"] in used_sids]
            return finish(rw["text"], [], cites_used, led, False,
                          {"checked": 0, "passed": [], "failures": [], "ok": True, "mode": "rewrite"}, 0)
        trace.stage("rewrite_fallback", errors=rw["errors"][:3])

    if INTENT_Q.search(question) and persona.get("kind") == "AUTHOR":
        raw_claims = []
        if snippets:
            sn = snippets[0]
            blk = db.one("SELECT * FROM blocks WHERE id=?", (sn["block_id"],))
            sent = split_sentences(blk["text"])[0] if blk else None
            if sent:
                raw_claims = [new_claim(
                    stable_id("c", "intent", sn["block_id"], n=14),
                    "书中没有直接说明这一写作意图；以下只是基于文本结构的解读，不是作者原话。",
                    "INTERPRETATION",
                    [{"block_id": sn["block_id"], "start": sent["start"], "end": sent["end"],
                      "quote": sent["text"], "relation": "context"}],
                    confidence=0.5, role="intent")]
    else:
        positions = _position_claims(db, persona, question)
        if positions:
            raw_claims = positions[:2]
        else:
            t_gen = time.perf_counter()
            raw_claims = generator.generate(db, provider, search_q, snippets, counter, main_doc,
                                            persona_kind=persona.get("kind", "AUTHOR"))
            log("generate", t_gen, {"question": search_q, "snippets": len(snippets)},
                {"claims": len(raw_claims),
                 "generators": sorted({c.get("meta", {}).get("generator", "?") for c in raw_claims})})
    trace.stage("generate", claims=len(raw_claims), provider=getattr(provider, "name", "offline"))

    block_texts = {}
    for did in scope.doc_ids:
        for b in db.q("SELECT id,text FROM blocks WHERE doc_id=?", (did,)):
            block_texts[b["id"]] = b["text"]

    kept, report, dropped = gate_mod.repair(raw_claims, block_texts, scope.allowed_blocks, provider,
                                            allow_interpretation=(mode != "strict"))
    trace.stage("gate", checked=report["checked"], passed=len(kept), failures=report["failures"][:5])
    if not kept:
        return abstain("gate_rejected_all")
    if YESNO.search(question) and not any(c.get("meta", {}).get("role") == "position" for c in kept) \
            and not any(any(p in c["text"] for p in POLAR) for c in kept):
        return abstain("yes_no_without_polar_evidence")

    ordered: list[dict] = []
    seen: set[str] = set()
    for c in kept:
        for e in c["evidence"]:
            if e["block_id"] in seen:
                continue
            row = db.one("SELECT * FROM blocks WHERE id=?", (e["block_id"],))
            if not row:
                continue
            if scope.allowed_blocks is not None and row["id"] not in scope.allowed_blocks:
                continue
            seen.add(e["block_id"])
            ordered.append({"block_id": row["id"], "doc_id": row["doc_id"],
                            "section_id": row["section_id"], "text": row["text"],
                            "page": row["page_from"], "score": 1.0})
    for s in snippets:
        if s["block_id"] not in seen and len(ordered) < 10:
            seen.add(s["block_id"])
            ordered.append(s)
    counter = [c for c in counter if c["block_id"] not in seen]

    led, cites = assembler.build(turn_id, persona["id"], card, ordered, counter, pins, facts,
                                 external, history, titles, total=budget, mode=mode)
    trace.stage("assemble", used=led["budget"]["used"], citations=len(cites))
    opener = ("书中没有对未来的直接预测；书中反复表述过的目标是："
              if future else None)
    text, used = render.render(kept, cites, persona, mode, block_docs, main_doc, titles,
                               opener_override=opener)
    if future and text:
        text += "\n（以上是文本中的目标表述，不是对未来的预测。）"
    if not text:
        return abstain("render_empty")
    return finish(text, kept, cites, led, False, report, dropped)
