"""
Playtomic REST API client.

Documentatie: geen officiële docs — gebaseerd op community reverse engineering.
API base: https://api.playtomic.io
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.playtomic.io"
HEADERS = {
    "X-Requested-With": "com.playtomic.web",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
}


class PlaytomicAuthError(Exception):
    pass


class PlaytomicClient:
    """REST API client voor Playtomic."""

    def __init__(self, email: str, password: str, token_cache_file: str):
        self._email = email
        self._password = password
        self._token_cache = Path(token_cache_file)
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._load_cached_token()

    # ------------------------------------------------------------------
    # Authenticatie
    # ------------------------------------------------------------------

    def _load_cached_token(self) -> None:
        if not self._token_cache.exists():
            return
        try:
            with open(self._token_cache) as f:
                data = json.load(f)
            expiry = datetime.fromisoformat(data["expiry"])
            if expiry > datetime.now(tz=timezone.utc):
                self._access_token = data["access_token"]
                self._token_expiry = expiry
                logger.info("Playtomic token geladen uit cache (verloopt %s)", expiry.isoformat())
        except Exception as e:
            logger.debug("Kon token cache niet laden: %s", e)

    def _save_cached_token(self, access_token: str, expiry: datetime) -> None:
        try:
            with open(self._token_cache, "w") as f:
                json.dump({
                    "access_token": access_token,
                    "expiry": expiry.isoformat(),
                }, f)
        except Exception as e:
            logger.warning("Kon token cache niet opslaan: %s", e)

    def _is_token_valid(self) -> bool:
        if not self._access_token or not self._token_expiry:
            return False
        # Vervalt token binnen 5 minuten? Beschouw als verlopen.
        from datetime import timedelta
        return self._token_expiry > datetime.now(tz=timezone.utc) + timedelta(minutes=5)

    def authenticate(self) -> None:
        """Log in bij Playtomic en sla het JWT token op."""
        logger.info("Inloggen bij Playtomic als %s...", self._email)
        resp = self._session.post(
            f"{API_BASE}/v3/auth/login",
            json={"email": self._email, "password": self._password},
        )
        if resp.status_code == 401:
            raise PlaytomicAuthError("Playtomic inloggen mislukt — controleer PLAYTOMIC_EMAIL/PLAYTOMIC_PASSWORD")
        resp.raise_for_status()

        data = resp.json()
        self._access_token = data["access_token"]
        expiry_str = data.get("access_token_expiration")
        if expiry_str:
            self._token_expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        else:
            from datetime import timedelta
            self._token_expiry = datetime.now(tz=timezone.utc) + timedelta(hours=1)

        self._save_cached_token(self._access_token, self._token_expiry)
        logger.info("Playtomic inloggen gelukt (token verloopt %s)", self._token_expiry.isoformat())

    def _ensure_authenticated(self) -> None:
        if not self._is_token_valid():
            self.authenticate()
        self._session.headers["Authorization"] = f"Bearer {self._access_token}"

    # ------------------------------------------------------------------
    # API calls
    # ------------------------------------------------------------------

    def search_clubs(self, lat: float, lon: float, radius_m: int = 50000) -> list[dict]:
        """Zoek clubs op locatie die padel aanbieden."""
        resp = self._session.get(
            f"{API_BASE}/v1/tenants",
            params={
                "coordinate": f"{lat},{lon}",
                "sport_id": "PADEL",
                "radius": radius_m,
                "size": 20,
            },
        )
        resp.raise_for_status()
        clubs = resp.json()
        logger.info("%d Playtomic club(s) gevonden binnen %d km", len(clubs), radius_m // 1000)
        return clubs

    def get_availability(self, tenant_id: str, start_min: str, start_max: str) -> list[dict]:
        """
        Haal beschikbare tijdsloten op voor een club.

        Args:
            tenant_id: Playtomic club ID
            start_min: ISO datetime string, bijv. "2026-04-10T00:00:00"
            start_max: ISO datetime string, bijv. "2026-04-10T23:59:59"
        """
        resp = self._session.get(
            f"{API_BASE}/v1/availability",
            params={
                "tenant_id": tenant_id,
                "sport_id": "PADEL",
                "start_min": start_min,
                "start_max": start_max,
            },
        )
        resp.raise_for_status()
        slots = resp.json()
        logger.debug("%d beschikbare slots bij tenant %s", len(slots), tenant_id)
        return slots

    def create_payment_intent(
        self,
        tenant_id: str,
        resource_id: str,
        start_time: str,
        duration_minutes: int,
    ) -> dict:
        """
        Stap 1 van boeking: maak een payment intent aan.

        Args:
            tenant_id: Playtomic club ID
            resource_id: Court/resource ID
            start_time: ISO datetime string
            duration_minutes: Speelduur in minuten
        """
        self._ensure_authenticated()
        payload = {
            "cart": [{
                "tenant_id": tenant_id,
                "resource_id": resource_id,
                "start_date": start_time,
                "duration": duration_minutes,
                "match_type": "PRIVATE",
            }]
        }
        resp = self._session.post(f"{API_BASE}/v1/payment_intents", json=payload)
        resp.raise_for_status()
        intent = resp.json()
        logger.info("Payment intent aangemaakt: %s", intent.get("id"))
        return intent

    def set_payment_method(self, intent_id: str, payment_method: str = "AT_CLUB") -> dict:
        """
        Stap 2 van boeking: selecteer betaalmethode.

        AT_CLUB = betaal ter plaatse (geen online betaling nodig).
        """
        self._ensure_authenticated()
        resp = self._session.patch(
            f"{API_BASE}/v1/payment_intents/{intent_id}",
            json={"payment_method_id": payment_method},
        )
        resp.raise_for_status()
        return resp.json()

    def confirm_booking(self, intent_id: str) -> dict:
        """Stap 3 van boeking: bevestig de boeking."""
        self._ensure_authenticated()
        resp = self._session.post(
            f"{API_BASE}/v1/payment_intents/{intent_id}/confirmation"
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info("Boeking bevestigd via Playtomic: %s", intent_id)
        return result
