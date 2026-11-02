"""Comprobaciones estáticas del dashboard (src/sentinel/static/index.html)."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

STATIC_INDEX = (
    Path(__file__).parent.parent / "src" / "sentinel" / "static" / "index.html"
)


def _client() -> TestClient:
    from sentinel.main import app

    return TestClient(app)


def test_dashboard_is_public_and_served() -> None:
    resp = _client().get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_dashboard_uses_jwt_login() -> None:
    html = _client().get("/").text
    assert "/auth/token" in html
    assert "Authorization" in html
    assert "sessionStorage" in html


def test_dashboard_has_no_hardcoded_credentials() -> None:
    html = STATIC_INDEX.read_text(encoding="utf-8")
    assert "AUTH_TOKEN" not in html
    assert "X-Sentinel-Key" not in html
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}", html)


def test_dashboard_does_not_inject_api_data_as_html() -> None:
    html = STATIC_INDEX.read_text(encoding="utf-8")
    for pattern in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert pattern not in html


def test_dashboard_forces_revalidation() -> None:
    """Browsers must revalidate the page so a new deploy is never masked by a stale copy."""
    resp = _client().get("/")
    assert resp.headers.get("cache-control") == "no-cache"
