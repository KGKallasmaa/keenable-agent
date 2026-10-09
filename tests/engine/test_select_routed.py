"""SELECT (MCP tools discovered at run start) and routed (router passes the query through) against a fake MCP client."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from mcp import types

from searchaudit.config import Settings
from searchaudit.models import Run
from searchaudit.providers.routed import (
    CONFIDENCE_FLOOR,
    ROUTE_QUESTION,
    RoutedProvider,
    pick_arm,
    route_state,
)
from searchaudit.providers.select import SelectProvider

from .fakes import FakeOpenRouter, completion, context, tool_call, transport

SELECT_TOOL = types.Tool(
    name="select", description="Run one read-only SQL query with WEB_SEARCH(...).",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}, "show_preview": {"type": "boolean"}},
                  "required": ["query"]},
)
HIDDEN = [types.Tool(name=n, description="x", input_schema={"type": "object", "properties": {}})
          for n in ("generate_html_report", "revoke_html_report")]


class FakeMCP:
    def __init__(self, error: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error = error

    async def list_tools(self):
        return SimpleNamespace(tools=[SELECT_TOOL, *HIDDEN])

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, args))
        text = "boom: bad SQL" if self.error else "| url | title |\n| https://a.test/x | A |"
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=self.error)


def connector(mcp: FakeMCP):
    @asynccontextmanager
    async def connect(_ctx):
        yield mcp
    return connect


KEYED = Settings(openrouter_api_key="k", keenable_api_key="secret")


async def test_select_exposes_discovered_tools_minus_report_tools_plus_fetch():
    mcp = FakeMCP()
    async with httpx.AsyncClient(transport=transport()) as http:
        ctx = context(http, KEYED)
        async with SelectProvider(connector(mcp)).open(ctx) as session:
            names = [t["function"]["name"] for t in session.function_tools()]
            sql = "SELECT url FROM WEB_SEARCH('CONSORT 2025 statement')"
            outcome = await session.call_tool("select", {"query": sql, "show_preview": False})
    tool = session.function_tools()[0]["function"]
    assert names == ["select", "fetch"]
    assert "WEB_SEARCH" in tool["description"]
    assert tool["parameters"]["required"] == ["query"]
    assert "WEB_SEARCH" in session.instructions()
    assert mcp.calls == [("select", {"query": sql, "show_preview": False})]
    assert outcome.calls[0].tool == "select" and outcome.calls[0].urls == ["https://a.test/x"]
    assert outcome.searches == 1 and outcome.calls[0].cost_usd == 0.004


async def test_select_tool_error_is_a_failed_call():
    async with httpx.AsyncClient(transport=transport()) as http:
        ctx = context(http, KEYED)
        async with SelectProvider(connector(FakeMCP(error=True))).open(ctx) as session:
            outcome = await session.call_tool("select", {"query": "bad"})
    assert not outcome.calls[0].ok and "bad SQL" in outcome.calls[0].error
    assert outcome.text.startswith("ERROR")


def test_select_disabled_without_key():
    ctx = SimpleNamespace(settings=Settings(openrouter_api_key="k"))
    assert "KEENABLE_API_KEY" in SelectProvider().unavailable_reason(ctx)
    assert "KEENABLE_API_KEY" in RoutedProvider().unavailable_reason(ctx)


def test_route_criteria_name_the_observed_win_conditions():
    criteria = ROUTE_QUESTION["tool"]["criteria"]
    assert "statute" in criteria["search"] and "register" in criteria["search"]
    assert "one web page is still search" in criteria["search"]
    assert "numbered order" in criteria["select"] and "full text" in criteria["select"]
    state = route_state("Did DA-26-553 grant the extension?")
    assert state["query"] == "Did DA-26-553 grant the extension?"
    assert {p["best"] for p in state["precedents"]} == {"search", "select"}
    assert len(state["precedents"]) >= 10


def test_weak_select_choice_falls_back_to_search():
    assert pick_arm("select", 0.9) == "select"
    assert pick_arm("select", CONFIDENCE_FLOOR) == "select"
    assert pick_arm("select", 0.1) == "keen"
    assert pick_arm("select", None) == "select"
    assert pick_arm("search", 0.99) == "keen"


async def test_routed_choice_exposes_only_the_picked_tool():
    mcp = FakeMCP()
    fake = FakeOpenRouter([])
    fake.decision = "select"
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http, KEYED)
        async with RoutedProvider(connector(mcp)).open(ctx) as session:
            await session.prepare("List every orbital launch in September")
            names = [t["function"]["name"] for t in session.function_tools()]
            assert "One SQL call" in session.instructions()
            assert "WEB_SEARCH" in session.instructions()
            sql = "SELECT url FROM WEB_SEARCH('eu agent startups', 'series a agent platforms')"
            selected = await session.call_tool("select", {"query": sql})
            run = Run(prompt_id=1, provider="routed")
            session.finish(run)
    assert names == ["select", "fetch"]
    assert mcp.calls == [("select", {"query": sql})]
    assert [c.tool for c in selected.calls] == ["select"]
    assert run.via == "select"
    assert run.route_confidence == 0.9
    assert run.tool_calls[0].tool == "jev_route"
    assert run.tool_calls[0].args["choice"] == "select"
    assert fake.requests == []


async def test_routed_hides_select_when_it_has_no_query_argument():
    class NoQueryMCP(FakeMCP):
        async def list_tools(self):
            tool = types.Tool(name="select", description="x",
                              input_schema={"type": "object", "properties": {"show_preview": {"type": "boolean"}}})
            return SimpleNamespace(tools=[tool])

    async with httpx.AsyncClient(transport=transport()) as http:
        ctx = context(http, KEYED)
        async with RoutedProvider(connector(NoQueryMCP())).open(ctx) as session:
            names = [t["function"]["name"] for t in session.function_tools()]
            await session.prepare("list things")
            assert session.instructions() == "Use one or two short queries, then fetch the page you cite."
            assert "WEB_SEARCH" not in session.instructions()
            out = await session.call_tool("search", {"query": "list things"})
            run = Run(prompt_id=1, provider="routed")
            session.finish(run)
    assert names == ["search", "fetch"]
    assert [c.tool for c in out.calls] == ["search"]
    assert run.via == "keen"


async def test_routed_lookup_through_the_agent_uses_search_only():
    from searchaudit.agent import AgentSettings, run_agent
    from searchaudit.models import Prompt

    agent_turns = [completion(None, [tool_call("search", {"query": "StackAI CEO"})], cost=0.001),
                   completion("Answer https://getmacha.com/blog/stack-ai-complete-guide", cost=0.001)]

    async with httpx.AsyncClient(transport=transport(FakeOpenRouter(lambda body: agent_turns.pop(0)))) as http:
        ctx = context(http, KEYED)
        async with RoutedProvider(connector(FakeMCP())).open(ctx) as session:
            prompt = Prompt(id=1, text="Who is StackAI's CEO?", type="lookup", reference="r")
            run = (await run_agent(prompt, "routed", session, ctx.openrouter, AgentSettings(model="m"))).run
    assert run.via == "keen"
    assert run.route_confidence == 0.9
    assert [c.tool for c in run.tool_calls] == ["jev_route", "search"]
    assert run.cost_search_usd == 0.004 and run.cost_model_usd == 0.00201
    assert run.searches == 1


def test_routed_lookup_copies_the_chosen_arms_answer():
    from searchaudit.models import ToolCall
    from searchaudit.providers.routed import copied_answer

    source = {
        "prompt_id": 3, "provider": "select", "answer": "the table", "latency_ms": 90000,
        "cost_usd": 0.03, "cost_model_usd": 0.02, "cost_search_usd": 0.01,
        "tool_calls": [{"tool": "select", "args": {"query": "sql"}}],
        "evidence": ["https://example.com"],
        "archive": {"completions": [{"id": "kept"}], "tools": []},
    }
    call = ToolCall(tool="jev_route", args={"choice": "select", "confidence": 0.8}, cost_usd=0.00002)
    row = copied_answer(source, "select", 0.8, call, jev_ms=40, decision={"answers": {"tool": {"choice": "select"}}})
    assert row["provider"] == "routed" and row["answer"] == "the table"
    assert row["via"] == "select" and row["route_confidence"] == 0.8
    assert row["latency_ms"] == 90040
    assert row["cost_usd"] == 0.03002
    assert row["tool_calls"][0]["tool"] == "jev_route"
    assert row["evidence"] == ["https://example.com"]
    assert row["archive"]["completions"] == [{"id": "kept"}]
    assert row["archive"]["decision"]["answers"]["tool"]["choice"] == "select"


def test_research_skill_teaches_a_query_instead_of_the_sentence() -> None:
    from searchaudit.providers.research_skill import RESEARCH_SKILL

    assert "six queries" in RESEARCH_SKILL
    assert "SEM_EXTRACT" in RESEARCH_SKILL
    assert "Do not paste the user's sentence" in RESEARCH_SKILL


def test_select_without_a_query_tool_has_no_sql_guidance() -> None:
    from searchaudit.providers.select import SelectSession

    session = SelectSession(client=None, tools=[], keenable=type("K", (), {"usd": 0.004})())
    assert session.instructions() == ""
