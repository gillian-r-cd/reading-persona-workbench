"""Model providers: offline deterministic default, OpenAI-compatible BYOK."""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..util import canonical, embed as hash_embed, tokenize

NEG = ("不", "没有", "未", "无", "not ", "never", "no ")


class ProviderError(RuntimeError):
    pass


@dataclass
class OfflineProvider:
    name: str = "offline"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embed(t) for t in texts]

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        qt = set(tokenize(query))
        out = []
        for d in docs:
            dt = set(tokenize(d))
            out.append(len(qt & dt) / max(1, len(qt)))
        return out

    def nli(self, premise: str, hypothesis: str) -> float:
        """Cheap entailment proxy: token containment gated by negation polarity."""
        pt, ht = set(tokenize(premise)), set(tokenize(hypothesis))
        if not ht:
            return 0.0
        overlap = len(pt & ht) / len(ht)
        p_neg = any(n in premise for n in NEG)
        h_neg = any(n in hypothesis for n in NEG)
        if p_neg != h_neg:
            overlap *= 0.25
        # numbers must be present
        pnums = set(re.findall(r"\d+(?:\.\d+)?%?", premise))
        hnums = set(re.findall(r"\d+(?:\.\d+)?%?", hypothesis))
        if hnums and not hnums <= pnums:
            return 0.05
        return min(0.99, 0.35 + 0.64 * overlap)

    def chat(self, messages: list[dict], temperature: float = 0.0,
             json_schema: dict | None = None) -> str:
        raise ProviderError("offline provider 不支持生成；请在设置中配置 API Key")


class OpenAICompatProvider:
    """BYOK provider. Any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str,
                 embed_model: str = "", timeout: int = 60):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.embed_model = embed_model
        self.timeout = timeout
        self.name = f"openai:{model}"

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            raise ProviderError(f"HTTP {exc.code}: {body}") from exc
        except Exception as exc:
            raise ProviderError(f"请求失败: {exc}") from exc

    def chat(self, messages: list[dict], temperature: float = 0.0,
             json_schema: dict | None = None) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        if json_schema:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = self._post("/chat/completions", payload)
        except ProviderError as exc:
            if json_schema and "HTTP 400" in str(exc) and "response_format" in payload:
                payload.pop("response_format", None)
                data = self._post("/chat/completions", payload)
            else:
                raise
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderError(f"响应格式异常: {data}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embed_model:
            return [hash_embed(t) for t in texts]
        data = self._post("/embeddings", {"model": self.embed_model, "input": texts})
        return [d["embedding"] for d in data["data"]]

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        return OfflineProvider().rerank(query, docs)

    def nli(self, premise: str, hypothesis: str) -> float:
        return OfflineProvider().nli(premise, hypothesis)


def get_provider(settings: dict):
    mode = settings.get("mode", "offline")
    if mode == "byok" and settings.get("api_key"):
        return OpenAICompatProvider(
            settings.get("base_url", ""), settings["api_key"],
            settings.get("model", "gpt-4o-mini"),
            settings.get("embed_model", ""), int(settings.get("timeout", 60)))
    return OfflineProvider()
