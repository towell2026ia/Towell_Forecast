"""Public-source launch context. It never supplies or changes forecast quantities."""
from __future__ import annotations

from urllib.parse import urlparse

import httpx


def _failure_reason(exc: Exception) -> str:
    """Return only a stable category; never echo provider text or credentials."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            code = (exc.response.json().get("error") or {}).get("code")
        except (ValueError, TypeError, AttributeError):
            code = None
        if status == 401 or code == "invalid_api_key":
            return "AUTHENTICATION"
        if code == "insufficient_quota":
            return "QUOTA"
        if status == 429:
            return "RATE_LIMIT"
        if status in {403, 404} or code == "model_not_found":
            return "MODEL_ACCESS"
        return "REQUEST_REJECTED" if status < 500 else "PROVIDER_ERROR"
    if isinstance(exc, httpx.TimeoutException):
        return "TIMEOUT"
    if isinstance(exc, httpx.HTTPError):
        return "NETWORK"
    return "INVALID_RESPONSE"


class ColdStartResearch:
    def __init__(self, api_key: str, *, model: str = "gpt-5.5", timeout: float = 15):
        if not api_key.startswith("sk-"):
            raise ValueError("openai_key_missing")
        self._key, self.model, self.timeout = api_key, model, timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(method, "https://api.openai.com/v1" + path,
                headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"},
                json=payload)
            response.raise_for_status()
            return response.json()

    def start(self, *, description: str, category: str, chain_name: str) -> dict:
        # Never send private sales, prices, customer records, product IDs or tokens to web search.
        prompt = ("Investigate PUBLIC information only about this retail product launch. "
            "Identify verifiable product attributes, seasonality or launch risks with source citations. "
            "Do not estimate quantities, recommend an uplift, or infer private sales. "
            "If no reliable source exists, say so. Respond in Spanish, under 350 words. "
            f"Chain: {chain_name[:80]}; category: {category[:80]}; product: {description[:160]}.")
        try:
            result = self._request("POST", "/responses", {"model": self.model,
                "reasoning": {"effort": "high"}, "background": True,
                "tools": [{"type": "web_search"}], "max_tool_calls": 3,
                "max_output_tokens": 3000, "input": prompt})
            response_id = result.get("id")
            if not isinstance(response_id, str) or not response_id.startswith("resp_"):
                return {"status": "UNAVAILABLE", "reason": "INVALID_RESPONSE"}
            return {"status": "PENDING", "response_id": response_id, "model": self.model}
        except (httpx.HTTPError, ValueError) as exc:
            return {"status": "UNAVAILABLE", "reason": _failure_reason(exc)}

    def poll(self, response_id: str) -> dict:
        if not response_id.startswith("resp_") or len(response_id) > 100:
            return {"status": "UNAVAILABLE", "reason": "INVALID_RESPONSE"}
        try:
            result = self._request("GET", "/responses/" + response_id)
        except (httpx.HTTPError, ValueError) as exc:
            return {"status": "UNAVAILABLE", "reason": _failure_reason(exc)}
        status = result.get("status")
        if status in {"queued", "in_progress"}:
            return {"status": "PENDING", "response_id": response_id, "model": self.model}
        if status != "completed":
            return {"status": "UNAVAILABLE", "reason": "INCOMPLETE" if status == "incomplete" else "RESPONSE_FAILED"}
        text, sources = [], []
        for item in result.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") != "output_text":
                    continue
                text.append(str(content.get("text") or "")[:3000])
                for annotation in content.get("annotations") or []:
                    url = annotation.get("url")
                    parsed = urlparse(url) if isinstance(url, str) else None
                    if parsed and parsed.scheme == "https" and parsed.hostname and not parsed.username \
                            and not parsed.password and len(url) <= 1000:
                        sources.append({"title": str(annotation.get("title") or "Fuente")[:120], "url": url})
        summary = "\n".join(text).strip()
        if not summary:
            return {"status": "UNAVAILABLE", "reason": "EMPTY_RESPONSE"}
        return {"status": "COMPLETED", "summary": summary, "sources": sources[:12], "model": self.model}
