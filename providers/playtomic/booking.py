"""
Playtomic boekingslogica.
Gebruikt de Playtomic REST API — geen browser nodig.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

from providers.base import ProviderResult
from providers.playtomic.client import PlaytomicAuthError, PlaytomicClient

logger = logging.getLogger(__name__)

def _format_address(address: dict) -> str:
    """Formatteert een Playtomic adres-object naar een leesbare string."""
    parts = [address.get("street", ""), address.get("city", "")]
    return ", ".join(p for p in parts if p)


WEEKDAYS = {
    "monday": 0, "maandag": 0,
    "tuesday": 1, "dinsdag": 1,
    "wednesday": 2, "woensdag": 2,
    "thursday": 3, "donderdag": 3,
    "friday": 4, "vrijdag": 4,
    "saturday": 5, "zaterdag": 5,
    "sunday": 6, "zondag": 6,
}


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

    def _get_upcoming_booking_dates(self, count: int = 3) -> list:
        target_day_name = self._booking["day"].lower()
        target_weekday = WEEKDAYS.get(target_day_name)
        if target_weekday is None:
            raise ValueError(f"Ongeldige dag in config: {target_day_name}")

        time_start_str = self._booking["time_start"]
        target_hour, target_minute = map(int, time_start_str.split(":"))

        today = datetime.now()
        days_ahead = (target_weekday - today.weekday()) % 7

        if days_ahead == 0:
            slot_time = today.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            if today >= slot_time:
                days_ahead = 7

        first_date = today + timedelta(days=days_ahead)
        return [first_date + timedelta(weeks=i) for i in range(count)]

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
            geo = club.get("address", {}).get("geo_location") or club.get("geo_location") or {}
            try:
                lat = float(geo.get("lat") or geo.get("latitude") or 0)
                lon = float(geo.get("lon") or geo.get("longitude") or 0)
                if lat == 0 and lon == 0:
                    return float("inf")
                return self._haversine_km(origin_lat, origin_lon, lat, lon)
            except Exception:
                return float("inf")

        sorted_clubs = sorted(clubs, key=distance)
        for club in sorted_clubs:
            geo = club.get("address", {}).get("geo_location") or club.get("geo_location") or {}
            try:
                lat = float(geo.get("lat") or geo.get("latitude") or 0)
                lon = float(geo.get("lon") or geo.get("longitude") or 0)
                d = self._haversine_km(origin_lat, origin_lon, lat, lon) if lat and lon else None
                dist_str = f"{d:.1f} km" if d is not None else "onbekend"
            except Exception:
                dist_str = "onbekend"
            logger.debug("Club: %s — %s", club.get("tenant_name", "?"), dist_str)
        return sorted_clubs

    # ------------------------------------------------------------------
    # Slot zoeken
    # ------------------------------------------------------------------

    def _find_slot(self, tenant: dict, booking_date: datetime) -> Optional[dict]:
        """
        Zoek een beschikbaar slot bij een club op de opgegeven datum.

        Returns:
            Dict met slot-info of None als er geen slot is.
        """
        tenant_id = tenant["tenant_id"]
        time_start = self._booking["time_start"]
        time_end = self._booking["time_end"]
        duration_minutes = int(self._booking.get("duration_minutes", 90))
        court_type = self._booking.get("court_type", "").lower()  # "indoor" of "outdoor"

        # Bouw resource-map op uit tenant data: resource_id → {name, type}
        resource_map = {}
        for r in tenant.get("resources", []):
            resource_map[r["resource_id"]] = {
                "name": r.get("name", "Padelbaan"),
                "type": r.get("properties", {}).get("resource_type", "").lower(),
            }

        start_min = booking_date.strftime("%Y-%m-%dT00:00:00")
        start_max = booking_date.strftime("%Y-%m-%dT23:59:59")

        try:
            slots = self._client.get_availability(tenant_id, start_min, start_max)
        except Exception as e:
            logger.warning("Beschikbaarheid opvragen mislukt voor %s: %s", tenant.get("tenant_name"), e)
            return None

        # Gewenst tijdvenster in minuten
        start_h, start_m = map(int, time_start.split(":"))
        end_h, end_m = map(int, time_end.split(":"))
        window_start = start_h * 60 + start_m
        window_end = end_h * 60 + end_m

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

            for slot in court.get("slots", []):
                start_time = slot.get("start_time", "")  # "HH:MM:SS"
                if not start_time:
                    continue

                try:
                    h, m, _ = start_time.split(":")
                    slot_minutes = int(h) * 60 + int(m)
                except Exception:
                    continue

                slot_duration = slot.get("duration", 0)

                logger.debug(
                    "Slot: %s %s %s → %02d:%02d, duur=%s min",
                    resource_name, start_date, start_time,
                    slot_minutes // 60, slot_minutes % 60, slot_duration,
                )

                if not (window_start <= slot_minutes < window_end):
                    continue

                if slot_duration != duration_minutes:
                    continue

                full_start = f"{start_date}T{start_time}"
                logger.info(
                    "Playtomic slot gevonden: %s — %s om %s (resource: %s, type: %s)",
                    tenant.get("tenant_name"), resource_name, full_start, resource_id, resource_type,
                )
                return {
                    "tenant_id": tenant_id,
                    "tenant_name": tenant.get("tenant_name", ""),
                    "tenant_address": _format_address(tenant.get("address", {})),
                    "resource_id": resource_id,
                    "resource_name": resource_name,
                    "start_time": full_start,
                    "duration_minutes": duration_minutes,
                }

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
            return ProviderResult(
                success=False,
                provider="playtomic",
                error="Geen latitude/longitude in config — vereist voor Playtomic",
            )

        radius_km = location.get("radius_km", 20)
        weeks_ahead = self._booking.get("weeks_ahead", 4)
        booking_dates = self._get_upcoming_booking_dates(count=weeks_ahead)

        skip_dates = set(self._booking.get("skip_dates", []))
        if skip_dates:
            before = len(booking_dates)
            booking_dates = [d for d in booking_dates if d.strftime("%Y-%m-%d") not in skip_dates]
            logger.info(
                "skip_booked_dates: %d datum(s) overgeslagen %s",
                before - len(booking_dates),
                sorted(skip_dates),
            )

        if not booking_dates:
            logger.info("Alle doeldata al geboekt — geen verdere actie nodig")
            return ProviderResult(
                success=False,
                provider="playtomic",
                error="Alle doeldata al geboekt (skip_booked_dates)",
            )

        logger.info(
            "Zoeken op Playtomic binnen %d km van (%.4f, %.4f)", radius_km, lat, lon
        )

        try:
            clubs = self._client.search_clubs(lat, lon, radius_m=radius_km * 1000)
        except PlaytomicAuthError as e:
            return ProviderResult(success=False, provider="playtomic", error=str(e))
        except Exception as e:
            return ProviderResult(
                success=False,
                provider="playtomic",
                error=f"Clubs ophalen mislukt: {e}",
            )

        if not clubs:
            return ProviderResult(
                success=False,
                provider="playtomic",
                error="Geen Playtomic clubs gevonden in de regio",
            )

        logger.info("%d Playtomic club(s) gevonden", len(clubs))
        clubs = self._sort_clubs_by_distance(clubs, lat, lon)

        for booking_date in booking_dates:
            date_str = booking_date.strftime("%d-%m-%Y")
            logger.info("Probeer datum: %s", date_str)

            for club in clubs:
                slot_info = self._find_slot(club, booking_date)
                if not slot_info:
                    continue

                if self._dry_run:
                    logger.info("Dry-run: slot gevonden maar boeking overgeslagen")
                    return ProviderResult(
                        success=False,
                        provider="playtomic",
                        error="dry_run — slot gevonden maar niet geboekt",
                    )

                start_dt = datetime.fromisoformat(slot_info["start_time"])
                time_str = start_dt.strftime("%H:%M")
                end_dt = start_dt + timedelta(minutes=slot_info["duration_minutes"])
                end_str = end_dt.strftime("%H:%M")
                date_str_iso = start_dt.strftime("%Y-%m-%d")

                club_url = (
                    f"https://app.playtomic.io/tenant/{slot_info['tenant_id']}"
                    f"?sport=PADEL&date={date_str_iso}&startTime={time_str}"
                    f"&resourceId={slot_info['resource_id']}"
                )

                logger.info(
                    "Beschikbaar slot gevonden bij %s op %s %s-%s — %s",
                    slot_info["tenant_name"], date_str, time_str, end_str, club_url,
                )

                return ProviderResult(
                    success=True,
                    provider="playtomic",
                    booked_date=booking_date.strftime("%Y-%m-%d"),
                    slot_info={
                        "club_name": slot_info["tenant_name"],
                        "club_address": slot_info["tenant_address"],
                        "court_name": slot_info["resource_name"],
                        "time_range": f"{time_str} - {end_str} ({slot_info['duration_minutes']} minuten)",
                        "payment_url": club_url,
                    },
                )

        return ProviderResult(
            success=False,
            provider="playtomic",
            error=f"Geen slot gevonden op Playtomic voor {len(booking_dates)} datum(s)",
        )
