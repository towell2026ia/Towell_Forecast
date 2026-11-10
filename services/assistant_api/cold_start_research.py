"""Public-source launch context. It never supplies or changes forecast quantities."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

import httpx

PUBLIC_TOPICS = {
    "TEXTILES_TOALLAS_MX": "toallas textiles para el hogar en México",
    "TEXTILES_TAPETES_MX": "tapetes textiles para el hogar en México",
    "TEXTILES_TRAPOS_MX": "trapos textiles para el hogar en México",
    "TEXTILES_BATAS_MX": "batas textiles para el hogar en México",
}
PUBLIC_CUTOFF = re.compile(r"20\d\d-(0[1-9]|1[0-2])\Z")
SAFE_RESPONSE_ID = re.compile(r"resp_[a-zA-Z0-9_-]{1,94}\Z")
SAFE_FAILURES = frozenset({
    "AUTHENTICATION", "QUOTA", "RATE_LIMIT", "MODEL_ACCESS", "REQUEST_REJECTED",
    "PROVIDER_ERROR", "TIMEOUT", "NETWORK", "INVALID_RESPONSE", "INCOMPLETE",
    "RESPONSE_FAILED", "EMPTY_RESPONSE", "NO_PUBLIC_CITATIONS",
})
_PRIVATE_MARKERS = re.compile(
    r"(?i)\b(?:sk-[\w-]{8,}|eyJ[\w-]+\.[\w-]+\.[\w-]+|resp_[\w-]+|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)


def public_citation(source):
    if not isinstance(source, dict):
        return None
    url = source.get("url")
    if (not isinstance(url, str) or not 1 <= len(url) <= 1000
            or re.search(r"[\s\\\x00-\x1f\x7f]", url)):
        return None
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or "." not in host
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))
                or _PRIVATE_MARKERS.search(unquote(parsed.path))):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]+", host):
            return None
    except ValueError:
        return None
    title = source.get("title")
    public_query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                    if not re.search(r"(?i)token|key|auth|session|password|secret|^utm_", key)
                    and not _PRIVATE_MARKERS.search(value)]
    return {
        "title": _PRIVATE_MARKERS.sub("[dato omitido]", title[:120]) if isinstance(title, str) and title.strip() else "Fuente",
        "url": urlunparse(("https", host, parsed.path, "", urlencode(public_query), "")),
    }


def public_research_result(result):
    if not isinstance(result, dict):
        return {"status": "UNAVAILABLE", "reason": "INVALID_RESPONSE"}
    usage = result.get("usage")
    counts = {k: n for k in ("input_tokens", "output_tokens", "total_tokens", "web_search_calls")
              if isinstance(usage, dict) and type(n := usage.get(k)) is int and 0 <= n <= 1_000_000}
    unavailable = {"status": "UNAVAILABLE", **({"usage": counts} if counts else {})}
    if result.get("status") == "UNAVAILABLE":
        reason = result.get("reason")
        return {**unavailable, "reason": reason if isinstance(reason, str) and reason in SAFE_FAILURES else "PROVIDER_ERROR"}
    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return {**unavailable, "reason": "EMPTY_RESPONSE"}
    sources, seen = [], set()
    candidates = result.get("sources")
    for item in candidates[:12] if isinstance(candidates, list) else []:
        source = public_citation(item)
        if source and source["url"] not in seen:
            sources.append(source)
            seen.add(source["url"])
    if not sources:
        return {**unavailable, "reason": "NO_PUBLIC_CITATIONS"}
    return {"status": "COMPLETED", "summary": _PRIVATE_MARKERS.sub("[dato omitido]", summary[:3000].strip()),
            "sources": sources, "usage": counts}


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
        """Compatibility for towel categories. Raw descriptor values remain local."""
        topic = "TEXTILES_TOALLAS_MX" if isinstance(category, str) and category.strip().casefold() in {"textil", "textiles", "toallas"} else None
        return self.start_public(topic)

    def start_public(self, topic: str | None, *, cutoff: str | None = None) -> dict:
        descriptor = PUBLIC_TOPICS.get(topic) if isinstance(topic, str) else None
        if not descriptor or (cutoff is not None and (not isinstance(cutoff, str) or not PUBLIC_CUTOFF.fullmatch(cutoff))):
            return {"status": "UNAVAILABLE", "reason": "REQUEST_REJECTED"}
        prompt = ("Investigate verifiable PUBLIC information with source citations. "
            "Describe category trends, seasonality or public commercial context only. "
            "Do not estimate quantities, recommend an uplift, or infer private sales. "
            "If no reliable source exists, say so. Respond in Spanish, under 350 words. "
            f"Public topic: {descriptor}.")
        if cutoff:
            prompt += f" Cite only dated public material available by the end of {cutoff}. Exclude sources with an unknown publication date."
        try:
            result = self._request("POST", "/responses", {"model": self.model,
                "reasoning": {"effort": "high"}, "background": True,
                "tools": [{"type": "web_search"}], "max_tool_calls": 3,
                "max_output_tokens": 3000, "input": prompt})
            response_id = result.get("id") if isinstance(result, dict) else None
            if not isinstance(response_id, str) or not SAFE_RESPONSE_ID.fullmatch(response_id):
                return {"status": "UNAVAILABLE", "reason": "INVALID_RESPONSE"}
            return {"status": "PENDING", "response_id": response_id, "model": self.model}
        except (httpx.HTTPError, ValueError) as exc:
            return {"status": "UNAVAILABLE", "reason": _failure_reason(exc)}

    def poll(self, response_id: str) -> dict:
        if not isinstance(response_id, str) or not SAFE_RESPONSE_ID.fullmatch(response_id):
            return {"status": "UNAVAILABLE", "reason": "INVALID_RESPONSE"}
        try:
            result = self._request("GET", "/responses/" + response_id)
        except (httpx.HTTPError, ValueError) as exc:
            return {"status": "UNAVAILABLE", "reason": _failure_reason(exc)}
        status = result.get("status") if isinstance(result, dict) else None
        if status in {"queued", "in_progress"}:
            return {"status": "PENDING", "response_id": response_id, "model": self.model}
        output = result.get("output") if isinstance(result, dict) else None
        items = output if isinstance(output, list) else []
        reported = result.get("usage") if isinstance(result, dict) else None
        usage = dict(reported) if isinstance(reported, dict) else {}
        if items:
            usage["web_search_calls"] = sum(isinstance(item, dict) and item.get("type") == "web_search_call"
                                            for item in items)
        if status != "completed":
            return public_research_result({"status": "UNAVAILABLE", "usage": usage,
                "reason": "INCOMPLETE" if status == "incomplete" else "RESPONSE_FAILED"})
        text, sources = [], []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            contents = item.get("content")
            for content in contents if isinstance(contents, list) else []:
                if not isinstance(content, dict) or content.get("type") != "output_text":
                    continue
                paragraph = content.get("text")
                if isinstance(paragraph, str):
                    text.append(paragraph[:3000])
                annotations = content.get("annotations")
                for annotation in annotations if isinstance(annotations, list) else []:
                    if isinstance(annotation, dict) and annotation.get("type") in (None, "url_citation"):
                        sources.append(annotation)
        return public_research_result({"status": "COMPLETED", "summary": "\n".join(text), "sources": sources,
            "usage": usage})
