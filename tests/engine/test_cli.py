"""run -> judge -> assemble end to end with fakes: resume skipping, cost cap, model pinning."""

import json
from pathlib import Path

import httpx
import pytest

from searchaudit.agent import Job, run_jobs
from searchaudit.cli import main_async, parse_args, pending_pairs, read_jsonl
from searchaudit.config import DEFAULT_AGENT_MODEL, AuditConfig
from searchaudit.cost import CostCap
from searchaudit.models import AuditResults, Prompt
from searchaudit.providers.keenable import KeenableSearchProvider

from .fakes import FakeOpenRouter, completion, context, tool_call, transport

YAML = """
slug: test
company: StackAI
category: Enterprise AI agent platform
model: openrouter/auto
judge_model: anthropic/claude-sonnet-5.5
router_model: typesafe/jev-router
cost_cap_usd: 40
providers:
  - {id: exa, label: Exa, short: Exa, domain: exa.ai, hex: "#c2410c", uses_today: true}
  - {id: keen, label: Keenable search, short: Search, domain: keenable.ai, hex: "#2563eb"}
  - {id: select, label: Keenable SELECT, short: SELECT, domain: keenable.ai, hex: "#dc2626"}
"""
PROMPTS = [
    {"id": i, "text": f"Question {i}?", "type": "lookup", "reference": f"Answer {i}", "as_of": "2026-10-01"}
    for i in (1, 2, 3)
]
JUDGE_REPLY = '{"grade": "c", "checks": [true, true, true, true], "failure_mode": null, "note": "Right."}'


def test_default_agent_model_is_gemini_pro_latest():
    assert DEFAULT_AGENT_MODEL == "~google/gemini-pro-latest"
    assert AuditConfig.model_validate({
        "slug": "t", "company": "c", "category": "c",
        "providers": [{"id": "none"}],
    }).model == "~google/gemini-pro-latest"


CHECKLIST_REPLY = json.dumps({"facts": [{"fact": "StackAI uses Exa", "level": "core"}], "traps": []})
VERDICT_REPLY = json.dumps({"facts": [{"id": 1, "status": "correct"}], "traps": [], "sources_support": True,
                            "failure_mode": None, "note": "Matches the checklist."})


def agent_script(body: dict) -> dict:
    name = (body.get("response_format") or {}).get("json_schema", {}).get("name")
    if name == "checklist":
        return completion(CHECKLIST_REPLY, cost=0.001)
    if name == "verdict":
        return completion(VERDICT_REPLY, cost=0.002)
    if body.get("response_format"):
        return completion(JUDGE_REPLY, cost=0.002)
    if body["model"] == "openrouter/auto":
        return completion("OK", model="anthropic/claude-sonnet-5.5", cost=0.0)
    return completion("Answer, see https://www.stackai.com/", cost=0.001)


@pytest.fixture
def audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    (tmp_path / "prompts.json").write_text(json.dumps(PROMPTS))
    path = tmp_path / "audit.yaml"
    path.write_text(YAML)
    return path


async def cli(argv: list[str], fake: FakeOpenRouter) -> int:
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        return await main_async(parse_args(argv), http=http)


def test_pending_pairs_is_query_major_and_skips_done():
    existing = [{"prompt_id": 1, "provider": "exa", "error": ""},
                {"prompt_id": 2, "provider": "keen", "error": "boom"}]
    pairs = pending_pairs([1, 2], ["exa", "keen"], existing, retry_errors=False)
    assert pairs == [(1, "keen"), (2, "exa")]
    assert (2, "keen") in pending_pairs([1, 2], ["exa", "keen"], existing, retry_errors=True)
    failed = [{"prompt_id": 1, "provider": "exa", "error": "", "tool_calls": [{"ok": False}]}]
    assert pending_pairs([1], ["exa"], failed, retry_errors=False) == []
    assert pending_pairs([1], ["exa"], failed, retry_errors=False, retry_tool_failures=True) == [(1, "exa")]
    unpaid = [{"prompt_id": 1, "provider": "exa", "error": "HTTP 402: Insufficient credits"}]
    assert pending_pairs([1], ["exa"], unpaid, retry_errors=False) == [(1, "exa")]
    capped = [{"prompt_id": 1, "provider": "keen", "budget_hit": True}]
    assert pending_pairs([1], ["keen"], capped, retry_errors=False) == []
    assert pending_pairs([1], ["keen"], capped, retry_errors=False, retry_budget_hits=True) == [(1, "keen")]


