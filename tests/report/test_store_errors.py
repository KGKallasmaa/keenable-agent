from pathlib import Path

from fastapi.testclient import TestClient

from searchaudit.web.app import create_app


def test_unreadable_audit_is_404_not_500(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "results.json").write_text("{not json")
    client = TestClient(create_app(tmp_path))
    assert client.get("/r/broken").status_code == 404
    assert client.get("/").status_code == 200
