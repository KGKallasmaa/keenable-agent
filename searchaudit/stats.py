"""Statistics behind the report. Pure Python, no numpy.

Conventions:
- A provider is "correct" on a prompt only for grade "c". Paired tests (McNemar) use correct vs
  not-correct; partly-correct answers count as not correct there.
- For value scores a partly-correct answer earns half credit (GRADE_CREDIT).
- Provider ids are opaque strings here; this module does not import the results contract.

The JavaScript in searchaudit/web/static/report.js mirrors `prompt_scores` and `headline` so the
weight sliders can recompute in the browser. Change both together.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal

GRADE_CREDIT: dict[str, float] = {"c": 1.0, "p": 0.5, "w": 0.0}
ALPHA = 0.05
MIN_PROMPTS_OVERALL = 30  # below this the headline says "not enough evidence yet"
MIN_PROMPTS_PER_TYPE = 10  # below this a prompt type gets no route decision
MAX_FAILED_SHARE = 0.5  # a provider failing more runs than this makes the comparison unfair
EXAMPLE_VOLUMES: tuple[int, ...] = (100_000, 1_000_000, 10_000_000)


@dataclass(frozen=True)
class Outcome:
    """One provider's result on one prompt, as far as scoring is concerned."""

    grade: str  # "c" | "p" | "w"
    cost: float  # USD, total for the run
    seconds: float  # wall clock


@dataclass(frozen=True)
class Weights:
    accuracy: float = 60
    cost: float = 25
    speed: float = 15


DEFAULT_WEIGHTS = Weights()


# ---------------------------------------------------------------- basic tests


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes out of n."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value from the two discordant counts (binomial, p = 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, returned in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def discordant(a: Sequence[bool], b: Sequence[bool]) -> tuple[int, int]:
    """(prompts only a got right, prompts only b got right)."""
    only_a = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    return only_a, only_b


@dataclass(frozen=True)
class BootstrapCI:
    diff: float  # accuracy(a) - accuracy(b)
    low: float
    high: float


def paired_bootstrap_diff(
    a: Sequence[bool], b: Sequence[bool], resamples: int = 2000, seed: int = 0, level: float = 0.95
) -> BootstrapCI:
    """Percentile bootstrap CI for the paired accuracy difference a - b (resampling prompts)."""
    n = len(a)
    if n == 0:
        return BootstrapCI(0.0, 0.0, 0.0)
    d = [int(x) - int(y) for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(d, k=n)) / n for _ in range(resamples))
    tail = (1 - level) / 2
    lo = means[math.floor(tail * (resamples - 1))]
    hi = means[math.ceil((1 - tail) * (resamples - 1))]
    return BootstrapCI(sum(d) / n, lo, hi)


# ---------------------------------------------------------------- value score


def _ratio(best: float, x: float) -> float:
    return 1.0 if x <= 0 else min(1.0, best / x)


def prompt_scores(
    outcomes: Mapping[str, Outcome], w: Weights = DEFAULT_WEIGHTS, bar_exclude: AbstractSet[str] = frozenset()
) -> dict[str, float]:
    """Value score in [0, 1] per provider on one prompt.

    Wrong answers score 0. Otherwise credit x (wA + wC * cheapness + wS * speed) / (wA + wC + wS),
    where cheapness and speed are measured against the cheapest and fastest NON-WRONG answer on this
    prompt: a fast wrong answer never sets the bar (eng review R1). Providers in `bar_exclude` (the
    no-search baseline) never set the bar either, unless nobody else answered; a ratio is capped at 1,
    so adding the baseline cannot reorder the other providers.
    """
    live = [o for o in outcomes.values() if o.grade != "w"]
    setters = [o for pid, o in outcomes.items() if o.grade != "w" and pid not in bar_exclude] or live
    if not live:
        return {pid: 0.0 for pid in outcomes}
    min_cost = min(o.cost for o in setters)
    min_time = min(o.seconds for o in setters)
    total = (w.accuracy + w.cost + w.speed) or 1
    scores: dict[str, float] = {}
    for pid, o in outcomes.items():
        credit = GRADE_CREDIT[o.grade]
        if credit == 0:
            scores[pid] = 0.0
            continue
        value = w.accuracy + w.cost * _ratio(min_cost, o.cost) + w.speed * _ratio(min_time, o.seconds)
        scores[pid] = credit * value / total
    return scores


def value_scores(
    prompts: Sequence[Mapping[str, Outcome]], w: Weights = DEFAULT_WEIGHTS,
    bar_exclude: AbstractSet[str] = frozenset(),
) -> dict[str, float]:
    """Mean value score per provider over prompts, on a 0-100 scale."""
    if not prompts:
        return {}
    totals: dict[str, float] = {pid: 0.0 for pid in prompts[0]}
    for outcomes in prompts:
        for pid, s in prompt_scores(outcomes, w, bar_exclude).items():
            totals[pid] += s
    return {pid: 100 * t / len(prompts) for pid, t in totals.items()}


# ---------------------------------------------------------------- ranking


