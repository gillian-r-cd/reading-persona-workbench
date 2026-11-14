"""PRODUCT: project workspace, sessions, documents, personas, multi-role, dev log."""
from __future__ import annotations

import functools
import http.server
import json
import tempfile
import threading
import urllib.request
from pathlib import Path

from tests.harness import Checker
from wb.api.server import start_background
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


def run() -> dict:
    ck = Checker("PRODUCT")
    vault = tempfile.mkdtemp(prefix="wb_prod_")
    wb = Workbench(vault)
    book = wb.add_document(CORPUS / "remote_work.md")
    iv = wb.add_corpus_document(book, CORPUS / "author_interview.md", "interview")
    rv = wb.add_corpus_document(book, CORPUS / "review_remote_work.md", "research")
    httpd, base = start_background(vault)

    def req(method, path, data=None, raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        r = urllib.request.Request(base + path, data=body, method=method,
                                   headers={"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.loads(resp.read())

    try:
        # --- project CRUD + materials + links ---
        proj = req("POST", "/api/projects", {"name": "远程办公研究", "description": "书+访谈+评论"})
        pid = proj["id"]
        for d in (book, iv, rv):
            req("POST", f"/api/projects/{pid}/documents", {"doc_id": d})
        detail = req("GET", f"/api/projects/{pid}")
        ck.eq("project holds three materials", len(detail["documents"]), 3)
        link = req("POST", f"/api/projects/{pid}/links", {"src": book, "dst": iv, "kind": "interview"})
        ck.true("link created", bool(link.get("id")))
        graph = req("GET", f"/api/projects/{pid}/graph")
        ck.eq("graph nodes", len(graph["nodes"]), 3)
        ck.eq("graph edges", len(graph["edges"]), 1)

        # --- project session spans materials ---
        sess = req("POST", "/api/sessions", {"project_id": pid, "mode": "strict",
                                             "persona_mode": "textual_author"})
        sid = sess["session_id"]
        ans = req("POST", f"/api/sessions/{sid}/ask", {"question": "你后来改过这个说法吗？"})
        cited = {c["doc_id"] for c in ans["citations"]}
        ck.true("project answer cites interview", iv in cited, str(cited))
        ck.true("project answer cites book", book in cited, str(cited))

        sess_detail = req("GET", f"/api/sessions/{sid}")
        ck.true("session doc_ids is a list", isinstance(sess_detail["doc_ids"], list),
                str(type(sess_detail["doc_ids"])))
        ck.true("session settings is an object", isinstance(sess_detail["settings"], dict))

        # --- developer log ---
        kinds = {c["kind"] for c in req("GET", f"/api/dev/calls?session_id={sid}")}
        ck.true("dev log records retrieval", "retrieve" in kinds, str(kinds))
        ck.true("dev log records generation", "generate" in kinds, str(kinds))
        stats = req("GET", f"/api/dev/stats?session_id={sid}")
        ck.ge("dev stats total", stats["total"], 2, str(stats))
        calls = req("GET", f"/api/dev/calls?session_id={sid}")
        detail_call = req("GET", f"/api/dev/calls/{calls[0]['id']}")
        ck.true("call detail has request/response",
                isinstance(detail_call.get("request"), dict) and isinstance(detail_call.get("response"), dict))

        # --- sessions history ---
        sessions = req("GET", f"/api/sessions?project={pid}")
        ck.ge("session listed", len(sessions), 1)
        ck.eq("session has message count", sessions[0]["message_count"] >= 2, True)
        req("PATCH", f"/api/sessions/{sid}", {"title": "改过的标题"})
        ck.eq("session renamed", req("GET", f"/api/sessions/{sid}")["title"], "改过的标题")

        # --- multi-persona, one bubble each ---
        personas = req("GET", f"/api/documents/{book}/personas")
        pids = [p["id"] for p in personas[:3]]
        multi = req("POST", f"/api/sessions/{sid}/ask_multi",
                    {"question": "远程办公的瓶颈是什么？", "persona_ids": pids})
        ck.ge("multi-persona returns multiple answers", len(multi["answers"]), 2)
        ck.true("each answer has a persona name",
                all(a.get("persona_name") for a in multi["answers"]))
        ck.true("answers differ by persona",
                len({a["persona_id"] for a in multi["answers"]}) == len(multi["answers"]))

        # --- in-conversation research attaches to the ledger ---
        site = tempfile.mkdtemp()
        Path(site, "page.html").write_text(
            "<html><head><title>Remote work evidence</title></head><body>"
            "<p>A study finds that 42% of remote teams adopt written decision logs.</p>"
            "<p>The report suggests documentation predicts performance.</p></body></html>", "utf-8")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
        site_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=site_srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{site_srv.server_address[1]}/page.html"
            res = wb.research(sid, "remote work", urls=[url], max_sources=1)
            ck.eq("research completes", res["status"], "done", str(res)[:200])
            ck.true("dossier attached to session",
                    wb.db.one("SELECT count(*) c FROM session_dossiers WHERE session_id=?", (sid,))["c"] == 1)
            from wb.research.dossier import external_text
            ck.true("external context is non-empty", len(external_text(wb.db, sid)) > 10)
            wb.ask(sid, "这个研究的主要发现是什么？")
            last = wb.messages(sid)[-1]
            names = [x["name"] for x in last["meta"]["ledger"]["sections"]]
            ck.true("research enters the context ledger", "research" in names, str(names))
        finally:
            site_srv.shutdown()

        # --- remove material narrows scope ---
        req("DELETE", f"/api/projects/{pid}/documents/{iv}")
        ck.eq("project material removed", len(req("GET", f"/api/projects/{pid}")["documents"]), 2)
        req("POST", f"/api/projects/{pid}/documents", {"doc_id": iv})  # restore for later

        # --- document rename / search / delete ---
        req("PATCH", f"/api/documents/{book}", {"title": "远程之后（修订版）", "tags": ["远程"]})
        found = req("GET", "/api/documents?q=%E4%BF%AE%E8%AE%A2")
        ck.true("document search finds renamed title", any(d["id"] == book for d in found))
        tmp = Path(tempfile.mkdtemp()) / "tmp.md"
        tmp.write_text("# 临时\n\n这是一份会被删除的临时素材。", "utf-8")
        tmp_id = wb.add_document(tmp)
        req("DELETE", f"/api/documents/{tmp_id}")
        ck.true("document deleted", not any(d["id"] == tmp_id for d in req("GET", "/api/documents")))

        # --- manual persona CRUD + reparse ---
        np = req("POST", f"/api/documents/{book}/personas",
                 {"name": "临时角色", "kind": "QUOTED_PERSON", "register": "干脆"})
        ck.eq("manual persona origin", np.get("origin"), "manual")
        listed = req("GET", f"/api/documents/{book}/personas")
        ck.true("manual persona listed",
                any(p["name"] == "临时角色" and p.get("origin") == "manual" for p in listed))
        req("PATCH", f"/api/personas/{np['id']}", {"register": "克制", "description": "测试"})
        ck.eq("persona updated", req("GET", f"/api/personas/{np['id']}")["voice"]["register"], "克制")
        ck.eq("persona reparsed", req("POST", f"/api/personas/{np['id']}/reparse")["name"], "临时角色")
        req("DELETE", f"/api/personas/{np['id']}")
        ck.true("manual persona deleted",
                not any(p["id"] == np["id"] for p in req("GET", f"/api/documents/{book}/personas")))

        # --- session delete ---
        req("DELETE", f"/api/sessions/{sid}")
        ck.true("session deleted", not any(s["id"] == sid for s in req("GET", f"/api/sessions?project={pid}")))

        # --- UI contract ---
        html = urllib.request.urlopen(base + "/").read().decode()
        js = urllib.request.urlopen(base + "/app.js").read().decode()
        for token in ('id="project-list"', 'id="project-detail"', 'id="session-list"',
                      'id="dev-panel"', 'id="multi"', 'id="persona-list"', 'id="doc-search"',
                      'id="btn-research"'):
            ck.true(f"ui element {token}", token in html)
        for token in ("ask_multi", "loadDev", "openProjectSession", "renderPersonaAdmin", "font-inc"):
            ck.true(f"ui logic {token}", token in js)
    finally:
        httpd.shutdown()

    return ck.result()
