"""M8: local HTTP API + interaction contract (citations, board, pins, export)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.harness import Checker
from wb.api.server import start_background

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


def run() -> dict:
    ck = Checker("M8")
    import tempfile
    vault = tempfile.mkdtemp(prefix="wb_m8_")
    httpd, base = start_background(vault)

    def req(path, data=None, ctype="application/json", raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        r = urllib.request.Request(base + path, data=body,
                                   headers={"Content-Type": ctype} if body else {})
        with urllib.request.urlopen(r, timeout=30) as resp:
            payload = resp.read()
            try:
                return json.loads(payload)
            except Exception:
                return payload.decode("utf-8", "replace")

    try:
        ck.true("health endpoint", req("/api/health").get("ok") is True)

        raw = (CORPUS / "remote_work.md").read_bytes()
        up = req("/api/documents?name=remote_work.md", raw=raw, ctype="application/octet-stream")
        doc = up.get("doc_id")
        ck.true("upload creates document", bool(doc), str(up))
        docs = req("/api/documents")
        ck.ge("document listed", len(docs), 1)

        outline = req(f"/api/documents/{doc}/outline")
        blocks = req(f"/api/documents/{doc}/blocks")
        personas = req(f"/api/documents/{doc}/personas")
        ck.ge("outline sections", len(outline), 5, f"{len(outline)}")
        ck.ge("blocks available", len(blocks), 30, f"{len(blocks)}")
        ck.ge("personas available", len(personas), 4, f"{len(personas)}")

        sess = req("/api/sessions", {"doc_id": doc, "mode": "strict",
                                     "persona_mode": "textual_author"})
        sid = sess["session_id"]
        t0 = time.perf_counter()
        ans = req(f"/api/sessions/{sid}/ask", {"question": "这本书的核心论点是什么？"})
        elapsed = time.perf_counter() - t0
        ck.true("ask returns answer", bool(ans.get("text")) and not ans.get("abstained"), ans.get("text", "")[:120])
        ck.le("ask latency (s)", elapsed, 2.5, f"{elapsed:.3f}s")
        ck.true("answer has citations", len(ans.get("citations", [])) >= 1)

        block_map = {b["id"]: b["text"] for b in blocks}
        bad = [c for c in ans["citations"]
               if c["block_id"] not in block_map
               or block_map[c["block_id"]][c["start"]:c["end"]] != c["quote"]]
        ck.eq("citations resolve to exact spans", len(bad), 0, f"{len(ans['citations'])} checked")

        board = req(f"/api/sessions/{sid}/board")
        ck.eq("context board equals ledger", json.dumps(board["ledger"], sort_keys=True),
              json.dumps(ans["ledger"], sort_keys=True))

        # highlight + pin flow
        first_para = next(b for b in blocks if b["kind"] == "para")
        hl = req("/api/highlights", {"doc_id": doc, "block_id": first_para["id"],
                                     "start": 0, "end": min(30, len(first_para["text"])),
                                     "session_id": sid})
        ck.true("highlight created", bool(hl.get("highlight_id")))
        req(f"/api/sessions/{sid}/pins", {"highlight_id": hl["highlight_id"]})
        ans2 = req(f"/api/sessions/{sid}/ask", {"question": "请解释这段原文的作用。"})
        names = [s["name"] for s in ans2["ledger"]["sections"]]
        ck.true("pinned context enters ledger", "pins" in names, str(names))

        # highlight survives an edit elsewhere in the block
        req(f"/api/documents/{doc}/overrides", {"block_id": first_para["id"],
                                                "text": first_para["text"] + "（补充说明）"})
        from wb import anchors as anchors_mod
        from wb.db import DB
        db = DB(str(Path(vault) / "meta.db"))
        row = db.one("SELECT anchor FROM highlights WHERE id=?", (hl["highlight_id"],))
        resolved = anchors_mod.resolve_anchor(json.loads(row["anchor"]),
                                              {first_para["id"]: first_para["text"] + "（补充说明）"})
        ck.eq("highlight survives reparse", resolved["status"], "ok")

        # export integrity
        exp = req(f"/api/documents/{doc}/export")
        ck.true("export contains blocks and anchors",
                len(exp.get("blocks", [])) > 0 and len(exp.get("anchors", [])) >= 1)
        ck.true("export includes personas", len(exp.get("personas", [])) >= 3)

        # static UI contract
        html = req("/")
        js = req("/app.js")
        css = req("/style.css")
        for token in ("id=\"blocks\"", "id=\"board\"", "id=\"persona\"", "id=\"toolbar\"",
                      "id=\"messages\"", "id=\"question\""):
            ck.true(f"ui element {token}", token in html)
        for token in ("data-sid", "markRange", "citeIndex", "board-sec", "block mark"):
            ck.true(f"ui logic {token}", token in js or token in css)

        # settings round trip
        st = req("/api/settings")
        ck.true("settings readable", "mode" in st)
        req("/api/settings", {"mode": "offline", "base_url": "", "api_key": "", "model": ""})
        st2 = req("/api/settings")
        ck.eq("settings saved", st2.get("mode"), "offline")

        # 404 handling
        try:
            req("/api/nope")
            ck.true("unknown route errors", False)
        except urllib.error.HTTPError as exc:
            ck.eq("unknown route errors", exc.code, 404)
    finally:
        httpd.shutdown()

    return ck.result()
