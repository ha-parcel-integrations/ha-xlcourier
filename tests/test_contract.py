"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.xlcourier.const import ParcelStatus
from custom_components.xlcourier.parcels import (
    _NO_WINDOW_STATUSES,
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import EVENTS, event, reply

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

ZONE = datetime.fromisoformat("2099-01-01T00:00:00+00:00").tzinfo


def _sample(*events: dict) -> dict:
    return {**reply(list(events)), "trackingNumber": "XLC1000000001"}


SAMPLES = {
    "delivered": lambda: _sample(*EVENTS),
    "in_transit": lambda: _sample(event(600, "2026-10-02 10:00:00", "At hub")),
    "out_for_delivery": lambda: _sample(event(1000, "2026-10-02 10:00:00", "Out")),
    "returning": lambda: _sample(event(1500, "2026-10-02 10:00:00", "Returned")),
    "problem": lambda: _sample(event(1100, "2026-10-02 10:00:00", "Failed")),
    "bare": lambda: {"trackingNumber": "XLC1000000002"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_a_delivered_sample_has_its_delivery_time():
    parcel = normalize_parcel(SAMPLES["delivered"]())
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None


def test_planned_is_empty_and_anchored(parcel):
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_stays_empty(parcel):
    assert isinstance(parcel["pickup"], bool)
    assert parcel["pickup_point"] is None


@pytest.mark.parametrize("type_id", [200, 300, 400, 500])
def test_collection_from_the_sender_is_not_a_pickup_point(type_id):
    parcel = normalize_parcel(_sample(event(type_id, "2026-10-02 10:00:00", "Pickup")))
    assert parcel["pickup"] is False


def test_the_window_is_emptied_for_every_finished_status():
    for status in _NO_WINDOW_STATUSES:
        assert planned_for_status(status, "2099-01-01", "2099-01-02") == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, "a", "b") == ("a", "b")


def test_pickup_is_sticky_after_collection():
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert not is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.IN_TRANSIT])


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-01-01T13:00:00+00:00", None, zone=ZONE)
    end = datetime.fromisoformat(end)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(start).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-01-01", None, zone=ZONE)
    start = datetime.fromisoformat(start)
    end = datetime.fromisoformat(end)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
