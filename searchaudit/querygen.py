"""Query generation: build a prompt set that measures search, not the agent model's memory.

    uv run python -m searchaudit querygen audits/<slug>/audit.yaml --target 60 [--pilot exa,keen]

Stages (each resumable; every drop is counted and logged with its reason, nothing is capped silently):

1. use cases   An LLM with web search researches the company's real agent workflows -> 6-10 use cases,
               each with the persona who runs it.                          -> querygen/use_cases.json
2. tasks       Per use case, candidate tasks phrased the way an end user instructs an agent, spread over
               the five prompt types, each with expected_searches and a draft answer. ~oversample x
               target.                                                      -> prompts.candidates.jsonl
3. closed book Every candidate goes through the pinned agent model with NO tools (provider "none") and is
               judged against the draft. Candidates the model already knows are dropped, except a small
               share kept as "routine" controls.                            -> querygen/closed_book.jsonl
4. verify      A verifier with web search writes the final reference (answer, partly-correct rule, wrong-
               answer traps, >= 2 independent sources, as_of) or rejects the task (ambiguous, sources
               disagree). The closed-book answer is re-judged against the verified reference, so a task
               the model knew but the draft got wrong is caught.            -> querygen/verified.jsonl
5. pilot       Optional: run a few providers; drop tasks everyone (and no-search) gets right, and tasks
               everyone gets wrong because the reference is the problem.    -> querygen/pilot.jsonl
Then select N balanced by type and use case, renumber 1..N        -> prompts.json, PROMPTS.md, funnel.json

Research and verification must not use a search engine that is one of the audited providers (that would
bias the prompt set toward it), so they use the model's native search ("native" engine on OpenRouter).
All model calls go through `Deps`, so tests run the whole pipeline with fakes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from searchaudit.config import AuditConfig
from searchaudit.cost import ModelPricing, model_cost
from searchaudit.models import PROMPT_TYPE_NAMES, Judgment, Prompt, Run
from searchaudit.providers.openrouter import OpenRouterClient, OpenRouterError, url_citations

log = logging.getLogger(__name__)

AUDITED_ENGINES = {"exa", "parallel"}  # OpenRouter engines that are themselves audited providers
TYPE_GUIDE = {
    "lookup": "one specific fact about a named entity that is NOT famous (long-tail: small companies, "
              "niche filings, specific product limits, a named person's current role)",
    "fresh": "something that changed or was published in the last {fresh_days} days (after {since})",
    "multihop": "needs 2+ dependent searches: find X, then use X to find Y (e.g. 'who was the previous CTO' "
                "= find the current CTO, then the history)",
    "wide": "build a list or table of several items from several sources (e.g. every X that did Y in a "
            "period), with a definite, checkable set of items",
    "domain": "regulation, standards, compliance or official guidance: a specific rule, deadline, threshold "
              "or recent amendment, from an official source",
}
SEARCH_BUCKETS = {1: "1", 2: "2-3", 4: "4+"}
MIN_USE_CASES, MAX_USE_CASES = 6, 10


# ---------------------------------------------------------------- injectable model calls


@dataclass
class Reply:
    text: str
    cost: float = 0.0
    searches: int = 0
    model: str = ""
    urls: list[str] = field(default_factory=list)


class LLM(Protocol):
    async def __call__(self, messages: list[dict], *, search: bool) -> Reply: ...


class OpenRouterLLM:
    """The research/verification model, with the openrouter:web_search server tool on a chosen engine."""

    def __init__(self, client: OpenRouterClient, model: str, engine: str, pricing: ModelPricing,
                 timeout: float = 300.0) -> None:
        self.client, self.model, self.engine, self.pricing, self.timeout = client, model, engine, pricing, timeout

    async def __call__(self, messages: list[dict], *, search: bool) -> Reply:
        payload: dict[str, Any] = {"model": self.model, "temperature": 0.3, "messages": messages}
        if search:
            payload["tools"] = [{"type": "openrouter:web_search", "parameters": {"engine": self.engine}}]
        response = await self.client.chat(payload, timeout=self.timeout)
        usage = response.get("usage") or {}
        tool_use = usage.get("server_tool_use_details") or usage.get("server_tool_use") or {}
        message = response["choices"][0].get("message") or {}
        return Reply(
            text=message.get("content") or "",
            cost=model_cost(usage, self.pricing).usd,
            searches=int(tool_use.get("web_search_requests") or 0),
            model=response.get("model") or self.model,
            urls=[c["url"] for c in url_citations(message)],
        )


@dataclass
class Deps:
    llm: LLM  # research / task writing / verification, with search when asked
    closed_book: Callable[[Prompt], Awaitable[Run]]  # the pinned agent model with no tools (provider "none")
    judge: Callable[[Prompt, Run, list[str]], Awaitable[tuple[Judgment, float]]]  # (.., evidence) -> (j, cost)
    pilot: Callable[[Prompt, str], Awaitable[tuple[Run, list[str]]]] | None = None  # (prompt, provider)
    today: date = field(default_factory=date.today)
    engine: str = "native"
    model: str = ""


class BudgetExhausted(RuntimeError):
    pass


# ---------------------------------------------------------------- stored records


class UseCase(BaseModel):
    id: str  # "u1"
    name: str
    persona: str
    workflow: str  # what the agent does, concretely
    web_need: str = ""  # what it has to look up on the public web
    sources: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    cid: str  # "u1-3"
    use_case: str  # UseCase.id
    text: str
    type: str
    expected_searches: int  # 1, 2 (= 2-3) or 4 (= 4+)
    needs: list[str] = Field(default_factory=list)  # fresh / long_tail / multi_step / list
    why_not_memory: str = ""
    draft_answer: str = ""
    draft_sources: list[str] = Field(default_factory=list)


@dataclass
class Funnel:
    """Counts per stage with drop reasons; written to funnel.json and PROMPTS.md."""

    stages: list[dict] = field(default_factory=list)

    def add(self, name: str, n_in: int, n_out: int, dropped: Counter | dict | None = None, note: str = "") -> None:
        dropped = {k: v for k, v in dict(dropped or {}).items() if v}
        self.stages.append({"stage": name, "in": n_in, "out": n_out, "dropped": dropped, "note": note})
        why = ", ".join(f"{k}: {v}" for k, v in dropped.items()) or "none"
        log.info("querygen %-12s in %d -> out %d; dropped %d (%s)%s", name, n_in, n_out,
                 sum(dropped.values()), why, f"; {note}" if note else "")


# ---------------------------------------------------------------- helpers


def extract_json(text: str) -> Any:
    """The JSON object or array in a model reply (fenced or bare). Raises ValueError when there is none."""
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    for chunk in [*fenced, text]:
        pairs = sorted((("{", "}"), ("[", "]")), key=lambda oc: (chunk.find(oc[0]) == -1, chunk.find(oc[0])))
        for opener, closer in pairs:
            start, end = chunk.find(opener), chunk.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(chunk[start:end + 1])
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"no JSON in reply: {text[:200]!r}")


def bucket_searches(value: Any) -> int | None:
    """'1' -> 1, '2-3' / 2 / 3 -> 2, '4+' / 5 -> 4."""
    text = str(value).strip()
    if text.startswith("4") or text.endswith("+"):
        return 4
    try:
        n = int(text.split("-")[0])
    except ValueError:
        return None
    return 1 if n <= 1 else 2 if n <= 3 else 4


def domain(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def norm_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def reference_text(v: dict) -> str:
    traps = "; ".join(v.get("wrong_traps") or []) or "anything else"
    return (f"Answer: {v['answer']} Grading: correct = {v.get('correct_rule') or 'matches the answer'}; "
            f"partly correct = {v.get('partly_correct_rule') or 'some but not all of it'}; wrong = {traps}. "
            f"Sources: {' ; '.join(v['sources'])}")


# ---------------------------------------------------------------- the pipeline


class QueryGen:
    def __init__(self, config: AuditConfig, deps: Deps, target: int, *, pilot: list[str] | None = None,
                 force: bool = False) -> None:
        self.config, self.deps, self.target = config, deps, target
        self.q = config.querygen
        self.pilot_ids = pilot or []
        self.force = force
        self.dir = config.querygen_dir
        self.candidates_path = config.base_dir / "prompts.candidates.jsonl"
        self.funnel = Funnel()
        self.spent = 0.0
        self.gate = asyncio.Semaphore(max(1, self.q.concurrency))
        self.today = deps.today.isoformat()
        if deps.engine in AUDITED_ENGINES:
            raise ValueError(f"querygen search engine {deps.engine!r} is an audited provider; use 'native'")

    # -------- spend

    def _charge(self, usd: float) -> None:
        self.spent += usd

    def _check_budget(self) -> None:
        if self.spent >= self.q.max_usd:
            raise BudgetExhausted(f"querygen spend ${self.spent:.2f} reached max_usd ${self.q.max_usd:.2f}")

    async def _llm(self, messages: list[dict], *, search: bool) -> Reply:
        async with self.gate:
            self._check_budget()
            reply = await self.deps.llm(messages, search=search)
        self._charge(reply.cost)
        return reply

    async def _each(self, items: Iterable[Any], work: Callable[[Any], Awaitable[None]]) -> int:
        """Run work(item) for every item; returns how many were skipped because the budget ran out."""
        skipped = 0

        async def one(item: Any) -> None:
            nonlocal skipped
            try:
                await work(item)
            except BudgetExhausted:
                skipped += 1
            except (OpenRouterError, ValueError, KeyError, TypeError) as exc:
                log.error("querygen: %s failed: %s", getattr(item, "cid", item), exc)

        await asyncio.gather(*(one(i) for i in items))
        return skipped

    # -------- stage 1: use cases

    async def use_cases(self) -> list[UseCase]:
        path = self.dir / "use_cases.json"
        if path.exists():
            data = json.loads(path.read_text())
            cases = [UseCase.model_validate(u) for u in data["use_cases"]]
            self.funnel.add("use cases", len(cases), len(cases), note=f"resumed from {path.name}")
            return cases
        c = self.config
        hints = "\n".join(f"- {h}" for h in c.use_case_hints) or "(none given)"
        prompt = f"""Today is {self.today}. Research {c.company}{f" ({c.company_url})" if c.company_url else ""}, \
