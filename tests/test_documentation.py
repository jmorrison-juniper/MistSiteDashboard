"""Regression checks for the landing page and its credential-free UI fixtures."""

import re
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import pytest

from scripts.offline_demo import OfflineMistConnection, dashboard_app

ROOT = Path(__file__).resolve().parents[1]


def test_landing_page_sections():
    text = (ROOT / "README.md").read_text()
    assert re.findall(r"^#+ (.+)$", text, re.MULTILINE) == [
        "What",
        "How",
        "Where",
        "When",
        "Why",
        "Who",
    ]
    assert text.count("![") >= 4


@pytest.mark.parametrize(
    "name", ["README.md", "docs/user-guide.md", "docs/offline-screenshots.md"]
)
def test_documentation_local_links(name):
    path = ROOT / name
    for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
        url = urlsplit(target)
        if url.scheme or not url.path:
            continue
        destination = path.parent / unquote(url.path)
        assert destination.is_file(), target
        if url.fragment and destination.suffix == ".md":
            headings = re.findall(r"^#+ (.+)$", destination.read_text(), re.MULTILINE)
            anchors = [
                re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                for heading in headings
            ]
            assert url.fragment in anchors, target


@pytest.mark.parametrize(
    "name", ["organization", "site", "wireless-clients", "wired-clients"]
)
def test_screenshots_are_png_files(name):
    image = ROOT / "docs" / "screenshots" / f"offline-{name}.png"
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert image.stat().st_size > 10000


@pytest.mark.parametrize(
    "route,key",
    [
        ("/", None),
        ("/sites/site-1", None),
        ("/ap-clients/site-1", None),
        ("/switch-clients/site-1", None),
        ("/api/sites", "sites"),
        ("/api/sites/site-1/health", "health"),
        ("/api/sites/site-1/wireless-clients", "sessions"),
        ("/api/sites/site-1/wired-clients", "clients"),
        ("/api/org/sle/wifi/metric/coverage", "sites"),
    ],
)
def test_offline_screenshot_routes_never_create_real_connection(route, key):
    connection = OfflineMistConnection()
    with (
        patch.object(dashboard_app, "get_mist_connection", return_value=connection),
        patch.object(
            dashboard_app,
            "MistConnection",
            side_effect=AssertionError("Live API forbidden"),
        ),
        dashboard_app.app.test_client() as client,
    ):
        response = client.get(route)
    assert response.status_code == 200
    if key:
        assert response.json["success"] is True
        assert response.json[key]


def test_demo_org_scores_match_template_contract():
    result = OfflineMistConnection().get_org_worst_sites_by_metric("coverage")
    assert result["sites"][0]["coverage"] == 0.94
    assert result["sites"][0]["site_id"] == "site-0"
