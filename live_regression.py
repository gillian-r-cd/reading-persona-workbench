#!/usr/bin/env python3
"""真实模型回归：使用 vault/settings.json 里的 BYOK 配置，不打印密钥。

用法：python live_regression.py [--vault PATH]
无 key 时退出码 2，并给出提示；不会发起任何请求。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from wb.contracts import validate_claim
from wb.util import now_iso
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "fixtures" / "corpus"

QUESTIONS = [
    ("identity", "你是谁？"),
    ("book", "这本书的核心论点是什么？"),
    ("book", "研究跟踪了多少家公司？"),
    ("book", "陈薇说了什么？"),
    ("book", "远程办公适用于所有公司吗？"),
    ("book", "作者给出了哪三条建议？"),
    ("trap", "作者对火星殖民计划有什么看法？"),
    ("trap", "研究跟踪了 73 家公司吗？"),
    ("trap", "Bloom 是在 2020 年做的那项研究吗？"),
    ("trap", "作者是否认为远程办公对所有团队都更好？"),
    ("interview", "你后来改过这个说法吗？"),
    ("interview", "周敏怎么批评你？"),
    ("intent", "你为什么写第三章？"),
]


def _find_doc(wb: Workbench) -> str | None:
    for d in wb.documents():
        if "远程之后" in (d["title"] or ""):
            return d["id"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=None)
    args = ap.parse_args()
    wb = Workbench(args.vault)
    ps = wb.cfg.provider_settings()
    print(f"provider={wb.provider.name} mode={ps['mode']} key={'set' if ps['api_key'] else 'MISSING'} "
          f"model={ps['model']}")
    if not ps["api_key"]:
        print("未检测到 API key：请在 http://127.0.0.1:8765 的「设置」里粘贴并保存（留空不会覆盖已有 key）。")
        return 2

    doc = _find_doc(wb)
    if not doc:
        print("vault 中没有《远程之后》，先导入演示语料……")
        doc = wb.add_document(CORPUS / "remote_work.md")
        wb.add_corpus_document(doc, CORPUS / "author_interview.md", "interview")
        wb.add_corpus_document(doc, CORPUS / "review_remote_work.md", "research")
    personas = wb.personas(doc)
    author = next((p for p in personas if p["kind"] == "AUTHOR"), personas[0])
    textual = wb.create_session(doc, author["id"], persona_mode="textual_author")
    hist = wb.create_session(doc, author["id"], persona_mode="historical_author", mode="labeled")

    blocks = {r["id"]: r["text"] for r in wb.db.q("SELECT id,text FROM blocks")}
    checks, latencies = [], []
    llm_claims = total_claims = 0
    trap_errors = []
    abstained = 0

    for kind, q in QUESTIONS:
        sid = hist if kind in ("interview", "intent") else textual
        t0 = time.perf_counter()
        try:
            ans = wb.ask(sid, q)
        except Exception as exc:
            checks.append({"name": q, "ok": False, "detail": f"exception: {exc}"})
            print(f"[ERR ] {q} -> {exc}")
            continue
        latencies.append((time.perf_counter() - t0) * 1000)
        errs = [e for c in ans["claims"] for e in validate_claim(c, blocks)]
        bad_cites = [c for c in ans["citations"]
                     if c["block_id"] not in blocks or blocks[c["block_id"]][c["start"]:c["end"]] != c["quote"]]
        for c in ans["claims"]:
            total_claims += 1
            if c.get("meta", {}).get("generator") == "llm":
                llm_claims += 1
        ok = not errs and not bad_cites
        if kind == "trap" and not ans["abstained"]:
            if q.startswith("作者对火星"):
                trap_errors.append(q)
                ok = False
            if "73 家" in q and "73" in ans["text"]:
                trap_errors.append(q)
                ok = False
            if "2020" in q and "2020" in ans["text"]:
                trap_errors.append(q)
                ok = False
            if q.startswith("作者是否认为") and not any(
                    k in ans["text"] for k in ("不适用", "仅适用", "并非", "不是", "边界", "未必")):
                trap_errors.append(q)
                ok = False
        if ans["abstained"]:
            abstained += 1
        checks.append({"name": q, "ok": ok, "detail": f"claims={len(ans['claims'])} abstain={ans['abstained']}"})
        print(f"[{'ok  ' if ok else 'FAIL'}] {q} | claims={len(ans['claims'])} | abstain={ans['abstained']}")
        if errs:
            print("      schema:", errs[:2])
        if bad_cites:
            print("      citations:", len(bad_cites))

    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0
    result = {
        "milestone": "LIVE", "ts": now_iso(),
        "provider": wb.provider.name, "model": ps["model"],
        "passed": all(c["ok"] for c in checks) and not trap_errors,
        "checks": checks,
        "metrics": {
            "questions": len(QUESTIONS), "llm_claims": llm_claims, "total_claims": total_claims,
            "llm_ratio": round(llm_claims / total_claims, 3) if total_claims else 0,
            "trap_errors": len(trap_errors), "abstained": abstained,
            "latency_p95_ms": round(p95, 1),
        },
    }
    out = ROOT / "eval" / "scorecard-LIVE.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nLLM claims {llm_claims}/{total_claims}  p95 {p95:.0f}ms  traps={len(trap_errors)}  "
          f"abstained={abstained}/{len(QUESTIONS)}")
    print("LIVE", "PASS" if result["passed"] else "FAIL", "->", out)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
