from fake_results import generate  # scripts/, put on sys.path by conftest

from searchaudit.models import AuditResults

from .conftest import FIXTURE


def test_fixture_file_is_the_generator_output():
    """audits/fixture/results.json must be regenerated when the generator changes.

    Compared as parsed models, so new optional fields with defaults do not force a regeneration."""
    assert AuditResults.model_validate_json(FIXTURE.read_text()) == generate()


def test_generator_is_seeded_and_complete():
    a, b = generate(5), generate(5)
    assert a == b
    assert a != generate(6)
    assert len(a.prompts) == 80
    assert len(a.runs) == len(a.judgments) == 80 * 5
    assert {p.id for p in a.providers} == {"exa", "parallel", "keen", "select", "routed"}


def test_routed_is_derived_from_search_or_select():
    res = generate()
    grade = {(j.prompt_id, j.provider): j.grade for j in res.judgments}
    for run in res.runs:
        if run.provider == "routed":
            assert run.via in ("keen", "select") and run.route_confidence is not None
            assert grade[(run.prompt_id, "routed")] == grade[(run.prompt_id, run.via)]
            assert run.tool_calls[0].tool == "jev_route"


def test_wrong_answers_have_a_failure_mode_and_correct_ones_none():
    for j in generate().judgments:
        assert (j.failure_mode is None) == (j.grade == "c")
