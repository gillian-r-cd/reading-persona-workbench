"""LLM query expansion (optional): 3 complementary retrieval queries."""
from __future__ import annotations

import json
import re

_CACHE: dict[tuple, list[str]] = {}

PROMPT = """你是检索查询改写器。把用户问题改写成 3 条互补的检索查询：
- 保留原意，补充同义表达、相关概念、可能出现的术语；
- 不要回答问题，不要引入新事实；
- 每条不超过 30 字。
只输出 JSON：{"queries": ["...", "...", "..."]}"""


def _parse(raw: str) -> list[str]:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s[:-3]
    try:
        data = json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return []
        try:
            data = json.loads(s[i:j + 1])
        except Exception:
            return []
    out = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(out, list):
        return []
    return [str(x).strip() for x in out if str(x).strip()][:3]


def expand(provider, question: str) -> list[str]:
    key = (getattr(provider, "base_url", ""), getattr(provider, "model", ""), question)
    if key in _CACHE:
        return _CACHE[key]
    try:
        raw = provider.chat([{"role": "user", "content": PROMPT + "\n\n问题：" + question}],
                            temperature=0.2, json_schema={"type": "json_object"})
        qs = _parse(raw)
    except Exception:
        qs = []
    qs = [q for q in qs if q and q != question]
    _CACHE[key] = qs
    return qs
