"""SQLite storage: schema, migrations, small helpers. Single-file vault."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

SCHEMA_VERSION = 7

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, title TEXT, author TEXT, doc_type TEXT, language TEXT,
  source_path TEXT, source_sha TEXT, version INTEGER DEFAULT 1,
  created_at TEXT, meta TEXT DEFAULT '{}', status TEXT DEFAULT 'ready'
);

CREATE TABLE IF NOT EXISTS sections(
  id TEXT PRIMARY KEY, doc_id TEXT, parent_id TEXT, kind TEXT, title TEXT,
  ordinal INTEGER, path TEXT, level INTEGER
);
CREATE INDEX IF NOT EXISTS ix_sections_doc ON sections(doc_id, ordinal);

CREATE TABLE IF NOT EXISTS blocks(
  id TEXT PRIMARY KEY, doc_id TEXT, section_id TEXT, kind TEXT, ordinal INTEGER,
  text TEXT, norm TEXT, page_from INTEGER, page_to INTEGER, attrs TEXT DEFAULT '{}',
  hash TEXT
);
CREATE INDEX IF NOT EXISTS ix_blocks_doc ON blocks(doc_id, ordinal);
CREATE INDEX IF NOT EXISTS ix_blocks_section ON blocks(section_id, ordinal);

CREATE TABLE IF NOT EXISTS anchors(
  id TEXT PRIMARY KEY, doc_id TEXT, block_id TEXT, start INTEGER, end INTEGER,
  exact TEXT, prefix TEXT, suffix TEXT, text_hash TEXT, page INTEGER,
  doc_version INTEGER, status TEXT DEFAULT 'ok'
);
CREATE INDEX IF NOT EXISTS ix_anchors_doc ON anchors(doc_id, status);

CREATE TABLE IF NOT EXISTS overrides(
  id TEXT PRIMARY KEY, doc_id TEXT, target_type TEXT, target_id TEXT,
  patch TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS entities(
  id TEXT PRIMARY KEY, doc_id TEXT, type TEXT, canonical_name TEXT,
  aliases TEXT DEFAULT '[]', first_block_id TEXT, meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_entities_doc ON entities(doc_id, type);

CREATE TABLE IF NOT EXISTS entity_mentions(
  id TEXT PRIMARY KEY, entity_id TEXT, doc_id TEXT, block_id TEXT,
  start INTEGER, end INTEGER, kind TEXT, confidence REAL
);

CREATE TABLE IF NOT EXISTS claims(
  id TEXT PRIMARY KEY, doc_id TEXT, block_id TEXT, section_id TEXT,
  claim_type TEXT, role TEXT, text TEXT, confidence REAL,
  qualifiers TEXT DEFAULT '[]', meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_claims_doc ON claims(doc_id, role);

CREATE TABLE IF NOT EXISTS claim_evidence(
  id TEXT PRIMARY KEY, claim_id TEXT, block_id TEXT, start INTEGER, end INTEGER,
  quote TEXT, relation TEXT, confidence REAL
);

CREATE TABLE IF NOT EXISTS argument_edges(
  id TEXT PRIMARY KEY, doc_id TEXT, src_id TEXT, dst_id TEXT, relation TEXT,
  rationale TEXT
);

CREATE TABLE IF NOT EXISTS quotes(
  id TEXT PRIMARY KEY, doc_id TEXT, block_id TEXT, start INTEGER, end INTEGER,
  text TEXT, speaker_entity_id TEXT, speaker_name TEXT, attribution TEXT,
  confidence REAL, status TEXT DEFAULT 'ok'
);
CREATE INDEX IF NOT EXISTS ix_quotes_doc ON quotes(doc_id);

CREATE TABLE IF NOT EXISTS ref_entries(
  id TEXT PRIMARY KEY, doc_id TEXT, block_id TEXT, raw TEXT,
  authors TEXT, year TEXT, title TEXT, venue TEXT, doi TEXT, url TEXT,
  parsed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ref_links(
  id TEXT PRIMARY KEY, doc_id TEXT, from_block_id TEXT, ref_entry_id TEXT,
  marker TEXT, confidence REAL
);

CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY, doc_id TEXT, section_id TEXT, level TEXT,
  block_ids TEXT, text TEXT, token_count INTEGER, embedding BLOB,
  meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id, level);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, chunk_id UNINDEXED, doc_id UNINDEXED, tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS personas(
  id TEXT PRIMARY KEY, doc_id TEXT, kind TEXT, name TEXT, card TEXT,
  version INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, origin TEXT DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS ix_personas_doc ON personas(doc_id, kind);

CREATE TABLE IF NOT EXISTS persona_corpora(
  persona_id TEXT, doc_id TEXT, kind TEXT, added_at TEXT,
  PRIMARY KEY(persona_id, doc_id)
);

CREATE TABLE IF NOT EXISTS persona_spans(
  persona_id TEXT, block_id TEXT, weight REAL DEFAULT 1.0,
  PRIMARY KEY(persona_id, block_id)
);

CREATE TABLE IF NOT EXISTS highlights(
  id TEXT PRIMARY KEY, doc_id TEXT, anchor TEXT, color TEXT, note TEXT,
  created_at TEXT, status TEXT DEFAULT 'ok', session_id TEXT
);

CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, doc_ids TEXT, persona_id TEXT, mode TEXT,
  created_at TEXT, settings TEXT DEFAULT '{}', title TEXT,
  project_id TEXT DEFAULT '', updated_at TEXT
);

CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
  claims TEXT DEFAULT '[]', citations TEXT DEFAULT '[]', meta TEXT DEFAULT '{}',
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS ledger(
  id TEXT PRIMARY KEY, session_id TEXT, fact TEXT, source TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS pins(
  id TEXT PRIMARY KEY, session_id TEXT, target_type TEXT, target_id TEXT,
  weight REAL DEFAULT 1.0, created_at TEXT
);

CREATE TABLE IF NOT EXISTS research_jobs(
  id TEXT PRIMARY KEY, session_id TEXT, subject TEXT, status TEXT,
  created_at TEXT, finished_at TEXT, error TEXT, dossier_id TEXT
);

CREATE TABLE IF NOT EXISTS sources(
  id TEXT PRIMARY KEY, url TEXT, final_url TEXT, title TEXT, fetched_at TEXT,
  snapshot_hash TEXT, snapshot_path TEXT, tier TEXT, content_hash TEXT,
  http_status INTEGER, meta TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS source_claims(
  id TEXT PRIMARY KEY, source_id TEXT, text TEXT, quote TEXT, locator TEXT,
  confidence REAL, topic TEXT
);
CREATE INDEX IF NOT EXISTS ix_source_claims_source ON source_claims(source_id);

CREATE TABLE IF NOT EXISTS dossiers(
  id TEXT PRIMARY KEY, subject TEXT, sections TEXT, freshness TEXT,
  trust_summary TEXT, created_at TEXT, query TEXT
);

CREATE TABLE IF NOT EXISTS traces(
  id TEXT PRIMARY KEY, session_id TEXT, turn_id TEXT, payload TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_traces_session ON traces(session_id, created_at);

CREATE TABLE IF NOT EXISTS section_summaries(
  section_id TEXT PRIMARY KEY, doc_id TEXT, text TEXT, citation_ids TEXT,
  method TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, name TEXT, description TEXT,
  created_at TEXT, updated_at TEXT, settings TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS project_documents(
  project_id TEXT, doc_id TEXT, role TEXT DEFAULT 'source', added_at TEXT,
  PRIMARY KEY(project_id, doc_id)
);
CREATE TABLE IF NOT EXISTS project_links(
  id TEXT PRIMARY KEY, project_id TEXT, src_doc TEXT, dst_doc TEXT,
  kind TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS session_dossiers(
  session_id TEXT, dossier_id TEXT, added_at TEXT,
  PRIMARY KEY(session_id, dossier_id)
);

CREATE TABLE IF NOT EXISTS ai_calls(
  id TEXT PRIMARY KEY, session_id TEXT, turn_id TEXT, kind TEXT,
  provider TEXT, model TEXT, latency_ms REAL, status TEXT, error TEXT,
  request TEXT, response TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls_session ON ai_calls(session_id, created_at);

CREATE TABLE IF NOT EXISTS eval_runs(
  id TEXT PRIMARY KEY, milestone TEXT, metrics TEXT, passed INTEGER,
  created_at TEXT, git_commit TEXT
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def conn(self):
        c = self.connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def init(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)
            row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                c.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) < SCHEMA_VERSION:
                self._migrate(c, int(row["value"]))
                c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))

    def _migrate(self, c: sqlite3.Connection, from_v: int) -> None:
        # Forward-only migrations. Each step is additive; v1->v2->v3 keeps data.
        if from_v < 2:
            c.execute("ALTER TABLE documents ADD COLUMN status TEXT DEFAULT 'ready'")
        if from_v < 6:
            scols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
            if "project_id" not in scols:
                c.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT DEFAULT ''")
            if "updated_at" not in scols:
                c.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT")
            pcols2 = {r["name"] for r in c.execute("PRAGMA table_info(personas)")}
            if "origin" not in pcols2:
                c.execute("ALTER TABLE personas ADD COLUMN origin TEXT DEFAULT 'auto'")
        if from_v < 3:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(personas)")}
            if "updated_at" not in cols:
                c.execute("ALTER TABLE personas ADD COLUMN updated_at TEXT")

    # --- small helpers -------------------------------------------------
    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.conn() as c:
            return list(c.execute(sql, args))

    def one(self, sql: str, args=()):
        with self.conn() as c:
            return c.execute(sql, args).fetchone()

    def x(self, sql: str, args=()) -> None:
        with self.conn() as c:
            c.execute(sql, args)

    def insert(self, table: str, row: dict) -> None:
        cols = ",".join(row.keys())
        ph = ",".join("?" for _ in row)
        with self.conn() as c:
            c.execute(f"INSERT INTO {table}({cols}) VALUES({ph})", tuple(row.values()))

    def insert_ignore(self, table: str, row: dict) -> None:
        cols = ",".join(row.keys())
        ph = ",".join("?" for _ in row)
        with self.conn() as c:
            c.execute(f"INSERT OR IGNORE INTO {table}({cols}) VALUES({ph})", tuple(row.values()))

    def insert_many(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = ",".join(rows[0].keys())
        ph = ",".join("?" for _ in rows[0])
        with self.conn() as c:
            c.executemany(f"INSERT INTO {table}({cols}) VALUES({ph})",
                          [tuple(r.values()) for r in rows])

    def meta_get(self, key: str, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def meta_set(self, key: str, value) -> None:
        v = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        with self.conn() as c:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, v))
