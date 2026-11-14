"""M0: contracts, anchors, ledger, trace replay."""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from tests.harness import Checker
from wb import anchors, contracts
from wb.context import ledger as L
from wb.trace import Trace, load, rebuild_prompt
from wb.util import dumps, sha256_text


def _block(text: str, bid: str = "b_1") -> dict:
    return {"id": bid, "text": text, "page_from": 1, "page_to": 1}


def run() -> dict:
    ck = Checker("M0")

    # --- contract positives / negatives --------------------------------
    text = "远程办公的瓶颈不是工具，而是书面协作能力。"
    blk = _block(text)
    good = contracts.new_claim(
        "c1", text, "FACT_EXPLICIT",
        [{"block_id": "b_1", "start": 0, "end": len(text), "quote": text,
          "relation": "supports"}], confidence=0.9)
    ck.eq("claim schema accepts valid claim", contracts.validate_claim(good, {"b_1": text}), [])

    bad_cases = {
        "missing evidence": dict(good, evidence=[]),
        "bad type": dict(good, type="OPINION"),
        "quote mismatch": dict(good, evidence=[{"block_id": "b_1", "start": 0, "end": 4,
                                                "quote": "完全不同的引文", "relation": "supports"}]),
        "bad confidence": dict(good, confidence=1.7),
        "unknown block": dict(good, evidence=[{"block_id": "b_404", "start": 0, "end": 4,
                                               "quote": text[:4], "relation": "supports"}]),
    }
    for name, case in bad_cases.items():
        errs = contracts.validate_claim(case, {"b_1": text})
        ck.true(f"claim schema rejects: {name}", len(errs) > 0, "; ".join(errs))

    persona = {
        "id": "p1", "kind": "AUTHOR", "name": "林知远",
        "modes": {"textual_author": {"scope": ["d1"]},
                  "historical_author": {"scope": ["d1", "corpus"]}},
        "evidence_radius": {"spans": "all_in_doc", "external_corpora": [], "forbidden": []},
        "positions": [], "voice": {}, "boundaries": {},
    }
    ck.eq("persona schema accepts author", contracts.validate_persona(persona), [])
    broken = json.loads(json.dumps(persona))
    broken["modes"] = {"textual_author": {"scope": ["d1"]}}
    ck.true("persona schema rejects author without both modes", len(contracts.validate_persona(broken)) > 0)

    anchor = anchors.make_anchor(blk, 4, 12)
    ck.eq("anchor schema accepts", contracts.validate_anchor(anchor), [])
    ck.true("anchor schema rejects bad offsets", len(contracts.validate_anchor(dict(anchor, start=9, end=3))) > 0)

    led = L.make_ledger("t1", "p1", 1000, [
        {"name": "persona_card", "tokens": 100, "body": "card"},
        {"name": "evidence", "tokens": 200, "body": "S1 ..."},
    ], citations=["S1"])
    ck.eq("ledger schema accepts", contracts.validate_ledger(led), [])
    over = json.loads(json.dumps(led))
    over["sections"][0]["tokens"] = 5000
    over["budget"]["used"] = 5200
    ck.true("ledger schema rejects budget overflow", len(contracts.validate_ledger(over)) > 0)

    # --- anchor round trip 10k ----------------------------------------
    rnd = random.Random(7)
    alphabet = "abcdefghijklmnopqrstuvwxyz，。！？、；："
    ok = 0
    total = 0
    for i in range(200):
        t = "".join(rnd.choice(alphabet) for _ in range(200))
        b = _block(t, f"b_{i}")
        for _ in range(50):
            s = rnd.randrange(0, 180)
            e = s + rnd.randrange(1, 20)
            a = anchors.make_anchor(b, s, e)
            r = anchors.resolve_anchor(a, {b["id"]: t})
            total += 1
            ok += (r["status"] == "ok" and r["start"] == s and r["end"] == e)
    ck.ge("anchor round-trip (10k)", ok / total, 1.0, f"{ok}/{total}")

    # --- perturbation: edits elsewhere in the paragraph ----------------
    ok = 0
    lost = 0
    total = 0
    for i in range(300):
        t = "".join(rnd.choice(alphabet) for _ in range(240))
        b = _block(t, "b")
        s = 60
        e = 120
        a = anchors.make_anchor(b, s, e)
        # insert ~2% noise away from the quoted span
        chars = list(t)
        for _ in range(5):
            pos = rnd.choice([rnd.randrange(0, 55), rnd.randrange(125, 239)])
            chars.insert(pos, rnd.choice(alphabet))
        new_t = "".join(chars)
        r = anchors.resolve_anchor(a, {"b": new_t})
        total += 1
        ok += (r["status"] == "ok")
        lost += (r["status"] == "orphaned")
    ck.ge("anchor survives 2% edits", ok / total, 0.99, f"{ok}/{total}")
    ck.eq("no anchor silently lost", lost, 0)

    # --- ledger reversibility ------------------------------------------
    text1 = L.render(led)
    back = L.parse(text1)
    ck.eq("ledger render/parse reversible", dumps(back), dumps(led))
    ck.eq("ledger render deterministic", sha256_text(L.render(led), 32), sha256_text(text1, 32))

    # --- trace replay ---------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        tr = Trace(Path(td), "s1", "t1")
        tr.set_ledger(led)
        tr.stage("retrieve", candidates=3)
        tr.request = {"question": "核心论点是什么"}
        path = tr.save()
        loaded = load(path)
        p1, h1 = rebuild_prompt(loaded)
        p2, h2 = rebuild_prompt(loaded)
        ck.eq("trace replay deterministic", h1, h2)
        ck.eq("trace replay matches rendered prompt", p1, L.to_prompt_text(led))

    return ck.result()
