"""Query generation with fakes: every stage, drops per stage, resume, pilot, guards and the CLI wiring."""

import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest

from searchaudit.cli import main_async, parse_args
from searchaudit.config import load_config
from searchaudit.models import Judgment, Prompt, Run
from searchaudit.querygen import (
    BudgetExhausted,
    Candidate,
    Deps,
    OpenRouterLLM,
    QueryGen,
    Reply,
    bucket_searches,
    extract_json,
    judge_verification,
    spread,
)

from .fakes import FakeOpenRouter, completion, context, transport

TODAY = date(2026, 10, 7)
YAML = """
slug: qg
company: StackAI
category: Enterprise AI agent platform
company_url: https://www.stack-ai.com
use_case_hints: [KYC due diligence]
model: anthropic/claude-sonnet-5.5
judge_model: anthropic/claude-sonnet-5.5
querygen: {oversample: 2, routine_share: 0.2, max_usd: 50}
providers:
  - {id: exa, label: Exa, short: Exa, domain: exa.ai, hex: "#c2410c"}
  - {id: none}
"""
TYPES = ["lookup", "fresh", "multihop", "wide", "domain"]


# ---------------------------------------------------------------- a scripted world


def use_cases_reply() -> str:
    cases = [{"name": f"Workflow {i}", "persona": f"Analyst {i}", "workflow": f"does thing {i}",
              "web_need": "public filings", "sources": [f"https://stack-ai.com/case/{i}"]} for i in range(1, 7)]
    cases.append({"persona": "missing name"})  # malformed -> dropped and counted
    return "Here you go:\n```json\n" + json.dumps({"use_cases": cases}) + "\n```"


def tasks_reply(prompt: str) -> str:
    """Two tasks per use case (the requested types), plus one duplicate and one with an unknown type."""
    uc = prompt.split("Use case: ", 1)[1].split("\n", 1)[0]  # "Workflow 3"
    n = uc.split()[-1]
    wanted = [line.split(" x ", 1)[1].split(" ", 1)[0] for line in prompt.splitlines() if " x " in line[:8]]
    kinds = {"1": "famous", "2": "ambiguous", "3": "draftwrong", "4": "onedomain"}
    tasks = []
    for j, t in enumerate(wanted):
        tag = kinds.get(n, "hard") if j == 0 else "hard"
        tasks.append({"text": f"[{tag}] task {n}.{j} for {uc}", "type": t, "expected_searches": ["1", "2-3", "4+"][j % 3],
                      "needs": ["fresh"], "why_not_memory": "recent", "draft_answer": f"draft {n}.{j}",
                      "draft_sources": ["https://a.example/x"]})
    tasks.append(dict(tasks[0]))  # duplicate
    tasks.append({**tasks[0], "text": "odd type task", "type": "poetry"})
    return json.dumps({"tasks": tasks})


def verify_reply(prompt: str) -> str:
    if "[ambiguous]" in prompt:
        return json.dumps({"verdict": "reject", "reject_kind": "ambiguous", "reason": "two readings",
                           "ambiguous": True, "sources_agree": True, "answer": "", "sources": []})
    sources = (["https://sec.gov/a", "https://www.sec.gov/b"] if "[onedomain]" in prompt
               else ["https://sec.gov/a", "https://reuters.com/b"])
    answer = "the real answer" if "[draftwrong]" in prompt else "verified answer"
    return json.dumps({"verdict": "ok", "reject_kind": "", "reason": "", "ambiguous": False,
                       "sources_agree": True, "answer": answer, "correct_rule": "exact",
                       "partly_correct_rule": "half", "wrong_traps": ["old value"], "sources": sources})


def review_reply(prompt: str) -> str:
    return json.dumps({"reference_problem": "[refproblem]" in prompt, "reason": "checked"})


def world(prompt: str) -> str:
    if "List 6-10" in prompt:
        return use_cases_reply()
    if "Write exactly these tasks" in prompt:
        return tasks_reply(prompt)
    if "fact-checker" in prompt:
        return verify_reply(prompt)
    if "Every search provider was graded wrong" in prompt:
        return review_reply(prompt)
    raise AssertionError(f"unexpected research prompt: {prompt[:80]}")


def closed_book_answer(text: str) -> str:
    """What the model 'knows' with no tools."""
    if "[famous]" in text:
        return "right from memory"
    if "[draftwrong]" in text:
        return "the real answer"  # right, but the draft reference disagrees
    return "I cannot know this without search."