def pareto_front(points: Mapping[str, tuple[float, float]]) -> set[str]:
    """Providers not dominated on (accuracy: higher is better, cost: lower is better)."""

    def dominates(b: tuple[float, float], a: tuple[float, float]) -> bool:
        return b[0] >= a[0] and b[1] <= a[1] and (b[0] > a[0] or b[1] < a[1])

    return {a for a, pa in points.items() if not any(dominates(pb, pa) for b, pb in points.items() if b != a)}


@dataclass(frozen=True)
class Ties:
    leader: str
    tied: frozenset[str]  # includes the leader
    adjusted_p: dict[str, float]  # Holm-adjusted p vs the leader, per other provider


def accuracy_ties(correct: Mapping[str, Sequence[bool]], by_accuracy: Sequence[str]) -> Ties:
    """Which providers are not significantly less accurate than the leader (McNemar + Holm)."""
    leader, others = by_accuracy[0], list(by_accuracy[1:])
    raw = [mcnemar_exact(*discordant(correct[leader], correct[o])) for o in others]
    adj = holm(raw)
    tied = {leader} | {o for o, p in zip(others, adj, strict=True) if p >= ALPHA}
    return Ties(leader, frozenset(tied), dict(zip(others, adj, strict=True)))


# ---------------------------------------------------------------- routing


RouteStatus = Literal["clear", "lean", "too_few"]


@dataclass(frozen=True)
class RouteDecision:
    choice: str  # SEARCH or SELECT id
    status: RouteStatus
    p: float
    search_only: int  # prompts only search got right
    select_only: int


def route_for_type(
    search_correct: Sequence[bool],
    select_correct: Sequence[bool],
    search_id: str = "keen",
    select_id: str = "select",
) -> RouteDecision:
    """Paired test search vs SELECT on one prompt type (eng review R3).

    "clear" (p < 0.05) routes to the winner; otherwise "lean · re-test" and the default is search,
    which is cheaper and faster. Fewer than MIN_PROMPTS_PER_TYPE prompts: no decision, default search.
    """
    s_only, x_only = discordant(search_correct, select_correct)
    p = mcnemar_exact(s_only, x_only)
    if len(search_correct) < MIN_PROMPTS_PER_TYPE:
        return RouteDecision(search_id, "too_few", p, s_only, x_only)
    if p < ALPHA:
        return RouteDecision(select_id if x_only > s_only else search_id, "clear", p, s_only, x_only)
    return RouteDecision(search_id, "lean", p, s_only, x_only)


# ---------------------------------------------------------------- headline


HeadlineKind = Literal["insufficient", "tradeoff", "sole", "tie"]


@dataclass(frozen=True)
class Headline:
    kind: HeadlineKind
    text: str


def headline(
    company: str,
    labels: Mapping[str, str],
    n: int,
    correct: Mapping[str, int],
    by_score: Sequence[str],
    ties: Ties,
    failed: Mapping[str, int] | None = None,
) -> Headline:
    """The verdict sentence. Its words are computed from the tests, never hand-written (eng review R2)."""
    failed = failed or {}
    leader = ties.leader
    if n < MIN_PROMPTS_OVERALL:
        return Headline(
            "insufficient",
            f"Not enough evidence yet: {n} prompts is too few to rank search providers for {company}. "
            f"Most correct so far: {labels[leader]} ({correct[leader]} of {n}).",
        )
    broken = [pid for pid in by_score if failed.get(pid, 0) > MAX_FAILED_SHARE * n]
    if broken:
        pid = broken[0]
        return Headline(
            "insufficient",
            f"Not enough evidence yet: {labels[pid]} failed on {failed[pid]} of {n} prompts, "
            "so this comparison is not fair to it.",
        )
    best = by_score[0]
    if best not in ties.tied:
        return Headline(
            "tradeoff",
            f"{labels[best]} is the best value for {company} at these weights, but {labels[leader]} "
            f"answers significantly more prompts correctly ({correct[leader]} vs {correct[best]} of {n}).",
        )
    if len(ties.tied) == 1:
        return Headline(
            "sole",
            f"{labels[best]} is the most accurate provider for {company} "
            f"({correct[best]} of {n} correct) and the best value overall.",
        )
    return Headline(
        "tie",
        f"{labels[best]} gives {company} the best balance of accuracy, cost and speed. "
        f"{len(ties.tied)} providers tie on accuracy, so cost and speed decide.",
    )


# ---------------------------------------------------------------- cost at volume


@dataclass(frozen=True)
class VolumeCost:
    volume: int
    usd: float
    estimated: bool  # any estimated input makes the output "~"


def cost_at_volume(
    per_prompt_usd: float, estimated: bool, volumes: Sequence[int] = EXAMPLE_VOLUMES
) -> list[VolumeCost]:
    return [VolumeCost(v, per_prompt_usd * v, estimated) for v in volumes]


# ---------------------------------------------------------------- small helpers


def median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    return (s[(len(s) - 1) // 2] + s[len(s) // 2]) / 2


def percentile(xs: Sequence[float], q: float) -> float:
    """Nearest-rank (floor) percentile, as in the prototype."""
    s = sorted(xs)
    if not s:
        return 0.0
    return s[math.floor(q * (len(s) - 1))]
