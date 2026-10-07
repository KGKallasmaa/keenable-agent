"""AuditResults -> report view model.

The report is a pure function of the stored results: this module computes every number, test and
sentence once, and the templates only lay them out. The same model feeds the web page, the print
view (PDF) and the per-prompt pages, so they never disagree. The output is plain JSON-serializable
data (dicts, lists, str, finite floats, None).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import permutations
from typing import Any
from urllib.parse import urlparse

from searchaudit import stats
from searchaudit.models import FAILURE_MODES, PROMPT_TYPE_NAMES, RUBRIC, AuditResults, Judgment, Run
from searchaudit.report.chart import pareto_chart
from searchaudit.stats import DEFAULT_WEIGHTS, Outcome, Weights

KEENABLE = ("keen", "select", "routed")
SEARCH, SELECT = "keen", "select"
ALT_NOTE_POINTS = 5  # another provider must beat Keenable's route by this many score points to get a note
MAX_LISTED = 8  # rows in the judge's "split decisions" list

VM = dict[str, Any]
log = logging.getLogger(__name__)


def build_view_model(results: AuditResults, weights: Weights = DEFAULT_WEIGHTS) -> VM:
    providers = [p.model_dump() for p in results.providers]
    ids = [p["id"] for p in providers]
    labels = {p["id"]: p["label"] for p in providers}
    prompts = sorted(results.prompts, key=lambda p: p.id)
    n = len(prompts)
    cells = _cells(results, ids)
    outcomes = [{pid: _outcome(cells[(p.id, pid)]) for pid in ids} for p in prompts]
    correct = {pid: [cells[(p.id, pid)]["grade"] == "c" for p in prompts] for pid in ids}

    summary = _summary(prompts, ids, cells, outcomes, weights)
    by_accuracy = sorted(ids, key=lambda i: (-summary[i]["k"], _or_inf(summary[i]["per_correct"]), labels[i]))
    by_score = sorted(ids, key=lambda i: (-summary[i]["score"], -summary[i]["k"], summary[i]["per_prompt"]))
    ties = stats.accuracy_ties(correct, by_accuracy)
    front = stats.pareto_front({i: (summary[i]["acc"], summary[i]["per_prompt"]) for i in ids})
    failed = {i: summary[i]["errors"] for i in ids}
    head = stats.headline(
        results.meta.company, labels, n, {i: summary[i]["k"] for i in ids}, by_score, ties, failed
    )

    prompt_rows = _prompt_rows(prompts, ids, cells, outcomes, weights)
    types = _types(prompt_rows, ids)
    guide = _guide(types, ids, labels, summary, prompt_rows)
    log.info(
        "report model %s: headline=%s leader=%s tied=%s",
        results.meta.slug,
        head.kind,
        ties.leader,
        sorted(ties.tied),
    )
    return {
        "meta": _meta(results, n, len(ids)),
        "providers": providers,
        "provider": {p["id"]: p for p in providers},
        "labels": labels,
        "ids": ids,
        "rubric": RUBRIC,
        "failure_modes": {k: {"name": v[0], "definition": v[1]} for k, v in FAILURE_MODES.items()},
        "weights": {"a": weights.accuracy, "c": weights.cost, "s": weights.speed},
        "n": n,
        "headline": {"kind": head.kind, "text": head.text},
        "facts": _facts(summary, ties, labels, n),
        "summary": summary,
        "highlight": {
            "score": _extremes({i: round(summary[i]["score"], 1) for i in ids}, lower_is_better=False),
            "k": _extremes({i: summary[i]["k"] for i in ids}, lower_is_better=False),
            "cost": _extremes({i: round(summary[i]["per_prompt"], 4) for i in ids}, lower_is_better=True),
            "t50": _extremes({i: round(summary[i]["t50"], 1) for i in ids}, lower_is_better=True),
        },
        "by_score": by_score,
        "by_accuracy": by_accuracy,
        "leader": ties.leader,
        "tied": [i for i in by_accuracy if i in ties.tied],
        "tie_p": ties.adjusted_p,
        "pareto": [i for i in ids if i in front],
        "chart": pareto_chart(providers, summary, front),
        "prompts": prompt_rows,
        "counts": {
            "all": n,
            "disagree": sum(1 for r in prompt_rows if r["disagree"]),
            "nobody": sum(1 for r in prompt_rows if r["nobody"]),
            "routed_select": sum(1 for r in prompt_rows if r["routed_via"] == SELECT),
        },
        "types": types,
        "h2h": _head_to_head(ids, correct, outcomes, labels),
        "h2h_default": _default_pair(by_score, by_accuracy, providers),
        "failures": _failures(prompt_rows, ids),
        "guide": guide,
        "judge": _judge(results, prompt_rows, ids),
        "client": _client(
            results.meta.company, ids, providers, prompt_rows, summary, ties, head.kind, weights, n
        ),
    }


# ---------------------------------------------------------------- cells


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def _expected_grade(checks: Sequence[bool]) -> str:
    """The rubric rule: all four pass = c; the first plus all but one = p; otherwise w."""
    if all(checks):
        return "c"
    return "p" if checks and checks[0] and sum(checks) == len(checks) - 1 else "w"


def _cell(run: Run | None, j: Judgment | None) -> VM:
    missing = run is None or j is None
    grade = "w" if missing else j.grade
    failure = "error" if missing else j.failure_mode
    if run is None:
        note = "No run was recorded for this provider on this prompt; counted as wrong."
    elif j is None:
        note = "This answer was not judged; counted as wrong."
    else:
        note = j.note
    checks = [False] * len(RUBRIC) if missing else list(j.checks)
    cost = (run.cost_usd or run.cost_search_usd + run.cost_model_usd) if run else 0.0
    citations = run.citations if run else []
    return {
        "grade": grade,
        "checks": checks,
        "expected": _expected_grade(checks),
        "failure": failure,
        "failure_name": FAILURE_MODES[failure][0] if failure else "",
        "failure_def": FAILURE_MODES[failure][1] if failure else "",
        "note": note,
        "cost": round(cost, 6),
        "est": bool(run and run.cost_estimated),
        "seconds": round(run.latency_ms / 1000, 2) if run else 0.0,
        "searches": run.searches if run else 0,
        "fetches": run.fetches if run else 0,
        "answer": run.answer if run else "",
        "error": run.error if run else "missing run",
        "budget_hit": bool(run and run.budget_hit),
        "via": run.via if run else None,
        "confidence": run.route_confidence if run else None,
        "model": run.model if run else "",
        "sources": [{"url": c.url, "title": c.title, "domain": _domain(c.url)} for c in citations],
        "trace": [
            {
                "tool": t.tool,
                "args": _args(t.args),
                "ms": t.latency_ms,
                "ok": t.ok,
                "error": t.error,
                "chars": t.result_chars,
                "cost": t.cost_usd,
                "urls": len(t.urls),
            }
            for t in (run.tool_calls if run else [])
        ],
        "missing": missing,
    }


def _args(args: Mapping[str, Any]) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return text if len(text) <= 160 else text[:157] + "..."


def _cells(results: AuditResults, ids: Sequence[str]) -> dict[tuple[int, str], VM]:
    runs = {(r.prompt_id, r.provider): r for r in results.runs}
    judgments = {(j.prompt_id, j.provider): j for j in results.judgments}
    return {
        (p.id, pid): _cell(runs.get((p.id, pid)), judgments.get((p.id, pid)))
        for p in results.prompts
        for pid in ids
    }


def _outcome(cell: Mapping[str, Any]) -> Outcome:
    return Outcome(cell["grade"], cell["cost"], cell["seconds"])


def _or_inf(x: float | None) -> float:
    return float("inf") if x is None else x


def _extremes(values: Mapping[str, float], lower_is_better: bool) -> dict[str, str]:
    """'best' / 'worst' marks for a row of numbers (nothing when all are equal)."""
    if len(values) < 2:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if lo == hi:
        return {}
    best, worst = (lo, hi) if lower_is_better else (hi, lo)
    return {i: "best" if v == best else "worst" if v == worst else "" for i, v in values.items()}


# ---------------------------------------------------------------- summary


def _summary(prompts, ids, cells, outcomes, weights: Weights) -> dict[str, VM]:
    n = len(prompts)
    scores = stats.value_scores(outcomes, weights)
    out: dict[str, VM] = {}
    for pid in ids:
        xs = [cells[(p.id, pid)] for p in prompts]
        grades = Counter(x["grade"] for x in xs)
        k = grades["c"]
        total = sum(x["cost"] for x in xs)
        times = [x["seconds"] for x in xs]
        lo, hi = stats.wilson(k, n)
        out[pid] = {
            "k": k,
            "partial": grades["p"],
            "wrong": grades["w"],
            "n": n,
            "acc": k / n if n else 0.0,
            "ci": [lo, hi],
            "per_prompt": total / n if n else 0.0,
            "per_correct": total / k if k else None,
            "est": any(x["est"] for x in xs),
            "t50": stats.median(times),
            "t90": stats.percentile(times, 0.9),
            "searches": sum(x["searches"] for x in xs) / n if n else 0.0,
            "errors": sum(1 for x in xs if x["error"] or x["failure"] == "error"),
            "backed": sum(1 for x in xs if len(x["checks"]) > 2 and x["checks"][2]),
            "score": scores.get(pid, 0.0),
        }
    return out


def _facts(summary: Mapping[str, VM], ties: stats.Ties, labels: Mapping[str, str], n: int) -> list[VM]:
    tied = sorted(ties.tied, key=lambda i: (-summary[i]["k"], labels[i]))
    cheapest = min(tied, key=lambda i: summary[i]["per_prompt"])
    fastest = min(summary, key=lambda i: summary[i]["t50"])
    lead = summary[ties.leader]
    others = [labels[i] for i in tied if i != ties.leader]
    return [
        {
            "key": "Most correct",
            "ids": tied,
            "value": f"{labels[ties.leader]}: {lead['k']} of {n}",
            "detail": (
                f"Tied within chance with {_join(others)}."
                if others
                else "Significantly ahead of every other provider."
            ),
        },
        {
            "key": "Cheapest of the most correct",
            "ids": [cheapest],
            "value": f"{labels[cheapest]}: {_money(summary[cheapest]['per_prompt'], summary[cheapest]['est'])} per prompt",
            "detail": f"{summary[cheapest]['k']} of {n} correct.",
        },
        {
            "key": "Fastest",
            "ids": [fastest],
            "value": f"{labels[fastest]}: {summary[fastest]['t50']:.1f}s median",
            "detail": f"{summary[fastest]['k']} of {n} correct.",
        },
    ]


def _money(x: float, est: bool) -> str:
    return f"{'~' if est else ''}${x:.3f}"


def _join(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------- prompts


def _prompt_rows(prompts, ids, cells, outcomes, weights: Weights) -> list[VM]:
    rows = []
    for p, oc in zip(prompts, outcomes, strict=True):
        row_cells = {pid: dict(cells[(p.id, pid)]) for pid in ids}
        scores = stats.prompt_scores(oc, weights)
        domain_count = Counter(d for c in row_cells.values() for d in {s["domain"] for s in c["sources"]})
        for pid, c in row_cells.items():
            c["score"] = scores[pid]
            for s in c["sources"]:
                s["unique"] = domain_count[s["domain"]] == 1
        best = max(ids, key=lambda i: scores[i])
        grades = {c["grade"] for c in row_cells.values()}
        rows.append(
            {
                "id": p.id,
                "text": p.text,
                "type": p.type,
                "type_name": PROMPT_TYPE_NAMES[p.type],
                "reference": p.reference,
                "as_of": p.as_of,
                "origin": p.origin,
                "cells": row_cells,
                "best_value": best if scores[best] > 0 else None,
                "highlight": {
                    "score": _extremes({i: round(scores[i], 3) for i in ids}, lower_is_better=False),
                    "seconds": _extremes({i: row_cells[i]["seconds"] for i in ids}, lower_is_better=True),
                    "cost": _extremes({i: round(row_cells[i]["cost"], 4) for i in ids}, lower_is_better=True),
                    "searches": _extremes({i: row_cells[i]["searches"] for i in ids}, lower_is_better=True),
                },
                "disagree": len(grades) > 1,
                "nobody": all(c["grade"] != "c" for c in row_cells.values()),
                "routed_via": row_cells["routed"]["via"] if "routed" in row_cells else None,
            }
        )
    return rows


# ---------------------------------------------------------------- prompt types and the guide


def _types(rows: Sequence[VM], ids: Sequence[str]) -> list[VM]:
    out = []
    for tid, name in PROMPT_TYPE_NAMES.items():
        ps = [r for r in rows if r["type"] == tid]
        if not ps:
            continue
        m = len(ps)
        k = {i: sum(1 for r in ps if r["cells"][i]["grade"] == "c") for i in ids}
        score = {i: 100 * sum(r["cells"][i]["score"] for r in ps) / m for i in ids}
        cost = {i: sum(r["cells"][i]["cost"] for r in ps) / m for i in ids}
        best = max(ids, key=lambda i: score[i])
        t: VM = {
            "id": tid,
            "name": name,
            "n": m,
            "k": k,
            "score": score,
            "cost": cost,
            "best": best,
            "est": {i: any(r["cells"][i]["est"] for r in ps) for i in ids},
            "not_right": {i: m - k[i] for i in ids},
            "not_right_hl": _extremes({i: m - k[i] for i in ids}, lower_is_better=True),
            "route": None,
            "alt": None,
        }
        if SEARCH in ids and SELECT in ids:
            d = stats.route_for_type(
                [r["cells"][SEARCH]["grade"] == "c" for r in ps],
                [r["cells"][SELECT]["grade"] == "c" for r in ps],
            )
            t["route"] = {
                "choice": d.choice,
                "status": d.status,
                "p": d.p,
                "search_only": d.search_only,
                "select_only": d.select_only,
            }
            gap = score[best] - score[d.choice]
            if best not in KEENABLE and gap > ALT_NOTE_POINTS:
                t["alt"] = {"provider": best, "points": round(gap)}
        out.append(t)
    return out


def _guide(types: Sequence[VM], ids, labels, summary, rows) -> VM:
    routable = all(t["route"] for t in types)
    if not routable:
        return {"available": False, "volumes": _volume_rows(ids, summary, None)}
    n = sum(t["n"] for t in types)
    mix = sum(t["n"] * t["cost"][t["route"]["choice"]] for t in types) / n
    mix_est = any(t["est"][t["route"]["choice"]] for t in types)
    to_select = [t["name"].lower() for t in types if t["route"]["choice"] == SELECT]
    fallback = next((i for i in ids if i not in KEENABLE), None)
    return {
        "available": True,
        "routes": [
            {"type": t["id"], "name": t["name"], "n": t["n"], **t["route"], "alt": t["alt"]} for t in types
        ],
        "to_select": to_select,
        "config": _router_config(types, fallback),
        "fallback": fallback,
        "volumes": _volume_rows(ids, summary, (mix, mix_est)),
    }


def _router_config(types: Sequence[VM], fallback: str | None) -> str:
    arm = {SEARCH: "keenable-search", SELECT: "keenable-select"}
    lines = [
        "from searchaudit.router import JevRouter",
        "",
        "router = JevRouter(",
        '    classify="typesafe/jev-router",  # via OpenRouter',
        "    routes={",
    ]
    for t in types:
        r = t["route"]
        why = {"clear": f"clear, p {_p(r['p'])}", "lean": "lean, re-test", "too_few": "too few prompts"}[
            r["status"]
        ]
        lines.append(f'        "{t["id"]}": "{arm[r["choice"]]}",  # {why}')
    lines += ["    },", '    default="keenable-search",']
    if fallback:
        lines.append(f'    fallback="{fallback}",  # on provider error')
    lines += [")", "results = router.search(prompt)"]
    return "\n".join(lines)


def _p(p: float) -> str:
    return "< 0.001" if p < 0.001 else f"= {p:.3f}"


def _volume_rows(ids, summary, mix: tuple[float, bool] | None) -> list[VM]:
    rows = [{"id": i, "per_prompt": summary[i]["per_prompt"], "est": summary[i]["est"]} for i in ids]
    if mix:
        rows.append({"id": "router", "per_prompt": mix[0], "est": mix[1]})
    hl = _extremes({r["id"]: round(r["per_prompt"], 4) for r in rows}, lower_is_better=True)
    for r in rows:
        r["cells"] = [
            {"volume": v.volume, "usd": v.usd, "est": v.estimated}
            for v in stats.cost_at_volume(r["per_prompt"], r["est"])
        ]
        r["hl"] = hl.get(r["id"], "")
    return rows


# ---------------------------------------------------------------- head-to-head


def _head_to_head(ids, correct, outcomes, labels) -> dict[str, VM]:
    out: dict[str, VM] = {}
    prompt_ids = list(range(len(outcomes)))
    for seed, (a, b) in enumerate(permutations(ids, 2)):
        ca, cb = correct[a], correct[b]
        only_a = [i for i in prompt_ids if ca[i] and not cb[i]]
        only_b = [i for i in prompt_ids if cb[i] and not ca[i]]
        p = stats.mcnemar_exact(len(only_a), len(only_b))
        ci = stats.paired_bootstrap_diff(ca, cb, seed=seed)
        if p < stats.ALPHA:
            verdict = f"{labels[a] if len(only_a) > len(only_b) else labels[b]} is more accurate"
        else:
            verdict = "No clear accuracy difference"
        out[f"{a}|{b}"] = {
            "a": a,
            "b": b,
            "both": sum(1 for i in prompt_ids if ca[i] and cb[i]),
            "neither": sum(1 for i in prompt_ids if not ca[i] and not cb[i]),
            "only_a_idx": only_a,
            "only_b_idx": only_b,
            "p": p,
            "significant": p < stats.ALPHA,
            "verdict": verdict,
            "diff": ci.diff,
            "diff_low": ci.low,
            "diff_high": ci.high,
            "cheaper": sum(1 for o in outcomes if o[a].cost < o[b].cost),
            "faster": sum(1 for o in outcomes if o[a].seconds < o[b].seconds),
        }
    return out


def _default_pair(by_score, by_accuracy, providers) -> list[str]:
    a = by_score[0]
    today = next((p["id"] for p in providers if p["uses_today"] and p["id"] != a), None)
    b = today or next(i for i in by_accuracy if i != a)
    return [a, b]


# ---------------------------------------------------------------- failures and judge


def _failures(rows: Sequence[VM], ids) -> VM:
    matrix = []
    for mode, (name, definition) in FAILURE_MODES.items():
        counts = {i: [r["id"] for r in rows if r["cells"][i]["failure"] == mode] for i in ids}
        matrix.append(
            {
                "mode": mode,
                "name": name,
                "definition": definition,
                "count": {i: len(v) for i, v in counts.items()},
                "example": {i: (v[0] if v else None) for i, v in counts.items()},
                "hl": _extremes({i: len(v) for i, v in counts.items()}, lower_is_better=True),
            }
        )
    totals = {i: sum(1 for r in rows if r["cells"][i]["failure"]) for i in ids}
    return {"matrix": matrix, "totals": totals}


def _judge(results: AuditResults, rows: Sequence[VM], ids) -> VM:
    meta = results.meta
    models = Counter(j.judge_model for j in results.judgments if j.judge_model)
    inconsistent = [
        {"prompt": r["id"], "text": r["text"], "provider": i, "grade": c["grade"], "expected": c["expected"]}
        for r in rows
        for i in ids
        for c in [r["cells"][i]]
        if not c["missing"] and c["grade"] != c["expected"]
    ]
    splits = sorted(rows, key=lambda r: (-_split(r["cells"][i]["grade"] for i in ids), r["id"]))
    splits = [r for r in splits if r["disagree"]][:MAX_LISTED]
    return {
        "model": meta.judge_model,
        "models": dict(models),
        "answers": sum(1 for r in rows for i in ids if not r["cells"][i]["missing"]),
        "missing": sum(1 for r in rows for i in ids if r["cells"][i]["missing"]),
        "human_agreement": meta.human_agreement,
        "human_labels_n": meta.human_labels_n,
        "inconsistent": inconsistent,
        "splits": [
            {
                "id": r["id"],
                "text": r["text"],
                "type_name": r["type_name"],
                "grades": {i: r["cells"][i]["grade"] for i in ids},
            }
            for r in splits
        ],
    }


def _split(grades: Iterable[str]) -> float:
    """How evenly providers split between correct and not (1 = half/half)."""
    gs = list(grades)
    share = sum(1 for g in gs if g == "c") / len(gs)
    return 1 - abs(share - 0.5) * 2


# ---------------------------------------------------------------- meta and client payload


def _meta(results: AuditResults, n: int, providers: int) -> VM:
    m = results.meta
    return {
        "slug": m.slug,
        "company": m.company,
        "category": m.category,
        "created_at": m.created_at,
        "date": m.created_at[:10],
        "agent_model": m.agent_model,
        "agent_model_note": m.agent_model_note,
        "judge_model": m.judge_model,
        "budget": m.budget,
        "total_cost_usd": m.total_cost_usd,
        "notes": m.notes,
        "n": n,
        "providers": providers,
    }


def _client(company, ids, providers, rows, summary, ties, kind, weights: Weights, n) -> VM:
    """The minimum report.js needs to recompute value scores, the ranking and the headline."""
    return {
        "company": company,
        "ids": ids,
        "labels": {p["id"]: p["label"] for p in providers},
        "n": n,
        "k": {i: summary[i]["k"] for i in ids},
        "perPrompt": {i: summary[i]["per_prompt"] for i in ids},
        "leader": ties.leader,
        "tied": sorted(ties.tied),
        "headlineFixed": kind == "insufficient",
        "weights": {"a": weights.accuracy, "c": weights.cost, "s": weights.speed},
        "prompts": [
            {
                "id": r["id"],
                "g": [r["cells"][i]["grade"] for i in ids],
                "c": [r["cells"][i]["cost"] for i in ids],
                "t": [r["cells"][i]["seconds"] for i in ids],
            }
            for r in rows
        ],
    }
