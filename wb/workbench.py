"""High-level facade used by the HTTP API, CLI and tests."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from . import admin, devlog, projects
from .config import Config
from .context import assembler
from .db import DB
from .index import chunker
from .ingest import pipeline
from .personas import registry
from .providers.base import get_provider
from .semantics import extractor
from .strict import answer as answer_mod
from .util import now_iso, stable_id


class Workbench:
    def __init__(self, vault: str | Path | None = None):
        self.cfg = Config(vault) if vault else Config()
        self.db = DB(self.cfg.db_path)
        self.provider = get_provider(self.cfg.provider_settings())

    # --- ingestion -----------------------------------------------------
    def add_document(self, path: str | Path, doc_type: str | None = None) -> str:
        doc_id = pipeline.ingest(self.cfg, self.db, path, doc_type=doc_type)
        self.analyze(doc_id)
        return doc_id

    def analyze(self, doc_id: str) -> dict:
        if not self.db.one("SELECT 1 FROM claims WHERE doc_id=? LIMIT 1", (doc_id,)):
            extractor.analyze(self.db, doc_id)
        from .ingest.repair import repair_section_titles
        repair_section_titles(self.db, doc_id)
        chunker.build_index(self.db, doc_id)
        registry.ensure_personas(self.db, doc_id)
        return {"doc_id": doc_id}

    # --- documents -----------------------------------------------------
    def documents(self, q: str = "") -> list[dict]:
        rows = self.db.q("SELECT * FROM documents ORDER BY created_at DESC")
        out = []
        for r in rows:
            d = dict(r)
            meta = json.loads(d.get("meta") or "{}")
            d["tags"] = meta.get("tags", [])
            d["block_count"] = self.db.one("SELECT count(*) c FROM blocks WHERE doc_id=?", (d["id"],))["c"]
            if q and q.lower() not in (d["title"] or "").lower() and q.lower() not in (d["author"] or "").lower():
                continue
            d.pop("meta", None)
            d.pop("source_path", None)
            out.append(d)
        return out

    def document(self, doc_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        return dict(r) if r else None

    def rename_document(self, doc_id: str, title: str, tags: list[str] | None = None) -> dict:
        if title:
            self.db.x("UPDATE documents SET title=? WHERE id=?", (title, doc_id))
        if tags is not None:
            row = self.db.one("SELECT meta FROM documents WHERE id=?", (doc_id,))
            meta = json.loads(row["meta"] or "{}") if row else {}
            meta["tags"] = tags
            self.db.x("UPDATE documents SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), doc_id))
        return {"ok": True}

    def delete_document(self, doc_id: str) -> dict:
        return admin.delete_document(self.db, self.cfg, doc_id)

    def outline(self, doc_id: str) -> list[dict]:
        return [dict(r) for r in self.db.q(
            "SELECT * FROM sections WHERE doc_id=? ORDER BY ordinal", (doc_id,))]

    def blocks(self, doc_id: str, section_id: str | None = None) -> list[dict]:
        if section_id:
            rows = self.db.q("SELECT * FROM blocks WHERE doc_id=? AND section_id=? ORDER BY ordinal",
                             (doc_id, section_id))
        else:
            rows = self.db.q("SELECT * FROM blocks WHERE doc_id=? ORDER BY ordinal", (doc_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["attrs"] = json.loads(d.get("attrs") or "{}")
            out.append(d)
        return out

    # --- projects ------------------------------------------------------
    def create_project(self, name: str, description: str = "") -> dict:
        return projects.create(self.db, name, description)

    def projects(self) -> list[dict]:
        return projects.list_all(self.db)

    def project(self, project_id: str) -> dict | None:
        return projects.get(self.db, project_id)

    def update_project(self, project_id: str, name: str | None = None,
                       description: str | None = None) -> dict | None:
        return projects.update(self.db, project_id, name, description)

    def delete_project(self, project_id: str) -> dict:
        return projects.delete(self.db, project_id)

    def add_project_document(self, project_id: str, doc_id: str, role: str = "source") -> dict:
        return projects.add_document(self.db, project_id, doc_id, role)

    def remove_project_document(self, project_id: str, doc_id: str) -> dict:
        return projects.remove_document(self.db, project_id, doc_id)

    def project_documents(self, project_id: str) -> list[dict]:
        return projects.documents(self.db, project_id)

    def add_project_link(self, project_id: str, src: str, dst: str, kind: str = "related",
                         note: str = "") -> dict:
        return projects.add_link(self.db, project_id, src, dst, kind, note)

    def delete_project_link(self, project_id: str, link_id: str) -> dict:
        return projects.delete_link(self.db, project_id, link_id)

    def project_graph(self, project_id: str) -> dict:
        return projects.graph(self.db, project_id)

    # --- personas ------------------------------------------------------
    def personas(self, doc_id: str) -> list[dict]:
        return registry.ensure_personas(self.db, doc_id)

    def persona(self, persona_id: str) -> dict | None:
        return registry.get_persona(self.db, persona_id)

    def create_persona(self, doc_id: str, name: str, kind: str = "QUOTED_PERSON",
                       doc_ids: list[str] | None = None, description: str = "",
                       register: str = "") -> dict:
        docs = doc_ids or [doc_id]
        pid = stable_id("p", doc_id, "manual", name, secrets.token_hex(4), n=14)
        card = {
            "id": pid, "doc_id": doc_id, "kind": kind, "name": name,
            "modes": {"self": {"scope": docs, "description": description or "手动创建的人格"}},
            "evidence_radius": {"spans": "all_in_doc", "doc_ids": docs,
                                "external_corpora": [], "forbidden": []},
            "positions": [], "corpora": [],
            "voice": {"register": register or "自然、克制", "avoid": ["客服腔", "替作者辩护"]},
            "boundaries": {"cannot_know": ["书中没有记录的信息"],
                           "must_disclaim": ["超出记录范围的推断"]},
            "origin": "manual", "description": description, "created_at": now_iso(),
        }
        self.db.insert("personas", {
            "id": pid, "doc_id": doc_id, "kind": kind, "name": name,
            "card": json.dumps(card, ensure_ascii=False), "version": 1,
            "created_at": now_iso(), "updated_at": now_iso(), "origin": "manual",
        })
        return card

    def update_persona(self, persona_id: str, patch: dict) -> dict | None:
        card = registry.get_persona(self.db, persona_id)
        if not card:
            return None
        for key in ("name", "description", "positions", "boundaries"):
            if key in patch:
                card[key] = patch[key]
        if patch.get("register"):
            card.setdefault("voice", {})["register"] = patch["register"]
        if patch.get("doc_ids"):
            card.setdefault("evidence_radius", {})["doc_ids"] = patch["doc_ids"]
        self.db.x("UPDATE personas SET card=?, name=?, updated_at=? WHERE id=?",
                  (json.dumps(card, ensure_ascii=False), card.get("name", ""), now_iso(), persona_id))
        return card

    def delete_persona(self, persona_id: str) -> dict:
        self.db.x("DELETE FROM persona_spans WHERE persona_id=?", (persona_id,))
        self.db.x("DELETE FROM persona_corpora WHERE persona_id=?", (persona_id,))
        self.db.x("DELETE FROM personas WHERE id=?", (persona_id,))
        return {"ok": True}

    def reparse_persona(self, persona_id: str) -> dict:
        card = registry.get_persona(self.db, persona_id)
        if not card:
            raise ValueError("persona not found")
        if card.get("kind") == "AUTHOR":
            registry.refresh_positions(self.db, persona_id)
        else:
            quotes = self.db.q(
                "SELECT text, block_id FROM quotes WHERE doc_id=? AND speaker_name=? LIMIT 20",
                (card["doc_id"], card["name"]))
            for s in quotes:
                self.db.x("INSERT OR IGNORE INTO persona_spans(persona_id, block_id, weight) VALUES(?,?,1.0)",
                          (persona_id, s["block_id"]))
            if quotes:
                card.setdefault("voice", {})["exemplars"] = [{"text": q["text"], "block_id": q["block_id"]}
                                                             for q in quotes[:5]]
                card["description"] = card.get("description") or f"书中被引述 {len(quotes)} 次"
                card["evidence_radius"]["stats"] = {"quotes": len(quotes)}
            self.db.x("UPDATE personas SET card=?, updated_at=? WHERE id=?",
                      (json.dumps(card, ensure_ascii=False), now_iso(), persona_id))
        return registry.get_persona(self.db, persona_id)

    # --- sessions ------------------------------------------------------
    def create_session(self, doc_id: str | None = None, persona_id: str | None = None,
                       mode: str = "strict", persona_mode: str = "textual_author",
                       title: str = "", project_id: str | None = None) -> str:
        if project_id:
            docs = projects.doc_ids(self.db, project_id)
            if not docs:
                raise ValueError("项目还没有素材")
            doc_ids = docs
            primary = doc_id if doc_id in docs else docs[0]
        else:
            if not doc_id:
                raise ValueError("doc_id or project_id required")
            doc_ids = [doc_id]
            primary = doc_id
        if not persona_id:
            author = self.db.one("SELECT id FROM personas WHERE doc_id=? AND kind='AUTHOR'", (primary,))
            persona_id = author["id"] if author else ""
        sid = stable_id("sess", primary, persona_id, secrets.token_hex(6), n=14)
        self.db.insert("sessions", {
            "id": sid, "doc_ids": json.dumps(doc_ids), "persona_id": persona_id,
            "mode": mode, "created_at": now_iso(), "updated_at": now_iso(),
            "settings": json.dumps({"persona_mode": persona_mode}, ensure_ascii=False),
            "title": title or "会话", "project_id": project_id or "",
        })
        return sid

    def add_corpus_document(self, book_doc_id: str, path: str | Path, kind: str = "interview") -> str:
        doc_id = self.add_document(path)
        author = self.db.one("SELECT id FROM personas WHERE doc_id=? AND kind='AUTHOR'", (book_doc_id,))
        if author:
            registry.add_corpus(self.db, author["id"], doc_id, kind)
        return doc_id

    def session(self, session_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        return dict(r) if r else None

    def sessions(self, project_id: str | None = None, doc_id: str | None = None,
                 limit: int = 50) -> list[dict]:
        rows = self.db.q("SELECT * FROM sessions ORDER BY updated_at DESC, created_at DESC LIMIT 200")
        out = []
        for r in rows:
            d = dict(r)
            if project_id and d.get("project_id") != project_id:
                continue
            if doc_id and doc_id not in json.loads(d.get("doc_ids") or "[]"):
                continue
            d["message_count"] = self.db.one(
                "SELECT count(*) c FROM messages WHERE session_id=?", (d["id"],))["c"]
            d["doc_ids"] = json.loads(d.get("doc_ids") or "[]")
            d.pop("settings", None)
            out.append(d)
            if len(out) >= limit:
                break
        return out

    def rename_session(self, session_id: str, title: str) -> dict:
        self.db.x("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title, now_iso(), session_id))
        return {"ok": True}

    def delete_session(self, session_id: str) -> dict:
        self.db.x("DELETE FROM messages WHERE session_id=?", (session_id,))
        self.db.x("DELETE FROM pins WHERE session_id=?", (session_id,))
        self.db.x("DELETE FROM ledger WHERE session_id=?", (session_id,))
        self.db.x("DELETE FROM ai_calls WHERE session_id=?", (session_id,))
        self.db.x("DELETE FROM sessions WHERE id=?", (session_id,))
        return {"ok": True}

    def messages(self, session_id: str) -> list[dict]:
        out = []
        for r in self.db.q("SELECT * FROM messages WHERE session_id=? ORDER BY created_at, rowid", (session_id,)):
            d = dict(r)
            d["claims"] = json.loads(d.get("claims") or "[]")
            d["citations"] = json.loads(d.get("citations") or "[]")
            d["meta"] = json.loads(d.get("meta") or "{}")
            out.append(d)
        return out

    # --- dialogue ------------------------------------------------------
    def _session_docs(self, sess: dict) -> list[str] | None:
        if sess.get("project_id"):
            docs = projects.doc_ids(self.db, sess["project_id"])
            return docs or None
        return None

    def ask(self, session_id: str, question: str, budget: int = 8000) -> dict:
        self.provider = get_provider(self.cfg.provider_settings())
        sess = self.session(session_id)
        return answer_mod.answer_question(self.cfg, self.db, session_id, question,
                                          self.provider, budget=budget,
                                          scope_docs=self._session_docs(sess))

    def ask_multi(self, session_id: str, question: str, persona_ids: list[str],
                  budget: int = 8000) -> dict:
        self.provider = get_provider(self.cfg.provider_settings())
        sess = self.session(session_id)
        if not sess:
            raise ValueError("session not found")
        docs = self._session_docs(sess)
        self.db.insert("messages", {
            "id": stable_id("m", session_id, "multi", secrets.token_hex(6), n=14),
            "session_id": session_id, "role": "user", "content": question,
            "claims": "[]", "citations": "[]", "meta": "{}", "created_at": now_iso(),
        })
        out = []
        for pid in persona_ids[:4]:
            persona = registry.get_persona(self.db, pid)
            if not persona:
                continue
            ans = answer_mod.answer_question(
                self.cfg, self.db, session_id, question, self.provider, budget=budget,
                persona_override=persona, store_user=False, scope_docs=docs)
            out.append({"persona_id": pid, "persona_name": persona["name"],
                        "persona_kind": persona["kind"], **ans})
        return {"answers": out}

    def research(self, session_id: str, subject: str, urls: list[str] | None = None,
                 fetcher=None, max_sources: int = 6) -> dict:
        from .research.dossier import run_research
        from .research.fetch import HttpFetcher
        from .research.search import get_search
        search = get_search(self.cfg.search_settings())
        result = run_research(self.cfg, self.db, session_id, subject,
                              fetcher or HttpFetcher(), urls=urls, search=search,
                              max_sources=max_sources)
        result["search_mode"] = search.name
        if result.get("dossier_id"):
            from .research.dossier import attach
            attach(self.db, session_id, result["dossier_id"])
            result["attached"] = True
        return result

    def dossiers(self, session_id: str) -> list[dict]:
        from .research.dossier import attached
        return attached(self.db, session_id)

    def roundtable(self, doc_id: str, question: str, persona_ids: list[str] | None = None,
                   limit: int = 4) -> list[dict]:
        personas = self.personas(doc_id)
        if persona_ids:
            personas = [p for p in personas if p["id"] in persona_ids]
        out = []
        for p in personas[:limit]:
            sid = self.create_session(doc_id, p["id"], persona_mode="textual_author")
            ans = self.ask(sid, question)
            out.append({"persona_id": p["id"], "name": p["name"], "kind": p["kind"],
                        "text": ans["text"], "abstained": ans["abstained"],
                        "citations": ans["citations"], "claims": ans["claims"]})
        return out

    # --- developer -----------------------------------------------------
    def dev_calls(self, session_id: str | None = None, kind: str | None = None,
                  limit: int = 100) -> list[dict]:
        return devlog.list_calls(self.db, session_id, kind, limit)

    def dev_call(self, call_id: str) -> dict | None:
        return devlog.get_call(self.db, call_id)

    def dev_stats(self, session_id: str | None = None) -> dict:
        return devlog.stats(self.db, session_id)

    # --- preview -------------------------------------------------------
    def assemble_preview(self, session_id: str, question: str, budget: int = 8000) -> dict:
        sess = self.session(session_id)
        persona = registry.get_persona(self.db, sess["persona_id"])
        scope = registry.scope_for(self.db, persona, "textual_author")
        chunks = __import__("wb.index.retrieve", fromlist=["search"]).search(self.db, scope, question, k=6)
        snippets = __import__("wb.index.retrieve", fromlist=["to_snippets"]).to_snippets(self.db, chunks)
        card = assembler.card_text(persona)
        led, cites = assembler.build("preview", persona["id"], card, snippets, [], [], [], "",
                                     "", {}, total=budget, mode=sess["mode"])
        return {"ledger": led, "citations": cites}
