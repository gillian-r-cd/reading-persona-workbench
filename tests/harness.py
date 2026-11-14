"""Tiny self-contained check harness -> scorecards (no external test framework)."""
from __future__ import annotations

import traceback
from typing import Any


class Checker:
    def __init__(self, milestone: str):
        self.milestone = milestone
        self.checks: list[dict] = []
        self.metrics: dict[str, Any] = {}

    def check(self, name: str, ok: bool, detail: str = "", **metrics) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:600]})
        self.metrics.update(metrics)
        return bool(ok)

    def eq(self, name: str, actual, expected, detail: str = "", **metrics) -> bool:
        ok = actual == expected
        return self.check(name, ok, detail or f"actual={actual!r} expected={expected!r}", **metrics)

    def ge(self, name: str, actual: float, threshold: float, detail: str = "", **metrics) -> bool:
        ok = actual >= threshold
        return self.check(name, ok, detail or f"actual={actual:.4f} >= {threshold}", **metrics)

    def le(self, name: str, actual: float, threshold: float, detail: str = "", **metrics) -> bool:
        ok = actual <= threshold
        return self.check(name, ok, detail or f"actual={actual:.4f} <= {threshold}", **metrics)

    def true(self, name: str, cond, detail: str = "", **metrics) -> bool:
        return self.check(name, bool(cond), detail, **metrics)

    def metric(self, **kw) -> None:
        self.metrics.update(kw)

    def run_guard(self, name: str, fn) -> None:
        try:
            fn(self)
        except Exception as exc:  # a crashed check is a failed check
            self.check(name, False, f"exception: {exc}\n{traceback.format_exc()[:500]}")

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks) and len(self.checks) > 0

    def result(self) -> dict:
        return {
            "milestone": self.milestone,
            "passed": self.passed,
            "checks": self.checks,
            "metrics": self.metrics,
            "failed": [c["name"] for c in self.checks if not c["ok"]],
        }
