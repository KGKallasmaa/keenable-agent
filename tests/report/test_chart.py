"""Direct tests for the Pareto chart label geometry in ``searchaudit.report.chart``.

The label font is ``600 12px`` IBM Plex Sans (``report.css:.chart .lbl``), whose
glyphs sit ~``CAP_H`` px *above* the SVG text baseline (cap height 706/1000 em).
``_spread_labels`` must keep each label clear of every nearby dot accounting for
that glyph height, not just the baseline anchor.
"""

import math

import pytest

from searchaudit.report.chart import CAP_H, LABEL_GAP, LABEL_WIDTH, _spread_labels

# Glyph metrics for ``.chart .lbl`` (600 12px IBM Plex Sans). The vertical ink box
# of a label is approx ``[ly - CAP_H, ly + DESC]``; cap height 706/1000 em, a small
# descender, and a per-character advance used for horizontal extent.
DESC = 2.5
CHAR_W = 7.0


def _glyph_box(d: dict) -> tuple[float, float, float, float]:
    """(x0, x1, y0, y1) ink box of label ``d``."""
    lx, ly = d["lx"], d["ly"]
    return lx, lx + CHAR_W * len(d["short"]), ly - CAP_H, ly + DESC


def painted_overlap(label: dict, dot: dict) -> float:
    """Area (px^2) of ``dot`` disk covered by ``label`` glyph ink, by quadrature."""
    gx0, gx1, gy0, gy1 = _glyph_box(label)
    cx, cy, r = dot["cx"], dot["cy"], dot["r"]
    if gx1 <= cx - r or gx0 >= cx + r or gy1 <= cy - r or gy0 >= cy + r:
        return 0.0
    total = 0.0
    rows = 2000
    for i in range(rows):
        y = gy0 + (gy1 - gy0) * (i + 0.5) / rows
        if abs(y - cy) >= r:
            continue
        half = math.sqrt(r * r - (y - cy) ** 2)
        ox = min(cx + half, gx1) - max(cx - half, gx0)
        if ox > 0:
            total += ox * (gy1 - gy0) / rows
    return total


def _dot(short, cx, cy, r, id=None):
    return {"id": id or short, "short": short, "cx": cx, "cy": cy, "r": r}


def _no_label_covers_a_dot(dots):
    """Invariant the docstring promises: no label ink overlaps any other dot."""
    for a in dots:
        for b in dots:
            if a is not b:
                assert painted_overlap(a, b) == pytest.approx(0.0, abs=0.1), (
                    f"{a['short']!r} label covers {b['short']!r} dot "
                    f"({painted_overlap(a, b):.2f} px^2)"
                )


def _labels_not_overlapping(dots):
    """Two horizontally-close labels must keep >= LABEL_GAP vertical separation."""
    for i, a in enumerate(dots):
        for b in dots[i + 1:]:
            if abs(a["lx"] - b["lx"]) < LABEL_WIDTH:
                assert abs(a["ly"] - b["ly"]) >= LABEL_GAP


def test_no_label_covers_a_dot_across_many_random_configs():
    # Core invariant of _spread_labels: no label glyph covers any other dot, and
    # horizontally-close labels keep >= LABEL_GAP separation. Guards the threshold
    # logic (the dot-overlap branch of crowded()) against regression.
    import random

    rng = random.Random(20261009)
    for _ in range(500):
        n = rng.randint(2, 6)
        dots = []
        for i in range(n):
            cx = round(rng.uniform(50, 400), 1)
            cy = round(rng.uniform(20, 260), 1)
            r = round(rng.uniform(5, 14), 1)
            dots.append(_dot(f"P{i}", cx, cy, r, id=f"p{i}"))
        _spread_labels(dots)
        _no_label_covers_a_dot(dots)
        _labels_not_overlapping(dots)


def test_fixture_audit_has_no_label_dot_overlap(fixture_results):
    # Guards the full pareto_chart -> _spread_labels pipeline (cx/cy/r transforms
    # included) against producing an overlap on real audit data.
    from searchaudit.report.model import build_view_model

    vm = build_view_model(fixture_results)
    _no_label_covers_a_dot(vm["chart"]["dots"])


def test_label_not_crowded_when_far_from_other_dots():
    # An isolated dot's label stays at its initial offset: crowded() must be False
    # so the label is not nudged unnecessarily.
    dots = [_dot("Solo", cx=100.0, cy=100.0, r=7.0)]
    _spread_labels(dots)
    assert dots[0]["lx"] == pytest.approx(100.0 + 7.0 + 6)
    assert dots[0]["ly"] == pytest.approx(100.0 + 4)
