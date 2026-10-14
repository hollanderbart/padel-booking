"""
Session management voor KNLTB Meet & Play.
Beheert cookies voor hergebruik van sessies.
"""

import contextlib
import json
import logging
import re
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

BASE_URL = "https://www.meetandplay.nl"
LOGIN_URL = f"{BASE_URL}/inloggen"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CONTEXT_OPTIONS = {"viewport": {"width": 1920, "height": 1080}, "user_agent": USER_AGENT}
DEBUG_DIR = Path("/config/padel")

SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"]'
SSO_PASSWORD_SELECTOR = 'input[type="password"], input[name="Password"], input[name="password"]'


def accept_cookie_banner(page: Page) -> None:
    """Klik 'Alles toestaan' als de cookiebanner zichtbaar is; fouten worden genegeerd."""
    with contextlib.suppress(Exception):
        btn = page.locator('button:has-text("Alles toestaan")')
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(800)


def save_debug_screenshot(page: Page, name: str) -> None:
    path = DEBUG_DIR / name
    with contextlib.suppress(Exception):
        page.screenshot(path=str(path), full_page=True)
        logger.info("Debug screenshot opgeslagen: %s", path)


def _click_submit_if_present(page: Page, selector: str = SUBMIT_SELECTOR) -> bool:
    submit_btn = page.locator(selector)
    if submit_btn.count() == 0:
        return False
    submit_btn.first.click()
    return True


