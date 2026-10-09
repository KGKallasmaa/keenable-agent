"""Second-judge check: re-grade answers with a model from another family and measure agreement.

With checklists.json present (judge-v3), every answer is re-graded against the same checklist and the
output adds per-fact agreement. Without it, only contested answers are re-graded (the old behaviour).
Writes <audit dir>/second_judge.json and second_judgments.jsonl; `assemble` copies the summary into
meta.second_judge and the report's Judge tab shows it.

    uv run python scripts/second_judge.py audits/spacex-jobs/audit.yaml [--model openai/gpt-6.1-sol]
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
from searchaudit.judge import JudgeBundle, judge_checklist, judge_run
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
    cl_path = config.base_dir / "checklists.json"
    checklists = {c["prompt_id"]: c for c in json.loads(cl_path.read_text())} if cl_path.exists() else {}
    if checklists:
        keys = sorted(k for k in runs if k[0] in prompts and k in judged)
        scope = "every answer, graded against the same checklist as the primary judge"
    else:
        keys = contested(runs, judged)
        scope = "answers on prompts where the primary judge graded the search providers differently"
    log.info("%s: %d answers to re-grade with %s", config.slug, len(keys), model)
    async with httpx.AsyncClient() as http:
        client = OpenRouterClient(http, settings.openrouter_api_key)
        bundle, pricing = JudgeBundle(model), await pricing_for(client, model)
        gate = asyncio.Semaphore(config.concurrency.global_limit)

        async def one(key):
            row = runs[key]
            async with gate:
                try:
                    if checklists:
                        j, cost = await judge_checklist(client, bundle, prompts[key[0]], Run.model_validate(row),
                                                        row.get("evidence") or [], checklists[key[0]], pricing)
                    else:
                        j, cost = await judge_run(client, bundle, prompts[key[0]], Run.model_validate(row),
                                                  row.get("evidence") or [], pricing)
                except OpenRouterError as exc:
                    log.error("second judge failed for %s: %s", key, exc)
                    return None
            return key, j, cost

        graded = [r for r in await asyncio.gather(*(one(k) for k in keys)) if r]
    with (config.base_dir / "second_judgments.jsonl").open("w") as fh:
        for _, j, _ in graded:
            fh.write(j.model_dump_json() + "\n")
    done = [(k, j.grade, c) for k, j, c in graded]
    pairs = [(judged[k]["grade"], g) for k, g, _ in done]
    fact_pairs = []
    for k, j, _ in graded:
        first = {f["id"]: f["status"] for f in judged[k].get("facts") or []}
        fact_pairs += [(first[f["id"]], f["status"]) for f in j.facts if f["id"] in first]
    binary = [(a == "c", b == "c") for a, b in pairs]
    out = {
        "model": model,
        "primary_model": config.judge_model,
        "n": len(pairs),
        "scope": scope,
        "fact_n": len(fact_pairs),
        "fact_agreement": sum(a == b for a, b in fact_pairs) / len(fact_pairs) if fact_pairs else None,
        "fact_kappa": kappa(fact_pairs),
        "second_correct": dict(Counter(k[1] for k, g, _ in done if g == "c")),
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
    ap.add_argument("--model", default="openai/gpt-6.1-sol")
    a = ap.parse_args()
    asyncio.run(main(a.audit, a.model))
