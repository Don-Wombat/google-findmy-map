"""Fast, no-Docker checks for the setup wizard's own control app
(setup/app/main.py) and its subprocess script (setup/app/run_flow.py).

Two distinct things are tested here, deliberately kept separate:

1. main.py's HTTP-level orchestration (the token gate, the single-run
   lock, the timeout/kill watchdog) -- exercised through a FAKE
   RUN_FLOW_SCRIPT (a small throwaway script written per-test), never the
   real run_flow.py. This avoids needing Selenium/gpsoauth/a real vendor
   checkout just to test that main.py spawns, watches and times out a
   subprocess correctly.
2. run_flow.py's own stage-sequencing and error-handling, called directly
   (no subprocess) with the vendored Auth/SpotApi/NovaApi chain replaced by
   fake modules injected into sys.modules -- the same
   stub-before-import pattern test_api.py uses for `locations`.

What neither of these (nor anything else in this repo) can test: the
actual interactive Google login itself -- real password entry, 2FA,
CAPTCHA, Google's anti-automation heuristics against a Selenium-driven
Chromium. That is a manual, human verification step against a real
account, not something a passing test suite implies.
"""

import json
import os
import sys
import time
import types

import pytest

_SETUP_DIR = __import__("pathlib").Path(__file__).parents[2] / "setup"
SETUP_APP_DIR = str(_SETUP_DIR / "app")
SETUP_WEB_DIR = str(_SETUP_DIR / "web")

_MODS_TO_RESET = ("main", "auth", "state")


def _import_wizard_main(monkeypatch, *, token="test-token", ttl="900", timeout="900"):
    monkeypatch.syspath_prepend(SETUP_APP_DIR)
    for mod in _MODS_TO_RESET:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setenv("SETUP_TOKEN", token)
    monkeypatch.setenv("GFM_SETUP_TOKEN_TTL", ttl)
    monkeypatch.setenv("GFM_SETUP_RUN_TIMEOUT_SECONDS", timeout)
    monkeypatch.setenv("GFM_SETUP_WEB_DIR", SETUP_WEB_DIR)
    import main as wizard_main

    from fastapi.testclient import TestClient
    client = TestClient(wizard_main.app)
    client._wizard_main = wizard_main
    return client, wizard_main


def _write_fake_flow(tmp_path, events, *, exit_code=0, sleep_seconds=0, name="fake_flow.py"):
    """A throwaway stand-in for run_flow.py: sleeps, prints the given JSON
    events (one per line, exactly like the real script's protocol), exits."""
    script = tmp_path / name
    script.write_text(
        "import json, sys, time\n"
        f"time.sleep({sleep_seconds})\n"
        f"for line in {events!r}:\n"
        "    print(json.dumps(line)); sys.stdout.flush()\n"
        f"sys.exit({exit_code})\n"
    )
    return script


