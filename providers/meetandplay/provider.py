"""
Meet & Play provider entry point.
Leest een BookingRequest JSON van stdin, boekt een baan, schrijft ProviderResult naar stdout.

Gebruik:
  echo '<json>' | python -m providers.meetandplay.provider
"""

from providers.base import run_provider_cli
from providers.meetandplay.booking import MeetAndPlayBooker

if __name__ == "__main__":
    run_provider_cli("meetandplay", MeetAndPlayBooker)
