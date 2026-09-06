"""The FastAPI app: PWA routes and their ordering relative to the static mount."""

import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


def test_manifest_names_the_app_and_points_at_the_icon():
    response = client.get("/manifest.json")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "clefline"
    assert body["display"] == "standalone"
    assert body["icons"][0]["src"] == "/icon.svg"


def test_favicon_is_served_as_svg():
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"


def test_service_worker_is_served_as_javascript():
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "clefline-shell" in response.text


@pytest.mark.parametrize("path", ["/manifest.json", "/favicon.ico", "/sw.js"])
def test_pwa_routes_are_not_shadowed_by_the_static_mount(path):
    """Regression guard: these three are registered before
    `app.mount("/", StaticFiles(...))`. Starlette tries routes in registration
    order, so registering them after the mount would silently hand all three
    to the mount instead -- a 404 for manifest.json/sw.js (no such static
    file) rather than a working PWA."""
    response = client.get(path)
    assert response.status_code == 200


def test_the_static_mount_still_serves_ordinary_files():
    """The explicit routes above must not swallow anything else static/ serves."""
    response = client.get("/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_the_icon_itself_is_reachable_through_the_static_mount():
    """icon.svg has no dedicated route -- it must reach the browser purely
    via the static mount, same as style.css and app.js."""
    response = client.get("/icon.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]
