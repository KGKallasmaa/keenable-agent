"""Shared data contract between the engine (producer) and the report (consumer).

An audit is stored as one `AuditResults` JSON file (audits/<slug>/results.json).
The engine writes it; the report reads it. Change this file only with both sides in mind.
"""

from typing import Literal

from pydantic import BaseModel, Field

ProviderId = Literal["exa", "parallel", "keen", "select", "routed"]
PromptType = Literal["lookup", "fresh", "multihop", "wide", "domain"]
Grade = Literal["c", "p", "w"]  # correct, partly correct, wrong
FailureMode = Literal[
    "wrong_source", "outdated", "incomplete", "unsupported", "wrong_tool", "over_search", "error"
]

PROMPT_TYPE_NAMES: dict[str, str] = {
    "lookup": "Lookup",
    "fresh": "Freshness",
    "multihop": "Multi-hop",
    "wide": "List-building",
    "domain": "Regulation",
}

# The four judge checks, in this order, in Judgment.checks.
RUBRIC = ["Answers the prompt", "Facts correct", "Sources support it", "Up to date"]

FAILURE_MODES: dict[str, tuple[str, str]] = {
    "wrong_source": ("Used the wrong source", "Answered from an unrelated or wrong page."),
    "outdated": ("Outdated information", "Found only older pages; the answer was right once but isn't now."),
    "incomplete": ("Incomplete list", "Returned only some of the items a list prompt asked for."),
    "unsupported": ("Claim not in sources", "Stated something the cited pages don't say."),
    "wrong_tool": ("Wrong tool for the job", "Built a table for a single-fact prompt."),
    "over_search": ("Over-searching", "Used more than 10 searches before answering."),
    "error": ("Tool error / timeout", "The provider errored or the agent hit its time limit."),
}


class ProviderInfo(BaseModel):
    id: ProviderId
    label: str  # "Exa", "Keenable search", ...
    short: str  # "Exa", "Search", ...
    domain: str  # for the favicon, e.g. "exa.ai"
    hex: str  # brand colour, see design v4: keen blue, select red, routed purple, exa orange, parallel slate
    uses_today: bool = False  # "StackAI uses today" tag (Exa for StackAI)
    via: str = ""  # how it was called, e.g. "OpenRouter openrouter:web_search engine=exa"
    price_note: str = ""  # e.g. "$0.007 per search (OpenRouter list price)"


class Prompt(BaseModel):
    id: int  # 1-based, stable; used in /r/<slug>/q/<id>
    text: str
    type: PromptType
    reference: str  # reference answer / grading notes the judge uses
    origin: Literal["generated", "uploaded", "hand"] = "generated"
    as_of: str = ""  # date the reference was checked, ISO


class ToolCall(BaseModel):
    tool: str  # "search", "fetch", "select", "jev_route", ...
    args: dict = Field(default_factory=dict)
    latency_ms: int = 0
    ok: bool = True
    error: str = ""
    result_chars: int = 0
    cost_usd: float = 0.0
    urls: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    url: str
    title: str = ""


class Run(BaseModel):
    prompt_id: int
    provider: ProviderId
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    searches: int = 0
    fetches: int = 0
    tool_calls: list[ToolCall] = Field(default_factory=list)
    latency_ms: int = 0  # wall clock for the whole agent run
    cost_search_usd: float = 0.0  # search + fetch spend
    cost_model_usd: float = 0.0  # agent LLM tokens
    cost_usd: float = 0.0  # total = search + model
    cost_estimated: bool = False  # any input priced from a list price instead of the response -> "~"
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""  # non-empty when the run failed outright
    budget_hit: bool = False  # agent ran out of tool calls / time and was forced to answer
    via: ProviderId | None = None  # routed only: which Keenable arm Jev picked
    route_confidence: float | None = None  # routed only
    model: str = ""  # agent model actually used (from the response)


class Judgment(BaseModel):
    prompt_id: int
    provider: ProviderId
    grade: Grade
    checks: list[bool]  # len 4, aligned with RUBRIC
    failure_mode: FailureMode | None = None  # None when grade == "c"
    note: str = ""  # one or two sentences: why this grade
    judge_model: str = ""


class AuditMeta(BaseModel):
    slug: str  # "stackai"
    company: str  # "StackAI"
    category: str  # "Enterprise AI agent platform"
    created_at: str  # ISO
    agent_model: str  # pinned model id used for every provider
    agent_model_note: str = ""  # e.g. "openrouter/auto resolved once and pinned"
    judge_model: str
    budget: dict = Field(default_factory=dict)  # {"max_tool_calls": 15, "max_seconds": 180}
    total_cost_usd: float = 0.0  # spend of the whole audit incl. judging
    human_agreement: float | None = None  # judge vs hand labels; None = not measured yet
    human_labels_n: int = 0
    notes: list[str] = Field(default_factory=list)  # methodology caveats shown in the report


class AuditResults(BaseModel):
    meta: AuditMeta
    providers: list[ProviderInfo]
    prompts: list[Prompt]
    runs: list[Run]
    judgments: list[Judgment]
