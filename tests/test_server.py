"""The FastAPI app: PWA routes and their ordering relative to the static mount."""

import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app, base_url="http://127.0.0.1:8104")


def test_manifest_names_the_app_and_points_at_the_icon():
    response = client.get("/manifest.json")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "clefline"
    assert body["display"] == "standalone"
    sources = [icon["src"] for icon in body["icons"]]
    assert "/icon.svg" in sources
    for source in sources:   # every icon the manifest names must actually be served
        assert client.get(source).status_code == 200, source


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


# -------------------------------------------------- caching, fonts, strict CSP

@pytest.mark.parametrize("path", [
    "/", "/index.html", "/style.css", "/app.js", "/poll.js", "/icon.svg",
    "/fonts/SpaceMono-Regular.woff2", "/sw.js", "/manifest.json", "/favicon.ico",
])
def test_the_app_shell_is_always_revalidated(path):
    """StaticFiles sends an ETag but no Cache-Control, so browsers served a stale
    app.js/style.css from heuristic freshness after an edit -- the shell is tiny,
    so make every load revalidate (a 304 when unchanged)."""
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_an_unchanged_file_revalidates_with_a_304():
    first = client.get("/style.css")
    again = client.get("/style.css", headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304


def test_fonts_are_served_from_this_origin_with_the_right_type():
    response = client.get("/fonts/SpaceMono-Bold.woff2")
    assert response.status_code == 200
    assert response.headers["content-type"] in ("font/woff2", "application/font-woff2")
    assert len(response.content) > 1000


def test_the_csp_allows_nothing_off_origin_but_images():
    csp = client.get("/").headers["content-security-policy"]
    assert "googleapis" not in csp and "gstatic" not in csp
    assert "style-src 'self'" in csp and "font-src 'self'" in csp
    assert "script-src" not in csp or "'unsafe-inline'" not in csp
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp


def test_the_service_worker_precaches_the_scripts_and_fonts():
    body = client.get("/sw.js").text
    for asset in ("/poll.js", "/app.js", "/style.css", "/fonts/SpaceMono-Regular.woff2",
                  "/fonts/SpaceMono-Bold.woff2", "/fonts/JetBrainsMono-Regular.woff2"):
        assert asset in body, asset
    assert "clefline-shell-v1'" not in body, "bump the cache name when the shell changes"
