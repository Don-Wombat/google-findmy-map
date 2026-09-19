# Security

## Authentication

`findmy-map` has an **optional** built-in login: a single account
identified by a username and a password, a signed session cookie, and
per-IP throttling of failed attempts. It is **disabled by default**.
Enable it and set the username and password on the settings page (the
gear icon in the header) — both are required the first time you enable
it.

Installs that enabled auth before this credential existed keep working
with just a password after an upgrade: the username is only checked once
one has actually been set (empty/unset means "not enforced"), so nothing
locks out until you visit the settings page and set one.

### With authentication enabled

You may expose the service directly — **but only over HTTPS**. Without
TLS the password and the session cookie travel in clear. When the request
is HTTPS the cookie is flagged `Secure` automatically (detected from the
request scheme / `X-Forwarded-Proto`).

A TLS-terminating reverse proxy is the usual way to add HTTPS. If it runs
on the same host, start uvicorn with `--proxy-headers
--forwarded-allow-ips=<proxy-ip>` so the login throttle sees real client
IPs (the `Secure` detection works regardless).

`GFM_LOGIN_DELAY_MS` (default `500`) is a fixed delay added to every login
attempt. After 5 failures from one IP within 15 minutes that IP is put on
a cooldown that grows 30 s → 2 min → 10 min → 30 min.

**Forgot the username or password / locked out:** set `GFM_AUTH_DISABLE=1`
in the environment and restart — authentication is then forced off
regardless of the stored setting, so you can open the settings page and
set new ones.

### With authentication disabled

The service has **no access control of any kind** — anyone who can reach
the HTTP port sees the full location history. You **must** run it behind
an authenticating reverse proxy, a VPN, or Tailscale. The provided
`docker-compose.yml` publishes no ports and attaches only to an external
`proxy-net` network for this reason.

Treat the database at `${GFM_DATA_DIR}/history.db` as sensitive personal
data. Back it up and store it accordingly. By default it is kept forever;
set `GFM_HISTORY_RETENTION_DAYS` if you'd rather it aged out automatically.

## Defence-in-depth measures built into the app

These do **not** replace the reverse-proxy authentication above.

- **Cross-site request blocking.** `POST /api/refresh`,
  `PUT /api/devices/{id}`, `DELETE /api/devices/{id}`, `DELETE /api/history`,
  `DELETE /api/devices/{id}/history`, `POST /api/devices/{id}/ring[/stop]`,
  `POST /api/auth/login` and `PUT /api/settings/auth` reject requests whose
  `Sec-Fetch-Site` header is
  `cross-site`/`same-site` (Fetch Metadata). This stops a random web page
  the operator visits from triggering polls, edits, deletes, or ringing a
  device. Non-browser clients (curl, scripts) send no such header and are
  unaffected.
- **Device deletion is id-keyed and can't hit a live device.**
  `DELETE /api/devices/{id}` resolves the target strictly by the path id
  (never by display name — two devices renamed to the same name stay
  independently addressable) and returns 409 while the device is still in
  the current poll, so its history can't be wiped by mistake and then
  silently re-accumulated.
- **Location-history deletion is explicit-range-only, with no silent
  default.** `DELETE /api/history` requires `start`/`end` in the query
  string (422 if `end` is before `start`) rather than defaulting to "the
  whole account" like the read/export endpoints' 0-to-now fallback — a
  destructive range delete should never silently apply to more than the
  caller asked for. `DELETE /api/devices/{id}/history` resets a device's
  entire history but, unlike `DELETE /api/devices/{id}`, leaves its
  `device_settings` row (name/colour/group) untouched and has no liveness
  (409) restriction — it may target a device that is still being polled.
- **Input validation.** Pin colours (from the API and from
  `GFM_DEVICE_COLORS`) must be a plain hex value or CSS colour keyword; SQL
  is fully parameterised; device names, error text and semantic-location
  place names are all HTML-escaped in the frontend before they ever reach
  the DOM.
- **`GET /api/health` is intentionally public** (reachable without the
  optional login, for an external uptime monitor). It carries only
  liveness booleans and `last_poll` — never the polling error string,
  which stays behind the gate on `/api/locations`.
- **The export endpoints** (`GET /api/export/history`,
  `GET /api/export/visits`) are read-only and sit behind the same access
  boundary as `GET /api/history` / `GET /api/visits` — the auth gate when
  the optional login is on, the reverse proxy otherwise. They never
  trigger a geocoding lookup (only already-cached labels are attached).
- **Reverse-geocoding rate limiting.** The background geocoder makes at
  most one request every ~1.1 s to the configured Nominatim endpoint,
  negatively caches failed lookups, and backs off exponentially on
  repeated failures — so an outage can't turn into a request flood that
  gets your IP blocked by the public OSM Nominatim.
- **Non-root container.** The service runs as an unprivileged UID
  (`${PUID}:${PGID}`).
- **Password storage.** The password is stored as a stdlib `scrypt` hash
  (`n=2^14, r=8, p=1`), never in clear; verification is constant-time.
- **Session tokens.** The session cookie is an HMAC-SHA256-signed token
  bound to a `cred_version` counter; changing the username, changing the
  password, or toggling auth on all increment it and invalidate every
  existing session.
- **The `current_password` check on the settings page is not rate-limited.**
  It is only reachable with a valid session (the auth gate requires one
  when auth is on), and `scrypt` verification is deliberately slow, so
  brute-forcing it from an already-authenticated session is impractical —
  but it is not behind the login throttle.

## Supply chain

The Docker image `git clone`s
[`leonboe1/GoogleFindMyTools`](https://github.com/leonboe1/GoogleFindMyTools)
at build time (`ARG GFM_UPSTREAM_REF` in the `Dockerfile`), pinned to a
specific commit SHA. You are trusting that
upstream project (and its dependency tree, which includes Selenium /
undetected-chromedriver even though no browser is launched at runtime).
Review the pinned commit before building, and bump it deliberately.

## Reporting a vulnerability

Open a private security advisory on the repository, or an issue if the
project has no advisory support. Please do not include working exploit
code in a public issue.
