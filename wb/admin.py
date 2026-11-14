"""Vault administration: export/import, delete, recovery, integrity, offline matrix."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from .util import now_iso, sha256_bytes

DOC_TABLES = ["blocks", "sections", "anchors", "overrides", "entities", "entity_mentions",
              "claims", "argument_edges", "quotes", "ref_entries", "ref_links",
              "chunks", "section_summaries", "highlights"]


def _sha_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def export_vault(cfg, out_path: str | Path, include_secrets: bool = False) -> dict:
    out = Path(out_path)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "vault"
        tmp.mkdir()
        src = sqlite3.connect(cfg.db_path)
        dst = sqlite3.connect(tmp / "meta.db")
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        for sub in ("originals", "snapshots", "traces"):
            src_dir = Path(cfg.vault) / sub
            if src_dir.exists():
                shutil.copytree(src_dir, tmp / sub)
        settings = cfg.load_settings()
        if not include_secrets and "provider" in settings:
            settings["provider"] = {k: v for k, v in settings["provider"].items() if k != "api_key"}
        (tmp / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), "utf-8")

        manifest = {"version": 1, "created_at": now_iso(), "files": {}}
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(tmp))
                manifest["files"][rel] = {"size": p.stat().st_size, "sha256": _sha_file(p)}
        (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")

        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(tmp.rglob("*")):
                if p.is_file():
                    z.write(p, str(p.relative_to(tmp)))
    return {"path": str(out), "files": len(manifest["files"]), "bytes": out.stat().st_size}


def import_vault(cfg, zip_path: str | Path, force: bool = False) -> dict:
    target = Path(cfg.vault)
    existing = [p for p in target.glob("*") if not (p.is_dir() and not any(p.iterdir()))]
    if existing and not force:
        raise ValueError("目标 vault 非空；使用 force=True 覆盖")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        manifest = json.loads((tmp / "manifest.json").read_text("utf-8"))
        for rel, meta in manifest["files"].items():
            p = tmp / rel
            if not p.exists() or p.stat().st_size != meta["size"] or _sha_file(p) != meta["sha256"]:
                raise ValueError(f"manifest 校验失败: {rel}")
        if force:
            for p in existing:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                dest = target / p.relative_to(tmp)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
    from .db import DB
    db = DB(cfg.db_path)
    # rewrite absolute paths from the source machine to this vault
    vault = Path(cfg.vault)
    for doc in db.q("SELECT id, source_path FROM documents"):
        old = Path(doc["source_path"] or "")
        if old.name:
            new = vault / "originals" / old.name
            if new.exists():
                db.x("UPDATE documents SET source_path=? WHERE id=?", (str(new), doc["id"]))
    for src in db.q("SELECT id, snapshot_path FROM sources"):
        old = Path(src["snapshot_path"] or "")
        if old.name:
            new = vault / "snapshots" / old.name
            if new.exists():
                db.x("UPDATE sources SET snapshot_path=? WHERE id=?", (str(new), src["id"]))
    return {"ok": True, "documents": db.one("SELECT count(*) c FROM documents")["c"],
            "blocks": db.one("SELECT count(*) c FROM blocks")["c"]}


def delete_document(db, cfg, doc_id: str) -> dict:
    row = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not row:
        return {"ok": False, "reason": "not found", "deleted": {}}
    counts = {}
    claim_ids = [r["id"] for r in db.q("SELECT id FROM claims WHERE doc_id=?", (doc_id,))]
    if claim_ids:
        ph = ",".join("?" for _ in claim_ids)
        db.x(f"DELETE FROM claim_evidence WHERE claim_id IN ({ph})", tuple(claim_ids))
        counts["claim_evidence"] = len(claim_ids)
    persona_ids = [r["id"] for r in db.q("SELECT id FROM personas WHERE doc_id=?", (doc_id,))]
    for pid in persona_ids:
        db.x("DELETE FROM persona_spans WHERE persona_id=?", (pid,))
        db.x("DELETE FROM persona_corpora WHERE persona_id=?", (pid,))
    for table in DOC_TABLES + ["personas"]:
        n = db.one(f"SELECT count(*) c FROM {table} WHERE doc_id=?", (doc_id,))["c"]
        if n:
            db.x(f"DELETE FROM {table} WHERE doc_id=?", (doc_id,))
            counts[table] = n
    n = db.one("SELECT count(*) c FROM chunks_fts WHERE doc_id=?", (doc_id,))["c"]
    if n:
        db.x("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
        counts["chunks_fts"] = n
    for s in db.q("SELECT id,doc_ids FROM sessions"):
        try:
            if doc_id in json.loads(s["doc_ids"]):
                db.x("DELETE FROM messages WHERE session_id=?", (s["id"],))
                db.x("DELETE FROM pins WHERE session_id=?", (s["id"],))
                db.x("DELETE FROM ledger WHERE session_id=?", (s["id"],))
                db.x("DELETE FROM ai_calls WHERE session_id=?", (s["id"],))
                db.x("DELETE FROM sessions WHERE id=?", (s["id"],))
                counts["sessions"] = counts.get("sessions", 0) + 1
        except Exception:
            continue
    db.x("DELETE FROM project_documents WHERE doc_id=?", (doc_id,))
    db.x("DELETE FROM project_links WHERE src_doc=? OR dst_doc=?", (doc_id, doc_id))
    db.x("DELETE FROM documents WHERE id=?", (doc_id,))
    counts["documents"] = 1
    other = db.one("SELECT count(*) c FROM documents WHERE source_sha=?", (row["source_sha"],))["c"]
    if other == 0:
        p = Path(row["source_path"]) if row["source_path"] else None
        if p and p.is_file():
            p.unlink()
            counts["original_file"] = 1
    return {"ok": True, "deleted": counts, "doc_id": doc_id}


def recover(cfg, db) -> dict:
    """Remove half-ingested documents left behind by a crash."""
    bad = [r["id"] for r in db.q("SELECT id FROM documents WHERE status != 'ready'")]
    removed = []
    for doc_id in bad:
        delete_document(db, cfg, doc_id)
        removed.append(doc_id)
    return {"removed": removed, "count": len(removed)}


def integrity_check(db, cfg) -> dict:
    problems = []
    for b in db.q("SELECT id,doc_id FROM blocks"):
        if not db.one("SELECT 1 FROM documents WHERE id=?", (b["doc_id"],)):
            problems.append(f"orphan block {b['id']}")
    for ch in db.q("SELECT id,block_ids FROM chunks WHERE level='unit'"):
        for bid in json.loads(ch["block_ids"]):
            if not db.one("SELECT 1 FROM blocks WHERE id=?", (bid,)):
                problems.append(f"chunk {ch['id']} missing block {bid}")
                break
    for a in db.q("SELECT id,block_id FROM anchors"):
        if not db.one("SELECT 1 FROM blocks WHERE id=?", (a["block_id"],)):
            problems.append(f"anchor {a['id']} orphaned")
    for s in db.q("SELECT id,snapshot_path FROM sources"):
        if s["snapshot_path"] and not Path(s["snapshot_path"]).exists():
            problems.append(f"snapshot missing {s['id']}")
    return {"ok": not problems, "problems": problems[:20], "checked_at": now_iso()}


def vault_stats(db, cfg) -> dict:
    tables = ["documents", "blocks", "chunks", "claims", "personas", "highlights", "sources", "dossiers"]
    out = {t: db.one(f"SELECT count(*) c FROM {t}")["c"] for t in tables}
    out["vault_bytes"] = sum(p.stat().st_size for p in Path(cfg.vault).rglob("*") if p.is_file())
    out["db_bytes"] = Path(cfg.db_path).stat().st_size if Path(cfg.db_path).exists() else 0
    return out


def offline_matrix() -> dict:
    return {
        "parse": True, "index": True, "retrieve": True, "strict_gate": True,
        "persona": True, "research": False, "generation_offline": True,
        "generation_byok": "needs_network",
        "note": "离线时生成走抽取式 claim；联网研究必须显式配置来源，失败会明确降级。",
    }


def network_audit(root: Path) -> dict:
    hits = []
    for p in sorted(Path(root).rglob("*.py")):
        if "tests" in p.parts:
            continue
        text = p.read_text("utf-8", errors="replace")
        import re as _re
        if _re.search(r"(?m)^\s*(import|from)\s+(urllib\.request|http\.client|socket)\b", text):
            hits.append(str(p.relative_to(root)))
    return {"modules": hits,
            "ok": set(hits) <= {"wb/providers/base.py", "wb/research/fetch.py",
                                "wb/research/search.py"}}


def benchmark(wb, doc_id: str, rounds: int = 20) -> dict:
    import time
    from .index import chunker, retrieve
    t0 = time.perf_counter()
    chunker.build_index(wb.db, doc_id)
    index_s = time.perf_counter() - t0
    scope = retrieve.Scope([doc_id])
    times = []
    for i in range(rounds):
        t = time.perf_counter()
        retrieve.search(wb.db, scope, "核心论点与证据", k=10)
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    pages = wb.db.one("SELECT max(page_to) m FROM blocks WHERE doc_id=?", (doc_id,))["m"] or 0
    return {"pages": pages, "index_seconds": round(index_s, 3),
            "retrieve_p95_ms": round(times[int(len(times) * 0.95) - 1], 2),
            "blocks": wb.db.one("SELECT count(*) c FROM blocks WHERE doc_id=?", (doc_id,))["c"]}
