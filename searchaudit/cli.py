"""Command line: run -> judge -> assemble, plus probe.

    uv run python -m searchaudit run audits/stackai/audit.yaml [--limit N] [--providers exa,keen]
    uv run python -m searchaudit judge audits/stackai/audit.yaml
    uv run python -m searchaudit assemble audits/stackai/audit.yaml
    uv run python -m searchaudit probe audits/stackai/audit.yaml
"""

import argparse
import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from searchaudit.agent import AgentResult, AgentSettings, Job, resolve_model, run_jobs
from searchaudit.config import AuditConfig, Settings, load_config, load_prompts, provider_info
from searchaudit.cost import CostCap, ModelPricing, pricing_from_models
from searchaudit.judge import BUNDLE_VERSION, JudgeBundle, judge_run
from searchaudit.models import AuditMeta, AuditResults, Judgment, Run
from searchaudit.notes import method_notes
from searchaudit.providers import Registry, default_registry
from searchaudit.providers.base import Provider, ProviderContext
from searchaudit.providers.openrouter import OpenRouterClient, OpenRouterError

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


def pending_pairs(
    prompt_ids: list[int], provider_ids: list[str], existing: list[dict], retry_errors: bool
) -> list[tuple[int, str]]:
    """Query-major (prompt, provider) pairs not yet in runs.jsonl (or errored, with retry_errors)."""
    latest = latest_by_pair(existing)
    pairs = []
    for pid in prompt_ids:
        for prov in provider_ids:
            row = latest.get((pid, prov))
            if row is None or (retry_errors and row.get("error")):
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
    agent_model, _ = await pin_model(config, client)
    router_model = config.router_model
    if not await client.model_exists(router_model):
        log.warning("router model %s not on OpenRouter; using agent model %s", router_model, agent_model)
        router_model = agent_model
    ctx = ProviderContext(settings, http, client, agent_model, router_model, config.budget.max_tool_calls)
    providers = {p.id: p for p in available_providers(config, registry, ctx, args.providers)}
    existing = read_jsonl(config.runs_path)
    by_id = {p.id: p for p in prompts}
    pairs = pending_pairs(list(by_id), list(providers), existing, args.retry_errors)
    log.info("%d runs to do (%d already in %s)", len(pairs), len(existing), config.runs_path)
    agent = AgentSettings(agent_model, config.temperature, config.budget.max_tool_calls,
                          config.budget.max_seconds, await pricing_for(client, agent_model))
    cap = CostCap(config.cost_cap_usd, sum(row.get("cost_usd", 0.0) for row in existing))

    async def save(result: AgentResult) -> None:
        append_jsonl(config.runs_path, {**result.run.model_dump(mode="json"), "evidence": result.evidence})
        r = result.run
        log.info("prompt %s %-8s %s searches=%d $%.4f %s", r.prompt_id, r.provider,
                 "ERROR" if r.error else "ok", r.searches, r.cost_usd, r.error[:80])

    done = await run_jobs(
        [Job(by_id[pid], providers[prov]) for pid, prov in pairs], ctx, agent,
        global_limit=config.concurrency.global_limit, per_provider=config.concurrency.limit_for,
        cap=cap, on_result=save,
    )
    log.info("finished %d runs; audit run spend $%.2f of $%.2f cap", done, cap.spent_usd, cap.cap_usd)
    return 0


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
            if key[0] in prompts and judged.get(key, {}).get("run") != run_fingerprint(row)]
    log.info("%d runs to judge with %s (%s)", len(todo), bundle.model, bundle.version)
    gate = asyncio.Semaphore(config.concurrency.global_limit)

    async def one(row: dict) -> None:
        run = Run.model_validate(row)
        async with gate:
            try:
                judgment, cost = await judge_run(client, bundle, prompts[run.prompt_id], run,
                                                 row.get("evidence") or [], pricing)
            except OpenRouterError as exc:
                log.error("judge failed for prompt %s %s: %s", run.prompt_id, run.provider, exc)
                return
        append_jsonl(config.judgments_path,
                     {**judgment.model_dump(mode="json"), "cost_usd": cost, "bundle": bundle.version,
                      "run": run_fingerprint(row)})

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
    )
    return AuditResults(meta=meta, providers=providers, prompts=prompts, runs=runs, judgments=judgments)


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


COMMANDS = {"run": cmd_run, "judge": cmd_judge, "assemble": cmd_assemble, "probe": cmd_probe}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="searchaudit", description="Search Audit engine")
    parser.add_argument("command", choices=list(COMMANDS))
    parser.add_argument("config", help="path to audit.yaml")
    parser.add_argument("--limit", type=int, default=0, help="only the first N prompts")
    parser.add_argument("--providers", type=lambda s: [p.strip() for p in s.split(",") if p.strip()],
                        help="comma-separated provider ids, e.g. exa,keen")
    parser.add_argument("--retry-errors", action="store_true", help="re-run pairs whose last run errored")
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
