"""Non-fiction semantics: argument structure, entities, quotes, references.

All extraction is deterministic and offline. Every claim is bound to its own
sentence span so it can be cited verbatim.
"""
from __future__ import annotations

import re

from ..contracts import new_claim
from ..util import canonical, match_key, sha256_text, split_sentences, stable_id

CLAIM_CUES = ["认为", "表明", "说明", "因此", "所以", "总之", "核心主张", "结论", "建议",
              "应当", "必须", "意味着", "关键在于", "本质", "第一", "第二", "第三",
              "argue", "show", "suggest", "therefore", "conclude", "should", "must",
              "imply", "indicate", "we find", "this paper"]
EVID_CUES = ["数据", "研究", "调查", "样本", "实验", "统计", "问卷", "跟踪", "观察到",
             "发现", "报告", "显示", "达到", "占比", "dataset", "study", "survey",
             "sample", "experiment", "we collected", "percent", "p <"]
QUAL_CUES = ["仅适用于", "只适用于", "不适用于", "除非", "在满足", "条件下",
             "限于", "局限于", "边界", "前提", "only", "unless", "limited to",
             "under the condition", "applies to"]
COUNTER_CUES = ["然而", "但是", "尽管", "相反", "批评", "反方", "质疑", "异议", "另一种",
                "however", "although", "critics", "contrary", "on the other hand",
                "nevertheless", "but "]
DEF_CUES = ["定义为", "所谓", "指的是", "我们把", "means", "defined as", "refers to"]

STOP_NAMES = {"我们", "他们", "她们", "你们", "作者", "本书", "本文", "研究", "数据", "团队",
              "公司", "企业", "员工", "管理者", "学者", "有人", "这种", "这个", "那个",
              "其中", "第一", "第二", "第三", "因此", "但是", "然而", "例如", "比如",
              "the", "this", "that", "we", "they", "it", "study", "paper",
              "文档", "工具", "平台", "系统", "报告", "论文", "行业", "市场", "结果",
              "方法", "问题", "样本", "数据", "团队", "企业", "公司", "员工", "管理者",
              "document", "platform", "system", "report", "paper", "market", "result",
              "method", "sample", "data", "team", "company"}

SPEECH = "说|指出|表示|认为|写道|称|强调|补充|回应|问|答"
PERSON_RE = re.compile(r"([\u4e00-\u9fff]{2,4})(?=(?:是|说|指出|表示|认为|写道|称|强调|在|对|与|和|将|把))")
NAME_BAD_SUFFIX = ("度", "性", "力", "率", "者", "化", "的", "了", "是", "在", "和", "与", "对", "将", "把", "这", "那", "其", "此", "们")
NAME_BAD_SUB = ("研究", "数据", "公司", "团队", "企业", "员工", "管理", "样本", "行业", "问题", "方法", "结果",
                "此", "该", "其", "我们", "他们", "同志们")
NAME_BAD_START = ("没", "不", "是", "在", "和", "与", "对", "把", "将", "被", "让", "给", "从",
                  "向", "于", "为", "的", "此", "该", "其", "这", "那", "所", "以", "而", "但",
                  "却", "也", "都", "就", "又", "并", "则", "即", "如", "若", "由", "自")
ORG_RE = re.compile(r"([\u4e00-\u9fff]{2,12}(?:公司|集团|研究院|大学|银行|机构|出版社|实验室))")
EN_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b")
QUOTE_RE = re.compile(r"[\u201c\u201d\"]([^\u201c\u201d\"]{2,300})[\u201c\u201d\"]")


def _has(text: str, cues: list[str]) -> bool:
    low = text.lower()
    return any(c.lower() in low for c in cues)


def classify(sentence: str) -> tuple[str, float]:
    s = sentence.strip()
    has_num = bool(re.search(r"\d", s))
    counter = _has(s, COUNTER_CUES)
    qual = _has(s, QUAL_CUES)
    evid = _has(s, EVID_CUES) or (has_num and ("%" in s or "年" in s or "倍" in s))
    claim = _has(s, CLAIM_CUES)
    definition = _has(s, DEF_CUES)
    if counter:
        return "counter", 0.8
    if definition:
        return "definition", 0.8
    if qual and not evid:
        return "qualifier", 0.75
    if evid:
        return "evidence", 0.8
    if claim:
        return "claim", 0.7
    return "statement", 0.35


