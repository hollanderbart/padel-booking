"""
Unit tests voor notify.py.
"""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from notify import (
    Notifier,
    notify_booking_available,
    notify_booking_error,
    notify_no_courts_available,
    notify_session_expired,
)


# ---------------------------------------------------------------------------
# Notifier.send routing
# ---------------------------------------------------------------------------

class TestNotifierSendRouting:
    def test_supervisor_token_stuurt_ha_push(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "testtoken")
        n = Notifier()
        with patch.object(n, "_send_ha_push") as mock_ha:
            n.send("Titel", "Bericht")
        mock_ha.assert_called_once_with("Titel", "Bericht", "")

    def test_zonder_token_darwin_stuurt_macos(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        n = Notifier()
        n.platform = "Darwin"
        with patch.object(n, "_send_macos") as mock_mac:
            n.send("Titel", "Bericht", sound=True)
        mock_mac.assert_called_once_with("Titel", "Bericht", True)

    def test_zonder_token_niet_darwin_stuurt_console(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        n = Notifier()
        n.platform = "Linux"
        with patch.object(n, "_send_console") as mock_con:
            n.send("Titel", "Bericht")
        mock_con.assert_called_once_with("Titel", "Bericht")

    def test_supervisor_token_geeft_url_door(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
        n = Notifier()
        with patch.object(n, "_send_ha_push") as mock_ha:
            n.send("T", "M", url="https://pay.nl")
        mock_ha.assert_called_once_with("T", "M", "https://pay.nl")


# ---------------------------------------------------------------------------
# Notifier._send_ha_push
# ---------------------------------------------------------------------------

class TestSendHaPush:
    def _notifier_with_token(self, monkeypatch, device_id=""):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "testtoken")
        monkeypatch.setenv("HA_NOTIFY_DEVICE_ID", device_id)
        return Notifier()

    def test_post_naar_device_url_als_device_id_aanwezig(self, monkeypatch):
        n = self._notifier_with_token(monkeypatch, "mijn_telefoon")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("notify._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            n._send_ha_push("T", "M")
        url = mock_requests.post.call_args[0][0]
        assert "mobile_app_mijn_telefoon" in url

    def test_post_naar_generiek_notify_zonder_device_id(self, monkeypatch):
        n = self._notifier_with_token(monkeypatch, "")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("notify._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            n._send_ha_push("T", "M")
        url = mock_requests.post.call_args[0][0]
        assert url.endswith("/services/notify/notify")

    def test_payload_bevat_url_als_opgegeven(self, monkeypatch):
        n = self._notifier_with_token(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("notify._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            n._send_ha_push("T", "M", url="https://pay.nl")
        payload = mock_requests.post.call_args[1]["json"]
        assert payload["data"]["url"] == "https://pay.nl"

    def test_payload_bevat_geen_data_als_geen_url(self, monkeypatch):
        n = self._notifier_with_token(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("notify._requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            n._send_ha_push("T", "M")
        payload = mock_requests.post.call_args[1]["json"]
        assert "data" not in payload

    def test_http_fout_valt_terug_op_console(self, monkeypatch, capsys):
        n = self._notifier_with_token(monkeypatch)
        with patch("notify._requests") as mock_requests:
            mock_requests.post.side_effect = Exception("timeout")
            n._send_ha_push("Titel", "Bericht")
        out = capsys.readouterr().out
        assert "Titel" in out

    def test_geen_requests_valt_terug_op_console(self, monkeypatch, capsys):
        n = self._notifier_with_token(monkeypatch)
        with patch("notify._HAS_REQUESTS", False):
            n._send_ha_push("Titel", "Bericht")
        out = capsys.readouterr().out
        assert "Titel" in out


# ---------------------------------------------------------------------------
# Notifier._send_macos
# ---------------------------------------------------------------------------

class TestSendMacOS:
    def test_roept_osascript_aan(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        n = Notifier()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            n._send_macos("Titel", "Bericht", sound=True)
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "osascript"
        assert "Titel" in cmd[2]
        assert "Bericht" in cmd[2]

    def test_geen_geluid_slaat_sound_over(self, monkeypatch):
        n = Notifier()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            n._send_macos("T", "M", sound=False)
        script = mock_run.call_args[0][0][2]
        assert "sound" not in script

    def test_aanhalingstekens_in_titel_worden_geescaped(self, monkeypatch):
        n = Notifier()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            n._send_macos('Titel "met" quotes', "Bericht")
        script = mock_run.call_args[0][0][2]
        assert '\\"met\\"' in script

    def test_subprocess_fout_valt_terug_op_console(self, capsys):
        n = Notifier()
        with patch("subprocess.run", side_effect=Exception("osascript niet gevonden")):
            n._send_macos("Titel", "Bericht")
        out = capsys.readouterr().out
        assert "Titel" in out


# ---------------------------------------------------------------------------
# Notifier._send_console
# ---------------------------------------------------------------------------

class TestSendConsole:
    def test_print_titel_en_bericht(self, capsys):
        Notifier()._send_console("Mijn Titel", "Mijn Bericht")
        out = capsys.readouterr().out
        assert "Mijn Titel" in out
        assert "Mijn Bericht" in out

    def test_print_scheidingslijnen(self, capsys):
        Notifier()._send_console("T", "M")
        out = capsys.readouterr().out
        assert "=" * 60 in out


# ---------------------------------------------------------------------------
# Module-niveau notificatiefuncties
# ---------------------------------------------------------------------------

class TestNotifyFunctions:
    def test_notify_booking_available_stuurt_melding(self, monkeypatch, capsys):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with patch("notify.Notifier") as MockNotifier:
            instance = MockNotifier.return_value
            notify_booking_available("Baan 1", "19:30 - 21:00", "Club A — Straat 1", "https://pay.nl")
        instance.send.assert_called_once()
        args, kwargs = instance.send.call_args
        assert "Padelbaan geboekt" in kwargs.get("title", args[0] if args else "")

    def test_notify_booking_available_zonder_url(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with patch("notify.Notifier") as MockNotifier:
            instance = MockNotifier.return_value
            notify_booking_available("Baan 1", "19:30", "Club A — Straat 1")
        instance.send.assert_called_once()

    def test_notify_booking_available_bericht_bevat_baaninfo(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with patch("notify.Notifier") as MockNotifier:
            instance = MockNotifier.return_value
            notify_booking_available("Baan 3", "20:00 - 21:30", "Sportcentrum Boskoop — Dorpstraat 1", "https://p.nl")
        _, kwargs = instance.send.call_args
        msg = kwargs.get("message", "")
        assert "Baan 3" in msg
        assert "20:00 - 21:30" in msg
        assert "Sportcentrum Boskoop" in msg

    def test_notify_no_courts_stuurt_melding(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with patch("notify.Notifier") as MockNotifier:
            instance = MockNotifier.return_value
            notify_no_courts_available()
        instance.send.assert_called_once()
        _, kwargs = instance.send.call_args
        assert kwargs.get("sound") is False

    def test_notify_booking_error_stuurt_melding(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with patch("notify.Notifier") as MockNotifier:
            instance = MockNotifier.return_value
            notify_booking_error("Timeout bij inloggen")
        instance.send.assert_called_once()
        _, kwargs = instance.send.call_args
        assert "Timeout bij inloggen" in kwargs.get("message", "")

    def test_notify_session_expired_stuurt_melding(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with patch("notify.Notifier") as MockNotifier:
            instance = MockNotifier.return_value
            notify_session_expired()
        instance.send.assert_called_once()
        _, kwargs = instance.send.call_args
        assert "verlopen" in kwargs.get("message", "").lower() or "verlopen" in kwargs.get("title", "").lower()
