"""
Playtomic boekingslogica.
Gebruikt de Playtomic REST API — geen browser nodig.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from providers.base import ProviderResult
from providers.playtomic.client import PlaytomicAuthError, PlaytomicClient

logger = logging.getLogger(__name__)

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

        for slot in slots:
            slot_start_str = slot.get("start_date", "")
            if not slot_start_str:
                continue

            try:
                slot_dt = datetime.fromisoformat(slot_start_str.replace("Z", "+00:00"))
                # Converteer naar lokale tijd voor vergelijking
                slot_local = slot_dt.replace(tzinfo=None)
                slot_minutes = slot_local.hour * 60 + slot_local.minute
            except Exception:
                continue

            if not (window_start <= slot_minutes < window_end):
                continue

            slot_duration = slot.get("duration", 0)
            if slot_duration != duration_minutes:
                continue

            resource_id = slot.get("resource_id", "")
            resource_name = slot.get("resource_name", "Court")

            logger.info(
                "Playtomic slot gevonden: %s om %s (baan: %s)",
                tenant.get("tenant_name"), slot_start_str, resource_name
            )
            return {
                "tenant_id": tenant_id,
                "tenant_name": tenant.get("tenant_name", ""),
                "tenant_address": tenant.get("address", {}).get("full_address", ""),
                "resource_id": resource_id,
                "resource_name": resource_name,
                "start_time": slot_start_str,
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

                try:
                    intent = self._client.create_payment_intent(
                        tenant_id=slot_info["tenant_id"],
                        resource_id=slot_info["resource_id"],
                        start_time=slot_info["start_time"],
                        duration_minutes=slot_info["duration_minutes"],
                    )
                    intent_id = intent["id"]

                    self._client.set_payment_method(intent_id, payment_method="AT_CLUB")
                    self._client.confirm_booking(intent_id)

                    payment_url = f"https://playtomic.io/booking/{intent_id}"

                    return ProviderResult(
                        success=True,
                        provider="playtomic",
                        booked_date=booking_date.strftime("%Y-%m-%d"),
                        slot_info={
                            "club_name": slot_info["tenant_name"],
                            "club_address": slot_info["tenant_address"],
                            "court_name": slot_info["resource_name"],
                            "time_range": f"{self._booking['time_start']} - {self._booking['time_end']} {slot_info['duration_minutes']} minuten",
                            "payment_url": payment_url,
                        },
                    )

                except Exception as e:
                    logger.warning(
                        "Boeking mislukt bij %s op %s: %s",
                        slot_info["tenant_name"], date_str, e,
                    )
                    continue

        return ProviderResult(
            success=False,
            provider="playtomic",
            error=f"Geen slot gevonden op Playtomic voor {len(booking_dates)} datum(s)",
        )
