"""A new provider is one Provider subclass plus one register call; the agent loop needs nothing else."""

import httpx
import pytest

from searchaudit.agent import AgentSettings, run_agent
from searchaudit.models import Prompt
from searchaudit.providers import Registry, default_registry
from searchaudit.providers.base import (
    Provider,
    ProviderSession,
    ToolOutcome,
    function_tool,
    static_session,
    timed_call,
)

from .fakes import FakeOpenRouter, completion, context, tool_call, transport


class EchoSession(ProviderSession):
    def function_tools(self) -> list[dict]:
        return [function_tool("search", "Echo search.", {"query": {"type": "string"}}, ["query"])]

    async def call_tool(self, name: str, args: dict) -> ToolOutcome:
        async def work():
            return f"echo: {args['query']} https://echo.test/a", ["https://echo.test/a"], 0.01

        text, call = await timed_call("search", args, work())
        return ToolOutcome(text=text, calls=[call], searches=1)


class EchoProvider(Provider):
    id = "keen"  # reuses a contract id; a brand-new id would also need models.ProviderId
    via = "echo"

    def open(self, ctx):
        return static_session(EchoSession())


def test_default_registry_has_all_five_plus_baseline():
    assert default_registry().ids() == ["exa", "parallel", "keen", "select", "routed", "none"]


def test_duplicate_and_unknown_ids_are_rejected():
    registry = Registry()
    registry.register(EchoProvider())
    with pytest.raises(ValueError):
        registry.register(EchoProvider())
    with pytest.raises(KeyError):
        registry.get("nope")


async def test_fake_provider_runs_through_the_agent():
    registry = Registry()
    registry.register(EchoProvider())
    fake = FakeOpenRouter([
        completion(None, [tool_call("search", {"query": "q"})]),
        completion("Done, see https://echo.test/a"),
    ])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        async with registry.get("keen").open(ctx) as session:
            prompt = Prompt(id=1, text="t", type="lookup", reference="r")
            run = (await run_agent(prompt, "keen", session, ctx.openrouter, AgentSettings(model="m"))).run
    assert run.searches == 1 and run.cost_search_usd == 0.01
    assert [c.url for c in run.citations] == ["https://echo.test/a"]
    assert fake.requests[0]["tools"][0]["function"]["name"] == "search"