class SessionManager:
    """Beheert browser sessies en cookies."""

    def __init__(self, cookies_file: str):
        self.cookies_file = Path(cookies_file)

    def cookies_exist(self) -> bool:
        return self.cookies_file.exists() and self.cookies_file.stat().st_size > 0

    def load_cookies(self, context: BrowserContext) -> bool:
        if not self.cookies_exist():
            return False
        try:
            with open(self.cookies_file, encoding="utf-8") as f:
                cookies = json.load(f)
            context.add_cookies(cookies)
            return True
        except Exception as e:  # noqa: BLE001 — corrupte cookies → opnieuw inloggen
            logger.warning("Fout bij laden van cookies: %s", e)
            return False

    def save_cookies(self, context: BrowserContext) -> None:
        try:
            cookies = context.cookies()
            with open(self.cookies_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
            logger.info("Cookies opgeslagen in %s", self.cookies_file)
        except Exception as e:  # noqa: BLE001
            logger.warning("Fout bij opslaan van cookies: %s", e)

    def clear_cookies(self) -> None:
        if self.cookies_file.exists():
            self.cookies_file.unlink()
            logger.info("Cookies verwijderd")

    def new_context(self, browser: Browser, with_cookies: bool = True) -> BrowserContext:
        """Maak een browsercontext met vaste viewport/UA en laad eventueel opgeslagen cookies."""
        context = browser.new_context(**CONTEXT_OPTIONS)
        if with_cookies and self.cookies_exist():
            logger.info("Bestaande sessie laden uit cookies...")
            self.load_cookies(context)
        return context

    def is_logged_in(self, page: Page) -> bool:
        try:
            if "meetandplay.nl" not in page.url:
                page.goto(BASE_URL, wait_until="load", timeout=15000)
            else:
                page.wait_for_load_state("load", timeout=15000)
            page.wait_for_timeout(1500)

            if "meetandplay.nl" not in page.url:
                logger.info("Niet op meetandplay.nl na sessiecheck (%s) — als niet ingelogd beschouwen", page.url)
                return False

            login_link = page.locator(
                'a[href="https://meetandplay.nl/inloggen"], a[href="/inloggen"]'
            )
            if login_link.count() > 0:
                logger.info("Sessie verlopen: 'Inloggen'-link aanwezig in navigatie")
                return False

            for selector in [
                'a[href*="uitloggen"]',
                'a[href*="logout"]',
                'a[href*="mijn-reserveringen"]',
                'a[href*="mijn-account"]',
            ]:
                if page.locator(selector).count() > 0:
                    logger.info("Sessie geldig (gevonden: %s)", selector)
                    return True

            logger.info("Sessie lijkt geldig (geen inloggen-link gevonden)")
            return True

        except Exception as e:  # noqa: BLE001
            logger.warning("Fout bij controleren login status: %s", e)
            return False

    # ------------------------------------------------------------------
    # Automatisch inloggen
    # ------------------------------------------------------------------

    def auto_login(self, browser: Browser, email: str, password: str) -> tuple:
        logger.info("Automatisch inloggen als %s...", email)

        context = self.new_context(browser, with_cookies=False)
        page = context.new_page()

        try:
            self._submit_email(page, email)
            password_input = page.locator('input[type="password"], input[wire\\:model\\.blur="password"]')
            if password_input.count() > 0:
                self._login_legacy(page, password_input, password)
            else:
                self._login_sso(page, email, password)
            login_ok = self.is_logged_in(page)
        except Exception as e:  # noqa: BLE001
            logger.error("Fout tijdens automatisch inloggen: %s", e)
            login_ok = False

        if login_ok:
            page.close()
            logger.info("Automatisch inloggen gelukt!")
            self.save_cookies(context)
            return context, True

        logger.warning("Automatisch inloggen mislukt.")
        save_debug_screenshot(page, "debug_login_failed.png")
        page.close()
        context.close()
        return None, False

    @staticmethod
    def _submit_email(page: Page, email: str) -> None:
        page.goto(LOGIN_URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(1500)
        accept_cookie_banner(page)

        page.wait_for_selector("#eail", state="visible", timeout=10000)
        email_input = page.locator("#eail")
        email_input.fill(email)
        email_input.blur()
        page.wait_for_timeout(1000)

        if _click_submit_if_present(page, 'button[type="submit"], input[type="submit"], button.btn-primary, button:has(svg)'):
            logger.info("Submit-knop gevonden, klikken...")
        else:
            email_input.press("Enter")
        page.wait_for_timeout(3000)

    @staticmethod
    def _login_legacy(page: Page, password_input, password: str) -> None:
        logger.info("Wachtwoordveld gevonden — legacy login flow")
        password_input.first.fill(password)
        page.locator('form[wire\\:submit="submit"]').first.evaluate(
            'el => el.dispatchEvent(new Event("submit", {bubbles:true, cancelable:true}))'
        )
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(2000)

    @staticmethod
    def _login_sso(page: Page, email: str, password: str) -> None:
        sso_match = re.search(r'href="(https://meetandplay\.nl/knltb-id/sso[^"]+)"', page.content())
        if not sso_match:
            logger.warning("Geen SSO-link en geen wachtwoordveld gevonden op %s", page.url)
            save_debug_screenshot(page, "debug_login.png")
            return

        logger.info("KNLTB ID SSO redirect gevonden")
        page.goto(sso_match.group(1).replace("&amp;", "&"), wait_until="load", timeout=30000)
        page.wait_for_timeout(2000)

        username_field = page.locator(
            'input[name="Login.Email"], input[name="Username"], '
            'input[name="email"], input[type="email"]'
        )
        if username_field.count() > 0:
            logger.info("Stap A: username/email invullen op SSO pagina")
            username_field.first.fill(email)
            if _click_submit_if_present(page):
                page.wait_for_timeout(2000)

        try:
            page.wait_for_selector(SSO_PASSWORD_SELECTOR, state="visible", timeout=10000)
        except Exception:  # noqa: BLE001
            logger.warning("Wachtwoordveld niet zichtbaar na stap A op SSO pagina (%s)", page.url)

        pw_input = page.locator(SSO_PASSWORD_SELECTOR)
        if pw_input.count() == 0:
            logger.warning("Geen wachtwoordveld gevonden op SSO pagina (%s)", page.url)
            return

        logger.info("Stap B: wachtwoord invullen op SSO pagina")
        pw_input.first.fill(password)
        if _click_submit_if_present(page):
            page.wait_for_load_state("load", timeout=30000)
            page.wait_for_timeout(2000)