def extract_claims(doc_id: str, blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    claims: list[dict] = []
    edges: list[dict] = []
    for b in blocks:
        if b["kind"] not in ("para", "quote", "list_item"):
            continue
        text = b["text"]
        for sent in split_sentences(text):
            st = sent["text"]
            if len(st) < 6:
                continue
            if st.startswith(("问：", "问:", "Q:", "Q：", "访谈者", "访谈时间")):
                continue
            role, conf = classify(st)
            cid = stable_id("c", doc_id, b["id"], str(sent["start"]), n=14)
            claim = new_claim(
                cid, st, "FACT_EXPLICIT",
                [{"block_id": b["id"], "start": sent["start"], "end": sent["end"],
                  "quote": st, "relation": "supports"}],
                qualifiers=_qualifiers(st), confidence=conf,
                role=role, section_id=b["section_id"],
            )
            claims.append(claim)
            edges.append({"src": cid, "dst": b["section_id"], "relation": "in_section"})
    return claims, edges


def _qualifiers(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"(仅适用于[^。；;]{0,20}|只适用于[^。；;]{0,20}|不适用于[^。；;]{0,20}|"
                         r"在[^。；;]{0,20}条件下|除非[^。；;]{0,20}|仅限于[^。；;]{0,20})", text):
        out.append(m.group(1))
    if not out and _has(text, QUAL_CUES):
        out.append(text[:24])
    return out[:3]


def select_thesis(claims: list[dict]) -> str | None:
    for c in claims:
        if any(k in c["text"] for k in ("核心主张", "本文认为", "本书认为", "we argue", "this paper argues")):
            return c["id"]
    for c in claims:
        if c.get("meta", {}).get("role") == "claim":
            return c["id"]
    return claims[0]["id"] if claims else None


def build_argument_edges(doc_id: str, claims: list[dict], thesis_id: str | None) -> list[dict]:
    edges: list[dict] = []
    by_section: dict[str, list[dict]] = {}
    for c in claims:
        by_section.setdefault(c["meta"].get("section_id", ""), []).append(c)
    for sec, group in by_section.items():
        anchors = [c for c in group if c["meta"].get("role") in ("claim", "definition")]
        last_claim = anchors[0]["id"] if anchors else None
        for c in group:
            role = c["meta"].get("role")
            if role == "evidence" and last_claim:
                edges.append(_edge(doc_id, last_claim, c["id"], "supported_by"))
            elif role == "counter":
                target = anchors[-1]["id"] if anchors else last_claim
                if target:
                    edges.append(_edge(doc_id, c["id"], target, "contradicts"))
            elif role == "qualifier" and last_claim:
                edges.append(_edge(doc_id, last_claim, c["id"], "qualified_by"))
            if role in ("claim", "definition"):
                last_claim = c["id"]
    for c in claims:
        if c["meta"].get("role") == "claim" and thesis_id and c["id"] != thesis_id:
            edges.append(_edge(doc_id, thesis_id, c["id"], "supports"))
    return edges


def _edge(doc_id: str, src: str, dst: str, rel: str) -> dict:
    return {"id": stable_id("e", doc_id, src, dst, rel, n=14), "doc_id": doc_id,
            "src_id": src, "dst_id": dst, "relation": rel, "rationale": ""}


def extract_entities(doc: dict, blocks: list[dict]) -> list[dict]:
    counts: dict[str, dict] = {}

    def add(name: str, etype: str, bid: str, start: int, end: int, kind: str) -> None:
        name = name.strip()
        if not name or name.lower() in STOP_NAMES or len(name) < 2:
            return
        rec = counts.setdefault(name, {"name": name, "type": etype, "mentions": []})
        rec["mentions"].append({"block_id": bid, "start": start, "end": end, "kind": kind})

    for b in blocks:
        for m in PERSON_RE.finditer(b["text"]):
            name = m.group(1)
            if (name in STOP_NAMES or name.endswith(NAME_BAD_SUFFIX)
                    or any(x in name for x in NAME_BAD_SUB) or name.startswith(NAME_BAD_START)):
                continue
            add(name, "person", b["id"], m.start(1), m.end(1), "mention")
        for m in ORG_RE.finditer(b["text"]):
            add(m.group(1), "org", b["id"], m.start(1), m.end(1), "mention")
        for m in EN_NAME_RE.finditer(b["text"]):
            if m.group(1).lower() not in STOP_NAMES:
                add(m.group(1), "person", b["id"], m.start(1), m.end(1), "mention")
    author = (doc.get("author") or "").strip()
    if author:
        for b in blocks:
            idx = b["text"].find(author)
            if idx >= 0:
                add(author, "person", b["id"], idx, idx + len(author), "author")
    out = []
    for name, rec in counts.items():
        if len(rec["mentions"]) < 2 and not any(m["kind"] in ("author", "speaker") for m in rec["mentions"]):
            continue
        eid = stable_id("en", doc["id"], name, n=12)
        out.append({"id": eid, "doc_id": doc["id"], "type": rec["type"], "name": name,
                    "aliases": [], "first_block_id": rec["mentions"][0]["block_id"],
                    "mentions": rec["mentions"]})
    return out


def extract_quotes(doc_id: str, blocks: list[dict], entities: list[dict]) -> list[dict]:
    names = set()
    for b in blocks:
        for m in PERSON_RE.finditer(b["text"]):
            n = m.group(1)
            if (n not in STOP_NAMES and not n.endswith(NAME_BAD_SUFFIX)
                    and not any(x in n for x in NAME_BAD_SUB) and not n.startswith(NAME_BAD_START)):
                names.add(n)
    names |= {e["name"] for e in entities if e["type"] == "person"}
    out = []
    for b in blocks:
        text = b["text"]
        for m in QUOTE_RE.finditer(text):
            quote = m.group(1).strip()
            if len(quote) < 4:
                continue
            prefix = text[max(0, m.start() - 160):m.start()]
            speaker = None
            conf = 0.3
            direct = re.search(r"([\u4e00-\u9fff]{2,4})(?:%s)[：:]?\s*$" % SPEECH, prefix)
            if direct and direct.group(1) not in STOP_NAMES:
                speaker = direct.group(1)
                conf = 0.9
            else:
                cands = [n for n in names if n in prefix]
                if cands:
                    speaker = max(cands, key=lambda n: prefix.rfind(n))
                    conf = 0.8
            out.append({
                "id": stable_id("q", doc_id, b["id"], str(m.start(1)), n=12),
                "doc_id": doc_id, "block_id": b["id"], "start": m.start(1), "end": m.end(1),
                "text": quote, "speaker_name": speaker, "speaker_entity_id": "",
                "attribution": prefix[-80:], "confidence": conf,
                "status": "ok" if speaker else "unattributed",
            })
    return out


def extract_refs(doc_id: str, sections: list[dict], blocks: list[dict]) -> list[dict]:
    ref_sections = {s["id"] for s in sections if ("参考文献" in s["title"] or "references" in s["title"].lower())}
    out = []
    for b in blocks:
        if b["section_id"] not in ref_sections and not re.match(r"^\[\d+\]", b["text"].strip()):
            continue
        for line in b["text"].split("\n"):
            line = line.strip()
            if not re.match(r"^\[\d+\]", line):
                continue
            raw = line
            year_m = re.findall(r"(19|20)\d{2}", line)
            year = re.search(r"\b(19|20)\d{2}\b", line)
            authors = ""
            title = ""
            venue = ""
            doi = ""
            url = ""
            m = re.match(r"^\[\d+\]\s*(.+)$", line)
            body = m.group(1) if m else line
            if "[J]" in body or "[M]" in body:
                marker = "[J]" if "[J]" in body else "[M]"
                head = body.split(marker)[0]
                parts = re.split(r"\.\s+", head)
                authors = parts[0].strip()
                title = parts[-1].strip() if len(parts) > 1 else head.strip()
                tail = body.split(marker, 1)[1]
                tail = tail.lstrip(".").strip()
                venue = re.split(r",\s*(?:19|20)\d{2}", tail)[0].strip()
            else:
                parts = [p.strip() for p in body.split(".") if p.strip()]
                authors = parts[0] if parts else body
                title = parts[1] if len(parts) > 1 else ""
            dm = re.search(r"10\.\d{4,9}/[^\s]+", body)
            if dm:
                doi = dm.group(0).rstrip(".")
            um = re.search(r"https?://[^\s]+", body)
            if um:
                url = um.group(0)
            out.append({
                "id": stable_id("r", doc_id, raw[:80], n=12), "doc_id": doc_id,
                "block_id": b["id"], "raw": raw, "authors": authors[:120],
                "year": year.group(0) if year else "", "title": title[:200],
                "venue": venue[:120], "doi": doi, "url": url, "parsed": 1 if title else 0,
            })
    return out


def link_refs(doc_id: str, blocks: list[dict], refs: list[dict]) -> list[dict]:
    out = []
    surname_year = []
    for r in refs:
        surname = re.split(r"[,，\s]", r["authors"])[0].strip()
        if surname:
            surname_year.append((surname, r["year"], r["id"]))
    for b in blocks:
        text = b["text"]
        for surname, year, rid in surname_year:
            if not surname or not year:
                continue
            if surname in text and year in text:
                out.append({"id": stable_id("rl", doc_id, b["id"], rid, n=12), "doc_id": doc_id,
                            "from_block_id": b["id"], "ref_entry_id": rid,
                            "marker": f"{surname} {year}", "confidence": 0.8})
    return out


def summarize_sections(doc_id: str, sections: list[dict], claims: list[dict]) -> list[dict]:
    by_sec: dict[str, list[dict]] = {}
    for c in claims:
        by_sec.setdefault(c["meta"].get("section_id", ""), []).append(c)
    out = []
    for s in sections:
        group = by_sec.get(s["id"], [])
        if not group:
            continue
        ranked = sorted(group, key=lambda c: (c["meta"].get("role") in ("claim", "definition"),
                                              c["confidence"], -len(c["text"])), reverse=True)
        picked = ranked[:2]
        text = "".join(c["text"] for c in picked)[:150]
        out.append({"section_id": s["id"], "doc_id": doc_id, "text": text,
                    "citation_ids": [c["id"] for c in picked], "method": "extractive"})
    return out


def analyze(db, doc_id: str) -> dict:
    from ..ingest.pipeline import blocks_of

    doc_row = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    secs = [dict(r) for r in db.q("SELECT * FROM sections WHERE doc_id=? ORDER BY ordinal", (doc_id,))]
    blocks = blocks_of(db, doc_id)

    claims, _ = extract_claims(doc_id, blocks)
    thesis_id = select_thesis(claims)
    for c in claims:
        if c["id"] == thesis_id:
            c["meta"]["role"] = "thesis"
    edges = build_argument_edges(doc_id, claims, thesis_id)
    entities = extract_entities({"id": doc_id, "author": doc_row["author"]}, blocks)
    quotes = extract_quotes(doc_id, blocks, entities)
    refs = extract_refs(doc_id, secs, blocks)
    links = link_refs(doc_id, blocks, refs)
    summaries = summarize_sections(doc_id, secs, claims)

    name_to_entity = {e["name"]: e["id"] for e in entities}
    for q in quotes:
        sp = q.get("speaker_name")
        if sp and sp not in name_to_entity:
            eid = stable_id("en", doc_id, sp, n=12)
            entities.append({"id": eid, "doc_id": doc_id, "type": "person", "name": sp,
                             "aliases": [], "first_block_id": q["block_id"],
                             "mentions": [{"block_id": q["block_id"], "start": q["start"],
                                           "end": q["end"], "kind": "speaker"}]})
            name_to_entity[sp] = eid
        q["speaker_entity_id"] = name_to_entity.get(sp, "")

    for c in claims:
        db.insert_ignore("claims", {
            "id": c["id"], "doc_id": doc_id, "block_id": c["evidence"][0]["block_id"],
            "section_id": c["meta"].get("section_id", ""), "claim_type": c["type"],
            "role": c["meta"].get("role", "statement"), "text": c["text"],
            "confidence": c["confidence"],
            "qualifiers": __import__("json").dumps(c["qualifiers"], ensure_ascii=False),
            "meta": __import__("json").dumps(c["meta"], ensure_ascii=False),
        })
        ev = c["evidence"][0]
        db.insert_ignore("claim_evidence", {
            "id": stable_id("ce", c["id"], ev["block_id"], str(ev["start"]), n=12),
            "claim_id": c["id"], "block_id": ev["block_id"], "start": ev["start"],
            "end": ev["end"], "quote": ev["quote"], "relation": "supports",
            "confidence": c["confidence"],
        })
    for e in edges:
        db.insert_ignore("argument_edges", e)
    for e in entities:
        db.insert_ignore("entities", {"id": e["id"], "doc_id": doc_id, "type": e["type"],
                               "canonical_name": e["name"], "aliases": "[]",
                               "first_block_id": e["first_block_id"], "meta": "{}"})
        for m in e["mentions"]:
            db.insert_ignore("entity_mentions", {
                "id": stable_id("em", e["id"], m["block_id"], str(m["start"]), n=12),
                "entity_id": e["id"], "doc_id": doc_id, "block_id": m["block_id"],
                "start": m["start"], "end": m["end"], "kind": m["kind"],
                "confidence": 0.8,
            })
    for q in quotes:
        db.insert_ignore("quotes", q)
    for r in refs:
        db.insert_ignore("ref_entries", r)
    for link in links:
        db.insert_ignore("ref_links", link)
    for s in summaries:
        db.insert_ignore("section_summaries", {**s, "citation_ids": __import__("json").dumps(s["citation_ids"], ensure_ascii=False), "created_at": ""})

    return {
        "doc_id": doc_id, "claims": len(claims), "thesis_id": thesis_id,
        "edges": len(edges), "entities": len(entities), "quotes": len(quotes),
        "refs": len(refs), "ref_links": len(links), "summaries": len(summaries),
    }
