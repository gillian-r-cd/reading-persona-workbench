"""Developer audit log: every AI call and retrieval step."""
from __future__ import annotations

import json
import secrets

from .util import now_iso, stable_id


def record(db, session_id: str, turn_id: str, kind: str, provider: str = "",
           model: str = "", latency_ms: float = 0.0, request=None, response=None,
           status: str = "ok", error: str = "") -> str:
    cid = stable_id("call", session_id, turn_id, kind, str(latency_ms), now_iso(), secrets.token_hex(3), n=16)
    db.insert("ai_calls", {
        "id": cid, "session_id": session_id, "turn_id": turn_id, "kind": kind,
        "provider": provider, "model": model, "latency_ms": round(latency_ms, 2),
        "status": status, "error": error,
        "request": json.dumps(request or {}, ensure_ascii=False)[:20000],
        "response": json.dumps(response or {}, ensure_ascii=False)[:40000],
        "created_at": now_iso(),
    })
    return cid


def list_calls(db, session_id: str | None = None, kind: str | None = None,
               limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM ai_calls"
    args: list = []
    cond = []
    if session_id:
        cond.append("session_id=?")
        args.append(session_id)
    if kind:
        cond.append("kind=?")
        args.append(kind)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in db.q(sql, tuple(args)):
        d = dict(r)
        d["request"] = json.loads(d.get("request") or "{}")
        d["response"] = json.loads(d.get("response") or "{}")
        out.append(d)
    return out


def get_call(db, call_id: str) -> dict | None:
    r = db.one("SELECT * FROM ai_calls WHERE id=?", (call_id,))
    if not r:
        return None
    d = dict(r)
    d["request"] = json.loads(d.get("request") or "{}")
    d["response"] = json.loads(d.get("response") or "{}")
    return d


def stats(db, session_id: str | None = None) -> dict:
    where, args = "", []
    if session_id:
        where, args = " WHERE session_id=?", [session_id]
    rows = db.q(f"SELECT kind, count(*) c, avg(latency_ms) avg_ms FROM ai_calls{where} GROUP BY kind", tuple(args))
    total = db.one(f"SELECT count(*) c FROM ai_calls{where}", tuple(args))["c"]
    return {"total": total,
            "by_kind": {r["kind"]: {"count": r["c"], "avg_ms": round(r["avg_ms"] or 0, 1)} for r in rows}}
