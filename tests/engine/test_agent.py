import httpx

from searchaudit.agent import AgentSettings, run_agent
from searchaudit.models import Prompt
from searchaudit.providers.keenable import KeenableSearchProvider

from .fakes import FakeOpenRouter, completion, context, tool_call, transport

PROMPT = Prompt(id=1, text="Who acquired StackAI?", type="lookup", reference="Asana, May 2026")


async def run_keen(script, max_tool_calls: int = 15, clock=None):
    fake = FakeOpenRouter(script)
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        settings = AgentSettings(model="m/pinned", max_tool_calls=max_tool_calls)
        async with KeenableSearchProvider().open(ctx) as session:
            kwargs = {"clock": clock} if clock else {}
            result = await run_agent(PROMPT, "keen", session, ctx.openrouter, settings, **kwargs)
    return result, fake


async def test_loop_runs_tools_then_answers():
    script = [
        completion(None, [tool_call("search", {"query": "StackAI acquisition"})], cost=0.002),
        completion(None, [tool_call("fetch", {"url": "https://docs.stackai.com/x"}, "call_2")], cost=0.003),
        completion("Asana acquired StackAI. Source: https://getmacha.com/blog/stack-ai-complete-guide.", cost=0.004),
    ]
    result, fake = await run_keen(script)
    run = result.run
    assert run.answer.startswith("Asana acquired StackAI")
    assert (run.searches, run.fetches) == (1, 1)
    assert [c.tool for c in run.tool_calls] == ["search", "fetch"]
    assert run.tool_calls[0].urls[0] == "https://getmacha.com/blog/stack-ai-complete-guide"
    assert run.tool_calls[0].result_chars > 0 and run.tool_calls[0].ok
    assert run.cost_model_usd == 0.009
    assert run.cost_search_usd == 0.008  # two Keenable requests at the default list price
    assert run.cost_usd == 0.017 and run.cost_estimated
    assert run.citations[0].url == "https://getmacha.com/blog/stack-ai-complete-guide"
    assert run.citations[0].title == "Features, Pricing, Asana Deal & Alternatives"
    assert not run.budget_hit and not run.error
    assert run.model == "anthropic/claude-sonnet-5.5"
    assert result.evidence
    # same model, temperature and tools for every request; tool results go back with their call id
    assert {r["model"] for r in fake.requests} == {"m/pinned"}
    assert fake.requests[1]["messages"][-1]["role"] == "tool"
    assert fake.requests[1]["messages"][-1]["tool_call_id"] == "call_1"


async def test_budget_exhaustion_forces_final_answer():
    def script(body):
        if body.get("tool_choice") == "none":
            return completion("Best effort answer.")
        return completion(None, [tool_call("search", {"query": "again"})])

    result, fake = await run_keen(script, max_tool_calls=3)
    run = result.run
    assert run.budget_hit
    assert run.searches == 3
    assert run.answer == "Best effort answer."
    final = fake.requests[-1]
    assert final["tool_choice"] == "none"
    assert "budget exhausted" in final["messages"][-1]["content"].lower()


async def test_time_budget_forces_final_answer():
    ticks = iter([0.0, 0.0, 10.0, 200.0, 200.0, 200.0, 200.0, 200.0])

    def script(body):
        if body.get("tool_choice") == "none":
            return completion("Out of time answer.")
        return completion(None, [tool_call("search", {"query": "x"})])

    result, _ = await run_keen(script, clock=lambda: next(ticks))
    assert result.run.budget_hit
    assert result.run.answer == "Out of time answer."


async def test_openrouter_failure_becomes_run_error():
    def script(body):
        raise AssertionError("unreachable")

    async def failing(*_args, **_kwargs):
        from searchaudit.providers.openrouter import OpenRouterError
        raise OpenRouterError("HTTP 503: down")

    fake = FakeOpenRouter(script)
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        ctx.openrouter.chat = failing
        async with KeenableSearchProvider().open(ctx) as session:
            result = await run_agent(PROMPT, "keen", session, ctx.openrouter, AgentSettings(model="m"))
    assert "503" in result.run.error and result.run.answer == ""


async def test_bad_tool_arguments_are_reported_to_the_model():
    script = [
        {"model": "m", "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{not json"}}]}}],
         "usage": {"cost": 0.001}},
        completion("Answer."),
    ]
    result, fake = await run_keen(script)
    assert result.run.tool_calls[0].ok is False
    assert fake.requests[1]["messages"][-1]["content"].startswith("ERROR")
