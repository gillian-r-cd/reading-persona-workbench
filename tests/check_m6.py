"""M6: quoted persons, subjects, synthetic critics, roundtable isolation."""
from __future__ import annotations

from pathlib import Path

from tests.harness import Checker
from tests.support import temp_vault
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


def run() -> dict:
    ck = Checker("M6")
    with temp_vault() as (cfg, db):
        wb = Workbench(cfg.vault)
        book = wb.add_document(CORPUS / "remote_work.md")
        personas = wb.personas(book)
        by_kind = {p["kind"]: p for p in personas}
        ck.true("four persona kinds", {"AUTHOR", "QUOTED_PERSON", "SUBJECT", "SYNTHETIC"} <= set(by_kind),
                str(sorted(by_kind)))
        quoted = by_kind.get("QUOTED_PERSON")
        subject = by_kind.get("SUBJECT")
        synthetic = by_kind.get("SYNTHETIC")
        author = by_kind["AUTHOR"]

        def allowed(pid: str) -> set[str]:
            return {r["block_id"] for r in db.q("SELECT block_id FROM persona_spans WHERE persona_id=?", (pid,))}

        qa = allowed(quoted["id"])
        sa = allowed(subject["id"])
        ck.ge("quoted persona has evidence set", len(qa), 1, f"{len(qa)}")
        ck.ge("subject persona has evidence set", len(sa), 1, f"{len(sa)}")

        # quoted person answers only from its own record
        sid = wb.create_session(book, quoted["id"])
        r = wb.ask(sid, "你为什么说没有输在工具上？")
        ck.true("quoted persona answers", not r["abstained"] and "没有输在工具上" in r["text"], r["text"][:120])
        ck.eq("quoted citations inside radius", {c["block_id"] for c in r["citations"]} - qa, set())

        r = wb.ask(sid, "你对制造业怎么看？")
        ck.true("quoted persona refuses out-of-record", r["abstained"], r["text"][:120])
        ck.eq("quoted refusal still inside radius", {c["block_id"] for c in r["citations"]} - qa, set())

        # subject persona (Bloom) answers only about itself
        sid = wb.create_session(book, subject["id"])
        r = wb.ask(sid, "Bloom 发现了什么？")
        ck.true("subject persona answers", not r["abstained"] and "13%" in r["text"], r["text"][:160])
        ck.eq("subject citations inside radius", {c["block_id"] for c in r["citations"]} - sa, set())
        r = wb.ask(sid, "陈薇说了什么？")
        ck.true("subject refuses unrelated records", r["abstained"], r["text"][:120])

        # synthetic is labelled
        sid = wb.create_session(book, synthetic["id"])
        r = wb.ask(sid, "这本书的结论可靠吗？")
        ck.true("synthetic critic labelled", ("审稿人" in r["text"]) or r["text"].startswith("文本没有"),
                r["text"][:120])

        # roundtable: shared question, isolated evidence
        table = wb.roundtable(book, "远程办公的瓶颈是什么？")
        ck.eq("roundtable includes all personas", len(table), 4, str([t["kind"] for t in table]))
        texts = {t["text"] for t in table if t["text"]}
        ck.ge("roundtable personas distinguishable", len(texts), 3, f"{len(texts)} distinct")
        contamination = 0
        for t in table:
            p = next(x for x in personas if x["id"] == t["persona_id"])
            if p["kind"] in ("QUOTED_PERSON", "SUBJECT"):
                rad = allowed(p["id"])
                contamination += sum(1 for c in t["citations"] if c["block_id"] not in rad)
        ck.eq("roundtable contamination", contamination, 0, f"{len(table)} answers checked")

        # each persona's answer stays in its own radius when asked the same question
        for p in (quoted, subject):
            ans = wb.ask(wb.create_session(book, p["id"]), "远程办公的瓶颈是什么？")
            rad = allowed(p["id"])
            bad = [c["block_id"] for c in ans["citations"] if c["block_id"] not in rad]
            ck.eq(f"radius isolation: {p['kind']}", bad, [])

    return ck.result()
