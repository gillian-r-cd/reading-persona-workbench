"""Persona registry: author is a first-class object with two modes."""
from __future__ import annotations

import json
import re

from ..index.retrieve import Scope
from ..util import now_iso, split_sentences, stable_id

POS_REVISED = ("改成", "修正", "不再", "放弃", "重写", "改口")
POS_MAINTAINED = ("仍然", "坚持", "没有放弃", "依旧")
POS_ACK = ("承认", "是对的", "批评是", "确实", "有道理")


def _doc_blocks(db, doc_id: str) -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM blocks WHERE doc_id=? ORDER BY ordinal", (doc_id,))]


def _corpora(db, persona_id: str) -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM persona_corpora WHERE persona_id=? ORDER BY added_at",
                                  (persona_id,))]


def author_card(db, doc_id: str) -> dict:
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    blocks = _doc_blocks(db, doc_id)
    exemplars = []
    for b in blocks:
        for s in split_sentences(b["text"]):
            if "我" in s["text"] and 8 < len(s["text"]) < 80:
                exemplars.append({"block_id": b["id"], "text": s["text"]})
            if len(exemplars) >= 6:
                break
        if len(exemplars) >= 6:
            break
    positions = []
    thesis = db.one("SELECT * FROM claims WHERE doc_id=? AND role='thesis'", (doc_id,))
    if thesis:
        positions.append({"stmt": thesis["text"], "source": doc_id,
                          "since": "", "status": "maintained", "kind": "book"})
    return {
        "id": stable_id("p", doc_id, "author", n=12),
        "doc_id": doc_id,
        "kind": "AUTHOR",
        "name": (doc["author"] or "作者").strip(),
        "modes": {
            "textual_author": {"scope": [doc_id],
                               "description": "文本中的作者，只依据本书回答“这本书在说什么”"},
            "historical_author": {"scope": [doc_id],
                                  "description": "历史中的作者，可引用访谈与其他语料"},
        },
        "evidence_radius": {
            "spans": "all_in_doc", "doc_ids": [doc_id],
            "external_corpora": [], "forbidden": ["未研究的出版后事件"],
        },
        "positions": positions,
        "corpora": [],
        "voice": {
            "register": "克制、书面、先给判断再给限定" if doc["language"] == "zh" else "measured, argumentative",
            "syntax": "短句为主，常用“因此/然而”承接",
            "avoid": ["客服腔", "网络流行语", "自称 AI"],
            "exemplars": exemplars,
        },
        "boundaries": {
            "cannot_know": ["书出版之后发生的事（除非另有研究）", "他人的私下想法"],
            "must_disclaim": ["未在文本或访谈中表达过的写作意图"],
        },
        "created_at": now_iso(),
    }


GENERIC_SPEAKERS = {"农民", "地主", "群众", "大家", "人们", "他们", "我们", "你们", "工人",
                    "士兵", "同志", "同志们", "干部", "人民", "敌人", "有人说", "这种人",
                    "那些人", "老百姓", "国民党", "共产党", "帝国主义", "日本", "中国"}
MAX_QUOTED = 8
MAX_SUBJECT = 8
SURNAMES = set("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹苏潘葛范彭鲁韦马方俞任袁柳史唐薛雷贺倪汤罗毕郝安常傅齐康伍余顾孟黄萧尹姚汪祁毛狄米戴宋庞熊纪舒项董梁杜阮蓝闵席季贾路江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡霍万卢莫房解应丁邓洪包石崔程陆翁荀羊惠曲封储段富焦巴牧山谷车侯全班秋仲伊宫宁仇甘厉祖武符刘景詹龙叶黎白怀蒲鄂索赖卓屠蒙池乔翟谭劳姬申扶冉雍桑桂牛寿通边燕浦尚农温庄晏柴瞿阎慕连茹习艾鱼容向古易慎戈廖庾居衡步都耿满匡国文寇广东欧沃利蔚越隆师巩聂晁融冷辛简饶曾沙鞠巢关查荆红游权盖益桓")
BAD_SPEAKERS = {"提出", "一个", "实行", "什么", "规定", "可以", "应该", "这是", "就是", "还有",
                "因为", "所以", "如果", "但是", "现在", "当时", "他们", "我们", "你们", "原文",
                "要求", "必须", "不能", "没有", "已经", "而且", "或者", "以及", "对于", "关于"}


def _plausible_person(name: str, author: str = "") -> bool:
    if not name or name in BAD_SPEAKERS or name in GENERIC_SPEAKERS:
        return False
    if author and (name == author or name in author or any(name[i:i + 2] in author for i in range(len(name) - 1))):
        return False
    if name.isascii():
        return name[:1].isupper() and 2 <= len(name) <= 30
    if not (2 <= len(name) <= 3):
        return False
    return name[0] in SURNAMES


