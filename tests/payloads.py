"""Sample XLCourier API payloads shared by the test modules.

Scrubbed from a real delivered parcel (one failed delivery, then a second
round). Keep them in one module rather than inline in each test — when the
payload shape turns out to differ, there is exactly one place to fix.
"""

from __future__ import annotations

import copy

ACTIVE_CODE = "XLC1000999999"
DELIVERED_CODE = "XLC1000123456"

NOT_FOUND = {
    "msg": "parse tracking response failed",
    "data": None,
    "code": "-1",
    "success": False,
}


def event(
    type_id: int,
    clock: str,
    description: str = "",
    *,
    title: str = "Received",
    reason: str | None = None,
    pods: list[str] | None = None,
) -> dict:
    """One scan of the carrier's own timeline."""
    return {
        "reason": reason,
        "tracking_event_status_id": type_id,
        "title": title,
        "updated_at_localized": clock,
        "description": description,
        "pods": pods or [],
    }


def reply(events: list[dict]) -> dict:
    """A found reply wrapped in the carrier's envelope."""
    return {
        "msg": "",
        "data": {
            "recipient_address": "1 Example Street, Exampletown, EX1 1AA, GB",
            "postcode": "EX11AA",
            "events": events,
        },
        "code": "0",
        "success": True,
    }


EVENTS = [
    event(
        100,
        "2026-10-02 04:28:05",
        "Your shipment information has been submitted.",
        title="Created",
    ),
    event(600, "2026-10-02 11:07:14", "Your parcel has arrived safely at MWS- BHM."),
    event(
        600, "2026-10-05 14:23:22", "Your parcel has arrived safely at National Hub."
    ),
    event(
        1000,
        "2026-10-07 08:46:47",
        "Your parcel is currently out for delivery.",
        title="Out for Delivery",
    ),
    event(
        1100,
        "2026-10-07 15:45:09",
        "Unfortunately, we were unable to deliver your parcel.",
        title="Delivery Failed",
        pods=["https://example.invalid/pod/failed.jpeg"],
    ),
    event(
        600, "2026-10-08 15:28:31", "Your parcel has arrived safely at National Hub."
    ),
    event(
        1000,
        "2026-10-12 08:00:42",
        "Your parcel is currently out for delivery.",
        title="Out for Delivery",
    ),
    event(
        1600,
        "2026-10-12 15:25:23",
        "Your parcel has been delivered successfully. Thank you!",
        title="Delivered",
        pods=[
            "https://example.invalid/pod/delivered-1.jpeg",
            "https://example.invalid/pod/delivered-2.jpeg",
        ],
    ),
]


def delivered_reply() -> dict:
    """The carrier's reply for the delivered parcel."""
    return reply(copy.deepcopy(EVENTS))


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """The delivered reply as the coordinator hands it on (number attached)."""
    return {**delivered_reply(), "trackingNumber": code}


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel out for delivery."""
    return {**reply(copy.deepcopy(EVENTS[:4])), "trackingNumber": code}
