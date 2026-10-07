"""List prices used when a provider does not return its own charge.

Exa search base price is $7 / 1k requests for up to 10 results:
https://exa.ai/docs/reference/pricing
Keenable Agent Builder is $4 / 1k requests:
https://keenable.ai/
Context dollars are an estimate of the model that reads the text, not a vendor invoice.
"""

EXA_SEARCH_USD = 0.007
KEENABLE_REQUEST_USD = 0.004
CONTEXT_USD_PER_MILLION = 3.0


def context_cost(tokens: int, usd_per_million: float = CONTEXT_USD_PER_MILLION) -> float:
    if tokens <= 0:
        return 0.0
    return tokens / 1_000_000 * usd_per_million


def tokens_from_text(text: str) -> int:
    return max(0, len(text) // 4)
