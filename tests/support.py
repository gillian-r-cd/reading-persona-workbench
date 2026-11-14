"""Shared test helpers."""
from __future__ import annotations

import tempfile
from contextlib import contextmanager

from wb.config import Config
from wb.db import DB


@contextmanager
def temp_vault():
    with tempfile.TemporaryDirectory(prefix="wb_test_") as td:
        cfg = Config(td)
        db = DB(cfg.db_path)
        yield cfg, db
