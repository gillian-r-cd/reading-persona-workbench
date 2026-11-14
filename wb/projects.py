"""Projects: a container of materials, links, sessions and personas."""
from __future__ import annotations

from .util import now_iso, stable_id


def create(db, name: str, description: str = "") -> dict:
    pid = stable_id("proj", name, now_iso(), str(db.one("SELECT count(*) c FROM projects")["c"]), n=12)
    db.insert("projects", {"id": pid, "name": name.strip() or "未命名项目",
                           "description": description, "created_at": now_iso(),
                           "updated_at": now_iso(), "settings": "{}"})
    return get(db, pid)


def list_all(db) -> list[dict]:
    rows = db.q("SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC")
    out = []
    for r in rows:
        d = dict(r)
        d["doc_count"] = db.one("SELECT count(*) c FROM project_documents WHERE project_id=?", (d["id"],))["c"]
        d["session_count"] = db.one("SELECT count(*) c FROM sessions WHERE project_id=?", (d["id"],))["c"]
        out.append(d)
    return out


def get(db, pid: str) -> dict | None:
    r = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not r:
        return None
    d = dict(r)
    d["documents"] = documents(db, pid)
    d["links"] = list_links(db, pid)
    return d


def update(db, pid: str, name: str | None = None, description: str | None = None) -> dict | None:
    if name is not None:
        db.x("UPDATE projects SET name=?, updated_at=? WHERE id=?", (name, now_iso(), pid))
    if description is not None:
        db.x("UPDATE projects SET description=?, updated_at=? WHERE id=?", (description, now_iso(), pid))
    return get(db, pid)


def delete(db, pid: str) -> dict:
    db.x("DELETE FROM project_documents WHERE project_id=?", (pid,))
    db.x("DELETE FROM project_links WHERE project_id=?", (pid,))
    db.x("UPDATE sessions SET project_id='' WHERE project_id=?", (pid,))
    db.x("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True}


def add_document(db, pid: str, doc_id: str, role: str = "source") -> dict:
    if not db.one("SELECT 1 FROM projects WHERE id=?", (pid,)):
        raise ValueError("project not found")
    if not db.one("SELECT 1 FROM documents WHERE id=?", (doc_id,)):
        raise ValueError("document not found")
    db.insert_ignore("project_documents", {"project_id": pid, "doc_id": doc_id,
                                           "role": role, "added_at": now_iso()})
    db.x("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), pid))
    return {"ok": True}


def remove_document(db, pid: str, doc_id: str) -> dict:
    db.x("DELETE FROM project_documents WHERE project_id=? AND doc_id=?", (pid, doc_id))
    db.x("DELETE FROM project_links WHERE project_id=? AND (src_doc=? OR dst_doc=?)", (pid, doc_id, doc_id))
    db.x("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), pid))
    return {"ok": True}


def documents(db, pid: str) -> list[dict]:
    rows = db.q(
        "SELECT d.*, pd.role AS project_role, pd.added_at FROM project_documents pd "
        "JOIN documents d ON d.id = pd.doc_id WHERE pd.project_id=? ORDER BY pd.added_at", (pid,))
    return [{k: v for k, v in dict(r).items() if k not in ("meta", "source_path")} for r in rows]


def doc_ids(db, pid: str) -> list[str]:
    return [r["doc_id"] for r in db.q(
        "SELECT doc_id FROM project_documents WHERE project_id=? ORDER BY added_at", (pid,))]


def add_link(db, pid: str, src: str, dst: str, kind: str = "related", note: str = "") -> dict:
    if src == dst:
        raise ValueError("不能连接自己")
    for d in (src, dst):
        if not db.one("SELECT 1 FROM project_documents WHERE project_id=? AND doc_id=?", (pid, d)):
            raise ValueError("两端素材都必须先加入项目")
    lid = stable_id("link", pid, src, dst, kind, now_iso(), n=12)
    db.insert("project_links", {"id": lid, "project_id": pid, "src_doc": src, "dst_doc": dst,
                                "kind": kind, "note": note, "created_at": now_iso()})
    return {"id": lid}


def list_links(db, pid: str) -> list[dict]:
    return [dict(r) for r in db.q(
        "SELECT l.*, a.title AS src_title, b.title AS dst_title FROM project_links l "
        "LEFT JOIN documents a ON a.id=l.src_doc LEFT JOIN documents b ON b.id=l.dst_doc "
        "WHERE l.project_id=? ORDER BY l.created_at", (pid,))]


def delete_link(db, pid: str, lid: str) -> dict:
    db.x("DELETE FROM project_links WHERE id=? AND project_id=?", (lid, pid))
    return {"ok": True}


def graph(db, pid: str) -> dict:
    docs = documents(db, pid)
    nodes = [{"id": d["id"], "label": d["title"], "kind": d.get("doc_type", "doc")} for d in docs]
    edges = [{"id": l["id"], "source": l["src_doc"], "target": l["dst_doc"],
              "kind": l["kind"], "label": l["kind"]} for l in list_links(db, pid)]
    return {"nodes": nodes, "edges": edges}
