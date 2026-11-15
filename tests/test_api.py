"""Tests for the XLCourier API client."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.xlcourier.api import XLCourierApiClient, XLCourierApiError

from .payloads import NOT_FOUND, delivered_reply, event, reply

CODE = "XLC1000123456"


def _session_returning(
    status: int, body: object = None, headers: dict | None = None
) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    raw = body if isinstance(body, (str, bytes)) else json.dumps(body)
    data = raw if isinstance(raw, bytes) else raw.encode()

    async def _chunks(_size):
        # Small chunks, so a reader that stops after the first one fails.
        for start in range(0, len(data), 7):
            yield data[start : start + 7]

    response.content.iter_chunked = MagicMock(side_effect=_chunks)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_reply_on_success():
    session = _session_returning(200, delivered_reply())
    parcel = await XLCourierApiClient(session).async_get_parcel(CODE)

    assert parcel["code"] == "0"
    assert session.get.call_args[0][0].endswith(f"/getLogisticsTrack/{CODE}")
    kwargs = session.get.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Accept"] == "application/json"


async def test_get_parcel_returns_none_for_the_not_found_object():
    client = XLCourierApiClient(_session_returning(200, NOT_FOUND))
    assert await client.async_get_parcel(CODE) is None


async def test_not_found_message_is_case_insensitive():
    body = {**NOT_FOUND, "msg": "Parse Tracking Response Failed"}
    client = XLCourierApiClient(_session_returning(200, body))
    assert await client.async_get_parcel(CODE) is None


async def test_other_non_zero_code_is_inconclusive(caplog):
    body = {"msg": "busy", "data": None, "code": "-2", "success": False}
    client = XLCourierApiClient(_session_returning(200, body))
    with pytest.raises(XLCourierApiError):
        await client.async_get_parcel(CODE)
    assert "issues/new" in caplog.text
    assert "busy" not in caplog.text


async def test_non_zero_code_with_data_is_not_not_found():
    body = {**NOT_FOUND, "data": {"events": []}}
    client = XLCourierApiClient(_session_returning(200, body))
    with pytest.raises(XLCourierApiError):
        await client.async_get_parcel(CODE)


async def test_code_zero_without_events_is_inconclusive_and_warns_once(caplog):
    client = XLCourierApiClient(_session_returning(200, reply([])))
    for _ in range(2):
        with pytest.raises(XLCourierApiError):
            await client.async_get_parcel(CODE)
    assert caplog.text.count("without events") == 1


async def test_code_zero_with_missing_data_is_inconclusive():
    body = {"msg": "", "data": None, "code": "0", "success": True}
    client = XLCourierApiClient(_session_returning(200, body))
    with pytest.raises(XLCourierApiError):
        await client.async_get_parcel(CODE)


async def test_live_reply_shape_logs_no_warning(caplog):
    client = XLCourierApiClient(_session_returning(200, delivered_reply()))
    await client.async_get_parcel(CODE)
    assert "Unrecognised" not in caplog.text


async def test_unseen_keys_warn_once_by_name_only(caplog):
    body = delivered_reply()
    body["extra"] = "SECRETVALUE"
    body["data"]["surprise"] = 1
    body["data"]["events"][0]["novel"] = "SECRETVALUE"
    client = XLCourierApiClient(_session_returning(200, body))
    await client.async_get_parcel(CODE)
    await client.async_get_parcel(CODE)
    assert caplog.text.count("new top-level keys") == 1
    assert caplog.text.count("new data keys") == 1
    assert caplog.text.count("new event keys") == 1
    assert "SECRETVALUE" not in caplog.text
    assert "issues/new" in caplog.text


async def test_known_shape_does_not_warn(caplog):
    caplog.set_level(logging.WARNING)
    body = reply([event(600, "2026-10-02 11:07:14", "x"), "junk"])
    await XLCourierApiClient(_session_returning(200, body)).async_get_parcel(CODE)
    assert caplog.text == ""


@pytest.mark.parametrize("status", [301, 302, 404, 410, 500, 503])
async def test_unexpected_status_is_a_transport_error(status):
    client = XLCourierApiClient(_session_returning(status, ""))
    with pytest.raises(XLCourierApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == status


async def test_429_carries_retry_after():
    client = XLCourierApiClient(_session_returning(429, "", {"Retry-After": "90"}))
    with pytest.raises(XLCourierApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 90.0


@pytest.mark.parametrize("header", [None, "Wed, 21 Oct 2026 07:28:00 GMT"])
async def test_429_without_seconds_has_no_retry_after(header):
    headers = {"Retry-After": header} if header else {}
    client = XLCourierApiClient(_session_returning(429, "", headers))
    with pytest.raises(XLCourierApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


CLOUDFLARE = (
    "<html><head><title>Just a moment...</title></head>"
    "<body>challenge-platform</body></html>"
)


@pytest.mark.parametrize("status", [200, 403])
async def test_cloudflare_body_is_a_transport_error(status, caplog):
    client = XLCourierApiClient(_session_returning(status, CLOUDFLARE))
    with pytest.raises(XLCourierApiError) as err:
        await client.async_get_parcel(CODE)
    assert "bot check" in err.value.detail
    assert "bot check" in caplog.text


async def test_forbidden_json_is_a_transport_error():
    client = XLCourierApiClient(_session_returning(403, {"a": 1}))
    with pytest.raises(XLCourierApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 403


async def test_forbidden_plain_text_is_a_transport_error():
    client = XLCourierApiClient(_session_returning(403, "denied"))
    with pytest.raises(XLCourierApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 403


async def test_non_json_body_is_a_schema_error(caplog):
    client = XLCourierApiClient(_session_returning(200, "<html>hello</html>"))
    for _ in range(2):
        with pytest.raises(XLCourierApiError) as err:
            await client.async_get_parcel(CODE)
    assert "unparseable" in err.value.detail
    assert caplog.text.count("not JSON") == 1
    assert "hello" not in caplog.text
    assert "issues/new" in caplog.text


async def test_non_object_body_is_an_error(caplog):
    client = XLCourierApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(XLCourierApiError):
        await client.async_get_parcel(CODE)
    assert "JSON list, not an object" in caplog.text


async def test_oversized_body_is_an_error():
    client = XLCourierApiClient(_session_returning(200, b"x" * (256 * 1024 + 1)))
    with pytest.raises(XLCourierApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await XLCourierApiClient(session).async_get_parcel(CODE)
