"""Keenable search: REST search + fetch exposed to the agent as function tools.

Shape learned from the public endpoint probe (tests/engine/fixtures/keenable_*_public.json):
POST /v1/search {query, max_results} -> {query, mode, results: [{title, url, snippet, published_at?}]}
GET  /v1/fetch?url=...               -> {url, title, content, published_at?}
"""

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import httpx

from searchaudit.config import Settings
from searchaudit.models import Citation
from searchaudit.providers.base import (
    Provider,
    ProviderContext,
    ProviderSession,
    ToolOutcome,
    function_tool,
    static_session,
    timed_call,
)

API = "https://api.keenable.ai/v1"
TITLE = "searchaudit"
MAX_RESULTS = 5
# 429 and 5xx are the caller's to ride out. 403/404/422 on fetch means the URL is not in the index.
RETRY_STATUS = {429, 500, 502, 503, 504}
INDEX_MISS = {403, 404, 422}
ATTEMPTS = 3
# Exa via OpenRouter returns ~2-4k chars of highlights per result; match it so neither side reads less.
SNIPPET_CHARS = 3000
# Some index entries carry a whole PDF in the title (612k and 1.1M chars, spacex-jobs prompt 31,
# 2026-10-09). Uncapped, one search overflowed Grok's 500k-token context.
TITLE_CHARS = 300
PAGE_CHARS = 8000
EVIDENCE_CHARS = 1500

# The model never sees query_time. Given the field, Grok filled it on 93% of searches with a past
# date, which hid the orders the questions were about (spacex-jobs, 2026-10-09). Only the harness
# pins a date, and only on a prompt that sets query_time.
SEARCH_TOOL = function_tool(
    "search",
    "Search the web. Returns titles, URLs, publish dates and snippets.",
    {"query": {"type": "string", "description": "The search query."}},
    ["query"],
)
FETCH_TOOL = function_tool(
    "fetch", "Fetch the readable text of a web page by URL.",
    {"url": {"type": "string", "description": "Absolute http(s) URL."}}, ["url"],
)


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    raw = response.headers.get("Retry-After") if response is not None else None
    if raw:
        try:
            return min(float(raw), 30.0)
        except ValueError:
            pass
    return min(30.0, 2**attempt + random.uniform(0, 0.5))


