"""Exa and Parallel through OpenRouter server tools: OpenRouter runs search and fetch inside the completion."""

from contextlib import AbstractAsyncContextManager

from searchaudit.config import Settings
from searchaudit.cost import FETCH_USD, SEARCH_USD
from searchaudit.models import ToolCall
from searchaudit.providers.base import (
    Provider,
    ProviderContext,
    ProviderSession,
    ServerUsage,
    static_session,
)
from searchaudit.providers.openrouter import url_citations

MAX_RESULTS = 5


class ServerToolSession(ProviderSession):
    def __init__(self, engine: str, max_tool_calls: int) -> None:
        self.engine = engine
        self.max_tool_calls = max_tool_calls

    def server_tools(self) -> list[dict]:
        return [
            {"type": "openrouter:web_search",
             "parameters": {"engine": self.engine, "max_results": MAX_RESULTS}},
            {"type": "openrouter:web_fetch",
             "parameters": {"engine": self.engine, "max_uses": self.max_tool_calls}},
        ]

    def account_response(self, response: dict) -> ServerUsage:
        return account_server_tools(response, self.engine)


def account_server_tools(response: dict, engine: str) -> ServerUsage:
    """Count searches/fetches from usage.server_tool_use_details and price them at OpenRouter list prices.

    When the counts are missing, assume one search if the answer carries url citations and flag
    the cost as estimated. (The agent replaces the list-price total with the billed split when the
    response reports usage.cost_details.upstream_inference_cost.)
    """
    usage = response.get("usage") or {}
    # OpenRouter reports `server_tool_use_details` (seen 2026-10-07); older docs say `server_tool_use`.
    tool_use = usage.get("server_tool_use_details") or usage.get("server_tool_use") or {}
    message = (response.get("choices") or [{}])[0].get("message") or {}
    cites = url_citations(message)
    searches_raw = tool_use.get("web_search_requests")
    executed = tool_use.get("tool_calls_executed")
    estimated = searches_raw is None
    searches = int(searches_raw) if searches_raw is not None else (1 if cites else 0)
    fetches = max(int(executed) - searches, 0) if executed is not None else 0
    search_price, fetch_price = SEARCH_USD[engine], FETCH_USD[engine]
    urls = [c["url"] for c in cites]
    calls = [
        ToolCall(tool="search", args={"engine": engine, "server_side": True}, cost_usd=search_price,
                 urls=urls if i == 0 else [])
        for i in range(searches)
    ] + [
        ToolCall(tool="fetch", args={"engine": engine, "server_side": True}, cost_usd=fetch_price)
        for _ in range(fetches)
    ]
    return ServerUsage(
        searches=searches,
        fetches=fetches,
        cost_usd=searches * search_price + fetches * fetch_price,
        cost_estimated=estimated,
        calls=calls,
    )


class ServerToolProvider(Provider):
    def __init__(self, provider_id: str, engine: str, name: str) -> None:
        self.id = provider_id
        self.engine = engine
        self.via = f"OpenRouter openrouter:web_search + openrouter:web_fetch, engine={engine}"
        self.name = name

    def price_note(self, settings: Settings) -> str:
        if self.engine == "native":
            return ("Passed through inside the OpenRouter completion charge. "
                    "No separate list price for native search.")
        return (f"${SEARCH_USD[self.engine]} per search, ${FETCH_USD[self.engine]} per fetched page "
                f"(OpenRouter list price for {self.name})")

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        return None if ctx.settings.openrouter_api_key else "OPENROUTER_API_KEY is not set"

    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        return static_session(ServerToolSession(self.engine, ctx.max_tool_calls))
