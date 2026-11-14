"""M2: non-fiction semantics (argument map, entities, quotes, references)."""
from __future__ import annotations

import json
from pathlib import Path

from tests.harness import Checker
from tests.make_fixtures import build_all
from tests.support import temp_vault
from wb.contracts import validate_claim
from wb.ingest import pipeline
from wb.semantics import extractor

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"
GOLD = json.loads((ROOT / "fixtures" / "golden" / "semantics_remote_work.json").read_text("utf-8"))


def run() -> dict:
    ck = Checker("M2")
    build_all()
    with temp_vault() as (cfg, db):
        docs = {
            "remote": pipeline.ingest(cfg, db, CORPUS / "remote_work.md"),
            "paper": pipeline.ingest(cfg, db, CORPUS / "platform_economics.txt"),
            "report": pipeline.ingest(cfg, db, CORPUS / "ai_adoption.html"),
            "review": pipeline.ingest(cfg, db, CORPUS / "review_remote_work.md"),
            "interview": pipeline.ingest(cfg, db, CORPUS / "author_interview.md"),
        }
        results = {}
        for key, did in docs.items():
            results[key] = extractor.analyze(db, did)

        ck.eq("all documents analyzed", all(docs.values()) and all(r["claims"] > 5 for r in results.values()), True,
              json.dumps({k: v["claims"] for k, v in results.items()}))

        # claims are structurally valid and self-cited
        total = 0
        bad = 0
        for did in docs.values():
            blocks = pipeline.text_map(db, did)
            for row in db.q("SELECT * FROM claims WHERE doc_id=?", (did,)):
                ev = db.one("SELECT * FROM claim_evidence WHERE claim_id=?", (row["id"],))
                total += 1
                if not ev:
                    bad += 1
                    continue
                c = {"id": row["id"], "text": row["text"], "type": row["claim_type"],
                     "evidence": [{"block_id": ev["block_id"], "start": ev["start"], "end": ev["end"],
                                   "quote": ev["quote"], "relation": "supports"}],
                     "qualifiers": json.loads(row["qualifiers"] or "[]"),
                     "confidence": row["confidence"]}
                if validate_claim(c, blocks):
                    bad += 1
        ck.eq("claims structurally valid (100%)", bad, 0, f"{total} claims, {bad} invalid")

        remote = results["remote"]
        thesis = db.one("SELECT text FROM claims WHERE id=?", (remote["thesis_id"],))
        ck.true("thesis selected", thesis is not None)
        ck.true("thesis captures core claim",
                all(k in (thesis["text"] if thesis else "") for k in GOLD["thesis_contains"]),
                thesis["text"] if thesis else "")

        # section structure
        titles = [r["title"] for r in db.q("SELECT title FROM sections WHERE doc_id=? AND level>0 ORDER BY ordinal", (docs["remote"],))]
        ck.eq("section titles match fixture", titles, GOLD["section_titles"])

        # argument edges
        rels = [r["relation"] for r in db.q("SELECT relation FROM argument_edges WHERE doc_id=?", (docs["remote"],))]
        ck.ge("support edges exist", rels.count("supported_by"), 5, f"{rels.count('supported_by')}")
        ck.ge("counter edges exist", rels.count("contradicts"), 2, f"{rels.count('contradicts')}")
        ck.ge("thesis supports subclaims", rels.count("supports"), 3, f"{rels.count('supports')}")

        # quotes
        quotes = [dict(r) for r in db.q("SELECT * FROM quotes WHERE doc_id=?", (docs["remote"],))]
        ck.eq("quote extracted", len(quotes), 1, f"{len(quotes)}")
        if quotes:
            q = quotes[0]
            ck.eq("quote verbatim", q["text"], GOLD["quote"]["text"])
            ck.eq("quote speaker attributed", q["speaker_name"], GOLD["quote"]["speaker"])
            ck.ge("quote attribution confidence", q["confidence"], 0.8, f"{q['confidence']}")
        ck.eq("no misattributed quotes", sum(1 for q in quotes if q["status"] == "ok" and not q["speaker_name"]), 0)

        # entities
        names = {r["canonical_name"] for r in db.q("SELECT canonical_name FROM entities WHERE doc_id=?", (docs["remote"],))}
        ck.true("expected entities present", all(n in names for n in GOLD["entities_include"]), str(sorted(names)))
        ck.true("no noun false positives as persons",
                not any(n in names for n in ("文档", "工具", "公司", "团队")), str(sorted(names)))

        # qualifiers / counters
        quals = [r["text"] for r in db.q("SELECT text FROM claims WHERE doc_id=? AND role='qualifier'", (docs["remote"],))]
        ck.true("boundary qualifiers extracted", all(any(q in t for t in quals) for q in GOLD["qualifiers_contain"]),
                str(quals)[:300])
        ctrs = [r["text"] for r in db.q("SELECT text FROM claims WHERE doc_id=? AND role='counter'", (docs["remote"],))]
        ck.true("counter arguments extracted", all(any(c in t for t in ctrs) for c in GOLD["counters_contain"]),
                str(ctrs)[:300])

        # references
        refs = [dict(r) for r in db.q("SELECT * FROM ref_entries WHERE doc_id=? ORDER BY rowid", (docs["remote"],))]
        ck.eq("reference count", len(refs), 3, f"{len(refs)}")
        ck.eq("references parsed", sum(1 for r in refs if r["parsed"]), 3)
        for exp in GOLD["refs"]:
            hit = [r for r in refs if r["year"] == exp["year"] and exp["title_contains"] in r["title"]]
            ck.true(f"reference matched: {exp['title_contains']}", len(hit) > 0, str([(r['year'], r['title']) for r in refs]))
        links = db.q("SELECT * FROM ref_links WHERE doc_id=?", (docs["remote"],))
        ck.ge("in-text reference links", len(links), 2, f"{len(links)}")

        # summaries
        sums = [dict(r) for r in db.q("SELECT * FROM section_summaries WHERE doc_id=?", (docs["remote"],))]
        ck.ge("section summaries", len(sums), 6, f"{len(sums)}")
        ck.true("summaries bounded", all(len(s["text"]) <= 150 for s in sums))
        ck.true("summaries cited", all(json.loads(s["citation_ids"]) for s in sums))

        # every document gets a thesis
        ck.true("thesis for every document", all(r["thesis_id"] for r in results.values()),
                str({k: v["thesis_id"] for k, v in results.items()}))

    return ck.result()
