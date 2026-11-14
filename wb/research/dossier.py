"""Deep research: plan -> fetch -> snapshot -> claims -> dossier -> fact check."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..util import now_iso, sha256_text, split_sentences, stable_id, tokenize
from .fetch import FACT_TIERS, detect_injection, record_source

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
NEG_RE = re.compile(r"不|没|未|无|not\b|no\b|never", re.I)
CLAIM_CUES = ("表明", "显示", "发现", "认为", "数据", "研究", "调查", "报告", "增加", "下降",
              "提升", "reduce", "increase", "show", "found", "study", "report", "suggest")


def plan_queries(subject: str) -> list[str]:
    return [f"{subject} 研究", f"{subject} 评论", f"{subject} 数据", f"{subject} 争议"]


def _overlap(a: str, b: str) -> float:
    at, bt = set(tokenize(a)), set(tokenize(b))
    if not at or not bt:
        return 0.0
    return len(at & bt) / min(len(at), len(bt))


def extract_source_claims(source_id: str, text: str, topic: str = "") -> list[dict]:
    out = []
    for i, sent in enumerate(split_sentences(text)):
        s = sent["text"]
        if len(s) < 25:
            continue
        if not (NUM_RE.search(s) or any(c in s.lower() for c in CLAIM_CUES)):
            continue
        out.append({
            "id": stable_id("sc", source_id, str(i), sha256_text(s, 8), n=14),
            "source_id": source_id, "text": s, "quote": s, "locator": f"p{i}",
            "confidence": 0.8 if NUM_RE.search(s) else 0.6, "topic": topic,
        })
    return out


def _numbers(text: str) -> set[str]:
    return set(NUM_RE.findall(text))


def _negated(text: str) -> bool:
    return bool(NEG_RE.search(text))


ANTONYMS = [("increase", "decrease"), ("increase", "reduce"), ("higher", "lower"),
            ("提升", "下降"), ("增加", "减少"), ("高于", "低于"), ("支持", "反对"),
            ("有效", "无效"), ("快", "慢")]


def _percent(nums: set[str]) -> set[str]:
    return {n for n in nums if n.endswith("%")}


def contradiction(a: dict, b: dict, threshold: float = 0.45) -> bool:
    if a["source_id"] == b["source_id"]:
        return False
    ov = _overlap(a["text"], b["text"])
    if ov < threshold:
        return False
    ta, tb = a["text"].lower(), b["text"].lower()
    na, nb = _numbers(a["text"]), _numbers(b["text"])
    pa, pb = _percent(na), _percent(nb)
    # conflicting percentages are the clearest signal
    if pa and pb and not (pa & pb):
        return True
    # single-number statements that disagree
    if len(na) == 1 and len(nb) == 1 and not (na & nb):
        return True
    for x, y in ANTONYMS:
        if (x in ta and y in tb) or (y in ta and x in tb):
            return True
    if ov > 0.7 and _negated(a["text"]) != _negated(b["text"]):
        return True
    return False


def detect_contradictions(claims: list[dict], threshold: float = 0.45) -> list[dict]:
    out = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            if contradiction(claims[i], claims[j], threshold):
                out.append({"a": claims[i]["id"], "b": claims[j]["id"],
                            "a_text": claims[i]["text"][:160], "b_text": claims[j]["text"][:160]})
    return out


def run_research(cfg, db, session_id: str, subject: str, fetcher,
                 urls: list[str] | None = None, search=None, max_sources: int = 6) -> dict:
    import secrets
    job_id = stable_id("rj", session_id, subject, secrets.token_hex(5), n=14)
    db.insert("research_jobs", {"id": job_id, "session_id": session_id, "subject": subject,
                                "status": "running", "created_at": now_iso(), "finished_at": "",
                                "error": "", "dossier_id": ""})
    queries = plan_queries(subject)
    if urls is None:
        urls = list(search(queries)) if search else []
    urls = urls[:max_sources]
    if not urls:
        db.x("UPDATE research_jobs SET status=?, finished_at=?, error=? WHERE id=?",
             ("offline", now_iso(), "no_urls_or_search_unavailable", job_id))
        return {"status": "offline", "job_id": job_id, "sources": 0, "claims": 0,
                "reason": "没有可用的检索来源（未配置搜索或未提供 URL）"}

    sources, claims, failures = [], [], 0
    for url in urls:
        try:
            fr = fetcher.fetch(url)
        except Exception:
            failures += 1
            continue
        if fr.status != 200 or not fr.body:
            failures += 1
            continue
        src = record_source(db, cfg, fr)
        sources.append(src)
        if src.get("injected"):
            continue
        sc = extract_source_claims(src["id"], src["text"], subject)
        for c in sc:
            db.insert_ignore("source_claims", c)
        claims.extend(sc)

    if not sources:
        db.x("UPDATE research_jobs SET status=?, finished_at=?, error=? WHERE id=?",
             ("offline", now_iso(), f"all_fetches_failed:{failures}", job_id))
        return {"status": "offline", "job_id": job_id, "sources": 0, "claims": 0,
                "reason": "全部来源抓取失败（离线或不可达）"}

    conflicts = detect_contradictions(claims)
    trust: dict[str, int] = {}
    for s in sources:
        trust[s["tier"]] = trust.get(s["tier"], 0) + 1
    sections = []
    for tier_label, heading in (("academic", "学术来源"), ("institutional", "机构来源"),
                                ("reviewed", "权威媒体"), ("news", "新闻"), ("blog", "博客"),
                                ("unknown", "其他来源")):
        group = [c for c in claims if any(s["id"] == c["source_id"] and s["tier"] == tier_label for s in sources)]
        if not group:
            continue
        sections.append({"heading": heading, "tier": tier_label,
                         "claims": [{**c, "url": next(s["final_url"] for s in sources if s["id"] == c["source_id"])}
                                    for c in group[:6]]})
    summary = " ".join(c["text"] for sec in sections for c in sec["claims"][:2])[:700]
    dossier_id = stable_id("dos", subject, *[s["snapshot_hash"] for s in sources], n=14)
    sections_json = json.dumps(sections, ensure_ascii=False)
    db.insert("dossiers", {
        "id": dossier_id, "subject": subject, "sections": sections_json,
        "freshness": now_iso(), "trust_summary": json.dumps(trust, ensure_ascii=False),
        "created_at": now_iso(), "query": json.dumps(queries, ensure_ascii=False),
    })
    db.x("UPDATE research_jobs SET status=?, finished_at=?, dossier_id=? WHERE id=?",
         ("done", now_iso(), dossier_id, job_id))
    return {"status": "done", "job_id": job_id, "dossier_id": dossier_id,
            "sources": len(sources), "claims": len(claims), "trust": trust,
            "contradictions": len(conflicts), "summary": summary,
            "injected_sources": sum(1 for s in sources if s.get("injected")),
            "source_list": [{"title": s.get("title", ""), "url": s.get("final_url", ""),
                             "tier": s.get("tier", ""), "injected": bool(s.get("injected"))}
                            for s in sources]}


def attach(db, session_id: str, dossier_id: str) -> None:
    db.insert_ignore("session_dossiers", {"session_id": session_id, "dossier_id": dossier_id,
                                          "added_at": now_iso()})


def attached(db, session_id: str, limit: int = 3) -> list[dict]:
    rows = db.q(
        "SELECT d.*, sd.added_at AS attached_at FROM session_dossiers sd "
        "JOIN dossiers d ON d.id = sd.dossier_id WHERE sd.session_id=? "
        "ORDER BY sd.added_at DESC LIMIT ?", (session_id, limit))
    out = []
    for r in rows:
        d = dict(r)
        d["sections"] = json.loads(d["sections"])
        d["trust_summary"] = json.loads(d["trust_summary"])
        out.append(d)
    return out


def external_text(db, session_id: str, limit: int = 2, max_chars: int = 1400) -> str:
    parts = []
    for d in attached(db, session_id, limit):
        claims = [c["text"] for sec in d["sections"] for c in sec["claims"]][:5]
        src = [f"{s.get('title') or s.get('url')}（{s.get('tier')}）"
               for sec in d["sections"] for c in sec["claims"][:1] for s in [c]]
        head = f"主题：{d['subject']}；来源：{', '.join(str(x) for x in d['trust_summary'])}"
        parts.append(head + "\n" + "\n".join(f"- {c}" for c in claims))
    text = "\n\n".join(parts)
    return text[:max_chars]


def load_dossier(db, dossier_id: str) -> dict | None:
    row = db.one("SELECT * FROM dossiers WHERE id=?", (dossier_id,))
    if not row:
        return None
    d = dict(row)
    d["sections"] = json.loads(d["sections"])
    d["trust_summary"] = json.loads(d["trust_summary"])
    return d


def dossier_claims(db, dossier_id: str) -> list[dict]:
    d = load_dossier(db, dossier_id)
    if not d:
        return []
    out = []
    for sec in d["sections"]:
        out.extend(sec["claims"])
    return out


def fact_check(db, doc_id: str, dossier_id: str, limit: int = 20) -> list[dict]:
    docs_claims = [dict(r) for r in db.q(
        "SELECT * FROM claims WHERE doc_id=? AND role IN ('thesis','claim','evidence') LIMIT ?",
        (doc_id, limit))]
    ext = dossier_claims(db, dossier_id)
    sources = {r["id"]: dict(r) for r in db.q("SELECT * FROM sources")}
    out = []
    for c in docs_claims:
        cands = [e for e in ext if _overlap(c["text"], e["text"]) >= 0.45]
        verdict = "UNDETERMINED"
        evidence = []
        refs = set()
        for e in cands:
            src = sources.get(e["source_id"], {})
            evidence.append({"text": e["text"][:200], "url": src.get("final_url", ""),
                             "tier": src.get("tier", "unknown")})
            refs.add(e["source_id"])
        if any(contradiction({"source_id": "doc", "text": c["text"]}, e) for e in cands):
            verdict = "CONFLICT"
        elif len(refs) >= 2 and all(sources.get(e["source_id"], {}).get("tier") in FACT_TIERS for e in cands):
            verdict = "CONSISTENT"
        elif len(refs) >= 2:
            verdict = "UNDETERMINED"
        out.append({"claim_id": c["id"], "text": c["text"], "verdict": verdict,
                    "evidence": evidence[:3]})
    return out
