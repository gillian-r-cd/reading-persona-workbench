"""Rewrite mode: coherent prose, citations enforced, graceful fallback."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.harness import Checker
from tests.support import temp_vault
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        cfg = self.server.cfg
        prompt = payload["messages"][-1]["content"]
        cfg["calls"] += 1
        if "检索查询改写器" in prompt:
            content = json.dumps({"queries": ["远程工作的书面协作", "远程办公 文档 留存"]},
                                 ensure_ascii=False)
        elif "严格受原文约束的写作者" in prompt:
            seg = prompt.split("可用片段：", 1)[-1]
            m = re.search(r"^\[S1\](?:（[^）]*）)?\s*(.+)$", seg, re.M)
            base = (m.group(1).strip() if m else "书中给出了相关论述。")[:120]
            if cfg["bad_cite"]:
                content = "书中强调的核心是这一点 [S99]。"
            elif cfg["bad_num"]:
                content = "书中提到有 999 家公司采用了远程办公 [S1]。"
            else:
                content = "书中反复强调的是：" + base + " [S1]。"
        else:
            m = re.search(r"\[1\] block=(\S+) page=\S+\n(.+?)(?:\n\n|\Z)", prompt, re.S)
            claims = []
            if m:
                sentence = re.split(r"(?<=[。！？!?])", m.group(2).strip())[0][:120]
                claims.append({"text": sentence, "block_id": m.group(1), "quote": sentence})
            content = json.dumps({"claims": claims}, ensure_ascii=False)
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run() -> dict:
    ck = Checker("REWRITE")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    httpd.cfg = {"calls": 0, "bad_cite": False, "bad_num": False}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    try:
        with temp_vault() as (cfg, db):
            cfg.save_settings({"provider": {"mode": "byok", "base_url": base,
                                            "api_key": "sk-test", "model": "stub"}})
            wb = Workbench(cfg.vault)
            doc = wb.add_document(CORPUS / "remote_work.md")
            blocks = {r["id"]: r["text"] for r in db.q("SELECT id,text FROM blocks")}
            sid = wb.create_session(doc, mode="rewrite")
            ans = wb.ask(sid, "这本书的核心论点是什么？")
            ck.true("rewrite mode answers", not ans["abstained"] and len(ans["text"]) > 20, ans["text"][:120])
            ck.true("output is prose, not JSON", not ans["text"].lstrip().startswith("{"), ans["text"][:80])
            ck.eq("rewrite bypasses claim gate", ans["claims"], [])
            ck.eq("rewrite marked in gate", ans["gate"].get("mode"), "rewrite")
            bad = [c for c in ans["citations"]
                   if c["block_id"] not in blocks or blocks[c["block_id"]][c["start"]:c["end"]] != c["quote"]]
            ck.eq("rewrite citations resolve", len(bad), 0)
            ck.true("rewrite uses citations", "[S1]" in ans["text"])
            ck.ge("query expansion + rewrite calls", httpd.cfg["calls"], 2)

            from wb.strict.rewrite import validate as rw_validate
            snips = [{"sid": "S1", "text": "书中说有 10 家公司采用了远程办公。"}]
            ck.true("external number requires a label",
                    bool(rw_validate("有 42 家公司采用了远程办公 [S1]。", snips,
                                     "外部研究显示 42 家公司采用了远程办公。")))
            ck.eq("labelled external number passes",
                  rw_validate("外部研究显示，有 42 家公司采用了远程办公，这一比例高于此前的调查结果 [S1]。",
                              snips, "外部研究显示 42 家公司采用了远程办公。"), [])

            httpd.cfg["bad_cite"] = True
            ans2 = wb.ask(sid, "作者给出了哪些建议？")
            ck.true("bad citation falls back to strict",
                    bool(ans2["claims"]) and ans2["gate"].get("mode") != "rewrite",
                    f"claims={len(ans2['claims'])}")

            httpd.cfg["bad_cite"] = False
            httpd.cfg["bad_num"] = True
            ans3 = wb.ask(sid, "研究跟踪了多少家公司？")
            ck.true("hallucinated number falls back to strict",
                    bool(ans3["claims"]) and "999" not in ans3["text"],
                    f"claims={len(ans3['claims'])} text={ans3['text'][:80]}")
    finally:
        httpd.shutdown()
    return ck.result()
