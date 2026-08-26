"""Own symmetric scalar representations shared by JSON input and output."""

import base64
import binascii
import re
from datetime import timedelta

_DURATION = re.compile(
    r"^(?P<sign>-)?P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)(?:\.(?P<fraction>\d{1,6}))?S)?)?$"
)


def encode_bytes(value: bytes) -> str:
    """Return canonical padded RFC 4648 base64 text."""

    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: str) -> bytes:
    """Decode canonical base64 text, rejecting invalid alphabet and padding."""

    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("invalid base64") from error


def format_timedelta(value: timedelta) -> str:
    """Return an exact ISO 8601 duration string at microsecond resolution."""

    total_microseconds = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    sign = "-" if total_microseconds < 0 else ""
    remaining = abs(total_microseconds)
    days, remaining = divmod(remaining, 86_400_000_000)
    hours, remaining = divmod(remaining, 3_600_000_000)
    minutes, remaining = divmod(remaining, 60_000_000)
    seconds, microseconds = divmod(remaining, 1_000_000)
    date_part = f"{days}D" if days else ""
    time_parts = []
    if hours:
        time_parts.append(f"{hours}H")
    if minutes:
        time_parts.append(f"{minutes}M")
    if seconds or microseconds or (not date_part and not time_parts):
        fraction = f".{microseconds:06d}".rstrip("0") if microseconds else ""
        time_parts.append(f"{seconds}{fraction}S")
    time_part = f"T{''.join(time_parts)}" if time_parts else ""
    return f"{sign}P{date_part}{time_part}"


def parse_timedelta(value: str) -> timedelta:
    """Parse Talea's exact ISO 8601 duration subset."""

    matched = _DURATION.fullmatch(value)
    if matched is None or not any(matched.group(name) for name in ("days", "hours", "minutes", "seconds")):
        raise ValueError("invalid ISO 8601 duration")
    microseconds = int((matched.group("fraction") or "").ljust(6, "0"))
    total_microseconds = (
        int(matched.group("days") or 0) * 86_400
        + int(matched.group("hours") or 0) * 3_600
        + int(matched.group("minutes") or 0) * 60
        + int(matched.group("seconds") or 0)
    ) * 1_000_000 + microseconds
    if matched.group("sign"):
        total_microseconds = -total_microseconds
    return timedelta(microseconds=total_microseconds)
