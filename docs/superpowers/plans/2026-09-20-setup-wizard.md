# Setup Wizard — Implementation Plan

**Goal:** Add an opt-in, separate `setup-wizard` Docker service that generates `secrets.json` by streaming a real, non-headless Chromium (running the vendored GoogleFindMyTools login chain) into a web page over noVNC, so the operator completes the actual Google sign-in themselves without needing a separately-run GoogleFindMyTools instance.

**Architecture:** `setup/` is a self-contained subdirectory with its own `Dockerfile`/build context. `supervisord` runs five non-root processes (Xvfb, fluxbox, x11vnc, websockify, a FastAPI control app); `nginx` is the single exposed port, path-routing `/vnc/` (auth-gated via `auth_request`) to websockify and everything else to the control app. The control app gates access with a one-time token (independent of `service/main.py`'s session system), enforces a single-run lock and a hard timeout, and runs the vendored login chain as a watched subprocess (`run_flow.py`) so a timeout can cleanly kill it and its Chrome child. See the design spec for the full rationale and every empirically-found permissions/startup bug this surfaced.

**Tech Stack:** Debian (`python:3.11-slim` base) + apt (`xvfb`, `x11vnc`, `fluxbox`, `chromium`/`chromium-driver`, `novnc`, `websockify`, `nginx`, `supervisor`, `gosu`, `curl`), Python 3.11 + FastAPI/uvicorn for the control app, the vendored GoogleFindMyTools `Auth`/`KeyBackup`/`SpotApi`/`NovaApi` chain (same pinned `GFM_UPSTREAM_REF` as the main image), pytest for the fast logic tests plus real Docker-backed tests via the repo's DinD dev sidecar.

**Spec:** `docs/superpowers/specs/2026-09-20-setup-wizard-design.md`

## Global Constraints

- No changes to `Auth/token_cache.py`'s non-atomic, per-key write behaviour.
- `builtins.input` must be monkeypatched to a no-op before calling either vendored interactive-login function (both block on real stdin otherwise).
- Never log/return a raw exception repr from the login subprocess — type name + a short, truncated message only (some vendor exceptions can carry response bodies).
- No published ports on the `setup-wizard` compose service — `proxy-net` only, matching every other service in this deployment.
- `restart: "no"`, not `unless-stopped` — this container must not silently reappear.
- Follow the existing Docker test conventions exactly (`_docker_available()`, the apt-sandbox `APT::Sandbox::User=root` patch, `pytest.mark.docker`, best-effort `docker rmi -f` cleanup) rather than inventing a new pattern.
- Commit as `feat:` / `test:` / `docs:` per logical change; push/tag/release only on explicit later request.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `setup/Dockerfile` | Create | Image build: apt packages, vendored checkout, pip install, permission fixes |
| `setup/entrypoint.sh` | Create | chown secrets file (or fail fast if Docker created a directory), generate+print `SETUP_TOKEN`, gosu-drop |
| `setup/supervisord.conf` | Create | Five-process supervision, file-based per-program logs |
| `setup/nginx.conf` | Create | Single-port path routing, `/vnc/` `auth_request` gate, all temp paths under `/tmp` |
| `setup/requirements.txt` | Create | FastAPI/uvicorn + the vendor-chain-driving deps |
| `setup/.env.example` | Create | Standalone env docs for running `setup/` outside the root compose file |
| `setup/app/auth.py` | Create | Token-gate session cookie helpers (duplicated/simplified from `service/auth.py`) |
| `setup/app/state.py` | Create | Run-state machine + bounded log buffer |
| `setup/app/run_flow.py` | Create | Subprocess script: monkeypatches `input`, drives the vendor chain, emits JSON-line progress |
| `setup/app/main.py` | Create | FastAPI control app: gate, `/api/auth-check`, `/api/status`, `/api/start`, subprocess watchdog |
| `setup/web/index.html` / `app.js` / `app.css` | Create | Start button, live log, embedded noVNC iframe |
| `docker-compose.yml` | Modify | New `setup-wizard` service (profile-gated); `GFM_SETUP_WIZARD_URL` on `findmy-map` |
| `.env.example` | Modify | `GFM_SETUP_WIZARD_URL` / `GFM_SETUP_TOKEN_TTL` / `GFM_SETUP_RUN_TIMEOUT_SECONDS` |
| `service/main.py` | Modify | `GET /api/config` (public), `PUBLIC_PATHS` addition |
| `web/settings.html` | Modify | New "Setup Wizard" section, hidden unless `GFM_SETUP_WIZARD_URL` is configured |
| `web/app.js` | Modify | `s_setup_wizard` / `setup_wizard_hint` / `open_setup_wizard` i18n strings (EN + DE) |
| `service/tests/test_setup_wizard_logic.py` | Create | Fast, no-Docker: gate, lock, timeout, `run_flow.py` stage sequencing |
| `service/tests/test_setup_docker_build.py` | Create | Real `docker build` of `setup/Dockerfile`, mirrors `test_docker_build.py` |
| `service/tests/test_setup_docker_process.py` | Create | Real container boot + gate/token/`/vnc/` verification, mirrors `test_docker_entrypoint.py` |
| `.github/workflows/ci.yml` | Modify | New `wizard-docker-build` job |
| `README.md` | Modify | "Generating secrets.json with the setup wizard" section, env table rows, known-limitations cross-reference |
| `SECURITY.md` | Modify | New "Setup wizard" section |

---

## Task 1: `setup/` scaffolding and the login-chain subprocess

- [x] `setup/requirements.txt`, `setup/Dockerfile` (apt packages, vendored checkout pinned to `GFM_UPSTREAM_REF`, pip install).
- [x] `setup/app/state.py` — `idle -> running -> {success, failed, timed_out}`, lock-guarded, bounded log deque.
- [x] `setup/app/run_flow.py` — monkeypatch `input`, redirect the vendor chain's `print()` output into JSON-Lines tagged with the current stage, call `get_aas_token()` → `get_owner_key()` (pulls in the second interactive login) → `list_devices()` as a smoke test, never leak a raw exception repr on failure.
- [x] `setup/app/auth.py` — signed session-token helpers (`token_matches`/`make_session_token`/`parse_session_token`), no `cred_version` needed (the token itself is the per-process secret).
- [x] `setup/app/main.py` — gate middleware (bare `?token=` redemption within `GFM_SETUP_TOKEN_TTL`, cookie thereafter), `/api/auth-check` (for nginx's `auth_request`), `/api/status`, `/api/start` (single-run lock, subprocess spawn + timeout watchdog + defensive `pkill -f chromium` sweep on timeout).
- [x] `setup/web/index.html` / `app.js` / `app.css` — Start button, polled log, embedded `/vnc/vnc.html` iframe.
- [x] `setup/entrypoint.sh` — fail fast with an actionable message if the secrets path is a directory (Docker's bind-mount auto-creation, unfixable from inside the container); otherwise chown + generate/print `SETUP_TOKEN` + gosu-drop.
- [x] `setup/supervisord.conf` / `setup/nginx.conf` — five-process supervision, single-port path routing with the `/vnc/` `auth_request` gate.

## Task 2: Make it actually boot (found only by building it)

- [x] Verified via the repo's DinD dev sidecar, iterating until the container fully booted and the gate worked end-to-end. Fixed, in order discovered: unreadable `nginx.conf`/`supervisord.conf` (missing `chmod`), unreadable `/app` (implicitly created by `git clone` before `WORKDIR`, inherits build-host umask), non-root supervisord unable to redirect children to `/dev/stdout`/`/dev/stderr` (switched to `/tmp/*.log`), nginx's fastcgi/uwsgi/scgi temp paths (root-owned defaults, added explicit `/tmp` paths), unreadable pip-installed `site-packages` (same umask issue, surfaced as a confusing `ModuleNotFoundError`), `python3 -m uvicorn` not supported by the installed uvicorn version (switched to the `uvicorn` console-script directly). All documented with rationale comments at the fix site and in the design spec.
- [x] `service/tests/test_setup_docker_build.py` and `test_setup_docker_process.py` written and passing for real against the dev sidecar (build, boot, token redemption, `/vnc/` gating, chown verification).

## Task 3: Wire into the main app

- [x] `service/main.py`: `GET /api/config` (public, returns `setup_wizard_url` from `GFM_SETUP_WIZARD_URL`), added to `PUBLIC_PATHS`.
- [x] `web/settings.html`: new section after Authentication, `hidden` by default, unhidden + linked by `loadSetupWizardLink()` if `GFM_SETUP_WIZARD_URL` is set.
- [x] `web/app.js`: EN + DE i18n strings.
- [x] `docker-compose.yml`: `setup-wizard` service (`profiles: ["setup-wizard"]`, `restart: "no"`, `proxy-net` only, shares `GFM_SECRETS_FILE`); `GFM_SETUP_WIZARD_URL` added to `findmy-map`'s environment.
- [x] `.env.example`: new vars documented.

## Task 4: Fast logic tests

- [x] `service/tests/test_setup_wizard_logic.py`: gate (missing/wrong/expired token, valid redemption + cookie), single-run lock, happy-path/timeout/failure/crash-without-final-line via a fake `RUN_FLOW_SCRIPT`, and `run_flow.py`'s own stage sequencing + `input`-patch + error-truncation with the vendor chain stubbed via `sys.modules` fakes.
- [x] Full suite (`python -m pytest -q`, all 275 tests incl. all `docker`-marked ones) green against the real dev sidecar.

## Task 5: CI and docs

- [x] `.github/workflows/ci.yml`: `wizard-docker-build` job mirroring `docker-build`.
- [x] `README.md`: new "Generating secrets.json with the setup wizard" section, "Requirements"/intro updated, "How it works" bullet, env-var table rows, "Known limitations" owner-key bullet cross-referenced.
- [x] `SECURITY.md`: new "Setup wizard" section (token gate, single-run lock, timeout, ephemeral profile, `--no-sandbox` rationale, broader network egress, explicit acknowledgment this is the first real-browser surface in the project); "Supply chain" section's "no browser at runtime" claim scoped to the main service only.

## Explicitly out of scope

No push, no version bump, no release, no GHCR image for `setup-wizard` — only on explicit later request, matching every prior round in this repo. No fix to the root `Dockerfile`'s equivalent latent `/app`-permissions gap (documented as a follow-up, not part of this change).
