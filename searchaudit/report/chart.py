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


def scatter_chart(
    points: Sequence[Mapping[str, Any]],
    *,
    x: str,
    y: str,
    x_axis: str,
    y_axis: str,
    x_ticks: Sequence[float],
    y_ticks: Sequence[float],
    x_fmt,
    y_fmt,
    size: str | None = None,
) -> dict[str, Any]:
    """One dot per provider. `size`, when set, is drawn as dot area."""
    xs = [float(p[x]) for p in points]
    ys = [float(p[y]) for p in points]
    x0, x1 = min(x_ticks), max(x_ticks)
    y0, y1 = min(y_ticks), max(y_ticks)
    if x1 == x0:
        x1 = x0 + 1
    if y1 == y0:
        y1 = y0 + 1
    sizes = [float(p[size]) for p in points] if size else []
    smax = max(sizes) if sizes else 1

    def sx(v: float) -> float:
        return round(LEFT + (v - x0) / (x1 - x0) * (W - LEFT - RIGHT), 1)

    def sy(v: float) -> float:
        return round(H - BOTTOM - (v - y0) / (y1 - y0) * (H - BOTTOM - TOP), 1)

    dots = []
    for p in points:
        area = (float(p[size]) / smax) if size and smax else 1
        r = round(6 + 8 * area**0.5, 1) if size else 7
        dots.append({
            "id": p["id"], "short": p["short"], "label": p["label"], "hex": p["hex"],
            "cx": sx(float(p[x])), "cy": sy(float(p[y])), "r": r,
        })
    _spread_labels(dots)
    return {
        "w": W, "h": H, "left": LEFT, "right": W - RIGHT, "top": TOP, "base": H - BOTTOM,
        "x_axis": x_axis, "y_axis": y_axis,
        "xticks": [{"x": sx(v), "label": x_fmt(v)} for v in x_ticks],
        "yticks": [{"y": sy(v), "label": y_fmt(v)} for v in y_ticks],
        "dots": dots,
    }


LANE_W = 760
LANE_LEFT, LANE_RIGHT, LANE_TOP, LANE_BOTTOM = 124, 16, 8, 46
LANE = 48
CLOUD_W, CLOUD_H = 760, 340
CLOUD_LEFT, CLOUD_RIGHT, CLOUD_TOP, CLOUD_BOTTOM = 54, 16, 12, 48


def lane_chart(
    points: Sequence[Mapping[str, Any]],
    providers: Sequence[Mapping[str, Any]],
    *,
    field: str,
    axis: str,
    ticks: Sequence[float],
    fmt,
    pad: float = 0.0,
) -> dict[str, Any]:
    """One lane per provider and one dot per prompt. A hollow dot is not fully correct."""
    x0, x1 = min(ticks) - pad, max(ticks) + pad
    if x1 == x0:
        x1 = x0 + 1
    height = LANE_TOP + len(providers) * LANE + LANE_BOTTOM
    plot_w = LANE_W - LANE_LEFT - LANE_RIGHT

    def sx(v: float) -> float:
        return round(LANE_LEFT + (float(v) - x0) / (x1 - x0) * plot_w, 1)

    lanes = []
    for i, provider in enumerate(providers):
        cy = LANE_TOP + i * LANE + LANE / 2
        lanes.append({"id": provider["id"], "short": provider["short"], "hex": provider["hex"], "cy": round(cy, 1), "y": round(cy + 4, 1)})
    lane_cy = {lane["id"]: lane["cy"] for lane in lanes}
    dots = []
    for point in points:
        if point["id"] not in lane_cy:
            continue
        jitter = ((int(point["prompt"]) * 17 + sum(ord(c) for c in str(point["id"]))) % 17) - 8
        dots.append({
            "id": point["id"],
            "prompt": point["prompt"],
            "cx": sx(point[field]),
            "cy": round(lane_cy[point["id"]] + jitter, 1),
            "r": 4.2,
            "hex": point["hex"],
            "hollow": point["grade"] != "c",
            "title": point["title"],
        })
    return {
        "kind": "lanes",
        "w": LANE_W,
        "h": height,
        "left": LANE_LEFT,
        "right": LANE_W - LANE_RIGHT,
        "top": LANE_TOP,
        "base": height - LANE_BOTTOM,
        "x_axis": axis,
        "lanes": lanes,
        "xticks": [{"x": sx(v), "label": fmt(v)} for v in ticks],
        "yticks": [],
        "dots": dots,
    }


def cloud_chart(
    points: Sequence[Mapping[str, Any]],
    *,
    x: str,
    y: str,
    x_axis: str,
    y_axis: str,
    x_ticks: Sequence[float],
    y_ticks: Sequence[float],
    x_fmt,
    y_fmt,
) -> dict[str, Any]:
    """One dot per prompt per provider. No labels on the dots."""
    x0, x1 = min(x_ticks), max(x_ticks)
    y0, y1 = min(y_ticks), max(y_ticks)
    if x1 == x0:
        x1 = x0 + 1
    if y1 == y0:
        y1 = y0 + 1

    def sx(v: float) -> float:
        return round(CLOUD_LEFT + (float(v) - x0) / (x1 - x0) * (CLOUD_W - CLOUD_LEFT - CLOUD_RIGHT), 1)

    def sy(v: float) -> float:
        return round(CLOUD_H - CLOUD_BOTTOM - (float(v) - y0) / (y1 - y0) * (CLOUD_H - CLOUD_BOTTOM - CLOUD_TOP), 1)

    dots = [
        {
            "id": point["id"],
            "prompt": point["prompt"],
            "cx": sx(point[x]),
            "cy": sy(point[y]),
            "r": 4.2,
            "hex": point["hex"],
            "hollow": point["grade"] != "c",
            "title": point["title"],
        }
        for point in points
    ]
    return {
        "kind": "cloud",
        "lanes": [],
        "w": CLOUD_W,
        "h": CLOUD_H,
        "left": CLOUD_LEFT,
        "right": CLOUD_W - CLOUD_RIGHT,
        "top": CLOUD_TOP,
        "base": CLOUD_H - CLOUD_BOTTOM,
        "x_axis": x_axis,
        "y_axis": y_axis,
        "xticks": [{"x": sx(v), "label": x_fmt(v)} for v in x_ticks],
        "yticks": [{"y": sy(v), "label": y_fmt(v)} for v in y_ticks],
        "dots": dots,
    }


def _spread_labels(dots: list[dict[str, Any]]) -> None:
    """Nudge labels down so labels of nearby dots do not overlap or cover another dot."""
    placed: list[tuple[float, float]] = []
    for d in sorted(dots, key=lambda d: (d["cy"], d["cx"])):
        lx, ly = d["cx"] + d["r"] + 6, d["cy"] + 4

        def crowded() -> bool:
            if any(abs(lx - px) < LABEL_WIDTH and abs(ly - py) < LABEL_GAP for px, py in placed):
                return True
            return any(
                other is not d and abs(lx - other["cx"]) < 28 and abs(ly - other["cy"]) < 14
                for other in dots
            )

        for _ in range(30):
            if not crowded():
                break
            ly += 4
        placed.append((lx, ly))
        d["lx"], d["ly"] = round(lx, 1), round(ly, 1)
