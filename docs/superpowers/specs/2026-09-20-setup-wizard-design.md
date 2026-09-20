# findmy-map — setup wizard for generating secrets.json

## Summary

`findmy-map` has always required a `secrets.json` produced elsewhere: run
the vendored GoogleFindMyTools CLI locally, click through a real Chrome
login, then bind-mount the resulting file. Add an optional, separate
**setup wizard** container that generates this file directly: a real,
non-headless Chromium runs inside the container, its screen is streamed
into a web page over noVNC, and the operator completes the actual Google
sign-in themselves, in their own browser tab, exactly like signing into any
other Google service.

The existing "share an already-logged-in GoogleFindMyTools container's
file" path stays fully supported and documented as the simpler option when
one already exists — this is an alternative, not a replacement.

## Goals

- Generate a working `secrets.json` without needing to run GoogleFindMyTools
  separately, interactively, on some other machine.
- Never handle the operator's actual Google password in this project's own
  code — the interactive step happens entirely inside Google's real pages,
  rendered in the streamed browser.
- Reuse the vendored `Auth/`/`KeyBackup/`/`SpotApi/` login chain exactly as
  upstream wrote it (same pinned `GFM_UPSTREAM_REF`), rather than
  reimplementing any part of the token exchange.
- Keep this heavy, security-sensitive component fully opt-in and separate
  from the always-on `findmy-map` service: its own image, not started by a
  plain `docker compose up`, no `restart: unless-stopped`.
- A single, independent access gate that works regardless of whether the
  main app's optional login is enabled.
- A test suite that actually exercises the container's own logic (gate,
  timeout, process supervision) without pretending the literal interactive
  Google login can be automated.

## Non-goals (YAGNI)

Server-driven headless login without a visible browser (defeats the
"never handle the password, avoid anti-automation flags" goal, and Google's
own anti-bot heuristics specifically target this). Multi-account support.
Persisting the Chromium profile across runs. A published GHCR image for
this component (build locally until it has real-world mileage). Replacing
the existing "share a GoogleFindMyTools container's file" path.

## Two interactive logins, not one

Tracing the vendored chain (`GoogleFindMyTools/Auth/*.py`,
`KeyBackup/*.py`) surfaced two facts that are easy to miss from a surface
reading and materially shape the design:

1. `Auth/aas_token_retrieval.py::_generate_aas_token()` calls
   `request_oauth_account_token_flow()` — the well-known interactive step
   (opens Chrome, waits up to 300s for an `oauth_token` cookie after the
   operator signs in). But `KeyBackup/shared_key_retrieval.py`
   (`get_owner_key()` → `get_shared_key()`, called *after* the aas_token)
   opens a **second, fresh** Chrome session via
   `KeyBackup/shared_key_flow.py::request_shared_key_flow()`, navigating to
   `accounts.google.com/` again and waiting up to 300s for a redirect to
   `myaccount.google.com`. In practice this second wait is usually an
   instant, cookie-based continuation of the first sign-in — but the code
   is fully prepared to block on a real second prompt (e.g. a step-up
   check), so the wizard treats it as a second potentially-interactive
   wait. Worst case: ~600s, not 300s — this sets
   `GFM_SETUP_RUN_TIMEOUT_SECONDS`'s default (900s, with headroom).
2. Both interactive functions open with a blocking
   `input("Press Enter to continue...")` on stdin — no flag to skip it, and
   no TTY in a container to satisfy it. `setup/app/run_flow.py` monkeypatches
   `builtins.input` to a no-op before calling either function.

## Architecture

A new, self-contained subdirectory, `setup/`, with its own `Dockerfile`
(build context = `setup/`, no dependency on `service/`/`web/`):

- **Process supervision:** `supervisord` runs five long-running processes
  non-root (entrypoint.sh has already dropped to `PUID:PGID`): Xvfb
  (virtual display), `fluxbox` (window manager), `x11vnc` (bound to
  `-localhost` only — this container may share `proxy-net` with other
  services, and the raw VNC port must not be reachable except through the
  gate), `websockify` (VNC-over-websocket + noVNC's static client, via
  `--web=/usr/share/novnc`), and the wizard's own FastAPI control app
  (`uvicorn`, port 5000, loopback only).
