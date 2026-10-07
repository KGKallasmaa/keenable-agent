"""Keenable routed: one `search` tool; per call a router model (Jev) picks SELECT or Keenable search.

SELECT takes SQL, not a natural-language query, so when the router picks it the pinned agent model
writes one SQL query from the agent's search query and the SELECT tool's own description.
"""

import json
import logging
import re
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from searchaudit.models import Run, ToolCall
from searchaudit.providers.base import ProviderContext, ProviderSession, ToolOutcome
from searchaudit.providers.keenable import FETCH_TOOL, SEARCH_TOOL, KeenablePriced, KeenableTools
from searchaudit.providers.openrouter import OpenRouterClient, OpenRouterError
from searchaudit.providers.select import (
    Connect,
    SelectSession,
    connect_select,
    open_select_session,
    select_unavailable,
)

log = logging.getLogger(__name__)

ROUTER_SYSTEM = (
    "You route web-search calls for a research agent between two Keenable tools.\n"
    "- select: structured retrieval over Keenable's index; best for wide/list queries that need many "
    "entities, filters or a table (e.g. 'all EU AI-agent startups that raised a Series A in 2026').\n"
    "- search: ranked web search; best for single facts, fresh news, a specific page or company detail.\n"
    "You see the user's task and the one search query the agent wants to run now; route the query "
    "with the task in mind.\n"
    'Reply with JSON only: {"arm": "select" | "search", "confidence": <0..1>}'
)
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)(?:```|$)", re.DOTALL | re.IGNORECASE)
HISTORY_TURNS = 3  # earlier SQL + outcomes the writer sees, so it can fix errors and reuse result-set ids
HISTORY_CHARS = 600
SQL_SYSTEM = (
    "You translate a web-research request into exactly one query for the tool documented below. "
    "Earlier queries in this session and their (trimmed) results may be shown; fix their errors and "
    "reuse their result-set ids when useful. Write one query that answers the whole request: several "
    "WEB_SEARCH phrasings in one call, every needed field extracted with SEM_EXTRACT, and the url column. "
    "Reply with the SQL only, no prose.\n\nTOOL DOCUMENTATION:\n{doc}"
)


class Router:
    """Calls the router model; falls back to the agent model once if the router model fails."""

    def __init__(self, client: OpenRouterClient, model: str, fallback: str) -> None:
        self.client = client
        self.model = model
        self.fallback = fallback

    async def decide(self, task: str, query: str) -> tuple[str, float, ToolCall]:
        started = time.perf_counter()
        try:
            response = await self._chat(f"TASK:\n{task}\n\nSEARCH QUERY:\n{query}")
        except OpenRouterError as exc:
            call = ToolCall(tool="jev_route", args={"query": query, "model": self.model}, ok=False,
                            error=str(exc)[:300], latency_ms=_ms(started))
            return "keen", 0.0, call
        content = response["choices"][0]["message"].get("content") or ""
        arm, confidence = parse_decision(content)
        call = ToolCall(
            tool="jev_route",
            args={"query": query, "model": self.model, "served_by": response.get("model") or "", "arm": arm,
                  "confidence": confidence, "raw": content[:200]},
            latency_ms=_ms(started), result_chars=len(content),
            cost_usd=float((response.get("usage") or {}).get("cost") or 0.0),
        )
        return arm, confidence, call

    async def _chat(self, user: str) -> dict:
        # Generous max_tokens: the router may dispatch to a reasoning model that spends tokens thinking.
        payload = {
            "model": self.model, "temperature": 0, "max_tokens": 600,
            "messages": [{"role": "system", "content": ROUTER_SYSTEM}, {"role": "user", "content": user}],
        }
        try:
            return await self.client.chat(payload, timeout=30.0)
        except OpenRouterError as exc:
            if self.model == self.fallback:
                raise
            log.warning("router model %s failed (%s); falling back to %s", self.model, exc, self.fallback)
            self.model = self.fallback
            return await self.client.chat({**payload, "model": self.model}, timeout=30.0)


class SqlWriter:
    """Turns a search query into one SELECT SQL query with the pinned agent model."""

    def __init__(self, client: OpenRouterClient, model: str) -> None:
        self.client = client
        self.model = model
        self.history: list[tuple[str, str]] = []  # (sql, trimmed result or error)

    def remember(self, sql: str, result: str) -> None:
        self.history = [*self.history, (sql, result[:HISTORY_CHARS])][-HISTORY_TURNS:]

    async def write(self, query: str, tool_doc: str) -> tuple[str, ToolCall]:
        started = time.perf_counter()
        earlier = "".join(f"EARLIER SQL:\n{sql}\nRESULT:\n{result}\n\n" for sql, result in self.history)
        payload = {
            "model": self.model, "temperature": 0, "max_tokens": 3000,
            "messages": [{"role": "system", "content": SQL_SYSTEM.format(doc=tool_doc)},
                         {"role": "user", "content": f"{earlier}REQUEST:\n{query}"}],
        }
        try:
            response = await self.client.chat(payload, timeout=60.0)
        except OpenRouterError as exc:
            call = ToolCall(tool="select_sql", args={"query": query}, ok=False, error=str(exc)[:300],
                            latency_ms=_ms(started))
            return "", call
        sql = extract_sql(response["choices"][0]["message"].get("content") or "")
        call = ToolCall(tool="select_sql", args={"query": query, "sql": sql}, ok=bool(sql),
                        error="" if sql else "model returned no SQL", latency_ms=_ms(started),
                        result_chars=len(sql),
                        cost_usd=float((response.get("usage") or {}).get("cost") or 0.0))
        return sql, call


def extract_sql(content: str) -> str:
    match = SQL_FENCE_RE.search(content)
    return (match.group(1) if match else content).strip()


def parse_decision(content: str) -> tuple[str, float]:
    """-> ("select" | "keen", confidence). Unparseable output routes to Keenable search."""
    match = JSON_RE.search(content)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    arm = "select" if str(data.get("arm", "")).lower() == "select" else "keen"
    try:
        confidence = min(max(float(data.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return arm, confidence


class RoutedSession(ProviderSession):
    def __init__(self, router: Router, writer: SqlWriter, select: SelectSession, keenable: KeenableTools) -> None:
        self.router = router
        self.writer = writer
        self.select = select
        self.keenable = keenable
        self.decisions: list[tuple[str, float]] = []
        self.task = ""

    def start(self, task: str) -> None:
        self.task = task

    def function_tools(self) -> list[dict]:
        return [SEARCH_TOOL, FETCH_TOOL]

    async def call_tool(self, name: str, args: dict) -> ToolOutcome:
        if name == "fetch":
            return await self.keenable.fetch(str(args.get("url", "")))
        if name != "search":
            raise KeyError(f"unknown tool: {name}")
        query = str(args.get("query", ""))
        arm, confidence, route_call = await self.router.decide(self.task, query)
        prep = [route_call]
        target = self.select.query_tool() if arm == "select" else None
        sql = ""
        if target:
            sql, sql_call = await self.writer.write(query, self.select.tools[target[0]].description or "")
            prep.append(sql_call)
        if sql:
            outcome = await self.select.call_select(target[0], {target[1]: sql})
            self.writer.remember(sql, outcome.text)
        else:  # router said search, SELECT has no usable tool, or SQL writing failed
            arm = "keen"
            outcome = await self.keenable.search(query)
        self.decisions.append((arm, confidence))
        outcome.calls[:0] = prep
        return outcome

    def finish(self, run: Run) -> None:
        if not self.decisions:
            return
        run.via = Counter(arm for arm, _ in self.decisions).most_common(1)[0][0]
        run.route_confidence = round(sum(c for _, c in self.decisions) / len(self.decisions), 3)


class RoutedProvider(KeenablePriced):
    id = "routed"
    via = ("Keenable search + SELECT behind one `search` tool; a router model picks the arm per call "
           "and the agent model writes the SQL when SELECT is picked")

    def __init__(self, connect: Connect = connect_select) -> None:
        self.connect = connect

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        return select_unavailable(ctx)

    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        return self._open(ctx)

    @asynccontextmanager
    async def _open(self, ctx: ProviderContext) -> AsyncIterator[ProviderSession]:
        async with open_select_session(ctx, self.connect) as select:
            router = Router(ctx.openrouter, ctx.router_model, ctx.agent_model)
            writer = SqlWriter(ctx.openrouter, ctx.agent_model)
            yield RoutedSession(router, writer, select, select.keenable)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
