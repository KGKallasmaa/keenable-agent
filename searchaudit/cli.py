"""Command line: run -> judge -> assemble, plus probe.

    uv run python -m searchaudit run audits/spacex-jobs/audit.yaml [--limit N] [--providers exa,keen]
    uv run python -m searchaudit judge audits/spacex-jobs/audit.yaml
    uv run python -m searchaudit assemble audits/spacex-jobs/audit.yaml
    uv run python -m searchaudit probe audits/spacex-jobs/audit.yaml
    uv run python -m searchaudit querygen audits/<slug>/audit.yaml --target 60 [--pilot exa,keen]
"""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from searchaudit.agent import AgentResult, AgentSettings, Job, resolve_model, run_jobs
from searchaudit.config import AuditConfig, Settings, load_config, load_prompts, provider_info
from searchaudit.cost import (
    CostCap,
    ModelPricing,
    credit_failure,
    grok_pin_error,
    preflight_usd,
    pricing_from_models,
)
from searchaudit.judge import (
    BUNDLE_VERSION,
    JudgeBundle,
    build_checklist,
    judge_checklist,
    judge_run,
    reference_key,
)
from searchaudit.models import AuditMeta, AuditResults, Judgment, Run
from searchaudit.notes import method_notes
from searchaudit.providers import Registry, default_registry
from searchaudit.providers.base import Provider, ProviderContext
from searchaudit.providers.openrouter import OpenRouterClient, OpenRouterError
from searchaudit.providers.routed import classify, copied_answer

log = logging.getLogger("searchaudit")

# ---- storage ---------------------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def latest_by_pair(rows: list[dict]) -> dict[tuple[int, str], dict]:
    """Later rows win, so a re-run replaces an earlier attempt."""
    return {(row["prompt_id"], row["provider"]): row for row in rows}


def run_fingerprint(row: dict) -> str:
    """Identifies one run attempt, so a re-run invalidates its old judgment."""
    raw = json.dumps([row.get("answer", ""), row.get("error", ""), row.get("latency_ms", 0)])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _tool_failed(row: dict) -> bool:
    return any(not call.get("ok", True) for call in row.get("tool_calls") or [])


def pending_pairs(
    prompt_ids: list[int], provider_ids: list[str], existing: list[dict], retry_errors: bool,
    retry_tool_failures: bool = False, retry_budget_hits: bool = False,
) -> list[tuple[int, str]]:
    """Query-major (prompt, provider) pairs not yet in runs.jsonl.

    retry_errors re-runs a pair whose last run errored. retry_tool_failures re-runs a pair whose
    last run recorded a tool call that was not ok. retry_budget_hits re-runs a pair whose last run
    used up the tool-call or time budget, so a raised budget only re-does the runs it could change.
    """
    latest = latest_by_pair(existing)
    pairs = []
    for pid in prompt_ids:
        for prov in provider_ids:
            row = latest.get((pid, prov))
            # A credit refusal is not an answer. The next run does that cell again.
            unpaid = credit_failure((row or {}).get("error") or "")
            if (row is None or unpaid or (retry_errors and row.get("error"))
                    or (retry_tool_failures and _tool_failed(row))
                    or (retry_budget_hits and row.get("budget_hit"))):
                pairs.append((pid, prov))
    return pairs


# ---- shared setup ----------------------------------------------------------------------------------

def available_providers(
    config: AuditConfig, registry: Registry, ctx: ProviderContext, only: list[str] | None
) -> list[Provider]:
    providers = []
    for cfg in config.enabled_providers(only):
        provider = registry.get(cfg.id)
        reason = provider.unavailable_reason(ctx)
        if reason:
            log.warning("provider %s disabled: %s", cfg.id, reason)
        else:
            providers.append(provider)
    return providers


