"""Admin-editable runtime app settings with an in-process TTL cache.

Source of truth: the app_settings table (single JSON row, id=1).
Hot path: reads come from an in-memory cache — NO DB hit per request. The cache
refreshes from the DB at most once every CACHE_TTL seconds, and is refreshed
immediately on the worker that performs an admin update. Other workers converge
within CACHE_TTL. If the DB is briefly unreachable, the last cached value (or the
defaults) is served, so reads never fail.
"""

import time
from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import settings as env_settings
from app.models.app_settings import AppSettings

CACHE_TTL = 15  # seconds — how long a worker serves config from memory before refreshing

# Default seed values. `unlimited_numbers` seeds from the campaign whitelist that
# previously lived in code; admins can edit the live list from the panel afterward.
DEFAULT_UNLIMITED_NUMBERS = [
    "7507069000", "8619763089", "9820099301", "9411795829", "9118720778",
    "8252261004", "7982592365", "8650856237", "8285022022", "8851260538",
    "9711129700", "9810009341", "8447663057", "9560370095", "9873246272",
    "7408386126", "9628884838", "9711091516", "9699435355", "7522860181",
    "9717785892", "9819499198", "9958255825", "8668350184", "9920306007",
    "7738570197", "9819752704", "9818277036",
]
DEFAULT_HELD_NUMBERS: list[str] = []


def _defaults() -> dict:
    return {
        "max_videos_per_user": env_settings.MAX_VIDEOS_PER_USER,
        "unlimited_numbers": list(DEFAULT_UNLIMITED_NUMBERS),
        "held_numbers": list(DEFAULT_HELD_NUMBERS),
    }


# Per-process cache: {"data": dict|None, "ts": monotonic-seconds}
_cache: dict = {"data": None, "ts": 0.0}


def _load_from_db(db: Session) -> Optional[dict]:
    row = db.query(AppSettings).filter(AppSettings.id == 1).first()
    if not row:
        return None
    merged = _defaults()          # ensures newly-added keys always have a value
    merged.update(row.data or {})
    return merged


def ensure_default_settings(db: Session) -> dict:
    """Seed the single settings row if it doesn't exist yet."""
    row = db.query(AppSettings).filter(AppSettings.id == 1).first()
    if not row:
        row = AppSettings(id=1, data=_defaults(), updated_by="system")
        db.add(row)
        db.commit()
        print("🌱 Seeded default app_settings")
    return _load_from_db(db) or _defaults()


def get_settings(force: bool = False) -> dict:
    """Return the current settings from the in-process cache (refreshing if stale)."""
    now = time.monotonic()
    if not force and _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    try:
        db = SessionLocal()
        try:
            data = _load_from_db(db) or ensure_default_settings(db)
        finally:
            db.close()
        _cache["data"] = data
        _cache["ts"] = now
        return data
    except Exception as e:
        print(f"⚠️ app_settings refresh failed, serving cache/defaults: {e}")
        return _cache["data"] if _cache["data"] is not None else _defaults()


def update_settings(patch: dict, admin: str) -> dict:
    """Apply a partial update, persist to DB, and refresh this worker's cache."""
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.id == 1).first()
        if not row:
            row = AppSettings(id=1, data=_defaults(), updated_by=admin)
            db.add(row)
            db.flush()
        data = dict(row.data or {})
        data.update(patch)
        row.data = data              # reassign so SQLAlchemy detects the JSON change
        row.updated_by = admin
        db.commit()
        fresh = _load_from_db(db)
    finally:
        db.close()
    _cache["data"] = fresh
    _cache["ts"] = time.monotonic()
    return fresh


# ── Typed accessors used on the hot path ─────────────────────────────────
def get_max_videos_per_user() -> int:
    try:
        return int(get_settings().get("max_videos_per_user", env_settings.MAX_VIDEOS_PER_USER))
    except (TypeError, ValueError):
        return env_settings.MAX_VIDEOS_PER_USER


def get_unlimited_numbers() -> set:
    return {str(n).strip() for n in get_settings().get("unlimited_numbers", []) if str(n).strip()}


def get_held_numbers() -> set:
    return {str(n).strip() for n in get_settings().get("held_numbers", []) if str(n).strip()}
