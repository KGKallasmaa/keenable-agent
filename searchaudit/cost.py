"""Prices, token-cost estimates and the per-audit spending cap."""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# OpenRouter server-tool list prices (openrouter.ai/tool/web-search, /tool/web-fetch).
SEARCH_USD = {"exa": 0.007, "parallel": 0.005, "native": 0.0}
FETCH_USD = {"exa": 0.001, "parallel": 0.001, "native": 0.0}
# Native search is billed inside the completion. This allowance is only for the preflight estimate.
NATIVE_RUN_ALLOWANCE_USD = 0.05


@dataclass(frozen=True)
class ModelPricing:
    """USD per token, as listed by GET https://openrouter.ai/api/v1/models."""

    prompt: float = 0.0
    completion: float = 0.0
    known: bool = False

    def estimate(self, tokens_in: int, tokens_out: int) -> float:
        return tokens_in * self.prompt + tokens_out * self.completion


def pricing_from_models(models: list[dict], model_id: str) -> ModelPricing:
    for entry in models:
        if entry.get("id") == model_id:
            p = entry.get("pricing") or {}
            return ModelPricing(float(p.get("prompt") or 0), float(p.get("completion") or 0), True)
    return ModelPricing()


@dataclass(frozen=True)
class ModelCost:
    usd: float
    estimated: bool


def model_cost(usage: dict, pricing: ModelPricing) -> ModelCost:
    """Total spend for one completion: usage.cost when reported, else a token estimate.

    With BYOK (usage.is_byok) OpenRouter's usage.cost excludes the provider's own bill, which
    arrives as cost_details.upstream_inference_cost; both are real spend, so they are added.
    """
    if usage.get("cost") is not None:
        upstream = (usage.get("cost_details") or {}).get("upstream_inference_cost")
        byok = float(upstream or 0.0) if usage.get("is_byok") else 0.0
        return ModelCost(float(usage["cost"]) + byok, False)
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    return ModelCost(pricing.estimate(tokens_in, tokens_out), True)


def grok_pin_error(model_ids: list[str], requested: str) -> str | None:
    """Stop unless the configured id is the only Grok 4.7 on the models list.

    Audits that do not pin Grok 4.7 are left alone.
    """
    if "grok-4.7" not in requested:
        return None
    matches = [mid for mid in model_ids if "grok-4.7" in mid]
    if len(matches) != 1 or matches[0] != requested:
        found = ", ".join(matches) if matches else "none"
        return f"expected exactly one Grok 4.7 model ({requested}); the list has {found}"
    return None


def preflight_usd(n_prompts: int, provider_ids: list[str], list_prices: dict[str, float]) -> float:
    """A start-of-run estimate from prompt count and list prices. Not a bill.

    Server-tool columns assume 4 searches and 2 fetches. Native search has no list price, so each
    of those runs uses NATIVE_RUN_ALLOWANCE_USD. Keenable search and the router assume 2 requests,
    SELECT assumes 1. The judge is $0.02 per cell.
    """
    per_run = {
        "exa": 4 * list_prices.get("exa_search", SEARCH_USD["exa"]) + 2 * list_prices.get("exa_fetch", FETCH_USD["exa"]),
        "parallel": 4 * SEARCH_USD["parallel"] + 2 * FETCH_USD["parallel"],
        "grok": NATIVE_RUN_ALLOWANCE_USD,
        "keen": 2 * list_prices.get("keenable", 0.004),
        "select": 1 * list_prices.get("keenable", 0.004),
        "routed": 2 * list_prices.get("keenable", 0.004),
        "none": 0.01,
    }
    runs = n_prompts * sum(per_run.get(pid, 0.05) for pid in provider_ids)
    judge = 0.02 * n_prompts * len(provider_ids)
    return round(runs + judge, 2)


def credit_failure(error: str) -> bool:
    """OpenRouter refused the call because the key cannot pay. Not an answer."""
    text = (error or "").lower()
    return "402" in text or "insufficient" in text or "payment required" in text


class CostCap:
    """Tracks audit spend; new runs stop launching once the cap is reached."""

    def __init__(self, cap_usd: float, spent_usd: float = 0.0) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd
        self.halted = ""
        self._logged = False

    def add(self, usd: float) -> None:
        self.spent_usd += usd

    def halt(self, reason: str) -> None:
        if not self.halted:
            log.error("%s", reason)
            self.halted = reason

    def exceeded(self) -> bool:
        if self.halted:
            return True
        over = self.spent_usd >= self.cap_usd
        if over and not self._logged:
            log.warning("cost cap reached: $%.2f spent of $%.2f; not launching new runs",
                        self.spent_usd, self.cap_usd)
            self._logged = True
        return over
