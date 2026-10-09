"""Number formatting shared by the view model and the templates (registered as Jinja filters).

report.js has the same helpers (money, secs); keep the formats in step.
"""

from __future__ import annotations

import html
import math
import re

from markupsafe import Markup


def money(x: float, digits: int = 3, estimated: bool = False) -> str:
    if not math.isfinite(x):
        return "—"
    return f"{'~' if estimated else ''}${x:.{digits}f}"


def big_money(x: float, estimated: bool = False) -> str:
    """$1.2M / $34k / $950, with "~" when any input was estimated."""
    tilde = "~" if estimated else ""
    if x >= 1e6:
        return f"{tilde}${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"{tilde}${int(x / 1e3 + 0.5)}k"
    return f"{tilde}${int(x + 0.5)}"


def secs(x: float) -> str:
    return f"{x / 60:.1f}m" if x >= 60 else f"{x:.1f}s"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def volume(v: int) -> str:
    return f"{v // 1_000_000}M" if v >= 1_000_000 else f"{v // 1000}k"


def pvalue(p: float) -> str:
    return "< 0.001" if p < 0.001 else f"= {p:.3f}" if p < 0.05 else f"= {p:.2f}"


_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_HEAD = re.compile(r"^#{1,6} +", re.MULTILINE)


def answer_html(text: str) -> Markup:
    """Escape an answer, then render the small markdown subset models actually emit."""
    safe = html.escape(text or "")
    safe = _HEAD.sub("", safe)
    safe = _LINK.sub(r'<a href="\2" rel="noopener nofollow" target="_blank">\1</a>', safe)
    safe = _BOLD.sub(r"<strong>\1</strong>", safe)
    safe = _CODE.sub(r"<code>\1</code>", safe)
    return Markup(safe)


FILTERS = {
    "money": money,
    "big_money": big_money,
    "secs": secs,
    "pct": pct,
    "volume": volume,
    "pvalue": pvalue,
    "answer_html": answer_html,
}
