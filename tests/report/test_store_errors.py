import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from searchaudit.models import AuditMeta, AuditResults
from searchaudit.web.app import create_app

from .conftest import make_results

DEGENERATE_META = {
    "slug": "deg", "company": "X", "category": "y",
    "created_at": "2024-01-01T00:00:00Z", "agent_model": "m", "judge_model": "j",
}
ONE_PROVIDER = [{"id": "exa", "label": "Exa", "short": "Exa", "domain": "exa.ai", "hex": "#f00"}]
ONE_PROMPT = [{"id": 1, "text": "hi", "type": "lookup", "reference": ""}]


def _write_audit(tmp_path: Path, slug: str, payload: dict) -> None:
    d = tmp_path / slug
    d.mkdir()
    (d / "results.json").write_text(json.dumps(payload))


def _write_valid_audit(tmp_path: Path, slug: str = "ok") -> str:
    """A well-formed audit (>= 2 providers, prompts, runs) that the store serves happily."""
    _write_audit(tmp_path, slug,
                 json.loads(make_results({"exa": "c", "keen": "cw", "select": "w"}).model_dump_json()))
    return slug


def test_unreadable_audit_is_404_not_500(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "results.json").write_text("{not json")
    client = TestClient(create_app(tmp_path))
    assert client.get("/r/broken").status_code == 404
    assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "name, payload",
    [
        # Crash 1: zero providers -> IndexError in accuracy_ties (by_accuracy[0]).
        ("empty", {**DEGENERATE_META, "slug": "deg",
                   "providers": [], "prompts": [], "runs": [], "judgments": []}),
        # Crash 2: one provider, zero prompts -> ZeroDivisionError in _guide (/ n, n == 0).
        ("no-prompts", {**DEGENERATE_META, "slug": "deg",
                        "providers": ONE_PROVIDER, "prompts": [], "runs": [], "judgments": []}),
        # Crash 3: single provider + single prompt -> StopIteration in _default_pair
        # (bare next() over the only id). This passes min_length=1, so it is caught at the store.
        ("single-provider", {**DEGENERATE_META, "slug": "deg",
                             "providers": ONE_PROVIDER, "prompts": ONE_PROMPT,
                             "runs": [], "judgments": []}),
    ],
)
def test_degenerate_audit_is_404_not_500(tmp_path: Path, name: str, payload: dict) -> None:
    _write_audit(tmp_path, "deg", payload)
    client = TestClient(create_app(tmp_path, show_fixture=False))
    assert client.get("/r/deg").status_code == 404, name
    assert client.get("/").status_code == 200, name


def test_one_degenerate_audit_does_not_poison_the_homepage(tmp_path: Path) -> None:
    """The headline guarantee: a single bad file among many must not take the listing down."""
    valid_slug = _write_valid_audit(tmp_path, "ok")
    # Also a second valid audit to confirm both healthy audits remain listed.
    _write_valid_audit(tmp_path, "ok2")
    _write_audit(tmp_path, "deg",
                 {**DEGENERATE_META, "providers": [], "prompts": [], "runs": [], "judgments": []})
    client = TestClient(create_app(tmp_path, show_fixture=False))
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert f"/r/{valid_slug}" in html          # healthy audit still listed
    assert "/r/ok2" in html                     # second healthy audit still listed
    assert "/r/deg" not in html                  # degenerate audit omitted from the listing
    assert "/r/deg" not in client.get("/").text  # homepage stays up across repeated calls


@pytest.mark.parametrize(
    "field, kwargs",
    [
        ("providers", {"providers": [], "prompts": ONE_PROMPT, "runs": [], "judgments": []}),
        ("prompts", {"providers": ONE_PROVIDER, "prompts": [], "runs": [], "judgments": []}),
    ],
)
@pytest.mark.parametrize("as_json", [True, False])
def test_audit_results_rejects_empty_providers_and_prompts(field: str, kwargs: dict, as_json: bool) -> None:
    """The schema now refuses empty providers/prompts so assemble() can't write degenerate data."""
    meta = AuditMeta(slug="d", company="X", category="y", created_at="2024-01-01T00:00:00Z",
                    agent_model="m", judge_model="j")
    payload = {"meta": meta.model_dump(), "providers": kwargs["providers"], "prompts": kwargs["prompts"],
               "runs": kwargs["runs"], "judgments": kwargs["judgments"]}
    with pytest.raises(ValidationError):
        if as_json:
            AuditResults.model_validate_json(json.dumps(payload))
        else:
            AuditResults(meta=meta, **kwargs)


def test_valid_single_provider_audit_is_not_rejected_by_schema() -> None:
    """min_length=1 only forbids empty lists; a one-of-each audit still constructs (it is the
    store's job to skip it if build_view_model cannot handle it)."""
    meta = AuditMeta(slug="d", company="X", category="y", created_at="2024-01-01T00:00:00Z",
                    agent_model="m", judge_model="j")
    AuditResults(meta=meta, providers=ONE_PROVIDER, prompts=ONE_PROMPT, runs=[], judgments=[])