- **Single external port via nginx path-routing:** `nginx` (the full
  package, not `nginx-light`, which lacks `ngx_http_auth_request_module`)
  is the only process actually bound to the container's exposed port
  (8090). `/vnc/` proxies to websockify with an `auth_request` subrequest
  against the wizard app (websockify has no notion of our session cookie,
  so the gate has to sit in front of it); everything else proxies to the
  wizard app directly. Chosen over Caddy (not in Debian's apt repo — would
  need a third-party source, against this project's "apt or a pinned git
  clone" build posture).
- **Access gate, independent of `service/main.py`'s:** a separate process/
  container can't share the main app's in-process session state. A random
  `SETUP_TOKEN` (`secrets.token_urlsafe(32)`) is generated once per
  container start by `entrypoint.sh`, printed only to `docker logs`, never
  persisted — it doubles as both the bearer credential and the HMAC
  signing secret for issued session cookies, so a container restart
  invalidates every previous session for free (no separate `cred_version`
  counter needed, unlike the main app, since there's only ever one process
  lifetime to invalidate). `setup/app/auth.py` duplicates
  `service/auth.py`'s token helpers near-verbatim rather than importing
  across the two independent build contexts — the same
  duplicate-a-small-helper pattern the existing Docker test files already
  established (no `conftest.py` in this repo).
- **Run-state machine + hard timeout:** `idle -> running ->
  {success, failed, timed_out}`, single-run-locked. The login chain runs as
  a genuine **subprocess** (`setup/app/run_flow.py`), not a thread —
  Selenium's blocking waits inside the vendored code aren't reliably
  interruptible from another thread in the same process, but a subprocess
  gives the timeout watchdog a clean `terminate()`/`kill()` target that
  takes the Chrome child down with it. Progress is reported as JSON Lines
  on the subprocess's real stdout (one per vendor `print()` call, tagged
  with the current stage) and polled by the frontend via `GET /api/status`
  — no websocket needed for this part, which is entirely separate from the
  noVNC websocket.