def ensure_personas(db, doc_id: str) -> list[dict]:
    existing = [dict(r) for r in db.q("SELECT * FROM personas WHERE doc_id=?", (doc_id,))]
    if existing:
        cards = []
        for r in existing:
            card = json.loads(r["card"])
            card["corpora"] = _corpora(db, card["id"])
            card["origin"] = r.get("origin") or card.get("origin", "auto")
            cards.append(card)
        return cards

    card = author_card(db, doc_id)
    _save(db, card, doc_id)
    author_name = db.one("SELECT author FROM documents WHERE id=?", (doc_id,))["author"] or ""

    def related_to_author(name: str) -> bool:
        return bool(author_name) and _plausible_person(name, author_name) is False and (
            name == author_name or len(name) < 2
            or name in author_name or any(name[i:i + 2] in author_name for i in range(len(name) - 1)))

    # Very large anthologies produce noisy speaker attribution; only the author
    # (and a synthetic critic) get auto-generated there. Users can still add corpora.
    block_count = db.one("SELECT count(*) c FROM blocks WHERE doc_id=?", (doc_id,))["c"]
    auto_cast = block_count <= 1500

    # quoted persons, ranked by how often they are quoted
    rows = db.q(
        "SELECT speaker_name AS name, count(*) AS c FROM quotes "
        "WHERE doc_id=? AND speaker_name IS NOT NULL AND speaker_name != '' "
        "GROUP BY speaker_name ORDER BY c DESC, speaker_name ASC", (doc_id,))
    created: set[str] = set()
    for row in (rows if auto_cast else []):
        name = row["name"]
        if not _plausible_person(name, author_name):
            continue
        pid = stable_id("p", doc_id, "quoted", name, n=12)
        if pid in created:
            continue
        created.add(pid)
        pcard = {
            "id": pid, "doc_id": doc_id, "kind": "QUOTED_PERSON", "name": name,
            "modes": {"self": {"scope": [doc_id], "description": "只依据被引述的内容发言"}},
            "evidence_radius": {"spans": "explicit_set", "doc_ids": [doc_id],
                                "external_corpora": [], "forbidden": ["书中未记录的立场"]},
            "positions": [], "corpora": [],
            "voice": {"register": "口语、直接", "avoid": ["替作者辩护"]},
            "boundaries": {"cannot_know": ["书中没有记录的其他观点"],
                           "must_disclaim": ["超出引述范围的推断"]},
            "created_at": now_iso(),
        }
        _save(db, pcard, doc_id)
        ids = {r["block_id"] for r in db.q(
            "SELECT block_id FROM entity_mentions WHERE entity_id IN "
            "(SELECT id FROM entities WHERE doc_id=? AND canonical_name=?)", (doc_id, name))}
        ids |= {r["block_id"] for r in db.q(
            "SELECT block_id FROM quotes WHERE doc_id=? AND speaker_name=?", (doc_id, name))}
        for bid in ids:
            db.x("INSERT OR IGNORE INTO persona_spans(persona_id, block_id, weight) VALUES(?,?,1.0)",
                 (pid, bid))
        if len(created) >= MAX_QUOTED:
            break

    # subjects: entities with the most mentions, excluding the author and quoted speakers
    quoted_names = {r["name"] for r in rows}
    candidates = []
    for ent in db.q("SELECT * FROM entities WHERE doc_id=? AND type IN ('person','org')", (doc_id,)):
        name = ent["canonical_name"]
        if not _plausible_person(name, author_name) or name in quoted_names:
            continue
        mentions = db.q("SELECT block_id FROM entity_mentions WHERE entity_id=?", (ent["id"],))
        if len(mentions) >= 2:
            candidates.append((len(mentions), ent, mentions))
    candidates.sort(key=lambda x: -x[0])
    for _, ent, mentions in (candidates[:MAX_SUBJECT] if auto_cast else []):
        name = ent["canonical_name"]
        pid = stable_id("p", doc_id, "subject", name, n=12)
        if pid in created:
            continue
        created.add(pid)
        subj = {
            "id": pid, "doc_id": doc_id, "kind": "SUBJECT", "name": name,
            "modes": {"self": {"scope": [doc_id], "description": "只依据书中与 TA 有关的记录发言"}},
            "evidence_radius": {"spans": "explicit_set", "doc_ids": [doc_id],
                                "external_corpora": [], "forbidden": ["书中未记录的立场"]},
            "positions": [], "corpora": [],
            "voice": {"register": "就事论事", "avoid": ["替作者说话"]},
            "boundaries": {"cannot_know": ["书中没有记录的信息"],
                           "must_disclaim": ["超出记录范围的推断"]},
            "created_at": now_iso(),
        }
        _save(db, subj, doc_id)
        for m in mentions:
            db.x("INSERT OR IGNORE INTO persona_spans(persona_id, block_id, weight) VALUES(?,?,1.0)",
                 (pid, m["block_id"]))

    pid = stable_id("p", doc_id, "synthetic", "reviewer", n=12)
    scard = {
        "id": pid, "doc_id": doc_id, "kind": "SYNTHETIC", "name": "严厉的审稿人",
        "modes": {"critic": {"scope": [doc_id], "description": "以方法学审稿人的立场提出质疑"}},
        "evidence_radius": {"spans": "all_in_doc", "doc_ids": [doc_id],
                            "external_corpora": [], "forbidden": []},
        "positions": [], "corpora": [],
        "voice": {"register": "直接、挑剔、就事论事", "avoid": ["人身攻击"]},
        "boundaries": {"cannot_know": ["作者未公开的意图"],
                       "must_disclaim": ["自己的判断是解读"]},
        "created_at": now_iso(),
    }
    _save(db, scard, doc_id)
    return ensure_personas(db, doc_id)


