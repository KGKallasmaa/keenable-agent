"""The research agent: one OpenRouter chat loop, identical for every provider, plus audit scheduling."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from searchaudit.cost import CostCap, ModelPricing, credit_failure, model_cost
from searchaudit.models import Citation, Prompt, Run, ToolCall
from searchaudit.providers.base import Provider, ProviderContext, ProviderSession, ServerUsage
from searchaudit.providers.openrouter import OpenRouterClient, OpenRouterError, url_citations

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the research assistant inside an enterprise AI-agent platform. Use the web search tools "
    "to find current, authoritative sources, read the pages you need, then answer the user's request "
    "concisely and factually. Cite the source URL for every key claim. If sources disagree or you "
    "cannot verify something, say so instead of guessing."
)
BUDGET_MESSAGE = (
    "Tool budget exhausted. Do not call any more tools. Answer now with what you have found, "
    "citing source URLs."
)
FINAL_ANSWER_TIMEOUT = 60.0
EVIDENCE_LIMIT = 12
URL_RE = re.compile(r"https?://[^\s)\]\"'<>,]+")


@dataclass(frozen=True)
class AgentSettings:
    model: str
    temperature: float = 0.2
    max_tool_calls: int = 15
    max_seconds: float = 180.0
    pricing: ModelPricing = field(default_factory=ModelPricing)


@dataclass
class AgentResult:
    run: Run
    evidence: list[str] = field(default_factory=list)  # trimmed tool results, for the judge
    archive: dict = field(default_factory=dict)  # full OpenRouter responses and tool bodies


class _Trace:
    """Mutable accumulators for one run."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.searches = self.fetches = self.tokens_in = self.tokens_out = 0
        self.cost_search = self.cost_model = 0.0
        self.estimated = False
        self.evidence: list[str] = []
        self.titles: dict[str, str] = {}
        self.cited: list[str] = []
        self.model = ""
        self.completions: list[dict] = []
        self.tools: list[dict] = []

    def add_completion(self, response: dict, session: ProviderSession, pricing: ModelPricing, request: dict) -> dict:
        self.completions.append({"request": request, "response": response})
        usage = response.get("usage") or {}
        self.tokens_in += int(usage.get("prompt_tokens") or 0)
        self.tokens_out += int(usage.get("completion_tokens") or 0)
        self.model = response.get("model") or self.model
        server = session.account_response(response)
        model_usd, search_usd, estimated = split_cost(usage, pricing, server, bool(session.server_tools()))
        self.cost_model += model_usd
        self.cost_search += search_usd
        self.estimated |= estimated
        self.searches += server.searches
        self.fetches += server.fetches
        self.calls.extend(server.calls)
        message = response["choices"][0].get("message") or {}
        for cite in url_citations(message):
            self.titles.setdefault(cite["url"], cite["title"])
            self.cited.append(cite["url"])
            if cite["content"]:
                self.evidence.append(f"{cite['url']}\n{cite['content'][:1500]}")
        return message

    def used(self) -> int:
        return self.searches + self.fetches


def split_cost(
    usage: dict, pricing: ModelPricing, server: ServerUsage, server_tools: bool
) -> tuple[float, float, bool]:
    """-> (model USD, server-tool USD, estimated) for one completion.

    usage.cost is the whole charge, server-tool fees included. When OpenRouter also reports the
    upstream inference cost, the remainder is the billed server-tool spend; otherwise the server
    tools are priced at list price and subtracted from the total.
    """
    total = model_cost(usage, pricing)
    if total.estimated:
        return total.usd, server.cost_usd, True
    upstream = (usage.get("cost_details") or {}).get("upstream_inference_cost")
    if server_tools and upstream is not None:
        upstream = float(upstream)
        return upstream, max(total.usd - upstream, 0.0), False
    return max(total.usd - server.cost_usd, 0.0), server.cost_usd, server.cost_estimated


