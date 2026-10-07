"""Keenable SELECT: tools discovered from the MCP server at run start, plus Keenable fetch for parity."""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError

from searchaudit.providers.base import (
    ProviderContext,
    ProviderSession,
    ToolOutcome,
    timed_call,
)
from searchaudit.providers.keenable import EVIDENCE_CHARS, FETCH_TOOL, KeenablePriced, KeenableTools

SELECT_URL = "https://select.keenable.ai/mcp"
# Server tools that publish or revoke shareable reports: side effects, not retrieval. Never shown to the agent.
HIDDEN_TOOLS = frozenset({"generate_html_report", "revoke_html_report"})
RESULT_CHARS = 12000
QUERY_ARG_NAMES = ("query", "prompt", "question", "q", "request", "task", "sql", "wql")
URL_RE = re.compile(r"https?://[^\s)\]\"'<>,]+")

Connect = Callable[[ProviderContext], AbstractAsyncContextManager[Any]]


@asynccontextmanager
async def connect_select(ctx: ProviderContext) -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {ctx.settings.keenable_api_key}"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        streamable_http_client(SELECT_URL, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        yield session


def mcp_tool_schema(tool: Any) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or tool.title or tool.name,
            "parameters": tool.input_schema or {"type": "object", "properties": {}},
        },
    }


def query_arg(tool: Any) -> str | None:
    """The argument of a SELECT tool that takes free text, so routed can pass a query to it."""
    schema = tool.input_schema or {}
    props = schema.get("properties") or {}
    strings = [name for name, spec in props.items() if (spec or {}).get("type") == "string"]
    for name in QUERY_ARG_NAMES:
        if name in strings:
            return name
    required = [name for name in schema.get("required") or [] if name in strings]
    return (required or strings or [None])[0]


def result_text(result: Any) -> str:
    parts = [getattr(block, "text", "") for block in result.content or []]
    text = "\n".join(p for p in parts if p)
    if not text and result.structured_content is not None:
        text = str(result.structured_content)
    return text


class SelectSession(ProviderSession):
    def __init__(self, client: Any, tools: list[Any], keenable: KeenableTools) -> None:
        self.client = client
        self.tools = {t.name: t for t in tools}
        self.keenable = keenable
        self.usd = keenable.usd

    def function_tools(self) -> list[dict]:
        schemas = [mcp_tool_schema(t) for t in self.tools.values()]
        return schemas if "fetch" in self.tools else [*schemas, FETCH_TOOL]

    def query_tool(self) -> tuple[str, str] | None:
        """(tool name, query argument) for the first SELECT tool that accepts free text."""
        for tool in self.tools.values():
            arg = query_arg(tool)
            if arg:
                return tool.name, arg
        return None

    async def call_tool(self, name: str, args: dict) -> ToolOutcome:
        if name in self.tools:
            return await self.call_select(name, args)
        if name == "fetch":
            return await self.keenable.fetch(str(args.get("url", "")))
        raise KeyError(f"unknown tool: {name}")

    async def call_select(self, name: str, args: dict, label: str = "select") -> ToolOutcome:
        async def work() -> tuple[str, list[str], float]:
            try:
                result = await self.client.call_tool(name, args)
            except MCPError as exc:
                raise ValueError(f"MCP error: {exc}") from exc
            text = result_text(result)
            if result.is_error:
                raise ValueError(text[:300] or "tool returned an error")
            return text[:RESULT_CHARS], list(dict.fromkeys(URL_RE.findall(text))), self.usd

        text, call = await timed_call(label, {"tool": name, **args}, work())
        return ToolOutcome(text=text, calls=[call], searches=1, cost_estimated=True,
                           evidence=[text[:EVIDENCE_CHARS]] if call.ok else [])


@asynccontextmanager
async def open_select_session(ctx: ProviderContext, connect: Connect) -> AsyncIterator[SelectSession]:
    async with connect(ctx) as client:
        tools = [t for t in (await client.list_tools()).tools if t.name not in HIDDEN_TOOLS]
        yield SelectSession(client, tools, KeenablePriced.tools(ctx))


def select_unavailable(ctx: ProviderContext) -> str | None:
    if not ctx.settings.keenable_api_key:
        return "KEENABLE_API_KEY is not set; Keenable SELECT needs a key (Bearer auth on its MCP server)"
    return None


class SelectProvider(KeenablePriced):
    id = "select"
    via = f"Keenable SELECT MCP ({SELECT_URL}) tools via list_tools + Keenable fetch"

    def __init__(self, connect: Connect = connect_select) -> None:
        self.connect = connect

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        return select_unavailable(ctx)

    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        return open_select_session(ctx, self.connect)

    async def describe(self, ctx: ProviderContext) -> dict:
        async with self.connect(ctx) as client:
            tools = (await client.list_tools()).tools
        return {
            "mcp_url": SELECT_URL,
            "hidden_from_agent": sorted(HIDDEN_TOOLS),
            "tools": [t.model_dump(mode="json", exclude_none=True) for t in tools],
        }
