from datetime import datetime, timezone, timedelta

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now():
    """Get current datetime in IST timezone."""
    return datetime.now(IST)


def utc_to_ist(utc_dt):
    """Convert a UTC datetime to IST."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(IST)
