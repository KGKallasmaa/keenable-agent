import httpx
import pytest

from searchaudit.agent import AgentSettings, resolve_model, run_agent
from searchaudit.models import Prompt
from searchaudit.providers.server_tools import ServerToolProvider, account_server_tools

from .fakes import FakeOpenRouter, completion, context, fixture, transport

PROMPT = Prompt(id=2, text="Who acquired StackAI and when?", type="fresh", reference="Asana, 2026-05-28")


def test_parses_counts_and_prices_from_usage():
    usage = account_server_tools(fixture("openrouter_server_tools.json"), "exa")
    assert (usage.searches, usage.fetches) == (3, 1)
    assert usage.cost_usd == 3 * 0.007 + 0.001
    assert not usage.cost_estimated
    assert [c.tool for c in usage.calls] == ["search", "search", "search", "fetch"]
    assert len(usage.calls[0].urls) == 2  # citations deduplicated by url


def test_missing_counts_fall_back_to_estimate():
    response = fixture("openrouter_server_tools.json")
    del response["usage"]["server_tool_use_details"]
    usage = account_server_tools(response, "parallel")
    assert usage.searches == 1 and usage.fetches == 0
    assert usage.cost_usd == 0.005 and usage.cost_estimated


def test_native_engine_has_no_separate_list_price():
    usage = account_server_tools(fixture("openrouter_server_tools.json"), "native")
    assert usage.searches == 3 and usage.cost_usd == 0
    note = ServerToolProvider("grok", "native", "Grok native").price_note(None)
    assert "No separate list price" in note


def test_legacy_server_tool_use_key_is_read():
    response = fixture("openrouter_server_tools.json")
    response["usage"]["server_tool_use"] = response["usage"].pop("server_tool_use_details")
    assert account_server_tools(response, "exa").searches == 3


async def test_live_exa_response_uses_billed_split():
    """Trimmed real response (2026-10-07): usage.cost = upstream inference + $0.007 Exa search."""
    fake = FakeOpenRouter([fixture("openrouter_exa_live.json")])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        async with ServerToolProvider("exa", "exa", "Exa").open(ctx) as session:
            run = (await run_agent(PROMPT, "exa", session, ctx.openrouter, AgentSettings(model="m"))).run
    assert (run.searches, run.fetches) == (1, 0)
    assert run.cost_search_usd == pytest.approx(0.007)
    assert run.cost_model_usd == pytest.approx(0.000579)  # Run costs are rounded to 6 places
    assert not run.cost_estimated
    assert run.answer.startswith("Asana acquired StackAI") and len(run.citations) == 3
    assert run.citations[0].url.startswith("https://investors.asana.com/")


async def test_agent_splits_search_and_model_cost():
    fake = FakeOpenRouter([fixture("openrouter_server_tools.json")])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        async with ServerToolProvider("exa", "exa", "Exa").open(ctx) as session:
            result = await run_agent(PROMPT, "exa", session, ctx.openrouter, AgentSettings(model="m"))
    run = result.run
    tools = fake.requests[0]["tools"]
    assert tools[0] == {"type": "openrouter:web_search", "parameters": {"engine": "exa", "max_results": 5}}
    assert tools[1]["type"] == "openrouter:web_fetch"
    assert run.cost_search_usd == 0.022
    assert run.cost_model_usd == 0.018  # usage.cost 0.04 minus server-tool fees
    assert run.cost_usd == 0.04 and not run.cost_estimated
    assert [c.url for c in run.citations] == [
        "https://techcrunch.com/2026/05/28/asana-acquires-no-code-agent-builder-stack-ai/",
        "https://www.stackai.com/blog/series-a",
    ]
    assert run.citations[1].title == "StackAI raises $16M Series A"
    assert len(result.evidence) == 2
    assert run.tokens_in == 6120


async def test_model_cost_estimated_from_pricing_when_not_reported():
    from searchaudit.cost import ModelPricing

    fake = FakeOpenRouter([completion("No search needed.", cost=None)])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        async with ServerToolProvider("exa", "exa", "Exa").open(ctx) as session:
            settings = AgentSettings(model="m", pricing=ModelPricing(0.001, 0.01, True))
            run = (await run_agent(PROMPT, "exa", session, ctx.openrouter, settings)).run
    assert run.cost_model_usd == pytest.approx(100 * 0.001 + 20 * 0.01)
    assert run.cost_estimated


async def test_resolve_auto_pins_returned_model():
    fake = FakeOpenRouter([completion("OK", model="google/gemini-3-pro")])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        model, note = await resolve_model(ctx.openrouter, "openrouter/auto")
        explicit, _ = await resolve_model(ctx.openrouter, "openai/gpt-5.2")
    assert model == "google/gemini-3-pro" and "pinned" in note
    assert explicit == "openai/gpt-5.2"
    assert len(fake.requests) == 1


def test_byok_cost_adds_the_upstream_bill():
    from searchaudit.cost import ModelPricing, model_cost

    usage = {"cost": 0, "is_byok": True, "cost_details": {"upstream_inference_cost": 0.0014}}
    assert model_cost(usage, ModelPricing()).usd == 0.0014
    assert model_cost({"cost": 0.01, "cost_details": {"upstream_inference_cost": 0.003}}, ModelPricing()).usd == 0.01