def grade(reference: str, answer: str) -> str:
    if answer == "right from memory":
        return "c"
    if answer == "the real answer":
        return "c" if "the real answer" in reference else "w"
    if answer.startswith("pilot-right"):
        return "c"
    return "w"


class Fakes:
    def __init__(self) -> None:
        self.llm_calls: list[tuple[str, bool]] = []
        self.closed: list[str] = []
        self.pilots: list[tuple[int, str]] = []

    async def llm(self, messages: list[dict], *, search: bool) -> Reply:
        text = messages[-1]["content"]
        self.llm_calls.append((text, search))
        return Reply(text=world(text), cost=0.01, searches=2, model="anthropic/claude-sonnet-5.5")

    async def closed_book(self, prompt: Prompt) -> Run:
        self.closed.append(prompt.text)
        return Run(prompt_id=prompt.id, provider="none", answer=closed_book_answer(prompt.text), cost_usd=0.001)

    async def judge(self, prompt: Prompt, run: Run, evidence: list[str]) -> tuple[Judgment, float]:
        g = grade(prompt.reference, run.answer)
        return Judgment(prompt_id=prompt.id, provider=run.provider, grade=g, checks=[g == "c"] * 4,
                        failure_mode=None if g == "c" else "outdated", note="fake"), 0.002

    async def pilot(self, prompt: Prompt, provider: str) -> tuple[Run, list[str]]:
        self.pilots.append((prompt.id, provider))
        answer = "pilot-wrong" if "[refproblem]" in prompt.text or "task 6" in prompt.text else "pilot-right"
        return Run(prompt_id=prompt.id, provider=provider, answer=answer, cost_usd=0.01), []

    def deps(self, pilot: bool = False) -> Deps:
        return Deps(llm=self.llm, closed_book=self.closed_book, judge=self.judge,
                    pilot=self.pilot if pilot else None, today=TODAY, engine="native",
                    model="anthropic/claude-sonnet-5.5")


@pytest.fixture
def audit(tmp_path: Path) -> Path:
    path = tmp_path / "audit.yaml"
    path.write_text(YAML)
    return path


# ---------------------------------------------------------------- the whole pipeline


async def test_pipeline_writes_balanced_prompts_and_counts_every_drop(audit: Path, caplog):
    caplog.set_level(logging.INFO)
    config = load_config(audit)
    fakes = Fakes()
    final = await QueryGen(config, fakes.deps(), target=5).run()

    # Every research call used search; use cases came from the company and its hints.
    assert all(search for _, search in fakes.llm_calls)
    assert "https://www.stack-ai.com" in fakes.llm_calls[0][0] and "KYC due diligence" in fakes.llm_calls[0][0]
    uc = json.loads((audit.parent / "querygen" / "use_cases.json").read_text())
    assert uc["search_engine"] == "native" and len(uc["use_cases"]) == 6 and uc["searches"] == 2

    # 6 use cases x ceil(5 * 2 / 6) = 2 tasks; duplicates and bad types dropped, not silently.
    cands = [json.loads(line) for line in (audit.parent / "prompts.candidates.jsonl").read_text().splitlines()]
    assert len(cands) == 12 and len(fakes.closed) == 12
    funnel = {s["stage"]: s for s in json.loads((audit.parent / "querygen" / "funnel.json").read_text())["stages"]}
    assert funnel["use cases"]["dropped"] == {"malformed": 1}
    assert funnel["tasks"]["dropped"] == {"duplicate": 6, "unknown type": 6}
    assert funnel["closed book"]["dropped"] == {"model knew it (no search, graded correct)": 1}
    assert funnel["verify"]["dropped"] == {
        "rejected: ambiguous": 1,
        "rejected: fewer than 2 independent sources": 1,
        "model knew it (re-check vs verified reference)": 1,
    }
    # 11 hard (12 - famous) + 1 routine reserve; 3 verify drops -> 8 hard + 1 routine.
    assert funnel["verify"]["out"] == 9
    assert funnel["select"]["out"] == 5 and funnel["select"]["dropped"] == {"not needed (balance)": 4}

    prompts = [Prompt.model_validate(p) for p in json.loads((audit.parent / "prompts.json").read_text())]
    assert [p.id for p in prompts] == [1, 2, 3, 4, 5] == [p.id for p in final]
    assert sum(p.tier == "routine" for p in prompts) == 1  # round(0.2 * 5)
    assert all(p.tier == "hard" for p in prompts if "[famous]" not in p.text)
    assert len({p.type for p in prompts}) == 5  # balanced over the five types
    assert all(p.use_case and p.expected_searches in (1, 2, 4) and p.as_of == "2026-10-07" for p in prompts)
    assert all("Sources: https://sec.gov/a ; https://reuters.com/b" in p.reference for p in prompts)
    assert not any("[ambiguous]" in p.text or "[draftwrong]" in p.text or "[onedomain]" in p.text
                   for p in prompts)

    md = (audit.parent / "PROMPTS.md").read_text()
    assert "## Funnel" in md and "closed book" in md and "`native`" in md and "Workflow 1" in md
    assert "rejected: ambiguous" in caplog.text or "verify rejected" in caplog.text


