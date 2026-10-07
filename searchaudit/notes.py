"""Methodology caveats written into AuditMeta.notes and shown in the report's "How we measured" list.

Notes are built from the audit config and the runs that actually happened, so a caveat only appears
when it applies (e.g. the SELECT note only when SELECT ran).
"""

from searchaudit.config import AuditConfig, Settings
from searchaudit.models import Judgment, ProviderId, Run

SERVER_TOOL_PROVIDERS: dict[ProviderId, str] = {"exa": "Exa", "parallel": "Parallel"}
KEENABLE_PROVIDERS: set[ProviderId] = {"keen", "select", "routed"}


def _providers_phrase(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _trouble(runs: list[Run], provider: ProviderId) -> str:
    """'3 of 60 runs errored and 5 hit the budget', or '' when neither happened."""
    mine = [r for r in runs if r.provider == provider]
    errors = sum(1 for r in mine if r.error)
    budget = sum(1 for r in mine if r.budget_hit and not r.error)
    parts = [f"{errors} errored"] if errors else []
    if budget:
        parts.append(f"{budget} hit the budget")
    return f"{' and '.join(parts)} of {len(mine)} runs" if parts else ""


def method_notes(
    config: AuditConfig,
    settings: Settings,
    runs: list[Run],
    judgments: list[Judgment],
    judge_bundle: str,
    human_agreement: float | None = None,
) -> list[str]:
    ran = {r.provider for r in runs}
    budget = config.budget
    notes = [
        ("Every provider ran the same prompts with the same pinned agent model, system prompt and temperature "
        f"({config.temperature})."),
        (f"Budget per run: {budget.max_tool_calls} tool calls and {budget.max_seconds:.0f} seconds; runs that hit "
        "it were forced to answer and are flagged."),
    ]

    server = [name for pid, name in SERVER_TOOL_PROVIDERS.items() if pid in ran]
    if server:
        notes.append(
            f"{_providers_phrase(server)} ran as OpenRouter server tools (openrouter:web_search and web_fetch), "
            "so OpenRouter's own loop decides how often they search inside one model call. Keenable ran as "
            "function tools in our own agent loop. Their search cost is what OpenRouter billed, split from the "
            "model cost when OpenRouter reported the split, otherwise estimated from list prices (marked ~)."
        )
    if ran & KEENABLE_PROVIDERS:
        notes.append(
            f"Keenable search and fetch are priced at the list price of ${settings.keenable_usd_per_request:g} "
            "per request (marked ~), not from an invoice."
        )
    if "select" in ran:
        notes.append(
            "Keenable SELECT is experimental: the agent writes one read-only SQL query (WEB_SEARCH / WEB_FETCH "
            "plus LLM functions) per call. A failed query still counts toward the tool-call budget and is "
            "priced at $0, since it is not known whether Keenable bills it."
        )
    if "routed" in ran:
        notes.append(
            f"Keenable routed is a prototype: a router model ({config.router_model}) picks SELECT or Keenable "
            "search for each search call, and when it picks SELECT the agent model writes the SQL. Router and "
            "SQL-writing calls are counted in its search cost."
        )
    if "none" in ran:
        notes.append(
            "No search (model memory) is a baseline, not a provider: the same agent, model, system prompt and "
            "budget with zero tools, told to say when it cannot know something. The judge grades its claims "
            "against the reference without requiring sources. Search lift = prompts a provider got right that "
            "the baseline got wrong. It is never recommended in the headline or the implementation guide."
        )
    for pid in sorted(ran):
        trouble = _trouble(runs, pid)
        if trouble:
            notes.append(f"{pid}: {trouble}. Errored runs count as wrong.")

    judged = {(j.prompt_id, j.provider) for j in judgments}
    unjudged = sum(1 for r in runs if (r.prompt_id, r.provider) not in judged)
    if unjudged:
        notes.append(f"{unjudged} runs have no judgment yet and count as wrong in this report.")
    notes.append(f"Judge: {config.judge_model}, bundle {judge_bundle}, temperature 0, sees the reference answer.")
    if human_agreement is None:
        notes.append(
            "The judge has not yet been calibrated against hand-graded answers, so treat small accuracy gaps "
            "between providers with care."
        )
    return notes

