"""Offline IP -> city lookup (no external API, no per-request network call).

Reads a local MaxMind-format .mmdb via the `geoip2` reader — the free DB-IP
"City Lite" database (CC-BY, direct download, no signup).

The .mmdb is ~130 MB so it is NOT in git. `ensure_db_async()` (called from the
app's startup lifespan) downloads it once in the background if it's missing, so
a fresh server/container needs no manual step. Set GEOIP_DB_PATH to a persistent
volume to keep it across redeploys and skip the re-download.

Everything here is best-effort: private/loopback IPs, an unknown IP, or a DB
that hasn't downloaded yet all return None instead of raising, so the submit
flow never breaks on geolocation.
"""
import gzip
import ipaddress
import logging
import os
import shutil
import tempfile
import threading
import urllib.request
from datetime import date
from functools import lru_cache
from typing import Optional

import geoip2.database
import geoip2.errors

logger = logging.getLogger(__name__)

# Bundled DB path, overridable via env (e.g. a mounted volume in production).
_DEFAULT_DB = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "dbip-city-lite.mmdb")
)
_DB_PATH = os.getenv("GEOIP_DB_PATH", _DEFAULT_DB)

# DB-IP publishes a fresh file monthly; the newest may not exist yet on the 1st,
# so fall back through recent months.
_DOWNLOAD_URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
# db-ip.com 403s the default "Python-urllib/x.y" agent, so send a real one.
_USER_AGENT = "Mozilla/5.0 (compatible; hero-destini-api/1.0; +geoip-city-db)"
_download_lock = threading.Lock()


@lru_cache(maxsize=1)
def _reader() -> Optional[geoip2.database.Reader]:
    """Open the .mmdb once (cached). None if the file isn't present/readable."""
    if not os.path.isfile(_DB_PATH):
        return None
    try:
        return geoip2.database.Reader(_DB_PATH)
    except Exception:
        return None


def _recent_months(count: int = 3) -> list[str]:
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def download_db() -> bool:
    """Fetch the newest DB-IP City Lite file to _DB_PATH. Returns True if the DB
    is present afterwards. Safe to call concurrently and on every boot."""
    with _download_lock:
        if os.path.isfile(_DB_PATH):
            return True
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

        for month in _recent_months():
            url = _DOWNLOAD_URL.format(month=month)
            gz_path = out_tmp = None
            try:
                logger.info("GeoIP: downloading %s", url)
                # Stream to a temp file, gunzip, then atomically rename into
                # place — a crash mid-download can never leave a corrupt .mmdb.
                req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    with tempfile.NamedTemporaryFile(
                        delete=False, dir=os.path.dirname(_DB_PATH), suffix=".gz"
                    ) as tmp:
                        shutil.copyfileobj(resp, tmp)
                        gz_path = tmp.name

                out_tmp = gz_path[:-3] + ".part"
                with gzip.open(gz_path, "rb") as fin, open(out_tmp, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                os.replace(out_tmp, _DB_PATH)
                out_tmp = None

                _reader.cache_clear()  # a previous miss cached None — re-open now
                logger.info(
                    "GeoIP: ready (%s, %.0f MB) — city lookup is live",
                    month, os.path.getsize(_DB_PATH) / 1e6,
                )
                return True
            except Exception as exc:
                logger.warning("GeoIP: %s unavailable (%s)", month, exc)
            finally:
                for p in (gz_path, out_tmp):
                    if p and os.path.exists(p):
                        try:
                            os.unlink(p)
                        except OSError:
                            pass

        logger.error("GeoIP: download failed — city will stay NULL until it succeeds")
        return False


def ensure_db_async() -> None:
    """Kick off a one-time background download if the DB is missing. Never
    blocks startup: the API serves immediately and city resolution switches on
    as soon as the file lands."""
    if os.path.isfile(_DB_PATH):
        logger.info("GeoIP: DB present at %s", _DB_PATH)
        return
    logger.info("GeoIP: DB missing — fetching in the background")
    threading.Thread(target=download_db, name="geoip-download", daemon=True).start()


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
