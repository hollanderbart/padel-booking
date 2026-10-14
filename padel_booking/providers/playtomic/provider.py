"""
Playtomic provider entry point.
Leest een BookingRequest JSON van stdin, boekt een baan via de Playtomic API,
schrijft ProviderResult naar stdout.

Gebruik:
  echo '<json>' | python -m providers.playtomic.provider
"""

from providers.base import run_provider_cli
from providers.playtomic.booking import PlaytomicBooker

if __name__ == "__main__":
    run_provider_cli("playtomic", PlaytomicBooker)
