"""Keenable routed: Jev picks search or SELECT, then the agent has only that tool.

One choice call with precedents in the state, a confidence floor, then an if. Search is a lookup. SELECT is a table across many pages.
The agent writes the SQL when the choice is select. Nothing rewrites a keyword into a statement.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from searchaudit.config import DEFAULT_ROUTER_MODEL
from searchaudit.models import Run, ToolCall
from searchaudit.providers.base import ProviderContext, ProviderSession, ToolOutcome
from searchaudit.providers.keenable import FETCH_TOOL, SEARCH_TOOL, KeenablePriced
from searchaudit.providers.openrouter import OpenRouterError
from searchaudit.providers.research_skill import RESEARCH_SKILL
from searchaudit.providers.select import (
    Connect,
    connect_select,
    open_select_session,
    select_unavailable,
)

SEARCH_INSTRUCTIONS = "Use one or two short queries, then fetch the page you cite."

# Below this confidence the choice is noise: on spacex-jobs (2026-10-09) every SELECT pick under
# 0.3 was partly right or wrong on both arms. Search is the cheaper, faster default.
CONFIDENCE_FLOOR = 0.3

# What actually separated the arms on spacex-jobs, read off the 21 prompts where they disagreed.
# SELECT reads the whole primary document, so it wins when the prompt names one numbered order or
# decision and asks for several exact figures or clauses from inside it. Search wins when the
# answer is an interpretation of a rule, a register or licence database lookup, or a comparison
# across documents, where snippets and a fetch are enough and SELECT's long SQL round trips hurt.
ROUTE_QUESTION = {
    "tool": {
        "type": "choice",
        "instructions": (
            "Which retrieval tool answers this research prompt best? "
            "search: web search returning snippets plus a page fetch. "
            "select: a SQL-style query engine that reads whole primary documents (orders, decisions, "
            "PDFs) and returns exact passages, slow and expensive per call. "
            "The precedents in the state are prompts where one tool was graded better than the other."
        ),
        "criteria": {
            "search": (
                "The answer is an interpretation or reading of a statute or regulation (for example a "
                "CFR part), a hypothetical (if X happens, does Y apply?), a register or licence-database "
                "entry, a date or count that any news page states, or a comparison of two or more "
                "documents against each other. A list or several facts from one web page is still search."
            ),
            "select": (
                "The prompt names one specific numbered order, decision or filing (for example DA-26-553, "
                "an Arcep or ISED decision) and asks for several exact figures, clauses, paragraph-level "
                "findings or conditions from inside that document, where a news summary of it is likely "
                "wrong and the full text has to be read."
            ),
        },
    }
}

# Precedents: spacex-jobs prompts where the arms disagreed, paraphrased, with the arm that won.
ROUTE_PRECEDENTS = [
    {"prompt": "Did the June 2026 order grant Amazon's 24-month Gen1 extension? If not, what did it grant, "
               "what is the 50% figure, and what happens to undeployed satellites on the milestone date?",
     "best": "select"},
    {"prompt": "On 9 January 2026 the Bureau acted on the Gen2 upgrade. How many satellites does that order "
               "authorize, how many are the new tranche, and what did it expressly defer?", "best": "select"},
    {"prompt": "Has Arcep authorised Starlink to use the ITU network USASAT-NGSO-3X in France? Which decision, "
               "which networks, bands and direction, and is the terminal protected from interference?",
     "best": "select"},
    {"prompt": "In March 2024 the Bureau acted on the application to add MSS to Gen2. Was it accepted for "
               "filing, held in abeyance, and what file number was dismissed?", "best": "select"},
    {"prompt": "DA-26-36 removes the one-beam condition. What cap replaces it, in which band does the order "
               "state it, and what GSO arc exclusion does the waiver require?", "best": "select"},
    {"prompt": "Do Amazon's Gen2 and Polar systems have the same 50% and 100% deadlines as Gen1? Give the "
               "deadlines and counts.", "best": "search"},
    {"prompt": "After Part 25 is replaced by Part 100, does a licence citing a Part 25 section have to be "
               "rewritten, and what does the citation mean after the sunset?", "best": "search"},
    {"prompt": "If third-party claims exceed the required insurance, does the United States pay the next "
               "$1.5 billion, and does that cover an experimental permit or wilful misconduct?",
     "best": "search"},
    {"prompt": "The Nigerian Communications Commission register lists SpaceX against 6,000 satellites. Is that "
               "landing permit a licence for 6,000 satellites, and is the Ka uplink a continuous block?",
     "best": "search"},
    {"prompt": "Starlink asked Ofcom for Ka-band gateway authority at two sites and E-band at three. For each, "
               "the sites, frequencies, whether it is a grant, a licence or a temporary licence, and the end "
               "date.", "best": "search"},
    {"prompt": "Did DA-26-471 refuse the EchoStar assignment on the ground that Musk is controlled by the CCP?",
     "best": "search"},
    {"prompt": "Does the insurance on a launch licence protect only the licensee, and must a customer's "
               "contractor pay the United States for its own cover?", "best": "search"},
]


def route_state(query: str) -> dict:
    return {"query": query, "precedents": ROUTE_PRECEDENTS}


def jev_model(router_model: str) -> str:
    """The decisions model. typesafe/jev-router picks a chat model; it does not answer a choice."""
    if router_model.startswith("typesafe/jev-1"):
        return router_model
    return DEFAULT_ROUTER_MODEL


class RoutedSession(ProviderSession):
    def __init__(self, select, keenable, ctx: ProviderContext) -> None:
        self.select = select
        self.keenable = keenable
        self.ctx = ctx
        self.arm = ""
        self.confidence: float | None = None
        self.route_call: ToolCall | None = None
        self.decision: dict | None = None
        self.forced_query_time = ""

    def bind_query_time(self, when: str) -> None:
        self.forced_query_time = (when or "").strip()

    async def prepare(self, task: str) -> None:
        if not self.select.query_tool():
            self.arm = "keen"
            return
        self.arm, self.confidence, self.route_call, self.decision = await classify(self.ctx, task)

    def instructions(self) -> str:
        if self.arm == "select":
            return RESEARCH_SKILL
        if self.arm == "keen":
            return SEARCH_INSTRUCTIONS
        return ""

    def function_tools(self) -> list[dict]:
        if self.arm == "select" and self.select.query_tool():
            tools = [tool for tool in self.select.function_tools() if tool["function"]["name"] != "fetch"]
            tools.append(FETCH_TOOL)
            return tools
        return [SEARCH_TOOL, FETCH_TOOL]

    async def call_tool(self, name: str, args: dict) -> ToolOutcome:
        if name == "fetch":
            return await self.keenable.fetch(str(args.get("url", "")))
        if name == "search":
            return await self.keenable.search(str(args.get("query", "")), query_time=self.forced_query_time or None)
        if name in self.select.tools:
            return await self.select.call_tool(name, args)
        raise KeyError(f"unknown tool: {name}")

    def finish(self, run: Run) -> None:
        if self.route_call is not None:
            run.tool_calls.insert(0, self.route_call)
            run.cost_model_usd = round(run.cost_model_usd + self.route_call.cost_usd, 6)
            run.cost_usd = round(run.cost_usd + self.route_call.cost_usd, 6)
        if self.arm:
            run.via = self.arm
        run.route_confidence = self.confidence

    def archive_extra(self) -> dict:
        return {"decision": self.decision} if self.decision is not None else {}


class RoutedProvider(KeenablePriced):
    id = "routed"
    via = ("Jev answers one choice, search or select. The agent then has only that tool")

    def __init__(self, connect: Connect = connect_select) -> None:
        self.connect = connect

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        return select_unavailable(ctx)

    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        return self._open(ctx)

    @asynccontextmanager
    async def _open(self, ctx: ProviderContext) -> AsyncIterator[ProviderSession]:
        async with open_select_session(ctx, self.connect) as select:
            yield RoutedSession(select, select.keenable, ctx)


def copied_answer(
    source: dict, arm: str, confidence: float | None, call: ToolCall, jev_ms: int,
    decision: dict | None = None,
) -> dict:
    """The routed row is the chosen arm's answer. Jev's cost and time are added on top."""
    evidence = source.get("evidence") or []
    archive = dict(source.get("archive") or {})
    if decision is not None:
        archive["decision"] = decision
    data = {key: value for key, value in source.items() if key not in ("evidence", "archive")}
    run = Run.model_validate(data)
    run.provider = "routed"
    run.via = "select" if arm == "select" else "keen"
    run.route_confidence = confidence
    run.latency_ms += max(jev_ms, 0)
    run.cost_model_usd = round(run.cost_model_usd + call.cost_usd, 6)
    run.cost_usd = round(run.cost_usd + call.cost_usd, 6)
    run.tool_calls.insert(0, call)
    row = run.model_dump(mode="json")
    if evidence:
        row["evidence"] = evidence
    if archive:
        row["archive"] = archive
    return row


async def classify(ctx: ProviderContext, query: str) -> tuple[str, float | None, ToolCall, dict | None]:
    """-> (arm, confidence, recorded call, decision body). A failed call falls through to search."""
    try:
        body = await ctx.openrouter.decide(
            jev_model(ctx.router_model),
            route_state(query),
            ROUTE_QUESTION,
        )
    except OpenRouterError as exc:
        return "keen", None, ToolCall(tool="jev_route", args={"query": query[:200]}, ok=False, error=str(exc)[:300]), exc.body
    answer = (body.get("answers") or {}).get("tool") or {}
    choice = answer.get("choice")
    confidence = answer.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) else None
    arm = pick_arm(choice, confidence)
    cost = float((body.get("usage") or {}).get("cost") or 0.0)
    call = ToolCall(
        tool="jev_route",
        args={"choice": choice, "confidence": confidence, "arm": arm},
        cost_usd=cost,
    )
    return arm, confidence, call, body


def pick_arm(choice: object, confidence: float | None) -> str:
    """SELECT only on a confident SELECT choice. Everything else, including a weak SELECT, is search."""
    if choice != "select":
        return "keen"
    if confidence is not None and confidence < CONFIDENCE_FLOOR:
        return "keen"
    return "select"
