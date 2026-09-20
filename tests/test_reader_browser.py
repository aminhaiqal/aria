from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # The normal API test image intentionally excludes browser tooling.
    PlaywrightTimeoutError = RuntimeError
    sync_playwright = None


@skipUnless(sync_playwright is not None, "Playwright is only installed in the browser test image.")
@override_settings(
    READER_FRONTEND="react",
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class ReaderBrowserTestCase(StaticLiveServerTestCase):
    def setUp(self) -> None:
        self.reader = get_user_model().objects.create_user(
            username="browser-reader",
            password="browser-reader-pass",
        )

    def test_login_reader_shell_and_logout_complete_in_a_real_browser(self) -> None:
        self.assertTrue((Path(settings.READER_FRONTEND_DIST) / ".vite/manifest.json").is_file())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            browser_errors = []
            page.on(
                "console",
                lambda message: (
                    browser_errors.append(f"console: {message.text}")
                    if message.type == "error"
                    else None
                ),
            )
            page.on("pageerror", lambda error: browser_errors.append(f"pageerror: {error}"))
            page.goto(f"{self.live_server_url}/reader/", wait_until="networkidle")

            page.get_by_label("Username").fill(self.reader.username)
            page.get_by_label("Password").fill("browser-reader-pass")
            page.get_by_role("button", name="Enter ARIA reader").click()

            try:
                page.get_by_role(
                    "heading", name="Find the passage. Verify the evidence."
                ).wait_for()
            except PlaywrightTimeoutError as error:
                rendered_text = page.locator("body").inner_text()[:2000]
                raise AssertionError(
                    f"React reader did not load at {page.url}. "
                    f"Rendered text: {rendered_text!r}. Browser errors: {browser_errors!r}"
                ) from error
            self.assertEqual(page.locator("#aria-reader-root").count(), 1)
            self.assertEqual(page.get_by_text("JPDP", exact=True).count(), 1)
            page.get_by_role("button", name="Search options").click()
            page.get_by_role("combobox", name="Method").click()
            page.get_by_role("option", name="Exact text").click()
            self.assertEqual(page.get_by_role("combobox", name="Method").inner_text(), "Exact text")

            page.evaluate(
                """
                const overflowProbe = document.createElement('div');
                overflowProbe.style.width = '1700px';
                overflowProbe.style.height = '1px';
                document.body.append(overflowProbe);
                window.scrollTo(300, 0);
                """
            )
            page.get_by_role("button", name="Ask ARIA").click()
            assistant = page.get_by_role("dialog", name="Ask ARIA")
            assistant.wait_for()
            bounds = assistant.bounding_box()
            self.assertIsNotNone(bounds)
            self.assertAlmostEqual(bounds["x"] + bounds["width"], 1280, delta=1)
            self.assertEqual(assistant.get_by_text("Evidence only", exact=True).count(), 1)
            self.assertEqual(assistant.get_by_label("Ask about the evidence").count(), 1)
            self.assertIn("All current evidence", assistant.inner_text())
            assistant.get_by_role("button", name="Close Ask ARIA").click()
            self.assertEqual(assistant.count(), 0)

            page.get_by_role("button", name="Sign out").click()
            page.get_by_role("heading", name="Read the source, not just the answer.").wait_for()
            self.assertEqual(browser_errors, [])
            browser.close()
