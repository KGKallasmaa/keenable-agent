import json
from pathlib import Path

from app.cost import KEENABLE_REQUEST_USD, tokens_from_text
from app.fixtures import PREVIEW
from app.providers import search_exa, search_keenable
from app.score import is_sourced, pick_winner, summarize, total_cost

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = ROOT / "sample" / "queries.json"


def load_sample() -> list[dict]:
    return json.loads(SAMPLE_PATH.read_text())


def run_queries(
    queries: list[dict],
    mode: str,
    exa_key: str,
    keenable_key: str,
    usd_per_million: float,
    keenable_usd: float = KEENABLE_REQUEST_USD,
) -> dict:
    tasks = []
    for item in queries:
        query = (item.get("query") or "").strip()
        if not query:
            continue
        task_id = item.get("id") or query[:48]
        if mode == "preview":
            exa, keenable = _preview_arms(task_id)
        else:
            exa = search_exa(query, exa_key)
            keenable = search_keenable(query, keenable_key, keenable_usd)
        exa["context_tokens"] = int(exa.get("context_tokens") or tokens_from_text(exa.get("text") or ""))
        keenable["context_tokens"] = int(
            keenable.get("context_tokens") or tokens_from_text(keenable.get("text") or "")
        )
        tasks.append(
            {
                "id": task_id,
                "query": query,
                "exa": exa,
                "keenable": keenable,
                "winner": pick_winner(exa, keenable, usd_per_million),
                "exa_total": round(total_cost(exa, usd_per_million), 6),
                "keenable_total": round(total_cost(keenable, usd_per_million), 6),
                "exa_sourced": is_sourced(exa),
                "keenable_sourced": is_sourced(keenable),
            }
        )
    return {
        "mode": mode,
        "context_usd_per_million": usd_per_million,
        "keenable_usd_per_request": keenable_usd,
        "note": _note(mode),
        "summary": summarize(tasks, usd_per_million),
        "tasks": tasks,
    }


def _preview_arms(task_id: str) -> tuple[dict, dict]:
    found = PREVIEW.get(task_id)
    if not found:
        missing = {
            "ok": False,
            "urls": [],
            "text": "",
            "search_cost": 0.0,
            "context_tokens": 0,
            "latency_ms": 0,
            "error": "Preview only includes the five sample queries. Run live for anything else.",
            "priced_as": "n/a",
        }
        return missing, dict(missing)
    exa = dict(found["exa"])
    keenable = dict(found["keenable"])
    exa["context_tokens"] = tokens_from_text(exa.get("text") or "")
    keenable["context_tokens"] = tokens_from_text(keenable.get("text") or "")
    return exa, keenable


def _note(mode: str) -> str:
    if mode == "preview":
        return (
            "Sample questions, canned results. Not your Exa log. "
            "A sourced row has a URL and at least 40 characters of text. "
            "Context cost estimates a model reading that text at the rate shown."
        )
    return (
        "Live calls. Exa uses costDollars.total when the API sends it. "
        "Keenable is one search plus one fetch, priced at the Agent Builder list rate "
        "even on the free public endpoint. SELECT's SQL tool is not on the public REST API."
    )
