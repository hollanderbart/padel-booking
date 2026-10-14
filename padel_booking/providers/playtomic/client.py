"""
Playtomic REST API client.

Documentatie: geen officiële docs — gebaseerd op community reverse engineering.
API base: https://api.playtomic.io
"""

import json
import logging
from http import HTTPStatus
from datetime import datetime, timedelta, UTC
from pathlib import Path

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
REQUEST_TIMEOUT = 30  # seconden — voorkom dat een hangende verbinding de nachtelijke run blokkeert

# Alleen offline methoden werken via de API — online methoden zoals iDEAL,
# Bancontact en Swish vereisen een browser-redirect en leveren een 503 op.
OFFLINE_METHODS = {"at_the_club", "at the club", "cash", "merchant_wallet", "pay at the club", "offer"}
ONLINE_ONLY_METHODS = {"ideal", "bancontact", "swish", "paytrail", "credit_card", "quick_pay", "direct"}


class PlaytomicAuthError(Exception):
    pass


class NoSuitablePaymentMethodError(Exception):
    pass


def _log_error_body(resp: requests.Response, what: str) -> None:
    if resp.ok:
        return
    try:
        error_body = resp.json()
    except ValueError:
        error_body = resp.text
    logger.warning("%s mislukt (%s) response body: %s", what, resp.status_code, error_body)


def _method_name(method: dict) -> str:
    return (method.get("name") or method.get("payment_method_type") or "").lower()


def _method_id(method: dict) -> str | None:
    return method.get("payment_method_id") or method.get("id")


def _select_payment_method_id(methods: list[dict]) -> str | None:
    """
    Kies bij voorkeur een offline methode ('betaal ter plaatse').
    Valt terug op de eerste methode, tenzij er alleen online methoden zijn.
    """
    for method in methods:
        name = _method_name(method)
        if any(p in name for p in OFFLINE_METHODS):
            logger.info("Betaalmethode geselecteerd: %s (id: %s)", name, _method_id(method))
            return _method_id(method)

    if not methods:
        return None

    names = [_method_name(m) for m in methods]
    if all(any(o in n for o in ONLINE_ONLY_METHODS) for n in names if n):
        raise NoSuitablePaymentMethodError(
            f"Geen offline betaalmethode beschikbaar — alleen: {', '.join(names)}"
        )
    logger.info("Geen voorkeursmethode gevonden — eerste methode gebruikt: %s", methods[0].get("name"))
    return _method_id(methods[0])


