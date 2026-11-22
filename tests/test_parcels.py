"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xlcourier import parcels
from custom_components.xlcourier.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.xlcourier.parcels import (
    _STATUS_MAP,
    apply_delivered_filter,
    build_history,
    build_rows,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    tracking_url,
)

from .payloads import (
    DELIVERED_CODE,
    EVENTS,
    active_sample,
    delivered_sample,
    event,
    reply,
)

EXPECTED_MAP = {
    100: ParcelStatus.REGISTERED,
    200: ParcelStatus.REGISTERED,
    300: ParcelStatus.REGISTERED,
    400: ParcelStatus.REGISTERED,
    500: ParcelStatus.IN_TRANSIT,
    600: ParcelStatus.IN_TRANSIT,
    700: ParcelStatus.IN_TRANSIT,
    800: ParcelStatus.IN_TRANSIT,
    900: ParcelStatus.IN_TRANSIT,
    1000: ParcelStatus.OUT_FOR_DELIVERY,
    1100: ParcelStatus.PROBLEM,
    1200: ParcelStatus.RETURNING,
    1300: ParcelStatus.RETURNING,
    1400: ParcelStatus.RETURNING,
    1500: ParcelStatus.RETURNING,
    1570: ParcelStatus.PROBLEM,
    1600: ParcelStatus.DELIVERED,
}


def _status_of(type_id: int) -> ParcelStatus:
    raw = reply([event(type_id, "2026-10-02 11:07:14", "x")])
    return normalize_parcel({**raw, "trackingNumber": DELIVERED_CODE})["status"]


@pytest.mark.parametrize(("type_id", "expected"), EXPECTED_MAP.items())
def test_every_published_type_maps(type_id, expected):
    assert _status_of(type_id) == expected


def test_map_covers_exactly_the_published_types():
    assert set(_STATUS_MAP) == {str(key) for key in EXPECTED_MAP}


def test_returned_is_returning_never_delivered():
    parcel = normalize_parcel(
        {
            **reply([event(1500, "2026-10-02 11:07:14", "Returned")]),
            "trackingNumber": "X",
        }
    )
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_scheduled_pickup_and_delivery_are_mapped_by_type_not_title():
    pickup = event(200, "2026-10-02 10:00:00", "Pickup booked", title="Scheduled")
    delivery = event(900, "2026-10-02 10:00:00", "Delivery booked", title="Scheduled")
    assert build_rows([pickup])[0]["mapped"] == ParcelStatus.REGISTERED
    assert build_rows([delivery])[0]["mapped"] == ParcelStatus.IN_TRANSIT


def test_unmapped_newest_type_falls_back_to_newest_mappable(caplog):
    raw = reply(
        [
            event(600, "2026-10-02 10:00:00", "At hub"),
            event(9999, "2026-10-03 10:00:00", "Mystery", title="Novel"),
        ]
    )
    parcel = normalize_parcel({**raw, "trackingNumber": "X"}, include_history=True)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "Mystery"
    assert parcel["history"][-1]["status"] == ParcelStatus.UNKNOWN
    normalize_parcel({**raw, "trackingNumber": "X"})
    assert caplog.text.count("type=9999") == 1
    assert "title=Novel" in caplog.text
    assert "Mystery" not in caplog.text
    assert "issues/new" in caplog.text


def test_only_unmapped_types_give_unknown_with_one_warning(caplog):
    raw = reply([event(9999, "2026-10-03 10:00:00", "Mystery")])
    for _ in range(2):
        parcel = normalize_parcel({**raw, "trackingNumber": "X"})
        assert parcel["status"] == ParcelStatus.UNKNOWN
    assert caplog.text.count("type=9999") == 1


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == UTC
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


# ---------------------------------------------------------------------------
# build_rows / build_history
# ---------------------------------------------------------------------------


def test_history_is_in_order_with_london_offsets():
    history = build_history(build_rows(delivered_sample()["data"]["events"]))
    assert len(history) == 8
    assert [entry["status"] for entry in history[3:6]] == [
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.PROBLEM,
        ParcelStatus.IN_TRANSIT,
    ]
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    # BST in early October: the naive clock is localised, never read as UTC.
    assert history[0]["timestamp"] == "2026-10-02T04:28:05+01:00"
    assert history[0]["raw_status"] == "Your shipment information has been submitted."


def test_winter_clock_has_no_offset():
    rows = build_rows([event(600, "2026-12-02 11:07:14", "x")])
    assert rows[0]["clock"].isoformat() == "2026-12-02T11:07:14+00:00"


def test_duplicate_scans_are_collapsed():
    scan = event(600, "2026-10-02 11:07:14", "At hub")
    rows = build_rows([scan, dict(scan), event(600, "2026-10-02 11:07:14", "Else")])
    assert [row["raw_status"] for row in rows] == ["At hub", "Else"]


def test_same_clock_scans_keep_reply_order():
    rows = build_rows(
        [
            event(600, "2026-10-02 11:07:14", "first"),
            event(1000, "2026-10-02 11:07:14", "second"),
            event(100, "2026-10-01 11:07:14", "earlier"),
        ]
    )
    assert [row["raw_status"] for row in rows] == ["earlier", "first", "second"]


