"""M5: author as first-class persona (dual modes, positions, sources, challenges)."""
from __future__ import annotations

from pathlib import Path

from tests.harness import Checker
from tests.support import temp_vault
from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"

BOOK_Q = [
    "你的核心论点是什么？", "研究跟踪了多少家公司？", "文档留存率的中位数是多少？",
    "陈薇说了什么？", "你给出了哪三条建议？", "反方观点是什么？",
    "工具成熟度与绩效的相关系数是多少？", "哪类团队不适用远程办公？",
    "强制书面提案对返工率有什么影响？", "Bloom 的研究结论是什么？",
]
IV_Q = [
    "你后来改过这个说法吗？", "再版时你会修正什么？", "你承认了哪些局限？",
    "你对周敏的批评怎么回应？", "你后来改口了吗？", "访谈里你怎么评价弱联系？",
]
RV_Q = [
    "样本的代表性有什么问题？", "这本书的证据强度如何？", "相关性能说明因果吗？",
    "作者忽略了什么反例？", "周敏认为样本有什么偏差？",
]


def run() -> dict:
    ck = Checker("M5")
    with temp_vault() as (cfg, db):
        wb = Workbench(cfg.vault)
        book = wb.add_document(CORPUS / "remote_work.md")
        iv = wb.add_corpus_document(book, CORPUS / "author_interview.md", "interview")
        rv = wb.add_corpus_document(book, CORPUS / "review_remote_work.md", "research")
        personas = wb.personas(book)
        author = next(p for p in personas if p["kind"] == "AUTHOR")
        quoted = next((p for p in personas if p["kind"] == "QUOTED_PERSON"), None)
        synthetic = next((p for p in personas if p["kind"] == "SYNTHETIC"), None)

        ck.true("author has both modes",
                set(author["modes"]) >= {"textual_author", "historical_author"}, str(list(author["modes"])))
        statuses = [p["status"] for p in author.get("positions", [])]
        ck.ge("revised positions recorded", statuses.count("revised"), 1, str(statuses))
        ck.ge("maintained positions recorded", statuses.count("maintained"), 1, str(statuses))
        ck.ge("acknowledged positions recorded", statuses.count("acknowledged"), 1, str(statuses))
        ck.ge("author corpus linked", len(author.get("corpora", [])), 2, str(author.get("corpora")))

        sid_t = wb.create_session(book, author["id"], persona_mode="textual_author")
        sid_h = wb.create_session(book, author["id"], persona_mode="historical_author", mode="labeled")
        sid_s = wb.create_session(book, author["id"], persona_mode="textual_author")

        r_id = wb.ask(wb.create_session(book, quoted["id"]), "你是谁？")
        ck.true("quoted persona self-identifies", not r_id["abstained"] and "陈薇" in r_id["text"],
                r_id["text"][:140])

        # textual author cannot know later interviews
        r = wb.ask(sid_t, "你后来改过这个说法吗？")
        ck.true("textual author abstains on post-book interview", r["abstained"], r["text"][:120])
        ck.true("textual citations stay in book",
                all(c["doc_id"] == book for c in r["citations"]))

        # historical author can, and labels the source
        r = wb.ask(sid_h, "你后来改过这个说法吗？")
        ck.true("historical author recalls revision", not r["abstained"] and "决策留痕" in r["text"],
                r["text"][:200])
        ck.true("interview source cited", any(c["doc_id"] == iv for c in r["citations"]))
        ck.true("interview labelled in text", "访谈" in r["text"])

        # three-source attribution
        book_ok = book_bad = 0
        for q in BOOK_Q:
            ans = wb.ask(sid_t, q)
            if ans["abstained"]:
                book_bad += 1
                continue
            if all(c["doc_id"] == book for c in ans["citations"]):
                book_ok += 1
            else:
                book_bad += 1
        iv_ok = iv_bad = 0
        for q in IV_Q:
            ans = wb.ask(sid_h, q)
            if ans["abstained"]:
                iv_bad += 1
                continue
            iv_ok += 1 if any(c["doc_id"] == iv for c in ans["citations"]) else 0
            iv_bad += 0 if any(c["doc_id"] == iv for c in ans["citations"]) else 1
        rv_ok = rv_bad = 0
        for q in RV_Q:
            ans = wb.ask(sid_h, q)
            if ans["abstained"]:
                rv_bad += 1
                continue
            hit = any(c["doc_id"] == rv for c in ans["citations"])
            rv_ok += 1 if hit else 0
            rv_bad += 0 if hit else 1
        ck.ge("book-source answers stay in book", book_ok / len(BOOK_Q), 0.90, f"{book_ok}/{len(BOOK_Q)}")
        ck.ge("interview questions cite interview", iv_ok / len(IV_Q), 0.90, f"{iv_ok}/{len(IV_Q)}")
        ck.ge("review questions cite review", rv_ok / len(RV_Q), 0.90, f"{rv_ok}/{len(RV_Q)}")
        ck.ge("three-source attribution overall", (book_ok + iv_ok + rv_ok) / (len(BOOK_Q) + len(IV_Q) + len(RV_Q)),
              0.95, f"{book_ok + iv_ok + rv_ok}/{len(BOOK_Q) + len(IV_Q) + len(RV_Q)}")

        # intent statements must be labelled interpretations
        r = wb.ask(sid_h, "你为什么写第三章？")
        ck.true("intent question answered", not r["abstained"], r["text"][:160])
        ck.true("intent labelled as interpretation", "（解读）" in r["text"], r["text"][:160])
        ck.eq("intent claims all interpretation",
              [c["type"] for c in r["claims"]], ["INTERPRETATION"])

        # boundary: post-publication events
        r = wb.ask(sid_h, "书出版之后发生了什么？")
        ck.true("post-publication boundary respected", r["abstained"], r["text"][:120])

        # consistency across repeats
        texts = {wb.ask(sid_s, "你的核心论点是什么？")["text"] for _ in range(5)}
        ck.eq("position consistency across repeats", len(texts), 1, str(len(texts)))

        # voice distinction
        a = wb.ask(sid_t, "远程办公的瓶颈是什么？")["text"]
        q = wb.ask(wb.create_session(book, quoted["id"]), "远程办公的瓶颈是什么？")["text"] if quoted else ""
        s = wb.ask(wb.create_session(book, synthetic["id"]), "远程办公的瓶颈是什么？")["text"] if synthetic else ""
        distinct = len({a, q, s})
        ck.ge("personas are distinguishable", distinct, 2, f"{distinct}/3")
        ck.true("synthetic persona labelled", ("审稿人" in s) or s.startswith("文本没有"), s[:80])

        # challenge handling: acknowledge, don't invent
        challenges = ["你的论证有什么漏洞？", "样本能代表制造业吗？", "弱联系的问题你怎么解决？",
                      "你的结论是不是过度推广了？", "相关性等于因果吗？"]
        good = 0
        for q in challenges:
            ans = wb.ask(sid_h, q)
            if ans["abstained"] or any(k in ans["text"] for k in ("承认", "批评", "局限", "不适用", "不等于", "不能", "并非", "不是")):
                good += 1
        ck.ge("challenge responses grounded", good / len(challenges), 0.90, f"{good}/{len(challenges)}")

    return ck.result()