def _record_unavailable(
    config: AuditConfig, registry: Registry, ctx: ProviderContext, only: list[str] | None, running: set[str]
) -> None:
    """A provider that cannot run stays on the page as unavailable. It is not dropped in silence."""
    path = config.base_dir / "unavailable.json"
    reasons = {}
    for cfg in config.enabled_providers(only):
        if cfg.id in running:
            continue
        reasons[cfg.id] = registry.get(cfg.id).unavailable_reason(ctx) or "unavailable"
    if reasons:
        path.write_text(json.dumps(reasons, indent=2))
    elif path.exists():
        path.unlink()


async def pin_model(config: AuditConfig, client: OpenRouterClient) -> tuple[str, str]:
    """Resolve the agent model once per audit and persist it so resumed runs use the same model."""
    if config.pinned_path.exists():
        pinned = json.loads(config.pinned_path.read_text())
        if pinned.get("requested") == config.model:
            return pinned["agent_model"], pinned["note"]
    model, note = await resolve_model(client, config.model)
    config.pinned_path.write_text(json.dumps(
        {"requested": config.model, "agent_model": model, "note": note,
         "pinned_at": datetime.now(UTC).isoformat()}, indent=2))
    log.info("agent model pinned: %s", note)
    return model, note


async def pricing_for(client: OpenRouterClient, model: str) -> ModelPricing:
    try:
        return pricing_from_models(await client.models(), model)
    except httpx.HTTPError as exc:
        log.warning("could not load model pricing (%s); token costs will be 0 unless reported", exc)
        return ModelPricing()


# ---- commands --------------------------------------------------------------------------------------

async def cmd_run(config: AuditConfig, settings: Settings, args: argparse.Namespace,
                  http: httpx.AsyncClient, registry: Registry) -> int:
    if not settings.openrouter_api_key:
        log.error("OPENROUTER_API_KEY is not set (add it to .env)")
        return 1
    prompts = load_prompts(config)[: args.limit or None]
    client = OpenRouterClient(http, settings.openrouter_api_key)
    pin_error = grok_pin_error([m.get("id", "") for m in await client.models()], config.model)
    if pin_error:
        log.error("%s", pin_error)
        return 1
    agent_model, _ = await pin_model(config, client)
    router_model = config.router_model
    if not await client.model_exists(router_model):
        log.warning("router model %s not on OpenRouter; using agent model %s", router_model, agent_model)
        router_model = agent_model
    ctx = ProviderContext(settings, http, client, agent_model, router_model, config.budget.max_tool_calls)
    providers = {p.id: p for p in available_providers(config, registry, ctx, args.providers)}
    _record_unavailable(config, registry, ctx, args.providers, set(providers))
    estimate = preflight_usd(
        len(prompts), list(providers), {"keenable": settings.keenable_usd_per_request}
    )
    log.info("preflight estimate $%.2f against cap $%.2f", estimate, config.cost_cap_usd)
    if estimate > config.cost_cap_usd:
        log.error("preflight estimate $%.2f is over the $%.2f cap; not starting", estimate, config.cost_cap_usd)
        return 1
    existing = read_jsonl(config.runs_path)
    by_id = {p.id: p for p in prompts}
    pairs = pending_pairs(list(by_id), list(providers), existing, args.retry_errors, args.retry_tool_failures,
                          args.retry_budget_hits)
    pairs = [pair for pair in pairs if pair[1] != "routed"]
    log.info("%d runs to do (%d already in %s)", len(pairs), len(existing), config.runs_path)
    agent = AgentSettings(agent_model, config.temperature, config.budget.max_tool_calls,
                          config.budget.max_seconds, await pricing_for(client, agent_model))
    cap = CostCap(config.cost_cap_usd, sum(row.get("cost_usd", 0.0) for row in existing))

    async def save(result: AgentResult) -> None:
        row = {**result.run.model_dump(mode="json"), "evidence": result.evidence}
        if result.archive:
            row["archive"] = result.archive
        append_jsonl(config.runs_path, row)
        r = result.run
        log.info("prompt %s %-8s %s searches=%d $%.4f %s", r.prompt_id, r.provider,
                 "ERROR" if r.error else "ok", r.searches, r.cost_usd, r.error[:80])

    done = await run_jobs(
        [Job(by_id[pid], providers[prov]) for pid, prov in pairs], ctx, agent,
        global_limit=config.concurrency.global_limit, per_provider=config.concurrency.limit_for,
        cap=cap, on_result=save,
    )
    replayed = 0
    if "routed" in providers and not cap.halted:
        replayed = await replay_routed(config, ctx, by_id, cap, reroute=args.reroute)
    log.info("finished %d runs, %d routed lookups; audit run spend $%.2f of $%.2f cap",
             done, replayed, cap.spent_usd, cap.cap_usd)
    if cap.halted:
        log.error("stopped: %s", cap.halted)
        return 2
    return 0


