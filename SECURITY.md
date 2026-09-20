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
- **Non-root container.** The app process runs as an unprivileged UID
  (`${PUID}:${PGID}`), never as root. The container itself starts as root
  very briefly: `entrypoint.sh` (baked into the image) chowns the `/data`
  volume to `PUID:PGID`, then immediately execs the app under `gosu`
  (the same minimal privilege-drop tool used by the official `postgres`/
  `redis` images) as that UID/GID and never returns to root. That root
  window does a single `chown` and nothing else -- no network listener is
  open yet -- before the unprivileged app process replaces it as the
  container's main process.
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

## Setup wizard

The optional `setup-wizard` service (see README "Generating secrets.json
with the setup wizard") is a materially different security surface from the
rest of this project: it is the **first and only place that launches a real
browser with live network access to Google**, driven by a Selenium-controlled
Chromium and streamed to you over noVNC. Treat it accordingly:

- **Not started by default, no `restart: unless-stopped`.** It only runs
  when explicitly invoked (`docker compose --profile setup-wizard up -d`)
  and won't reappear on a host reboot or a plain `docker compose up -d` of
  the rest of the stack. Stop it (`--profile setup-wizard down`) once you're
  done — don't leave a browser-equipped container running longer than an
  actual setup session.
- **Independent, mandatory access gate.** It cannot share the main app's
  session/`cred_version` state (separate process, separate container) and is
  gated **regardless of whether the main app's optional login is on**: a
  random token (`secrets.token_urlsafe(32)`, 256 bits) is generated fresh on
  every container start, printed only to `docker compose logs`, and never
  written to disk. It is redeemable for a limited window
  (`GFM_SETUP_TOKEN_TTL`, default 900s) and, once redeemed, issues an
  HMAC-signed session cookie for that browser only. A container restart
  invalidates every previously issued session for free, since it mints a
  new token/signing secret each time.
- **Single-run lock and a hard timeout.** Only one login flow can run at a
  time (`POST /api/start` returns 409 while one is in progress). The flow
  runs as a watched subprocess with a hard ceiling
  (`GFM_SETUP_RUN_TIMEOUT_SECONDS`, default 900s, covering two sequential
  interactive Google logins plus overhead — see the design spec); on
  timeout the subprocess and its Chromium child are terminated, then killed
  if still alive.
- **The embedded browser (`/vnc/`) is gated the same way as everything
  else**, via nginx's `auth_request` against the same session cookie — an
  unauthenticated visitor cannot see or interact with an in-progress login,
  even if they reach the container's network address.
- **Ephemeral by design.** Each container run starts with a fresh Chromium
  profile — nothing about the browser session persists across restarts,
  only `secrets.json` itself (written by the same, unmodified
  `Auth/token_cache.py` mechanism the vendored library already uses).
- **`--no-sandbox` Chromium.** The vendored `chrome_driver.py` already
  launches Chromium with `--no-sandbox --disable-dev-shm-usage` — standard
  for any containerised Chrome, since Chrome's own sandbox needs setuid-root
  helper binaries this container deliberately doesn't grant. The container
  itself (non-root process, no other services on its network beyond
  loopback-bound internals) is the sandbox boundary here, not Chrome's own.
- **This app's code never sees your Google password.** The interactive step
  happens entirely inside Google's own pages, rendered in the browser you're
  looking at over noVNC; the wizard only ever reads back an OAuth cookie
  Google's page sets after you sign in, exactly like the vendored
  GoogleFindMyTools CLI tool already does when run interactively yourself.
- **Broader network egress than the rest of this project.** Unlike
  `findmy-map` itself (which only talks to Google's APIs and, optionally,
  Nominatim), the wizard's Chromium needs to reach Google's actual login
  pages, and `undetected_chromedriver` may attempt to fetch a matching
  chromedriver build from a CDN on first use even though `chromium-driver`
  is already installed at build time. Don't run this service somewhere with
  restricted egress without accounting for that.
- **Uploading an existing `secrets.json`** (`POST /api/upload`, gated the
  same as everything else here) validates only that the upload is
  well-formed JSON, is a JSON object, and is under a size ceiling (256 KB —
  a real `secrets.json` is a few KB) before writing it — it does **not**
  otherwise inspect the uploaded content. Trust in what it actually
  contains comes from the same place trust in a fresh login does: the
  ensuing verification step (a real `list_devices()` call against Google's
  API) either succeeds or reports a clear failure, never a silent
  false-positive. The single-run lock is acquired *before* the file is
  written, so an upload can never race a login already in progress for the
  same `secrets.json`. An upload **overwrites** the file outright — back it
  up yourself first if you want to keep whatever was there.

## Supply chain

The Docker image `git clone`s
[`leonboe1/GoogleFindMyTools`](https://github.com/leonboe1/GoogleFindMyTools)
at build time (`ARG GFM_UPSTREAM_REF` in the `Dockerfile`), pinned to a
specific commit SHA. You are trusting that
upstream project (and its dependency tree, which includes Selenium /
undetected-chromedriver even though no browser is launched at runtime in
this main service -- the separate, opt-in setup-wizard image, `setup/
Dockerfile`, is the exception: it does launch a real Chromium, and vendors
the same pinned upstream commit for the same reason -- see "Setup wizard"
above). Review the pinned commit before building, and bump it deliberately.

## Reporting a vulnerability

Open a private security advisory on the repository, or an issue if the
project has no advisory support. Please do not include working exploit
code in a public issue.
