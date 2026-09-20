"""Setup wizard control app: gate, run-state machine, and the noVNC-facing
auth check nginx's auth_request module calls for /vnc/ (see nginx.conf).

Kept deliberately small and separate from service/main.py -- this is a
different process/container with a different, much narrower security
surface (see SECURITY.md): one bearer token instead of a username/password,
no persistent config, exactly one thing it can do.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import auth
import state

APP_DIR = Path(__file__).parent
WEB_DIR = Path(os.environ.get("GFM_SETUP_WEB_DIR", "/app/setup-web"))
RUN_FLOW_SCRIPT = APP_DIR / "run_flow.py"
# Same path entrypoint.sh chowns and the vendored Auth/token_cache.py
# writes to -- hardcoded here to match, the same way it's hardcoded in
# entrypoint.sh, the Dockerfile's vendor clone target, and
# docker-compose.yml's mount target (this project doesn't parameterise it
# anywhere else either).
SECRETS_PATH = Path("/app/vendor/Auth/secrets.json")
_MAX_UPLOAD_BYTES = 256 * 1024  # a real secrets.json is a few KB; generous headroom

SESSION_COOKIE = "wiz_session"
SETUP_TOKEN = os.environ["SETUP_TOKEN"]  # set by entrypoint.sh; fail loudly if missing
TOKEN_TTL = int(os.environ.get("GFM_SETUP_TOKEN_TTL", "900"))
RUN_TIMEOUT = int(os.environ.get("GFM_SETUP_RUN_TIMEOUT_SECONDS", "900"))

_started_at = time.time()

# Nothing needs to be reachable without a session except the redeem step
# itself, which the gate below special-cases rather than allowlisting a
# path -- ?token=... can arrive on any path (the operator pastes the whole
# URL from the container logs).
PUBLIC_PATHS: set[str] = set()

app = FastAPI()


def _token_redeemable() -> bool:
    return time.time() - _started_at < TOKEN_TTL


def _cookie_ok(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    return auth.parse_session_token(token, SETUP_TOKEN)


def _set_session_cookie(response) -> None:
    token = auth.make_session_token(SETUP_TOKEN)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", path="/")


@app.middleware("http")
async def _gate(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    candidate = request.query_params.get("token")
    if candidate and _token_redeemable() and auth.token_matches(candidate, SETUP_TOKEN):
        redirect = RedirectResponse(request.url.path, status_code=302)
        _set_session_cookie(redirect)
        return redirect

    if _cookie_ok(request):
        return await call_next(request)

    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return JSONResponse(
        {"detail": "unauthorized -- open the URL with ?token=... from "
                    "'docker compose logs setup-wizard'"},
        status_code=401,
    )


@app.get("/api/auth-check")
async def auth_check(request: Request):
    # Called by nginx's auth_request for /vnc/ (see nginx.conf) -- nginx
    # forwards only the Cookie header for this subrequest, no query string,
    # so a bare ?token= redemption doesn't apply here; the operator must
    # already hold a session cookie by the time they reach the embedded
    # browser view.
    if _cookie_ok(request):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=401)


@app.get("/api/status")
async def get_status():
    snap = state.snapshot()
    snap["log"] = state.log_events()
    snap["run_timeout_seconds"] = RUN_TIMEOUT
    return snap


async def _run_flow_and_watch() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(RUN_FLOW_SCRIPT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def _read_output() -> None:
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            try:
                event = json.loads(raw_line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "stage" in event and "ok" in event:
                if event["ok"]:
                    state.finish("success")
                else:
                    state.finish("failed", error=event.get("error"))
            else:
                state.append_log(event.get("stage", "?"), event.get("msg", ""))

    reader_task = asyncio.create_task(_read_output())
    try:
        await asyncio.wait_for(proc.wait(), timeout=RUN_TIMEOUT)
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
        # Defensive sweep: create_driver() already kills stray chrome
        # processes at the *start* of its own next attempt, but doing it
        # here too keeps the container tidy in the meantime rather than
        # leaving an orphaned, still-rendering browser around.
        try:
            await asyncio.create_subprocess_exec(
                "pkill", "-f", "chromium",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            pass
        state.finish("timed_out")
    finally:
        reader_task.cancel()

    if state.snapshot()["phase"] == "running":
        # Process exited on its own without ever emitting a final
        # {"stage": "done"/"failed", ...} line (e.g. an unhandled crash
        # outside run_flow.py's own try/except) -- don't leave the state
        # machine stuck in "running" forever.
        state.finish("failed", error="wizard process exited unexpectedly")


@app.post("/api/start")
async def start_run():
    if not state.try_start():
        return JSONResponse({"detail": "a run is already in progress"}, status_code=409)
    asyncio.create_task(_run_flow_and_watch())
    return {"ok": True}


@app.post("/api/upload")
async def upload_secrets(file: UploadFile = File(...)):
    """Bring in an existing secrets.json instead of logging in through the
    embedded browser -- e.g. one already produced by a GoogleFindMyTools
    instance elsewhere. Runs through the *same* run/verify flow as a fresh
    login: Auth/token_cache.py's get_cached_value_or_set() is idempotent
    per key, so if the uploaded file already has aas_token/owner_key
    cached, get_aas_token()/get_owner_key() return immediately without
    ever opening a browser -- the flow falls straight through to the
    list_devices() smoke test, giving the same success/failure reporting
    the browser-login path already has, with no separate code path to
    keep in sync.
    """
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        return JSONResponse({"detail": "file too large"}, status_code=413)
    try:
        parsed = json.loads(data)
    except ValueError:
        return JSONResponse({"detail": "not valid JSON"}, status_code=400)
    if not isinstance(parsed, dict):
        return JSONResponse({"detail": "must be a JSON object"}, status_code=400)

    # Acquire the single-run lock *before* touching the file: if a
    # browser-login run is already in progress, try_start() fails and
    # nothing is written -- an uploaded file must never race a live run
    # for the same secrets.json.
    if not state.try_start():
        return JSONResponse({"detail": "a run is already in progress"}, status_code=409)

    SECRETS_PATH.write_text(json.dumps(parsed))
    asyncio.create_task(_run_flow_and_watch())
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
