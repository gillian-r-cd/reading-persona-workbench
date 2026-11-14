"""M1: ingestion across formats, stable IDs, page mapping, reading order, failures."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from tests.harness import Checker
from tests.make_fixtures import build_all
from tests.support import temp_vault
from wb import anchors
from wb.ingest import pipeline

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"


def _paragraphs(path: Path) -> list[str]:
    text = path.read_text("utf-8")
    parts = [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts[1:]  # drop title


def run() -> dict:
    ck = Checker("M1")
    outs = build_all()

    with temp_vault() as (cfg, db):
        ids = {
            "md": pipeline.ingest(cfg, db, CORPUS / "remote_work.md"),
            "docx": pipeline.ingest(cfg, db, outs["docx"]),
            "epub": pipeline.ingest(cfg, db, outs["epub"]),
            "html": pipeline.ingest(cfg, db, CORPUS / "ai_adoption.html"),
            "txt": pipeline.ingest(cfg, db, CORPUS / "platform_economics.txt"),
            "pdf": pipeline.ingest(cfg, db, outs["pdf"]),
        }

        def blocks(doc_id: str) -> list[dict]:
            return pipeline.blocks_of(db, doc_id)

        md_b = blocks(ids["md"])
        ck.ge("markdown parsed", len(md_b), 30, f"{len(md_b)} blocks")
        ck.ge("docx parsed", len(blocks(ids["docx"])), 30, f"{len(blocks(ids['docx']))} blocks")
        ck.ge("epub parsed", len(blocks(ids["epub"])), 30, f"{len(blocks(ids['epub']))} blocks")
        ck.ge("html parsed", len(blocks(ids["html"])), 10, f"{len(blocks(ids['html']))} blocks")
        ck.ge("txt parsed", len(blocks(ids["txt"])), 15, f"{len(blocks(ids['txt']))} blocks")

        # stable IDs on re-ingest
        again = pipeline.ingest(cfg, db, CORPUS / "remote_work.md")
        ck.eq("re-ingest dedupes by content", again, ids["md"])
        ck.eq("block ids stable on reparse", [b["id"] for b in blocks(again)], [b["id"] for b in md_b])

        # identical content across formats -> same text blocks
        docx_texts = [b["text"] for b in blocks(ids["docx"]) if b["kind"] == "para"]
        md_texts = [b["text"] for b in md_b if b["kind"] == "para"]
        ck.ge("docx/markdown paragraph parity", len(set(docx_texts) & set(md_texts)) / max(1, len(md_texts)), 0.95,
              f"{len(set(docx_texts) & set(md_texts))}/{len(md_texts)}")

        # PDF specifics
        pdf_b = blocks(ids["pdf"])
        ck.ge("pdf blocks extracted", len(pdf_b), 18, f"{len(pdf_b)} blocks")
        ck.eq("pdf page mapping complete", all(b["page_from"] for b in pdf_b), True)
        pages = sorted({b["page_from"] for b in pdf_b})
        ck.ge("pdf spans multiple pages", len(pages), 2, f"pages={pages}")
        ck.true("pdf footnote detected", any(b["kind"] == "footnote" and "reviewers" in b["text"] for b in pdf_b))
        ck.true("pdf title detected", any(b["kind"] == "title" and "Platform Economics" in b["text"] for b in pdf_b))

        # reading order: extracted paragraphs follow layout order
        expected = [p for p in _paragraphs(CORPUS / "platform_economics.txt")
                    if len(p) > 70 and not re.match(r"^(\d+\.|Abstract|References)", p)]
        para_blocks = [b["text"] for b in pdf_b if b["kind"] == "para"]
        positions = []
        for p in expected:
            key = p[:28].replace(" ", "")
            hit = None
            for i, bt in enumerate(para_blocks):
                if key[:20] in bt.replace(" ", ""):
                    hit = i
                    break
            positions.append(hit)
        found = [p for p in positions if p is not None]
        ck.ge("pdf reading-order coverage", len(found) / max(1, len(expected)), 0.95,
              f"{len(found)}/{len(expected)}")
        ck.eq("pdf reading order monotonic", found, sorted(found))

        # heading levels
        heads = [b for b in pdf_b if b["kind"] == "heading"]
        ck.ge("pdf headings detected", len(heads), 7, f"{len(heads)} headings")

        # anchors survive an in-place edit (override) without losing block id
        target = md_b[5]
        a = anchors.make_anchor(target, 0, min(12, len(target["text"])))
        edited = target["text"] + "（修订）"
        db.insert("overrides", {"id": "o1", "doc_id": ids["md"], "target_type": "block",
                                "target_id": target["id"], "patch": json.dumps({"text": edited}),
                                "created_at": "2026-01-01T00:00:00+08:00"})
        pipeline.apply_overrides(db, ids["md"])
        after = pipeline.block_map(db, ids["md"])[target["id"]]
        ck.true("override applied without id change", after["id"] == target["id"] and after["text"].endswith("（修订）"))
        resolved = anchors.resolve_anchor(a, {b["id"]: b["text"] for b in blocks(ids["md"])})
        ck.eq("anchor resolves after edit", resolved["status"], "ok")

    # bad files: structured errors, never crashes
    rnd = random.Random(3)
    errors = 0
    crashes = 0
    with temp_vault() as (cfg, db):
        import tempfile
        for i in range(24):
            ext = [".pdf", ".epub", ".docx", ".html"][i % 4]
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(bytes(rnd.randrange(256) for _ in range(400)))
                path = f.name
            try:
                pipeline.parse_file(path)
            except pipeline.IngestError:
                errors += 1
            except Exception:
                crashes += 1
    ck.eq("malformed files never crash", crashes, 0)
    ck.ge("malformed files produce structured errors", errors, 20, f"{errors}/24")

    return ck.result()
