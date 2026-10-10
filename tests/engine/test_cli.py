"""run -> judge -> assemble end to end with fakes: resume skipping, cost cap, model pinning."""

import json
from pathlib import Path

import httpx
import pytest

from searchaudit.agent import Job, run_jobs
from searchaudit.cli import main_async, parse_args, pending_pairs, read_jsonl, replay_routed
from searchaudit.config import DEFAULT_AGENT_MODEL, AuditConfig, load_config, load_prompts
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


# ---- routed replay: cost-cap gating and accounting -------------------------------------------------

ROUTE_YAML = """
slug: test
company: StackAI
category: Enterprise AI agent platform
model: anthropic/claude-sonnet-5.5
judge_model: anthropic/claude-sonnet-5.5
router_model: typesafe/jev-router
cost_cap_usd: 100
providers:
  - {id: keen, label: Keenable search, short: Search, domain: keenable.ai, hex: "#2563eb"}
  - {id: select, label: Keenable SELECT, short: SELECT, domain: keenable.ai, hex: "#dc2626"}
  - {id: routed, label: Routed, short: Routed, domain: keenable.ai, hex: "#16a34a"}
"""
ROUTE_PROMPTS = [
    {"id": i, "text": f"Question {i}?", "type": "lookup", "reference": f"Answer {i}", "as_of": "2026-10-01"}
    for i in range(1, 4)
]
CLASSIFY_COST = 0.00001  # the fake /decisions transport reports this cost per call


def _row(pid: int, provider: str, *, answer: str = "a", cost: float = 0.04) -> dict:
    return {"prompt_id": pid, "provider": provider, "answer": answer, "error": "",
            "latency_ms": 1000, "cost_usd": cost, "cost_model_usd": 0.01,
            "cost_search_usd": round(cost - 0.01, 6), "tool_calls": [],
            "model": "anthropic/claude-sonnet-5.5"}


def _seed(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for r in rows:
            handle.write(json.dumps(r) + "\n")


@pytest.fixture
def route_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AuditConfig:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    (tmp_path / "prompts.json").write_text(json.dumps(ROUTE_PROMPTS))
    (tmp_path / "audit.yaml").write_text(ROUTE_YAML)
    return load_config(tmp_path / "audit.yaml")


async def test_replay_routed_charges_classify_cost_to_cap(route_config: AuditConfig):
    """Each classified routed row is charged to the cap, not just persisted."""
    config = route_config
    rows = [_row(i, "keen") for i in (1, 2, 3)] + [_row(i, "select") for i in (1, 2, 3)]
    _seed(config.runs_path, rows)
    fake = FakeOpenRouter([])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        cap = CostCap(40.0)
        by_id = {p.id: p for p in load_prompts(config)}
        done = await replay_routed(config, ctx, by_id, cap)
    keen_cost = 0.04
    routed_cost = round(keen_cost + CLASSIFY_COST, 6)  # copied_answer adds call.cost_usd
    assert done == 3
    assert cap.spent_usd == pytest.approx(3 * routed_cost)
    routed_rows = [r for r in read_jsonl(config.runs_path) if r["provider"] == "routed"]
    assert len(routed_rows) == 3
    assert all(r["cost_usd"] == pytest.approx(routed_cost) for r in routed_rows)
    assert all(r["via"] == "keen" for r in routed_rows)  # fake.decision defaults to "search"


async def test_replay_routed_stops_when_cap_exceeded(route_config: AuditConfig):
    """The cap.exceeded() gate halts routing before a classify call that would breach the cap."""
    config = route_config
    rows = [_row(i, "keen", cost=2.0) for i in (1, 2, 3)] + [_row(i, "select", cost=3.0) for i in (1, 2, 3)]
    _seed(config.runs_path, rows)
    fake = FakeOpenRouter([])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        cap = CostCap(4.0)  # two routed rows cost ~4.00002, exceeding the 4.0 cap
        by_id = {p.id: p for p in load_prompts(config)}
        done = await replay_routed(config, ctx, by_id, cap)
    assert done == 2  # prompt 3 is skipped by the cap.exceeded() break
    assert cap.exceeded()
    assert not cap.halted
    routed_rows = [r for r in read_jsonl(config.runs_path) if r["provider"] == "routed"]
    assert len(routed_rows) == 2


async def test_replay_routed_skips_when_cap_already_halted(route_config: AuditConfig):
    """When the cap already halted run_jobs, replay_routed issues no classify calls at all."""
    config = route_config
    rows = [_row(i, "keen") for i in (1, 2, 3)] + [_row(i, "select") for i in (1, 2, 3)]
    _seed(config.runs_path, rows)
    fake = FakeOpenRouter([])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        cap = CostCap(40.0)
        cap.halt("test: halted by run_jobs credit refusal")
        by_id = {p.id: p for p in load_prompts(config)}
        done = await replay_routed(config, ctx, by_id, cap)
    assert done == 0
    assert cap.spent_usd == 0.0
    routed_rows = [r for r in read_jsonl(config.runs_path) if r["provider"] == "routed"]
    assert len(routed_rows) == 0


async def test_cmd_run_skips_replay_routed_when_cap_halted(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch):
    """cmd_run does not call replay_routed when run_jobs already halted the cap."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("KEENABLE_API_KEY", "secret")
    (tmp_path / "prompts.json").write_text(json.dumps(ROUTE_PROMPTS))
    (tmp_path / "audit.yaml").write_text(ROUTE_YAML)
    config = load_config(tmp_path / "audit.yaml")
    # Pre-populate keen 1-2 and select 1-3; keen 3 is pending and will hit a credit refusal.
    _seed(config.runs_path, [_row(1, "keen"), _row(2, "keen")] + [_row(i, "select") for i in (1, 2, 3)])
    fake = FakeOpenRouter(lambda body: {"error": {"message": "Insufficient credits"}})
    rc = await cli(["run", str(tmp_path / "audit.yaml")], fake)
    assert rc == 2  # cap.halted -> exit 2
    routed_rows = [r for r in read_jsonl(config.runs_path) if r["provider"] == "routed"]
    assert len(routed_rows) == 0
    assert len(fake.requests) == 1  # only the failed keen-3 chat completion, no classify calls
