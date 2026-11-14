"""M3: retrieval quality, evidence radius, counter-evidence, assembly ledger."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from tests.harness import Checker
from tests.support import temp_vault
from wb.context import assembler
from wb.contracts import validate_ledger
from wb.index import chunker, retrieve
from wb.ingest import pipeline
from wb.semantics import extractor
from wb.util import canonical

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"
QA = json.loads((ROOT / "fixtures" / "golden" / "qa.json").read_text("utf-8"))


def run() -> dict:
    ck = Checker("M3")
    with temp_vault() as (cfg, db):
        docs = {}
        for f in ("remote_work.md", "platform_economics.txt", "ai_adoption.html"):
            did = pipeline.ingest(cfg, db, CORPUS / f)
            extractor.analyze(db, did)
            chunker.build_index(db, did)
            docs[f] = did

        hits = 0
        ndcg = 0.0
        for item in QA:
            scope = retrieve.Scope([docs[item["doc"]]])
            res = retrieve.search(db, scope, item["q"], k=10)
            rank = None
            for i, r in enumerate(res):
                if canonical(item["expect"]) in canonical(r["text"]):
                    rank = i
                    break
            if rank is not None:
                hits += 1
                ndcg += 1.0 / math.log2(rank + 2)
        ck.ge("span recall@10", hits / len(QA), 0.85, f"{hits}/{len(QA)}")
        ck.ge("nDCG@10", ndcg / len(QA), 0.70, f"{ndcg / len(QA):.3f}")

        # evidence radius (persona scope) is a hard filter
        remote = docs["remote_work.md"]
        sec = db.one("SELECT * FROM sections WHERE doc_id=? AND title LIKE '%第三章%'", (remote,))
        allowed = {r["id"] for r in db.q("SELECT id FROM blocks WHERE section_id=?", (sec["id"],))}
        radius_scope = retrieve.Scope([remote], allowed_blocks=allowed, name="chapter3")
        res = retrieve.search(db, radius_scope, "远程办公", k=10)
        violations = [r for r in res if not set(r["block_ids"]) <= allowed]
        ck.eq("evidence radius violations", len(violations), 0, f"{len(res)} chunks checked")

        # mandatory counter-evidence
        counter = retrieve.counter_search(db, retrieve.Scope([remote]), "远程办公适用于所有团队吗", k=4)
        ck.ge("counter-evidence retrieved", len(counter), 2, f"{len(counter)}")
        ck.true("counter-evidence contains boundary",
                any("不适用于" in c["text"] or "仅适用于" in c["text"] for c in counter))

        # assembly
        scope = retrieve.Scope([remote])
        chunks = retrieve.search(db, scope, "远程办公的边界条件是什么？", k=5)
        snippets = retrieve.to_snippets(db, chunks, max_blocks=3)
        ck.ge("snippets produced", len(snippets), 3, f"{len(snippets)}")
        card = assembler.card_text({"name": "林知远", "kind": "AUTHOR",
                                    "modes": {"textual_author": {"scope": [remote]}},
                                    "voice": {"register": "克制、书面"}})
        led, cites = assembler.build("t1", "p1", card, snippets, counter, total=4000)
        ck.eq("ledger contract valid", validate_ledger(led), [])
        ck.le("ledger within budget", led["budget"]["used"], 4000)
        ck.eq("citation ids unique", len({c["sid"] for c in cites}), len(cites))
        ck.eq("citation blocks resolve",
              sum(1 for c in cites if db.one("SELECT id FROM blocks WHERE id=?", (c["block_id"],))), len(cites))
        led2, _ = assembler.build("t1", "p1", card, snippets, counter, total=4000)
        from wb.context.ledger import structural_hash
        ck.eq("assembly deterministic", structural_hash(led), structural_hash(led2))
        ck.eq("no duplicate snippets", len({s["block_id"] for s in snippets}), len(snippets))

        # over-budget trimming
        led3, _ = assembler.build("t2", "p1", card, snippets, counter, total=300)
        ck.le("over-budget assembly trims", led3["budget"]["used"], 300)
        ck.true("dropped items recorded", len(led3["dropped"]) > 0, str(led3["dropped"]))

        # not-found
        ck.true("unrelated query -> NOT_FOUND", retrieve.not_found(db, scope, "火星殖民计划的预算"))
        ck.true("unrelated query 2 -> NOT_FOUND", retrieve.not_found(db, scope, "量子计算机的制冷方案"))
        ck.true("related query is not NOT_FOUND", not retrieve.not_found(db, scope, "这本书的核心论点是什么"))

        # latency
        times = []
        for i in range(20):
            t0 = time.perf_counter()
            retrieve.search(db, scope, QA[i % 10]["q"], k=10)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        ck.le("retrieval p95 latency (ms)", times[18], 800, f"{times[18]:.1f}ms")

    return ck.result()