class KeenableAPI:
    def __init__(
        self, http: httpx.AsyncClient, api_key: str, *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.http = http
        self.authenticated = bool(api_key)
        self.headers = {"X-API-Key": api_key} if api_key else {"X-Keenable-Title": TITLE}
        self.suffix = "" if api_key else "/public"
        self._sleep = sleep

    async def _send(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Retry 429 and 5xx. Transport errors get the same attempts. The last response is returned."""
        last: httpx.Response | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = await self.http.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == ATTEMPTS - 1:
                    raise
                response = None
            else:
                if response.status_code not in RETRY_STATUS:
                    return response
                last = response
            if attempt < ATTEMPTS - 1:
                await self._sleep(_retry_delay(response, attempt))
        assert last is not None
        return last

    async def search(self, query: str, max_results: int = MAX_RESULTS, query_time: str | None = None) -> list[dict]:
        return parse_search(await self.search_body(query, max_results, query_time))

    async def search_body(self, query: str, max_results: int = MAX_RESULTS, query_time: str | None = None) -> dict:
        body: dict = {"query": query, "max_results": max_results, "snippet_max_length": SNIPPET_CHARS}
        if query_time:
            body["query_time"] = query_time
        response = await self._send(
            "POST", f"{API}/search{self.suffix}",
            json=body,
            headers=self.headers, timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    async def fetch(self, url: str) -> dict:
        return parse_fetch(await self.fetch_body(url))

    async def fetch_body(self, url: str) -> dict:
        """Indexed copy first. A miss is fetched live from the source, which is what the API requires."""
        path = f"{API}/fetch{self.suffix}"
        response = await self._send("GET", path, params={"url": url}, headers=self.headers, timeout=45.0)
        if response.status_code in INDEX_MISS:
            response = await self._send(
                "GET", path, params={"url": url, "live": "true"}, headers=self.headers, timeout=60.0,
            )
        response.raise_for_status()
        return response.json()


def _response_body(response: httpx.Response) -> dict | str:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return response.text
    return body if isinstance(body, dict) else response.text


def clean_title(title: str) -> str:
    """First line, whitespace collapsed, capped. The full document stays reachable through fetch and SELECT."""
    first = next((line for line in title.splitlines() if line.strip()), "")
    return " ".join(first.split())[:TITLE_CHARS]


def parse_search(body: dict) -> list[dict]:
    hits = []
    for item in body.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            hits.append({
                "url": item["url"],
                "title": clean_title(item.get("title") or ""),
                "snippet": item.get("snippet") or item.get("description") or "",
                "published_at": item.get("published_at") or "",
            })
    return hits


def parse_fetch(body: dict) -> dict:
    if not body.get("content") and not body.get("title"):
        raise ValueError(body.get("error") or "empty page")
    return {
        "url": body.get("url") or "",
        "title": clean_title(body.get("title") or ""),
        "content": body.get("content") or "",
        "published_at": str(body.get("published_at") or ""),
    }


def format_hits(hits: list[dict]) -> str:
    if not hits:
        return "No results."
    lines = []
    for i, hit in enumerate(hits, 1):
        date = f"\n   Published: {hit['published_at']}" if hit["published_at"] else ""
        lines.append(f"{i}. {hit['title']}\n   URL: {hit['url']}{date}\n   {hit['snippet'][:SNIPPET_CHARS]}")
    return "\n".join(lines)


class KeenableTools:
    """Keenable search and fetch as tool handlers, shared by keen, select and routed."""

    def __init__(self, api: KeenableAPI, usd_per_request: float) -> None:
        self.api = api
        self.usd = usd_per_request

    async def search(self, query: str, tool: str = "search", query_time: str | None = None) -> ToolOutcome:
        hits: list[dict] = []
        held: dict = {}
        when = query_time or None
        sent = {"query": query, **({"query_time": when} if when else {})}

        async def work() -> tuple[str, list[str], float]:
            try:
                held["body"] = await self.api.search_body(query, query_time=when)
            except httpx.HTTPStatusError as exc:
                held["body"] = _response_body(exc.response)
                raise
            hits.extend(parse_search(held["body"]))
            return format_hits(hits), [h["url"] for h in hits], self.usd

        text, call = await timed_call(tool, sent, work())
        return ToolOutcome(
            text=text, calls=[call], searches=1, cost_estimated=True,
            evidence=[text[:EVIDENCE_CHARS]] if call.ok else [],
            citations=[Citation(url=h["url"], title=h["title"]) for h in hits],
            raw=held.get("body"),
        )

    async def fetch(self, url: str) -> ToolOutcome:
        page: dict = {}
        held: dict = {}

        async def work() -> tuple[str, list[str], float]:
            try:
                held["body"] = await self.api.fetch_body(url)
            except httpx.HTTPStatusError as exc:
                held["body"] = _response_body(exc.response)
                raise
            page.update(parse_fetch(held["body"]))
            text = f"{page['title']}\nURL: {page['url'] or url}\n\n{page['content'][:PAGE_CHARS]}"
            return text, [page["url"] or url], self.usd

        text, call = await timed_call("fetch", {"url": url}, work())
        return ToolOutcome(
            text=text, calls=[call], fetches=1, cost_estimated=True,
            evidence=[text[:EVIDENCE_CHARS]] if call.ok else [],
            citations=[Citation(url=page["url"] or url, title=page["title"])] if page else [],
            raw=held.get("body"),
        )


class KeenableSearchSession(ProviderSession):
    def __init__(self, tools: KeenableTools) -> None:
        self.tools = tools
        self.forced_query_time = ""

    def bind_query_time(self, when: str) -> None:
        self.forced_query_time = (when or "").strip()

    def function_tools(self) -> list[dict]:
        return [SEARCH_TOOL, FETCH_TOOL]

    async def call_tool(self, name: str, args: dict) -> ToolOutcome:
        if name == "search":
            return await self.tools.search(str(args.get("query", "")), query_time=self.forced_query_time or None)
        if name == "fetch":
            return await self.tools.fetch(str(args.get("url", "")))
        raise KeyError(f"unknown tool: {name}")


class KeenablePriced(Provider):
    """Base for the Keenable providers: priced per request at the configured list price."""

    def price_note(self, settings: Settings) -> str:
        return f"${settings.keenable_usd_per_request} per request (Keenable list price)"

    @staticmethod
    def tools(ctx: ProviderContext) -> KeenableTools:
        api = KeenableAPI(ctx.http, ctx.settings.keenable_api_key)
        return KeenableTools(api, ctx.settings.keenable_usd_per_request)


class KeenableSearchProvider(KeenablePriced):
    id = "keen"
    via = "Keenable REST /v1/search + /v1/fetch as function tools"

    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        return static_session(KeenableSearchSession(self.tools(ctx)))