class PlaytomicClient:
    """REST API client voor Playtomic."""

    def __init__(self, email: str, password: str, token_cache_file: str):
        self._email = email
        self._password = password
        self._token_cache = Path(token_cache_file)
        self._access_token: str | None = None
        self._token_expiry: datetime | None = None
        self._user_id: str | None = None
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
            with open(self._token_cache, encoding="utf-8") as f:
                data = json.load(f)
            expiry = datetime.fromisoformat(data["expiry"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry > datetime.now(tz=UTC):
                self._access_token = data["access_token"]
                self._token_expiry = expiry
                self._user_id = data.get("user_id")
                logger.info("Playtomic token geladen uit cache (verloopt %s)", expiry.isoformat())
        except (OSError, ValueError, KeyError) as e:
            logger.debug("Kon token cache niet laden: %s", e)

    def _save_cached_token(self, access_token: str, expiry: datetime, user_id: str | None = None) -> None:
        try:
            with open(self._token_cache, "w", encoding="utf-8") as f:
                json.dump({
                    "access_token": access_token,
                    "expiry": expiry.isoformat(),
                    "user_id": user_id,
                }, f)
        except OSError as e:
            logger.warning("Kon token cache niet opslaan: %s", e)

    def _is_token_valid(self) -> bool:
        if not self._access_token or not self._token_expiry:
            return False
        # Vervalt token binnen 5 minuten? Beschouw als verlopen.
        return self._token_expiry > datetime.now(tz=UTC) + timedelta(minutes=5)

    def authenticate(self) -> None:
        """Log in bij Playtomic en sla het JWT token op."""
        logger.info("Inloggen bij Playtomic als %s...", self._email)
        resp = self._session.post(
            f"{API_BASE}/v3/auth/login",
            json={"email": self._email, "password": self._password},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == HTTPStatus.UNAUTHORIZED:
            raise PlaytomicAuthError("Playtomic inloggen mislukt — controleer PLAYTOMIC_EMAIL/PLAYTOMIC_PASSWORD")
        resp.raise_for_status()

        data = resp.json()
        self._access_token = data["access_token"]
        self._user_id = data.get("user_id")
        expiry_str = data.get("access_token_expiration")
        if expiry_str:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            self._token_expiry = expiry
        else:
            self._token_expiry = datetime.now(tz=UTC) + timedelta(hours=1)

        self._save_cached_token(self._access_token, self._token_expiry, self._user_id)
        logger.info("Playtomic inloggen gelukt (user_id: %s, token verloopt %s)", self._user_id, self._token_expiry.isoformat())

    def _ensure_authenticated(self) -> None:
        if not self._is_token_valid():
            self.authenticate()
        self._session.headers["Authorization"] = f"Bearer {self._access_token}"

    @property
    def user_id(self) -> str | None:
        return self._user_id

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
            timeout=REQUEST_TIMEOUT,
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
            timeout=REQUEST_TIMEOUT,
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
        number_of_players: int = 4,
    ) -> dict:
        """
        Stap 1 van boeking: maak een payment intent aan.

        Args:
            tenant_id: Playtomic club ID
            resource_id: Court/resource ID
            start_time: ISO datetime string "YYYY-MM-DDTHH:MM:SS"
            duration_minutes: Speelduur in minuten
            number_of_players: Aantal spelers (standaard 4 voor dubbelspel)
        """
        self._ensure_authenticated()
        payload = {
            "allowed_payment_method_types": [
                "OFFER", "CASH", "MERCHANT_WALLET", "DIRECT",
                "SWISH", "IDEAL", "BANCONTACT", "PAYTRAIL",
                "CREDIT_CARD", "QUICK_PAY",
            ],
            "user_id": self._user_id,
            "cart": {
                "requested_item": {
                    "cart_item_type": "CUSTOMER_MATCH",
                    "cart_item_voucher_id": None,
                    "cart_item_data": {
                        "supports_split_payment": True,
                        "number_of_players": number_of_players,
                        "tenant_id": tenant_id,
                        "resource_id": resource_id,
                        "start": start_time,
                        "duration": duration_minutes,
                        "match_registrations": [
                            {"user_id": self._user_id, "pay_now": True}
                        ],
                    },
                }
            },
        }
        logger.debug("Payment intent payload: %s", payload)
        resp = self._session.post(f"{API_BASE}/v1/payment_intents", json=payload, timeout=REQUEST_TIMEOUT)
        _log_error_body(resp, "Payment intent")
        resp.raise_for_status()
        intent = resp.json()
        logger.info("Payment intent aangemaakt: %s", intent.get("payment_intent_id") or intent.get("id"))
        return intent

    def set_payment_method(self, intent_id: str, intent_response: dict | None = None) -> dict:
        """
        Stap 2 van boeking: selecteer betaalmethode 'betaal ter plaatse'.

        Leest de beschikbare betaalmethoden uit de intent response en kiest
        de eerste die 'at the club' / 'cash' / 'merchant_wallet' heet.
        Valt terug op de eerste beschikbare methode als geen match gevonden.
        """
        self._ensure_authenticated()
        methods = (intent_response or {}).get("available_payment_methods") or []
        resp = self._session.patch(
            f"{API_BASE}/v1/payment_intents/{intent_id}",
            json={
                "selected_payment_method_id": _select_payment_method_id(methods),
                "selected_payment_method_data": None,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def confirm_booking(self, intent_id: str) -> dict:
        """Stap 3 van boeking: bevestig de boeking."""
        self._ensure_authenticated()
        resp = self._session.post(
            f"{API_BASE}/v1/payment_intents/{intent_id}/confirmation",
            timeout=REQUEST_TIMEOUT,
        )
        _log_error_body(resp, "Bevestiging")
        resp.raise_for_status()
        result = resp.json()
        logger.info("Boeking bevestigd via Playtomic: %s", intent_id)
        return result

    def get_upcoming_matches(self) -> list[dict]:
        """Haal bevestigde toekomstige matches (boekingen) van de ingelogde gebruiker op."""
        self._ensure_authenticated()
        if not self._user_id:
            logger.warning("Playtomic user_id niet beschikbaar — boekingen ophalen niet mogelijk")
            return []
        resp = self._session.get(
            f"{API_BASE}/v1/matches",
            params={
                "user_id": self._user_id,
                "status": "CONFIRMED",
                "start_min": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "size": 50,
                "sort": "start_date,asc",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()
        # Gepagineerd object {"content": [...]} of een kale lijst bij oudere endpoints.
        if isinstance(raw, dict):
            return raw.get("content") or raw.get("data") or raw.get("matches") or []
        return raw
