"""Generate a seeded, illustrative AuditResults fixture (dev/test only, never real results).

Ports the prototype's generator (docs/prototype/index.html): a Gaussian copula ties providers'
correctness on the same prompt together (hard prompts are hard for everyone), per-type accuracy
rates, cost/time/search profiles, failure modes, and "routed" derived from the search or SELECT
result via a per-prompt router choice (it is not an independent draw).

    uv run python scripts/fake_results.py            # writes audits/fixture/results.json
    uv run python scripts/fake_results.py --out x.json --seed 7
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

from searchaudit.models import (
    RUBRIC,
    AuditMeta,
    AuditResults,
    Citation,
    Judgment,
    Prompt,
    ProviderInfo,
    Run,
    ToolCall,
)

SEED = 2  # Python RNG differs from the prototype JS; this seed exercises clear->search, clear->SELECT and lean routes
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "audits" / "fixture" / "results.json"

PROVIDERS = [
    ProviderInfo(
        id="exa",
        label="Exa",
        short="Exa",
        domain="exa.ai",
        hex="#c2410c",
        uses_today=True,
        via="Exa MCP (web_search_exa, web_fetch_exa)",
        price_note="~$0.007 per search (list price)",
    ),
    ProviderInfo(
        id="parallel",
        label="Parallel",
        short="Parallel",
        domain="parallel.ai",
        hex="#475569",
        via="Parallel Search MCP (web_search, web_fetch)",
        price_note="~$0.005 per search (list price)",
    ),
    ProviderInfo(
        id="keen",
        label="Keenable search",
        short="Search",
        domain="keenable.ai",
        hex="#2563eb",
        via="Keenable MCP (search_web_pages, fetch_page_content)",
        price_note="from response usage",
    ),
    ProviderInfo(
        id="select",
        label="Keenable SELECT",
        short="SELECT",
        domain="keenable.ai",
        hex="#dc2626",
        via="Keenable SELECT MCP",
        price_note="from response usage",
    ),
    ProviderInfo(
        id="routed",
        label="Keenable routed",
        short="Routed",
        domain="keenable.ai",
        hex="#7c3aed",
        via="One search tool; Jev picks SELECT or search per call",
        price_note="sum of the routed calls + Jev",
    ),
]
ARMS = ["exa", "parallel", "keen", "select"]

# type -> (count, P(correct) per arm)
TYPES: dict[str, tuple[int, dict[str, float]]] = {
    "lookup": (22, {"exa": 0.86, "parallel": 0.82, "keen": 0.88, "select": 0.55}),
    "fresh": (14, {"exa": 0.86, "parallel": 0.78, "keen": 0.72, "select": 0.45}),
    "multihop": (18, {"exa": 0.70, "parallel": 0.72, "keen": 0.70, "select": 0.66}),
    "wide": (16, {"exa": 0.58, "parallel": 0.66, "keen": 0.58, "select": 0.90}),
    "domain": (10, {"exa": 0.88, "parallel": 0.90, "keen": 0.80, "select": 0.74}),
}
# arm -> (mean cost USD, mean seconds, mean searches)
BASE = {
    "exa": (0.094, 7.9, 6.4),
    "parallel": (0.071, 8.6, 5.1),
    "keen": (0.019, 5.8, 6.0),
    "select": (0.022, 13.5, 1.6),
}
ESTIMATED = {"exa", "parallel"}  # priced from list prices -> "~"
TEMPLATES = {
    "lookup": [
        "Is {v} SOC 2 Type II certified?",
        "Does {v} offer SAML SSO on its mid-tier plan?",
        "Where is {v} headquartered, and who is its CEO?",
        "Does {v} expose a public REST API for {t}?",
        "Which data-residency regions does {v} support?",
        "What is {v}'s list price for its enterprise plan?",
    ],
    "fresh": [
        "What changed in {v}'s most recent pricing announcement?",
        "Has {v} announced a funding round this quarter?",
        "What did {v} ship at its latest product event?",
        "Who is {v}'s current CTO?",
    ],
    "multihop": [
        "Which of {v}'s top three competitors integrate with Salesforce, and on which plans?",
        "Which customers did {v} announce in 2026, and in which industries?",
        "Compare {v} and its closest rival on uptime SLA and support hours.",
        "Which of {v}'s partners are also ISO 27001 certified?",
    ],
    "wide": [
        "List enterprise {t} vendors with SOC 2 Type II and SAML SSO, with pricing links.",
        "Table of Series B+ {t} startups in Europe founded after 2021, with their latest round.",
        "List every {t} tool that offers on-prem deployment, with source links.",
        "Which {t} vendors publish a public status page? Give the URLs.",
    ],
    "domain": [
        "What does the EU AI Act require for high-risk {t} systems, and from when?",
        "What are the HIPAA requirements for storing {t} data with a third-party vendor?",
        "What does DORA require from ICT providers serving EU banks?",
    ],
}
VENDORS = [
    "Workday",
    "Snowflake",
    "Databricks",
    "HubSpot",
    "Zendesk",
    "Notion",
    "Rippling",
    "Ramp",
    "Gong",
    "Datadog",
    "Okta",
    "Airtable",
]
TOPICS = [
    "HR software",
    "contract-management",
    "procurement",
    "observability",
    "customer-support",
    "data-warehouse",
    "identity",
    "expense-management",
]
SOURCES = {
    "lookup": ["{v}.com", "g2.com", "wikipedia.org", "crunchbase.com", "trust.{v}.com"],
    "fresh": ["reuters.com", "techcrunch.com", "{v}.com", "theverge.com", "bloomberg.com"],
    "multihop": ["g2.com", "{v}.com", "salesforce.com", "gartner.com", "techcrunch.com"],
    "wide": ["crunchbase.com", "g2.com", "capterra.com", "{v}.com", "wikipedia.org"],
    "domain": ["europa.eu", "hhs.gov", "eba.europa.eu", "iapp.org", "lexology.com"],
}
TESLA = "Who is Tesla's current CTO?"
TESLA_ANSWERS = {
    "exa": "Tesla has had no titled CTO since 2019; engineering is led by its SVP of Vehicle Engineering.",
    "parallel": "As of October 2026 Tesla lists no CTO; the most senior engineering leader is its SVP of Engineering.",
    "keen": "Tesla has had no titled CTO since JB Straubel left in 2019.",
    "select": "Returned a table of past CTOs; the 2019 row was presented as the current CTO.",
}
JEV_COST = 0.0004
JEV_SECONDS = 0.3


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _failure(arm: str, via: str | None, ptype: str, grade: str, err: bool, searches: int) -> str | None:
    if grade == "c":
        return None
    if err:
        return "error"
    if (arm == "select" or via == "select") and ptype != "wide" and grade == "w":
        return "wrong_tool"
    if ptype == "fresh":
        return "outdated"
    if ptype == "wide":
        return "incomplete"
    if searches > 10:
        return "over_search"
    return "unsupported" if grade == "p" else "wrong_source"


def _checks(grade: str, pid: int, ptype: str) -> list[bool]:
    if grade == "c":
        return [True] * 4
    if grade == "p":
        return [k != 1 + pid % 3 for k in range(len(RUBRIC))]
    return [pid % 2 == 1, False, False, ptype != "fresh"]  # at most two pass -> "w" under the rubric rule


def _answer(text: str, vendor: str, grade: str, domains: list[str]) -> str:
    src = domains[0]
    if grade == "c":
        return (
            f"Short answer, with sources: based on {src} and {len(domains) - 1} other page(s), here is what "
            f'{vendor}\'s public material says about "{text}" (illustrative fixture text).'
        )
    if grade == "p":
        return (
            f'Partly answered: the agent found {src} and answered the main part of "{text}", '
            "but one detail is missing or not backed by the cited page (illustrative fixture text)."
        )
    return (
        f'Answered from {src}, which does not address "{text}"; the stated facts do not match the '
        "reference (illustrative fixture text)."
    )


def _note(grade: str, fail: str | None, checks: list[bool]) -> str:
    if grade == "c":
        return "Matches the reference; every claim is backed by a cited page."
    failed = ", ".join(RUBRIC[k].lower() for k, ok in enumerate(checks) if not ok)
    return f"Fails: {failed}. Main reason: {(fail or 'error').replace('_', ' ')}."


def _tool_calls(
    arm: str, query: str, searches: int, seconds: float, search_cost: float, urls: list[str], err: bool
) -> list[ToolCall]:
    per = max(1, int(seconds * 1000 * 0.6 / max(1, searches)))
    if arm == "select":
        calls = [
            ToolCall(
                tool="select",
                args={"query": f"SELECT name, url FROM WEB_SEARCH('{query[:60]}')"},
                latency_ms=per,
                result_chars=4200,
                cost_usd=search_cost / searches,
                urls=urls,
            )
            for _ in range(searches)
        ]
    else:
        calls = [
            ToolCall(
                tool="search",
                args={"query": query[:80]},
                latency_ms=per,
                result_chars=3100,
                cost_usd=search_cost / searches,
                urls=urls[:3],
            )
            for _ in range(searches)
        ]
        calls.append(
            ToolCall(tool="fetch", args={"url": urls[0]}, latency_ms=800, result_chars=9000, urls=urls[:1])
        )
    if err:
        calls[-1] = calls[-1].model_copy(update={"ok": False, "error": "timeout after 30s"})
    return calls


def _arm_run(rng: random.Random, arm: str, rate: float, z: float) -> dict:
    u = _norm_cdf(0.6 * z + 0.8 * rng.gauss(0, 1))
    err = rng.random() < 0.025
    grade = "w" if err else "c" if u < rate else "p" if rng.random() < 0.45 else "w"
    cost0, t0, s0 = BASE[arm]
    tail = 3 + rng.random() * 8 if arm == "select" and rng.random() < 0.12 else 1.0
    searches = 1 + int(rng.random() * 2) if arm == "select" else max(1, round(s0 + rng.gauss(0, 1) * 2.2))
    return {
        "grade": grade,
        "err": err,
        "cost": cost0 * (0.7 + 0.6 * rng.random()),
        "time": t0 * (0.7 + 0.6 * rng.random()) * tail,
        "searches": searches,
    }


def generate(seed: int = SEED) -> AuditResults:
    rng = random.Random(seed)
    prompts: list[Prompt] = []
    runs: list[Run] = []
    judgments: list[Judgment] = []
    pid = 1
    for ptype, (count, rates) in TYPES.items():
        for i in range(count):
            vendor = rng.choice(VENDORS)
            text = (
                TESLA
                if ptype == "fresh" and i == 0
                else (rng.choice(TEMPLATES[ptype]).replace("{v}", vendor).replace("{t}", rng.choice(TOPICS)))
            )
            z = rng.gauss(0, 1)
            r = {arm: _arm_run(rng, arm, rates[arm], z) for arm in ARMS}
            to_select = ptype == "wide" or (ptype == "multihop" and rng.random() < 0.25)
            confidence = round(0.7 + rng.random() * 0.27, 2)
            if text == TESLA:
                for arm in ("exa", "parallel", "keen"):
                    r[arm] |= {"grade": "c", "err": False}
                r["select"] |= {"grade": "w", "err": False}
                to_select, confidence = False, 0.86
            via = "select" if to_select else "keen"
            r["routed"] = r[via] | {"cost": r[via]["cost"] + JEV_COST, "time": r[via]["time"] + JEV_SECONDS}
            v = vendor.lower()
            for arm, x in r.items():
                h, k = (pid * 7 + len(arm) * 3) % 5, {"w": 1, "p": 2, "c": 3}[x["grade"]]
                domains = [SOURCES[ptype][(h + j) % 5].replace("{v}", v) for j in range(k)]
                urls = [f"https://{d}/" for d in domains]
                fail = _failure(
                    arm, via if arm == "routed" else None, ptype, x["grade"], x["err"], x["searches"]
                )
                checks = _checks(x["grade"], pid, ptype)
                search_cost = round(x["cost"] * 0.35, 6)
                arm_for_calls = via if arm == "routed" else arm
                calls = _tool_calls(
                    arm_for_calls, text, x["searches"], x["time"], search_cost, urls, x["err"]
                )
                if arm == "routed":
                    calls.insert(
                        0,
                        ToolCall(
                            tool="jev_route",
                            args={"choice": via, "confidence": confidence},
                            latency_ms=int(JEV_SECONDS * 1000),
                            cost_usd=JEV_COST,
                        ),
                    )
                answer = (
                    TESLA_ANSWERS[via if arm == "routed" else arm]
                    if text == TESLA
                    else _answer(text, vendor, x["grade"], domains)
                )
                runs.append(
                    Run(
                        prompt_id=pid,
                        provider=arm,
                        answer=answer,
                        citations=[Citation(url=u, title=d) for u, d in zip(urls, domains, strict=True)],
                        searches=x["searches"],
                        fetches=0 if arm_for_calls == "select" else 1,
                        tool_calls=calls,
                        latency_ms=int(x["time"] * 1000),
                        cost_search_usd=search_cost,
                        cost_model_usd=round(x["cost"] - search_cost, 6),
                        cost_usd=round(x["cost"], 6),
                        cost_estimated=arm in ESTIMATED,
                        tokens_in=int(9000 + x["searches"] * 2500),
                        tokens_out=420,
                        error="provider timeout" if x["err"] else "",
                        via=via if arm == "routed" else None,
                        route_confidence=confidence if arm == "routed" else None,
                        model="anthropic/claude-sonnet-4.5",
                    )
                )
                judgments.append(
                    Judgment(
                        prompt_id=pid,
                        provider=arm,
                        grade=x["grade"],
                        checks=checks,
                        failure_mode=fail,
                        note=_note(x["grade"], fail, checks),
                        judge_model="anthropic/claude-opus-4.1",
                    )
                )
            prompts.append(
                Prompt(
                    id=pid,
                    text=text,
                    type=ptype,
                    origin="generated",
                    as_of="2026-10-05",
                    reference=f"Illustrative reference for prompt {pid}; checked against "
                    f"{SOURCES[ptype][0].replace('{v}', v)}.",
                )
            )
            pid += 1
    meta = AuditMeta(
        slug="fixture",
        company="StackAI",
        category="Enterprise AI agent platform",
        created_at="2026-10-08T09:00:00Z",
        agent_model="anthropic/claude-sonnet-4.5",
        agent_model_note="openrouter/auto resolved once and pinned",
        judge_model="anthropic/claude-opus-4.1",
        budget={"max_tool_calls": 15, "max_seconds": 180},
        total_cost_usd=round(sum(r.cost_usd for r in runs) + 3.10, 2),
        human_agreement=None,
        human_labels_n=0,
        notes=[
            "Illustrative fixture: seeded synthetic data, not real results.",
            "Prompts were generated from public information about the company, not from its logs.",
            "Exa and Parallel costs use list prices, so they are estimates (marked ~).",
        ],
    )
    return AuditResults(meta=meta, providers=PROVIDERS, prompts=prompts, runs=runs, judgments=judgments)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(generate(args.seed).model_dump_json(indent=1) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
