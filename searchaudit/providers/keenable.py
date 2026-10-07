"""Keenable search: REST search + fetch exposed to the agent as function tools.

Shape learned from the public endpoint probe (tests/engine/fixtures/keenable_*_public.json):
POST /v1/search {query, max_results} -> {query, mode, results: [{title, url, snippet, published_at?}]}
GET  /v1/fetch?url=...               -> {url, title, content, published_at?}
"""

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
# Exa via OpenRouter returns ~2-4k chars of highlights per result; match it so neither side reads less.
SNIPPET_CHARS = 3000
PAGE_CHARS = 8000
EVIDENCE_CHARS = 1500

SEARCH_TOOL = function_tool(
    "search", "Search the web. Returns titles, URLs, publish dates and snippets.",
    {"query": {"type": "string", "description": "The search query."}}, ["query"],
)
FETCH_TOOL = function_tool(
    "fetch", "Fetch the readable text of a web page by URL.",
    {"url": {"type": "string", "description": "Absolute http(s) URL."}}, ["url"],
)


class KeenableAPI:
    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self.http = http
        self.authenticated = bool(api_key)
        self.headers = {"X-API-Key": api_key} if api_key else {"X-Keenable-Title": TITLE}
        self.suffix = "" if api_key else "/public"

    async def search(self, query: str, max_results: int = MAX_RESULTS) -> list[dict]:
        response = await self.http.post(
            f"{API}/search{self.suffix}", json={"query": query, "max_results": max_results, "snippet_max_length": SNIPPET_CHARS},
            headers=self.headers, timeout=30.0,
        )
        response.raise_for_status()
        return parse_search(response.json())

    async def fetch(self, url: str) -> dict:
        response = await self.http.get(
            f"{API}/fetch{self.suffix}", params={"url": url}, headers=self.headers, timeout=45.0
        )
        response.raise_for_status()
        return parse_fetch(response.json())


def parse_search(body: dict) -> list[dict]:
    hits = []
    for item in body.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            hits.append({
                "url": item["url"],
                "title": item.get("title") or "",
                "snippet": item.get("snippet") or item.get("description") or "",
                "published_at": item.get("published_at") or "",
            })
    return hits


def parse_fetch(body: dict) -> dict:
    if not body.get("content") and not body.get("title"):
        raise ValueError(body.get("error") or "empty page")
    return {
        "url": body.get("url") or "",
        "title": body.get("title") or "",
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

    async def search(self, query: str, tool: str = "search") -> ToolOutcome:
        hits: list[dict] = []

        async def work() -> tuple[str, list[str], float]:
            hits.extend(await self.api.search(query))
            return format_hits(hits), [h["url"] for h in hits], self.usd

        text, call = await timed_call(tool, {"query": query}, work())
        return ToolOutcome(
            text=text, calls=[call], searches=1, cost_estimated=True,
            evidence=[text[:EVIDENCE_CHARS]] if call.ok else [],
            citations=[Citation(url=h["url"], title=h["title"]) for h in hits],
        )

    async def fetch(self, url: str) -> ToolOutcome:
        page: dict = {}

        async def work() -> tuple[str, list[str], float]:
            page.update(await self.api.fetch(url))
            text = f"{page['title']}\nURL: {page['url'] or url}\n\n{page['content'][:PAGE_CHARS]}"
            return text, [page["url"] or url], self.usd

        text, call = await timed_call("fetch", {"url": url}, work())
        return ToolOutcome(
            text=text, calls=[call], fetches=1, cost_estimated=True,
            evidence=[text[:EVIDENCE_CHARS]] if call.ok else [],
            citations=[Citation(url=page["url"] or url, title=page["title"])] if page else [],
        )


class KeenableSearchSession(ProviderSession):
    def __init__(self, tools: KeenableTools) -> None:
        self.tools = tools

    def function_tools(self) -> list[dict]:
        return [SEARCH_TOOL, FETCH_TOOL]

    async def call_tool(self, name: str, args: dict) -> ToolOutcome:
        if name == "search":
            return await self.tools.search(str(args.get("query", "")))
        if name == "fetch":
            return await self.tools.fetch(str(args.get("url", "")))
        raise KeyError(f"unknown tool: {name}")


class KeenablePriced(Provider):
    """Base for the Keenable providers: priced per request at the configured list price."""

    def price_note(self, settings: Settings) -> str:
        return f"~${settings.keenable_usd_per_request} per request (Keenable list price)"

    @staticmethod
    def tools(ctx: ProviderContext) -> KeenableTools:
        api = KeenableAPI(ctx.http, ctx.settings.keenable_api_key)
        return KeenableTools(api, ctx.settings.keenable_usd_per_request)


class KeenableSearchProvider(KeenablePriced):
    id = "keen"
    via = "Keenable REST /v1/search + /v1/fetch as function tools"

    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        return static_session(KeenableSearchSession(self.tools(ctx)))
