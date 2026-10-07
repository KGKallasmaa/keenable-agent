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


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


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
    assert html.count('role="tab"') == 6
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
