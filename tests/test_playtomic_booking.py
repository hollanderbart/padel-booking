"""
Unit tests voor providers/playtomic/booking.py.
"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from providers.playtomic.booking import PlaytomicBooker
from providers.playtomic.client import PlaytomicAuthError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    day="thursday",
    time_start="19:30",
    time_end="21:00",
    duration=90,
    lat=52.07,
    lon=4.65,
    dry_run=False,
    weeks_ahead=4,
) -> dict:
    return {
        "booking_request": {
            "location": {"city": "Boskoop", "radius_km": 20, "latitude": lat, "longitude": lon},
            "day": day,
            "time_start": time_start,
            "time_end": time_end,
            "duration_minutes": duration,
            "court_type": "indoor",
            "game_type": "double",
            "weeks_ahead": weeks_ahead,
        },
        "credentials": {"email": "test@test.nl", "password": "pw"},
        "provider_config": {"token_cache_file": "/tmp/.playtomic_token.json"},
        "dry_run": dry_run,
    }


def _make_booker(request: dict = None) -> PlaytomicBooker:
    req = request or _make_request()
    with patch("providers.playtomic.booking.PlaytomicClient"):
        booker = PlaytomicBooker(req)
    return booker


def _make_club(tenant_id="t1", name="Club A", lat=52.07, lon=4.65, address="Straat 1", resource_id="r1", resource_type="indoor") -> dict:
    return {
        "tenant_id": tenant_id,
        "tenant_name": name,
        "address": {
            "street": address,
            "city": "Teststad",
            "geo_location": {"lat": lat, "lon": lon},
        },
        "resources": [
            {
                "resource_id": resource_id,
                "name": "Baan 1",
                "properties": {"resource_type": resource_type},
            }
        ],
    }


def _make_availability(start_time="19:30:00", duration=90, resource_id="r1", start_date="2026-04-10") -> list:
    return [{
        "resource_id": resource_id,
        "start_date": start_date,
        "slots": [{"start_time": start_time, "duration": duration}],
    }]


# ---------------------------------------------------------------------------
# _haversine_km
# ---------------------------------------------------------------------------

class TestHaversineKm:
    def test_zelfde_punt_is_nul(self):
        assert PlaytomicBooker._haversine_km(52.07, 4.65, 52.07, 4.65) == pytest.approx(0.0)

    def test_amsterdam_rotterdam_circa_57km(self):
        # Amsterdam (52.3676, 4.9041) → Rotterdam (51.9244, 4.4777) ≈ 57 km
        d = PlaytomicBooker._haversine_km(52.3676, 4.9041, 51.9244, 4.4777)
        assert 55 < d < 60

    def test_retourneert_float(self):
        d = PlaytomicBooker._haversine_km(52.0, 4.0, 53.0, 5.0)
        assert isinstance(d, float)

    def test_symmetrisch(self):
        d1 = PlaytomicBooker._haversine_km(52.07, 4.65, 52.37, 4.90)
        d2 = PlaytomicBooker._haversine_km(52.37, 4.90, 52.07, 4.65)
        assert d1 == pytest.approx(d2)


# ---------------------------------------------------------------------------
# _sort_clubs_by_distance
# ---------------------------------------------------------------------------

class TestSortClubsByDistance:
    def test_dichtstbijzijnde_club_staat_eerst(self):
        booker = _make_booker()
        clubs = [
            _make_club("t1", "Ver weg", lat=53.0, lon=5.0),
            _make_club("t2", "Dichtbij", lat=52.08, lon=4.66),
        ]
        sorted_clubs = booker._sort_clubs_by_distance(clubs, 52.07, 4.65)
        assert sorted_clubs[0]["tenant_name"] == "Dichtbij"
        assert sorted_clubs[1]["tenant_name"] == "Ver weg"

    def test_club_zonder_geo_staat_achteraan(self):
        booker = _make_booker()
        clubs = [
            {"tenant_id": "t1", "tenant_name": "Geen geo", "address": {}},
            _make_club("t2", "Met geo", lat=52.08, lon=4.66),
        ]
        sorted_clubs = booker._sort_clubs_by_distance(clubs, 52.07, 4.65)
        assert sorted_clubs[0]["tenant_name"] == "Met geo"
        assert sorted_clubs[1]["tenant_name"] == "Geen geo"

    def test_club_met_nul_nul_coordinaten_staat_achteraan(self):
        booker = _make_booker()
        clubs = [
            {"tenant_id": "t1", "tenant_name": "Nul nul", "address": {"geo_location": {"lat": 0, "lon": 0}}},
            _make_club("t2", "Geldig", lat=52.08, lon=4.66),
        ]
        sorted_clubs = booker._sort_clubs_by_distance(clubs, 52.07, 4.65)
        assert sorted_clubs[0]["tenant_name"] == "Geldig"

    def test_ondersteunt_geo_location_op_top_niveau(self):
        booker = _make_booker()
        clubs = [
            {"tenant_id": "t1", "tenant_name": "Top-level geo", "geo_location": {"lat": 52.08, "lon": 4.66}},
        ]
        result = booker._sort_clubs_by_distance(clubs, 52.07, 4.65)
        assert len(result) == 1

    def test_retourneert_alle_clubs(self):
        booker = _make_booker()
        clubs = [_make_club(f"t{i}", f"Club {i}", lat=52.0 + i * 0.01, lon=4.65) for i in range(5)]
        result = booker._sort_clubs_by_distance(clubs, 52.07, 4.65)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# _get_upcoming_booking_dates
# ---------------------------------------------------------------------------

class TestGetUpcomingBookingDates:
    def test_retourneert_gevraagd_aantal_datums(self):
        booker = _make_booker(_make_request(day="thursday", weeks_ahead=4))
        dates = booker._get_upcoming_booking_dates(count=4)
        assert len(dates) == 4

    def test_alle_datums_vallen_op_de_juiste_dag(self):
        booker = _make_booker(_make_request(day="thursday"))
        dates = booker._get_upcoming_booking_dates(count=3)
        for d in dates:
            assert d.weekday() == 3  # 3 = donderdag

    def test_datums_zijn_een_week_uit_elkaar(self):
        booker = _make_booker(_make_request(day="thursday"))
        dates = booker._get_upcoming_booking_dates(count=3)
        assert (dates[1] - dates[0]).days == 7
        assert (dates[2] - dates[1]).days == 7

    def test_ongeldige_dag_gooit_valueerror(self):
        booker = _make_booker(_make_request(day="zaterdag"))
        booker._booking["day"] = "quatember"
        with pytest.raises(ValueError, match="Ongeldige dag"):
            booker._get_upcoming_booking_dates()

    def test_eerst_volgende_datum_ligt_in_de_toekomst(self):
        booker = _make_booker(_make_request(day="thursday"))
        dates = booker._get_upcoming_booking_dates(count=1)
        assert dates[0] > datetime.now()

    def test_nederlandse_dagnaam_werkt(self):
        booker = _make_booker(_make_request(day="donderdag"))
        dates = booker._get_upcoming_booking_dates(count=2)
        assert len(dates) == 2
        for d in dates:
            assert d.weekday() == 3


# ---------------------------------------------------------------------------
# _find_slot
# ---------------------------------------------------------------------------

class TestFindSlot:
    def test_slot_in_tijdvenster_en_juiste_duur_wordt_gevonden(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        club = _make_club()
        availability = _make_availability(start_time="19:30:00", duration=90)
        booker._client.get_availability.return_value = availability

        result = booker._find_slot(club, datetime(2026, 4, 10))

        assert result is not None
        assert result["tenant_id"] == "t1"
        assert result["start_time"] == "2026-04-10T19:30:00"
        assert result["duration_minutes"] == 90

    def test_slot_buiten_tijdvenster_wordt_overgeslagen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        club = _make_club()
        # Slot om 21:30 — buiten het venster
        availability = _make_availability(start_time="21:30:00", duration=90)
        booker._client.get_availability.return_value = availability

        result = booker._find_slot(club, datetime(2026, 4, 10))
        assert result is None

    def test_slot_met_verkeerde_duur_wordt_overgeslagen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        club = _make_club()
        availability = _make_availability(start_time="19:30:00", duration=60)  # 60 min, niet 90
        booker._client.get_availability.return_value = availability

        result = booker._find_slot(club, datetime(2026, 4, 10))
        assert result is None

    def test_api_fout_retourneert_none(self):
        booker = _make_booker()
        club = _make_club()
        booker._client.get_availability.side_effect = Exception("API timeout")

        result = booker._find_slot(club, datetime(2026, 4, 10))
        assert result is None

    def test_slot_precies_op_start_van_venster_wordt_gevonden(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        club = _make_club()
        availability = _make_availability(start_time="19:30:00", duration=90)
        booker._client.get_availability.return_value = availability

        result = booker._find_slot(club, datetime(2026, 4, 10))
        assert result is not None

    def test_slot_precies_op_einde_venster_wordt_overgeslagen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        club = _make_club()
        # 21:00 is het einde — niet inclusief
        availability = _make_availability(start_time="21:00:00", duration=90)
        booker._client.get_availability.return_value = availability

        result = booker._find_slot(club, datetime(2026, 4, 10))
        assert result is None

    def test_leeg_start_time_wordt_overgeslagen(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        club = _make_club()
        booker._client.get_availability.return_value = [{
            "resource_id": "r1",
            "start_date": "2026-04-10",
            "slots": [{"start_time": "", "duration": 90}],
        }]
        result = booker._find_slot(club, datetime(2026, 4, 10))
        assert result is None

    def test_slot_bevat_resource_en_tenant_info(self):
        booker = _make_booker(_make_request(time_start="19:30", time_end="21:00", duration=90))
        club = _make_club(tenant_id="t99", name="Club Speciaal", address="Bijzondere Laan 1", resource_id="res-42")
        availability = _make_availability(start_time="19:30:00", duration=90, resource_id="res-42")
        booker._client.get_availability.return_value = availability

        result = booker._find_slot(club, datetime(2026, 4, 10))
        assert result["tenant_id"] == "t99"
        assert result["tenant_name"] == "Club Speciaal"
        assert result["tenant_address"] == "Bijzondere Laan 1, Teststad"
        assert result["resource_id"] == "res-42"


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

class TestPlaytomicBookerRun:
    def test_geen_lat_lon_retourneert_fout(self):
        req = _make_request(lat=None, lon=None)
        req["booking_request"]["location"] = {"city": "Boskoop", "radius_km": 20}
        with patch("providers.playtomic.booking.PlaytomicClient"):
            booker = PlaytomicBooker(req)
        result = booker.run()
        assert result.success is False
        assert "latitude" in result.error.lower() or "longitude" in result.error.lower()

    def test_auth_fout_retourneert_fout(self):
        booker = _make_booker()
        booker._client.search_clubs.side_effect = PlaytomicAuthError("Ongeldige credentials")
        result = booker.run()
        assert result.success is False
        assert "Ongeldige credentials" in result.error

    def test_geen_clubs_retourneert_fout(self):
        booker = _make_booker()
        booker._client.search_clubs.return_value = []
        result = booker.run()
        assert result.success is False
        assert "geen" in result.error.lower()

    def test_dry_run_retourneert_geen_succes(self):
        booker = _make_booker(_make_request(dry_run=True, weeks_ahead=1))
        booker._client.search_clubs.return_value = [_make_club()]
        booker._client.get_availability.return_value = _make_availability()
        result = booker.run()
        assert result.success is False
        assert "dry_run" in result.error

    def test_succesvolle_boeking_retourneert_succes(self):
        booker = _make_booker(_make_request(weeks_ahead=1))
        booker._client.search_clubs.return_value = [_make_club()]
        booker._client.get_availability.return_value = _make_availability()
        # API geeft payment_intent_id terug (nieuwe structuur)
        booker._client.create_payment_intent.return_value = {"payment_intent_id": "intent-123"}
        booker._client.set_payment_method.return_value = {}
        booker._client.confirm_booking.return_value = {}

        result = booker.run()

        assert result.success is True
        assert result.provider == "playtomic"
        assert result.booked_date is not None
        assert result.slot_info["club_name"] == "Club A"
        assert "intent-123" in result.slot_info["payment_url"]

    def test_succesvolle_boeking_werkt_ook_met_id_veld(self):
        # Backwards compat: sommige API versies geven 'id' terug
        booker = _make_booker(_make_request(weeks_ahead=1))
        booker._client.search_clubs.return_value = [_make_club()]
        booker._client.get_availability.return_value = _make_availability()
        booker._client.create_payment_intent.return_value = {"id": "intent-456"}
        booker._client.set_payment_method.return_value = {}
        booker._client.confirm_booking.return_value = {}

        result = booker.run()

        assert result.success is True
        assert "intent-456" in result.slot_info["payment_url"]

    def test_set_payment_method_ontvangt_intent_response(self):
        booker = _make_booker(_make_request(weeks_ahead=1))
        booker._client.search_clubs.return_value = [_make_club()]
        booker._client.get_availability.return_value = _make_availability()
        intent_response = {"payment_intent_id": "intent-789", "available_payment_methods": []}
        booker._client.create_payment_intent.return_value = intent_response
        booker._client.set_payment_method.return_value = {}
        booker._client.confirm_booking.return_value = {}

        booker.run()

        # set_payment_method moet de intent response meekrijgen
        call_kwargs = booker._client.set_payment_method.call_args
        assert call_kwargs[1].get("intent_response") == intent_response or (
            len(call_kwargs[0]) >= 2 and call_kwargs[0][1] == intent_response
        )

    def test_booking_fout_gaat_door_naar_volgende_club(self):
        booker = _make_booker(_make_request(weeks_ahead=1))
        club_a = _make_club("t1", "Club A")
        club_b = _make_club("t2", "Club B", lat=52.09, lon=4.66)
        booker._client.search_clubs.return_value = [club_a, club_b]

        def get_availability(tenant_id, *args, **kwargs):
            return _make_availability()

        booker._client.get_availability.side_effect = get_availability

        call_count = {"n": 0}

        def create_intent(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("Boeking mislukt")
            return {"payment_intent_id": "intent-ok"}

        booker._client.create_payment_intent.side_effect = create_intent
        booker._client.set_payment_method.return_value = {}
        booker._client.confirm_booking.return_value = {}

        result = booker.run()
        assert result.success is True

    def test_search_clubs_exception_retourneert_fout(self):
        booker = _make_booker()
        booker._client.search_clubs.side_effect = Exception("Netwerk fout")
        result = booker.run()
        assert result.success is False
        assert "Clubs ophalen mislukt" in result.error

    def test_clubs_worden_gesorteerd_op_afstand(self):
        booker = _make_booker(_make_request(weeks_ahead=1))
        # t2 is dichter bij de origine (52.07, 4.65) dan t1
        club_ver = _make_club("t1", "Ver", lat=53.0, lon=5.5)
        club_dicht = _make_club("t2", "Dichtbij", lat=52.08, lon=4.66)
        booker._client.search_clubs.return_value = [club_ver, club_dicht]

        visited = []

        def get_availability(tenant_id, *args, **kwargs):
            visited.append(tenant_id)
            return []  # geen slots — gaat door

        booker._client.get_availability.side_effect = get_availability
        booker.run()

        # Dichtbij (t2) moet als eerste worden geprobeerd
        if visited:
            assert visited[0] == "t2"
