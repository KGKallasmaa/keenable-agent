from app.cost import context_cost


def is_sourced(arm: dict) -> bool:
    if not arm.get("ok"):
        return False
    urls = arm.get("urls") or []
    text = (arm.get("text") or "").strip()
    return len(urls) > 0 and len(text) >= 40


def total_cost(arm: dict, usd_per_million: float) -> float:
    return float(arm.get("search_cost") or 0) + context_cost(
        int(arm.get("context_tokens") or 0), usd_per_million
    )


def pick_winner(exa: dict, keenable: dict, usd_per_million: float) -> str:
    """Sourced beats unsourced. If both are sourced, the cheaper total wins.

    Within 10 percent, call it a tie so a rounding difference is not a verdict.
    """
    exa_ok = is_sourced(exa)
    keen_ok = is_sourced(keenable)
    if exa_ok and not keen_ok:
        return "exa"
    if keen_ok and not exa_ok:
        return "keenable"
    if not exa_ok and not keen_ok:
        return "neither"
    exa_total = total_cost(exa, usd_per_million)
    keen_total = total_cost(keenable, usd_per_million)
    higher = max(exa_total, keen_total, 0.000001)
    if abs(exa_total - keen_total) / higher <= 0.10:
        return "tie"
    return "exa" if exa_total < keen_total else "keenable"


def summarize(tasks: list[dict], usd_per_million: float) -> dict:
    summary = {}
    for name in ("exa", "keenable"):
        sourced = 0
        search = 0.0
        context = 0.0
        latencies = []
        for task in tasks:
            arm = task[name]
            search += float(arm.get("search_cost") or 0)
            context += context_cost(int(arm.get("context_tokens") or 0), usd_per_million)
            if is_sourced(arm):
                sourced += 1
            if arm.get("latency_ms") is not None:
                latencies.append(arm["latency_ms"])
        n = len(tasks) or 1
        spend = search + context
        summary[name] = {
            "sourced": sourced,
            "queries": len(tasks),
            "sourced_rate": sourced / n if tasks else 0,
            "search_cost": round(search, 6),
            "context_cost": round(context, 6),
            "spend": round(spend, 6),
            "cost_per_sourced": round(spend / sourced, 6) if sourced else None,
            "latency_ms_p50": _p50(latencies),
        }
    wins = {"exa": 0, "keenable": 0, "tie": 0, "neither": 0}
    for task in tasks:
        wins[task["winner"]] = wins.get(task["winner"], 0) + 1
    summary["wins"] = wins
    return summary


def _p50(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]
