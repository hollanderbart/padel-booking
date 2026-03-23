"""
Session management voor KNLTB Meet & Play.
Beheert cookies voor hergebruik van sessies.
"""

import json
import os
from pathlib import Path
from typing import Optional
from playwright.sync_api import Browser, BrowserContext, Page


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
            print(f"⚠️  Fout bij laden van cookies: {e}")
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
            print(f"✓ Cookies opgeslagen in {self.cookies_file}")
        except Exception as e:
            print(f"⚠️  Fout bij opslaan van cookies: {e}")

    def clear_cookies(self) -> None:
        """Verwijder opgeslagen cookies."""
        if self.cookies_file.exists():
            self.cookies_file.unlink()
            print("✓ Cookies verwijderd")

    def is_logged_in(self, page: Page) -> bool:
        """
        Check of de gebruiker is ingelogd op Meet & Play.

        Args:
            page: Playwright page object

        Returns:
            True als ingelogd, False anders
        """
        try:
            # Navigeer naar de homepage
            page.goto("https://www.meetandplay.nl", wait_until="networkidle", timeout=10000)

            # Check of er een logout/account knop is (indicatie dat gebruiker is ingelogd)
            # Dit moet aangepast worden op basis van de daadwerkelijke structuur van de site
            page.wait_for_timeout(2000)  # Geef de pagina tijd om te laden

            # Zoek naar account-gerelateerde elementen
            # De exacte selectors moeten worden bepaald na inspectie van de site
            account_selectors = [
                'text=/uitloggen/i',
                'text=/account/i',
                'text=/mijn account/i',
                '[data-testid*="account"]',
                '[data-testid*="logout"]',
                'a[href*="logout"]',
                'a[href*="account"]'
            ]

            for selector in account_selectors:
                try:
                    element = page.locator(selector).first
                    if element.count() > 0:
                        print("✓ Sessie is nog geldig")
                        return True
                except:
                    continue

            print("⚠️  Sessie is verlopen of gebruiker is niet ingelogd")
            return False

        except Exception as e:
            print(f"⚠️  Fout bij controleren login status: {e}")
            return False

    def manual_login(self, browser: Browser, url: str = "https://www.meetandplay.nl") -> BrowserContext:
        """
        Open een zichtbare browser voor handmatige login.
        Wacht tot de gebruiker is ingelogd en sluit de browser.

        Args:
            browser: Playwright browser object
            url: URL om te openen

        Returns:
            Browser context met de nieuwe sessie
        """
        print("\n🔐 Sessie verlopen. Browser wordt geopend voor handmatige login...")
        print("   Volg deze stappen:")
        print("   1. Log in op Meet & Play")
        print("   2. Wacht tot de homepage volledig is geladen")
        print("   3. Druk op ENTER in deze terminal om door te gaan\n")

        # Maak een nieuwe context voor de handmatige login
        context = browser.new_context()
        page = context.new_page()

        # Open de login pagina
        page.goto(url)

        # Wacht op gebruikersinvoer
        input("Druk op ENTER nadat je bent ingelogd... ")

        # Controleer of login succesvol was
        if self.is_logged_in(page):
            print("✓ Login succesvol!")
            self.save_cookies(context)
        else:
            print("⚠️  Login status onduidelijk. Cookies worden toch opgeslagen.")
            self.save_cookies(context)

        page.close()
        return context