def _wait_until(predicate, *, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --- token gate --------------------------------------------------------

def test_gate_rejects_missing_token(monkeypatch):
    client, _ = _import_wizard_main(monkeypatch)
    with client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 401


def test_gate_rejects_wrong_token(monkeypatch):
    client, _ = _import_wizard_main(monkeypatch, token="right-token")
    with client:
        resp = client.get("/?token=wrong-token", follow_redirects=False)
        assert resp.status_code == 401
        assert "wiz_session" not in resp.cookies


def test_gate_accepts_valid_token_and_sets_cookie(monkeypatch):
    client, _ = _import_wizard_main(monkeypatch, token="right-token")
    with client:
        resp = client.get("/?token=right-token", follow_redirects=False)
        assert resp.status_code == 302
        assert "wiz_session" in resp.cookies

        # The redirect strips the query string so the token doesn't linger
        # in browser history / get sent onward as a referrer.
        assert resp.headers["location"] == "/"

        ok = client.get("/api/status")
        assert ok.status_code == 200


def test_token_not_redeemable_after_ttl(monkeypatch):
    client, wizard_main = _import_wizard_main(monkeypatch, token="right-token", ttl="900")
    with client:
        # Pretend the container started long ago.
        wizard_main._started_at = time.time() - 1000
        resp = client.get("/?token=right-token", follow_redirects=False)
        assert resp.status_code == 401


# --- single-run lock -----------------------------------------------------

def test_single_run_lock(monkeypatch, tmp_path):
    client, wizard_main = _import_wizard_main(monkeypatch, token="t")
    fake = _write_fake_flow(tmp_path, [{"ts": 0, "stage": "aas_token", "msg": "..."}],
                             sleep_seconds=1)
    monkeypatch.setattr(wizard_main, "RUN_FLOW_SCRIPT", fake)
    monkeypatch.setattr(wizard_main, "SETUP_TOKEN", "t")
    with client:
        client.cookies.set("wiz_session", wizard_main.auth.make_session_token("t"))
        first = client.post("/api/start")
        assert first.status_code == 200
        second = client.post("/api/start")
        assert second.status_code == 409


# --- happy path / timeout / failure, via a fake subprocess ---------------

def _authed_client(monkeypatch, **kw):
    client, wizard_main = _import_wizard_main(monkeypatch, **kw)
    client.__enter__()
    client.cookies.set("wiz_session", wizard_main.auth.make_session_token(
        kw.get("token", "test-token")))
    return client, wizard_main


def test_happy_path_reaches_success(monkeypatch, tmp_path):
    client, wizard_main = _authed_client(monkeypatch, token="t")
    try:
        fake = _write_fake_flow(tmp_path, [
            {"ts": 0, "stage": "aas_token", "msg": "..."},
            {"ts": 0, "stage": "owner_key", "msg": "..."},
            {"stage": "done", "ok": True},
        ])
        monkeypatch.setattr(wizard_main, "RUN_FLOW_SCRIPT", fake)

        resp = client.post("/api/start")
        assert resp.status_code == 200

        _wait_until(lambda: client.get("/api/status").json()["phase"] != "running")
        status = client.get("/api/status").json()
        assert status["phase"] == "success"
        stages = [e["stage"] for e in status["log"]]
        assert "aas_token" in stages and "owner_key" in stages
    finally:
        client.__exit__(None, None, None)


def test_failure_surfaces_short_error(monkeypatch, tmp_path):
    client, wizard_main = _authed_client(monkeypatch, token="t")
    try:
        fake = _write_fake_flow(tmp_path, [
            {"stage": "failed", "ok": False, "error": "RuntimeError: exchange failed"},
        ], exit_code=1)
        monkeypatch.setattr(wizard_main, "RUN_FLOW_SCRIPT", fake)

        client.post("/api/start")
        _wait_until(lambda: client.get("/api/status").json()["phase"] != "running")
        status = client.get("/api/status").json()
        assert status["phase"] == "failed"
        assert status["error"] == "RuntimeError: exchange failed"
    finally:
        client.__exit__(None, None, None)


def test_timeout_kills_the_run(monkeypatch, tmp_path):
    client, wizard_main = _authed_client(monkeypatch, token="t", timeout="1")
    try:
        fake = _write_fake_flow(tmp_path, [], sleep_seconds=30)
        monkeypatch.setattr(wizard_main, "RUN_FLOW_SCRIPT", fake)
        monkeypatch.setattr(wizard_main, "RUN_TIMEOUT", 1)

        client.post("/api/start")
        _wait_until(lambda: client.get("/api/status").json()["phase"] != "running",
                    timeout=10)
        status = client.get("/api/status").json()
        assert status["phase"] == "timed_out"
    finally:
        client.__exit__(None, None, None)


def test_crash_without_final_line_does_not_stay_running_forever(monkeypatch, tmp_path):
    client, wizard_main = _authed_client(monkeypatch, token="t")
    try:
        # Exits nonzero without ever printing a {"stage": ..., "ok": ...}
        # line -- simulates a crash outside run_flow.py's own try/except.
        fake = _write_fake_flow(tmp_path, [{"ts": 0, "stage": "aas_token", "msg": "x"}],
                                 exit_code=3)
        monkeypatch.setattr(wizard_main, "RUN_FLOW_SCRIPT", fake)

        client.post("/api/start")
        _wait_until(lambda: client.get("/api/status").json()["phase"] != "running")
        status = client.get("/api/status").json()
        assert status["phase"] == "failed"
    finally:
        client.__exit__(None, None, None)


# --- run_flow.py's own stage sequencing, vendor chain stubbed ------------

def _stub_module(monkeypatch, dotted_name, **attrs):
    """Register dotted_name (and any missing parent packages) in
    sys.modules as a fake module with the given attributes -- Python's
    `from a.b.c import x` needs every ancestor package importable too, so a
    bare `monkeypatch.setitem(sys.modules, dotted_name, fake)` alone isn't
    enough for a multi-level dotted path."""
    parts = dotted_name.split(".")
    for i in range(1, len(parts) + 1):
        partial = ".".join(parts[:i])
        if partial not in sys.modules:
            monkeypatch.setitem(sys.modules, partial, types.ModuleType(partial))
    mod = sys.modules[dotted_name]
    for k, v in attrs.items():
        monkeypatch.setattr(mod, k, v, raising=False)


def _import_run_flow(monkeypatch):
    monkeypatch.syspath_prepend(SETUP_APP_DIR)
    monkeypatch.delitem(sys.modules, "run_flow", raising=False)
    import run_flow
    return run_flow


def test_run_flow_happy_path_calls_chain_in_order(monkeypatch, capsys):
    calls = []
    _stub_module(monkeypatch, "Auth.aas_token_retrieval",
                 get_aas_token=lambda: calls.append("aas_token"))
    _stub_module(monkeypatch, "SpotApi.GetEidInfoForE2eeDevices.get_owner_key",
                 get_owner_key=lambda: calls.append("owner_key"))
    _stub_module(monkeypatch, "NovaApi.ListDevices.nbe_list_devices",
                 list_devices=lambda: calls.append("verify"))

    run_flow = _import_run_flow(monkeypatch)
    rc = run_flow.main()

    assert rc == 0
    assert calls == ["aas_token", "owner_key", "verify"]
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines[-1] == {"stage": "done", "ok": True}


def test_run_flow_patches_input_before_any_vendor_call(monkeypatch):
    """Both request_oauth_account_token_flow() and _retrieve_shared_key()
    open with a blocking input() call in the real vendored code (see the
    design spec) -- run_flow.main() must neutralise builtins.input before
    calling anything, or this would hang/crash in a container with no
    TTY."""
    def _boom_if_real_input():
        assert input() == "", "builtins.input was not patched to a no-op"

    _stub_module(monkeypatch, "Auth.aas_token_retrieval",
                 get_aas_token=_boom_if_real_input)
    _stub_module(monkeypatch, "SpotApi.GetEidInfoForE2eeDevices.get_owner_key",
                 get_owner_key=lambda: None)
    _stub_module(monkeypatch, "NovaApi.ListDevices.nbe_list_devices",
                 list_devices=lambda: None)

    run_flow = _import_run_flow(monkeypatch)
    assert run_flow.main() == 0


def test_run_flow_error_is_short_and_typed_not_a_raw_repr(monkeypatch, capsys):
    _stub_module(monkeypatch, "Auth.aas_token_retrieval",
                 get_aas_token=lambda: (_ for _ in ()).throw(
                     RuntimeError("token exchange failed: " + "x" * 500)))
    _stub_module(monkeypatch, "SpotApi.GetEidInfoForE2eeDevices.get_owner_key",
                 get_owner_key=lambda: None)
    _stub_module(monkeypatch, "NovaApi.ListDevices.nbe_list_devices",
                 list_devices=lambda: None)

    run_flow = _import_run_flow(monkeypatch)
    rc = run_flow.main()

    assert rc == 1
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    final = lines[-1]
    assert final["ok"] is False
    assert final["error"].startswith("RuntimeError:")
    assert len(final["error"]) < 250
