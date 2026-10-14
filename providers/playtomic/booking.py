"""
Playtomic boekingslogica.
Gebruikt de Playtomic REST API — geen browser nodig.
"""

import logging
import math
from datetime import datetime, timedelta

from providers.base import (
    ProviderResult,
    booking_days,
    hhmm_to_minutes,
    upcoming_booking_dates,
    without_skip_dates,
)
from providers.playtomic.client import NoSuitablePaymentMethodError, PlaytomicAuthError, PlaytomicClient

logger = logging.getLogger(__name__)

PROVIDER = "playtomic"


def format_address(address: dict) -> str:
    """Formatteert een Playtomic adres-object naar een leesbare string."""
    parts = [address.get("street", ""), address.get("city", "")]
    return ", ".join(p for p in parts if p)


def _club_coordinates(club: dict) -> tuple[float, float] | None:
    """Lees (lat, lon) uit een club; None als onbekend of (0, 0)."""
    geo = club.get("address", {}).get("geo_location") or club.get("geo_location") or {}
    try:
        lat = float(geo.get("lat") or geo.get("latitude") or 0)
        lon = float(geo.get("lon") or geo.get("longitude") or 0)
    except (TypeError, ValueError):
        return None
    if lat == 0 and lon == 0:
        return None
    return lat, lon


def _failure(error: str) -> ProviderResult:
    return ProviderResult(success=False, provider=PROVIDER, error=error)


