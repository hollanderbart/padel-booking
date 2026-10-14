"""
Unit tests voor providers/meetandplay/session.py.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


from providers.meetandplay.session import SessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(tmp_path, filename=".cookies.json") -> SessionManager:
    return SessionManager(str(tmp_path / filename))


def _make_page(url="https://www.meetandplay.nl/", html=""):
    page = MagicMock()
    page.url = url
    page.content.return_value = html
    page.goto.return_value = None
    page.wait_for_timeout.return_value = None
    return page


# ---------------------------------------------------------------------------
# cookies_exist
# ---------------------------------------------------------------------------

class TestCookiesExist:
    def test_geen_bestand_retourneert_false(self, tmp_path):
        m = _make_manager(tmp_path)
        assert m.cookies_exist() is False

    def test_bestand_met_inhoud_retourneert_true(self, tmp_path):
        m = _make_manager(tmp_path)
        Path(m.cookies_file).write_text('[{"name": "session", "value": "abc"}]')
        assert m.cookies_exist() is True

    def test_leeg_bestand_retourneert_false(self, tmp_path):
        m = _make_manager(tmp_path)
        Path(m.cookies_file).touch()
        assert m.cookies_exist() is False


# ---------------------------------------------------------------------------
# load_cookies
# ---------------------------------------------------------------------------

class TestLoadCookies:
    def test_laadt_cookies_in_context(self, tmp_path):
        m = _make_manager(tmp_path)
        cookies = [{"name": "sess", "value": "xyz", "domain": ".meetandplay.nl"}]
        Path(m.cookies_file).write_text(json.dumps(cookies))

        context = MagicMock()
        result = m.load_cookies(context)

        assert result is True
        context.add_cookies.assert_called_once_with(cookies)

    def test_ongeldige_json_retourneert_false(self, tmp_path):
        m = _make_manager(tmp_path)
        Path(m.cookies_file).write_text("GEEN_JSON")

        context = MagicMock()
        result = m.load_cookies(context)

        assert result is False
        context.add_cookies.assert_not_called()

    def test_geen_bestand_retourneert_false(self, tmp_path):
        m = _make_manager(tmp_path)
        context = MagicMock()
        result = m.load_cookies(context)
        assert result is False


# ---------------------------------------------------------------------------
# save_cookies
# ---------------------------------------------------------------------------

class TestSaveCookies:
    def test_slaat_cookies_op_uit_context(self, tmp_path):
        m = _make_manager(tmp_path)
        cookies = [{"name": "sess", "value": "abc"}]
        context = MagicMock()
        context.cookies.return_value = cookies

        m.save_cookies(context)

        saved = json.loads(Path(m.cookies_file).read_text())
        assert saved == cookies

    def test_overschrijft_bestaand_bestand(self, tmp_path):
        m = _make_manager(tmp_path)
        Path(m.cookies_file).write_text(json.dumps([{"name": "oud"}]))

        new_cookies = [{"name": "nieuw", "value": "xyz"}]
        context = MagicMock()
        context.cookies.return_value = new_cookies

        m.save_cookies(context)

        saved = json.loads(Path(m.cookies_file).read_text())
        assert saved[0]["name"] == "nieuw"


# ---------------------------------------------------------------------------
# clear_cookies
# ---------------------------------------------------------------------------

class TestClearCookies:
    def test_verwijdert_bestand_als_aanwezig(self, tmp_path):
        m = _make_manager(tmp_path)
        Path(m.cookies_file).write_text("[]")
        m.clear_cookies()
        assert not Path(m.cookies_file).exists()

    def test_geen_fout_als_bestand_niet_bestaat(self, tmp_path):
        m = _make_manager(tmp_path)
        m.clear_cookies()  # moet stil falen


# ---------------------------------------------------------------------------
# is_logged_in
# ---------------------------------------------------------------------------

class TestIsLoggedIn:
    def test_inloggen_link_aanwezig_retourneert_false(self, tmp_path):
        m = _make_manager(tmp_path)
        page = _make_page(url="https://www.meetandplay.nl/")
        page.content.return_value = '<a href="/inloggen">Inloggen</a>'

        # Simuleer: inloggen-link gevonden → locator count > 0
        def locator_dispatch(selector):
            mock = MagicMock()
            if "inloggen" in selector.lower():
                mock.count.return_value = 1
            else:
                mock.count.return_value = 0
            return mock

        page.locator.side_effect = locator_dispatch

        result = m.is_logged_in(page)
        assert result is False

    def test_uitloggen_link_aanwezig_retourneert_true(self, tmp_path):
        m = _make_manager(tmp_path)
        page = _make_page(url="https://www.meetandplay.nl/")
        page.content.return_value = '<a href="/uitloggen">Uitloggen</a>'

        def locator_dispatch(selector):
            mock = MagicMock()
            # inloggen-link niet aanwezig
            if "inloggen" in selector.lower():
                mock.count.return_value = 0
            elif any(k in selector.lower() for k in ["uitloggen", "mijn-account", "reserveringen"]):
                mock.count.return_value = 1
            else:
                mock.count.return_value = 0
            return mock

        page.locator.side_effect = locator_dispatch

        result = m.is_logged_in(page)
        assert result is True

    def test_niet_op_meetandplay_navigeert_eerst(self, tmp_path):
        m = _make_manager(tmp_path)
        page = _make_page(url="https://www.google.com/")

        def locator_dispatch(selector):
            mock = MagicMock()
            mock.count.return_value = 0
            return mock

        page.locator.side_effect = locator_dispatch

        m.is_logged_in(page)
        page.goto.assert_called()
        call_url = page.goto.call_args[0][0]
        assert "meetandplay.nl" in call_url


# ---------------------------------------------------------------------------
# auto_login
# ---------------------------------------------------------------------------

def _login_browser(page):
    browser = MagicMock()
    context = MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    return browser, context


def _login_page(present: set[str], html=""):
    """Page-mock waarbij selectors die een substring uit `present` bevatten gevonden worden."""
    page = _make_page(url="https://www.meetandplay.nl/inloggen", html=html)

    def locator(selector):
        loc = MagicMock()
        loc.count.return_value = 1 if any(key in selector for key in present) else 0
        return loc

    page.locator.side_effect = locator
    return page


class TestAutoLogin:
    def test_legacy_flow_slaat_cookies_op(self, tmp_path):
        m = _make_manager(tmp_path)
        page = _login_page({'input[type="password"]', "form[wire", 'button[type="submit"]'})
        browser, context = _login_browser(page)

        with patch.object(m, "is_logged_in", return_value=True), patch.object(m, "save_cookies") as save:
            result, ok = m.auto_login(browser, "a@b.nl", "pw")

        assert ok is True
        assert result is context
        save.assert_called_once_with(context)

    def test_sso_flow_navigeert_naar_sso_link(self, tmp_path):
        m = _make_manager(tmp_path)
        page = _login_page(
            {'input[name="Login.Email"]', 'input[name="Password"]', 'button[type="submit"]'},
            html='<a href="https://meetandplay.nl/knltb-id/sso?a=1&amp;b=2">KNLTB ID</a>',
        )
        # Geen legacy wachtwoordveld op de meetandplay-pagina zelf
        original = page.locator.side_effect
        page.locator.side_effect = lambda sel: (
            MagicMock(count=MagicMock(return_value=0)) if "wire\\:model" in sel else original(sel)
        )
        browser, _ = _login_browser(page)

        with patch.object(m, "is_logged_in", return_value=True), patch.object(m, "save_cookies"):
            _, ok = m.auto_login(browser, "a@b.nl", "pw")

        assert ok is True
        page.goto.assert_any_call("https://meetandplay.nl/knltb-id/sso?a=1&b=2", wait_until="load", timeout=30000)

    def test_mislukte_login_sluit_context(self, tmp_path):
        m = _make_manager(tmp_path)
        page = _login_page(set())
        browser, context = _login_browser(page)

        with patch.object(m, "is_logged_in", return_value=False):
            result, ok = m.auto_login(browser, "a@b.nl", "pw")

        assert (result, ok) == (None, False)
        context.close.assert_called_once()

    def test_exception_tijdens_login_geeft_false(self, tmp_path):
        m = _make_manager(tmp_path)
        page = _make_page()
        page.goto.side_effect = Exception("netwerk weg")
        browser, _ = _login_browser(page)

        _, ok = m.auto_login(browser, "a@b.nl", "pw")
        assert ok is False
