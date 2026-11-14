#!/usr/bin/env python3
"""Milestone verification runner.

Usage: python verify.py M0 M1 ... | python verify.py all
Writes eval/scorecard-<M>.json and exits non-zero on any failure.
"""
from __future__ import annotations

import importlib
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wb.config import Config  # noqa: E402
from wb.db import DB  # noqa: E402
from wb.util import now_iso  # noqa: E402

ALL = ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "PRODUCT", "BYOK", "REWRITE", "SEARCH"]


def run_one(m: str) -> dict:
    name = f"tests.check_{m.lower()}"
    try:
        mod = importlib.import_module(name)
        result = mod.run()
    except Exception as exc:
        result = {"milestone": m, "passed": False, "checks": [
            {"name": "import/run", "ok": False, "detail": f"{exc}\n{traceback.format_exc()[:800]}"}
        ], "metrics": {}, "failed": ["import/run"]}
    result["ts"] = now_iso()
    out = ROOT / "eval"
    out.mkdir(exist_ok=True)
    (out / f"scorecard-{m}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    try:
        cfg = Config()
        db = DB(cfg.db_path)
        db.insert("eval_runs", {
            "id": f"run_{m}_{now_iso()}", "milestone": m,
            "metrics": json.dumps(result.get("metrics", {}), ensure_ascii=False),
            "passed": 1 if result["passed"] else 0, "created_at": now_iso(), "git_commit": "",
        })
    except Exception:
        pass
    return result


def show(result: dict) -> None:
    m = result["milestone"]
    ok = result["passed"]
    print(f"\n=== {m} {'PASS' if ok else 'FAIL'} ===")
    for c in result["checks"]:
        mark = "ok  " if c["ok"] else "FAIL"
        print(f"  [{mark}] {c['name']}: {c['detail'][:160]}")
    if result.get("metrics"):
        print("  metrics: " + json.dumps(result["metrics"], ensure_ascii=False)[:400])


def main(argv: list[str]) -> int:
    targets = argv[1:] or ["all"]
    if targets == ["all"]:
        targets = ALL
    results = []
    for m in targets:
        r = run_one(m.upper())
        show(r)
        results.append(r)
    failed = [r["milestone"] for r in results if not r["passed"]]
    print("\nSUMMARY:", " | ".join(f"{r['milestone']}={'PASS' if r['passed'] else 'FAIL'}" for r in results))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
