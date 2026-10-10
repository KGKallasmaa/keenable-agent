"""The provider plugin interface.

A provider gives the agent its search (and, for parity, fetch) for one run, in one of two ways:
- server tools that OpenRouter executes inside the completion (`server_tools` + `account_response`), or
- function tools that our agent loop executes (`function_tools` + `call_tool`).
Each run opens a fresh session with `Provider.open`.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

import httpx

from searchaudit.config import Settings
from searchaudit.models import Citation, Run, ToolCall
from searchaudit.providers.openrouter import OpenRouterClient


@dataclass
class ProviderContext:
    settings: Settings
    http: httpx.AsyncClient
    openrouter: OpenRouterClient
    agent_model: str
    router_model: str
    max_tool_calls: int = 15


@dataclass
class ToolOutcome:
    """What one function-tool call returned: text for the model plus the calls to record."""

    text: str
    calls: list[ToolCall]
    searches: int = 0
    fetches: int = 0
    cost_estimated: bool = False
    evidence: list[str] = field(default_factory=list)  # trimmed result text for the judge
    citations: list[Citation] = field(default_factory=list)  # titles for urls the tools surfaced
    raw: dict | str | None = None  # provider body, kept so a later read does not repeat the call


@dataclass
class ServerUsage:
    """Server-tool activity inside one OpenRouter completion."""

    searches: int = 0
    fetches: int = 0
    cost_usd: float = 0.0
    cost_estimated: bool = False
    calls: list[ToolCall] = field(default_factory=list)


class ProviderSession:
    """One run's tools. Override what the provider needs; the defaults mean 'none'."""

    def server_tools(self) -> list[dict]:
        return []

    def function_tools(self) -> list[dict]:
        return []

    async def call_tool(self, name: str, args: dict) -> ToolOutcome:
        raise KeyError(f"unknown tool: {name}")

    def account_response(self, response: dict) -> ServerUsage:
        return ServerUsage()

    def instructions(self) -> str:
        """Extra system-prompt text for this run ('' = none). The no-search baseline uses it."""
        return ""

    def bind_query_time(self, when: str) -> None:
        """Harness pin for a date cut. Search sessions send it. Other sessions ignore it."""

    def start(self, task: str) -> None:
        """Called once with the user's prompt before the first model turn."""

    async def prepare(self, task: str) -> None:
        """Async setup before the first model turn. The router classifies the query here."""

    def finish(self, run: Run) -> None:
        """Last chance to annotate the run (routed sets `via` and `route_confidence`)."""

    def archive_extra(self) -> dict:
        """Paid calls made outside the chat loop, such as the Jev decision."""
        return {}


class Provider(ABC):
    id: str
    via: str = ""

    def price_note(self, settings: Settings) -> str:
        return ""

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        """None when the provider can run, else a human-readable reason it is disabled."""
        return None

    @abstractmethod
    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        """Async context manager yielding a ProviderSession for one run."""

    async def describe(self, ctx: ProviderContext) -> dict:
        """Tool listing for `probe`."""
        async with self.open(ctx) as session:
            return {"server_tools": session.server_tools(), "function_tools": session.function_tools()}



def function_tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


async def timed_call(tool: str, args: dict, work: Awaitable[tuple[str, list[str], float]]) -> tuple[str, ToolCall]:
    """Await `work` -> (text, urls, cost) and wrap it in a ToolCall; errors become ok=False."""
    started = time.perf_counter()
    try:
        text, urls, cost = await work
    except Exception as exc:  # noqa: BLE001 - docstring promise: every error becomes ok=False, not a run-ending crash
        error = f"{type(exc).__name__}: {exc}"[:300]
        call = ToolCall(tool=tool, args=args, ok=False, error=error, latency_ms=_ms(started))
        return f"ERROR: {error}", call
    call = ToolCall(tool=tool, args=args, latency_ms=_ms(started), result_chars=len(text),
                    cost_usd=cost, urls=urls)
    return text, call


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@asynccontextmanager
async def static_session(session: ProviderSession) -> AsyncIterator[ProviderSession]:
    yield session
