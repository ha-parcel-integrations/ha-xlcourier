"""Tests for XLCourier diagnostics."""

from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.xlcourier.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "XLC1000123456"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "XLC1000123456",
            "sender": "Example Shop",
            "receiver": "Jane Doe",
            "status": "out_for_delivery",
            "raw": {
                "trackingNumber": "XLC1000123456",
                "recipient": "Jane Doe",
                "deliveryAddress": {"city": "Rotterdam", "street": "Coolsingel 1"},
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["recipient"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["deliveryAddress"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }


async def test_diagnostics_redacts_address_and_reasons(hass):
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "XLC1000123456",
            "raw": {
                "data": {
                    "recipient_address": "1 Example Street, Exampletown",
                    "postcode": "EX11AA",
                    "events": [
                        {
                            "reason": "call 07700 900 123",
                            "title": "Failed",
                            "pods": ["https://example.invalid/pod.jpeg"],
                        }
                    ],
                }
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    data = result["incoming"][0]["raw"]["data"]
    assert data["recipient_address"] == "**REDACTED**"
    assert data["postcode"] == "**REDACTED**"
    assert data["events"][0]["pods"] == "**REDACTED**"
    assert data["events"][0]["reason"] == "**REDACTED**"
    assert data["events"][0]["title"] == "Failed"
