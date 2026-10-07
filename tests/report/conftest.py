import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from searchaudit.models import AuditMeta, AuditResults, Judgment, Prompt, ProviderInfo, Run

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "audits" / "fixture" / "results.json"
sys.path.insert(0, str(ROOT / "scripts"))  # fake_results


@pytest.fixture(scope="session")
def fixture_results() -> AuditResults:
    return AuditResults.model_validate_json(FIXTURE.read_text())


PROVIDERS = [
    ProviderInfo(id="exa", label="Exa", short="Exa", domain="exa.ai", hex="#c2410c", uses_today=True),
    ProviderInfo(id="keen", label="Keenable search", short="Search", domain="keenable.ai", hex="#2563eb"),
    ProviderInfo(id="select", label="Keenable SELECT", short="SELECT", domain="keenable.ai", hex="#dc2626"),
]
CHECKS = {"c": [True] * 4, "p": [True, True, False, True], "w": [True, False, False, True]}


def make_results(
    grades: dict[str, str], ptype: str = "lookup", n: int = 40, cost: dict[str, float] | None = None
) -> AuditResults:
    """Small synthetic audit: every prompt gets grades[provider] (a string of c/p/w cycled over prompts)."""
    cost = cost or {"exa": 0.09, "keen": 0.02, "select": 0.03}
    prompts, runs, judgments = [], [], []
    for i in range(1, n + 1):
        prompts.append(Prompt(id=i, text=f"Prompt {i}?", type=ptype, reference="ref"))
        for p in PROVIDERS:
            g = grades[p.id][(i - 1) % len(grades[p.id])]
            runs.append(
                Run(
                    prompt_id=i,
                    provider=p.id,
                    answer=f"answer {i}",
                    latency_ms=1000 + 100 * i,
                    cost_usd=cost[p.id],
                    cost_estimated=p.id == "exa",
                    searches=2,
                )
            )
            judgments.append(
                Judgment(
                    prompt_id=i,
                    provider=p.id,
                    grade=g,
                    checks=CHECKS[g],
                    failure_mode=None if g == "c" else "wrong_source",
                    note="n",
                )
            )
    meta = AuditMeta(
        slug="mini",
        company="Acme",
        category="Testing",
        created_at="2026-10-01T00:00:00Z",
        agent_model="m",
        judge_model="j",
    )
    return AuditResults(meta=meta, providers=PROVIDERS, prompts=prompts, runs=runs, judgments=judgments)


@pytest.fixture
def results_factory() -> Callable[..., AuditResults]:
    return make_results
