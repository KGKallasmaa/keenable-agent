"""The no-search baseline: same agent loop with zero tools, config defaults, judge note."""

import httpx

from searchaudit.agent import SYSTEM_PROMPT, AgentSettings, run_agent
from searchaudit.config import AuditConfig
from searchaudit.judge import JudgeBundle, judge_messages
from searchaudit.models import Prompt, Run
from searchaudit.providers import default_registry
from searchaudit.providers.none import NO_TOOLS_NOTE

from .fakes import FakeOpenRouter, completion, context, transport

PROMPT = Prompt(id=1, text="Who is Tesla's CTO?", type="lookup", reference="r")


async def test_none_runs_one_turn_without_tools_and_says_so_in_the_system_prompt():
    fake = FakeOpenRouter([completion("I cannot know this without search.", cost=0.0004)])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        provider = default_registry().get("none")
        assert provider.unavailable_reason(ctx) is None
        async with provider.open(ctx) as session:
            run = (await run_agent(PROMPT, "none", session, ctx.openrouter, AgentSettings(model="m"))).run
    body = fake.requests[0]
    assert "tools" not in body and len(fake.requests) == 1
    assert body["messages"][0]["content"] == f"{SYSTEM_PROMPT}\n\n{NO_TOOLS_NOTE}"
    assert run.provider == "none" and run.searches == 0 and run.cost_search_usd == 0
    assert run.cost_model_usd == 0.0004 and not run.error


def test_other_providers_keep_the_plain_system_prompt():
    session = default_registry().get("exa")
    assert session.id == "exa"
    from searchaudit.providers.base import ProviderSession
    assert ProviderSession().instructions() == ""


def test_config_fills_baseline_display_defaults():
    cfg = AuditConfig.model_validate({
        "slug": "s", "company": "C", "category": "x",
        "providers": [{"id": "exa", "label": "Exa", "short": "Exa", "domain": "exa.ai", "hex": "#000"},
                      {"id": "none"}],
    })
    none = cfg.providers[1]
    assert (none.label, none.short, none.domain, none.hex) == ("No search (model memory)", "No search", "",
                                                               "#9ca3af")
    assert default_registry().get("none").price_note(None) == "model tokens only"


def test_judge_is_told_the_baseline_has_no_sources():
    bundle = JudgeBundle("j")
    base = judge_messages(bundle, PROMPT, Run(prompt_id=1, provider="none", answer="a"), [])[1]["content"]
    exa = judge_messages(bundle, PROMPT, Run(prompt_id=1, provider="exa", answer="a"), [])[1]["content"]
    assert base.startswith("NOTE: this answer comes from the no-search baseline")
    assert exa.startswith("PROMPT:")
