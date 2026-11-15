"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options — so the carrier mapping stays
apart from the coordinator and is testable without spinning up HA.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. The ``?template=`` parameter
# matters: without it the link opens a blank form, and the report comes back
# missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-xlcourier/issues/new"
    "?template=unrecognised_status.yml"
)

# Keyed on ``tracking_event_status_id`` as a string. ``200`` and ``900`` share
# the title "Scheduled", so the type is the only safe key.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "100": ParcelStatus.REGISTERED,
    "200": ParcelStatus.REGISTERED,
    "300": ParcelStatus.REGISTERED,
    # The parcel is still with the sender after a failed pickup.
    "400": ParcelStatus.REGISTERED,
    "500": ParcelStatus.IN_TRANSIT,
    "600": ParcelStatus.IN_TRANSIT,
    "700": ParcelStatus.IN_TRANSIT,
    "800": ParcelStatus.IN_TRANSIT,
    "900": ParcelStatus.IN_TRANSIT,
    "1000": ParcelStatus.OUT_FOR_DELIVERY,
    "1100": ParcelStatus.PROBLEM,
    "1200": ParcelStatus.RETURNING,
    "1300": ParcelStatus.RETURNING,
    # Still on its way back.
    "1400": ParcelStatus.RETURNING,
    # Back with the sender: never delivered.
    "1500": ParcelStatus.RETURNING,
    "1570": ParcelStatus.PROBLEM,
    "1600": ParcelStatus.DELIVERED,
}

_DELIVERED_TYPE = "1600"

# The reply carries naive wall-clock times.
_CLOCK_ZONE = ZoneInfo("Europe/London")
_CLOCK_FORMAT = "%Y-%m-%d %H:%M:%S"

_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_PHONE = re.compile(r"\+?\(?\d(?:[\s.\-()]{0,3}\d){8,}")

# Unseen things already warned about, so each is logged once per HA session.
_unmapped_statuses_logged: set[str] = set()
_bad_clocks_logged: set[str] = set()


def _warn_unmapped_status(code: str, title: str | None) -> None:
    """Log an unmapped event type once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised XLCourier status — help us map it. Open an issue "
        "and paste this line: %s\n  type=%s title=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
        title,
    )


def _warn_bad_clock() -> None:
    """Log an unparseable scan clock once."""
    if "clock" in _bad_clocks_logged:
        return
    _bad_clocks_logged.add("clock")
    _LOGGER.warning(
        "Unrecognised XLCourier scan time format — help us support it. Open "
        "an issue and paste this line: %s\n  updated_at_localized has an "
        "unexpected format",
        NEW_ISSUE_URL,
    )


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _scrub_reason(reason: str) -> str:
    """Remove phone numbers and e-mail addresses from a free-text reason."""
    reason = _EMAIL.sub("", reason)
    reason = _PHONE.sub("", reason)
    return re.sub(r"\s+", " ", reason).strip()


def _raw_status(event: dict) -> str:
    """The scan's own wording, plus its scrubbed reason when it has one."""
    description = event.get("description")
    text = description.strip() if isinstance(description, str) else ""
    if not text:
        title = event.get("title")
        text = title.strip() if isinstance(title, str) else ""
    reason = event.get("reason")
    if isinstance(reason, str) and (cleaned := _scrub_reason(reason)):
        text = f"{text} Reason: {cleaned}".strip()
    return text


def build_rows(events: list | None) -> list[dict]:
    """Return the scans oldest → newest, repeated scans collapsed.

    The source reports the same scan more than once, so rows identical in
    clock, type and description are dropped. The sort is stable, so scans on
    one clock keep the reply's order. A scan whose clock cannot be read is
    dropped with a one-shot warning.
    """
    seen: set[tuple[Any, Any, Any]] = set()
    rows: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        key = (
            event.get("updated_at_localized"),
            event.get("tracking_event_status_id"),
            event.get("description"),
        )
        if key in seen:
            continue
        seen.add(key)
        try:
            clock = datetime.strptime(
                str(event.get("updated_at_localized")), _CLOCK_FORMAT
            ).replace(tzinfo=_CLOCK_ZONE)
        except ValueError:
            _warn_bad_clock()
            continue
        type_id = str(event.get("tracking_event_status_id"))
        status = _STATUS_MAP.get(type_id)
        if status is None:
            _warn_unmapped_status(type_id, event.get("title"))
        rows.append(
            {
                "type": type_id,
                "clock": clock,
                "mapped": status,
                "raw_status": _raw_status(event),
            }
        )
    rows.sort(key=lambda row: row["clock"])
    return rows


def build_history(
    rows: list[dict], *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from :func:`build_rows` output.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. Capped to the most recent ``max_events``.
    """
    return [
        {
            "timestamp": row["clock"].isoformat(),
            "status": row["mapped"] or ParcelStatus.UNKNOWN,
            "raw_status": row["raw_status"],
        }
        for row in rows[-max_events:]
    ]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The status is the newest scan's type; when that type is unmapped, the
    newest scan that does map. A parcel without scans is ``unknown``.
    ``barcode`` is the code the user entered (the reply does not echo it).
    """
    tracking_code = raw.get("trackingNumber")
    data = raw.get("data")
    rows = build_rows(data.get("events") if isinstance(data, dict) else None)

    status = next(
        (row["mapped"] for row in reversed(rows) if row["mapped"] is not None),
        ParcelStatus.UNKNOWN,
    )
    delivered = status is ParcelStatus.DELIVERED
    delivered_at = None
    if delivered:
        delivered_row = next(
            (row for row in reversed(rows) if row["type"] == _DELIVERED_TYPE), None
        )
        delivered_at = delivered_row["clock"].isoformat() if delivered_row else None

    return {
        "carrier": "XLCourier",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": rows[-1]["raw_status"] if rows else None,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None,
        "planned_to": None,
        "pickup": None,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(rows) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
