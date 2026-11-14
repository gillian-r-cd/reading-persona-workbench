#!/usr/bin/env python3
"""离线演示：导入《远程之后》+ 访谈 + 评论，跑三种人格的问答。"""
from __future__ import annotations

import sys
from pathlib import Path

from wb.workbench import Workbench

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "fixtures" / "corpus"


def main() -> int:
    wb = Workbench(ROOT / "vault")
    print("→ 导入《远程之后》……")
    book = wb.add_document(CORPUS / "remote_work.md")
    print("→ 导入作者访谈与周敏评论……")
    wb.add_corpus_document(book, CORPUS / "author_interview.md", "interview")
    wb.add_corpus_document(book, CORPUS / "review_remote_work.md", "research")
    personas = wb.personas(book)
    author = next(p for p in personas if p["kind"] == "AUTHOR")
    print("人格：", "、".join(f"{p['name']}({p['kind']})" for p in personas))

    text_session = wb.create_session(book, author["id"], persona_mode="textual_author")
    hist_session = wb.create_session(book, author["id"], persona_mode="historical_author", mode="labeled")

    questions = [
        (text_session, "这本书的核心论点是什么？"),
        (text_session, "研究跟踪了多少家公司？"),
        (text_session, "陈薇说了什么？"),
        (text_session, "远程办公适用于所有公司吗？"),
        (text_session, "作者对火星殖民有什么看法？"),
        (hist_session, "你后来改过这个说法吗？"),
        (hist_session, "周敏怎么批评你？"),
        (hist_session, "你为什么写第三章？"),
    ]
    for sid, q in questions:
        ans = wb.ask(sid, q)
        tag = "（弃权）" if ans["abstained"] else ""
        print(f"\n问：{q}{tag}")
        print(ans["text"])
    print("\n完成。运行 `python run.py` 打开 http://127.0.0.1:8765 查看界面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
