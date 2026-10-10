"""Regression test: when a spacex audit results.json exists, the print view
(/r/spacex/print) must render the SpaceX walkthrough (via _spacex_frame.html),
not the standard audit print layout.  The web view (/r/spacex) already does
this — see test_homepage_spacex_row_opens_the_merged_report in test_web.py.
print.html lacks the {% if pitch %} branch that report.html has, so the two
views disagree.
"""
import shutil

from fastapi.testclient import TestClient

from searchaudit.web.app import create_app

from .conftest import FIXTURE, make_results


def _client(audits):
    return TestClient(create_app(audits, show_fixture=False))


def _setup(tmp_path):
    """Standard audit dirs plus a spacex dir with a results.json (merged-report state)."""
    (tmp_path / "fixture").mkdir()
    shutil.copy(FIXTURE, tmp_path / "fixture" / "results.json")
    (tmp_path / "mini").mkdir()
    (tmp_path / "mini" / "results.json").write_text(
        make_results({"exa": "c", "keen": "cw", "select": "w"}).model_dump_json()
    )
    spacex = tmp_path / "spacex"
    spacex.mkdir()
    (spacex / "results.json").write_text((tmp_path / "mini" / "results.json").read_text())
    return tmp_path


def test_spacex_with_audit_print_view_shows_pitch(tmp_path):
    audits = _setup(tmp_path)
    client = _client(audits)

    # Web view already renders the pitch (the merged report).
    web = client.get("/r/spacex")
    assert web.status_code == 200
    assert "Keenable for SpaceX compliance" in web.text
    assert "Final thoughts" in web.text
    assert "Where providers disagree" not in web.text   # standard report absent

    # Print view must agree with the web view.
    prn = client.get("/r/spacex/print")
    assert prn.status_code == 200
    assert "Keenable for SpaceX compliance" in prn.text, (
        "BUG: /r/spacex/print renders the standard audit layout, not the SpaceX pitch. "
        "report.html has a {% if pitch %} branch that includes _spacex_frame.html; "
        "print.html does not."
    )
    assert "Final thoughts" in prn.text
    assert "Where providers disagree" not in prn.text    # standard report must be absent
    assert "Implementation guide" not in prn.text
