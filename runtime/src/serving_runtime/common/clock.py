"""UTC wall-clock values shared by runtime components."""

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_datetime() -> datetime:
    return datetime.now(timezone.utc)