a company in the category "{c.category}", using web search. Read its site, docs, templates, customer stories, \
case studies and recent announcements.

Goal: we are building a benchmark of the web-search tasks that AI agents built on {c.company} actually run \
for its customers. List {MIN_USE_CASES}-{MAX_USE_CASES} concrete use cases (agent workflows) where the agent \
must look things up on the PUBLIC web (not only in the customer's private data). For each give the persona \
who runs it (role and industry), what the agent does step by step, and what it must find on the web.
Prefer workflows you found evidence for (cite the URL); do not invent generic ones.
Use cases the company asked us to make sure are covered:
{hints}

Reply with JSON only:
{{"use_cases": [{{"name": str, "persona": str, "workflow": str, "web_need": str, "sources": [url, ...]}}]}}"""
        reply = await self._llm([{"role": "user", "content": prompt}], search=True)
        raw = extract_json(reply.text)
        items = raw.get("use_cases", []) if isinstance(raw, dict) else raw
        cases, bad = [], 0
        for item in items:
            try:
                cases.append(UseCase(id=f"u{len(cases) + 1}", **{k: item[k] for k in ("name", "persona", "workflow")},
                                     web_need=item.get("web_need", ""), sources=item.get("sources") or []))
            except (KeyError, TypeError):
                bad += 1
        extra = max(0, len(cases) - MAX_USE_CASES)
        cases = cases[:MAX_USE_CASES]
        if len(cases) < MIN_USE_CASES:
            log.warning("querygen: only %d use cases (wanted %d-%d)", len(cases), MIN_USE_CASES, MAX_USE_CASES)
        if not cases:
            raise ValueError("use-case research returned no usable use cases")
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "created": self.today, "model": reply.model or self.deps.model, "search_engine": self.deps.engine,
            "searches": reply.searches, "cost_usd": round(reply.cost, 4), "cited": reply.urls,
            "use_cases": [u.model_dump() for u in cases],
        }, indent=2, ensure_ascii=False))
        self.funnel.add("use cases", len(items), len(cases),
                        {"malformed": bad, f"over {MAX_USE_CASES}": extra},
                        note=f"engine={self.deps.engine}, {reply.searches} searches")
        return cases

    # -------- stage 2: tasks

    def _type_plan(self, index: int, k: int) -> Counter:
        """Types for one use case, rotated by use-case index so the whole set is balanced."""
        types = list(PROMPT_TYPE_NAMES)
        return Counter(types[(index + j) % len(types)] for j in range(k))

    async def tasks(self, cases: list[UseCase]) -> list[Candidate]:
        existing = [Candidate.model_validate(r) for r in read_jsonl(self.candidates_path)]
        done = {c.use_case for c in existing}
        per_case = math.ceil(self.target * self.q.oversample / len(cases))
        since = (self.deps.today - timedelta(days=self.q.fresh_days)).isoformat()
        dropped: Counter = Counter()
        asked = 0
        seen = {norm_text(c.text) for c in existing}

        async def one(pair: tuple[int, UseCase]) -> None:
            nonlocal asked
            index, case = pair
            plan = self._type_plan(index, per_case)
            asked += per_case
            reply = await self._llm([{"role": "user", "content": self._task_prompt(case, plan, since)}], search=True)
            raw = extract_json(reply.text)
            items = raw.get("tasks", []) if isinstance(raw, dict) else raw
            dropped["not returned"] += max(0, per_case - len(items))
            n = 0
            for item in items:
                cand = self._candidate(case, item, n + 1)
                if isinstance(cand, str):
                    dropped[cand] += 1
                    continue
                key = norm_text(cand.text)
                if key in seen:
                    dropped["duplicate"] += 1
                    continue
                seen.add(key)
                n += 1
                append_jsonl(self.candidates_path, cand.model_dump())

        todo = [(i, c) for i, c in enumerate(cases) if c.id not in done]
        skipped = await self._each(todo, one)
        out = [Candidate.model_validate(r) for r in read_jsonl(self.candidates_path)]
        note = f"{per_case} asked per use case" + (f"; {len(done)} use cases resumed" if done else "")
        if skipped:
            note += f"; {skipped} use cases not generated (budget)"
        self.funnel.add("tasks", asked + len(existing), len(out), dropped, note=note)
        return out

    def _task_prompt(self, case: UseCase, plan: Counter, since: str) -> str:
        c = self.config
        wanted = "\n".join(
            f"- {n} x {t} ({PROMPT_TYPE_NAMES[t]}): "
            + TYPE_GUIDE[t].format(fresh_days=self.q.fresh_days, since=since)
            for t, n in plan.items()
        )
        return f"""Today is {self.today}. You write benchmark tasks for AI agents built on {c.company} \
({c.category}). The benchmark compares web-search providers, so every task must be one an agent can only \
answer well by searching the web NOW. A strong LLM answering from memory must fail or be out of date.

Use case: {case.name}
Persona: {case.persona}
Workflow: {case.workflow}
What it looks up on the web: {case.web_need}

Write exactly these tasks:
{wanted}

Rules:
- Phrase each task the way this persona instructs their agent: a natural request with context and purpose \
(e.g. "We're onboarding Acme GmbH as a supplier; find ..."), not a keyword query.
- Prefer: facts published after {since}; long-tail entities (small companies, niche filings, specific \
numbers); tasks that need several dependent searches; lists built from several sources.
- Avoid: anything famous or stable that is in a model's training data, opinions, predictions, anything \
that changes hourly (stock prices), answers that live only behind a login or paywall.
- Every task needs one objectively checkable answer (or a definite list) that stays true for weeks.
  Pin the period or version when needed ("as of {self.today}", "in Q3 2026").
- Use web search to make sure each task is real and answerable, and draft the answer with source URLs.
- expected_searches: how many searches a good agent needs: "1", "2-3" or "4+".

Reply with JSON only:
{{"tasks": [{{"text": str, "type": one of {list(plan)}, "expected_searches": "1"|"2-3"|"4+", \
"needs": [subset of "fresh","long_tail","multi_step","list"], "why_not_memory": str, \
"draft_answer": str, "draft_sources": [url, ...]}}]}}"""

    def _candidate(self, case: UseCase, item: Any, n: int) -> Candidate | str:
        """A Candidate, or the drop reason."""
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            return "malformed"
        if item.get("type") not in PROMPT_TYPE_NAMES:
            return "unknown type"
        searches = bucket_searches(item.get("expected_searches", ""))
        if searches is None:
            return "no expected_searches"
        if not str(item.get("draft_answer", "")).strip():
            return "no draft answer"
        return Candidate(
            cid=f"{case.id}-{n}", use_case=case.id, text=item["text"].strip(), type=item["type"],
            expected_searches=searches, needs=[str(x) for x in item.get("needs") or []],
            why_not_memory=str(item.get("why_not_memory", "")), draft_answer=str(item["draft_answer"]),
            draft_sources=[str(u) for u in item.get("draft_sources") or []],
        )

    # -------- stage 3: closed book

    def _prompt(self, cand: Candidate, reference: str, pid: int = 0) -> Prompt:
        return Prompt(id=pid, text=cand.text, type=cand.type, reference=reference, as_of=self.today,
                      use_case=cand.use_case, expected_searches=cand.expected_searches)

    async def closed_book(self, cands: list[Candidate]) -> dict[str, dict]:
        path = self.dir / "closed_book.jsonl"
        rows = {r["cid"]: r for r in read_jsonl(path)}

        async def one(cand: Candidate) -> None:
            self._check_budget()
            prompt = self._prompt(cand, f"Answer (draft, unverified): {cand.draft_answer}")
            run = await self.deps.closed_book(prompt)
            self._charge(run.cost_usd)
            if run.error:
                raise ValueError(f"closed-book run errored: {run.error}")
            judgment, cost = await self.deps.judge(prompt, run, [])
            self._charge(cost)
            row = {"cid": cand.cid, "grade": judgment.grade, "note": judgment.note, "answer": run.answer,
                   "model": run.model, "cost_usd": round(run.cost_usd + cost, 6)}
            rows[cand.cid] = row
            append_jsonl(path, row)

        skipped = await self._each([c for c in cands if c.cid not in rows], one)
        known = sum(1 for c in cands if rows.get(c.cid, {}).get("grade") == "c")
        missing = sum(1 for c in cands if c.cid not in rows)
        self.funnel.add("closed book", len(cands), len(cands) - known - missing,
                        {"model knew it (no search, graded correct)": known,
                         "not run (error or budget)": missing},
                        note=f"{known} known candidates are the pool for routine controls"
                             + (f"; {skipped} skipped for budget" if skipped else ""))
        return rows

    # -------- stage 4: verification

    def _routine_quota(self) -> int:
        return round(self.q.routine_share * self.target)

    async def verify(self, cands: list[Candidate], closed: dict[str, dict]) -> tuple[list[Prompt], list[Prompt]]:
        """-> (hard prompts, routine prompts), each with its verified reference (ids 0 for now)."""
        path = self.dir / "verified.jsonl"
        rows = {r["cid"]: r for r in read_jsonl(path)}
        hard = [c for c in cands if c.cid in closed and closed[c.cid]["grade"] != "c"]
        known = [c for c in cands if c.cid in closed and closed[c.cid]["grade"] == "c"]
        # Verify twice the routine quota (verification rejects some), spread over use cases.
        reserve = spread(known, 2 * self._routine_quota())
        todo = [c for c in [*hard, *reserve] if c.cid not in rows]

        async def one(cand: Candidate) -> None:
            reply = await self._llm([{"role": "user", "content": self._verify_prompt(cand)}], search=True)
            row = {"cid": cand.cid, **judge_verification(extract_json(reply.text)), "searches": reply.searches,
                   "cost_usd": round(reply.cost, 6), "as_of": self.today}
            if row["ok"] and closed[cand.cid]["grade"] != "c":
                # Re-check: did the model actually know it, and only the draft was wrong?
                self._check_budget()
                prompt = self._prompt(cand, row["reference"])
                run = Run(prompt_id=0, provider="none", answer=closed[cand.cid]["answer"])
                judgment, cost = await self.deps.judge(prompt, run, [])
                self._charge(cost)
                row["recheck_grade"] = judgment.grade
            rows[cand.cid] = row
            append_jsonl(path, row)

        skipped = await self._each(todo, one)
        reasons: Counter = Counter()
        hard_out, routine_out = [], []
        for cand in [*hard, *reserve]:
            row = rows.get(cand.cid)
            if row is None:
                reasons["not verified (error or budget)"] += 1
                continue
            if not row["ok"]:
                reasons[f"rejected: {row['reject_kind']}"] += 1
                continue
            prompt = self._prompt(cand, row["reference"]).model_copy(update={"as_of": row["as_of"]})
            if cand in reserve:
                routine_out.append(prompt.model_copy(update={"tier": "routine"}))
            elif row.get("recheck_grade") == "c":
                reasons["model knew it (re-check vs verified reference)"] += 1
            else:
                hard_out.append(prompt.model_copy(update={"tier": "hard"}))
        self.funnel.add("verify", len(hard) + len(reserve), len(hard_out) + len(routine_out), reasons,
                        note=f"{len(hard_out)} hard + {len(routine_out)} routine controls "
                             f"(verified {len(reserve)} of {len(known)} known candidates as the routine reserve)"
                             + (f"; {skipped} skipped for budget" if skipped else ""))
        for cid, row in rows.items():
            if not row["ok"]:
                log.info("querygen verify rejected %s (%s): %s", cid, row["reject_kind"], row["reason"][:200])
        return hard_out, routine_out

    def _verify_prompt(self, cand: Candidate) -> str:
        return f"""Today is {self.today}. You are the fact-checker for a search benchmark. Independently \
research this task with web search; do not trust the draft. Then write the grading reference, or reject \
the task.

TASK ({PROMPT_TYPE_NAMES[cand.type]}): {cand.text}

DRAFT ANSWER (unverified): {cand.draft_answer}
DRAFT SOURCES: {", ".join(cand.draft_sources) or "(none)"}

Reject (verdict "reject") when: the task is ambiguous (more than one reasonable reading, or no definite \
answer); credible sources disagree; you cannot confirm the answer from at least two independent sources \
(different organisations' sites, not syndicated copies); or the answer depends on login/paywalled pages.
Otherwise write: the answer (complete; for lists every item), what counts as correct, what counts as \
partly correct, the wrong answers an agent is likely to give (traps: outdated values, similar-named \
entities, wrong period), and the source URLs you confirmed it on.

Reply with JSON only:
{{"verdict": "ok"|"reject", "reject_kind": ""|"ambiguous"|"sources disagree"|"unverifiable"|"paywalled", \
"reason": str, "ambiguous": bool, "sources_agree": bool, "answer": str, "correct_rule": str, \
"partly_correct_rule": str, "wrong_traps": [str], "sources": [url, ...]}}"""

    # -------- stage 5: pilot

    async def pilot(self, prompts: list[Prompt], closed: dict[str, dict], cids: dict[str, str]) -> list[Prompt]:
        if not self.pilot_ids or self.deps.pilot is None:
            return prompts
        path = self.dir / "pilot.jsonl"
        rows = {(r["cid"], r["provider"]): r for r in read_jsonl(path)}
        pairs = [(p, prov) for p in prompts for prov in self.pilot_ids if (cids[norm_text(p.text)], prov) not in rows]

        async def one(pair: tuple[Prompt, str]) -> None:
            prompt, prov = pair
            self._check_budget()
            run, evidence = await self.deps.pilot(prompt, prov)
            self._charge(run.cost_usd)
            judgment, cost = await self.deps.judge(prompt, run, evidence)
            self._charge(cost)
            row = {"cid": cids[norm_text(prompt.text)], "provider": prov, "grade": judgment.grade, "note": judgment.note,
                   "answer": run.answer[:4000]}
            rows[(row["cid"], prov)] = row
            append_jsonl(path, row)

        await self._each(pairs, one)
        review_path = self.dir / "pilot_review.jsonl"
        reviews = {r["cid"]: r for r in read_jsonl(review_path)}
        reasons: Counter = Counter()
        kept: list[Prompt] = []
        for prompt in prompts:
            cid = cids[norm_text(prompt.text)]
            grades = [rows.get((cid, prov), {}).get("grade") for prov in self.pilot_ids]
            if None in grades:  # kept; counted in the note
                kept.append(prompt)
                continue
            # "Every provider and no-search right": hard prompts were already re-judged closed-book against
            # the verified reference in stage 4, so this only fires if that grade is "c"; routine controls are
            # meant to be easy and are exempt.
            if prompt.tier == "hard" and all(g == "c" for g in grades) and closed[cid]["grade"] == "c":
                reasons["every provider and no-search right"] += 1
                continue
            if all(g != "c" for g in grades):
                review = reviews.get(cid)
                if review is None:
                    try:
                        review = await self._review(prompt, cid, [rows[(cid, p)] for p in self.pilot_ids])
                    except BudgetExhausted:
                        kept.append(prompt)
                        continue
                    append_jsonl(review_path, review)
                    reviews[cid] = review
                if review["reference_problem"]:
                    reasons["every provider wrong: reference problem"] += 1
                    continue
            kept.append(prompt)
        incomplete = sum(1 for p in prompts
                         if any((cids[norm_text(p.text)], prov) not in rows for prov in self.pilot_ids))
        self.funnel.add("pilot", len(prompts), len(kept), reasons,
                        note=f"providers {', '.join(self.pilot_ids)}"
                             + (f"; {incomplete} not fully piloted (kept)" if incomplete else ""))
        return kept

    async def _review(self, prompt: Prompt, cid: str, rows: list[dict]) -> dict:
        answers = "\n\n".join(f"[{r['provider']}] graded {r['grade']}: {r['note']}\n{r['answer'][:1500]}"
                              for r in rows)
        text = f"""Every search provider was graded wrong or partly wrong on this benchmark task. Decide \
with web search whether the REFERENCE is the problem (wrong, outdated, ambiguous task, or unfindable on \
the public web) or the providers genuinely failed.

TASK: {prompt.text}
REFERENCE: {prompt.reference}

ANSWERS:
{answers}

Reply with JSON only: {{"reference_problem": bool, "reason": str}}"""
        reply = await self._llm([{"role": "user", "content": text}], search=True)
        data = extract_json(reply.text)
        return {"cid": cid, "reference_problem": bool(data.get("reference_problem")),
                "reason": str(data.get("reason", ""))[:600], "cost_usd": round(reply.cost, 6)}

    # -------- selection and output

    def select(self, hard: list[Prompt], routine: list[Prompt]) -> list[Prompt]:
        n_routine = min(self._routine_quota(), len(routine))
        n_hard = min(self.target - n_routine, len(hard))
        chosen = spread(routine, n_routine) + spread(hard, n_hard)
        short = self.target - len(chosen)
        if short > 0:
            log.warning("querygen: only %d prompts survived for a target of %d (short by %d); "
                        "rerun with a larger --oversample or more use cases", len(chosen), self.target, short)
        order = {t: i for i, t in enumerate(PROMPT_TYPE_NAMES)}
        chosen.sort(key=lambda p: (p.use_case, order[p.type], p.tier != "routine"))
        final = [p.model_copy(update={"id": i}) for i, p in enumerate(chosen, 1)]
        self.funnel.add("select", len(hard) + len(routine), len(final),
                        {"not needed (balance)": len(hard) + len(routine) - len(final)},
                        note=f"{n_hard} hard + {n_routine} routine, target {self.target}"
                             + (f", SHORT by {short}" if short > 0 else ""))
        return final

    def write(self, prompts: list[Prompt], cases: list[UseCase]) -> None:
        out = self.config.base_dir / self.config.prompts_file
        if out.exists() and self.config.runs_path.exists() and not self.force:
            raise FileExistsError(f"{out} already has runs in {self.config.runs_path.name}; "
                                  "pass --force to overwrite it")
        out.write_text(json.dumps([p.model_dump(mode="json") for p in prompts], indent=1, ensure_ascii=False)
                       + "\n")
        (self.dir / "funnel.json").write_text(json.dumps(
            {"target": self.target, "spent_usd": round(self.spent, 4), "engine": self.deps.engine,
             "model": self.deps.model, "stages": self.funnel.stages}, indent=2))
        (self.config.base_dir / "PROMPTS.md").write_text(self.summary(prompts, cases))
        log.info("querygen wrote %s (%d prompts) and PROMPTS.md; spend this session $%.2f",
                 out, len(prompts), self.spent)

    def summary(self, prompts: list[Prompt], cases: list[UseCase]) -> str:
        c = self.config
        names = {u.id: u.name for u in cases}
        lines = [
            f"# {c.company} prompt set", "",
            (f"Generated {self.today} by `searchaudit querygen` (target {self.target}). Research and reference "
             f"checks used {self.deps.model or c.querygen.model} with OpenRouter web search engine "
             f"`{self.deps.engine}`, which is not one of the audited providers. Closed-book filter: the pinned "
             "agent model with no tools (provider `none`)."), "",
            "## Use cases", "",
        ]
        for u in cases:
            used = sum(1 for p in prompts if p.use_case == u.id)
            lines.append(f"- **{u.id} {u.name}** ({used} prompts). Persona: {u.persona}. {u.workflow}")
        lines += ["", "## Funnel", "", "| Stage | In | Out | Dropped |", "|---|---:|---:|---|"]
        for s in self.funnel.stages:
            why = "; ".join(f"{k}: {v}" for k, v in s["dropped"].items()) or "-"
            lines.append(f"| {s['stage']} | {s['in']} | {s['out']} | {why}{' (' + s['note'] + ')' if s['note'] else ''} |")
        types = Counter(p.type for p in prompts)
        tiers = Counter(p.tier for p in prompts)
        searches = Counter(SEARCH_BUCKETS.get(p.expected_searches or 0, "?") for p in prompts)
        lines += [
            "", "## Mix", "",
            "Types: " + ", ".join(f"{PROMPT_TYPE_NAMES[t]} {types[t]}" for t in PROMPT_TYPE_NAMES) + ".",
            f"Tiers: hard {tiers['hard']}, routine {tiers['routine']}.",
            "Expected searches: " + ", ".join(f"{k}: {searches[k]}" for k in SEARCH_BUCKETS.values()) + ".",
            "", "## Prompts", "", "| # | Type | Tier | Use case | Searches | Task |", "|---:|---|---|---|---|---|",
        ]
        for p in prompts:
            text = p.text.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {p.id} | {PROMPT_TYPE_NAMES[p.type]} | {p.tier} | {names.get(p.use_case, p.use_case)} "
                         f"| {SEARCH_BUCKETS.get(p.expected_searches or 0, '?')} | {text} |")
        lines.append(f"\nSpend this session: ${self.spent:.2f}.\n")
        return "\n".join(lines)

    # -------- all stages

    async def run(self) -> list[Prompt]:
        self.dir.mkdir(parents=True, exist_ok=True)
        cases = await self.use_cases()
        cands = await self.tasks(cases)
        closed = await self.closed_book(cands)
        hard, routine = await self.verify(cands, closed)
        cids = {norm_text(c.text): c.cid for c in cands}  # texts are unique after stage 2
        survivors = await self.pilot([*hard, *routine], closed, cids)
        final = self.select([p for p in survivors if p.tier == "hard"], [p for p in survivors if p.tier == "routine"])
        self.write(final, cases)
        return final


def judge_verification(data: Any) -> dict:
    """Verifier JSON -> {ok, reject_kind, reason, reference, sources}. Enforces the rules, not just the verdict."""
    if not isinstance(data, dict):
        return {"ok": False, "reject_kind": "malformed", "reason": "verifier returned no object",
                "reference": "", "sources": []}
    sources = [str(u) for u in data.get("sources") or [] if str(u).startswith("http")]
    kind, reason = str(data.get("reject_kind") or ""), str(data.get("reason") or "")
    if data.get("verdict") != "ok":
        kind = kind or "rejected"
    elif data.get("ambiguous"):
        kind = "ambiguous"
    elif data.get("sources_agree") is False:
        kind = "sources disagree"
    elif len({domain(u) for u in sources}) < 2:
        kind, reason = "fewer than 2 independent sources", f"sources: {sources}"
    elif not str(data.get("answer") or "").strip():
        kind = "no answer"
    else:
        kind = ""
    ok = not kind
    return {"ok": ok, "reject_kind": kind, "reason": reason, "sources": sources,
            "reference": reference_text({**data, "sources": sources}) if ok else ""}


def spread(items: list, n: int) -> list:
    """Pick n items balanced by type and use case (fewest-so-far first), preferring more searches."""
    pool, chosen = list(items), []
    by_type: Counter = Counter()
    by_case: Counter = Counter()
    while pool and len(chosen) < n:
        best = min(pool, key=lambda x: (by_type[x.type], by_case[x.use_case], -(x.expected_searches or 0)))
        pool.remove(best)
        chosen.append(best)
        by_type[best.type] += 1
        by_case[best.use_case] += 1
    return chosen
