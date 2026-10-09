"""AuditResults -> report view model.

The report is a pure function of the stored results: this module computes every number, test and
sentence once, and the templates only lay them out. The same model feeds the web page, the print
view (PDF) and the per-prompt pages, so they never disagree. The output is plain JSON-serializable
data (dicts, lists, str, finite floats, None).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import permutations
from typing import Any
from urllib.parse import urlparse

from searchaudit import stats
from searchaudit.models import BASELINE, FAILURE_MODES, PROMPT_TYPE_NAMES, RUBRIC, AuditResults, Judgment, Run
from searchaudit.report.chart import pareto_chart
from searchaudit.report.format import secs
from searchaudit.stats import DEFAULT_WEIGHTS, Outcome, Weights

KEENABLE = ("keen", "select", "routed")
SEARCH, SELECT = "keen", "select"
ALT_NOTE_POINTS = 5  # another provider must beat Keenable's route by this many score points to get a note
MAX_LISTED = 8  # rows in the judge's "split decisions" list

VM = dict[str, Any]
log = logging.getLogger(__name__)


def build_view_model(results: AuditResults, weights: Weights = DEFAULT_WEIGHTS) -> VM:
    providers = [p.model_dump() for p in results.providers]
    for p in providers:
        note = p.get("price_note") or ""
        if p["id"] in {"keen", "select", "routed"} and note.startswith("~"):
            p["price_note"] = note[1:]
    ids = [p["id"] for p in providers]
    labels = {p["id"]: p["label"] for p in providers}
    prompts = sorted(results.prompts, key=lambda p: p.id)
    n = len(prompts)
    cells = _cells(results, ids)
    outcomes = [{pid: _outcome(cells[(p.id, pid)]) for pid in ids} for p in prompts]
    correct = {pid: [cells[(p.id, pid)]["grade"] == "c" for p in prompts] for pid in ids}
    # The no-search baseline is shown like any provider, but never ranked for the headline, facts or guide.
    base = [i for i in ids if i == BASELINE]
    sids = [i for i in ids if i not in base] or ids
    # No-search stays out of the page. Search, SELECT, and routed stay in.
    shown = [i for i in ids if i != BASELINE] or list(ids)
    skip = frozenset(base)

    summary = _summary(prompts, ids, cells, outcomes, weights, skip)
    by_accuracy = sorted(ids, key=lambda i: (-summary[i]["k"], _or_inf(summary[i]["per_correct"]), labels[i]))
    by_score = sorted(ids, key=lambda i: (-summary[i]["score"], -summary[i]["k"], summary[i]["per_prompt"]))
    s_accuracy = [i for i in by_accuracy if i in sids]
    s_score = [i for i in by_score if i in sids]
    ties = stats.accuracy_ties(correct, s_accuracy)
    front = stats.pareto_front({i: (summary[i]["acc"], summary[i]["per_prompt"]) for i in ids})
    failed = {i: summary[i]["errors"] for i in sids}
    head = stats.headline(
        results.meta.company, labels, n, {i: summary[i]["k"] for i in ids}, s_score, ties, failed
    )

    prompt_rows = _prompt_rows(prompts, ids, cells, outcomes, weights, skip)
    tiers = _tiers(prompts, ids, sids, labels, cells, outcomes, weights, skip)
    types = _types(prompt_rows, ids, sids)
    guide = _guide(types, sids, labels, summary, prompt_rows)
    lift = _lift(prompts, sids, base, correct, labels, ties.leader, n)
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
        "shown": shown,
        "leaders": _metric_leaders(shown, summary),
        "reads": _reads(prompts, ids, cells, shown, summary, labels, correct),
        "keenable_request_usd": _keenable_unit(cells, prompts, ids),
        "rank_accuracy": [i for i in by_accuracy if i in shown],
        "rank_cost": sorted(shown, key=lambda i: (summary[i]["per_prompt"], -summary[i]["k"], labels[i])),
        "rank_speed": sorted(shown, key=lambda i: (summary[i]["t50"], -summary[i]["k"], labels[i])),
        "rubric": RUBRIC,
        "failure_modes": {k: {"name": v[0], "definition": v[1]} for k, v in FAILURE_MODES.items()},
        "weights": {"a": weights.accuracy, "c": weights.cost, "s": weights.speed},
        "n": n,
        "headline": {"kind": head.kind, "text": head.text},
        "facts": _facts({i: summary[i] for i in sids}, ties, labels, n) + lift["facts"],
        "lift": lift,
        "baseline": base[0] if base else None,
        "tiers": tiers,
        "economics": _economics(prompts, ids, labels, cells, results.providers),
        "sources_view": _sources_view(prompts, ids, cells),
        "summary": summary,
        "highlight": {
            "score": _extremes({i: round(summary[i]["score"], 1) for i in ids}, lower_is_better=False),
            "k": _accuracy_marks({i: summary[i]["k"] for i in ids}, set(ties.tied)),
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
        "h2h": (h2h := _head_to_head(ids, correct, outcomes, labels, [r["type_name"] for r in prompt_rows])),
        "h2h_default": (h2h_default := _default_pair(
            [i for i in s_score if i in shown] or shown,
            [i for i in s_accuracy if i in shown] or shown,
            [p for p in providers if p["id"] in shown],
            shown,
        )),
        # The Keenable arm that wins the most prompts on its own, against the most accurate other
        # provider. Printed next to the default pair so a category win is not lost in the average.
        "h2h_spotlight": _spotlight_pair(
            h2h, h2h_default, [i for i in s_accuracy if i in shown] or shown,
            next((p["id"] for p in providers if p["uses_today"] and p["id"] in shown), None),
        ),
        "failures": _failures(prompt_rows, ids),
        "guide": guide,
        "judge": _judge(results, prompt_rows, ids),
        "client": _client(
            results.meta.company, ids, providers, prompt_rows, summary, ties, head.kind, weights, n, base
        ),
    }


# ---------------------------------------------------------------- cells


_URL_RE = re.compile(r"https?://[^\s)\]\"'<>,]+")
INDEX_AS_OF = frozenset({"keen", "routed"})


def _clean_url(url: str) -> str:
    return url.rstrip(".,);>")


def _ref_urls(text: str) -> list[str]:
    seen: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        url = _clean_url(raw)
        if url not in seen:
            seen.append(url)
    return seen


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
    # A prompt with no run is not a tool error: it is not counted under any failure mode.
    failure = "" if run is None else ("error" if j is None else j.failure_mode)
    if run is None:
        note = ("No run on this prompt: one Keenable arm failed and the router replay needs a finished answer "
                "from both arms. Counted as wrong, in no failure mode.")
    elif j is None:
        note = "This answer was not judged; counted as wrong."
    else:
        note = j.note
    checks = [False] * len(RUBRIC) if missing else list(j.checks)
    cost = (run.cost_usd or run.cost_search_usd + run.cost_model_usd) if run else 0.0
    citations = run.citations if run else []
    calls = run.tool_calls if run else []
    fetched_urls: list[str] = []
    for call in calls:
        if call.tool != "fetch":
            continue
        for url in call.urls:
            cleaned = _clean_url(url)
            if cleaned not in fetched_urls:
                fetched_urls.append(cleaned)
    fetches = run.fetches if run else 0
    return {
        "grade": grade,
        "checks": checks,
        # The four-check rubric rule. A checklist judgment (facts present) is graded from its facts,
        # so its grade is its own rule and never "breaks" the four checks.
        "expected": grade if (j is not None and j.facts) else _expected_grade(checks),
        "checklist": bool(j is not None and j.facts),
        "failure": failure,
        "failure_name": FAILURE_MODES[failure][0] if failure else ("No run" if run is None else ""),
        "failure_def": FAILURE_MODES[failure][1] if failure else "",
        "note": note,
        "cost": round(cost, 6),
        "est": bool(run and run.cost_estimated),
        "seconds": round(run.latency_ms / 1000, 2) if run else 0.0,
        "searches": run.searches if run else 0,
        "fetches": run.fetches if run else 0,
        "cost_search": round(run.cost_search_usd, 6) if run else 0.0,
        "answer": run.answer if run else "",
        "error": run.error if run else "missing run",
        "budget_hit": bool(run and run.budget_hit),
        "via": run.via if run else None,
        "confidence": run.route_confidence if run else None,
        "model": run.model if run else "",
        "sources": [{"url": _clean_url(c.url), "title": c.title, "domain": _domain(c.url)} for c in citations],
        "hits": [
            [_clean_url(url) for url in t.urls[:8]]
            for t in calls
            if t.tool in ("search", "select") and t.urls
        ][:3],
        "fetched_urls": fetched_urls,
        "fetch_urls_reported": not (fetches > 0 and not fetched_urls),
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


def _metric_leaders(shown: Sequence[str], summary: Mapping[str, VM]) -> dict[str, list[str]]:
    """Who leads each metric. Cost and speed leaders are taken from correct answers only."""
    acc = max((summary[i]["k"] for i in shown), default=0)
    costs = {i: summary[i]["per_correct"] for i in shown if summary[i]["per_correct"] is not None}
    speeds = {i: summary[i]["t50_correct"] for i in shown if summary[i]["t50_correct"] is not None}
    best_cost = min(costs.values()) if costs else None
    best_speed = min(speeds.values()) if speeds else None
    return {
        "acc": [i for i in shown if summary[i]["k"] == acc],
        "per_correct": [i for i, v in costs.items() if v == best_cost],
        "speed": [i for i, v in speeds.items() if v == best_speed],
    }


def _keenable_unit(cells, prompts, ids: Sequence[str]) -> float | None:
    """List price per Keenable request, from the search spend actually recorded."""
    rates = []
    for pid in ("keen", "select"):
        if pid not in ids:
            continue
        for p in prompts:
            cell = cells[(p.id, pid)]
            requests = cell["searches"] + cell["fetches"]
            if requests and cell["cost_search"] > 0:
                rates.append(cell["cost_search"] / requests)
    if not rates:
        return None
    return round(stats.median(rates), 4)


def _extremes(values: Mapping[str, float], lower_is_better: bool) -> dict[str, str]:
    """'best' / 'worst' marks for a row of numbers (nothing when all are equal)."""
    if len(values) < 2:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if lo == hi:
        return {}
    best, worst = (lo, hi) if lower_is_better else (hi, lo)
    return {i: "best" if v == best else "worst" if v == worst else "" for i, v in values.items()}


def _accuracy_marks(k: Mapping[str, float], tied: set[str]) -> dict[str, str]:
    """Best/worst on correct counts, except a statistical tie is never also marked worst."""
    marks = _extremes(k, lower_is_better=False)
    return {i: "" if mark == "worst" and i in tied else mark for i, mark in marks.items()}


# ---------------------------------------------------------------- summary


def _summary(prompts, ids, cells, outcomes, weights: Weights, skip=frozenset()) -> dict[str, VM]:
    n = len(prompts)
    scores = stats.value_scores(outcomes, weights, skip)
    out: dict[str, VM] = {}
    for pid in ids:
        xs = [cells[(p.id, pid)] for p in prompts]
        grades = Counter(x["grade"] for x in xs)
        k = grades["c"]
        total = sum(x["cost"] for x in xs)
        times = [x["seconds"] for x in xs]
        correct_times = [x["seconds"] for x in xs if x["grade"] == "c"]
        spread = stats.time_spread(times)
        spread_ok = stats.time_spread(correct_times)
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
            "t50": spread["median"] or 0.0,
            "t_mean": spread["mean"] or 0.0,
            "p50": spread["p50"] or 0.0,
            "t90": spread["p90"] or 0.0,
            "p99": spread["p99"] or 0.0,
            "t50_correct": spread_ok["median"],
            "t_mean_correct": spread_ok["mean"],
            "p50_correct": spread_ok["p50"],
            "p90_correct": spread_ok["p90"],
            "p99_correct": spread_ok["p99"],
            "search_per_prompt": sum(x["cost_search"] for x in xs) / n if n else 0.0,
            "model_per_prompt": sum(x["cost"] - x["cost_search"] for x in xs) / n if n else 0.0,
            "failed_calls": sum(1 for x in xs for t in x["trace"] if not t["ok"]),
            "searches_median": stats.median([x["searches"] for x in xs]),
            "cost_max": max((x["cost"] for x in xs), default=0.0),
            "searches": sum(x["searches"] for x in xs) / n if n else 0.0,
            "fetches": sum(x["fetches"] for x in xs) / n if n else 0.0,
            "errors": sum(1 for x in xs if x["error"] or x["failure"] == "error"),
            "backed": sum(1 for x in xs if len(x["checks"]) > 2 and x["checks"][2]),
            "score": scores.get(pid, 0.0),
        }
    return out


def _reads(prompts, ids, cells, shown, summary, labels, correct) -> dict[str, dict[str, str]]:
    """One headline and one paragraph per view. Paired tests use Exa when it was run."""
    ref = "exa" if "exa" in shown else None
    others = [i for i in shown if i != ref]

    def series(pid: str, key: str) -> list[float]:
        return [cells[(p.id, pid)][key] for p in prompts]

    def kinds(pid: str) -> Counter:
        out: Counter = Counter()
        for p in prompts:
            for t in cells[(p.id, pid)]["trace"]:
                if t["ok"]:
                    continue
                err = t["error"] or ""
                if "Timeout" in err or "budget" in err:
                    out["budget"] += 1
                elif "429" in err:
                    out["rate"] += 1
                elif "SQL" in err or "Binder" in err or "Parser" in err:
                    out["sql"] += 1
                elif t["tool"] == "fetch" or "/v1/fetch" in err or "during fetch" in err:
                    out["fetch"] += 1
                else:
                    out["other"] += 1
        return out

    def against(key: str) -> dict[str, float]:
        if ref is None:
            return {}
        base = series(ref, key)
        raw = [stats.wilcoxon(series(i, key), base) for i in others]
        return dict(zip(others, stats.holm(raw), strict=True))

    cost_p, time_p, search_p = against("cost"), against("seconds"), against("searches")
    acc_p: dict[str, float] = {}
    if ref is not None:
        raw = []
        for i in others:
            only_i, only_ref = stats.discordant(correct[i], correct[ref])
            raw.append(stats.mcnemar_exact(only_i, only_ref))
        acc_p = dict(zip(others, stats.holm(raw), strict=True))

    def name(pid: str) -> str:
        return labels[pid]

    def ptext(p: float) -> str:
        if p < 0.001:
            return "p < 0.001"
        if p < 0.01:
            return f"p = {p:.3f}"
        return f"p = {p:.2f}"

    def paired(pid: str, pmap: dict[str, float], field: str, more: str, less: str) -> str:
        if pid not in pmap or ref is None:
            return ""
        p = pmap[pid]
        if p > 0.05:
            return f"{name(pid)} is not distinguishable from {name(ref)} ({ptext(p)})."
        here, there = summary[pid][field], summary[ref][field]
        if here == there:
            return f"{name(pid)} matches {name(ref)} on this measure ({ptext(p)})."
        word = more if here > there else less
        return f"{name(pid)} {word} {name(ref)} ({ptext(p)})."

    dearest = max(shown, key=lambda i: summary[i]["cost_max"])
    cost_bits = [paired(i, cost_p, "per_prompt", "is more expensive than", "is cheaper than") for i in others]
    cost_bits = [b for b in cost_bits if b]
    cost_body = " ".join(cost_bits)
    cost_body += (
        f" The dearest single prompt is {name(dearest)} at ${summary[dearest]['cost_max']:.2f}."
    )
    if ref and dearest != ref:
        cost_body += f" {name(ref)}'s dearest prompt is ${summary[ref]['cost_max']:.2f}."

    time_bits = [paired(i, time_p, "t50", "is slower than", "is faster than") for i in others]
    time_body = " ".join(b for b in time_bits if b)
    time_body += " A hollow dot means the answer was wrong. It is not a time."

    acc_bits = []
    for i in others:
        if i not in acc_p:
            continue
        gap = summary[i]["k"] - summary[ref]["k"]
        if acc_p[i] > 0.05:
            acc_bits.append(f"{name(i)} is not distinguishable from {name(ref)} ({ptext(acc_p[i])}).")
        elif gap < 0:
            acc_bits.append(f"{name(i)} got {abs(gap)} fewer prompts right than {name(ref)} ({ptext(acc_p[i])}).")
        else:
            acc_bits.append(f"{name(i)} got {gap} more prompts right than {name(ref)} ({ptext(acc_p[i])}).")
    acc_body = " ".join(acc_bits) or f"{name(shown[0])} got {summary[shown[0]]['k']} of {summary[shown[0]]['n']} right."
    acc_body += " McNemar, Holm-adjusted, correct against not correct."

    fail_bits = []
    for i in shown:
        knd = kinds(i)
        total = sum(knd.values())
        if total == 0:
            fail_bits.append(f"{name(i)} had no failed tool call.")
            continue
        parts = []
        if knd["budget"]:
            parts.append(f"{knd['budget']} hit the time budget")
        if knd["sql"]:
            parts.append(f"{knd['sql']} were SQL errors")
        if knd["fetch"]:
            parts.append(f"{knd['fetch']} were page fetches that returned an error")
        if knd["rate"]:
            parts.append(f"{knd['rate']} were rate limits")
        if knd["other"]:
            parts.append(f"{knd['other']} were other errors")
        fail_bits.append(f"{name(i)}: {total} failed calls ({', '.join(parts)}).")
    fail_body = " ".join(fail_bits)
    fail_body += (
        " A failed call is priced at $0 here. Keenable's pay-per-request path does not charge an HTTP 400 or above."
        " A keyed call reports what it cost on the response, and this audit did not read that field, so $0 is not a receipt."
    )

    search_bits = [paired(i, search_p, "searches_median", "uses more searches than", "uses fewer searches than") for i in others]
    search_body = " ".join(b for b in search_bits if b)
    if "select" in shown and ref:
        search_body += (
            f" SELECT's median is {summary['select']['searches_median']:g} searches and"
            f" {name(ref)}'s is {summary[ref]['searches_median']:g}."
            " One SQL query did not replace the loop on this set."
        )
    elif not search_body:
        search_body = "Search count is how many times the agent called search before it answered."

    def title_cost() -> str:
        cheap = [i for i in shown if summary[i]["per_correct"] is not None]
        if not cheap:
            return "Cost"
        best = min(cheap, key=lambda i: summary[i]["per_correct"])
        return f"{name(best)} is the lowest dollars per correct answer, ${summary[best]['per_correct']:.4f}"

    def title_time() -> str:
        ok = [i for i in shown if summary[i]["t50_correct"] is not None]
        if not ok:
            return "Time"
        best = min(ok, key=lambda i: summary[i]["t50_correct"])
        return f"{name(best)} is fastest when the answer is right, median {secs(summary[best]['t50_correct'])}"

    def title_acc() -> str:
        best = max(shown, key=lambda i: summary[i]["k"])
        tied = [i for i in shown if summary[i]["k"] == summary[best]["k"]]
        who = " and ".join(name(i) for i in tied[:3])
        return f"{who} got the most prompts right, {summary[best]['k']} of {summary[best]['n']}"

    worst_fail = max(shown, key=lambda i: summary[i]["failed_calls"])
    fail_title = (
        f"{name(worst_fail)} had {summary[worst_fail]['failed_calls']} failed tool calls"
        if summary[worst_fail]["failed_calls"]
        else "No provider had a failed tool call"
    )
    fewest = min(shown, key=lambda i: summary[i]["searches_median"])
    search_title = f"{name(fewest)} uses the fewest searches, median {summary[fewest]['searches_median']:g}"

    note = " Wilcoxon signed-rank against Exa, Holm-adjusted." if ref else ""
    return {
        "cost": {"title": title_cost(), "body": (cost_body + note).strip()},
        "time": {"title": title_time(), "body": (time_body + note).strip()},
        "acc": {"title": title_acc(), "body": acc_body.strip()},
        "fail": {"title": fail_title, "body": fail_body.strip()},
        "search": {"title": search_title, "body": (search_body + note).strip()},
    }


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
            "value": f"{labels[cheapest]}: {_money(summary[cheapest]['per_prompt'], False)} per prompt",
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


def _prompt_rows(prompts, ids, cells, outcomes, weights: Weights, skip=frozenset()) -> list[VM]:
    rows = []
    for p, oc in zip(prompts, outcomes, strict=True):
        row_cells = {pid: dict(cells[(p.id, pid)]) for pid in ids}
        scores = stats.prompt_scores(oc, weights, skip)
        domain_count = Counter(d for c in row_cells.values() for d in {s["domain"] for s in c["sources"]})
        cited = {pid: [_clean_url(s["url"]) for s in row_cells[pid]["sources"]] for pid in ids}
        owners: dict[str, set[str]] = {}
        for pid, urls in cited.items():
            for url in urls:
                owners.setdefault(url, set()).add(pid)
        reference_urls = _ref_urls(p.reference)
        for pid, c in row_cells.items():
            c["score"] = scores[pid]
            mine = set(cited[pid])
            c["missed"] = [url for url in reference_urls if url not in mine]
            c["only_here"] = [url for url in cited[pid] if owners[url] == {pid}]
            c["fetched_uncited"] = [url for url in c["fetched_urls"] if url not in mine]
            if p.query_time and pid in INDEX_AS_OF:
                c["search_mode"] = "index-as-of"
            elif p.query_time:
                c["search_mode"] = "live search"
            else:
                c["search_mode"] = ""
            for s in c["sources"]:
                s["unique"] = domain_count[s["domain"]] == 1
        rankable = [i for i in ids if i not in {BASELINE, "routed"}] or list(ids)
        # A wrong answer does not win the prompt, however cheap or fast it was.
        passed = [i for i in rankable if row_cells[i]["grade"] == "c"]
        best = max(passed, key=lambda i: (scores[i], -row_cells[i]["cost"])) if passed else None
        cheapest = min(passed, key=lambda i: (row_cells[i]["cost"], row_cells[i]["seconds"])) if passed else None
        fastest = min(passed, key=lambda i: (row_cells[i]["seconds"], row_cells[i]["cost"])) if passed else None
        grades = {row_cells[i]["grade"] for i in rankable}

        def marks(values: dict, lower: bool) -> dict:
            picked = _extremes({i: values[i] for i in rankable}, lower_is_better=lower)
            return {i: picked.get(i, "") for i in ids}
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
                "passed": passed,
                "best_value": best,
                "best_cost": cheapest,
                "fastest": fastest,
                "highlight": {
                    "score": marks({i: round(scores[i], 3) for i in ids}, False),
                    "seconds": marks({i: row_cells[i]["seconds"] for i in ids}, True),
                    "cost": marks({i: round(row_cells[i]["cost"], 4) for i in ids}, True),
                    "searches": marks({i: row_cells[i]["searches"] for i in ids}, True),
                },
                "disagree": len(grades) > 1,
                "nobody": all(row_cells[i]["grade"] != "c" for i in rankable),
                "routed_via": row_cells["routed"]["via"] if "routed" in row_cells else None,
            }
        )
    return rows


# ---------------------------------------------------------------- prompt types and the guide


def _types(rows: Sequence[VM], ids: Sequence[str], sids: Sequence[str] | None = None) -> list[VM]:
    sids = list(sids or ids)  # the best provider of a type is never the no-search baseline
    out = []
    for tid, name in PROMPT_TYPE_NAMES.items():
        ps = [r for r in rows if r["type"] == tid]
        if not ps:
            continue
        m = len(ps)
        k = {i: sum(1 for r in ps if r["cells"][i]["grade"] == "c") for i in ids}
        score = {i: 100 * sum(r["cells"][i]["score"] for r in ps) / m for i in ids}
        cost = {i: sum(r["cells"][i]["cost"] for r in ps) / m for i in ids}
        best = max(sids, key=lambda i: score[i])
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
    rivals = [i for i in ids if i not in KEENABLE and i in summary]
    fallback = max(rivals, key=lambda i: summary[i]["k"]) if rivals else None
    per_prompt = None
    if "routed" in summary and SEARCH in summary and SELECT in summary:
        r, k, s = summary["routed"]["k"], summary[SEARCH]["k"], summary[SELECT]["k"]
        if r > max(k, s):
            per_prompt = (f"Per-prompt routing did better than this per-type rule: Jev choosing per prompt "
                          f"(the Keenable router column) got {r} correct against {k} for search alone and "
                          f"{s} for SELECT alone. The per-type rule above is the conservative version; "
                          f"the per-prompt choice is what the router column measured.")
    return {
        "available": True,
        "per_prompt_note": per_prompt,
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
        '    classify="typesafe/jev-router",  # the Jev decision model, via OpenRouter',
        "    routes={",
    ]
    for t in types:
        r = t["route"]
        why = {"clear": f"clear, p {_p(r['p'])}", "lean": "no clear SELECT win, search by default",
               "too_few": "too few prompts"}[
            r["status"]
        ]
        lines.append(f'        "{t["id"]}": "{arm[r["choice"]]}",  # {why}')
    lines += ["    },", '    default="keenable-search",']
    if fallback:
        lines.append(f'    fallback="{fallback}",  # the most accurate other provider, used on a Keenable error')
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


def _head_to_head(ids, correct, outcomes, labels, type_names: Sequence[str] = ()) -> dict[str, VM]:
    out: dict[str, VM] = {}
    prompt_ids = list(range(len(outcomes)))
    type_names = list(type_names) or [""] * len(prompt_ids)
    for seed, (a, b) in enumerate(permutations(ids, 2)):
        ca, cb = correct[a], correct[b]
        only_a = [i for i in prompt_ids if ca[i] and not cb[i]]
        only_b = [i for i in prompt_ids if cb[i] and not ca[i]]
        by_type = []
        for name in dict.fromkeys(type_names):
            idx = [i for i in prompt_ids if type_names[i] == name]
            by_type.append({
                "name": name, "n": len(idx),
                "both": sum(1 for i in idx if ca[i] and cb[i]),
                "only_a": sum(1 for i in idx if i in set(only_a)),
                "only_b": sum(1 for i in idx if i in set(only_b)),
            })
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
            "by_type": by_type,
        }
    return out


def _spotlight_pair(h2h: dict[str, VM], default: Sequence[str], by_accuracy: Sequence[str],
                    today: str | None = None) -> list[str] | None:
    """[keenable arm, rival]: the Keenable arm with the most prompts only it got right, against the
    provider the company uses today, else Exa, else the most accurate other provider. None when that is
    already the default pair or there is nothing to show."""
    rivals = [i for i in by_accuracy if i not in KEENABLE]
    arms = [i for i in by_accuracy if i in KEENABLE]
    if not rivals or not arms:
        return None
    rival = today if today in rivals else ("exa" if "exa" in rivals else rivals[0])
    arm = max(arms, key=lambda a: (len(h2h[f"{a}|{rival}"]["only_a_idx"]), -by_accuracy.index(a)))
    pair = [arm, rival]
    if set(pair) == set(default) or not h2h[f"{arm}|{rival}"]["only_a_idx"]:
        return None
    return pair


def _default_pair(by_score, by_accuracy, providers, all_ids=()) -> list[str]:
    a = by_score[0]
    today = next((p["id"] for p in providers if p["uses_today"] and p["id"] != a), None)
    b = today or next((i for i in by_accuracy if i != a), None) or next(i for i in all_ids if i != a)
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
        "second": meta.second_judge,
        "checklist": any(r["cells"][i]["checklist"] for r in rows for i in ids),
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


def _client(company, ids, providers, rows, summary, ties, kind, weights: Weights, n, base=()) -> VM:
    """The minimum report.js needs to recompute value scores, the ranking and the headline."""
    return {
        "company": company,
        "ids": ids,
        "hex": {p["id"]: p["hex"] for p in providers},
        "labels": {p["id"]: p["label"] for p in providers},
        "n": n,
        "k": {i: summary[i]["k"] for i in ids},
        "perPrompt": {i: summary[i]["per_prompt"] for i in ids},
        "leader": ties.leader,
        "tied": sorted(ties.tied),
        "headlineFixed": kind == "insufficient",
        "baseline": list(base),
        "weights": {"a": weights.accuracy, "c": weights.cost, "s": weights.speed},
        "prompts": [
            {
                "id": r["id"],
                "type": r["type"],
                "g": [r["cells"][i]["grade"] for i in ids],
                "c": [r["cells"][i]["cost"] for i in ids],
                "t": [r["cells"][i]["seconds"] for i in ids],
                "q": [r["cells"][i]["searches"] for i in ids],
                "f": [sum(1 for t in r["cells"][i]["trace"] if not t["ok"]) for i in ids],
            }
            for r in rows
        ],
    }


TIER_NAMES = {"routine": "Routine prompts", "hard": "Hard prompts"}


def _tiers(prompts, ids, sids, labels, cells, outcomes, weights: Weights, skip=frozenset()) -> list[VM]:
    """Routine vs hard slices: accuracy ties and value winner within each slice (empty if one slice)."""
    present = [t for t in TIER_NAMES if any(p.tier == t for p in prompts)]
    if len(present) < 2:
        return []
    out: list[VM] = []
    for tier in present:
        idx = [i for i, p in enumerate(prompts) if p.tier == tier]
        sub = [prompts[i] for i in idx]
        summary = _summary(sub, ids, cells, [outcomes[i] for i in idx], weights, skip)
        by_accuracy = sorted(sids, key=lambda i: (-summary[i]["k"], summary[i]["per_prompt"], labels[i]))
        correct = {pid: [cells[(p.id, pid)]["grade"] == "c" for p in sub] for pid in ids}
        ties = stats.accuracy_ties(correct, by_accuracy)
        by_score = sorted(sids, key=lambda i: (-summary[i]["score"], -summary[i]["k"], summary[i]["per_prompt"]))
        tied = [i for i in by_accuracy if i in ties.tied]
        cheapest_tied = min(tied, key=lambda i: summary[i]["per_prompt"])
        if len(tied) == len(sids):
            takeaway = (f"All {len(sids)} providers tie on accuracy here, so price decides: "
                        f"{labels[cheapest_tied]} is cheapest.")
        elif len(tied) == 1:
            takeaway = (f"{labels[ties.leader]} is clearly most accurate here "
                        f"({summary[ties.leader]['k']} of {len(sub)}).")
        else:
            takeaway = (f"{', '.join(labels[i] for i in tied)} tie for most accurate; "
                        f"{labels[cheapest_tied]} is the cheapest of them.")
        out.append({
            "id": tier,
            "name": TIER_NAMES[tier],
            "n": len(sub),
            "k": {i: summary[i]["k"] for i in ids},
            "score": {i: summary[i]["score"] for i in ids},
            "per_prompt": {i: summary[i]["per_prompt"] for i in ids},
            "est": {i: summary[i]["est"] for i in ids},
            "tied": tied,
            "leader": ties.leader,
            "value_winner": by_score[0],
            "takeaway": takeaway,
            "k_hl": _accuracy_marks({i: summary[i]["k"] for i in ids}, set(ties.tied)),
        })
    return out


def _lift(prompts, sids, base, correct, labels, leader, n) -> VM:
    """Search lift: per provider, prompts it got right that the no-search baseline got wrong.

    Also a fact line for the search leader, and a plain warning when its gain over the model's memory
    is within chance (then the prompt set measures the model more than search)."""
    if not base:
        return {"available": False, "facts": []}
    b = base[0]
    tiers = [t for t in TIER_NAMES if any(p.tier == t for p in prompts)]
    rows = []
    for pid in sids:
        gained, lost = stats.discordant(correct[pid], correct[b])
        by_tier = {
            t: sum(1 for i, p in enumerate(prompts) if p.tier == t and correct[pid][i] and not correct[b][i])
            for t in tiers
        } if len(tiers) > 1 else {}
        rows.append({"id": pid, "gained": gained, "lost": lost, "tiers": by_tier})
    gained, lost = stats.discordant(correct[leader], correct[b])
    p = stats.mcnemar_exact(gained, lost)
    k_base, k_lead = sum(correct[b]), sum(correct[leader])
    if p < stats.ALPHA and gained > lost:
        detail = (f"{labels[b]} got {k_base} of {n} right from memory. Search is significantly better "
                  f"(McNemar p {_p(p)}).")
    else:
        detail = (f"{labels[b]} got {k_base} of {n} right from memory, within chance of {labels[leader]} "
                  f"({k_lead}): on this prompt set search added little over what the model already knows.")
    fact = {"key": "Search added", "ids": [leader],
            "value": f"{labels[leader]}: +{gained} prompts over no search", "detail": detail}
    return {"available": True, "id": b, "k": k_base, "n": n, "rows": rows, "tiers": tiers,
            "within_chance": not (p < stats.ALPHA and gained > lost), "p": p, "facts": [fact]}


# ---------------------------------------------------------------- cost break-even and sources


def _economics(prompts, ids, labels, cells, providers) -> VM | None:
    """Requests per prompt vs price per request, against the provider the company uses today.

    A provider is cheaper on search than the reference when its request ratio is below the
    reference's price-per-request ratio (e.g. Exa $7 / Keenable $4 = 1.75x).
    """
    searchers = [i for i in ids if i != "none"]
    ref = next((p.id for p in providers if p.uses_today and p.id in searchers), None)
    ref = ref or ("exa" if "exa" in searchers else (searchers[0] if searchers else None))
    if ref is None or not prompts:
        return None

    def agg(pid: str, ps) -> VM:
        xs = [cells[(p.id, pid)] for p in ps]
        req = sum(x["searches"] + x["fetches"] for x in xs)
        spend = sum(x["cost_search"] for x in xs)
        k = sum(1 for x in xs if x["grade"] == "c")
        return {
            "requests": req / len(xs),
            "price": spend / req if req else None,
            "search_per_prompt": spend / len(xs),
            "search_per_correct": spend / k if k else None,
            "requests_per_correct": req / k if k else None,
            "est": any(x["est"] for x in xs),
        }

    rows = {pid: agg(pid, prompts) for pid in searchers}
    base = rows[ref]

    def verdict(r: VM, b: VM) -> VM:
        if not r["price"] or not b["price"] or not b["requests"]:
            return {"price_ratio": None, "request_ratio": None, "cheaper": None}
        price_ratio = b["price"] / r["price"]
        request_ratio = r["requests"] / b["requests"]
        return {"price_ratio": price_ratio, "request_ratio": request_ratio,
                "cheaper": request_ratio < price_ratio}

    for pid in searchers:
        rows[pid].update(verdict(rows[pid], base))
    by_type = []
    for tid, name in PROMPT_TYPE_NAMES.items():
        ps = [p for p in prompts if p.type == tid]
        if not ps:
            continue
        tb = agg(ref, ps)
        by_type.append({"id": tid, "name": name, "n": len(ps),
                        "cells": {pid: {**agg(pid, ps), **verdict(agg(pid, ps), tb)} for pid in searchers}})
    return {"ref": ref, "ref_label": labels[ref], "ids": searchers, "rows": rows, "by_type": by_type}


def _sources_view(prompts, ids, cells) -> VM:
    """Which domains each provider's answers cite, and how much of that only it found."""
    by_pid: dict[str, Counter] = {pid: Counter() for pid in ids}
    for p in prompts:
        for pid in ids:
            for d in {s["domain"] for s in cells[(p.id, pid)]["sources"] if s["domain"]}:
                by_pid[pid][d] += 1
    owners: dict[str, set[str]] = {}
    for pid, c in by_pid.items():
        for d in c:
            owners.setdefault(d, set()).add(pid)
    out = {}
    for pid in ids:
        xs = [cells[(p.id, pid)] for p in prompts]
        wrong = [x for x in xs if x["grade"] == "w" and not x["missing"]]
        unique = sorted(d for d in by_pid[pid] if owners[d] == {pid})
        out[pid] = {
            "top": by_pid[pid].most_common(6),
            "distinct": len(by_pid[pid]),
            "unique": len(unique),
            "unique_examples": unique[:5],
            "per_answer": sum(len(x["sources"]) for x in xs) / len(xs) if xs else 0,
            "uncited": sum(1 for x in xs if not x["sources"]),
            "wrong_uncited": sum(1 for x in wrong if not x["sources"]),
            "wrong": len(wrong),
            "n": len(xs),
        }
    return out
