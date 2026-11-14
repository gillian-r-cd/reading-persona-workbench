"""SEARCH: pluggable search providers + research pipeline with auto-search."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.harness import Checker
from tests.support import temp_vault
from wb.research.dossier import run_research
from wb.research.fetch import FakeFetcher
from wb.research.search import (BraveSearch, NullSearch, SearxngSearch, SerperSearch,
                                TavilySearch, get_search)
from wb.workbench import Workbench


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path.startswith("/tavily"):
            self._send({"results": [{"title": "T1", "url": "https://a.example/1", "content": "42%"}]})
        elif self.path.startswith("/serper"):
            self._send({"organic": [{"title": "S1", "link": "https://b.example/1", "snippet": "x"}]})
        else:
            self._send({})

    def do_GET(self):
        if self.path.startswith("/brave"):
            self._send({"web": {"results": [{"title": "B1", "url": "https://c.example/1",
                                             "description": "y"}]}})
        elif self.path.startswith("/searxng/search"):
            self._send({"results": [{"title": "X1", "url": "https://d.example/1", "content": "z"}]})
        else:
            self._send({})


def run() -> dict:
    ck = Checker("SEARCH")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        ck.eq("tavily result", len(TavilySearch("k", base + "/tavily").search(["q"])), 1)
        ck.eq("brave result", len(BraveSearch("k", base + "/brave").search(["q"])), 1)
        ck.eq("serper result", len(SerperSearch("k", base + "/serper").search(["q"])), 1)
        ck.eq("searxng result", len(SearxngSearch(base + "/searxng").search(["q"])), 1)
        ck.true("none yields null search", isinstance(get_search({"mode": "none"}), NullSearch))
        ck.true("missing key falls back to null", isinstance(get_search({"mode": "tavily"}), NullSearch))

        with temp_vault() as (cfg, db):
            wb = Workbench(cfg.vault)
            doc = wb.add_document("fixtures/corpus/remote_work.md")
            sid = wb.create_session(doc)
            page = "https://example.com/study"
            html = ("<html><head><title>Study</title></head><body>"
                    "<p>A study finds that 42% of remote teams keep written logs.</p></body></html>")
            fetcher = FakeFetcher({page: html})
            res = run_research(cfg, db, sid, "remote work", fetcher, urls=None,
                               search=lambda qs: [{"url": page}], max_sources=2)
            ck.eq("auto-search research completes", res["status"], "done", str(res)[:200])
            ck.eq("auto-search produced a source", res["sources"], 1)
            ck.true("auto-search dossier stored", bool(res.get("dossier_id")))
            ck.true("auto-search attached to session",
                    db.one("SELECT count(*) c FROM session_dossiers WHERE session_id=?", (sid,))["c"] == 1)
    finally:
        srv.shutdown()
    return ck.result()
