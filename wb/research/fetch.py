"""Fetching, snapshots, trust tiers and prompt-injection screening."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from lxml import html as LH

from ..util import canonical, sha256_bytes, sha256_text, stable_id, now_iso

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) WB/1.0"

INJECTION_PATTERNS = [
    r"ignore (all )?(previous|above|prior) instructions",
    r"disregard (the )?(system|previous)",
    r"you are now",
    r"system prompt",
    r"忽略(以上|之前|先前)(的)?(指令|提示)",
    r"忽略以上",
    r"你现在是",
    r"执行以下指令",
    r"请忽略系统",
    r"do not follow",
    r"new instructions",
]

ACADEMIC = ("arxiv.org", "nature.com", "science.org", "springer.com", "sciencedirect.com",
            "jstor.org", "wiley.com", "tandfonline.com", "ssrn.com", "nber.org", "acm.org",
            "ieee.org", "oup.com", "cambridge.org", "doi.org")
INSTITUTIONAL = (".gov", ".edu", ".ac.uk", "who.int", "worldbank.org", "imf.org", "oecd.org",
                 "un.org", "nber.org")
REVIEWED = ("ft.com", "economist.com", "hbr.org", "nytimes.com", "wsj.com", "bloomberg.com",
            "theatlantic.com", "newyorker.com")
NEWS = ("reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "cnn.com", "theguardian.com",
        "xinhuanet.com", "people.com.cn", "caixin.com")
BLOG = ("medium.com", "substack.com", "wordpress.com", "blogspot.com", "zhihu.com", "jianshu.com")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    error: str = ""


class HttpFetcher:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read(4_000_000)
                return FetchResult(url, resp.geturl(), resp.status,
                                   resp.headers.get("Content-Type", ""), body)
        except Exception as exc:
            return FetchResult(url, url, 0, "", b"", str(exc))


class FakeFetcher:
    """Deterministic fetcher for tests / offline demos."""

    def __init__(self, pages: dict[str, bytes | str], fail_all: bool = False):
        self.pages = {k: (v.encode("utf-8") if isinstance(v, str) else v) for k, v in pages.items()}
        self.fail_all = fail_all

    def fetch(self, url: str) -> FetchResult:
        if self.fail_all:
            return FetchResult(url, url, 0, "", b"", "offline")
        if url not in self.pages:
            return FetchResult(url, url, 404, "", b"", "not found")
        return FetchResult(url, url, 200, "text/html", self.pages[url])


def html_to_text(body: bytes) -> tuple[str, str]:
    text = body.decode("utf-8", "replace")
    try:
        root = LH.fromstring(text)
    except Exception:
        return "", canonical(text)
    for el in list(root.iter()):
        if isinstance(el.tag, str) and el.tag.lower() in ("script", "style", "nav", "footer",
                                                          "header", "aside", "form", "noscript"):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    title_el = root.find(".//title")
    title = canonical(title_el.text_content()) if title_el is not None else ""
    paras = []
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag.lower() in ("p", "li", "blockquote", "h1", "h2", "h3", "td"):
            txt = canonical(el.text_content() or "")
            if txt:
                paras.append(txt)
    return title, "\n".join(paras)


def detect_injection(text: str) -> list[str]:
    hits = []
    for pat in INJECTION_PATTERNS:
        if re.search(pat, text, re.I):
            hits.append(pat)
    return hits


def classify_tier(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if any(d in host for d in ACADEMIC):
        return "academic"
    if any(host.endswith(d) or d in host for d in INSTITUTIONAL):
        return "institutional"
    if any(d in host for d in REVIEWED):
        return "reviewed"
    if any(d in host for d in NEWS):
        return "news"
    if any(d in host for d in BLOG):
        return "blog"
    if host.endswith(".org") or host.endswith(".int"):
        return "institutional"
    return "unknown" if host else "unknown"


FACT_TIERS = {"academic", "institutional", "reviewed"}


def save_snapshot(cfg, body: bytes, ext: str = "html") -> tuple[str, str]:
    h = sha256_bytes(body)
    path = Path(cfg.vault) / "snapshots" / f"{h}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(body)
    return h, str(path)


def record_source(db, cfg, fr: FetchResult, check_change: bool = True) -> dict:
    title, text = html_to_text(fr.body)
    content_hash = sha256_text(text, 32)
    snap_hash, snap_path = save_snapshot(cfg, fr.body)
    tier = classify_tier(fr.final_url or fr.url)
    injections = detect_injection(text)
    prior = db.one("SELECT * FROM sources WHERE url=? AND snapshot_hash=?",
                   (fr.final_url or fr.url, snap_hash))
    if prior:
        return {**dict(prior), "text": text, "title": title, "injected": bool(injections),
                "patterns": injections, "changed": False}
    changed = False
    if check_change:
        old = db.one("SELECT * FROM sources WHERE url=? ORDER BY rowid DESC LIMIT 1",
                     (fr.final_url or fr.url,))
        changed = bool(old and old["content_hash"] != content_hash)
    sid = stable_id("src", fr.final_url or fr.url, snap_hash, n=14)
    row = {
        "id": sid, "url": fr.url, "final_url": fr.final_url or fr.url, "title": title,
        "fetched_at": now_iso(), "snapshot_hash": snap_hash, "snapshot_path": snap_path,
        "tier": tier, "content_hash": content_hash, "http_status": fr.status,
        "meta": __import__("json").dumps({"injected": bool(injections), "patterns": injections,
                                          "changed": changed}, ensure_ascii=False),
    }
    if not prior:
        db.insert("sources", row)
    return {**row, "text": text, "title": title, "injected": bool(injections),
            "patterns": injections, "changed": changed}