async def test_resume_skips_finished_stages(audit: Path):
    config = load_config(audit)
    await QueryGen(config, Fakes().deps(), target=5).run()
    first = (audit.parent / "prompts.json").read_text()

    class Broke(Fakes):
        async def llm(self, messages, *, search):
            raise AssertionError("no research call expected on resume")

        async def closed_book(self, prompt):
            raise AssertionError("no closed-book call expected on resume")

    again = Broke()
    await QueryGen(load_config(audit), again.deps(), target=5).run()
    assert (audit.parent / "prompts.json").read_text() == first


async def test_pilot_drops_reference_problems_and_keeps_real_failures(audit: Path, monkeypatch):
    orig = tasks_reply
    monkeypatch.setitem(world.__globals__, "tasks_reply",
                        lambda prompt: orig(prompt).replace("[hard] task 5.1", "[refproblem] task 5.1"))
    fakes = Fakes()
    await QueryGen(load_config(audit), fakes.deps(pilot=True), target=5, pilot=["exa", "keen"]).run()
    funnel = {s["stage"]: s for s in json.loads((audit.parent / "querygen" / "funnel.json").read_text())["stages"]}
    assert funnel["pilot"]["dropped"] == {"every provider wrong: reference problem": 1}
    assert funnel["pilot"]["in"] == 9 and funnel["pilot"]["out"] == 8
    assert len(fakes.pilots) == 18 and {p for _, p in fakes.pilots} == {"exa", "keen"}
    texts = [p["text"] for p in json.loads((audit.parent / "prompts.json").read_text())]
    assert not any("[refproblem]" in t for t in texts)
    reviews = [json.loads(x) for x in (audit.parent / "querygen" / "pilot_review.jsonl").read_text().splitlines()]
    # task 6.0 / 6.1: every provider wrong, but the reference is fine -> real failures, kept for selection
    assert sorted(r["reference_problem"] for r in reviews) == [False, False, True]


# ---------------------------------------------------------------- guards


def test_research_engine_cannot_be_an_audited_provider(audit: Path):
    deps = Fakes().deps()
    deps.engine = "exa"
    with pytest.raises(ValueError, match="audited provider"):
        QueryGen(load_config(audit), deps, target=5)


async def test_refuses_to_overwrite_prompts_of_an_audit_with_runs(audit: Path):
    (audit.parent / "prompts.json").write_text("[]")
    (audit.parent / "runs.jsonl").write_text("{}\n")
    with pytest.raises(FileExistsError):
        await QueryGen(load_config(audit), Fakes().deps(), target=5).run()
    assert (audit.parent / "prompts.json").read_text() == "[]"
    await QueryGen(load_config(audit), Fakes().deps(), target=5, force=True).run()
    assert len(json.loads((audit.parent / "prompts.json").read_text())) == 5


async def test_budget_stops_new_calls_and_resume_continues(audit: Path, caplog):
    config = load_config(audit)
    config.querygen.max_usd = 0.035  # use cases (0.01) + 3 task calls, then stop
    fakes = Fakes()
    await QueryGen(config, fakes.deps(), target=5).run()
    funnel = {s["stage"]: s for s in json.loads((audit.parent / "querygen" / "funnel.json").read_text())["stages"]}
    assert "not generated (budget)" in funnel["tasks"]["note"]
    assert len(fakes.llm_calls) <= 5
    config = load_config(audit)  # a larger cap resumes where it stopped
    await QueryGen(config, Fakes().deps(), target=5).run()
    cands = (audit.parent / "prompts.candidates.jsonl").read_text().splitlines()
    assert len(cands) == 12


# ---------------------------------------------------------------- helpers


