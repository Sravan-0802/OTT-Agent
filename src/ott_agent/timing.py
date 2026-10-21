"""Server time is trustworthy. Phone clocks and offline buffers are not."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
LIVE_BUFFER_S = 600


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ist_day(dt: datetime):
    return dt.astimezone(IST).date()


def in_quiet_hours(dt: datetime) -> bool:
    hour = dt.astimezone(IST).hour
    return hour >= 22 or hour < 8


def timing(event: dict) -> dict:
    """Recover when an event happened.

    `received_at` is the server clock. `occurred_at` and `sent_at` share the
    phone clock, so a phone that is hours off does not by itself make the event
    old. The gap between occurred and sent is how long the phone held the event.
    A hold longer than 10 minutes is an offline buffer, not a live visit.
    """
    occurred = parse_ts(event.get("occurred_at"))
    sent = parse_ts(event.get("sent_at"))
    received = parse_ts(event.get("received_at"))
    if occurred is None or sent is None or received is None or sent < occurred:
        return {
            "trusted": False,
            "kind": "untrusted_clock",
            "true_time": None,
            "buffer_s": None,
            "received_at": received,
        }
    buffer_s = int((sent - occurred).total_seconds())
    true_time = received - timedelta(seconds=buffer_s)
    kind = "live" if buffer_s <= LIVE_BUFFER_S else "buffered"
    return {
        "trusted": True,
        "kind": kind,
        "true_time": true_time,
        "buffer_s": buffer_s,
        "received_at": received,
    }


def event_age_s(event: dict) -> int:
    info = timing(event)
    if not info["trusted"] or info["buffer_s"] is None:
        return 0
    return max(0, int(info["buffer_s"]))
