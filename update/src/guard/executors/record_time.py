"""Timestamp parsing for bounded record fields."""

from datetime import datetime


def parse_record_time(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError
    return stamp


def is_record_time(value):
    try:
        parse_record_time(value)
    except ValueError:
        return False
    return True
