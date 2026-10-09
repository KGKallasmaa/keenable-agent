import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from searchaudit.web.app import create_app

from .conftest import FIXTURE, make_results


@pytest.fixture
def audits(tmp_path):
    (tmp_path / "fixture").mkdir()
    shutil.copy(FIXTURE, tmp_path / "fixture" / "results.json")
    (tmp_path / "mini").mkdir()
    (tmp_path / "mini" / "results.json").write_text(
        make_results({"exa": "c", "keen": "cw", "select": "w"}).model_dump_json()
    )
    return tmp_path


@pytest.fixture
def client(audits):
    return TestClient(create_app(audits, show_fixture=False))


def test_spacex_pitch_page(client):
    html = client.get("/r/spacex-pitch").text
    assert "Keenable for SpaceX compliance" in html
    assert "best back end model" in html
    assert ">Accuracy<" in html
    assert ">Cost<" in html
    assert ">Speed<" in html
    assert "General results" not in html
    assert "Results by accuracy" not in html
    assert "Out of budget" in html
    assert "Wrong answer it tests for" in html
    assert "<th>Answer</th>" not in html  # the plan column is the trap, not the reference answer
    # The 100 prompts are listed once, in By prompt, not again under each tab.
    assert "Correct answers for each question" not in html
    assert "How long each question took" not in html
    assert "By prompt" in html
    assert "Dollars and time" in html
    assert "Dollars and correct answers" in html
    assert "Final thoughts" in html
    assert "Summary" in html
    assert "typesafe/jev-1.13" in html
    assert "Canada" in html
    assert "Exa 59" not in html
    assert "first Starship flight to reach orbit" not in html  # the consumer 60 stay off this page
    assert "Five ways to read" not in html
    method = client.get("/methodology").text
    assert "one choice question" in method
    assert "about 90 seconds" in method
    assert "does not describe this router" not in method
    assert client.get("/").text.count("/r/spacex-pitch") == 1


def test_homepage_spacex_row_opens_the_merged_report(audits):
    spacex = audits / "spacex"
    spacex.mkdir()
    (spacex / "results.json").write_text((audits / "mini" / "results.json").read_text())
    client = TestClient(create_app(audits, show_fixture=False))
    html = client.get("/").text
    assert 'href="/r/spacex-pitch"' not in html
    assert html.count('href="/r/spacex"') == 2
    old = client.get("/r/spacex-pitch", follow_redirects=False)
    assert old.status_code == 302
    assert old.headers["location"] == "/r/spacex"
    merged = client.get("/r/spacex").text
    assert "Keenable for SpaceX compliance" in merged
    assert "Final thoughts" in merged
    assert ">p99<" not in merged


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_diplomat_route_is_gone(audits):
    slug = audits / "diplomat"
    slug.mkdir()
    (slug / "gap.json").write_text(json.dumps({"kind": "gap", "company": "Model Diplomat"}))
    client = TestClient(create_app(audits, show_fixture=False))
    assert "/r/diplomat" not in client.get("/").text
    assert client.get("/r/diplomat").status_code == 404


def test_home_hides_fixture_unless_asked(audits, client):
    html = client.get("/").text
    assert "/r/mini" in html and "Acme" in html and "Testing" in html
    assert "/r/fixture" not in html
    shown = TestClient(create_app(audits, show_fixture=True)).get("/").text
    assert "/r/fixture" in shown


def test_report_page(client):
    r = client.get("/r/fixture")
    assert r.status_code == 200
    html = r.text
    assert "StackAI" in html and "Enterprise AI agent platform" in html
    assert 'id="report-data"' in html and "/static/report.js" in html
    assert html.count('role="tab"') == 7
    assert 'role="button"' in html and 'aria-expanded="false"' in html
    assert 'aria-modal="true"' in html
    assert "Not measured yet" in html  # human_agreement is null
    assert "Illustrative fixture" in html
    assert "icons.duckduckgo.com/ip3/exa.ai.ico" in html
    assert "Recommended router" in html
    assert "Exa MCP" in html  # provider "via" in How we measured


