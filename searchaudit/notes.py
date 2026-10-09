"""Methodology caveats written into AuditMeta.notes and shown in the report's "How we measured" list.

Notes are built from the audit config and the runs that actually happened, so a caveat only appears
when it applies (e.g. the SELECT note only when SELECT ran).
"""

import json
from searchaudit.config import AuditConfig, Settings
from searchaudit.models import Judgment, ProviderId, Run

SERVER_TOOL_PROVIDERS: dict[ProviderId, str] = {"exa": "Exa", "parallel": "Parallel", "grok": "Grok native"}
KEENABLE_PROVIDERS: set[ProviderId] = {"keen", "select", "routed"}


def _providers_phrase(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _trouble(runs: list[Run], provider: ProviderId) -> str:
    """'1 run errored, 3 returned no answer and 5 hit the budget, of 60', or '' when none of that happened."""
    mine = [r for r in runs if r.provider == provider]
    errors = sum(1 for r in mine if r.error)
    budget = sum(1 for r in mine if r.budget_hit)
    empty = sum(1 for r in mine if not r.error and not r.budget_hit and not r.answer.strip())
    parts = []
    if errors:
        parts.append(f"{errors} run{'s' if errors != 1 else ''} errored")
    if empty:
        parts.append(f"{empty} returned no answer")
    if budget:
        parts.append(f"{budget} hit the budget")
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"{joined}, of {len(mine)} run{'s' if len(mine) != 1 else ''}"


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
            "function tools in our own agent loop. The server-tool providers' search cost is what OpenRouter billed, split from the "
            "model cost when OpenRouter reported the split, otherwise estimated from list prices (marked ~)."
        )
    if ran & KEENABLE_PROVIDERS:
        notes.append(
            f"{'Keenable search and SELECT are' if ran & {'select', 'routed'} else 'Keenable search is'} priced at the list price of ${settings.keenable_usd_per_request:g} "
            "per request. Requests that failed are priced at $0, so an effective rate can sit below list."
        )
    if "select" in ran:
        notes.append(
            "Keenable SELECT is experimental: the agent is given a research skill and writes DuckDB SQL "
            "(WEB_SEARCH, SEM_EXTRACT) in the query argument. The other providers do not get that skill. "
            "A failed query still counts toward the tool-call budget and is priced at $0, since it is not "
            "known whether Keenable bills it."
        )
    if "routed" in ran:
        notes.append(
            "Keenable router asks Jev (typesafe/jev-1.13, a small decision model served through OpenRouter) one "
            "choice question, search or select, and returns the answer that arm already gave, plus the cost and "
            "time of the Jev call. A prompt where either arm failed has no router run and counts as wrong."
        )
    if "none" in ran:
        notes.append(
            "No search (model memory) is a baseline, not a provider: the same agent, model, system prompt and "
            "budget with zero tools, told to say when it cannot know something. The judge grades its claims "
            "against the reference without requiring sources. Search lift = prompts a provider got right that "
            "the baseline got wrong. It is never recommended in the headline or the implementation guide."
        )
    cut = config.base_dir / "cut-2026-10-09.json"
    if cut.is_file():
        notes.append(
            f"Prompt ids run past 100 with gaps: on 2026-10-09 the {len(json.loads(cut.read_text()))} prompts that "
            "every provider had answered correctly were removed, since they could not separate anyone, and the "
            "same number of harder cross-document prompts were added. Ids are never reused."
        )
    labels = {p.id: p.label for p in config.providers}
    troubles = [(labels.get(pid, pid), _trouble(runs, pid)) for pid in sorted(ran)]
    troubles = [(label, t) for label, t in troubles if t]
    if troubles:
        notes.append("; ".join(f"{label}: {t}" for label, t in troubles)
                     + ". A run that errored or returned no answer counts as wrong; a run that hit the budget "
                       "was made to answer and graded like any other.")

    judged = {(j.prompt_id, j.provider) for j in judgments}
    unjudged = sum(1 for r in runs if (r.prompt_id, r.provider) not in judged)
    if unjudged:
        notes.append(f"{unjudged} runs have no judgment yet and count as wrong in this report.")
    notes.append(f"Judge: {config.judge_model}, bundle {judge_bundle}, temperature 0, sees the reference answer.")
    notes.append(
        "One run per prompt is the scored trial. Rerun the audit before treating the ranking as current: "
        "providers, models and the web change. About 100 prompts is the smallest set with a real chance of "
        "detecting a 10-point accuracy gap. A tie on fewer prompts means the sample was too small to see "
        "that gap, not that the providers are equal. The set is 100 prompts because each provider run "
        "costs real money; a gap of a few points here is a lead to test at a larger size, not a verdict."
    )
    if human_agreement is None:
        notes.append(
            "The judge was not calibrated against hand-graded answers in this audit, so treat small accuracy gaps "
            "between providers with care."
        )
    return notes

