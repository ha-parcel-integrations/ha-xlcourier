"""XLCourier public tracking API client."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

from .const import MAX_BODY_BYTES, TRACKING_API_URL
from .parcels import NEW_ISSUE_URL

_LOGGER = logging.getLogger(__name__)

NOT_FOUND_MESSAGE = "parse tracking response failed"

_BOT_CHECK = re.compile(
    r"_cf_chl_opt|cf-turnstile|<title>\s*(Just a moment|Attention Required)"
    r"|challenge-platform|captcha",
    re.IGNORECASE,
)

_KNOWN_TOP_KEYS = frozenset({"msg", "data", "code", "success"})
_KNOWN_DATA_KEYS = frozenset({"recipient_address", "postcode", "events"})
_KNOWN_EVENT_KEYS = frozenset(
    {
        "reason",
        "tracking_event_status_id",
        "title",
        "description",
        "updated_at_localized",
        "pods",
    }
)

# Shapes already warned about, so each is logged once per HA session.
_shapes_logged: set[str] = set()


def _warn_once(key: str, detail: str, *args: object) -> None:
    """Log one WARNING per unseen shape, with the copy-paste issue link."""
    if key in _shapes_logged:
        return
    _shapes_logged.add(key)
    _LOGGER.warning(
        "Unrecognised XLCourier reply — help us support it. Open an issue and "
        "paste this line: %s\n  %s",
        NEW_ISSUE_URL,
        detail % args,
    )


class XLCourierApiError(Exception):
    """Raised when an XLCourier API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"XLCourier API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


def _check_shape(payload: dict[str, Any]) -> None:
    """Warn once about keys this build has never seen in a found reply."""
    if extra := set(payload) - _KNOWN_TOP_KEYS:
        _warn_once(
            "top:" + ",".join(sorted(extra)), "new top-level keys=%s", sorted(extra)
        )
    data = payload.get("data")
    if isinstance(data, dict):
        if extra := set(data) - _KNOWN_DATA_KEYS:
            _warn_once(
                "data:" + ",".join(sorted(extra)), "new data keys=%s", sorted(extra)
            )
        for event in data.get("events") or []:
            if isinstance(event, dict) and (extra := set(event) - _KNOWN_EVENT_KEYS):
                _warn_once(
                    "event:" + ",".join(sorted(extra)),
                    "new event keys=%s",
                    sorted(extra),
                )


class XLCourierApiClient:
    """Client for the public XLCourier tracking endpoint.

    No authentication: the endpoint is keyed on the tracking code alone and
    answers HTTP 200 for unknown codes too, so the JSON body decides.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking reply.

        Returns the reply for a known parcel, or ``None`` when the carrier
        says the code is unknown. An inconclusive reply, a bot check or a
        non-200 status raises :class:`XLCourierApiError`, so the coordinator
        keeps the last good data; network errors propagate as
        ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        async with self._session.get(
            url, headers={"Accept": "application/json"}, allow_redirects=False
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds
                raise XLCourierApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status not in (200, 403):
                raise XLCourierApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            body = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    break
            status = response.status

        if len(body) > MAX_BODY_BYTES:
            raise XLCourierApiError("reply too large", status_code=status)
        text = bytes(body).decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except ValueError as err:
            if _BOT_CHECK.search(text):
                _warn_once("bot-check", "the tracking route answered a bot check")
                raise XLCourierApiError("bot check", status_code=status) from err
            if status != 200:
                raise XLCourierApiError(f"HTTP {status}", status_code=status) from err
            _warn_once("body:not-json", "the reply body is not JSON")
            raise XLCourierApiError(f"unparseable body ({err})") from err
        if status != 200:
            raise XLCourierApiError(f"HTTP {status}", status_code=status)

        if not isinstance(payload, dict):
            _warn_once(
                "body:" + type(payload).__name__,
                "the reply body is JSON %s, not an object",
                type(payload).__name__,
            )
            raise XLCourierApiError("unexpected body (not a JSON object)")

        code = str(payload.get("code"))
        data = payload.get("data")
        if code != "0":
            message = str(payload.get("msg") or "").strip().lower()
            if data is None and message == NOT_FOUND_MESSAGE:
                return None
            _warn_once("code:" + code, "unexpected reply code=%s", code)
            raise XLCourierApiError(f"unexpected reply code {code}")

        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list) or not events:
            _warn_once("empty-events", "code 0 reply without events")
            raise XLCourierApiError("found reply without events")

        _check_shape(payload)
        return payload
