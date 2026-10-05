"""Capture unmodified offline demo pages using an optional Playwright install."""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


class ScreenshotCapture:
    SCREENS = (
        ("organization", "/", "#orgSleTableBody tr a"),
        ("site", "/sites/site-1", "#siteName"),
        ("wireless-clients", "/ap-clients/site-1", "tbody tr"),
        ("wired-clients", "/switch-clients/site-1", "tbody tr"),
    )

    @staticmethod
    def allow_request(route) -> None:
        host = urlsplit(route.request.url).hostname
        if host in {"127.0.0.1", "cdn.jsdelivr.net", "cdnjs.cloudflare.com"}:
            route.continue_()
        else:
            raise RuntimeError(f"Unexpected browser request to {host}")

    @classmethod
    def run(cls) -> None:
        output = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1080})
            page.route("**/*", cls.allow_request)
            for name, path, selector in cls.SCREENS:
                cls.capture(page, output, (name, path, selector))
            browser.close()

    @staticmethod
    def capture(page, output: Path, screen: tuple[str, str, str]) -> None:
        name, path, selector = screen
        errors: list[str] = []

        def record_error(error) -> None:
            errors.append(str(error))

        page.on("pageerror", record_error)
        response = page.goto(f"http://127.0.0.1:5055{path}", wait_until="networkidle")
        assert response is not None and response.status == 200
        page.locator(selector).first.wait_for(state="visible")
        if name == "site":
            page.wait_for_function(
                "() => document.querySelector('#siteName').textContent === 'Site 1'"
            )
        if name == "organization":
            assert "94.0%" in page.locator("#orgSleTableBody").inner_text()
        if name.endswith("clients"):
            assert "demo-laptop-1" in page.locator("tbody").inner_text()
        assert not errors, errors
        page.screenshot(path=str(output / f"offline-{name}.png"), full_page=True)
        print(f"Captured {name}: {page.title()}")
        page.remove_listener("pageerror", record_error)


if __name__ == "__main__":
    ScreenshotCapture.run()
