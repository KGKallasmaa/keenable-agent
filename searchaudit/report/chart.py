"""Geometry for the accuracy-vs-cost (Pareto) chart. The template draws the SVG from this."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

W, H = 460, 300
LEFT, BOTTOM, TOP, RIGHT = 46, 36, 14, 78
LABEL_GAP = 14  # min vertical px between two labels
LABEL_WIDTH = 64  # labels closer than this horizontally can collide


def pareto_chart(
    providers: Sequence[Mapping[str, Any]], summary: Mapping[str, Mapping[str, Any]], front: set[str]
) -> dict[str, Any]:
    ids = [p["id"] for p in providers]
    costs = [summary[i]["per_prompt"] for i in ids]
    accs = [summary[i]["acc"] for i in ids]
    x1 = (max(costs) or 0.001) * 1.12
    y0 = max(0.0, min(accs) - 0.08)
    y1 = min(1.0, max(accs) + 0.05)
    if y1 - y0 < 0.1:
        y0 = max(0.0, y1 - 0.1)
    tmax = max(summary[i]["t50"] for i in ids) or 1

    def sx(v: float) -> float:
        return round(LEFT + v / x1 * (W - LEFT - RIGHT), 1)

    def sy(v: float) -> float:
        return round(H - BOTTOM - (v - y0) / (y1 - y0) * (H - BOTTOM - TOP), 1)

    dots = []
    for p in providers:
        s = summary[p["id"]]
        r = round(5 + 9 * s["t50"] / tmax, 1)
        dots.append(
            {
                "id": p["id"],
                "short": p["short"],
                "label": p["label"],
                "hex": p["hex"],
                "cx": sx(s["per_prompt"]),
                "cy": sy(s["acc"]),
                "r": r,
                "ring": p["id"] in front,
            }
        )
    _spread_labels(dots)
    front_pts = sorted((d for d in dots if d["ring"]), key=lambda d: d["cx"])
    return {
        "w": W,
        "h": H,
        "left": LEFT,
        "right": W - RIGHT,
        "top": TOP,
        "base": H - BOTTOM,
        "xticks": [{"x": sx(v), "label": f"${v:.3f}"} for v in (0, x1 / 2, x1)],
        "yticks": [{"y": sy(v), "label": f"{round(v * 100)}%"} for v in (y0, (y0 + y1) / 2, y1)],
        "dots": dots,
        "front": " ".join(f"{d['cx']},{d['cy']}" for d in front_pts) if len(front_pts) > 1 else "",
    }


def _spread_labels(dots: list[dict[str, Any]]) -> None:
    """Nudge labels down so labels of nearby dots do not overlap."""
    placed: list[tuple[float, float]] = []
    for d in sorted(dots, key=lambda d: d["cy"]):
        lx, ly = d["cx"] + d["r"] + 6, d["cy"] + 4
        while any(abs(lx - px) < LABEL_WIDTH and abs(ly - py) < LABEL_GAP for px, py in placed):
            ly += 2
        placed.append((lx, ly))
        d["lx"], d["ly"] = round(lx, 1), round(ly, 1)