async def replay_routed(config: AuditConfig, ctx: ProviderContext, by_id: dict, cap: CostCap,
                        reroute: bool = False) -> int:
    """Jev picks search or SELECT and the routed row copies that answer. No third agent call.

    reroute asks Jev again for every prompt, for after the route question changes.
    """
    latest = latest_by_pair(read_jsonl(config.runs_path))
    judged = latest_by_pair(read_jsonl(config.judgments_path))
    done = 0
    for prompt in by_id.values():
        keen = latest.get((prompt.id, "keen"))
        select = latest.get((prompt.id, "select"))
        if not keen or not select or keen.get("error") or select.get("error"):
            log.info("prompt %s routed skipped: search and SELECT both need a finished answer", prompt.id)
            continue
        already = latest.get((prompt.id, "routed"))
        # A routed row is current while it still carries its source arm's latest answer.
        if already and not already.get("error") and not reroute:
            source_now = select if already.get("via") == "select" else keen
            if already.get("answer", "") == source_now.get("answer", ""):
                continue
        if cap.exceeded():
            break
        started = time.monotonic()
        arm, confidence, call, decision = await classify(ctx, prompt.text)
        jev_ms = int((time.monotonic() - started) * 1000)
        if call.error:
            log.error("prompt %s routed classify failed: %s", prompt.id, call.error[:160])
            if credit_failure(call.error):
                cap.halt(
                    "OpenRouter has no credits. Routing stopped. "
                    "Finished cells stay in runs.jsonl; run the same command again after a top-up."
                )
                return done
            continue
        source = select if arm == "select" else keen
        row = copied_answer(source, arm, confidence, call, jev_ms, decision)
        cap.add(row.get("cost_usd", 0.0))
        append_jsonl(config.runs_path, row)
        prior = judged.get((prompt.id, arm))
        # Copy the grade only if it is for this exact source run; otherwise judge grades the routed row.
        if prior and prior.get("run") == run_fingerprint(source):
            copied = {key: value for key, value in prior.items() if key not in ("cost_usd", "run")}
            copied["provider"] = "routed"
            copied["cost_usd"] = 0
            copied["run"] = run_fingerprint(row)
            append_jsonl(config.judgments_path, copied)
        log.info("prompt %s routed  %s conf=%s $%.4f", prompt.id, arm, confidence, call.cost_usd)
        done += 1
    return done


async def ensure_checklists(config: AuditConfig, client: OpenRouterClient, model: str, prompts: list,
                            gate: asyncio.Semaphore) -> dict[int, dict]:
    """One checklist per prompt, cached in checklists.json and rebuilt only when the reference changes."""
    path = config.base_dir / "checklists.json"
    cached = {c["prompt_id"]: c for c in json.loads(path.read_text())} if path.exists() else {}
    stale = [p for p in prompts if cached.get(p.id, {}).get("reference") != reference_key(p)]
    if stale:
        log.info("building %d checklists with %s", len(stale), model)

        async def one(prompt):
            async with gate:
                checklist, _ = await build_checklist(client, model, prompt)
            cached[prompt.id] = checklist

        await asyncio.gather(*(one(p) for p in stale))
        path.write_text(json.dumps([cached[k] for k in sorted(cached)], indent=2, ensure_ascii=False))
    return cached


