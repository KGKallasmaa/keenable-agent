"""SELECT (MCP tools discovered at run start) and routed (router + SQL writer) against a fake MCP client."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from mcp import types

from searchaudit.config import Settings
from searchaudit.models import Run
from searchaudit.providers.routed import RoutedProvider, extract_sql, parse_decision
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
            outcome = await session.call_tool("select", {"query": "SELECT 1 FROM WEB_SEARCH('a')"})
    assert names == ["select", "fetch"]
    assert session.function_tools()[0]["function"]["parameters"] == SELECT_TOOL.input_schema
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


def test_parse_decision_and_sql_extraction():
    assert parse_decision('{"arm": "select", "confidence": 0.8}') == ("select", 0.8)
    assert parse_decision("no idea") == ("keen", 0.0)
    assert parse_decision('{"arm": "search", "confidence": 7}') == ("keen", 1.0)
    assert extract_sql("```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("```sql\nSELECT 2") == "SELECT 2"  # unclosed fence
    assert extract_sql("SELECT 3") == "SELECT 3"


async def test_routed_records_route_and_picks_majority_arm():
    sql_requests: list[dict] = []

    def script(body):
        system = body["messages"][0]["content"]
        if system.startswith("You route"):
            user = body["messages"][1]["content"]
            assert user.startswith("TASK:\nFind agent platforms")
            arm = "select" if "list" in user.split("SEARCH QUERY:")[1] else "search"
            return completion(f'{{"arm": "{arm}", "confidence": 0.9}}', model="typesafe/jev-router", cost=0.0001)
        sql_requests.append(body)
        return completion("```sql\nSELECT url FROM WEB_SEARCH('a')\n```", cost=0.0002)

    mcp = FakeMCP()
    async with httpx.AsyncClient(transport=transport(FakeOpenRouter(script))) as http:
        ctx = context(http, KEYED)
        async with RoutedProvider(connector(mcp)).open(ctx) as session:
            session.start("Find agent platforms")
            assert [t["function"]["name"] for t in session.function_tools()] == ["search", "fetch"]
            a = await session.call_tool("search", {"query": "list all agent platforms"})
            b = await session.call_tool("search", {"query": "list SOC 2 vendors"})
            c = await session.call_tool("search", {"query": "StackAI CEO"})
            run = Run(prompt_id=1, provider="routed")
            session.finish(run)
    assert [x.tool for x in a.calls] == ["jev_route", "select_sql", "select"]
    assert a.calls[0].args["arm"] == "select" and a.calls[0].args["served_by"] == "typesafe/jev-router"
    assert mcp.calls[0] == ("select", {"query": "SELECT url FROM WEB_SEARCH('a')"})
    assert "EARLIER SQL:\nSELECT url FROM WEB_SEARCH('a')" in sql_requests[1]["messages"][1]["content"]
    assert [x.tool for x in c.calls] == ["jev_route", "search"]
    assert b.searches == c.searches == 1
    assert run.via == "select" and run.route_confidence == 0.9


async def test_routed_falls_back_to_search_when_sql_writing_fails():
    def script(body):
        if body["messages"][0]["content"].startswith("You route"):
            return completion('{"arm": "select", "confidence": 0.6}')
        return completion("")

    async with httpx.AsyncClient(transport=transport(FakeOpenRouter(script))) as http:
        ctx = context(http, KEYED)
        async with RoutedProvider(connector(FakeMCP())).open(ctx) as session:
            out = await session.call_tool("search", {"query": "list things"})
            run = Run(prompt_id=1, provider="routed")
            session.finish(run)
    assert [x.tool for x in out.calls] == ["jev_route", "select_sql", "search"]
    assert not out.calls[1].ok and run.via == "keen"


async def test_routed_tool_call_through_agent_loop_counts_router_cost_as_search():
    from searchaudit.agent import AgentSettings, run_agent
    from searchaudit.models import Prompt

    agent_turns = [completion(None, [tool_call("search", {"query": "StackAI CEO"})], cost=0.001),
                   completion("Answer https://getmacha.com/blog/stack-ai-complete-guide", cost=0.001)]

    def script(body):
        if body["messages"][0]["content"].startswith("You route"):
            return completion('{"arm": "search", "confidence": 0.7}', cost=0.0005)
        return agent_turns.pop(0)

    async with httpx.AsyncClient(transport=transport(FakeOpenRouter(script))) as http:
        ctx = context(http, KEYED)
        async with RoutedProvider(connector(FakeMCP())).open(ctx) as session:
            prompt = Prompt(id=1, text="Who is StackAI's CEO?", type="lookup", reference="r")
            run = (await run_agent(prompt, "routed", session, ctx.openrouter, AgentSettings(model="m"))).run
    assert run.via == "keen" and run.route_confidence == 0.7
    assert run.cost_search_usd == 0.0045 and run.cost_model_usd == 0.002
    assert run.searches == 1
