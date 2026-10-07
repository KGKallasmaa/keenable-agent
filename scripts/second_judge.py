"""Second-judge check: re-grade contested answers with a different model and measure agreement.

Contested = prompts where the primary judge did not grade every search provider the same, i.e.
the answers that decide the ranking. Writes <audit dir>/second_judge.json; `assemble` copies it
into meta.second_judge and the report's Judge tab shows it.

    uv run python scripts/second_judge.py audits/stackai/audit.yaml [--model anthropic/claude-opus-5.5]
"""

import argparse
import asyncio
import json
import logging
from collections import Counter

import httpx
from dotenv import load_dotenv

from searchaudit.cli import latest_by_pair, pricing_for, read_jsonl
from searchaudit.config import Settings, load_config, load_prompts
from searchaudit.judge import JudgeBundle, judge_run
from searchaudit.models import Run
from searchaudit.providers.openrouter import OpenRouterClient, OpenRouterError

log = logging.getLogger("second_judge")


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for two raters over the same items."""
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[k] * right[k] for k in set(left) | set(right)) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def contested(runs: dict, judged: dict) -> list[tuple[int, str]]:
    by_prompt: dict[int, dict[str, str]] = {}
    for (pid, prov), j in judged.items():
        if prov != "none":
            by_prompt.setdefault(pid, {})[prov] = j["grade"]
    keys = []
    for pid, grades in by_prompt.items():
        if len(set(grades.values())) > 1:
            keys += [(pid, prov) for prov in grades
                     if (pid, prov) in runs and not runs[(pid, prov)].get("error")]
    return sorted(keys)


async def main(path: str, model: str) -> None:
    config, settings = load_config(path), Settings.from_env()
    prompts = {p.id: p for p in load_prompts(config)}
    runs = latest_by_pair(read_jsonl(config.runs_path))
    judged = latest_by_pair(read_jsonl(config.judgments_path))
    keys = contested(runs, judged)
    log.info("%s: %d contested answers to re-grade with %s", config.slug, len(keys), model)
    async with httpx.AsyncClient() as http:
        client = OpenRouterClient(http, settings.openrouter_api_key)
        bundle, pricing = JudgeBundle(model), await pricing_for(client, model)
        gate = asyncio.Semaphore(config.concurrency.global_limit)

        async def one(key):
            row = runs[key]
            async with gate:
                try:
                    j, cost = await judge_run(client, bundle, prompts[key[0]], Run.model_validate(row),
                                              row.get("evidence") or [], pricing)
                except OpenRouterError as exc:
                    log.error("second judge failed for %s: %s", key, exc)
                    return None
            return key, j.grade, cost

        done = [r for r in await asyncio.gather(*(one(k) for k in keys)) if r]
    pairs = [(judged[k]["grade"], g) for k, g, _ in done]
    binary = [(a == "c", b == "c") for a, b in pairs]
    out = {
        "model": model,
        "primary_model": config.judge_model,
        "n": len(pairs),
        "scope": "answers on prompts where the primary judge graded the search providers differently",
        "exact_agreement": sum(a == b for a, b in pairs) / len(pairs) if pairs else None,
        "correct_agreement": sum(a == b for a, b in binary) / len(binary) if binary else None,
        "kappa": kappa(pairs),
        "disagreements": [
            {"prompt_id": k[0], "provider": k[1], "primary": judged[k]["grade"], "second": g}
            for k, g, _ in done if judged[k]["grade"] != g
        ],
        "cost_usd": round(sum(c for *_, c in done), 4),
    }
    (config.base_dir / "second_judge.json").write_text(json.dumps(out, indent=2))
    log.info("%s: exact %.0f%%, correct-vs-not %.0f%%, kappa %.2f over %d answers ($%.2f)",
             config.slug, 100 * (out["exact_agreement"] or 0), 100 * (out["correct_agreement"] or 0),
             out["kappa"] or 0, out["n"], out["cost_usd"])


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("audit")
    ap.add_argument("--model", default="anthropic/claude-opus-5.5")
    a = ap.parse_args()
    asyncio.run(main(a.audit, a.model))
