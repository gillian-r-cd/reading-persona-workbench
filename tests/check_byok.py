"""BYOK path regression against a local OpenAI-compatible stub (no real key needed)."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.harness import Checker
from tests.support import temp_vault
from wb.contracts import validate_claim
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


class StubHandler(BaseHTTPRequestHandler):
    server_version = "StubLLM/1.0"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        cfg = self.server.cfg
        cfg["calls"] += 1
        if payload.get("response_format") and cfg["reject_format"] and not cfg["retried"]:
            cfg["retried"] = True
            self.send_response(400)
            body = b'{"error":"response_format not supported"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        prompt = payload["messages"][-1]["content"]
        if "FORCE_ERROR" in prompt:
            self.send_response(500)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        m = re.search(r"\[1\] block=(\S+) page=\S+\n(.+?)(?:\n\n|\Z)", prompt, re.S)
        claims = []
        if m:
            block_id, snippet = m.group(1), m.group(2).strip()
            sentence = re.split(r"(?<=[。！？!?])", snippet)[0].strip()[:120]
            if len(sentence) >= 8:
                claims.append({"text": sentence, "block_id": block_id, "quote": sentence})
        content = json.dumps({"claims": claims}, ensure_ascii=False)
        if cfg["fenced"]:
            content = "```json\n" + content + "\n```"
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_stub() -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    httpd.cfg = {"calls": 0, "fenced": False, "reject_format": False, "retried": False}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/v1"


def run() -> dict:
    ck = Checker("BYOK")
    httpd, base = _start_stub()
    try:
        with temp_vault() as (cfg, db):
            cfg.save_settings({"provider": {"mode": "byok", "base_url": base,
                                            "api_key": "sk-test-not-a-real-key",
                                            "model": "stub-model"}})
            wb = Workbench(cfg.vault)
            doc = wb.add_document(CORPUS / "remote_work.md")
            ck.eq("provider switched to BYOK", wb.provider.name, "openai:stub-model")
            blocks = {b["id"]: b["text"] for b in wb.blocks(doc)}
            sess = wb.create_session(doc)

            # fenced JSON still parses
            httpd.cfg["fenced"] = True
            a = wb.ask(sess, "这本书的核心论点是什么？")
            ck.true("llm answer returned", not a["abstained"] and bool(a["claims"]), a["text"][:120])
            llm = [c for c in a["claims"] if c.get("meta", {}).get("generator") == "llm"]
            ck.ge("claims came from the LLM path", len(llm), 1, f"{len(llm)}/{len(a['claims'])}")
            ck.eq("llm claims pass the contract", [e for c in a["claims"] for e in validate_claim(c, blocks)], [])
            ck.true("llm answer keeps citations", "[S1]" in a["text"])
            ck.eq("citations resolve",
                  sum(1 for c in a["citations"] if c["block_id"] in blocks), len(a["citations"]))

            # response_format rejected once, provider retries without it
            httpd.cfg["reject_format"] = True
            httpd.cfg["retried"] = False
            a2 = wb.ask(sess, "作者给出了哪些建议？")
            ck.true("retry without response_format works",
                    httpd.cfg["retried"] and not a2["abstained"], a2["text"][:100])

            # upstream failure -> extractive fallback, still grounded
            a3 = wb.ask(sess, "FORCE_ERROR 这本书的核心论点是什么？")
            ext = [c for c in a3["claims"] if c.get("meta", {}).get("generator") == "extractive"]
            ck.true("falls back to extractive on provider error", len(ext) >= 1,
                    str([c.get("meta", {}).get("generator") for c in a3["claims"]]))
            ck.true("fallback answer still cited", "[S1]" in a3["text"])
    finally:
        httpd.shutdown()

    return ck.result()