async def cmd_judge(config: AuditConfig, settings: Settings, args: argparse.Namespace,
                    http: httpx.AsyncClient, registry: Registry) -> int:
    if not settings.openrouter_api_key:
        log.error("OPENROUTER_API_KEY is not set (add it to .env)")
        return 1
    client = OpenRouterClient(http, settings.openrouter_api_key)
    if not await client.model_exists(config.judge_model):
        log.error("judge model %s is not listed on OpenRouter", config.judge_model)
        return 1
    bundle = JudgeBundle(config.judge_model)
    pricing = await pricing_for(client, bundle.model)
    prompts = {p.id: p for p in load_prompts(config)}
    judged = latest_by_pair(read_jsonl(config.judgments_path))
    todo = [row for key, row in latest_by_pair(read_jsonl(config.runs_path)).items()
            if key[0] in prompts and not credit_failure(row.get("error") or "")
            and (judged.get(key, {}).get("run") != run_fingerprint(row)
                 or judged.get(key, {}).get("bundle") != bundle.version)]
    log.info("%d runs to judge with %s (%s)", len(todo), bundle.model, bundle.version)
    gate = asyncio.Semaphore(config.concurrency.global_limit)
    checklists = await ensure_checklists(config, client, bundle.model, list(prompts.values()), gate)

    async def one(row: dict) -> None:
        run = Run.model_validate(row)
        async with gate:
            held: list = []
            try:
                judgment, cost = await judge_checklist(
                    client, bundle, prompts[run.prompt_id], run, row.get("evidence") or [],
                    checklists[run.prompt_id], pricing, store=held)
            except OpenRouterError as exc:
                log.error("judge failed for prompt %s %s: %s", run.prompt_id, run.provider, exc)
                return
        saved = {**judgment.model_dump(mode="json"), "cost_usd": cost, "bundle": bundle.version,
                 "run": run_fingerprint(row)}
        if held:
            saved["request"] = held[0]["request"]
            saved["response"] = held[0]["response"]
        append_jsonl(config.judgments_path, saved)

    await asyncio.gather(*(one(row) for row in todo))
    return 0


def assemble(config: AuditConfig, settings: Settings, registry: Registry) -> AuditResults:
    prompts = load_prompts(config)
    prompt_ids = {p.id for p in prompts}
    run_rows = [r for k, r in latest_by_pair(read_jsonl(config.runs_path)).items() if k[0] in prompt_ids]
    judge_rows = latest_by_pair(read_jsonl(config.judgments_path))
    runs = [Run.model_validate(row) for row in run_rows]
    judgments = [Judgment.model_validate(judge_rows[(r.prompt_id, r.provider)])
                 for r in runs if (r.prompt_id, r.provider) in judge_rows]
    pinned = json.loads(config.pinned_path.read_text()) if config.pinned_path.exists() else {}
    ran = {r.provider for r in runs}
    providers = [
        provider_info(cfg, registry.get(cfg.id).via, registry.get(cfg.id).price_note(settings))
        for cfg in config.providers if cfg.id in ran
    ]
    judge_cost = sum(judge_rows[(j.prompt_id, j.provider)].get("cost_usd", 0.0) for j in judgments)
    meta = AuditMeta(
        slug=config.slug, company=config.company, category=config.category,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        agent_model=pinned.get("agent_model", config.model), agent_model_note=pinned.get("note", ""),
        judge_model=config.judge_model, budget=config.budget.model_dump(),
        total_cost_usd=round(sum(r.cost_usd for r in runs) + judge_cost, 4),
        notes=method_notes(config, settings, runs, judgments, BUNDLE_VERSION),
        second_judge=_second_judge(config),
    )
    return AuditResults(meta=meta, providers=providers, prompts=prompts, runs=runs, judgments=judgments)


