"""Runtime configuration and vault layout."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = Path(os.environ.get("WB_VAULT", str(ROOT / "vault")))

SUBDIRS = ["originals", "snapshots", "traces", "logs", "exports", "models"]


class Config:
    def __init__(self, vault: str | Path = DEFAULT_VAULT):
        self.vault = Path(vault).resolve()
        self.ensure()

    def ensure(self) -> None:
        self.vault.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:
            (self.vault / d).mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> str:
        return str(self.vault / "meta.db")

    def path(self, *parts: str) -> Path:
        p = self.vault.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def settings_path(self) -> Path:
        return self.vault / "settings.json"

    def load_settings(self) -> dict:
        if self.settings_path.exists():
            try:
                return json.loads(self.settings_path.read_text("utf-8"))
            except Exception:
                return {}
        return {}

    def save_settings(self, data: dict) -> None:
        self.settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    def search_settings(self) -> dict:
        s = self.load_settings().get("search", {}) or {}
        return {
            "mode": s.get("mode", "none"),
            "endpoint": s.get("endpoint", ""),
            "api_key": s.get("api_key", ""),
        }

    def provider_settings(self) -> dict:
        s = self.load_settings().get("provider", {})
        return {
            "mode": s.get("mode", "offline"),
            "base_url": s.get("base_url", ""),
            "api_key": s.get("api_key", "") or os.environ.get("OPENAI_API_KEY", ""),
            "model": s.get("model", "gpt-4o-mini"),
            "embed_model": s.get("embed_model", ""),
            "timeout": s.get("timeout", 60),
        }
