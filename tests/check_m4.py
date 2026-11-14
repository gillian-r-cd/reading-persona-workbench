"""M4: strict fact gate, end-to-end answering, traps, abstention, trace."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from tests.harness import Checker
from tests.support import temp_vault
from wb.contracts import validate_claim
from wb.providers.base import OfflineProvider
from wb.strict import gate as gate_mod
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"

OUT_OF_DOC = ["火星殖民", "量子计算", "咖啡烘焙", "深海采矿", "太阳能电池", "疫苗研发", "城市地铁",
              "足球战术", "葡萄酒酿造", "冰川融化", "芯片光刻", "无人机配送", "核聚变", "珊瑚礁",
              "古代钱币", "交响乐", "蜜蜂养殖", "沙漠农业", "极地考察", "虚拟现实", "热带雨林",
              "页岩气", "基因编辑", "自动驾驶", "养老产业", "短视频算法", "稀土贸易", "海洋塑料",
              "城市供热", "航空发动机"]

LEADING = [
    "作者是否认为远程办公适用于所有公司？",
    "作者是否说过工具是远程绩效的决定因素？",
    "作者是否认为远程办公对所有团队都更好？",
    "作者是否主张取消线下办公？",
    "作者是否认为文档越漂亮越好？",
    "作者是否认为样本可以代表制造业？",
    "作者是否认为 Bloom 的研究可以直接外推到创意工作？",
    "作者是否认为远程办公没有边界条件？",
    "作者是否说过书面协作没有成本？",
    "作者是否认为所有员工都适合远程？",
]
BOUNDARY_CUES = ("不适用", "仅适用", "并非", "不是", "边界", "未必", "不能", "无法", "局限")

NEAR_MISS = [("73 家", "73"), ("55 家", "55"), ("120 家", "120"), ("500 家", "500"),
             ("99%", "99%"), ("87%", "87%"), ("2000 家", "2000"), ("300 家", "300"),
             ("88%", "88%"), ("42 家", "42")]
TIME_MISS = ["1998", "2005", "2008", "2010", "2012", "2016", "2017", "2018", "1999", "2003"]


def _valid_claims(answer, blocks: dict[str, str]) -> list[str]:
    errs = []
    for c in answer["claims"]:
        errs.extend(validate_claim(c, blocks))
    return errs


def run() -> dict:
    ck = Checker("M4")
    with temp_vault() as (cfg, db):
        wb = Workbench(cfg.vault)
        did = wb.add_document(CORPUS / "remote_work.md")
        sid = wb.create_session(did, mode="strict")
        blocks = {b["id"]: b["text"] for b in wb.blocks(did)}
        provider = OfflineProvider()

        # --- happy path -------------------------------------------------
        r = wb.ask(sid, "这本书的核心论点是什么？")
        ck.true("thesis question answered", not r["abstained"] and "瓶颈不是工具" in r["text"], r["text"][:200])
        ck.true("thesis answer cited", "[S1]" in r["text"])
        ck.eq("thesis claims structurally valid", _valid_claims(r, blocks), [])

        r_id = wb.ask(sid, "你是谁？")
        ck.true("identity question answered", not r_id["abstained"] and "林知远" in r_id["text"],
                r_id["text"][:160])
        ck.eq("identity makes no factual claims", len(r_id["claims"]), 0)

        r2 = wb.ask(sid, "陈薇说了什么？")
        ck.true("quote question answered", not r2["abstained"] and "没有输在工具上" in r2["text"], r2["text"][:200])
        ck.true("quote speaker present", "陈薇" in r2["text"])

        r3 = wb.ask(sid, "研究跟踪了多少家公司？")
        ck.true("numeric question answered", "37" in r3["text"], r3["text"][:200])
        ck.eq("numeric claims valid", _valid_claims(r3, blocks), [])

        ck.eq("ledger contract valid", __import__("wb.contracts", fromlist=["validate_ledger"]).validate_ledger(r["ledger"]), [])
        ck.true("trace persisted", Path(r["trace_path"]).exists())
        ck.eq("citations resolve",
              sum(1 for c in r["citations"] if c["block_id"] in blocks), len(r["citations"]))
        ck.true("all rendered sentences carry citations",
                all(seg.strip().endswith("]") for seg in re.findall(r"[^。！？]*[。！？]", r["text"]) if seg.strip()))

        # --- gate unit tests -------------------------------------------
        bt = {"b1": "远程办公并不是普遍更优的选择。", "b2": "研究跟踪了 37 家公司。"}
        emp = {"block_id": "b1", "start": 0, "end": 10, "quote": "远程办公并不是普遍", "relation": "supports"}
        bad_quote = {"id": "c_bad", "text": "远程办公并不是普遍", "type": "FACT_EXPLICIT",
                     "evidence": [dict(emp, quote="完全不存在的一句话")], "qualifiers": [], "confidence": 0.9}
        rep = gate_mod.evaluate([bad_quote], bt, None, provider)
        ck.true("gate rejects fabricated quote", any(f["gate"] in ("G1_structure", "G2_quote") for f in rep["failures"]))

        radius_claim = {"id": "c_rad", "text": "远程办公并不是普遍更优的选择。", "type": "FACT_EXPLICIT",
                        "evidence": [{"block_id": "b1", "start": 0, "end": 16,
                                      "quote": "远程办公并不是普遍更优的选择。", "relation": "supports"}],
                        "qualifiers": [], "confidence": 0.9}
        rep = gate_mod.evaluate([radius_claim], bt, {"b2"}, provider)
        ck.true("gate enforces evidence radius", any(f["gate"] == "G6_radius" for f in rep["failures"]))

        num_claim = {"id": "c_num", "text": "研究跟踪了 73 家公司。", "type": "FACT_ENTAILED",
                     "evidence": [{"block_id": "b2", "start": 0, "end": 14,
                                   "quote": "研究跟踪了 37 家公司。", "relation": "supports"}],
                     "qualifiers": [], "confidence": 0.9}
        rep = gate_mod.evaluate([num_claim], bt, None, provider)
        ck.true("gate blocks untraceable numbers", any(f["gate"] == "G4_number" for f in rep["failures"]))

        qual_claim = {"id": "c_q", "text": "远程办公适用于所有团队。", "type": "FACT_ENTAILED",
                      "evidence": [{"block_id": "b1", "start": 0, "end": 16,
                                    "quote": "远程办公并不是普遍更优的选择。", "relation": "supports"}],
                      "qualifiers": ["仅适用于成熟团队"], "confidence": 0.9}
        rep = gate_mod.evaluate([qual_claim], bt, None, provider)
        ck.true("gate blocks lost qualifiers", any(f["gate"] == "G5_qualifier" for f in rep["failures"]))

        unsup = {"id": "c_u", "text": "远程办公适用于所有公司。", "type": "FACT_ENTAILED",
                 "evidence": [{"block_id": "b1", "start": 0, "end": 16,
                               "quote": "远程办公并不是普遍更优的选择。", "relation": "supports"}],
                 "qualifiers": [], "confidence": 0.9}
        rep = gate_mod.evaluate([unsup], bt, None, provider)
        ck.true("gate blocks unsupported claim", any(f["gate"] == "G3_support" for f in rep["failures"]))

        mixed = [radius_claim, num_claim, qual_claim, unsup,
                 dict(radius_claim, id="c_ok", evidence=[{"block_id": "b2", "start": 0, "end": 10,
                                                          "quote": "研究跟踪了 37 家", "relation": "supports"}],
                      text="研究跟踪了 37 家", type="FACT_EXPLICIT")]
        kept, report, dropped = gate_mod.repair(mixed, bt, {"b2"}, provider)
        ck.eq("gate repairs by dropping", dropped, 4, f"dropped={dropped}, kept={len(kept)}")
        ck.eq("kept claims are valid", [c["id"] for c in kept], ["c_ok"])

        # --- future/goal questions are answered from stated goals -------
        vision = Path(tempfile.mkdtemp()) / "vision.md"
        vision.write_text("# 愿景\n\n我们的目标是建立一个独立、自由、民主、统一和富强的新中国。"
                          "为了这个目标，必须联合一切力量，并建立广泛的共同纲领。\n", "utf-8")
        vdoc = wb.add_document(vision)
        vsid = wb.create_session(vdoc)
        vans = wb.ask(vsid, "中国的未来理想情况下是什么样的？")
        ck.true("future question answered from stated goals",
                not vans["abstained"] and "独立、自由、民主" in vans["text"], vans["text"][:160])
        ck.true("future answer labelled as goals, not prediction",
                "不是对未来的预测" in vans["text"])

        # --- traps ------------------------------------------------------
        trap_errors = []
        for topic in OUT_OF_DOC[:12]:
            ans = wb.ask(sid, f"作者对{topic}的看法是什么？")
            if not ans["abstained"]:
                trap_errors.append(("out_of_doc", topic))
        leading_errors = 0
        for q in LEADING:
            ans = wb.ask(sid, q)
            if not ans["abstained"] and not any(c in ans["text"] for c in BOUNDARY_CUES):
                leading_errors += 1
        number_errors = 0
        for phrase, forbidden in NEAR_MISS:
            ans = wb.ask(sid, f"研究跟踪了 {phrase}公司吗？")
            flat = ans["text"].replace(" ", "")
            if re.search(r"(?<![\d.])" + re.escape(forbidden.rstrip("%")) + r"(?![\d%])", flat):
                number_errors += 1
        time_errors = 0
        for year in TIME_MISS:
            ans = wb.ask(sid, f"Bloom 是在 {year} 年做的那项研究吗？")
            if year in ans["text"]:
                time_errors += 1
        ck.eq("out-of-document traps answered", len(trap_errors), 0, str(trap_errors))
        ck.eq("leading-question trap errors", leading_errors, 0, f"{leading_errors}/{len(LEADING)}")
        ck.eq("near-miss number errors", number_errors, 0, f"{number_errors}/{len(NEAR_MISS)}")
        ck.eq("time-mismatch errors", time_errors, 0, f"{time_errors}/{len(TIME_MISS)}")

        # --- abstention -------------------------------------------------
        abstained = 0
        for topic in OUT_OF_DOC[12:]:
            ans = wb.ask(sid, f"作者如何评价{topic}？")
            abstained += 1 if ans["abstained"] else 0
        ck.ge("correct abstention rate", abstained / 18, 0.90, f"{abstained}/18")

        # --- qualifier preservation ------------------------------------
        boundary_qs = ["远程办公适用于哪些团队？", "远程办公有什么边界条件？", "远程办公对所有公司都适用吗？",
                       "远程办公在什么情况下不适用？", "远程红利的前提是什么？"] * 4
        preserved = 0
        for q in boundary_qs:
            ans = wb.ask(sid, q)
            if any(c in ans["text"] for c in ("不适用", "仅适用", "边界", "条件")):
                preserved += 1
        ck.ge("qualifier preservation", preserved / len(boundary_qs), 0.90, f"{preserved}/{len(boundary_qs)}")

        # --- quote fidelity across all stored claims --------------------
        bad_quotes = 0
        total = 0
        for row in db.q("SELECT * FROM messages WHERE session_id=? AND role='assistant'", (sid,)):
            for c in json.loads(row["claims"] or "[]"):
                for e in c["evidence"]:
                    total += 1
                    if e["quote"] not in blocks.get(e["block_id"], ""):
                        bad_quotes += 1
        ck.eq("quote fidelity (all turns)", bad_quotes, 0, f"{total} evidence spans")

    return ck.result()