async def run_agent(
    prompt: Prompt,
    provider_id: str,
    session: ProviderSession,
    client: OpenRouterClient,
    settings: AgentSettings,
    clock: Callable[[], float] = time.monotonic,
) -> AgentResult:
    started = clock()
    deadline = started + settings.max_seconds
    trace = _Trace()
    extra = session.instructions()
    messages: list[dict] = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{extra}" if extra else SYSTEM_PROMPT},
        {"role": "user", "content": prompt.text},
    ]
    answer, error, budget_hit = "", "", False
    payload: dict | None = None
    session.start(prompt.text)
    session.bind_query_time(prompt.query_time)
    await session.prepare(prompt.text)
    try:
        while True:
            remaining = deadline - clock()
            exhausted = trace.used() >= settings.max_tool_calls or remaining <= 0
            budget_hit |= exhausted
            if exhausted:
                messages.append({"role": "user", "content": BUDGET_MESSAGE})
            payload = _payload(settings, messages, session, final=exhausted)
            timeout = FINAL_ANSWER_TIMEOUT if exhausted else remaining
            try:
                response = await asyncio.wait_for(client.chat(payload, timeout=timeout), timeout)
            except TimeoutError:
                if exhausted:
                    raise
                budget_hit = True
                deadline = clock()  # out of time: next turn forces an answer
                continue
            message = trace.add_completion(response, session, settings.pricing, payload)
            tool_calls = message.get("tool_calls") or []
            if exhausted or not tool_calls:
                answer = (message.get("content") or "").strip()
                break
            messages.append({k: v for k, v in message.items() if k in ("role", "content", "tool_calls")})
            for tool_call in tool_calls:
                messages.append(await _execute(tool_call, session, trace, settings, deadline - clock()))
            budget_hit |= trace.used() > settings.max_tool_calls
    except (OpenRouterError, TimeoutError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        body = getattr(exc, "body", None)
        if body is not None:
            trace.completions.append({"request": payload, "response": body, "error": error})
    run = Run(
        prompt_id=prompt.id, provider=provider_id, answer=answer, citations=_citations(answer, trace),
        searches=trace.searches, fetches=trace.fetches, tool_calls=trace.calls,
        latency_ms=int((clock() - started) * 1000),
        cost_search_usd=round(trace.cost_search, 6), cost_model_usd=round(trace.cost_model, 6),
        cost_usd=round(trace.cost_search + trace.cost_model, 6), cost_estimated=trace.estimated,
        tokens_in=trace.tokens_in, tokens_out=trace.tokens_out, error=error, budget_hit=budget_hit,
        model=trace.model or settings.model,
    )
    session.finish(run)
    archive = {"completions": trace.completions, "tools": trace.tools, **session.archive_extra()}
    return AgentResult(run=run, evidence=trace.evidence[:EVIDENCE_LIMIT], archive=archive)


def _payload(settings: AgentSettings, messages: list[dict], session: ProviderSession, final: bool) -> dict:
    payload: dict = {"model": settings.model, "temperature": settings.temperature, "messages": list(messages)}
    functions = session.function_tools()
    tools = functions if final else [*session.server_tools(), *functions]
    if tools:
        payload["tools"] = tools
        if final:
            payload["tool_choice"] = "none"  # history holds tool calls, so tools stay declared
    return payload


async def _execute(
    tool_call: dict, session: ProviderSession, trace: _Trace, settings: AgentSettings, remaining: float
) -> dict:
    function = tool_call.get("function") or {}
    name = function.get("name") or ""
    reply = {"role": "tool", "tool_call_id": tool_call.get("id", "")}
    if trace.used() >= settings.max_tool_calls or remaining <= 0:
        return {**reply, "content": "Not executed: tool budget exhausted."}
    try:
        args = json.loads(function.get("arguments") or "{}")
        outcome = await asyncio.wait_for(session.call_tool(name, args), remaining)
    except (json.JSONDecodeError, KeyError, TimeoutError) as exc:
        error = f"{type(exc).__name__}: {str(exc) or 'run time budget exhausted'}"[:300]
        trace.calls.append(ToolCall(tool=name, args={"raw": function.get("arguments", "")}, ok=False, error=error))
        trace.tools.append({"name": name, "args": {"raw": function.get("arguments", "")}, "text": f"ERROR: {error}"})
        trace.searches += 1  # a failed call still spends budget
        return {**reply, "content": f"ERROR: {error}"}
    trace.calls.extend(outcome.calls)
    trace.searches += outcome.searches
    trace.fetches += outcome.fetches
    # jev_route is a classifier call. Its cost is trivial next to a search and stays on the call only.
    trace.cost_search += sum(c.cost_usd for c in outcome.calls if c.tool != "jev_route")
    trace.estimated |= outcome.cost_estimated
    trace.evidence.extend(outcome.evidence)
    for cite in outcome.citations:
        trace.titles.setdefault(cite.url, cite.title)
    stored = {"name": name, "args": args, "text": outcome.text}
    if outcome.raw is not None:
        stored["raw"] = outcome.raw
    trace.tools.append(stored)
    return {**reply, "content": outcome.text}


def _citations(answer: str, trace: _Trace) -> list[Citation]:
    """Annotation citations when the model returned them, else URLs written in the answer."""
    stripped = [url.rstrip(".;:") for url in URL_RE.findall(answer)]
    urls = list(dict.fromkeys(trace.cited or stripped))
    return [Citation(url=url, title=trace.titles.get(url, "")) for url in urls]


async def resolve_model(client: OpenRouterClient, requested: str) -> tuple[str, str]:
    """Pin `openrouter/auto` to a concrete model with one tiny calibration call."""
    if requested != "openrouter/auto":
        return requested, "pinned in the audit config"
    response = await client.chat({
        "model": requested, "max_tokens": 5,
        "messages": [{"role": "user", "content": "Reply with OK."}],
    })
    pinned = response.get("model") or requested
    return pinned, f"openrouter/auto resolved once to {pinned} and pinned for every provider"


@dataclass(frozen=True)
class Job:
    prompt: Prompt
    provider: Provider


async def run_jobs(
    jobs: Iterable[Job],
    ctx: ProviderContext,
    settings: AgentSettings,
    *,
    global_limit: int,
    per_provider: Callable[[str], int],
    cap: CostCap,
    on_result: Callable[[AgentResult], Awaitable[None]],
) -> int:
    """Run jobs (already in query-major order) under global and per-provider limits. Returns runs done."""
    gate = asyncio.Semaphore(global_limit)
    lanes: dict[str, asyncio.Semaphore] = {}
    done = 0

    async def one(job: Job) -> None:
        nonlocal done
        lane = lanes.setdefault(job.provider.id, asyncio.Semaphore(per_provider(job.provider.id)))
        async with lane, gate:
            if cap.exceeded():
                return
            result = await run_one(job, ctx, settings)
            if credit_failure(result.run.error):
                cap.halt(
                    "OpenRouter has no credits. This cell was not saved. "
                    "Finished cells stay in runs.jsonl; run the same command again after a top-up."
                )
                return
            cap.add(result.run.cost_usd)
            await on_result(result)
            done += 1

    await asyncio.gather(*(one(job) for job in jobs))
    return done


async def run_one(job: Job, ctx: ProviderContext, settings: AgentSettings) -> AgentResult:
    try:
        async with job.provider.open(ctx) as session:
            return await run_agent(job.prompt, job.provider.id, session, ctx.openrouter, settings)
    except Exception as exc:  # provider setup failed (e.g. MCP connect); record it, keep the audit going
        log.exception("run failed: prompt %s provider %s", job.prompt.id, job.provider.id)
        run = Run(prompt_id=job.prompt.id, provider=job.provider.id, error=f"{type(exc).__name__}: {exc}"[:500],
                  model=settings.model)
        return AgentResult(run=run)