- **Partial-write behaviour deliberately unchanged:**
  `Auth/token_cache.py::set_cached_value` rewrites the whole file per key,
  non-atomically — existing, documented behaviour
  (`.claude/rules/secrets-and-git.md`: don't make this more aggressive). A
  timeout between `aas_token` and `owner_key` leaves exactly the subset a
  manually-interrupted CLI run would leave; `get_cached_value_or_set` is
  idempotent per key, so a re-run resumes rather than starting over — this
  is also how an owner-key regeneration (see README "Known limitations")
  is meant to be used.
- **`docker-compose.yml`:** a new `setup-wizard` service using Compose
  `profiles: ["setup-wizard"]` (new to this project, but the built-in
  mechanism for exactly this "occasionally-needed helper" shape) so a
  plain `docker compose up` never starts it; `restart: "no"`; attached only
  to `proxy-net`, no published port — consistent with every other service
  in this deployment (see the user's own homelab-wide convention: no
  published ports, reachability only via the reverse proxy). Mounts the
  *same* `GFM_SECRETS_FILE` the main service uses, so a successful run is
  immediately usable with no manual copy/upload step.

## Things found only by building and running it for real

Several failures only showed up building against this repo's actual dev
sidecar (a nested Docker-in-Docker environment with an unusually
restrictive build-host umask compared to GitHub Actions' runners) — not
predictable from reading the Dockerfile alone:

- **`git clone .../app/vendor` before `WORKDIR /app` implicitly creates
  `/app` with the build host's umask**, not a fixed mode. In this repo's
  dev sidecar that leaves `/app` non-traversable for any UID but root,
  breaking every non-root process's access under it — the *same* latent,
  umask-dependent gap exists in the root `Dockerfile` too (never
  manifested there because GitHub Actions' standard umask happens to leave
  it traversable). Fixed here with an explicit `chmod a+rX /app`; worth
  hardening in the root `Dockerfile` too, but out of scope for this
  change.
- **`pip install`-ed packages inherit the same build-host umask** — left
  unreadable to the non-root runtime user, surfacing as a confusing
  `ModuleNotFoundError: No module named 'uvicorn.main'` (import machinery
  silently treating "permission denied" as "not found") rather than an
  obvious permissions error. Fixed with an explicit
  `chmod -R a+rX /usr/local/lib/python3.11/site-packages /usr/local/bin`
  after `pip install`.
- **`COPY`-ed `nginx.conf`/`supervisord.conf` had the same problem** —
  `supervisord` failed outright with "could not read config file" until
  explicitly `chmod`ed.
- **supervisord can't reliably redirect a non-root child's stdout/stderr to
  `/dev/stdout`/`/dev/stderr`** — every supervised process failed to spawn
  at all ("unknown error making dispatchers: EACCES") until per-program
  logs were pointed at plain files under `/tmp` instead. A known
  supervisord-in-Docker rough edge, not specific to this project.
- **nginx validates/creates *every* standard temp-file path at startup**
  (`fastcgi_temp_path`, `uwsgi_temp_path`, `scgi_temp_path`), not just the
  ones a given config actually uses — the Debian package's compiled-in
  defaults (`/var/lib/nginx/*`) are root-owned, so a non-root master
  process failed with `mkdir() ... Permission denied` until all of them
  were pointed at `/tmp` explicitly, even though this config never proxies
  to fastcgi/uwsgi/scgi.
- **`python3 -m uvicorn` fails on this uvicorn version** (0.53.0) — its own
  installed console-script also fails with `ModuleNotFoundError: No module
  named 'uvicorn.main'`, an apparent upstream packaging inconsistency
  between the script's `entry_points` metadata and the package's actual
  internal layout. Worked around by invoking the `uvicorn` console-script
  directly rather than `-m uvicorn`.
- **A missing `GFM_SECRETS_FILE` host path cannot be "fixed" from inside
  the container.** Docker's bind-mount machinery decides file-vs-directory
  for a non-existent host path *before* the container even starts, daemon-
  side — no in-container logic can turn an already-created directory back
  into a file. `entrypoint.sh` therefore fails fast with an explicit,
  actionable error instead of attempting (and silently failing at) an
  in-container fix; the README documents the required pre-step
  (`echo '{}' > "$GFM_SECRETS_FILE"` before the very first run).

Every one of these was caught by actually building and running the image
against the dev sidecar and iterating until `service/tests/
test_setup_docker_process.py` passed end-to-end (build, boot all five
supervised processes, redeem a token, confirm the `/vnc/` gate) — not by
reasoning about the Dockerfile on paper.

## Testing strategy

- `service/tests/test_setup_wizard_logic.py` — fast, no Docker. Exercises
  `setup/app/main.py`'s HTTP-level behaviour (gate, TTL, single-run lock,
  timeout/kill, a crash-without-a-final-line safety net) against a *fake*
  `RUN_FLOW_SCRIPT` monkeypatched in, and `setup/app/run_flow.py`'s own
  stage-sequencing/error-handling directly (no subprocess) with the
  vendored `Auth`/`SpotApi`/`NovaApi` chain stubbed via `sys.modules`
  fakes — the same stub-before-import pattern `test_api.py` already uses
  for `locations`.
- `service/tests/test_setup_docker_build.py` /
  `test_setup_docker_process.py` — mirror `test_docker_build.py` /
  `test_docker_entrypoint.py` exactly (same `_docker_available()` /
  apt-sandbox-workaround pattern, `pytest.mark.docker`, auto-skip without a
  reachable daemon). The process test drives all HTTP checks through
  `docker exec ... curl` rather than a published port, since this repo's
  dev sidecar isn't necessarily on the same network as the test process
  (same reasoning `test_docker_entrypoint.py` already documents for
  bind-mount paths, extended to networking here).
- **Explicitly not automatable, by any test here:** the actual interactive
  Google login — real password entry, 2FA, CAPTCHA, Google's anti-
  automation heuristics against a Selenium-driven Chromium. That is a
  manual, human verification step against a real (ideally disposable)
  account, documented as a release-gate step, not something a passing test
  suite implies.
