"""The strict gate: six checks every claim must pass before it can be rendered."""
from __future__ import annotations

import re

from ..contracts import FACT_TYPES, validate_claim
from ..util import canonical, match_key

GATE_NAMES = ["G1_structure", "G2_quote", "G3_support", "G4_number", "G5_qualifier", "G6_radius"]


def evaluate(claims: list[dict], block_texts: dict[str, str],
             radius_blocks: set[str] | None, provider, nli_threshold: float = 0.55) -> dict:
    failures: list[dict] = []
    for c in claims:
        errs = validate_claim(c, block_texts)
        if errs:
            failures.append({"claim_id": c.get("id"), "gate": "G1_structure", "detail": "; ".join(errs)})
            continue
        ev = c["evidence"]
        # G2: quote must be verbatim at the recorded offsets
        g2 = False
        for i, e in enumerate(ev):
            bt = block_texts.get(e["block_id"], "")
            if match_key(bt[e["start"]:e["end"]]) != match_key(e["quote"]):
                failures.append({"claim_id": c["id"], "gate": "G2_quote", "detail": f"evidence[{i}]"})
                g2 = True
                break
        if g2:
            continue
        if c.get("type") == "INTERPRETATION":
            if radius_blocks is not None:
                out = [e["block_id"] for e in ev if e["block_id"] not in radius_blocks]
                if out:
                    failures.append({"claim_id": c["id"], "gate": "G6_radius", "detail": str(out)})
            continue
        # G4: every number must be traceable to the evidence
        nums = set(re.findall(r"\d+(?:[.,]\d+)?%?", c["text"]))
        if nums:
            pool = " ".join(e["quote"] for e in ev) + " " + " ".join(
                block_texts.get(e["block_id"], "") for e in ev)
            missing = [n for n in nums if n not in pool]
            if missing:
                failures.append({"claim_id": c["id"], "gate": "G4_number",
                                 "detail": f"untraceable numbers: {missing}"})
                continue
        # G5: qualifiers must survive verbatim
        lost = [q for q in c.get("qualifiers", []) if match_key(q) not in match_key(c["text"])]
        if lost:
            failures.append({"claim_id": c["id"], "gate": "G5_qualifier", "detail": f"lost: {lost}"})
            continue
        # G3: entailment
        premise = " ".join(e["quote"] for e in ev)
        score = provider.nli(premise, c["text"])
        if score < nli_threshold:
            failures.append({"claim_id": c["id"], "gate": "G3_support",
                             "detail": f"nli={score:.2f} < {nli_threshold}"})
            continue
        # G6: evidence radius
        if radius_blocks is not None:
            out = [e["block_id"] for e in ev if e["block_id"] not in radius_blocks]
            if out:
                failures.append({"claim_id": c["id"], "gate": "G6_radius", "detail": str(out)})
                continue
    failed_ids = {f["claim_id"] for f in failures}
    return {
        "checked": len(claims),
        "passed": [c["id"] for c in claims if c["id"] not in failed_ids],
        "failures": failures,
        "ok": len(failures) == 0,
    }


def repair(claims: list[dict], block_texts: dict[str, str],
           radius_blocks: set[str] | None, provider,
           allow_interpretation: bool = False) -> tuple[list[dict], dict, int]:
    """Drop failing claims. Returns (kept, report, dropped_count)."""
    report = evaluate(claims, block_texts, radius_blocks, provider)
    bad = {f["claim_id"] for f in report["failures"]}
    kept = [c for c in claims if c["id"] not in bad]
    if not allow_interpretation:
        kept = [c for c in kept if c["type"] in FACT_TYPES]
    return kept, report, len(bad)
