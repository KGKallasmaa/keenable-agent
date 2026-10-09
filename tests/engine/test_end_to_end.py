"""Engine output feeds the report: run -> judge -> assemble with fakes, then serve results.json."""

from pathlib import Path

from fastapi.testclient import TestClient

from searchaudit.config import Settings, load_config
from searchaudit.models import AuditResults, Judgment, Run
from searchaudit.notes import method_notes
from searchaudit.web.app import create_app

from .fakes import FakeOpenRouter
from .test_cli import agent_script, audit, cli  # noqa: F401 - `audit` is a pytest fixture


async def test_assembled_results_render_in_report(audit: Path):  # noqa: F811
    fake = FakeOpenRouter(agent_script)
    for command in ("run", "judge", "assemble"):
        assert await cli([command, str(audit), "--providers", "exa,keen"], fake) == 0
    results = AuditResults.model_validate_json((audit.parent / "results.json").read_text())
    notes = " ".join(results.meta.notes)
    assert "OpenRouter server tools" in notes and "list price of $0.004" in notes
    assert "was not calibrated against hand-graded answers" in notes
    assert "SELECT" not in notes  # SELECT did not run (no Keenable key)

    client = TestClient(create_app(audits_dir=audit.parent.parent))
    slug = audit.parent.name  # the server keys reports by directory, audits/<slug>/
    home = client.get("/")
    assert home.status_code == 200 and "StackAI" in home.text
    for path in (f"/r/{slug}", f"/r/{slug}/print", f"/r/{slug}/q/1", f"/r/{slug}/results.json"):
        assert client.get(path).status_code == 200, path
    assert "OpenRouter server tools" in client.get(f"/r/{slug}").text


def test_notes_flag_trouble_and_unjudged(tmp_path: Path):
    (tmp_path / "audit.yaml").write_text(
        "slug: t\ncompany: C\ncategory: X\nproviders:\n"
        "  - {id: select, label: S, short: S, domain: keenable.ai, hex: '#000'}\n"
        "  - {id: routed, label: R, short: R, domain: keenable.ai, hex: '#000'}\n"
    )
    config = load_config(tmp_path / "audit.yaml")
    runs = [
        Run(prompt_id=1, provider="select", error="timeout"),
        Run(prompt_id=2, provider="select", budget_hit=True),
        Run(prompt_id=1, provider="routed"),
    ]
    judgments = [Judgment(prompt_id=1, provider="routed", grade="c", checks=[True] * 4)]
    notes = method_notes(config, Settings(), runs, judgments, "judge-v1", human_agreement=0.9)
    text = " ".join(notes)
    assert "S: 1 run errored and 1 hit the budget, of 2 runs" in text
    assert "2 runs have no judgment yet" in text
    assert "SELECT is experimental" in text and "one choice question" in text
    assert "OpenRouter server tools" not in text and "calibrated" not in text
