"""Prices, token-cost estimates and the per-audit spending cap."""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# OpenRouter server-tool list prices (openrouter.ai/tool/web-search, /tool/web-fetch).
SEARCH_USD = {"exa": 0.007, "parallel": 0.005}
FETCH_USD = {"exa": 0.001, "parallel": 0.001}


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


class CostCap:
    """Tracks audit spend; new runs stop launching once the cap is reached."""

    def __init__(self, cap_usd: float, spent_usd: float = 0.0) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd
        self._logged = False

    def add(self, usd: float) -> None:
        self.spent_usd += usd

    def exceeded(self) -> bool:
        over = self.spent_usd >= self.cap_usd
        if over and not self._logged:
            log.warning("cost cap reached: $%.2f spent of $%.2f; not launching new runs",
                        self.spent_usd, self.cap_usd)
            self._logged = True
        return over
