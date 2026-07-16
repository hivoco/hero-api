"""Offline IP -> city lookup (no external API, no per-request network call).

Reads a local MaxMind-format .mmdb via the `geoip2` reader. We ship the free
DB-IP "City Lite" database (CC-BY, direct download — no signup); see
scripts/download_geoip.sh. Everything here is best-effort: private/loopback IPs,
an unknown IP, or a missing DB file all return None instead of raising, so the
submit flow never breaks on geolocation.
"""
import ipaddress
import os
from functools import lru_cache
from typing import Optional

import geoip2.database
import geoip2.errors

# Bundled DB path, overridable via env (e.g. a mounted volume in production).
_DEFAULT_DB = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "dbip-city-lite.mmdb")
)
_DB_PATH = os.getenv("GEOIP_DB_PATH", _DEFAULT_DB)


@lru_cache(maxsize=1)
def _reader() -> Optional[geoip2.database.Reader]:
    """Open the .mmdb once (cached). None if the file isn't present/readable."""
    if not os.path.isfile(_DB_PATH):
        return None
    try:
        return geoip2.database.Reader(_DB_PATH)
    except Exception:
        return None


def city_from_ip(ip: Optional[str]) -> Optional[str]:
    """Best-effort city name for a public IP, else None. Never raises."""
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    # Addresses that can't geolocate (localhost, LAN, CGNAT, etc.).
    if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
        return None
    reader = _reader()
    if reader is None:
        return None
    try:
        name = reader.city(ip).city.name
    except (geoip2.errors.AddressNotFoundError, ValueError):
        return None
    except Exception:
        return None
    if not name:
        return None
    # DB-IP annotates some cities with a district in parentheses
    # ("Navi Mumbai (Ghansoli)") — keep just the city.
    return name.split(" (")[0].strip() or None
