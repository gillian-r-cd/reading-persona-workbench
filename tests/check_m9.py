"""M9: local delivery, data sovereignty, migration, offline, performance."""
from __future__ import annotations

import json
import socket
import sqlite3
import tempfile
import time
from pathlib import Path

from tests.harness import Checker
from tests.support import temp_vault
from wb import admin
from wb.config import Config
from wb.db import DB
from wb.ingest.pdf_writer import TextItem, write_pdf
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


def run() -> dict:
    ck = Checker("M9")

    # --- export / import round trip -----------------------------------
    with temp_vault() as (cfg, db):
        wb = Workbench(cfg.vault)
        doc = wb.add_document(CORPUS / "remote_work.md")
        sid = wb.create_session(doc)
        ans = wb.ask(sid, "这本书的核心论点是什么？")
        block = wb.blocks(doc)[1]
        from wb import anchors
        a = anchors.make_anchor(block, 0, min(20, len(block["text"])))
        db.insert("anchors", {"id": "a_test", "doc_id": doc, "block_id": block["id"],
                              "start": a["start"], "end": a["end"], "exact": a["exact"],
                              "prefix": a["prefix"], "suffix": a["suffix"],
                              "text_hash": a["text_hash"], "page": a.get("page"),
                              "doc_version": 1, "status": "ok"})
        db.insert("highlights", {"id": "h_test", "doc_id": doc,
                                 "anchor": json.dumps(a, ensure_ascii=False), "color": "yellow",
                                 "note": "test", "created_at": "2026-01-01T00:00:00+08:00",
                                 "status": "ok", "session_id": sid})
        before = admin.vault_stats(db, cfg)
        out = Path(tempfile.mkdtemp()) / "backup.zip"
        exp = admin.export_vault(cfg, out)
        ck.ge("export bundle produced", exp["bytes"], 100, str(exp))
        ck.true("export excludes api key",
                "api_key" not in json.dumps(json.loads(Path(cfg.vault, "settings.json").read_text("utf-8")))
                if Path(cfg.vault, "settings.json").exists() else True)

        with tempfile.TemporaryDirectory() as td2:
            cfg2 = Config(td2)
            imp = admin.import_vault(cfg2, out, force=True)
            db2 = DB(cfg2.db_path)
            after = admin.vault_stats(db2, cfg2)
            ck.eq("import preserves documents", after["documents"], before["documents"])
            ck.eq("import preserves blocks", after["blocks"], before["blocks"])
            ck.eq("import preserves highlights", after["highlights"], before["highlights"])
            ck.eq("import preserves claims", after["claims"], before["claims"])
            ck.true("imported anchors resolve",
                    db2.one("SELECT count(*) c FROM anchors WHERE id='a_test'")["c"] == 1)
            wb2 = Workbench(td2)
            doc2 = wb2.documents()[0]["id"]
            s2 = wb2.create_session(doc2)
            ans2 = wb2.ask(s2, "这本书的核心论点是什么？")
            ck.true("imported vault answers", not ans2["abstained"] and "瓶颈不是工具" in ans2["text"])

    # --- delete leaves no residue -------------------------------------
    with temp_vault() as (cfg, db):
        wb = Workbench(cfg.vault)
        doc = wb.add_document(CORPUS / "remote_work.md")
        s = wb.create_session(doc)
        wb.ask(s, "核心论点是什么？")
        res = admin.delete_document(db, cfg, doc)
        ck.true("delete reports counts", res["ok"] and res["deleted"]["blocks"] > 0, str(res["deleted"]))
        residue = {}
        for t in ("documents", "blocks", "sections", "chunks", "claims", "personas", "highlights",
                  "entities", "quotes", "ref_entries", "section_summaries"):
            residue[t] = db.one(f"SELECT count(*) c FROM {t} WHERE doc_id=?", (doc,))["c"] if t != "documents" \
                else db.one("SELECT count(*) c FROM documents WHERE id=?", (doc,))["c"]
        ck.eq("delete leaves no residue", sum(residue.values()), 0, json.dumps(residue))
        ck.eq("fts rows removed", db.one("SELECT count(*) c FROM chunks_fts")["c"], 0)
        ck.eq("persona spans removed", db.one("SELECT count(*) c FROM persona_spans")["c"], 0)
        ck.true("integrity after delete", admin.integrity_check(db, cfg)["ok"])

    # --- crash recovery ------------------------------------------------
    with temp_vault() as (cfg, db):
        wb = Workbench(cfg.vault)
        db.insert("documents", {"id": "d_crash", "title": "half", "author": "", "doc_type": "report",
                                "language": "zh", "source_path": "", "source_sha": "x", "version": 1,
                                "created_at": "2026-01-01T00:00:00+08:00", "meta": "{}",
                                "status": "ingesting"})
        db.insert("blocks", {"id": "b_crash", "doc_id": "d_crash", "section_id": "", "kind": "para",
                             "ordinal": 0, "text": "partial", "norm": "partial", "page_from": 1,
                             "page_to": 1, "attrs": "{}", "hash": "x"})
        rec = admin.recover(cfg, db)
        ck.eq("half-ingested document removed", rec["count"], 1)
        ck.eq("crash block removed", db.one("SELECT count(*) c FROM blocks WHERE doc_id='d_crash'")["c"], 0)
        doc = wb.add_document(CORPUS / "remote_work.md")
        ck.true("re-ingest after crash succeeds", len(wb.blocks(doc)) > 30)
        ck.true("integrity after recovery", admin.integrity_check(db, cfg)["ok"])

    # --- migration from v1 --------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "old.db")
        c = sqlite3.connect(path)
        c.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','1');
        CREATE TABLE documents(id TEXT PRIMARY KEY, title TEXT, author TEXT, doc_type TEXT,
          language TEXT, source_path TEXT, source_sha TEXT, version INTEGER, created_at TEXT,
          meta TEXT);
        CREATE TABLE personas(id TEXT PRIMARY KEY, doc_id TEXT, kind TEXT, name TEXT, card TEXT,
          version INTEGER, created_at TEXT);
        """)
        c.commit()
        c.close()
        db = DB(path)
        cols = {r["name"] for r in db.q("PRAGMA table_info(documents)")}
        pcols = {r["name"] for r in db.q("PRAGMA table_info(personas)")}
        ck.true("migration adds documents.status", "status" in cols, str(cols))
        ck.true("migration adds personas.updated_at", "updated_at" in pcols, str(pcols))
        ck.eq("schema version bumped", db.meta_get("schema_version"), 7)

    # --- offline capability -------------------------------------------
    real_socket = socket.socket
    class Blocked(socket.socket):
        def __init__(self, *a, **k):
            raise OSError("network disabled in offline test")
    socket.socket = Blocked
    try:
        with temp_vault() as (cfg, db):
            wb = Workbench(cfg.vault)
            doc = wb.add_document(CORPUS / "ai_adoption.html")
            s = wb.create_session(doc)
            ans = wb.ask(s, "AI 试点比例是多少？")
            ck.true("parse/index/retrieve/ask work offline",
                    not ans["abstained"] and "63%" in ans["text"], ans["text"][:120])
            ck.eq("offline matrix marks research unavailable", admin.offline_matrix()["research"], False)
    finally:
        socket.socket = real_socket

    # --- network audit -------------------------------------------------
    audit = admin.network_audit(ROOT)
    ck.true("only intended modules can reach the network", audit["ok"], str(audit["modules"]))

    # --- performance on a 300-page PDF ---------------------------------
    with tempfile.TemporaryDirectory() as td:
        pdf_path = Path(td) / "big.pdf"
        pages = []
        for p in range(300):
            items = [TextItem(60, 780, 16, f"Chapter {p // 20 + 1}", bold=True)]
            for line in range(24):
                items.append(TextItem(60, 745 - line * 24, 10,
                                      f"Page {p} line {line}: platform standardization reduces search costs."))
            pages.append(items)
        write_pdf(pdf_path, pages, title="Big Report")
        with temp_vault() as (cfg, db):
            t0 = time.perf_counter()
            wb = Workbench(cfg.vault)
            doc = wb.add_document(pdf_path)
            ingest_s = time.perf_counter() - t0
            bench = admin.benchmark(wb, doc)
            ck.ge("300-page pdf block count", bench["blocks"], 5000, f"{bench['blocks']} blocks")
            ck.ge("300-page pdf pages", bench["pages"], 290, f"{bench['pages']} pages")
            ck.le("300-page ingest time (s)", ingest_s, 120, f"{ingest_s:.1f}s")
            ck.le("retrieval p95 (ms)", bench["retrieve_p95_ms"], 800, f"{bench['retrieve_p95_ms']}ms")
            ck.le("db size (MB)", admin.vault_stats(db, cfg)["db_bytes"] / 1e6, 300,
                  f"{admin.vault_stats(db, cfg)['db_bytes'] / 1e6:.1f}MB")
            ck.true("integrity after large ingest", admin.integrity_check(db, cfg)["ok"])

    return ck.result()