class PlaytomicBooker:
    """Boekt padelbanen via de Playtomic REST API."""

    def __init__(self, request: dict):
        self._request = request
        self._booking = request["booking_request"]
        self._credentials = request["credentials"]
        self._provider_config = request.get("provider_config", {})
        self._dry_run = request.get("dry_run", False)

        token_cache = self._provider_config.get("token_cache_file", ".playtomic_token.json")
        self._client = PlaytomicClient(
            email=self._credentials["email"],
            password=self._credentials["password"],
            token_cache_file=token_cache,
        )

    # ------------------------------------------------------------------
    # Datumberekening
    # ------------------------------------------------------------------

    def _get_upcoming_booking_dates(self, count: int = 3) -> list[datetime]:
        return upcoming_booking_dates(booking_days(self._booking), self._booking["time_start"], count)

    # ------------------------------------------------------------------
    # Afstandsberekening
    # ------------------------------------------------------------------

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Berekent de afstand in km tussen twee coördinaten (Haversine formule)."""
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    def _sort_clubs_by_distance(self, clubs: list, origin_lat: float, origin_lon: float) -> list:
        """Sorteert clubs op afstand van de opgegeven locatie (dichtstbijzijnde eerst)."""
        def distance(club: dict) -> float:
            coords = _club_coordinates(club)
            return self._haversine_km(origin_lat, origin_lon, *coords) if coords else math.inf

        sorted_clubs = sorted(clubs, key=distance)
        for club in sorted_clubs:
            d = distance(club)
            logger.debug("Club: %s — %s", club.get("tenant_name", "?"), "onbekend" if math.isinf(d) else f"{d:.1f} km")
        return sorted_clubs

    # ------------------------------------------------------------------
    # Slot zoeken
    # ------------------------------------------------------------------

    def _find_slot(self, tenant: dict, booking_date: datetime) -> dict | None:
        """
        Zoek een beschikbaar slot bij een club op de opgegeven datum.

        Returns:
            Dict met slot-info of None als er geen slot is.
        """
        tenant_id = tenant["tenant_id"]
        duration_minutes = int(self._booking.get("duration_minutes", 90))
        court_type = self._booking.get("court_type", "").lower()  # "indoor" of "outdoor"

        # Bouw resource-map op uit tenant data: resource_id → {name, type}
        resource_map = {
            r["resource_id"]: {
                "name": r.get("name", "Padelbaan"),
                "type": r.get("properties", {}).get("resource_type", "").lower(),
            }
            for r in tenant.get("resources", [])
        }

        start_min = booking_date.strftime("%Y-%m-%dT00:00:00")
        start_max = booking_date.strftime("%Y-%m-%dT23:59:59")

        try:
            slots = self._client.get_availability(tenant_id, start_min, start_max)
        except Exception as e:  # noqa: BLE001 — één club mag de zoektocht niet afbreken
            logger.warning("Beschikbaarheid opvragen mislukt voor %s: %s", tenant.get("tenant_name"), e)
            return None

        window_start = hhmm_to_minutes(self._booking["time_start"])
        window_end = hhmm_to_minutes(self._booking["time_end"])

        # Response structuur: lijst van court-objecten, elk met:
        #   { "resource_id": "...", "start_date": "YYYY-MM-DD", "slots": [{"start_time": "HH:MM:SS", "duration": 90, "price": "..."}, ...] }
        for court in slots:
            resource_id = court.get("resource_id", "")
            start_date = court.get("start_date", "")  # "YYYY-MM-DD"
            resource_info = resource_map.get(resource_id, {})
            resource_name = resource_info.get("name", "Padelbaan")
            resource_type = resource_info.get("type", "")

            # Filter op baantype als geconfigureerd
            if court_type and resource_type and resource_type != court_type:
                logger.debug(
                    "Baan '%s' overgeslagen: type '%s' ≠ gevraagd '%s'",
                    resource_name, resource_type, court_type,
                )
                continue

            start_time = self._first_matching_start(court.get("slots", []), window_start, window_end, duration_minutes)
            if start_time:
                full_start = f"{start_date}T{start_time}"
                logger.info(
                    "Playtomic slot gevonden: %s — %s om %s (resource: %s, type: %s)",
                    tenant.get("tenant_name"), resource_name, full_start, resource_id, resource_type,
                )
                return {
                    "tenant_id": tenant_id,
                    "tenant_name": tenant.get("tenant_name", ""),
                    "tenant_address": format_address(tenant.get("address", {})),
                    "resource_id": resource_id,
                    "resource_name": resource_name,
                    "start_time": full_start,
                    "duration_minutes": duration_minutes,
                }

        return None

    @staticmethod
    def _first_matching_start(slots: list[dict], window_start: int, window_end: int, duration: int) -> str | None:
        """Geef de start_time ("HH:MM:SS") van het eerste slot binnen het tijdvenster met de juiste duur."""
        for slot in slots:
            start_time = slot.get("start_time", "")
            try:
                slot_minutes = hhmm_to_minutes(start_time)
            except ValueError:
                continue
            logger.debug("Slot %s, duur=%s min", start_time, slot.get("duration", 0))
            if window_start <= slot_minutes < window_end and slot.get("duration", 0) == duration:
                return start_time
        return None

    # ------------------------------------------------------------------
    # Hoofdflow
    # ------------------------------------------------------------------

    def run(self) -> ProviderResult:
        logger.info("=" * 60)
        logger.info("Playtomic provider gestart")
        logger.info("=" * 60)

        location = self._booking.get("location", {})
        lat = location.get("latitude")
        lon = location.get("longitude")

        if not lat or not lon:
            return _failure("Geen latitude/longitude in config — vereist voor Playtomic")

        radius_km = location.get("radius_km", 20)
        booking_dates = without_skip_dates(
            self._get_upcoming_booking_dates(count=self._booking.get("weeks_ahead", 4)),
            self._booking.get("skip_dates", []),
        )
        if not booking_dates:
            logger.info("Alle doeldata al geboekt — geen verdere actie nodig")
            return _failure("Alle doeldata al geboekt (skip_booked_dates)")

        logger.info(
            "Zoeken op Playtomic binnen %d km van (%.4f, %.4f)", radius_km, lat, lon
        )

        try:
            clubs = self._client.search_clubs(lat, lon, radius_m=radius_km * 1000)
        except PlaytomicAuthError as e:
            return _failure(str(e))
        except Exception as e:  # noqa: BLE001
            return _failure(f"Clubs ophalen mislukt: {e}")

        if not clubs:
            return _failure("Geen Playtomic clubs gevonden in de regio")

        logger.info("%d Playtomic club(s) gevonden", len(clubs))
        clubs = self._sort_clubs_by_distance(clubs, lat, lon)

        for booking_date in booking_dates:
            logger.info("Probeer datum: %s", booking_date.strftime("%d-%m-%Y"))

            for club in clubs:
                slot_info = self._find_slot(club, booking_date)
                if not slot_info:
                    continue

                if self._dry_run:
                    logger.info("Dry-run: kandidaat slot gevonden maar boeking overgeslagen")
                    return _failure("dry_run — slot gevonden maar niet geboekt")

                if self._book_slot(slot_info):
                    return self._success_result(slot_info, booking_date)

        return _failure(f"Geen beschikbaar slot op Playtomic voor {len(booking_dates)} datum(s)")

    def _book_slot(self, slot_info: dict) -> bool:
        """
        Valideer en boek via payment intent — de availability API geeft het
        rooster terug, niet de echte bezetting. De payment intent geeft een
        4xx als het slot al bezet is. Geeft True terug als de boeking bevestigd is.
        """
        club = slot_info["tenant_name"]
        start_dt = datetime.fromisoformat(slot_info["start_time"])
        end_dt = start_dt + timedelta(minutes=slot_info["duration_minutes"])
        when = f"{start_dt:%d-%m-%Y %H:%M}-{end_dt:%H:%M}"

        logger.info(
            "Kandidaat slot bij %s — %s op %s, bevestigen via payment intent...",
            club, slot_info["resource_name"], when,
        )

        try:
            intent = self._client.create_payment_intent(
                tenant_id=slot_info["tenant_id"],
                resource_id=slot_info["resource_id"],
                start_time=slot_info["start_time"],
                duration_minutes=slot_info["duration_minutes"],
            )
        except Exception as e:  # noqa: BLE001
            logger.info("Slot %s bij %s is niet beschikbaar: %s — volgende proberen", when, club, e)
            return False

        intent_id = intent.get("payment_intent_id") or intent.get("id")
        if not intent_id:
            logger.warning("Payment intent heeft geen ID — slot overgeslagen")
            return False

        try:
            self._client.set_payment_method(intent_id, intent_response=intent)
        except NoSuitablePaymentMethodError as e:
            logger.info("Geen geschikte betaalmethode voor %s: %s — volgende proberen", club, e)
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("Betaalmethode instellen mislukt voor %s: %s — volgende proberen", club, e)
            return False

        try:
            self._client.confirm_booking(intent_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Boeking bevestigen mislukt voor %s: %s — volgende proberen", club, e)
            return False

        logger.info("Boeking bevestigd bij %s op %s", club, when)
        return True

    @staticmethod
    def _success_result(slot_info: dict, booking_date: datetime) -> ProviderResult:
        start_dt = datetime.fromisoformat(slot_info["start_time"])
        end_dt = start_dt + timedelta(minutes=slot_info["duration_minutes"])
        club_url = (
            f"https://app.playtomic.io/tenant/{slot_info['tenant_id']}"
            f"?sport=PADEL&date={start_dt:%Y-%m-%d}&startTime={start_dt:%H:%M}"
            f"&resourceId={slot_info['resource_id']}"
        )
        return ProviderResult(
            success=True,
            provider=PROVIDER,
            booked_date=booking_date.strftime("%Y-%m-%d"),
            slot_info={
                "club_name": slot_info["tenant_name"],
                "club_address": slot_info["tenant_address"],
                "court_name": slot_info["resource_name"],
                "time_range": (
                    f"{start_dt:%H:%M} - {end_dt:%H:%M} ({slot_info['duration_minutes']} minuten)"
                ),
                "payment_url": club_url,
            },
        )