def test_judge_verification_enforces_two_independent_sources():
    base = {"verdict": "ok", "ambiguous": False, "sources_agree": True, "answer": "A", "correct_rule": "A",
            "partly_correct_rule": "B", "wrong_traps": ["C"]}
    ok = judge_verification({**base, "sources": ["https://a.com/1", "https://b.org/2"]})
    assert ok["ok"] and ok["reference"].startswith("Answer: A Grading: correct = A; partly correct = B; wrong = C.")
    assert not judge_verification({**base, "sources": ["https://a.com/1", "https://news.a.com/2"]})["ok"]
    assert judge_verification({**base, "sources_agree": False,
                               "sources": ["https://a.com", "https://b.com"]})["reject_kind"] == "sources disagree"
    assert judge_verification({"verdict": "reject", "reject_kind": "paywalled"})["reject_kind"] == "paywalled"
    assert judge_verification([])["reject_kind"] == "malformed"


def test_bucket_and_json_helpers():
    assert [bucket_searches(x) for x in ("1", "2-3", 3, "4+", 7, "x")] == [1, 2, 2, 4, 4, None]
    assert extract_json('text ```json\n{"a": 1}\n``` more') == {"a": 1}
    assert extract_json('[{"a": 1}]') == [{"a": 1}]
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_spread_balances_types_then_use_cases():
    items = [Candidate(cid=f"{u}-{t}", use_case=u, text=f"{u}{t}", type=t, expected_searches=1)
             for u in ("u1", "u2") for t in TYPES]
    picked = spread(items, 5)
    assert sorted(c.type for c in picked) == sorted(TYPES)
    picked = spread(items, 4)
    assert {c.use_case for c in picked} == {"u1", "u2"}


async def test_openrouter_llm_uses_native_web_search_and_reports_cost():
    reply_body = completion("answer", cost=0.02, annotations=[
        {"type": "url_citation", "url_citation": {"url": "https://x.org/a", "title": "A"}}])
    reply_body["usage"]["server_tool_use_details"] = {"web_search_requests": 3}
    fake = FakeOpenRouter([reply_body, completion("plain", cost=0.01)])
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        ctx = context(http)
        from searchaudit.cost import ModelPricing

        llm = OpenRouterLLM(ctx.openrouter, "anthropic/claude-sonnet-5.5", "native", ModelPricing())
        got = await llm([{"role": "user", "content": "q"}], search=True)
        plain = await llm([{"role": "user", "content": "q"}], search=False)
    assert fake.requests[0]["tools"] == [{"type": "openrouter:web_search", "parameters": {"engine": "native"}}]
    assert "tools" not in fake.requests[1]
    assert (got.text, got.cost, got.searches, got.urls) == ("answer", 0.02, 3, ["https://x.org/a"])
    assert plain.searches == 0


# ---------------------------------------------------------------- CLI end to end over the fake transport


async def test_cli_querygen_end_to_end(audit: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    judge_reply = {"grade": "w", "checks": [True, False, False, True], "failure_mode": "outdated", "note": "n"}

    def script(body: dict) -> dict:
        if body.get("response_format"):  # the judge: the closed-book model never knows anything here
            return completion(json.dumps(judge_reply), cost=0.001)
        if body.get("tools"):  # research / verification with native search
            assert body["tools"][0]["parameters"]["engine"] == "native"
            return completion(world(body["messages"][-1]["content"]), cost=0.01)
        assert "NO search or fetch tools" in body["messages"][0]["content"]  # closed-book baseline
        return completion("I cannot know this without search.", cost=0.0005)

    fake = FakeOpenRouter(script)
    async with httpx.AsyncClient(transport=transport(fake)) as http:
        code = await main_async(parse_args(["querygen", str(audit), "--target", "5"]), http=http)
    assert code == 0
    prompts = json.loads((audit.parent / "prompts.json").read_text())
    assert len(prompts) == 5 and [p["id"] for p in prompts] == [1, 2, 3, 4, 5]
    assert (audit.parent / "PROMPTS.md").exists()
    # Nothing was known closed-book, so there are no routine controls to keep.
    assert all(p["tier"] == "hard" for p in prompts)


async def test_cli_querygen_requires_target(audit: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    async with httpx.AsyncClient(transport=transport(FakeOpenRouter([]))) as http:
        assert await main_async(parse_args(["querygen", str(audit)]), http=http) == 1


def test_budget_error_is_a_runtime_error():
    assert issubclass(BudgetExhausted, RuntimeError)