def test_report_embeds_safe_json(client):
    html = client.get("/r/fixture").text
    payload = html.split('id="report-data">', 1)[1].split("</script>", 1)[0]
    assert "<" not in payload


def test_prompt_page(client):
    r = client.get("/r/fixture/q/23")
    assert r.status_code == 200
    assert "Who is Tesla&#39;s current CTO?" in r.text
    assert 'aria-expanded="true"' in r.text
    assert "/r/fixture/q/22" in r.text and "/r/fixture/q/24" in r.text
    assert client.get("/r/fixture/q/999").status_code == 404


def test_print_view_stacks_every_section(client):
    html = client.get("/r/fixture/print").text
    for title in (
        "Prompts",
        "Where providers disagree",
        "Failure modes",
        "Head-to-head",
        "Implementation guide",
        "Where one call is the table",
        "query_time",
        "Judge",
        "How we measured",
    ):
        assert title in html
    assert 'class="printdoc"' in html and 'role="tab"' not in html


def test_results_json_download(client, fixture_results):
    r = client.get("/r/fixture/results.json")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert r.json()["meta"]["slug"] == fixture_results.meta.slug


def test_pdf_404_until_generated(audits, client):
    assert client.get("/r/mini.pdf").status_code == 404
    (audits / "mini" / "report.pdf").write_bytes(b"%PDF-1.4 test")
    r = client.get("/r/mini.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "Acme_search_audit.pdf" in r.headers["content-disposition"]


def test_methodology(client):
    r = client.get("/methodology")
    assert r.status_code == 200 and "McNemar" in r.text


@pytest.mark.parametrize(
    "path", ["/r/nope", "/r/..%2Fetc", "/r/UPPER", "/r/nope/print", "/r/nope.pdf", "/r/nope/results.json"]
)
def test_unknown_reports_404(client, path):
    assert client.get(path).status_code == 404


def test_cache_reloads_when_results_change(audits, client):
    assert "Acme" in client.get("/r/mini").text
    path = audits / "mini" / "results.json"
    res = make_results({"exa": "c", "keen": "c", "select": "c"})
    res.meta.company = "Renamed Co"
    path.write_text(res.model_dump_json())

    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))
    assert "Renamed Co" in client.get("/r/mini").text


def test_report_with_no_search_baseline_renders_lift(audits):
    from .test_model import _with_baseline

    (audits / "base").mkdir()
    (audits / "base" / "results.json").write_text(
        _with_baseline({"exa": "c", "keen": "ccw", "select": "w"}, base="w", tiers=True).model_dump_json()
    )
    c = TestClient(create_app(audits, show_fixture=False))
    for path in ("/r/base", "/r/base/print", "/r/base/q/1"):
        assert c.get(path).status_code == 200, path
    html = c.get("/r/base").text
    assert "Accuracy" in html and 'id="scatter"' in html and 'id="graph-cost"' in html
    assert 'id="graph-time"' in html and 'id="graph-acc"' in html and 'id="map-3d"' in html
    assert 'id="graph-fail"' in html and 'id="graph-search"' in html and 'id="scatter-ca"' in html
    assert 'data-view="fail"' in html and "hollow dot" in html and 'data-panel="search"' in html
    assert "plot-3d" not in html and "strip-cost" not in html and "$ / correct" in html
    assert "Routine vs hard prompts" not in html and "Search lift" not in html
    head = html.split('class="head"', 1)[1].split("</div>", 1)[0]
    assert "No search" not in head
    assert 'class="mono-badge"' in html  # no domain -> monogram
    h1 = html.split('id="headline"', 1)[1].split("</h1>", 1)[0]
    assert "No search" not in h1


def test_spacex_pitch_print_view_shows_every_panel(client):
    resp = client.get("/r/spacex/print")
    assert resp.status_code == 200
    html = resp.text
    assert 'class="printdoc"' in html
    assert "history.replaceState" not in html  # no tab script, so all three panels print
    assert client.get("/r/spacex.pdf").status_code in (200, 404)
