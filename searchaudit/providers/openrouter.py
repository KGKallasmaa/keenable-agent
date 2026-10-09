"""Thin async client for OpenRouter chat completions, shared by the agent, judge and router."""

import asyncio
import json
import random
from collections.abc import Awaitable, Callable

import httpx

BASE_URL = "https://openrouter.ai/api/v1"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
RETRY_STATUS = {429, 500, 502, 503, 504}


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, *, body: dict | str | None = None) -> None:
        super().__init__(message)
        self.body = body


class OpenRouterClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        *,
        retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        title: str = "searchaudit",
    ) -> None:
        self.http = http
        self.api_key = api_key
        self.retries = retries
        self._sleep = sleep
        self._headers = {"Authorization": f"Bearer {api_key}", "X-Title": title}
        self._models: list[dict] | None = None

    async def chat(self, payload: dict, timeout: float = 120.0) -> dict:
        """POST /chat/completions with up to `retries` jittered retries on 429/5xx and network errors."""
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not set")
        last = ""
        last_body: dict | str | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self.http.post(
                    f"{BASE_URL}/chat/completions", json=payload, headers=self._headers, timeout=timeout
                )
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUS:
                    return _parse(response)
                last_body = _response_body(response)
                last = f"HTTP {response.status_code}: {response.text[:200]}"
            if attempt < self.retries:
                await self._sleep(2**attempt + random.uniform(0, 1))
        raise OpenRouterError(last, body=last_body)

    async def decide(self, model: str, state: dict, questions: dict, timeout: float = 30.0) -> dict:
        """POST /api/alpha/decisions. Jev returns a typed answer, not chat text."""
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not set")
        last = ""
        last_body: dict | str | None = None
        payload = {"model": model, "state": state, "questions": questions}
        for attempt in range(self.retries + 1):
            try:
                response = await self.http.post(DECISIONS_URL, json=payload, headers=self._headers, timeout=timeout)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUS:
                    return _parse_decision(response)
                last_body = _response_body(response)
                last = f"HTTP {response.status_code}: {response.text[:200]}"
            if attempt < self.retries:
                await self._sleep(2**attempt + random.uniform(0, 1))
        raise OpenRouterError(last, body=last_body)

    async def models(self) -> list[dict]:
        """The public model catalog (free, unauthenticated GET). Cached per client."""
        if self._models is None:
            response = await self.http.get(f"{BASE_URL}/models", timeout=30.0)
            response.raise_for_status()
            self._models = response.json().get("data") or []
        return self._models

    async def model_exists(self, model_id: str) -> bool:
        return any(m.get("id") == model_id for m in await self.models())


def _response_body(response: httpx.Response) -> dict | str:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return response.text
    return body if isinstance(body, dict) else response.text


def _parse_decision(response: httpx.Response) -> dict:
    body = _response_body(response)
    if not isinstance(body, dict):
        raise OpenRouterError(f"HTTP {response.status_code}: non-JSON body", body=body)
    if response.status_code >= 400 or "answers" not in body:
        error = body.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error or response.text[:200])
        raise OpenRouterError(f"HTTP {response.status_code}: {message}", body=body)
    return body


def _parse(response: httpx.Response) -> dict:
    body = _response_body(response)
    if not isinstance(body, dict):
        raise OpenRouterError(f"HTTP {response.status_code}: non-JSON body", body=body)
    if response.status_code >= 400 or "error" in body:
        error = body.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        raw = ((error.get("metadata") or {}).get("raw") or "") if isinstance(error, dict) else ""
        detail = f" ({str(raw)[:300]})" if raw else ""
        raise OpenRouterError(f"HTTP {response.status_code}: {message or response.text[:200]}{detail}", body=body)
    if not body.get("choices"):
        raise OpenRouterError("response has no choices", body=body)
    return body


def url_citations(message: dict) -> list[dict]:
    """`message.annotations[*].url_citation` entries ({url, title, content}), deduplicated by url."""
    seen: dict[str, dict] = {}
    for item in message.get("annotations") or []:
        cite = item.get("url_citation") if isinstance(item, dict) else None
        if isinstance(cite, dict) and cite.get("url") and cite["url"] not in seen:
            seen[cite["url"]] = {
                "url": cite["url"], "title": cite.get("title") or "", "content": cite.get("content") or ""
            }
    return list(seen.values())