def test_unsorted_reply_is_sorted_by_clock():
    rows = build_rows(
        [
            event(1600, "2026-10-12 15:25:23", "late"),
            event(100, "2026-10-02 04:28:05", "early"),
        ]
    )
    assert [row["raw_status"] for row in rows] == ["early", "late"]


def test_history_keeps_the_last_twenty():
    events = [
        event(600, f"2026-10-{day:02d} 10:00:00", f"scan {day}") for day in range(1, 26)
    ]
    history = build_history(build_rows(events))
    assert len(history) == 20
    assert history[-1]["raw_status"] == "scan 25"


def test_malformed_events_are_skipped():
    assert build_rows(None) == []
    assert build_rows(["junk", {"tracking_event_status_id": 600}]) == []


def test_unparseable_clock_drops_the_scan_and_warns_once(caplog):
    bad = event(600, "02/10/2026 11:07", "x")
    assert build_rows([bad, bad, dict(bad, description="y")]) == []
    assert caplog.text.count("scan time format") == 1
    assert "02/10/2026" not in caplog.text


def test_raw_status_falls_back_to_title_then_empty():
    assert (
        build_rows([event(600, "2026-10-02 11:07:14", "  ")])[0]["raw_status"]
        == "Received"
    )
    blank = {
        "tracking_event_status_id": 600,
        "updated_at_localized": "2026-10-02 11:07:14",
    }
    assert build_rows([blank])[0]["raw_status"] == ""


def test_reason_is_appended_with_phone_numbers_and_emails_removed():
    scan = event(
        1100,
        "2026-10-07 15:45:09",
        "Unable to deliver.",
        reason="Call 07700 900 123 or mail jane.doe@example.com, no access",
    )
    raw_status = build_rows([scan])[0]["raw_status"]
    assert raw_status == "Unable to deliver. Reason: Call or mail no access"
    assert "0770" not in raw_status
    assert "@" not in raw_status


@pytest.mark.parametrize(
    "reason", ["call (0121) 496 0000", "ring +44 (0)7700 900123", "tel 07700.900.123"]
)
def test_reason_drops_bracketed_and_dotted_phone_numbers(reason):
    scan = event(1100, "2026-10-07 15:45:09", "Unable.", reason=reason)
    raw_status = build_rows([scan])[0]["raw_status"]
    assert not any(digit in raw_status for digit in "0123456789")


def test_reason_that_is_only_contact_details_adds_nothing():
    scan = event(1100, "2026-10-07 15:45:09", "Unable.", reason="+44 7700-900-123")
    assert build_rows([scan])[0]["raw_status"] == "Unable."


def test_blank_or_non_string_reason_adds_nothing():
    for reason in ("", "   ", 5):
        scan = event(1100, "2026-10-07 15:45:09", "Unable.", reason=reason)
        assert build_rows([scan])[0]["raw_status"] == "Unable."


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    """The docs site would have to pick one; "awaiting data" must not hide a confirmed field."""
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample."""
    delivered = normalize_parcel(delivered_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    assert {"url", "history"} == CAPABILITIES
    assert delivered["url"] is not None
    assert with_history["history"] is not None
    for field in ("weight", "dimensions", "planned_from", "planned_to"):
        assert delivered[field] is None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "XLCourier"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert (
        parcel["raw_status"]
        == "Your parcel has been delivered successfully. Thank you!"
    )
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-10-12T15:25:23+01:00"
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert (
        parcel["url"] == f"https://www.xlcourier.com/trackOrder?number={DELIVERED_CODE}"
    )
    assert parcel["history"] is None  # opt-in, default off


def test_delivered_after_a_failed_attempt_ends_delivered():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert [entry["status"] for entry in parcel["history"][4:]] == [
        ParcelStatus.PROBLEM,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.DELIVERED,
    ]


def test_failed_attempt_is_a_problem_until_the_next_scan():
    raw = {**reply(EVENTS[:5]), "trackingNumber": DELIVERED_CODE}
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.PROBLEM
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert (
        parcel["raw_status"] == "Unfortunately, we were unable to deliver your parcel."
    )


def test_normalize_active_parcel():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["planned_from"] is None


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"trackingNumber": "XLC1000000001"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["history"] is None
    assert (
        normalize_parcel({"trackingNumber": "X"}, include_history=True)["history"] == []
    )


def test_normalize_keeps_the_full_reply_in_raw():
    raw = delivered_sample()
    assert normalize_parcel(raw)["raw"] is raw
    assert "recipient_address" in normalize_parcel(raw)["raw"]["data"]


def test_the_address_never_reaches_a_published_field():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    published = {key: value for key, value in parcel.items() if key != "raw"}
    assert "Example Street" not in repr(published)


def test_tracking_url_needs_a_code():
    assert tracking_url(None) is None
    assert tracking_url("") is None


def test_non_dict_data_gives_unknown():
    parcel = normalize_parcel({"trackingNumber": "X", "data": "odd"})
    assert parcel["status"] == ParcelStatus.UNKNOWN


def test_delivered_without_a_delivered_row_has_no_delivered_at():
    """Only reachable by a future type mapped to delivered; stay safe."""
    parcels._STATUS_MAP["1601"] = ParcelStatus.DELIVERED
    try:
        raw = reply([event(1601, "2026-10-12 15:25:23", "x")])
        parcel = normalize_parcel({**raw, "trackingNumber": "X"})
    finally:
        del parcels._STATUS_MAP["1601"]
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is None


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