async def test_run_resumes_judges_and_assembles(audit: Path):
    fake = FakeOpenRouter(agent_script)
    assert await cli(["run", str(audit), "--limit", "2", "--providers", "exa,keen"], fake) == 0
    runs_path = audit.parent / "runs.jsonl"
    first = read_jsonl(runs_path)
    assert {(r["prompt_id"], r["provider"]) for r in first} == {(1, "exa"), (1, "keen"), (2, "exa"), (2, "keen")}
    pinned = json.loads((audit.parent / "pinned.json").read_text())
    assert pinned["agent_model"] == "anthropic/claude-sonnet-5.5"

    # resume: all prompts now, select is disabled (no key), done pairs are skipped
    before = len(fake.requests)
    assert await cli(["run", str(audit)], fake) == 0
    rows = read_jsonl(runs_path)
    assert len(rows) == 6
    assert {(r["prompt_id"], r["provider"]) for r in rows[4:]} == {(3, "exa"), (3, "keen")}
    assert all(r["model"] == "anthropic/claude-sonnet-5.5" for r in rows)
    assert all(r["archive"]["completions"] for r in rows)
    assert all(req["model"] != "openrouter/auto" for req in fake.requests[before:])  # pinned, not re-resolved

    assert await cli(["judge", str(audit)], fake) == 0
    judgments = read_jsonl(audit.parent / "judgments.jsonl")
    assert len(judgments) == 6
    assert all(j["response"]["choices"] for j in judgments)
    judged = len(fake.requests)
    assert await cli(["judge", str(audit)], fake) == 0
    assert len(fake.requests) == judged  # already judged runs are skipped

    assert await cli(["assemble", str(audit)], fake) == 0
    results = AuditResults.model_validate_json((audit.parent / "results.json").read_text())
    assert [p.id for p in results.providers] == ["exa", "keen"]
    assert results.providers[0].uses_today and results.providers[0].price_note
    assert len(results.runs) == len(results.judgments) == 6
    assert results.meta.agent_model == "anthropic/claude-sonnet-5.5"
    assert results.meta.budget == {"max_tool_calls": 15, "max_seconds": 180.0}
    assert results.meta.total_cost_usd == pytest.approx(6 * 0.001 + 6 * 0.002)  # agent runs + judging


async def test_cost_cap_stops_launching_runs():
    prompts = [Prompt(id=i, text="q", type="lookup", reference="r") for i in range(1, 6)]
    fake = FakeOpenRouter(lambda body: completion(None, [tool_call("search", {"query": "x"})], cost=1.0)
                          if len(body["messages"]) == 2 else completion("a", cost=1.0))
    saved = []

    async def save(result):
        saved.append(result.run)

    async with httpx.AsyncClient(transport=transport(fake)) as http:
        from searchaudit.agent import AgentSettings

        done = await run_jobs(
            [Job(p, KeenableSearchProvider()) for p in prompts], context(http), AgentSettings(model="m"),
            global_limit=1, per_provider=lambda _: 1, cap=CostCap(5.0), on_result=save,
        )
    # each run costs $2.004; the third run pushes spend past $5, so runs 4 and 5 never start
    assert done == 3 and len(saved) == 3


def test_assemble_rejects_degenerate_empty_providers(tmp_path: Path):
    """assemble() filters providers to those that actually ran; with no runs it would emit
    providers=[]. The schema now rejects that, so cmd_assemble raises before writing a
    results.json that would later 500 the web app (cli.py:339-343 -> cli.py:372)."""
    from pydantic import ValidationError

    from searchaudit.cli import assemble
    from searchaudit.config import Settings, load_config
    from searchaudit.providers import default_registry

    (tmp_path / "prompts.json").write_text(json.dumps([
        {"id": 1, "text": "q", "type": "lookup", "reference": "r", "as_of": "2026-10-01"}
    ]))
    (tmp_path / "runs.jsonl").write_text("")        # no runs -> ran = set() -> providers = []
    (tmp_path / "judgments.jsonl").write_text("")
    (tmp_path / "audit.yaml").write_text(
        "slug: t\ncompany: C\ncategory: X\nproviders:\n"
        "  - {id: exa, label: Exa, short: Exa, domain: exa.ai, hex: '#c2410c', uses_today: true}\n"
        "  - {id: keen, label: Keenable search, short: Search, domain: keenable.ai, hex: '#2563eb'}\n"
    )
    config = load_config(tmp_path / "audit.yaml")
    with pytest.raises(ValidationError):
        assemble(config, Settings(), default_registry())
    assert not (tmp_path / "results.json").exists()  # raised before cmd_assemble's write


async def test_credit_refusal_is_not_saved_and_stops_the_queue():
    prompts = [Prompt(id=i, text="q", type="lookup", reference="r") for i in range(1, 4)]
    fake = FakeOpenRouter(lambda body: {"error": {"message": "Insufficient credits"}})
    saved = []

    async def save(result):
        saved.append(result.run)

    async with httpx.AsyncClient(transport=transport(fake)) as http:
        from searchaudit.agent import AgentSettings

        done = await run_jobs(
            [Job(p, KeenableSearchProvider()) for p in prompts], context(http), AgentSettings(model="m"),
            global_limit=1, per_provider=lambda _: 1, cap=CostCap(50.0), on_result=save,
        )
    assert done == 0 and saved == [] and len(fake.requests) == 1
