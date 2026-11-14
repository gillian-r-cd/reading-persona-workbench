"""Turn traces: every input/output/decision persisted for audit and replay."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .context import ledger as ledger_mod
from .util import now_iso, sha256_text, stable_id


class Trace:
    def __init__(self, vault: Path, session_id: str, turn_id: str):
        self.vault = Path(vault)
        self.session_id = session_id
        self.turn_id = turn_id
        self.stages: list[dict] = []
        self.ledger: dict | None = None
        self.request: dict = {}
        self.response: dict = {}

    def stage(self, name: str, **data) -> None:
        self.stages.append({"name": name, "at": now_iso(), **data})

    def set_ledger(self, led: dict) -> None:
        self.ledger = led

    def payload(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "stages": self.stages,
            "ledger": self.ledger,
            "request": self.request,
            "response": self.response,
            "created_at": now_iso(),
        }

    def save(self) -> str:
        d = self.vault / "traces" / self.session_id
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.turn_id}.json"
        p.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=1), "utf-8")
        return str(p)


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text("utf-8"))


def rebuild_prompt(trace: dict) -> tuple[str, str]:
    """Return (prompt_text, structural_hash) from a trace. Must be deterministic."""
    led = trace.get("ledger")
    if not led:
        raise ValueError("trace has no ledger")
    text = ledger_mod.to_prompt_text(led)
    return text, sha256_text(text, 32)


def new_turn_id() -> str:
    import time
    return stable_id("t", str(time.time_ns()), n=12)
