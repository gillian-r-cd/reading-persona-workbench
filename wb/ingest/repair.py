"""Repair parser artifacts: id-like section titles, empty sections."""
from __future__ import annotations

import re

BAD_TITLE = re.compile(r"^第\s*(?:id\d+|titlepage|cover|toc|nav|content)\s*节$", re.I)


def repair_section_titles(db, doc_id: str | None = None) -> dict:
    docs = [doc_id] if doc_id else [r["id"] for r in db.q("SELECT id FROM documents")]
    renamed = removed = 0
    for did in docs:
        secs = [dict(r) for r in db.q(
            "SELECT * FROM sections WHERE doc_id=? ORDER BY ordinal", (did,))]
        has_child = {r["parent_id"] for r in secs if r["parent_id"]}
        for s in secs:
            if s["level"] == 0:
                continue
            first = db.one(
                "SELECT text FROM blocks WHERE section_id=? AND kind!='heading' ORDER BY ordinal LIMIT 1",
                (s["id"],))
            if BAD_TITLE.match(s["title"] or ""):
                if first and first["text"].strip():
                    text = re.sub(r"\s+", " ", first["text"].strip().split("\n")[0])
                    new = text[:30] + ("…" if len(text) > 30 else "")
                    db.x("UPDATE sections SET title=? WHERE id=?", (new, s["id"]))
                    renamed += 1
                elif not has_child and not db.one("SELECT 1 FROM blocks WHERE section_id=? LIMIT 1", (s["id"],)):
                    db.x("DELETE FROM sections WHERE id=?", (s["id"],))
                    removed += 1
    return {"renamed": renamed, "removed": removed}
