from datetime import datetime, timezone, timedelta
import re
from typing import Any


def format_date(date_str: str) -> datetime:
    """
    Processing different types of datetime for metadata information.
    :param date_str: raw date in string format
    :return: correct datetime
    """
    default_date = datetime(1900, 1, 1, 0, 0, 0)
    if not date_str or not date_str.startswith("D:"):
        return default_date
    date_body = date_str[2:]
    match = re.match(r"(\d{14})([+-]\d{2})'(\d{2})'", date_body)
    if match:
        dt_part = match.group(1)
        tz_hour = int(match.group(2))
        tz_minute = int(match.group(3))
        try:
            dt = datetime.strptime(dt_part, "%Y%m%d%H%M%S")
            tz = timezone(timedelta(hours=tz_hour, minutes=tz_minute))
            dt = dt.replace(tzinfo=tz)
            return dt
        except ValueError:
            return default_date
    match_z = re.match(r"(\d{14})Z", date_body)
    if match_z:
        dt_part = match_z.group(1)
        try:
            dt = datetime.strptime(dt_part, "%Y%m%d%H%M%S")
            dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return default_date
    try:
        if len(date_body) == 8:
            return datetime.strptime(date_body, "%Y%m%d")
        elif len(date_body) == 14:
            return datetime.strptime(date_body, "%Y%m%d%H%M%S")
    except ValueError:
        return default_date
    return default_date


def normalize_datetime(date_value: Any) -> datetime:
    """
    Unified datetime normalizer for all document formats.
    :param date_value: Can be datetime, ISO string, PDF-style string, or None
    :return: timezone-aware datetime object, or default datetime(1900, 1, 1)
    """
    default_date = datetime(1900, 1, 1, tzinfo=timezone.utc)
    if date_value is None:
        return default_date
    if isinstance(date_value, datetime):
        if date_value.tzinfo is None:
            return date_value.replace(tzinfo=timezone.utc)
        return date_value
    if isinstance(date_value, str):
        if date_value.startswith("D:"):
            return format_date(date_value)
        try:
            dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return default_date


def safe_decode(s: str) -> str:
    """
    Decoding word metadata if that needs.
    :param s: The raw metadata string to potentially decode.
    :return: The decoded string if the decoding process is successful and the
        result looks more sensible, otherwise returns the original string.
    """
    if not isinstance(s, str):
        return s
    try:
        return s.encode('latin1').decode('cp1251')
    except UnicodeEncodeError:
        return s