def _second_judge(config: AuditConfig) -> dict:
    """The second-judge summary, or {} when it was never run or every re-grade failed (n == 0)."""
    path = config.base_dir / "second_judge.json"
    if not path.exists():
        return {}
    summary = json.loads(path.read_text())
    return summary if summary.get("n") else {}


async def cmd_assemble(config: AuditConfig, settings: Settings, args: argparse.Namespace,
                       http: httpx.AsyncClient, registry: Registry) -> int:
    results = assemble(config, settings, registry)
    if config.base_dir.resolve().name != config.slug:
        log.warning("slug %r differs from directory %s; the report is served at /r/<directory name>",
                    config.slug, config.base_dir)
    config.results_path.write_text(results.model_dump_json(indent=2))
    log.info("wrote %s: %d prompts, %d runs, %d judgments", config.results_path,
             len(results.prompts), len(results.runs), len(results.judgments))
    return 0


async def cmd_probe(config: AuditConfig, settings: Settings, args: argparse.Namespace,
                    http: httpx.AsyncClient, registry: Registry) -> int:
    client = OpenRouterClient(http, settings.openrouter_api_key)
    ctx = ProviderContext(settings, http, client, config.model, config.router_model, config.budget.max_tool_calls)
    config.probes_dir.mkdir(parents=True, exist_ok=True)
    for cfg in config.enabled_providers(args.providers):
        provider = registry.get(cfg.id)
        reason = provider.unavailable_reason(ctx)
        try:
            snapshot = {"disabled": reason} if reason else await provider.describe(ctx)
        except Exception as exc:  # noqa: BLE001 - a probe reports any failure rather than aborting the rest
            snapshot = {"error": f"{type(exc).__name__}: {exc}"}
        snapshot = {"provider": cfg.id, "probed_at": datetime.now(UTC).isoformat(), "via": provider.via,
                    **snapshot}
        (config.probes_dir / f"{cfg.id}.json").write_text(json.dumps(snapshot, indent=2, default=str))
        log.info("probe %s: %s", cfg.id, reason or snapshot.get("error") or "ok")
    return 0


