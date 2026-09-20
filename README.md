# findmy-map

Shows the locations of the devices registered with Google "Find My Device"
(phone, ESP32 tracker, …) on a real map (Leaflet + OpenStreetMap) instead of
just a Google Maps link in the terminal. On top of that: a history track for a
chosen time range and a text-oriented list of visited places (similar to the
Google Timeline).

The service builds on the
[`leonboe1/GoogleFindMyTools`](https://github.com/leonboe1/GoogleFindMyTools)
library. It's meant to run as an **add-on container next to an already
configured GoogleFindMyTools container**, sharing that container's
`Auth/secrets.json` -- or, if you don't have one, the optional [setup
wizard](#generating-secretsjson-with-the-setup-wizard) below generates that
file for you directly, no separate instance required.

> This project is not affiliated with Google or Apple. Use at your own risk and
> only for devices/accounts you are authorised to access.

## Screenshots

<img src="docs/img/map-dark.jpg" alt="Map view with devices grouped into collapsible sections" width="100%">

<table>
<tr>
<td width="50%"><img src="docs/img/timeline.jpg" alt="Timeline: day/week/month step-through, track, visited places and GPX/GeoJSON/CSV export" width="100%"></td>
<td width="50%"><img src="docs/img/edit-device.jpg" alt="Editing a device's name, pin colour and group" width="100%"></td>
</tr>
<tr>
<td><img src="docs/img/map-light.jpg" alt="Map view, light theme, with one device group collapsed" width="100%"></td>
<td align="center"><img src="docs/img/map-mobile.jpg" alt="Mobile layout with the grouped device list as a bottom sheet" height="420"></td>
</tr>
</table>

![Clicking through the map, groups, the polling-failure banner and the timeline](docs/img/demo.gif)

<sub>All screenshots use synthetic demo data (fictional devices moving around Berlin), not real location history.</sub>

## Requirements

- A `secrets.json` with valid tokens, from either:
  - an already **logged-in** GoogleFindMyTools container, or
  - the [setup wizard](#generating-secretsjson-with-the-setup-wizard),
    which generates one directly, without a separate instance.

## Setup

Grab only `docker-compose.yml` and `.env.example` — the image is pulled from
GHCR, no clone or local build needed:

```bash
mkdir google-findmy-map && cd google-findmy-map
curl -O https://raw.githubusercontent.com/Don-Wombat/google-findmy-map/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/Don-Wombat/google-findmy-map/main/.env.example
# edit .env: GFM_SECRETS_FILE, GFM_DATA_DIR, PUID/PGID, PROXY_NETWORK
docker compose up -d
```

To build from source instead, clone the repo and flip `image:` back to
`build: .` in `docker-compose.yml`, then `docker compose up -d --build`.

Key `.env` values:

| Variable | Meaning |
|---|---|
| `GFM_SECRETS_FILE` | Host path to the **single** `secrets.json` of the existing GoogleFindMyTools container. Bind-mounted read-write so token refreshes stay in sync between both containers. Do **not** mount the whole `Auth/` folder — only this one file. |
| `GFM_DATA_DIR` | Host directory for this service's SQLite database (`history.db`). Holds raw location data — treat it as personal data. |
| `PUID` / `PGID` | Owner of the two paths above, and the UID/GID the app process runs as. The container starts as root just long enough for its entrypoint to chown `GFM_DATA_DIR` to match, then drops to this user before running the app. Check with `stat -c '%u:%g' "$GFM_SECRETS_FILE"`. |
| `PROXY_NETWORK` | Name of the external Docker network your reverse proxy is on. |

All other (optional) variables are documented in `.env.example`.

## Generating secrets.json with the setup wizard

Already have a logged-in GoogleFindMyTools container? Point `GFM_SECRETS_FILE`
at its `Auth/secrets.json` as described above and skip this section entirely
-- it's simpler when you already have one.

Don't have one, or need to regenerate `secrets.json` after an owner-key
change (see "Known limitations" below)? The **setup wizard** is a second,
opt-in container that generates it directly: it runs a real (not headless)
Chromium inside the container and streams its screen into a web page, so you
complete the actual Google sign-in yourself, in your own browser tab, exactly
like signing into any other Google service. This app's own code never sees
your password.

**This is a real browser with live Google network access, gated behind a
one-time token -- read the "Setup wizard" section in `SECURITY.md` before
using it.** It is not started by a plain `docker compose up`.

```bash
# GFM_SECRETS_FILE must already exist as a file (even if empty JSON) before
# the FIRST run, from either source above. If you're starting completely
# fresh:
mkdir -p "$(dirname "$GFM_SECRETS_FILE")"
echo '{}' > "$GFM_SECRETS_FILE"

docker compose --profile setup-wizard up -d setup-wizard
docker compose logs setup-wizard   # prints a one-time URL with ?token=...
```

Open that URL (via whatever reverse-proxy host you've pointed at the
`setup-wizard` service -- it has no published port, same as `findmy-map`
itself), click Start, and complete the Google sign-in inside the embedded
browser. **Two sign-in prompts may appear in a row** -- the wizard drives two
separate steps of the vendored login flow, and the second one is usually (but
not always) an instant, cookie-based continuation of the first rather than a
fresh prompt. Once it reports success, `secrets.json` is ready and
`findmy-map` will pick it up on its next poll.

```bash
docker compose --profile setup-wizard down   # stop it once you're done
```

Set `GFM_SETUP_WIZARD_URL` (see `.env.example`) to also show an "Open Setup
Wizard" link on the main app's settings page.

## Web UI

- **Light/dark toggle** (☀/☾ in the header of both pages), remembered in the
  browser. In dark mode the OSM tiles are darkened via a CSS filter (building
  outlines / streets / labels stay intact); no API key.
- **Language toggle** (EN/DE in the header), also remembered in the browser.
- The map fills the screen; the device list sits on top as a **floating panel**
  (top right on desktop, sized to its content; a collapsible bottom sheet on
  narrow screens). Sorted by most recent location; clicking a row centres the
  map.
- Each device has its own pin colour; the last 5 positions are joined by a
  line.
- **Edit devices:** the ✎ button on each row → change the display name, pick a
  pin colour from a palette, and put the device in a **group** (free text,
  with suggestions from the groups already in use). **Default** clears all
  three.
- **Groups:** once any device has a group, the list is split into collapsible
  sections ("Familie", "Fahrzeuge", …) with an "Ungrouped" section at the
  bottom. Collapsing a group also hides its pins from the map — collapsed
  state is remembered per browser. The timeline's device picker groups the
  same way (`<optgroup>`).
- **Old devices:** the settings page lists devices that no longer appear in
  the poll (still in the timeline picker, gone from the map) and lets you
  delete one — its whole history and its overrides. The delete is keyed on
  the device id, never the name, and the confirm step shows the full id, so
  two devices you happened to rename to the same name stay distinguishable;
  a device that is still live cannot be deleted.
- **Reset a device's history:** also on the settings page, a device picker
  (every device, live or stale) lets you wipe just its location history —
  unlike deleting the device, its name/colour/group overrides are kept.
  There is no liveness restriction: resetting a live device's history
  simply starts it fresh from the next poll.
- **Ring a device:** the 🔔 button on each row makes it play its "find my
  device" sound; tap again (or wait ~30 s) to stop. The button shakes the
  instant the tap registers — there's a real multi-second FCM round-trip
  before the phone actually rings, so that feedback can't wait on the
  network.
- A device whose last report is a **semantic location** (a named place
  without coordinates, e.g. "Home") gets no map pin — Google's API doesn't
  send coordinates for those — but the place name is shown in the device
  list instead of being silently dropped.
- **Polling-failure banner:** a red bar across the top of the map and
  timeline pages once polling has failed `GFM_POLL_ALERT_AFTER` times in a
  row (default 3 — an expired `secrets.json`, an upstream break), so you
  find out without opening the app to check. It clears itself when polling
  recovers. `GET /api/health` exposes the same state for an external
  uptime monitor.
- **Timeline** (`timeline.html`): pick a device, then step through by
  **Day / Week / Month** with the ‹ › arrows (or pick a free **Range**) →
  the full track as a line, plus a **list of visited places** (address,
  arrival–departure, duration) with numbered markers. A visit is only
  formed when the stored history actually contains several reports from
  *one* place (radius `GFM_VISIT_RADIUS_M`) spanning at least
  `GFM_VISIT_MIN_MINUTES` — with a still sparse history a note is shown,
  and it fills in over time. Long visit lists are capped with a "show
  more" button so the export controls below stay reachable. The device
  picker lists every device ever seen, not just ones in the current poll;
  the chosen view mode + date are remembered per browser. Each visited
  place can also be deleted — this hard-deletes the underlying raw points
  for that stay (so it also shortens the track shown for that period) and
  cannot be undone.
- **Export:** the timeline offers **GPX / GeoJSON / CSV** downloads of the
  track and of the visited places for the chosen device and date range; the
  settings page has a per-device **full-history** export. For backup, or for
  QGIS / Google Earth / a script.

### Authentication

Optional and **off by default**. Open the settings page (the ⚙ icon in the
header), tick **Require login** and set a username and a password (min. 8
characters) — both are required the first time you enable it. From then on
every page and API call needs the session cookie from the login page. The
same settings page changes the username, the password, or turns auth off
again (all three ask for the current password).

If you lock yourself out — forgotten username or password — set
`GFM_AUTH_DISABLE=1` and restart — auth is forced off so you can reset it.
With auth enabled you can expose the service directly, but **only over
HTTPS** (see `SECURITY.md`).

## How it works

- `Dockerfile` clones `GoogleFindMyTools` (commit pinned via
  `ARG GFM_UPSTREAM_REF`) into `/app/vendor`.
- `docker-compose.yml` mounts only the single `secrets.json` into the vendored
  `Auth` folder — login data / token refresh shared rather than duplicated,
  without overwriting the rest of the vendored auth code. The container
  starts as root; `entrypoint.sh` chowns the data volume to `PUID:PGID`,
  then execs the app via `gosu` as that user, so the long-running process
  is never root.
- `service/locations.py` uses the library functions but returns structured
  data instead of only printing it.
- `service/main.py` — FastAPI + a background poll thread. Endpoints:
  `GET /api/locations` (incl. `palette` and `poll_alert`), `GET /api/health`
  (public liveness probe — `{ok, poll_alert, last_poll, …}`, no error
  detail), `GET /api/devices` (every device ever seen, for the timeline
  picker, with a `live` flag and `point_count`), `GET /api/history`,
  `GET /api/visits`, `GET /api/export/history` and `GET /api/export/visits`
  (`?format=gpx|geojson|csv`, optional `start`/`end`), `POST /api/refresh`,
  `PUT /api/devices/{id}`, `DELETE /api/devices/{id}` (stale devices only —
  409 while live), `DELETE /api/history` (deletes a device's location fixes
  in a required `start`/`end` window — used to delete one visited place),
  `DELETE /api/devices/{id}/history` (resets a device's history, keeping
  its name/colour/group — no liveness restriction),
  `POST /api/devices/{id}/ring[/stop]`. The mutating endpoints reject
  cross-site requests (Fetch Metadata).
- `service/export.py` — the GPX / GeoJSON / CSV formatters (stdlib only).
- `service/store.py` — SQLite: full history (`add`/`recent`/`range` + a
  one-time `history.json` migration), device overrides (name / colour /
  group), geocode cache. New columns are added to an existing DB on
  startup (`_migrate_schema`).
- `service/colors.py` (pin colour, with validation), `service/augment.py`
  (track/name/colour/group per device), `service/visits.py` (clusters
  points into stays), `service/geocode.py` (reverse geocoding via
  Nominatim, ≤ 1 request/1.1 s, negative cache + backoff),
  `service/auth.py` (the optional built-in login).
- `web/index.html` (map), `web/timeline.html`, `web/settings.html`
  (theme / language / login / data export / device-history reset /
  old-device deletion),
  `web/login.html`, `web/app.css`, `web/app.js`.
- `setup/` — the opt-in setup wizard, a separate image/container (own
  `Dockerfile`, not built or started by default). A virtual X display
  (Xvfb) runs a real, non-headless Chromium, streamed into a browser tab via
  noVNC/websockify; a small FastAPI app (`setup/app/main.py`) gates access
  behind a one-time token, drives the vendored login chain in a watched
  subprocess (`setup/app/run_flow.py`) with a hard timeout, and writes
  directly into the same `secrets.json` `findmy-map` uses. See "Generating
  secrets.json with the setup wizard" above and the "Setup wizard" section
  in `SECURITY.md`.

## Environment variables

See `.env.example`. Summary:

| Variable | Default | Purpose |
|---|---|---|
| `GFM_POLL_INTERVAL_SECONDS` | `120` | Poll interval |
| `GFM_HISTORY_DB` | `/data/history.db` | SQLite DB (full history + settings + geocode cache) |
| `GFM_HISTORY_FILE` | `/data/history.json` | only for a one-time migration from an earlier version |
| `GFM_DEVICE_COLORS` | – | JSON `{id-or-name: hex/keyword}`. Priority: UI colour → env → palette |
| `GFM_NOMINATIM_URL` | public OSM Nominatim | reverse geocoding; **empty = off** |
| `GFM_GEOCODE_EMAIL` | – | contact email sent as the `email=` parameter (OSM policy) |
| `GFM_VISIT_RADIUS_M` / `GFM_VISIT_MIN_MINUTES` | `100` / `15` | definition of a "visited place" |
| `GFM_HISTORY_RETENTION_DAYS` | – | delete location fixes older than this many days; **empty = keep forever** (unchanged default) |
| `GFM_POLL_ALERT_AFTER` | `3` | show the "updates are failing" banner after this many failed poll cycles in a row; `0` disables it |
| `GFM_AUTH_DISABLE` | – | `1` forces the optional login off (recovery from a lost password) |
| `GFM_LOGIN_DELAY_MS` | `500` | fixed delay per login attempt |
| `GFM_SETUP_WIZARD_URL` | – | shows an "Open Setup Wizard" link on the settings page when set |
| `GFM_SETUP_TOKEN_TTL` | `900` | (setup-wizard service) how long its printed token stays redeemable |
| `GFM_SETUP_RUN_TIMEOUT_SECONDS` | `900` | (setup-wizard service) hard ceiling on one wizard run |

## Known limitations

- The location history grows unbounded unless you opt into
  `GFM_HISTORY_RETENTION_DAYS` — it defaults to off, so an upgrade never
  starts silently deleting data an existing install never asked to lose.
- "Semantic locations" (named places without coordinates, e.g. "Home") never
  get a map pin — Google's API sends no coordinates for them, so there is
  nothing to place on the map. The name is shown in the device list instead.
- On an owner-key version change, `secrets.json` must be regenerated --
  either in the existing GoogleFindMyTools container, or by re-running the
  [setup wizard](#generating-secretsjson-with-the-setup-wizard) (it only
  overwrites the keys it re-fetches, so a re-run resumes rather than starting
  over).
- `secrets.json` is written non-atomically by both containers; a conflict on an
  exactly simultaneous token refresh is theoretically possible, in practice
  unlikely.

## License

[MIT](LICENSE)
