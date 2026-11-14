"""Local HTTP server (stdlib only): JSON API + static UI."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import anchors
from ..config import ROOT, Config
from ..ingest import pipeline
from ..workbench import Workbench

WEB = ROOT / "web"
UPLOADS = "uploads"

ROUTES_GET = [
    (re.compile(r"^/api/health$"), "health"),
    (re.compile(r"^/api/documents$"), "documents"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)$"), "document"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)/outline$"), "outline"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)/blocks$"), "blocks"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)/personas$"), "personas"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)/export$"), "export"),
    (re.compile(r"^/api/sessions/(?P<sid>[^/]+)/messages$"), "messages"),
    (re.compile(r"^/api/sessions/(?P<sid>[^/]+)/board$"), "board"),
    (re.compile(r"^/api/traces/(?P<sid>[^/]+)/(?P<turn>[^/]+)$"), "trace"),
    (re.compile(r"^/api/settings$"), "get_settings"),
    (re.compile(r"^/api/scorecards$"), "scorecards"),
    (re.compile(r"^/api/projects$"), "projects"),
    (re.compile(r"^/api/projects/(?P<pid>[^/]+)$"), "project"),
    (re.compile(r"^/api/projects/(?P<pid>[^/]+)/documents$"), "project_documents"),
    (re.compile(r"^/api/projects/(?P<pid>[^/]+)/links$"), "project_links"),
    (re.compile(r"^/api/projects/(?P<pid>[^/]+)/graph$"), "project_graph"),
    (re.compile(r"^/api/sessions$"), "sessions"),
    (re.compile(r"^/api/sessions/(?P<sid>[^/]+)$"), "session"),
    (re.compile(r"^/api/personas/(?P<pid>[^/]+)$"), "persona"),
    (re.compile(r"^/api/dev/calls$"), "dev_calls"),
    (re.compile(r"^/api/dev/calls/(?P<cid>[^/]+)$"), "dev_call"),
    (re.compile(r"^/api/dev/stats$"), "dev_stats"),
]
ROUTES_POST = [
    (re.compile(r"^/api/documents$"), "upload"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)/corpus$"), "upload_corpus"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)/overrides$"), "override"),
    (re.compile(r"^/api/sessions$"), "create_session"),
    (re.compile(r"^/api/sessions/(?P<sid>[^/]+)/ask$"), "ask"),
    (re.compile(r"^/api/sessions/(?P<sid>[^/]+)/research$"), "research"),
    (re.compile(r"^/api/highlights$"), "highlight"),
    (re.compile(r"^/api/sessions/(?P<sid>[^/]+)/pins$"), "pin"),
    (re.compile(r"^/api/settings$"), "set_settings"),
    (re.compile(r"^/api/projects$"), "create_project"),
    (re.compile(r"^/api/projects/(?P<pid>[^/]+)/documents$"), "add_project_document"),
    (re.compile(r"^/api/projects/(?P<pid>[^/]+)/links$"), "add_project_link"),
    (re.compile(r"^/api/documents/(?P<doc>[^/]+)/personas$"), "create_persona"),
    (re.compile(r"^/api/personas/(?P<pid>[^/]+)/reparse$"), "reparse_persona"),
    (re.compile(r"^/api/sessions/(?P<sid>[^/]+)/ask_multi$"), "ask_multi"),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "WB/1.0"
    wb: Workbench

    def log_message(self, fmt, *args):  # quiet
        pass

    # --- plumbing ------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _err(self, msg: str, status: int = 400) -> None:
        self._json({"error": msg}, status)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        full = (WEB / rel).resolve()
        if not str(full).startswith(str(WEB.resolve())) or not full.is_file():
            self._err("not found", 404)
            return
        ctype = mimetypes.guess_type(str(full))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, full.read_bytes(), ctype)

    # --- GET -----------------------------------------------------------
    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/" and "v" not in u.query:
            self.send_response(302)
            self.send_header("Location", "/index.html?v=8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not u.path.startswith("/api/"):
            self._static(u.path)
            return
        for rx, name in ROUTES_GET:
            m = rx.match(u.path)
            if m:
                try:
                    getattr(self, "h_" + name)(u, **m.groupdict())
                except Exception as exc:
                    self._err(f"{type(exc).__name__}: {exc}", 500)
                return
        self._err("not found", 404)

    def do_PATCH(self) -> None:
        u = urlparse(self.path)
        table = [
            (re.compile(r"^/api/documents/(?P<doc>[^/]+)$"), "patch_document"),
            (re.compile(r"^/api/projects/(?P<pid>[^/]+)$"), "patch_project"),
            (re.compile(r"^/api/sessions/(?P<sid>[^/]+)$"), "patch_session"),
            (re.compile(r"^/api/personas/(?P<pid>[^/]+)$"), "patch_persona"),
        ]
        for rx, name in table:
            m = rx.match(u.path)
            if m:
                try:
                    getattr(self, "h_" + name)(u, **m.groupdict())
                except Exception as exc:
                    self._err(f"{type(exc).__name__}: {exc}", 500)
                return
        self._err("not found", 404)

    def do_DELETE(self) -> None:
        u = urlparse(self.path)
        table = [
            (re.compile(r"^/api/documents/(?P<doc>[^/]+)$"), "delete_document"),
            (re.compile(r"^/api/projects/(?P<pid>[^/]+)$"), "delete_project"),
            (re.compile(r"^/api/projects/(?P<pid>[^/]+)/documents/(?P<doc>[^/]+)$"), "remove_project_document"),
            (re.compile(r"^/api/projects/(?P<pid>[^/]+)/links/(?P<lid>[^/]+)$"), "delete_project_link"),
            (re.compile(r"^/api/sessions/(?P<sid>[^/]+)$"), "delete_session"),
            (re.compile(r"^/api/personas/(?P<pid>[^/]+)$"), "delete_persona"),
        ]
        for rx, name in table:
            m = rx.match(u.path)
            if m:
                try:
                    getattr(self, "h_" + name)(u, **m.groupdict())
                except Exception as exc:
                    self._err(f"{type(exc).__name__}: {exc}", 500)
                return
        self._err("not found", 404)

    # --- POST ----------------------------------------------------------
    def do_POST(self) -> None:
        u = urlparse(self.path)
        for rx, name in ROUTES_POST:
            m = rx.match(u.path)
            if m:
                try:
                    getattr(self, "h_" + name)(u, **m.groupdict())
                except pipeline.IngestError as exc:
                    self._err(str(exc), 422)
                except Exception as exc:
                    self._err(f"{type(exc).__name__}: {exc}", 500)
                return
        self._err("not found", 404)

    # --- handlers ------------------------------------------------------
    def h_health(self, u, **kw):
        self._json({"ok": True, "python": os.sys.version.split()[0]})

    def h_documents(self, u, **kw):
        q = parse_qs(u.query)
        self._json(self.wb.documents((q.get("q") or [""])[0]))

    def h_patch_document(self, u, doc):
        b = self._json_body()
        self._json(self.wb.rename_document(doc, b.get("title", ""), b.get("tags")))

    def h_delete_document(self, u, doc):
        self._json(self.wb.delete_document(doc))

    def h_document(self, u, doc):
        d = self.wb.document(doc)
        if not d:
            return self._err("document not found", 404)
        self._json(d)

    def h_outline(self, u, doc):
        self._json(self.wb.outline(doc))

    def h_blocks(self, u, doc):
        q = parse_qs(u.query)
        sec = (q.get("section") or [None])[0]
        self._json(self.wb.blocks(doc, sec))

    def h_personas(self, u, doc):
        ps = self.wb.personas(doc)
        out = []
        for p in ps:
            out.append({"id": p["id"], "kind": p["kind"], "name": p["name"],
                        "modes": list(p.get("modes", {}).keys()),
                        "positions": p.get("positions", []),
                        "origin": p.get("origin", "auto"),
                        "description": p.get("description", "")})
        self._json(out)

    def h_export(self, u, doc):
        d = self.wb.document(doc)
        if not d:
            return self._err("document not found", 404)
        anchors_rows = [dict(r) for r in self.wb.db.q("SELECT * FROM anchors WHERE doc_id=?", (doc,))]
        self._json({"document": d, "outline": self.wb.outline(doc),
                    "blocks": self.wb.blocks(doc), "personas": self.wb.personas(doc),
                    "anchors": anchors_rows})

    def h_messages(self, u, sid):
        self._json(self.wb.messages(sid))

    def h_board(self, u, sid):
        rows = self.wb.messages(sid)
        last = next((m for m in reversed(rows) if m["role"] == "assistant"), None)
        if not last:
            return self._json({"ledger": None, "citations": []})
        self._json({"ledger": last["meta"].get("ledger"), "citations": last["citations"],
                    "turn_id": last["meta"].get("turn_id")})

    def h_trace(self, u, sid, turn):
        path = Path(self.wb.cfg.vault) / "traces" / sid / f"{turn}.json"
        if not path.exists():
            return self._err("trace not found", 404)
        self._json(json.loads(path.read_text("utf-8")))

    def h_get_settings(self, u, **kw):
        s = self.wb.cfg.load_settings().get("provider", {}) or {}
        safe = {"mode": s.get("mode", "offline"), "base_url": s.get("base_url", ""),
                "model": s.get("model", ""), "embed_model": s.get("embed_model", "")}
        safe["has_key"] = bool(s.get("api_key"))
        ss = self.wb.cfg.search_settings()
        safe["search"] = {"mode": ss.get("mode", "none"),
                          "endpoint": ss.get("endpoint", ""),
                          "has_key": bool(ss.get("api_key"))}
        self._json(safe)

    def h_scorecards(self, u, **kw):
        out = {}
        for p in sorted((ROOT / "eval").glob("scorecard-*.json")):
            try:
                d = json.loads(p.read_text("utf-8"))
                out[d["milestone"]] = {"passed": d["passed"], "failed": d.get("failed", []),
                                       "metrics": d.get("metrics", {})}
            except Exception:
                continue
        self._json(out)

    # --- POST handlers -------------------------------------------------
    def h_upload(self, u, **kw):
        q = parse_qs(u.query)
        name = (q.get("name") or ["upload.txt"])[0]
        name = Path(name).name
        data = self._body()
        if not data:
            return self._err("empty body")
        dest = self.wb.cfg.path(UPLOADS, name)
        dest.write_bytes(data)
        doc_id = self.wb.add_document(dest, doc_type=(q.get("type") or [None])[0])
        self._json({"doc_id": doc_id})

    def h_upload_corpus(self, u, doc, **kw):
        q = parse_qs(u.query)
        name = Path((q.get("name") or ["corpus.txt"])[0]).name
        kind = (q.get("kind") or ["interview"])[0]
        data = self._body()
        dest = self.wb.cfg.path(UPLOADS, name)
        dest.write_bytes(data)
        doc_id = self.wb.add_corpus_document(doc, dest, kind)
        self._json({"doc_id": doc_id})

    def h_override(self, u, doc, **kw):
        body = self._json_body()
        block_id = body.get("block_id")
        if not block_id:
            return self._err("block_id required")
        self.wb.db.insert("overrides", {
            "id": f"ov_{os.urandom(4).hex()}", "doc_id": doc, "target_type": "block",
            "target_id": block_id, "patch": json.dumps({"text": body.get("text", "")}, ensure_ascii=False),
            "created_at": __import__("wb.util", fromlist=["now_iso"]).now_iso(),
        })
        pipeline.apply_overrides(self.wb.db, doc)
        self._json({"ok": True})

    def h_create_session(self, u, **kw):
        b = self._json_body()
        doc = b.get("doc_id")
        if not doc and not b.get("project_id"):
            return self._err("doc_id or project_id required")
        sid = self.wb.create_session(doc, b.get("persona_id"), b.get("mode", "strict"),
                                     b.get("persona_mode", "textual_author"),
                                     b.get("title", ""), b.get("project_id"))
        self._json({"session_id": sid})

    def h_ask(self, u, sid, **kw):
        b = self._json_body()
        q = (b.get("question") or "").strip()
        if not q:
            return self._err("question required")
        self._json(self.wb.ask(sid, q))

    def h_research(self, u, sid, **kw):
        b = self._json_body()
        subject = (b.get("subject") or "").strip()
        if not subject:
            return self._err("subject required")
        self._json(self.wb.research(sid, subject, urls=b.get("urls")))

    def h_highlight(self, u, **kw):
        b = self._json_body()
        doc = b.get("doc_id")
        block = self.wb.db.one("SELECT * FROM blocks WHERE id=?", (b.get("block_id"),))
        if not doc or not block:
            return self._err("doc_id/block_id invalid")
        a = anchors.make_anchor(dict(block), int(b.get("start", 0)), int(b.get("end", 1)))
        hid = f"h_{os.urandom(6).hex()}"
        self.wb.db.insert("anchors", {
            "id": f"a_{hid}", "doc_id": doc, "block_id": a["block_id"],
            "start": a["start"], "end": a["end"], "exact": a["exact"],
            "prefix": a["prefix"], "suffix": a["suffix"], "text_hash": a["text_hash"],
            "page": a.get("page"), "doc_version": a.get("doc_version", 1), "status": "ok",
        })
        self.wb.db.insert("highlights", {
            "id": hid, "doc_id": doc, "anchor": json.dumps(a, ensure_ascii=False),
            "color": b.get("color", "yellow"), "note": b.get("note", ""),
            "created_at": __import__("wb.util", fromlist=["now_iso"]).now_iso(),
            "status": "ok", "session_id": b.get("session_id", ""),
        })
        self._json({"highlight_id": hid, "anchor": a})

    def h_pin(self, u, sid, **kw):
        b = self._json_body()
        hid = b.get("highlight_id")
        if not hid:
            return self._err("highlight_id required")
        self.wb.db.insert("pins", {
            "id": f"pin_{os.urandom(5).hex()}", "session_id": sid,
            "target_type": "highlight", "target_id": hid, "weight": 1.0,
            "created_at": __import__("wb.util", fromlist=["now_iso"]).now_iso(),
        })
        self._json({"ok": True})

    def h_set_settings(self, u, **kw):
        b = self._json_body()
        s = self.wb.cfg.load_settings()
        old = s.get("provider", {}) or {}
        # Never wipe an existing key when the field is left blank.
        key = b.get("api_key", "") or old.get("api_key", "")
        s["provider"] = {"mode": b.get("mode", "offline"),
                         "base_url": b.get("base_url", "") or old.get("base_url", ""),
                         "api_key": key,
                         "model": b.get("model", "") or old.get("model", ""),
                         "embed_model": b.get("embed_model", "")}
        old_search = s.get("search", {}) or {}
        s["search"] = {"mode": b.get("search_mode", old_search.get("mode", "none")),
                       "endpoint": b.get("search_endpoint", "") or old_search.get("endpoint", ""),
                       "api_key": b.get("search_api_key", "") or old_search.get("api_key", "")}
        self.wb.cfg.save_settings(s)
        try:
            os.chmod(self.wb.cfg.settings_path, 0o600)
        except Exception:
            pass
        self.wb.provider = __import__("wb.providers.base", fromlist=["get_provider"]).get_provider(
            self.wb.cfg.provider_settings())
        self._json({"ok": True})


    # --- project / session / persona / dev handlers -----------------
    def h_projects(self, u, **kw):
        self._json(self.wb.projects())

    def h_create_project(self, u, **kw):
        b = self._json_body()
        if not b.get("name"):
            return self._err("name required")
        self._json(self.wb.create_project(b["name"], b.get("description", "")))

    def h_project(self, u, pid):
        p = self.wb.project(pid)
        if not p:
            return self._err("project not found", 404)
        self._json(p)

    def h_patch_project(self, u, pid):
        b = self._json_body()
        self._json(self.wb.update_project(pid, b.get("name"), b.get("description")))

    def h_delete_project(self, u, pid):
        self._json(self.wb.delete_project(pid))

    def h_project_documents(self, u, pid):
        self._json(self.wb.project_documents(pid))

    def h_add_project_document(self, u, pid):
        b = self._json_body()
        if not b.get("doc_id"):
            return self._err("doc_id required")
        self._json(self.wb.add_project_document(pid, b["doc_id"], b.get("role", "source")))

    def h_remove_project_document(self, u, pid, doc):
        self._json(self.wb.remove_project_document(pid, doc))

    def h_project_links(self, u, pid):
        self._json(self.wb.project(pid)["links"] if self.wb.project(pid) else [])

    def h_add_project_link(self, u, pid):
        b = self._json_body()
        self._json(self.wb.add_project_link(pid, b.get("src"), b.get("dst"),
                                            b.get("kind", "related"), b.get("note", "")))

    def h_delete_project_link(self, u, pid, lid):
        self._json(self.wb.delete_project_link(pid, lid))

    def h_project_graph(self, u, pid):
        self._json(self.wb.project_graph(pid))

    def h_sessions(self, u, **kw):
        q = parse_qs(u.query)
        self._json(self.wb.sessions((q.get("project") or [None])[0],
                                    (q.get("doc") or [None])[0]))

    def h_session(self, u, sid):
        s = self.wb.session(sid)
        if not s:
            return self._err("session not found", 404)
        s["doc_ids"] = json.loads(s.get("doc_ids") or "[]")
        s["settings"] = json.loads(s.get("settings") or "{}")
        s["messages"] = self.wb.messages(sid)
        self._json(s)

    def h_patch_session(self, u, sid):
        b = self._json_body()
        if not b.get("title"):
            return self._err("title required")
        self._json(self.wb.rename_session(sid, b["title"]))

    def h_delete_session(self, u, sid):
        self._json(self.wb.delete_session(sid))

    def h_persona(self, u, pid):
        p = self.wb.persona(pid)
        if not p:
            return self._err("persona not found", 404)
        self._json(p)

    def h_create_persona(self, u, doc):
        b = self._json_body()
        if not b.get("name"):
            return self._err("name required")
        self._json(self.wb.create_persona(doc, b["name"], b.get("kind", "QUOTED_PERSON"),
                                          b.get("doc_ids"), b.get("description", ""),
                                          b.get("register", "")))

    def h_patch_persona(self, u, pid):
        self._json(self.wb.update_persona(pid, self._json_body()))

    def h_delete_persona(self, u, pid):
        self._json(self.wb.delete_persona(pid))

    def h_reparse_persona(self, u, pid):
        self._json(self.wb.reparse_persona(pid))

    def h_ask_multi(self, u, sid):
        b = self._json_body()
        q = (b.get("question") or "").strip()
        pids = b.get("persona_ids") or []
        if not q or not pids:
            return self._err("question and persona_ids required")
        self._json(self.wb.ask_multi(sid, q, pids))

    def h_dev_calls(self, u, **kw):
        q = parse_qs(u.query)
        self._json(self.wb.dev_calls((q.get("session_id") or [None])[0],
                                     (q.get("kind") or [None])[0],
                                     int((q.get("limit") or ["100"])[0])))

    def h_dev_call(self, u, cid):
        c = self.wb.dev_call(cid)
        if not c:
            return self._err("call not found", 404)
        self._json(c)

    def h_dev_stats(self, u, **kw):
        q = parse_qs(u.query)
        self._json(self.wb.dev_stats((q.get("session_id") or [None])[0]))


def serve(host: str = "127.0.0.1", port: int = 8765, vault: str | None = None,
          workbench: Workbench | None = None) -> None:
    wb = workbench or Workbench(vault)
    handler = type("BoundHandler", (Handler,), {"wb": wb})
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"WB listening on http://{host}:{port}  vault={wb.cfg.vault}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def start_background(vault: str, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    wb = Workbench(vault)
    handler = type("BoundHandler", (Handler,), {"wb": wb})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
