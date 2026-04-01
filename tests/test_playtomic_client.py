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
        resp = _mock_response(200, {"payment_intent_id": "intent-123"})
        client = _make_client(tmp_path)
        client._user_id = "user-1"
        with patch.object(client, "_ensure_authenticated") as mock_auth:
            with patch.object(client._session, "post", return_value=resp):
                client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        mock_auth.assert_called_once()

    def test_retourneert_intent_response(self, tmp_path):
        resp = _mock_response(200, {"payment_intent_id": "intent-abc"})
        client = _make_client(tmp_path)
        client._user_id = "user-1"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp):
                result = client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        assert result["payment_intent_id"] == "intent-abc"

    def test_payload_cart_heeft_juiste_structuur(self, tmp_path):
        resp = _mock_response(200, {"payment_intent_id": "x"})
        client = _make_client(tmp_path)
        client._user_id = "user-42"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.create_payment_intent("tenant1", "res1", "2026-04-10T19:30:00", 90)
        payload = mock_post.call_args[1]["json"]
        assert "cart" in payload
        cart_data = payload["cart"]["requested_item"]["cart_item_data"]
        assert cart_data["tenant_id"] == "tenant1"
        assert cart_data["resource_id"] == "res1"
        assert cart_data["start"] == "2026-04-10T19:30:00"
        assert cart_data["duration"] == 90

    def test_payload_bevat_cart_item_type(self, tmp_path):
        resp = _mock_response(200, {"payment_intent_id": "x"})
        client = _make_client(tmp_path)
        client._user_id = "user-1"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        payload = mock_post.call_args[1]["json"]
        assert payload["cart"]["requested_item"]["cart_item_type"] == "CUSTOMER_MATCH"

    def test_payload_bevat_user_id_op_top_niveau(self, tmp_path):
        resp = _mock_response(200, {"payment_intent_id": "x"})
        client = _make_client(tmp_path)
        client._user_id = "user-xyz"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        payload = mock_post.call_args[1]["json"]
        assert payload["user_id"] == "user-xyz"

    def test_payload_bevat_match_registrations(self, tmp_path):
        resp = _mock_response(200, {"payment_intent_id": "x"})
        client = _make_client(tmp_path)
        client._user_id = "user-abc"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        payload = mock_post.call_args[1]["json"]
        registrations = payload["cart"]["requested_item"]["cart_item_data"]["match_registrations"]
        assert len(registrations) >= 1
        assert registrations[0]["user_id"] == "user-abc"

    def test_payload_bevat_allowed_payment_methods(self, tmp_path):
        resp = _mock_response(200, {"payment_intent_id": "x"})
        client = _make_client(tmp_path)
        client._user_id = "user-1"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        payload = mock_post.call_args[1]["json"]
        assert "allowed_payment_method_types" in payload
        assert len(payload["allowed_payment_method_types"]) > 0

    def test_400_logt_response_body(self, tmp_path, caplog):
        import logging
        resp = _mock_response(400)
        resp.json.return_value = {"error": "invalid_payload", "detail": "start is required"}
        resp.ok = False
        client = _make_client(tmp_path)
        client._user_id = "user-1"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp):
                with caplog.at_level(logging.WARNING):
                    with pytest.raises(Exception):
                        client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90)
        assert any("400" in r.message or "invalid_payload" in r.message for r in caplog.records)

    def test_aangepast_aantal_spelers(self, tmp_path):
        resp = _mock_response(200, {"payment_intent_id": "x"})
        client = _make_client(tmp_path)
        client._user_id = "user-1"
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "post", return_value=resp) as mock_post:
                client.create_payment_intent("t1", "r1", "2026-04-10T19:30:00", 90, number_of_players=2)
        payload = mock_post.call_args[1]["json"]
        assert payload["cart"]["requested_item"]["cart_item_data"]["number_of_players"] == 2


class TestSetPaymentMethod:
    def test_gebruikt_selected_payment_method_id_key(self, tmp_path):
        resp = _mock_response(200, {})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "patch", return_value=resp) as mock_patch:
                client.set_payment_method("intent-1")
        payload = mock_patch.call_args[1]["json"]
        assert "selected_payment_method_id" in payload

    def test_selecteert_at_the_club_methode_uit_intent_response(self, tmp_path):
        resp = _mock_response(200, {})
        client = _make_client(tmp_path)
        intent_response = {
            "available_payment_methods": [
                {"payment_method_id": "pm-cash", "name": "At the club"},
                {"payment_method_id": "pm-card", "name": "Credit card"},
            ]
        }
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "patch", return_value=resp) as mock_patch:
                client.set_payment_method("intent-1", intent_response=intent_response)
        payload = mock_patch.call_args[1]["json"]
        assert payload["selected_payment_method_id"] == "pm-cash"

    def test_valt_terug_op_eerste_methode_als_geen_voorkeur_gevonden(self, tmp_path):
        resp = _mock_response(200, {})
        client = _make_client(tmp_path)
        intent_response = {
            "available_payment_methods": [
                {"payment_method_id": "pm-ideal", "name": "iDEAL"},
                {"payment_method_id": "pm-card", "name": "Credit card"},
            ]
        }
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "patch", return_value=resp) as mock_patch:
                client.set_payment_method("intent-1", intent_response=intent_response)
        payload = mock_patch.call_args[1]["json"]
        assert payload["selected_payment_method_id"] == "pm-ideal"

    def test_zonder_intent_response_stuurt_none_als_method_id(self, tmp_path):
        resp = _mock_response(200, {})
        client = _make_client(tmp_path)
        with patch.object(client, "_ensure_authenticated"):
            with patch.object(client._session, "patch", return_value=resp) as mock_patch:
                client.set_payment_method("intent-1")
        payload = mock_patch.call_args[1]["json"]
        assert payload["selected_payment_method_id"] is None


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