def _save(db, card: dict, doc_id: str) -> None:
    db.insert("personas", {
        "id": card["id"], "doc_id": doc_id, "kind": card["kind"], "name": card["name"],
        "card": json.dumps(card, ensure_ascii=False), "version": 1,
        "created_at": card.get("created_at", now_iso()), "updated_at": now_iso(),
    })


def get_persona(db, persona_id: str) -> dict | None:
    row = db.one("SELECT * FROM personas WHERE id=?", (persona_id,))
    if not row:
        return None
    card = json.loads(row["card"])
    card["corpora"] = _corpora(db, persona_id)
    origin = row["origin"] if "origin" in row.keys() else card.get("origin", "auto")
    card["origin"] = origin or "auto"
    return card


def add_corpus(db, persona_id: str, doc_id: str, kind: str = "interview") -> None:
    person = get_persona(db, persona_id)
    if not person:
        raise ValueError("persona not found")
    db.x("INSERT OR IGNORE INTO persona_corpora(persona_id, doc_id, kind, added_at) VALUES(?,?,?,?)",
         (persona_id, doc_id, kind, now_iso()))
    refresh_positions(db, persona_id)


def refresh_positions(db, persona_id: str) -> list[dict]:
    person = get_persona(db, persona_id)
    if not person:
        return []
    book = person["doc_id"]
    corpus = _corpora(db, persona_id)
    positions = [p for p in person.get("positions", []) if p.get("kind") == "book"]
    for c in corpus:
        if c["kind"] not in ("interview", "research", "other"):
            continue
        for row in db.q("SELECT * FROM claims WHERE doc_id=? ORDER BY rowid", (c["doc_id"],)):
            text = row["text"]
            if "我" not in text and not any(k in text for k in ("批评是", "是对的")):
                continue
            status = None
            if any(k in text for k in POS_REVISED):
                status = "revised"
            elif any(k in text for k in POS_MAINTAINED):
                status = "maintained"
            elif any(k in text for k in POS_ACK):
                status = "acknowledged"
            if not status:
                continue
            positions.append({"stmt": text, "source": c["doc_id"], "since": "",
                              "status": status, "kind": c["kind"]})
    db.x("UPDATE personas SET card=?, updated_at=? WHERE id=?",
         (json.dumps({**person, "positions": positions}, ensure_ascii=False), now_iso(), persona_id))
    return positions


def scope_for(db, persona: dict, mode: str) -> Scope:
    kind = persona.get("kind", "AUTHOR")
    book = persona["doc_id"]
    if kind == "AUTHOR" and mode == "historical_author":
        docs = [book] + [c["doc_id"] for c in persona.get("corpora", [])
                         if c["kind"] in ("interview", "research", "other")]
    elif kind == "AUTHOR":
        docs = [book]
    else:
        docs = persona.get("evidence_radius", {}).get("doc_ids") or [book]
    radius = persona.get("evidence_radius", {})
    if radius.get("spans") == "all_in_doc":
        allowed = None
    else:
        allowed = {r["block_id"] for r in db.q(
            "SELECT block_id FROM persona_spans WHERE persona_id=?", (persona["id"],))}
    return Scope(doc_ids=docs, allowed_blocks=allowed, name=f"{persona['id']}:{mode}")
