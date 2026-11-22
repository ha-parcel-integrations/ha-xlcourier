# XLCourier Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-xlcourier.svg)](https://github.com/ha-parcel-integrations/ha-xlcourier/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-xlcourier/total.svg)](https://github.com/ha-parcel-integrations/ha-xlcourier/releases)
[![Marketplace](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg)](https://hacs.xyz)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [XLCourier](https://www.xlcourier.com) parcels in the **United Kingdom** — the carrier for extra-large and heavy items such as furniture, appliances and e-bikes. No account is needed — you enter the tracking code yourself, just like on the XLCourier website.

> **Pre-release.** Tracking has been confirmed on a real delivered parcel. Several scan types XLCourier publishes (pickups, returns, destruction) have not been seen yet, so treat it as experimental. Unfamiliar answers are logged as warnings with a link to report them.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of XLCourier parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own scan text and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar
- `xlcourier.track_parcel` / `xlcourier.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- An XLCourier parcel and its tracking code (`XLC` followed by 10 to 12
  digits, or `ZX` followed by 8 digits) from the shipping confirmation — no
  account needed

## Installation

### Marketplace (recommended)

Click the button below to open this repository in the Marketplace, then install **XLCourier**:

[![Open your Home Assistant instance and open a repository inside the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-xlcourier&category=integration)

Or add it by hand: in the Marketplace, choose the three-dot menu → **Custom repositories**, add `https://github.com/ha-parcel-integrations/ha-xlcourier` as an **Integration** and install **XLCourier**.

Restart Home Assistant if it asks you to.

### Manual

Copy `custom_components/xlcourier` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=xlcourier)

Add the integration via **Settings → Devices & Services → Add Integration → XLCourier**. There is nothing to fill in: the hub is created immediately (XLCourier tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`xlcourier.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every XLCourier
  hub out there polls at exactly the same second.

XLCourier reports no delivery window, so a parcel that is out for delivery is polled at the hot cadence for as long as it stays in that status.

## Removal

Standard HA removal applies: **Settings → Devices & Services → XLCourier → ⋮ → Delete**. Nothing is stored on XLCourier's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.xlcourier_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.xlcourier_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.xlcourier_next_delivery` | Earliest expected delivery moment across all active parcels (stays empty: XLCourier publishes no delivery window) |
| `sensor.xlcourier_en_route_to_pickup_point` | Incoming parcels on their way to a pickup point that have not arrived yet (reads 0 until XLCourier reports one) |
| `sensor.xlcourier_awaiting_pickup` | Incoming parcels that are ready to collect at a pickup point (reads 0 until XLCourier reports one) |
| `sensor.xlcourier_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `calendar.xlcourier_deliveries` | Read-only calendar of expected deliveries, no extra API calls (stays empty: no delivery window) |
| `sensor.xlcourier_last_successful_update` | Diagnostic: when XLCourier was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced to XLCourier, or a pickup is being arranged |
| `in_transit` | Collected, or received at a depot or the national hub |
| `out_for_delivery` | With the courier today |
| `delivered` | Delivered |
| `returning` | Being returned, or already back with the sender |
| `problem` | A delivery attempt failed (until the next scan), or the parcel was destroyed |
| `unknown` | Not yet scanned, or a scan type we have not mapped yet |

The status follows the newest scan. After a failed attempt the parcel shows
`problem` until the next scan, which is usually its return to the hub.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the XLCourier device):

| Event | When |
|---|---|
| `xlcourier_parcel_registered` | A new parcel appears in the active list |
| `xlcourier_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `xlcourier_parcel_delivered` | A parcel is delivered |
| `xlcourier_parcel_delivery_time_changed` | The expected delivery window changes — XLCourier gives no delivery window, so this does not fire today |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `xlcourier.track_parcel` | `tracking_code` | Start tracking a parcel |
| `xlcourier.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/ha-parcel-card](https://github.com/jonisnet/ha-parcel-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.xlcourier: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — XLCourier does not know the code. A parcel normally shows up once the sender has created its label, so check the code against your shipping confirmation; a very old parcel may also have been removed from their system.
- **A status logs "Unrecognised XLCourier status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-xlcourier/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by XLCourier, Home Assistant, or any other third party referenced in this project. Please don't contact XLCourier for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to XLCourier's terms. Data is sent only to XLCourier's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking endpoint as the XLCourier consumer website.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
