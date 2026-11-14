"""Pluggable web search providers for Deep Research.

Default is `none`: the UI asks for URLs. Configure one of tavily / brave /
serper / searxng in settings to enable direct internet search.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) WB/1.0"


class SearchError(RuntimeError):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        raise SearchError(f"{type(exc).__name__}: {exc}") from exc


def _get(url: str, headers: dict, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        raise SearchError(f"{type(exc).__name__}: {exc}") from exc


class SearchProvider:
    name = "none"

    def search(self, queries: list[str], max_results: int = 8) -> list[dict]:
        return []


class NullSearch(SearchProvider):
    pass


class TavilySearch(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str, endpoint: str = ""):
        self.api_key = api_key
        self.endpoint = endpoint or "https://api.tavily.com/search"

    def search(self, queries: list[str], max_results: int = 8) -> list[dict]:
        out, seen = [], set()
        for q in queries:
            data = _post(self.endpoint, {"api_key": self.api_key, "query": q,
                                         "max_results": max_results, "search_depth": "basic"},
                         {})
            for r in data.get("results", []):
                url = r.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    out.append({"title": r.get("title", ""), "url": url,
                                "snippet": (r.get("content") or "")[:300]})
        return out[:max_results]


class BraveSearch(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str, endpoint: str = ""):
        self.api_key = api_key
        self.endpoint = endpoint or "https://api.search.brave.com/res/v1/web/search"

    def search(self, queries: list[str], max_results: int = 8) -> list[dict]:
        out, seen = [], set()
        for q in queries:
            url = self.endpoint + "?" + urllib.parse.urlencode({"q": q, "count": max_results})
            data = _get(url, {"Accept": "application/json",
                              "X-Subscription-Token": self.api_key})
            for r in (data.get("web") or {}).get("results", []):
                u = r.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    out.append({"title": r.get("title", ""), "url": u,
                                "snippet": (r.get("description") or "")[:300]})
        return out[:max_results]


class SerperSearch(SearchProvider):
    name = "serper"

    def __init__(self, api_key: str, endpoint: str = ""):
        self.api_key = api_key
        self.endpoint = endpoint or "https://google.serper.dev/search"

    def search(self, queries: list[str], max_results: int = 8) -> list[dict]:
        out, seen = [], set()
        for q in queries:
            data = _post(self.endpoint, {"q": q, "num": max_results},
                         {"X-API-KEY": self.api_key})
            for r in data.get("organic", []):
                u = r.get("link", "")
                if u and u not in seen:
                    seen.add(u)
                    out.append({"title": r.get("title", ""), "url": u,
                                "snippet": (r.get("snippet") or "")[:300]})
        return out[:max_results]


class SearxngSearch(SearchProvider):
    name = "searxng"

    def __init__(self, endpoint: str):
        self.endpoint = endpoint.rstrip("/")

    def search(self, queries: list[str], max_results: int = 8) -> list[dict]:
        out, seen = [], set()
        for q in queries:
            url = self.endpoint + "/search?" + urllib.parse.urlencode({"q": q, "format": "json"})
            data = _get(url, {})
            for r in data.get("results", []):
                u = r.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    out.append({"title": r.get("title", ""), "url": u,
                                "snippet": (r.get("content") or "")[:300]})
        return out[:max_results]


def get_search(settings: dict | None) -> SearchProvider:
    s = settings or {}
    mode = (s.get("mode") or "none").lower()
    key = s.get("api_key") or ""
    endpoint = s.get("endpoint") or ""
    if mode == "tavily" and key:
        return TavilySearch(key, endpoint)
    if mode == "brave" and key:
        return BraveSearch(key, endpoint)
    if mode == "serper" and key:
        return SerperSearch(key, endpoint)
    if mode == "searxng" and endpoint:
        return SearxngSearch(endpoint)
    return NullSearch()
