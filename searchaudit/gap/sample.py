"""Stratified sample, weighted back to the log. About 30 tasks per class."""

from __future__ import annotations

import random

from searchaudit.gap.log import CLASSES, class_counts, rate_allowed

PER_CLASS = 30
SPOTCHECK = 5


def stratified(rows: list[dict], *, per_class: int = PER_CLASS, seed: int = 7) -> tuple[list[dict], dict[str, float]]:
    """Return the sample and a weight (log count / sample count) per class."""
    groups: dict[str, list[dict]] = {name: [] for name in CLASSES}
    for row in rows:
        groups.setdefault(row["class"], []).append(row)
    rng = random.Random(seed)
    picked: list[dict] = []
    weights: dict[str, float] = {}
    for name in CLASSES:
        group = list(groups.get(name) or [])
        rng.shuffle(group)
        take = group[:per_class]
        picked.extend(take)
        weights[name] = (len(group) / len(take)) if take else 0.0
    return picked, weights


def spotcheck(rows: list[dict], *, per_class: int = SPOTCHECK, seed: int = 7) -> list[dict]:
    """Thirty tags (five per class) drawn with the same seed as the sample, for a human check."""
    groups: dict[str, list[dict]] = {name: [] for name in CLASSES}
    for row in rows:
        groups.setdefault(row["class"], []).append(row)
    rng = random.Random(seed)
    out = []
    for name in CLASSES:
        group = list(groups.get(name) or [])
        rng.shuffle(group)
        out.extend({"task": row["task"], "class": row["class"], "turn_id": row.get("turn_id", "")}
                   for row in group[:per_class])
    return out


def headline_inputs(rows: list[dict]) -> dict[str, dict]:
    """Per-class log size and whether a rate may be shown."""
    counts = class_counts(rows)
    return {name: {"n": counts[name], "rate": rate_allowed(counts[name])} for name in CLASSES}
