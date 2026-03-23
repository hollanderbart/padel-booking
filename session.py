"""
Session management voor KNLTB Meet & Play.
Beheert cookies voor hergebruik van sessies.
"""

import json
import logging
import os
from pathlib import Path
from playwright.sync_api import Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


class SessionManager:
    """Beheert browser sessies en cookies."""

    def __init__(self, cookies_file: str):
        """
        Initialiseer de session manager.

        Args:
            cookies_file: Pad naar het bestand om cookies in op te slaan
        """
        self.cookies_file = Path(cookies_file)

    def cookies_exist(self) -> bool:
        """Check of er opgeslagen cookies zijn."""
        return self.cookies_file.exists() and self.cookies_file.stat().st_size > 0

    def load_cookies(self, context: BrowserContext) -> bool:
        """
        Laad opgeslagen cookies in de browser context.

        Args:
            context: Playwright browser context

        Returns:
            True als cookies succesvol geladen zijn, False anders
        """
        if not self.cookies_exist():
            return False

        try:
            with open(self.cookies_file, 'r') as f:
                cookies = json.load(f)

            context.add_cookies(cookies)
            return True
        except Exception as e:
            logger.warning("Fout bij laden van cookies: %s", e)
            return False

    def save_cookies(self, context: BrowserContext) -> None:
        """
        Sla cookies van de huidige context op.

        Args:
            context: Playwright browser context
        """
        try:
            cookies = context.cookies()
            with open(self.cookies_file, 'w') as f:
                json.dump(cookies, f, indent=2)
            logger.info("Cookies opgeslagen in %s", self.cookies_file)
        except Exception as e:
            logger.warning("Fout bij opslaan van cookies: %s", e)

    def clear_cookies(self) -> None:
        """Verwijder opgeslagen cookies."""
        if self.cookies_file.exists():
            self.cookies_file.unlink()
            logger.info("Cookies verwijderd")

    def is_logged_in(self, page: Page) -> bool:
        """
        Check of de gebruiker is ingelogd op Meet & Play.

        Navigeert naar de homepage en controleert of de 'Inloggen'-knop
        aanwezig is (niet ingelogd) of afwezig is (ingelogd).

        Args:
            page: Playwright page object

        Returns:
            True als ingelogd, False anders
        """
        try:
            page.goto("https://www.meetandplay.nl", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(1500)

            # Als de gebruiker ingelogd is, verdwijnt de 'Inloggen'-link uit de nav
            # en verschijnt er een 'Uitloggen' of account-gerelateerde link.
            # De 'Inloggen'-link heeft href="https://meetandplay.nl/inloggen"
            login_link = page.locator('a[href="https://meetandplay.nl/inloggen"]')
            if login_link.count() > 0:
                logger.info("Sessie verlopen: 'Inloggen'-link aanwezig in navigatie")
                return False

            # Extra check: zoek naar account/uitloggen link
            for selector in [
                'a[href*="uitloggen"]',
                'a[href*="logout"]',
                'a[href*="mijn-reserveringen"]',
                'a[href*="mijn-account"]',
            ]:
                if page.locator(selector).count() > 0:
                    logger.info("Sessie geldig (gevonden: %s)", selector)
                    return True

            # Geen inloggen-link gevonden maar ook geen duidelijke account-link:
            # veronderstel ingelogd (login-link is weg)
            logger.info("Sessie lijkt geldig (geen inloggen-link gevonden)")
            return True

        except Exception as e:
            logger.warning("Fout bij controleren login status: %s", e)
            return False

    def auto_login(self, browser: Browser, email: str, password: str) -> BrowserContext:
        """
        Probeer automatisch in te loggen via KNLTB ID SSO.

        De login op meetandplay.nl werkt via een two-step flow:
        1. Voer e-mailadres in op /inloggen
        2. Livewire bepaalt of wachtwoord of SSO-redirect gebruikt wordt

        Args:
            browser: Playwright browser object
            email: KNLTB e-mailadres
            password: KNLTB wachtwoord

        Returns:
            Browser context met de nieuwe sessie
        """
        import re as _re

        logger.info("Automatisch inloggen als %s...", email)

        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        )
        page = context.new_page()

        try:
            page.goto("https://www.meetandplay.nl/inloggen", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)

            # Accepteer cookie banner indien aanwezig
            try:
                cookie_btn = page.locator('button:has-text("Alles toestaan")')
                if cookie_btn.count() > 0:
                    cookie_btn.first.click()
                    page.wait_for_timeout(800)
            except Exception:
                pass

            # Stap 1: vul e-mailadres in en activeer Livewire blur event
            page.wait_for_selector('#eail', state='visible', timeout=10000)
            email_input = page.locator('#eail')
            email_input.fill(email)
            email_input.blur()
            page.wait_for_timeout(2500)

            # Stap 2: check of een wachtwoordveld verscheen (legacy accounts)
            password_input = page.locator('input[type="password"], input[wire\\:model\\.blur="password"]')
            if password_input.count() > 0:
                logger.info("Wachtwoordveld gevonden — legacy login flow")
                password_input.first.fill(password)
                page.locator('form[wire\\:submit="submit"]').first.evaluate(
                    'el => el.dispatchEvent(new Event("submit", {bubbles:true, cancelable:true}))'
                )
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(2000)
            else:
                # KNLTB ID SSO flow: haal de SSO-link op uit de Livewire HTML
                html = page.content()
                sso_match = _re.search(
                    r'href="(https://meetandplay\.nl/knltb-id/sso[^"]+)"', html
                )
                if sso_match:
                    sso_url = sso_match.group(1).replace('&amp;', '&')
                    logger.info("KNLTB ID SSO redirect gevonden")
                    page.goto(sso_url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(2000)

                    # Op het KNLTB ID portaal: vul wachtwoord in
                    # Het exacte formulier hangt af van id.knltb.nl
                    pw_input = page.locator(
                        'input[type="password"], input[name="Password"], input[name="password"]'
                    )
                    if pw_input.count() > 0:
                        pw_input.first.fill(password)
                        # Submit het formulier
                        submit_btn = page.locator(
                            'button[type="submit"], input[type="submit"]'
                        )
                        if submit_btn.count() > 0:
                            submit_btn.first.click()
                            page.wait_for_load_state("networkidle", timeout=30000)
                            page.wait_for_timeout(2000)
                    else:
                        logger.warning(
                            "Geen wachtwoordveld gevonden op SSO pagina (%s)", page.url
                        )
                else:
                    logger.warning(
                        "Geen SSO-link en geen wachtwoordveld gevonden. "
                        "Handmatige login vereist."
                    )

            # Controleer of login gelukt is
            if self.is_logged_in(page):
                logger.info("Automatisch inloggen gelukt!")
                self.save_cookies(context)
            else:
                logger.warning(
                    "Automatisch inloggen mogelijk mislukt. Cookies worden toch opgeslagen."
                )
                self.save_cookies(context)

        except Exception as e:
            logger.error("Fout tijdens automatisch inloggen: %s", e)
            self.save_cookies(context)
        finally:
            page.close()

        return context

    def manual_login(self, browser: Browser, url: str = "https://www.meetandplay.nl/inloggen") -> BrowserContext:
        """
        Open een zichtbare browser voor handmatige login.
        Wacht tot de gebruiker is ingelogd en sluit de browser.

        Args:
            browser: Playwright browser object
            url: URL om te openen

        Returns:
            Browser context met de nieuwe sessie
        """
        logger.info("Browser openen voor handmatige login...")
        print("\n" + "=" * 60)
        print("HANDMATIGE LOGIN VEREIST")
        print("=" * 60)
        print("Stappen:")
        print("  1. Log in op Meet & Play in de geopende browser")
        print("  2. Wacht tot de homepage volledig geladen is")
        print("  3. Druk op ENTER in deze terminal")
        print("=" * 60 + "\n")

        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        )
        page = context.new_page()
        page.goto(url)

        input("Druk op ENTER nadat je bent ingelogd... ")

        if self.is_logged_in(page):
            logger.info("Handmatige login succesvol!")
            self.save_cookies(context)
        else:
            logger.warning("Login status onduidelijk. Cookies worden toch opgeslagen.")
            self.save_cookies(context)

        page.close()
        return context
