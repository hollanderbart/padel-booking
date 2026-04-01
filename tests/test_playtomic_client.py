"""
Unit tests voor providers/playtomic/client.py.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from providers.playtomic.client import PlaytomicAuthError, PlaytomicClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(tmp_path, email="test@test.nl", password="pw") -> PlaytomicClient:
    cache = str(tmp_path / ".playtomic_token.json")
    return PlaytomicClient(email=email, password=password, token_cache_file=cache)


def _future_expiry(hours: int = 2) -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(hours=hours)


def _mock_response(status_code: int = 200, json_data: dict = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Token cache laden
# ---------------------------------------------------------------------------

class TestLoadCachedToken:
    def test_geen_cachebestand_doet_niets(self, tmp_path):
        client = _make_client(tmp_path)
        assert client._access_token is None

    def test_geldig_token_wordt_geladen(self, tmp_path):
        expiry = _future_expiry(2)
        cache = tmp_path / ".playtomic_token.json"
        cache.write_text(json.dumps({
            "access_token": "mijntoken123",
            "expiry": expiry.isoformat(),
        }))
        client = _make_client(tmp_path)
        assert client._access_token == "mijntoken123"

    def test_verlopen_token_wordt_niet_geladen(self, tmp_path):
        expiry = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        cache = tmp_path / ".playtomic_token.json"
        cache.write_text(json.dumps({
            "access_token": "oud_token",
            "expiry": expiry.isoformat(),
        }))
        client = _make_client(tmp_path)
        assert client._access_token is None

    def test_naive_datetime_in_cache_wordt_geaccepteerd(self, tmp_path):
        # Sla een naive datetime op (zonder timezone info)
        expiry = datetime.now() + timedelta(hours=2)
        cache = tmp_path / ".playtomic_token.json"
        cache.write_text(json.dumps({
            "access_token": "naive_token",
            "expiry": expiry.isoformat(),  # naive ISO string
        }))
        client = _make_client(tmp_path)
        assert client._access_token == "naive_token"

    def test_corrupt_json_wordt_genegeerd(self, tmp_path):
        cache = tmp_path / ".playtomic_token.json"
        cache.write_text("GEEN_JSON{{{")
        client = _make_client(tmp_path)
        assert client._access_token is None


# ---------------------------------------------------------------------------
# Token geldigheidscheck
# ---------------------------------------------------------------------------

class TestIsTokenValid:
    def test_geen_token_retourneert_false(self, tmp_path):
        client = _make_client(tmp_path)
        assert client._is_token_valid() is False

    def test_geldig_token_retourneert_true(self, tmp_path):
        client = _make_client(tmp_path)
        client._access_token = "token"
        client._token_expiry = _future_expiry(2)
        assert client._is_token_valid() is True

    def test_token_verloopt_binnen_5_minuten_retourneert_false(self, tmp_path):
        client = _make_client(tmp_path)
        client._access_token = "token"
        client._token_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=3)
        assert client._is_token_valid() is False

    def test_verlopen_token_retourneert_false(self, tmp_path):
        client = _make_client(tmp_path)
        client._access_token = "token"
        client._token_expiry = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        assert client._is_token_valid() is False


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------

class TestAuthenticate:
    def test_succesvol_inloggen_slaat_token_op(self, tmp_path):
        expiry = _future_expiry(1).isoformat().replace("+00:00", "Z")
        resp = _mock_response(200, {"access_token": "nieuw_token", "access_token_expiration": expiry})
        client = _make_client(tmp_path)
        with patch.object(client._session, "post", return_value=resp):
            client.authenticate()
        assert client._access_token == "nieuw_token"

    def test_401_gooit_playtomic_auth_error(self, tmp_path):
        resp = _mock_response(401)
        resp.raise_for_status.return_value = None  # 401 niet via raise_for_status
        client = _make_client(tmp_path)
        with patch.object(client._session, "post", return_value=resp):
            with pytest.raises(PlaytomicAuthError):
                client.authenticate()

    def test_ontbrekende_expiratie_gebruikt_standaard_1_uur(self, tmp_path):
        resp = _mock_response(200, {"access_token": "tok"})  # geen access_token_expiration
        client = _make_client(tmp_path)
        with patch.object(client._session, "post", return_value=resp):
            client.authenticate()
        assert client._token_expiry is not None
        # Expiry moet in de buurt van 1 uur vanaf nu zijn
        diff = client._token_expiry - datetime.now(tz=timezone.utc)
        assert timedelta(minutes=50) < diff < timedelta(minutes=70)

    def test_token_wordt_opgeslagen_in_cache(self, tmp_path):
        expiry = _future_expiry(1).isoformat().replace("+00:00", "Z")
        resp = _mock_response(200, {"access_token": "gecached_token", "access_token_expiration": expiry})
        client = _make_client(tmp_path)
        cache_file = tmp_path / ".playtomic_token.json"
        with patch.object(client._session, "post", return_value=resp):
            client.authenticate()
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["access_token"] == "gecached_token"

    def test_andere_http_fout_gooit_exception(self, tmp_path):
        resp = _mock_response(500)
        client = _make_client(tmp_path)
        with patch.object(client._session, "post", return_value=resp):
            with pytest.raises(Exception):
                client.authenticate()


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

class TestSearchClubs:
    def test_retourneert_lijst_van_clubs(self, tmp_path):
        clubs = [{"tenant_id": "abc", "tenant_name": "Club A"}]
        resp = _mock_response(200, clubs)
        client = _make_client(tmp_path)
        with patch.object(client._session, "get", return_value=resp):
            result = client.search_clubs(52.07, 4.65, radius_m=20000)
        assert result == clubs

    def test_stuurt_juiste_parameters(self, tmp_path):
        resp = _mock_response(200, [])
        client = _make_client(tmp_path)
        with patch.object(client._session, "get", return_value=resp) as mock_get:
            client.search_clubs(52.07, 4.65, radius_m=15000)
        params = mock_get.call_args[1]["params"]
        assert params["coordinate"] == "52.07,4.65"
        assert params["sport_id"] == "PADEL"
        assert params["radius"] == 15000

    def test_http_fout_gooit_exception(self, tmp_path):
        resp = _mock_response(500)
        client = _make_client(tmp_path)
        with patch.object(client._session, "get", return_value=resp):
            with pytest.raises(Exception):
                client.search_clubs(52.07, 4.65)


class TestGetAvailability:
    def test_retourneert_beschikbaarheidslijst(self, tmp_path):
        slots = [{"resource_id": "r1", "start_date": "2026-04-10", "slots": []}]
        resp = _mock_response(200, slots)
        client = _make_client(tmp_path)
        with patch.object(client._session, "get", return_value=resp):
            result = client.get_availability("tenant1", "2026-04-10T00:00:00", "2026-04-10T23:59:59")
        assert result == slots

    def test_stuurt_juiste_parameters(self, tmp_path):
        resp = _mock_response(200, [])
        client = _make_client(tmp_path)
        with patch.object(client._session, "get", return_value=resp) as mock_get:
            client.get_availability("t1", "2026-04-10T00:00:00", "2026-04-10T23:59:59")
        params = mock_get.call_args[1]["params"]
        assert params["tenant_id"] == "t1"
        assert params["start_min"] == "2026-04-10T00:00:00"


class TestCreatePaymentIntent:
    def test_roept_ensure_authenticated_aan(self, tmp_path):
        resp = _mock_response(200, {"id": "intent-123"})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated") as mock_auth:
            with patch.object(client._session, "post", return_value=resp):
                client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        mock_auth.assert_called_once()

    def test_retourneert_intent_met_id(self, tmp_path):
        resp = _mock_response(200, {"id": "intent-abc"})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp):
                result = client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        assert result["id"] == "intent-abc"

    def test_payload_bevat_cart(self, tmp_path):
        resp = _mock_response(200, {"id": "x"})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.create_payment_intent("tenant1", "res1", "2026-04-10T19:30:00", 90)
        payload = mock_post.call_args[1]["json"]
        assert "cart" in payload
        cart = payload["cart"][0]
        assert cart["tenant_id"] == "tenant1"
        assert cart["resource_id"] == "res1"
        assert cart["duration"] == 90


class TestSetPaymentMethod:
    def test_standaard_betaalmethode_is_at_club(self, tmp_path):
        resp = _mock_response(200, {"id": "intent-1"})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "patch", return_value=resp) as mock_patch:
                client.set_payment_method("intent-1")
        payload = mock_patch.call_args[1]["json"]
        assert payload["payment_method_id"] == "AT_CLUB"

    def test_aangepaste_betaalmethode_wordt_doorgestuurd(self, tmp_path):
        resp = _mock_response(200, {})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "patch", return_value=resp) as mock_patch:
                client.set_payment_method("intent-1", payment_method="ONLINE")
        payload = mock_patch.call_args[1]["json"]
        assert payload["payment_method_id"] == "ONLINE"


class TestConfirmBooking:
    def test_bevestigt_boeking(self, tmp_path):
        resp = _mock_response(200, {"status": "CONFIRMED"})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp):
                result = client.confirm_booking("intent-99")
        assert result["status"] == "CONFIRMED"

    def test_url_bevat_intent_id(self, tmp_path):
        resp = _mock_response(200, {})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.confirm_booking("intent-XYZ")
        url = mock_post.call_args[0][0]
        assert "intent-XYZ" in url
        assert "confirmation" in url