async def cmd_querygen(config: AuditConfig, settings: Settings, args: argparse.Namespace,
                       http: httpx.AsyncClient, registry: Registry) -> int:
    """Generate prompts.json for an audit (see searchaudit/querygen.py for the stages)."""
    from searchaudit.agent import run_agent, run_one
    from searchaudit.providers.none import NoSearchProvider
    from searchaudit.querygen import AUDITED_ENGINES, Deps, OpenRouterLLM, QueryGen

    if not settings.openrouter_api_key:
        log.error("OPENROUTER_API_KEY is not set (add it to .env)")
        return 1
    if not args.target:
        log.error("--target N is required for querygen")
        return 1
    q = config.querygen
    if q.search_engine in AUDITED_ENGINES:
        log.error("querygen.search_engine %r is an audited provider; research must use another engine",
                  q.search_engine)
        return 1
    client = OpenRouterClient(http, settings.openrouter_api_key)
    for model in (q.model, config.judge_model):
        if not await client.model_exists(model):
            log.error("model %s is not listed on OpenRouter", model)
            return 1
    agent_model, _ = await pin_model(config, client)
    ctx = ProviderContext(settings, http, client, agent_model, config.router_model, config.budget.max_tool_calls)
    agent = AgentSettings(agent_model, config.temperature, config.budget.max_tool_calls,
                          config.budget.max_seconds, await pricing_for(client, agent_model))
    bundle = JudgeBundle(config.judge_model)
    judge_pricing = await pricing_for(client, bundle.model)
    baseline = NoSearchProvider()

    calls_path = config.querygen_dir / "calls.jsonl"

    async def closed_book(prompt):
        async with baseline.open(ctx) as session:
            result = await run_agent(prompt, "none", session, client, agent)
        append_jsonl(calls_path, {
            "kind": "closed_book", "text": prompt.text,
            "answer": result.run.answer, "error": result.run.error, "archive": result.archive,
        })
        return result.run

    async def judge(prompt, run, evidence):
        held: list = []
        judgment, cost = await judge_run(client, bundle, prompt, run, evidence, judge_pricing, store=held)
        if held:
            append_jsonl(calls_path, {
                "kind": "judge", "text": prompt.text, "provider": run.provider,
                "request": held[0]["request"], "response": held[0]["response"],
            })
        return judgment, cost

    pilot_ids = args.pilot or []
    try:
        pilot_providers = {p.id: p for p in available_providers(config, registry, ctx, pilot_ids)} if pilot_ids else {}
    except ValueError as exc:  # pilot provider not in audit.yaml
        log.error("%s", exc)
        return 1
    missing = set(pilot_ids) - set(pilot_providers)
    if missing:
        log.error("pilot provider(s) unavailable: %s", ", ".join(sorted(missing)))
        return 1

    async def pilot(prompt, provider_id):
        result = await run_one(Job(prompt, pilot_providers[provider_id]), ctx, agent)
        append_jsonl(calls_path, {
            "kind": "pilot", "provider": provider_id, "text": prompt.text,
            "answer": result.run.answer, "error": result.run.error, "archive": result.archive,
        })
        return result.run, result.evidence

    deps = Deps(
        llm=OpenRouterLLM(client, q.model, q.search_engine, await pricing_for(client, q.model)),
        closed_book=closed_book, judge=judge, pilot=pilot if pilot_ids else None,
        engine=q.search_engine, model=q.model,
    )
    if args.max_usd:
        config.querygen.max_usd = args.max_usd
    gen = QueryGen(config, deps, args.target, pilot=pilot_ids, force=args.force)
    try:
        await gen.run()
    except FileExistsError as exc:
        log.error("%s", exc)
        return 1
    return 0


COMMANDS = {"run": cmd_run, "judge": cmd_judge, "assemble": cmd_assemble, "probe": cmd_probe,
            "querygen": cmd_querygen}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="searchaudit", description="Search Audit engine")
    parser.add_argument("command", choices=list(COMMANDS))
    parser.add_argument("config", help="path to audit.yaml")
    parser.add_argument("--limit", type=int, default=0, help="only the first N prompts")
    parser.add_argument("--providers", type=lambda s: [p.strip() for p in s.split(",") if p.strip()],
                        help="comma-separated provider ids, e.g. exa,keen")
    parser.add_argument("--retry-errors", action="store_true", help="re-run pairs whose last run errored")
    parser.add_argument("--reroute", action="store_true",
                        help="run: ask Jev again for every routed prompt (after the route question changes)")
    parser.add_argument("--retry-budget-hits", action="store_true",
                        help="re-run pairs whose last run used up the budget (after raising it in audit.yaml)")
    parser.add_argument("--retry-tool-failures", action="store_true",
                        help="re-run pairs whose last run has a failed tool call")
    parser.add_argument("--target", type=int, default=0, help="querygen: number of prompts to produce")
    parser.add_argument("--pilot", type=lambda s: [p.strip() for p in s.split(",") if p.strip()],
                        help="querygen: providers for the optional pilot stage, e.g. exa,keen")
    parser.add_argument("--max-usd", type=float, default=0.0, help="querygen: spend cap (default from audit.yaml)")
    parser.add_argument("--force", action="store_true", help="querygen: overwrite prompts.json even if runs exist")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace, registry: Registry | None = None,
                     http: httpx.AsyncClient | None = None) -> int:
    config = load_config(args.config)
    settings = Settings.from_env()
    registry = registry or default_registry()
    if http is not None:
        return await COMMANDS[args.command](config, settings, args, http, registry)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return await COMMANDS[args.command](config, settings, args, client, registry)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(main_async(parse_args(argv)))
