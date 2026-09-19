"""End-to-end checks for the HTTP API, with the heavy vendored
``locations`` module stubbed out."""

import json
import pathlib
import sys
import time
import types
from xml.etree import ElementTree

import pytest


def _build_client(tmp_path, monkeypatch, env=None):
    stub = types.ModuleType("locations")
    stub.poll_all_devices = lambda: []
    stub.start_sound = lambda device_id: True
    stub.stop_sound = lambda device_id: True
    monkeypatch.setitem(sys.modules, "locations", stub)
    monkeypatch.setenv("GFM_HISTORY_DB", str(tmp_path / "history.db"))
    monkeypatch.setenv("GFM_HISTORY_FILE", str(tmp_path / "history.json"))
    monkeypatch.setenv("GFM_WEB_DIR", str(tmp_path))
    monkeypatch.setenv("GFM_NOMINATIM_URL", "")   # no network in tests
    monkeypatch.setenv("GFM_LOGIN_DELAY_MS", "0")
    for name, content in (
        ("index.html", "<html>index</html>"),
        ("login.html", "<html>login</html>"),
        ("settings.html", "<html>settings</html>"),
        ("app.js", "// app"),
        ("app.css", "/* css */"),
        ("favicon.svg", "<svg></svg>"),
        ("favicon.ico", "fake-ico"),
    ):
        (tmp_path / name).write_text(content)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)

    for mod in ("main", "auth", "augment", "store", "colors", "visits",
                "geocode", "export"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    import main

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    c._main = main
    return c


@pytest.fixture
def client(tmp_path, monkeypatch):
    c = _build_client(tmp_path, monkeypatch)
    with c:
        yield c


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Build extra clients sharing this test's tmp DB (e.g. with different env)."""
    def _make(**env):
        return _build_client(tmp_path, monkeypatch, env)
    return _make


def _enable_auth(client, password="secret123", username="admin"):
    r = client.put("/api/settings/auth",
                   json={"enabled": True, "new_password": password, "username": username})
    assert r.status_code == 200
    return r


def _login(client, password, username="admin"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_history_endpoint_returns_points_in_the_window(client):
    store = client._main._store
    for ts in (1_000, 2_000, 3_000, 4_000):
        store.add("dev-x", {"latitude": 52.5, "longitude": 13.4, "time": ts, "accuracy": 5})

    resp = client.get("/api/history", params={"device": "dev-x", "start": 2_000, "end": 3_000})

    assert resp.status_code == 200
    body = resp.json()
    assert body["device"] == "dev-x"
    assert [p["time"] for p in body["points"]] == [2_000, 3_000]


def test_history_endpoint_defaults_to_the_last_seven_days(client):
    import time

    store = client._main._store
    now = int(time.time())
    store.add("dev-x", {"latitude": 1, "longitude": 2, "time": now - 3_600, "accuracy": 5})
    store.add("dev-x", {"latitude": 1, "longitude": 2, "time": now - 30 * 24 * 3600, "accuracy": 5})

    body = client.get("/api/history", params={"device": "dev-x"}).json()

    assert [p["time"] for p in body["points"]] == [now - 3_600]


def test_history_endpoint_unknown_device_returns_empty(client):
    body = client.get("/api/history", params={"device": "nope"}).json()
    assert body["points"] == [] and body["name"] is None


def test_locations_endpoint_still_responds(client):
    resp = client.get("/api/locations")
    assert resp.status_code == 200
    assert "devices" in resp.json()


def test_locations_endpoint_exposes_the_palette(client):
    body = client.get("/api/locations").json()
    assert isinstance(body["palette"], list) and body["palette"]


def _seed_one_device(main, name="iPhone", id="dev-1"):
    with main._state_lock:
        main._state["devices"] = main._augment_all([{
            "name": name, "id": id, "type": "geo",
            "latitude": 52.5, "longitude": 13.4, "time": 1000, "accuracy": 5,
        }])


def _device(client, id="dev-1"):
    return next(d for d in client.get("/api/locations").json()["devices"] if d["id"] == id)


def test_update_device_sets_name_and_colour(client):
    _seed_one_device(client._main)

    resp = client.put("/api/devices/dev-1", json={"name": "Backpack", "color": "#123456"})
    assert resp.status_code == 200

    dev = _device(client)
    assert dev["name"] == "Backpack"
    assert dev["color"] == "#123456"
    assert dev["default_name"] == "iPhone"


def test_update_device_persists_to_the_store(client):
    client.put("/api/devices/dev-9", json={"name": "X", "color": "#abcdef"})
    assert client._main._store.get_settings()["dev-9"] == {
        "name": "X", "color": "#abcdef", "group": None}


def test_clearing_settings_restores_the_defaults(client):
    _seed_one_device(client._main)
    client.put("/api/devices/dev-1", json={"name": "Backpack", "color": "#123456"})
    client.put("/api/devices/dev-1", json={"name": "", "color": ""})

    dev = _device(client)
    assert dev["name"] == "iPhone"
    assert dev["color"] != "#123456"


def test_update_device_rejects_a_non_hex_colour(client):
    resp = client.put("/api/devices/dev-1", json={"name": "X", "color": "red"})
    assert resp.status_code == 422


def test_mutating_endpoints_reject_cross_site_requests(client):
    assert client.post("/api/refresh", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.put("/api/devices/d", json={"name": "x"},
                      headers={"sec-fetch-site": "same-site"}).status_code == 403
    assert client.post("/api/devices/d/ring",
                       headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/devices/d/ring/stop",
                       headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_mutating_endpoints_allow_same_origin_and_non_browser(client):
    assert client.post("/api/refresh", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    assert client.post("/api/refresh").status_code == 200  # no header (curl/scripts)


def test_ring_device_starts_and_stops(client):
    assert client.post("/api/devices/dev-1/ring").status_code == 200
    assert client.post("/api/devices/dev-1/ring/stop").status_code == 200


def test_ring_device_surfaces_a_failure(client):
    client._main.locations.start_sound = lambda device_id: False
    resp = client.post("/api/devices/dev-1/ring")
    assert resp.status_code == 502


def test_stop_ring_surfaces_a_failure(client):
    client._main.locations.stop_sound = lambda device_id: False
    resp = client.post("/api/devices/dev-1/ring/stop")
    assert resp.status_code == 502


def test_devices_endpoint_lists_a_device_no_longer_in_the_live_poll(client):
    main = client._main
    _seed_one_device(main, name="iPhone", id="dev-1")   # persists last_known_name
    with main._state_lock:
        main._state["devices"] = []   # simulate: dev-1 dropped out of the live poll

    devices = client.get("/api/devices").json()["devices"]
    assert [d["id"] for d in devices] == ["dev-1"]
    assert devices[0]["name"] == "iPhone"
    assert devices[0]["last_seen"] == 1000


def test_devices_endpoint_prefers_a_manual_rename(client):
    main = client._main
    _seed_one_device(main, name="iPhone", id="dev-1")
    client.put("/api/devices/dev-1", json={"name": "Backpack", "color": ""})

    devices = client.get("/api/devices").json()["devices"]
    assert devices[0]["name"] == "Backpack"


def test_devices_endpoint_sorts_most_recently_seen_first(client):
    main = client._main
    _seed_one_device(main, name="Old", id="dev-old")
    main._store.add("dev-old", {"latitude": 1, "longitude": 1, "time": 1_000})
    main._store.add("dev-new", {"latitude": 1, "longitude": 1, "time": 5_000})
    main._store.set_last_seen_name("dev-new", "New")

    devices = client.get("/api/devices").json()["devices"]
    assert [d["id"] for d in devices] == ["dev-new", "dev-old"]


class TestDeviceGroups:
    def test_group_is_persisted_and_echoed(self, client):
        _seed_one_device(client._main, id="dev-1")
        r = client.put("/api/devices/dev-1",
                       json={"name": "", "color": "", "group": "Familie"})
        assert r.status_code == 200
        assert r.json()["settings"]["group"] == "Familie"
        assert _device(client)["group"] == "Familie"

    def test_group_is_trimmed_and_capped(self, client):
        _seed_one_device(client._main, id="dev-1")
        client.put("/api/devices/dev-1",
                   json={"name": "", "color": "", "group": "  " + "x" * 60 + "  "})
        assert len(_device(client)["group"]) == 40

    def test_group_appears_on_the_devices_endpoint(self, client):
        _seed_one_device(client._main, id="dev-1")
        client.put("/api/devices/dev-1", json={"name": "", "color": "", "group": "Fahrzeuge"})
        dev = next(d for d in client.get("/api/devices").json()["devices"] if d["id"] == "dev-1")
        assert dev["group"] == "Fahrzeuge"

    def test_no_group_is_null_everywhere(self, client):
        _seed_one_device(client._main, id="dev-1")
        assert _device(client)["group"] is None
        dev = next(d for d in client.get("/api/devices").json()["devices"] if d["id"] == "dev-1")
        assert dev["group"] is None


class TestDeleteDevice:
    def _stale_device(self, main, id="dev-old"):
        for t in (1_000, 2_000, 3_000):
            main._store.add(id, {"latitude": 52.5, "longitude": 13.4, "time": t, "accuracy": 5})
        main._store.set_setting(id, name="Retired Tag")
        # not in _state["devices"] -> stale

    def test_devices_endpoint_flags_live_vs_stale(self, client):
        main = client._main
        _seed_one_device(main, id="dev-live")     # goes into _state["devices"]
        self._stale_device(main, id="dev-old")
        by_id = {d["id"]: d for d in client.get("/api/devices").json()["devices"]}
        assert by_id["dev-live"]["live"] is True
        assert by_id["dev-old"]["live"] is False
        assert by_id["dev-old"]["point_count"] == 3

    def test_deletes_a_stale_device_and_its_history(self, client):
        main = client._main
        self._stale_device(main, id="dev-old")
        r = client.request("DELETE", "/api/devices/dev-old")
        assert r.status_code == 200
        assert r.json() == {"deleted": "dev-old", "points": 3}
        assert not any(d["id"] == "dev-old"
                       for d in client.get("/api/devices").json()["devices"])
        assert main._store.range("dev-old", 0, 9999) == []

    def test_refuses_to_delete_a_live_device(self, client):
        main = client._main
        _seed_one_device(main, id="dev-live", name="Phone")
        main._store.add("dev-live", {"latitude": 1, "longitude": 2, "time": 5, "accuracy": 1})
        r = client.request("DELETE", "/api/devices/dev-live")
        assert r.status_code == 409
        assert main._store.range("dev-live", 0, 9999) != []   # history untouched

    def test_delete_is_keyed_on_id_not_name_for_same_named_devices(self, client):
        """Two devices renamed to the same display name stay independently
        deletable -- the id in the path is the only thing that matters."""
        main = client._main
        for did in ("dev-a", "dev-b"):
            for t in (1, 2):
                main._store.add(did, {"latitude": 1, "longitude": 2, "time": t, "accuracy": 1})
            main._store.set_setting(did, name="Backpack")

        client.request("DELETE", "/api/devices/dev-a")

        remaining = client.get("/api/devices").json()["devices"]
        assert [d["id"] for d in remaining] == ["dev-b"]
        assert remaining[0]["name"] == "Backpack"      # dev-b, untouched

    def test_deleting_an_unknown_device_is_a_noop_200(self, client):
        r = client.request("DELETE", "/api/devices/never-existed")
        assert r.status_code == 200 and r.json()["points"] == 0

    def test_delete_is_blocked_cross_site(self, client):
        self._stale_device(client._main, id="dev-old")
        r = client.request("DELETE", "/api/devices/dev-old",
                           headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403

    def test_delete_holds_the_state_lock_across_the_db_delete(self, client):
        """Regression guard for the TOCTOU race this endpoint's 409 exists to
        prevent: the liveness check and the store delete must be atomic
        against the poll thread, which also needs _state_lock to repopulate
        _state["devices"]. Verified by probing whether the lock is already
        held at the moment _store.delete_device() runs."""
        main = client._main
        self._stale_device(main, id="dev-old")
        real_delete = main._store.delete_device
        was_locked = {}

        def spy(device_id):
            got_it = main._state_lock.acquire(blocking=False)
            if got_it:
                main._state_lock.release()
            was_locked["value"] = not got_it
            return real_delete(device_id)

        main._store.delete_device = spy
        try:
            r = client.request("DELETE", "/api/devices/dev-old")
        finally:
            main._store.delete_device = real_delete

        assert r.status_code == 200
        assert was_locked["value"] is True


class TestDeleteHistoryRange:
    def test_deletes_points_in_the_inclusive_range(self, client):
        main = client._main
        for t in (100, 200, 300, 400):
            main._store.add("dev-1", {"latitude": 1, "longitude": 2, "time": t, "accuracy": 1})
        r = client.request("DELETE", "/api/history?device=dev-1&start=200&end=300")
        assert r.status_code == 200
        assert r.json() == {"device": "dev-1", "start": 200, "end": 300, "points": 2}
        assert [p["time"] for p in main._store.range("dev-1", 0, 1000)] == [100, 400]

    def test_rejects_end_before_start_with_422(self, client):
        r = client.request("DELETE", "/api/history?device=dev-1&start=300&end=200")
        assert r.status_code == 422

    def test_missing_start_or_end_is_422(self, client):
        assert client.request("DELETE", "/api/history?device=dev-1&start=1").status_code == 422
        assert client.request("DELETE", "/api/history?device=dev-1&end=1").status_code == 422

    def test_deleting_an_unknown_device_range_is_a_noop_200(self, client):
        r = client.request("DELETE", "/api/history?device=never-existed&start=0&end=100")
        assert r.status_code == 200 and r.json()["points"] == 0

    def test_delete_is_blocked_cross_site(self, client):
        r = client.request("DELETE", "/api/history?device=dev-1&start=0&end=100",
                           headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403

    def test_does_not_touch_device_settings(self, client):
        main = client._main
        main._store.add("dev-1", {"latitude": 1, "longitude": 2, "time": 100, "accuracy": 1})
        main._store.set_setting("dev-1", name="Kept")
        client.request("DELETE", "/api/history?device=dev-1&start=0&end=1000")
        assert main._store.get_settings()["dev-1"]["name"] == "Kept"

    def test_does_not_resurrect_the_live_devices_latest_fix(self, client):
        """Regression guard for the augment_device()/store.add() resurrection
        bug: naively re-augmenting like update_device() does would undo the
        delete for this one point."""
        main = client._main
        _seed_one_device(main, id="dev-1")   # caches+persists a fix at time=1000
        r = client.request("DELETE", "/api/history?device=dev-1&start=1000&end=1000")
        assert r.status_code == 200 and r.json()["points"] == 1
        assert main._store.range("dev-1", 0, 9999) == []
        assert _device(client, id="dev-1")["history"] == []


class TestResetDeviceHistoryEndpoint:
    def test_removes_history_but_keeps_settings(self, client):
        main = client._main
        _seed_one_device(main, id="dev-1", name="Phone")
        main._store.set_setting("dev-1", name="Renamed", color="#112233", group="G")
        r = client.request("DELETE", "/api/devices/dev-1/history")
        assert r.status_code == 200 and r.json() == {"device": "dev-1", "points": 1}
        assert main._store.range("dev-1", 0, 9999) == []
        assert main._store.get_settings()["dev-1"] == {
            "name": "Renamed", "color": "#112233", "group": "G",
        }

    def test_can_reset_a_live_device_no_409(self, client):
        _seed_one_device(client._main, id="dev-live")
        r = client.request("DELETE", "/api/devices/dev-live/history")
        assert r.status_code == 200

    def test_reset_of_a_live_device_does_not_resurrect_its_latest_fix(self, client):
        main = client._main
        _seed_one_device(main, id="dev-1")
        client.request("DELETE", "/api/devices/dev-1/history")
        assert main._store.range("dev-1", 0, 9999) == []
        assert _device(client, id="dev-1")["history"] == []

    def test_resetting_an_unknown_device_is_a_noop_200(self, client):
        r = client.request("DELETE", "/api/devices/never-existed/history")
        assert r.status_code == 200 and r.json()["points"] == 0

    def test_reset_is_blocked_cross_site(self, client):
        r = client.request("DELETE", "/api/devices/dev-1/history",
                           headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403


def test_semantic_location_does_not_clobber_the_device_name(client):
    """Regression test for the locations.py bug this feature fixed: merging
    a semantic-only fix into the device entry must not overwrite its name."""
    with client._main._state_lock:
        client._main._state["devices"] = client._main._augment_all([{
            "name": "iPhone", "id": "dev-2", "type": "semantic",
            "place_name": "Home", "time": 1000,
        }])

    dev = _device(client, id="dev-2")
    assert dev["name"] == "iPhone"
    assert dev["place_name"] == "Home"


def test_history_retention_prunes_only_when_configured_and_due(make_client):
    c = make_client(GFM_HISTORY_RETENTION_DAYS="1")
    with c:
        main = c._main
        now = int(time.time())
        main._store.add("dev-1", {"latitude": 1, "longitude": 1, "time": now - 2 * 86400})
        main._store.add("dev-1", {"latitude": 1, "longitude": 1, "time": now - 3600})

        # The background poll loop (stubbed to return no devices) already
        # ran its own _maybe_prune_history() once by now and may have
        # consumed the daily gate -- reset it so this call is deterministically due.
        main._last_prune["at"] = 0.0
        main._maybe_prune_history()
        points = main._store.range("dev-1", 0, now + 1)
        assert len(points) == 1 and points[0]["time"] == now - 3600

        # A second call right away is not due yet -- add an old point and
        # confirm it survives until the next scheduled prune.
        main._store.add("dev-1", {"latitude": 1, "longitude": 1, "time": now - 5 * 86400})
        main._maybe_prune_history()
        assert len(main._store.range("dev-1", 0, now + 1)) == 2


def test_history_retention_disabled_by_default(client):
    main = client._main
    assert main.HISTORY_RETENTION_DAYS == 0
    now = int(time.time())
    main._store.add("dev-1", {"latitude": 1, "longitude": 1, "time": now - 3650 * 86400})
    main._maybe_prune_history()
    assert len(main._store.range("dev-1", 0, now + 1)) == 1


def _raiser(exc):
    def _f():
        raise exc
    return _f


class TestPollAlert:
    def test_alert_only_after_the_threshold_of_consecutive_failures(self, client):
        main = client._main
        main.locations.poll_all_devices = _raiser(RuntimeError("token expired"))
        main._state["consecutive_failures"] = 0

        main._run_poll_cycle()
        main._run_poll_cycle()
        assert client.get("/api/locations").json()["poll_alert"] is False
        assert client.get("/api/health").json()["poll_alert"] is False

        main._run_poll_cycle()  # 3rd in a row
        body = client.get("/api/locations").json()
        assert body["poll_alert"] is True
        assert body["consecutive_failures"] == 3
        assert "token expired" in body["last_error"]
        assert body["poll_interval_seconds"] == main.POLL_INTERVAL_SECONDS

        health = client.get("/api/health").json()
        assert health == {
            "ok": False, "poll_alert": True, "poll_stale": False,
            "last_poll": main._state["last_poll"], "consecutive_failures": 3,
            "poll_interval_seconds": main.POLL_INTERVAL_SECONDS,
        }
        assert "last_error" not in health   # never on the public probe

    def test_a_good_cycle_clears_the_alert(self, client):
        main = client._main
        main.locations.poll_all_devices = _raiser(RuntimeError("boom"))
        main._state["consecutive_failures"] = 0
        for _ in range(3):
            main._run_poll_cycle()
        assert client.get("/api/health").json()["poll_alert"] is True

        main.locations.poll_all_devices = lambda: []   # empty account = healthy
        main._run_poll_cycle()
        body = client.get("/api/locations").json()
        assert body["poll_alert"] is False
        assert body["consecutive_failures"] == 0
        assert body["last_error"] is None

    def test_every_device_errored_counts_as_a_failure_without_advancing_last_poll(self, client):
        main = client._main
        main.locations.poll_all_devices = lambda: [
            {"id": "d1", "name": "A", "error": "fetch_failed"},
            {"id": "d2", "name": "B", "error": "no_response"},
        ]
        main._state["consecutive_failures"] = 0
        before = main._state["last_poll"]
        for _ in range(3):
            main._run_poll_cycle()
        body = client.get("/api/locations").json()
        assert body["poll_alert"] is True
        assert body["last_error"] == "every device reported an error"
        assert main._state["last_poll"] == before

    def test_a_partial_failure_does_alert(self, client):
        """A single healthy device must not mask errors on the rest of the
        fleet (e.g. 9 of 10 tokens expired but one keeps working)."""
        main = client._main
        main.locations.poll_all_devices = lambda: [
            {"id": "d1", "name": "A", "type": "geo",
             "latitude": 1, "longitude": 2, "time": 5, "accuracy": 3},
            {"id": "d2", "name": "B", "error": "no_response"},
        ]
        main._state["consecutive_failures"] = 0
        for _ in range(5):
            main._run_poll_cycle()
        body = client.get("/api/locations").json()
        assert body["poll_alert"] is True
        assert body["consecutive_failures"] == 5
        assert body["last_error"] == "1 of 2 device(s) reported an error"

    def test_partial_failure_message_counts_the_errored_devices(self, client):
        main = client._main
        main.locations.poll_all_devices = lambda: [
            {"id": "d1", "name": "A", "type": "geo",
             "latitude": 1, "longitude": 2, "time": 5, "accuracy": 3},
            {"id": "d2", "name": "B", "error": "no_response"},
            {"id": "d3", "name": "C", "error": "no_response"},
        ]
        main._state["consecutive_failures"] = 0
        main._run_poll_cycle()
        assert client.get("/api/locations").json()["last_error"] == \
            "2 of 3 device(s) reported an error"

    def test_systemexit_from_the_vendored_lib_is_caught(self, client):
        main = client._main
        main.locations.poll_all_devices = _raiser(SystemExit(1))
        main._state["consecutive_failures"] = 0
        assert main._run_poll_cycle() is False   # no SystemExit escapes
        assert main._state["consecutive_failures"] == 1
        assert main._state["last_error"]

    def test_threshold_is_configurable(self, make_client):
        c = make_client(GFM_POLL_ALERT_AFTER="1")
        with c:
            main = c._main
            main.locations.poll_all_devices = _raiser(RuntimeError("x"))
            main._state["consecutive_failures"] = 0
            main._run_poll_cycle()
            assert c.get("/api/health").json()["poll_alert"] is True

    def test_zero_threshold_disables_the_alert(self, make_client):
        c = make_client(GFM_POLL_ALERT_AFTER="0")
        with c:
            main = c._main
            main.locations.poll_all_devices = _raiser(RuntimeError("x"))
            main._state["consecutive_failures"] = 0
            for _ in range(10):
                main._run_poll_cycle()
            assert c.get("/api/health").json()["poll_alert"] is False

    def test_stale_last_poll_alerts_even_without_a_failure_streak(self, client):
        """A hung poll thread never bumps the failure counter -- last_poll
        just stops advancing. The stale check catches that."""
        main = client._main
        main._state["consecutive_failures"] = 0
        main._state["last_poll"] = int(time.time()) - 24 * 3600   # a day ago
        health = client.get("/api/health").json()
        assert health["poll_alert"] is False
        assert health["poll_stale"] is True
        assert health["ok"] is False
        assert client.get("/api/locations").json()["poll_stale"] is True

    def test_never_having_polled_is_not_flagged_stale(self, client):
        main = client._main
        main._state["consecutive_failures"] = 0
        main._state["last_poll"] = None
        assert client.get("/api/health").json()["poll_stale"] is False


def test_read_endpoints_are_not_blocked_cross_site(client):
    assert client.get("/api/locations", headers={"sec-fetch-site": "cross-site"}).status_code == 200


def _seed_stay(store, device="dev-1", lat=52.52, lon=13.405, start=1_000_000, count=16):
    for i in range(count):
        store.add(device, {"latitude": lat + (i % 3) * 1e-5, "longitude": lon,
                           "time": start + i * 120, "accuracy": 10})


def test_visits_endpoint_detects_a_stay(client):
    _seed_stay(client._main._store)
    body = client.get("/api/visits", params={"device": "dev-1",
                                             "start": 999_000, "end": 1_100_000}).json()
    assert body["geocoding"] is False
    assert len(body["visits"]) == 1
    v = body["visits"][0]
    assert v["start"] == 1_000_000 and v["end"] == 1_000_000 + 15 * 120
    assert v["label"] is None


def test_visits_endpoint_surfaces_cached_addresses(client):
    main = client._main
    _seed_stay(main._store)
    found = __import__("visits").detect_visits(
        main._store.range("dev-1", 0, 2_000_000), main.VISIT_RADIUS_M, main.VISIT_MIN_SECONDS
    )
    main._store.geocode_put(found[0]["lat"], found[0]["lon"], "Home", "Home, Berlin")

    body = client.get("/api/visits", params={"device": "dev-1",
                                             "start": 0, "end": 2_000_000}).json()
    assert body["visits"][0]["label"] == "Home"
    assert body["visits"][0]["address"] == "Home, Berlin"


def test_visits_endpoint_queues_uncached_coords_when_geocoding_is_on(client):
    from geocode import Geocoder

    main = client._main
    main._geocoder = Geocoder(main._store, base_url="https://example.test",
                              http_get=lambda url: {"display_name": "Place, City", "address": {}})
    _seed_stay(main._store)

    body = client.get("/api/visits", params={"device": "dev-1",
                                             "start": 0, "end": 2_000_000}).json()
    assert body["geocoding"] is True
    assert body["pending"] == 1
    assert main._geocoder.pending_count == 1

    main._geocoder._drain_one()
    body2 = client.get("/api/visits", params={"device": "dev-1",
                                              "start": 0, "end": 2_000_000}).json()
    assert body2["visits"][0]["label"] == "Place"
    assert body2["pending"] == 0


class TestExport:
    def _seed(self, store):
        for i in range(4):
            store.add("dev-1", {"latitude": 52.5 + i * 1e-4, "longitude": 13.4,
                                "time": 1_700_000_000 + i * 60, "accuracy": 5})

    def test_history_gpx_download(self, client):
        self._seed(client._main._store)
        client.put("/api/devices/dev-1", json={"name": "Peter's Phone"})
        r = client.get("/api/export/history",
                       params={"device": "dev-1", "format": "gpx"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/gpx+xml")
        assert r.headers["content-disposition"] == \
            'attachment; filename="Peter-s-Phone-history.gpx"'
        root = ElementTree.fromstring(r.text)
        ns = "{http://www.topografix.com/GPX/1/1}"
        assert len(root.findall(f"{ns}trk/{ns}trkseg/{ns}trkpt")) == 4

    def test_history_csv_and_geojson_content_types(self, client):
        self._seed(client._main._store)
        csv_r = client.get("/api/export/history",
                           params={"device": "dev-1", "format": "csv"})
        assert csv_r.headers["content-type"].startswith("text/csv")
        assert csv_r.text.splitlines()[0].startswith("time_iso,")

        gj = client.get("/api/export/history",
                        params={"device": "dev-1", "format": "geojson"})
        assert gj.headers["content-type"] == "application/geo+json"
        assert json.loads(gj.text)["features"][0]["geometry"]["type"] == "LineString"

    def test_range_is_applied_and_reflected_in_the_filename(self, client):
        self._seed(client._main._store)
        r = client.get("/api/export/history", params={
            "device": "dev-1", "format": "csv",
            "start": 1_700_000_030, "end": 1_700_000_150,
        })
        rows = r.text.splitlines()
        assert len(rows) == 1 + 2   # header + the two points inside the window
        assert r.headers["content-disposition"] == \
            'attachment; filename="dev-1-history-1700000030-1700000150.csv"'

    def test_start_only_is_applied_and_reflected_in_the_filename(self, client):
        """Regression test: only ``start`` set (``end`` omitted) used to
        crash with a 500 (int(None) in the filename-span calculation)."""
        self._seed(client._main._store)
        r = client.get("/api/export/history", params={
            "device": "dev-1", "format": "csv", "start": 1_700_000_090,
        })
        assert r.status_code == 200
        rows = r.text.splitlines()
        assert len(rows) == 1 + 2   # header + the two points from +120s/+180s
        assert r.headers["content-disposition"].startswith(
            'attachment; filename="dev-1-history-1700000090-')

    def test_end_only_is_applied_and_reflected_in_the_filename(self, client):
        """Regression test: only ``end`` set (``start`` omitted) used to
        crash with a 500 (int(None) in the filename-span calculation)."""
        self._seed(client._main._store)
        r = client.get("/api/export/history", params={
            "device": "dev-1", "format": "csv", "end": 1_700_000_090,
        })
        assert r.status_code == 200
        rows = r.text.splitlines()
        assert len(rows) == 1 + 2   # header + the two points from +0s/+60s
        assert r.headers["content-disposition"] == \
            'attachment; filename="dev-1-history-0-1700000090.csv"'

    def test_no_range_exports_the_full_history(self, client):
        self._seed(client._main._store)
        r = client.get("/api/export/history",
                       params={"device": "dev-1", "format": "csv"})
        assert len(r.text.splitlines()) == 1 + 4
        assert "-history.csv" in r.headers["content-disposition"]

    def test_bad_format_is_422(self, client):
        r = client.get("/api/export/history",
                       params={"device": "dev-1", "format": "kml"})
        assert r.status_code == 422

    def test_unknown_device_is_an_empty_file_not_404(self, client):
        r = client.get("/api/export/history",
                       params={"device": "ghost", "format": "csv"})
        assert r.status_code == 200
        assert r.text.splitlines() == ["time_iso,time_unix,latitude,longitude,accuracy"]

    def test_visits_export_uses_only_cached_labels(self, client):
        main = client._main
        _seed_stay(main._store)
        found = __import__("visits").detect_visits(
            main._store.range("dev-1", 0, 2_000_000),
            main.VISIT_RADIUS_M, main.VISIT_MIN_SECONDS,
        )
        main._store.geocode_put(found[0]["lat"], found[0]["lon"], "Home", "Home, Berlin")

        r = client.get("/api/export/visits",
                       params={"device": "dev-1", "format": "geojson"})
        assert r.status_code == 200
        feats = json.loads(r.text)["features"]
        assert feats[0]["properties"]["label"] == "Home"
        assert main._geocoder.pending_count == 0   # never enqueued a lookup

    def test_visits_export_gpx_and_csv(self, client):
        _seed_stay(client._main._store)
        gpx = client.get("/api/export/visits",
                         params={"device": "dev-1", "format": "gpx"})
        assert gpx.headers["content-type"].startswith("application/gpx+xml")
        ElementTree.fromstring(gpx.text)   # parses
        csv_r = client.get("/api/export/visits",
                           params={"device": "dev-1", "format": "csv"})
        assert csv_r.text.splitlines()[0].startswith("start_iso,")

    def test_export_endpoints_are_not_blocked_cross_site(self, client):
        self._seed(client._main._store)
        r = client.get("/api/export/history",
                       params={"device": "dev-1", "format": "csv"},
                       headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 200   # GET, read-only -- same as /api/history


class TestStaticPages:
    def test_login_page_is_served(self, client):
        assert client.get("/login.html").status_code == 200

    def test_settings_page_is_served(self, client):
        assert client.get("/settings.html").status_code == 200

    def test_settings_page_requires_auth_when_enabled(self, client):
        _enable_auth(client)
        client.cookies.clear()
        r = client.get("/settings.html", follow_redirects=False)
        assert r.status_code == 302 and "/login.html" in r.headers["location"]


class TestAuthGate:
    def test_auth_is_off_by_default(self, client):
        assert client.get("/api/locations").status_code == 200
        assert client.get("/", follow_redirects=False).status_code == 200

    def test_enabling_auth_gates_api_and_pages(self, client):
        _enable_auth(client)                       # PUT response sets the cookie
        assert client.get("/api/locations").status_code == 200
        client.cookies.clear()
        assert client.get("/api/locations").status_code == 401
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302 and "/login.html" in r.headers["location"]

    def test_allowlisted_paths_reachable_while_locked(self, client):
        _enable_auth(client)
        client.cookies.clear()
        assert client.get("/login.html").status_code == 200
        assert client.get("/app.js").status_code == 200
        assert client.get("/api/auth/status").status_code == 200
        # The login page itself references these; they must not redirect to
        # login.html while locked out, or the tab icon (and any browser's
        # automatic /favicon.ico probe) silently breaks behind the gate.
        assert client.get("/favicon.svg").status_code == 200
        assert client.get("/favicon.ico").status_code == 200

    def test_auth_disable_allows_resetting_a_forgotten_password(self, make_client):
        """The documented recovery flow (README / SECURITY.md), end to end."""
        c1 = make_client()
        with c1:
            _enable_auth(c1, "orig-pass-1")

        c2 = make_client(GFM_AUTH_DISABLE="1")
        with c2:
            c2.cookies.clear()
            assert c2.get("/api/auth/status").json() == {
                "auth_enabled": False, "authenticated": False}
            # No current_password: it is the one the operator forgot.
            r = c2.put("/api/settings/auth",
                       json={"enabled": True, "new_password": "brandnew-1"})
            assert r.status_code == 200

        c3 = make_client(GFM_AUTH_DISABLE="")   # escape hatch removed again
        with c3:
            c3.cookies.clear()
            assert _login(c3, "brandnew-1").status_code == 200
            c3.cookies.clear()
            assert _login(c3, "orig-pass-1").status_code == 401

    def test_auth_disable_env_overrides_the_db(self, make_client):
        c1 = make_client()
        with c1:
            _enable_auth(c1)
        c2 = make_client(GFM_AUTH_DISABLE="1")
        with c2:
            c2.cookies.clear()
            assert c2.get("/api/locations").status_code == 200


class TestAuthEndpoints:
    def test_status_reports_disabled(self, client):
        body = client.get("/api/auth/status").json()
        assert body == {"auth_enabled": False, "authenticated": False}

    def test_status_reports_enabled_and_authenticated(self, client):
        _enable_auth(client)
        body = client.get("/api/auth/status").json()
        assert body["auth_enabled"] is True and body["authenticated"] is True
        # The username is only ever revealed to a caller who already holds a
        # valid session -- never to the anonymous request below.
        assert body["username"] == "admin"
        client.cookies.clear()
        body2 = client.get("/api/auth/status").json()
        assert body2["auth_enabled"] is True and body2["authenticated"] is False
        assert "username" not in body2

    def test_login_wrong_then_right(self, client):
        _enable_auth(client, "hunter2222")
        client.cookies.clear()
        assert _login(client, "nope").status_code == 401
        r = _login(client, "hunter2222")
        assert r.status_code == 200
        assert client.get("/api/locations").status_code == 200

    def test_login_wrong_username_is_rejected(self, client):
        _enable_auth(client, "hunter2222", username="admin")
        client.cookies.clear()
        assert _login(client, "hunter2222", username="someone-else").status_code == 401
        assert _login(client, "hunter2222", username="admin").status_code == 200

    def test_login_without_stored_username_accepts_any_username(self, client):
        """Backward compat: an install that enabled auth before this
        credential existed has a password but no stored username. Login
        must keep working there regardless of what (or whether) a username
        is submitted, so the upgrade never locks the operator out."""
        _enable_auth(client, "hunter2222", username="admin")
        store = client._main._store
        store.set_config("username", "")   # simulate a pre-upgrade install
        client.cookies.clear()
        assert _login(client, "hunter2222", username="").status_code == 200
        client.cookies.clear()
        assert _login(client, "hunter2222", username="whatever").status_code == 200

    def test_login_when_disabled_is_400(self, client):
        assert _login(client, "whatever").status_code == 400

    def test_login_throttles_after_free_attempts(self, client):
        _enable_auth(client)
        client.cookies.clear()
        for _ in range(5):
            assert _login(client, "wrong").status_code == 401
        r = _login(client, "wrong")
        assert r.status_code == 429
        assert r.json()["retry_after"] > 0
        assert r.headers["retry-after"]

    def test_logout_clears_the_cookie(self, client):
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        set_cookie = r.headers.get("set-cookie", "").lower()
        assert "fmm_session=" in set_cookie
        assert 'max-age=0' in set_cookie or '01 jan 1970' in set_cookie

    def test_secure_flag_follows_forwarded_proto(self, client):
        _enable_auth(client)
        client.cookies.clear()
        r = _login(client, "secret123")
        assert "secure" not in r.headers["set-cookie"].lower()
        client.cookies.clear()
        r2 = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"},
                         headers={"x-forwarded-proto": "https"})
        assert "secure" in r2.headers["set-cookie"].lower()

    def test_login_blocks_cross_site(self, client):
        r = client.post("/api/auth/login", json={"password": "secret123"},
                        headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403


class TestAuthSettings:
    def test_enable_requires_a_password(self, client):
        r = client.put("/api/settings/auth", json={"enabled": True})
        assert r.status_code == 422

    def test_enable_rejects_a_short_password(self, client):
        r = client.put("/api/settings/auth", json={"enabled": True, "new_password": "short"})
        assert r.status_code == 422

    def test_enable_requires_a_username(self, client):
        r = client.put("/api/settings/auth",
                       json={"enabled": True, "new_password": "secret123"})
        assert r.status_code == 422

    def test_enable_rejects_a_blank_username(self, client):
        r = client.put("/api/settings/auth",
                       json={"enabled": True, "new_password": "secret123", "username": "   "})
        assert r.status_code == 422

    def test_enable_sets_the_cookie_and_persists(self, client):
        r = client.put("/api/settings/auth",
                       json={"enabled": True, "new_password": "secret123", "username": "admin"})
        assert r.status_code == 200 and r.json() == {"auth_enabled": True}
        assert client.get("/api/locations").status_code == 200   # cookie from the PUT

    def test_change_password_needs_current_and_rotates_sessions(self, client):
        _enable_auth(client, "secret123")
        old = dict(client.cookies)
        bad = client.put("/api/settings/auth", json={
            "enabled": True, "current_password": "WRONG", "new_password": "newpass123"})
        assert bad.status_code == 403
        good = client.put("/api/settings/auth", json={
            "enabled": True, "current_password": "secret123", "new_password": "newpass123"})
        assert good.status_code == 200
        assert client.get("/api/locations").status_code == 200   # re-issued cookie
        client.cookies.clear()
        client.cookies.update(old)
        assert client.get("/api/locations").status_code == 401   # old session invalid

    def test_change_username_needs_current_and_rotates_sessions(self, client):
        _enable_auth(client, "secret123", username="admin")
        old = dict(client.cookies)
        bad = client.put("/api/settings/auth", json={
            "enabled": True, "current_password": "WRONG", "username": "new-admin"})
        assert bad.status_code == 403
        good = client.put("/api/settings/auth", json={
            "enabled": True, "current_password": "secret123", "username": "new-admin"})
        assert good.status_code == 200
        assert client.get("/api/locations").status_code == 200   # re-issued cookie
        client.cookies.clear()
        client.cookies.update(old)
        assert client.get("/api/locations").status_code == 401   # old session invalid
        client.cookies.clear()
        assert _login(client, "secret123", username="admin").status_code == 401
        assert _login(client, "secret123", username="new-admin").status_code == 200

    def test_resending_the_same_username_is_a_noop(self, client):
        _enable_auth(client, "secret123", username="admin")
        store = client._main._store
        version = store.get_config("cred_version")
        r = client.put("/api/settings/auth", json={"enabled": True, "username": "admin"})
        assert r.status_code == 200
        assert store.get_config("cred_version") == version
        assert "set-cookie" not in {k.lower() for k in r.headers}

    def test_disable_needs_current_password(self, client):
        _enable_auth(client, "secret123")
        assert client.put("/api/settings/auth", json={
            "enabled": False, "current_password": "WRONG"}).status_code == 403
        assert client.put("/api/settings/auth", json={
            "enabled": False, "current_password": "secret123"}).status_code == 200
        client.cookies.clear()
        assert client.get("/api/locations").status_code == 200

    def test_reenable_with_stored_hash_needs_no_new_password(self, client):
        _enable_auth(client, "secret123")
        client.put("/api/settings/auth", json={"enabled": False, "current_password": "secret123"})
        r = client.put("/api/settings/auth", json={"enabled": True})
        assert r.status_code == 200 and r.json() == {"auth_enabled": True}
        client.cookies.clear()
        assert _login(client, "secret123").status_code == 200

    def test_resaving_enabled_without_a_new_password_changes_nothing(self, client):
        _enable_auth(client, "secret123")
        store = client._main._store
        version = store.get_config("cred_version")
        old = dict(client.cookies)

        r = client.put("/api/settings/auth", json={"enabled": True})
        assert r.status_code == 200 and r.json() == {"auth_enabled": True}
        assert "set-cookie" not in {k.lower() for k in r.headers}
        assert store.get_config("cred_version") == version

        client.cookies.clear()
        client.cookies.update(old)
        assert client.get("/api/locations").status_code == 200   # session survived

    def test_saving_disabled_while_already_off_is_a_noop(self, client):
        store = client._main._store
        before = store.get_config_many(["auth_enabled", "password", "cred_version"])
        r = client.put("/api/settings/auth", json={"enabled": False})
        assert r.status_code == 200 and r.json() == {"auth_enabled": False}
        assert "set-cookie" not in {k.lower() for k in r.headers}
        assert store.get_config_many(["auth_enabled", "password", "cred_version"]) == before
        assert client.get("/api/locations").status_code == 200

    def test_blocks_cross_site(self, client):
        r = client.put("/api/settings/auth",
                       json={"enabled": True, "new_password": "secret123"},
                       headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403


def test_real_index_header_has_gear_and_no_toggles():
    html = (pathlib.Path(__file__).parents[2] / "web" / "index.html").read_text()
    assert 'href="settings.html"' in html
    assert 'id="theme-toggle"' not in html
    assert 'id="lang-toggle"' not in html


def test_real_timeline_header_has_gear_and_no_toggles():
    html = (pathlib.Path(__file__).parents[2] / "web" / "timeline.html").read_text()
    assert 'href="settings.html"' in html
    assert 'id="theme-toggle"' not in html
    assert 'id="lang-toggle"' not in html


def test_real_favicon_files_exist_and_are_referenced():
    web_dir = pathlib.Path(__file__).parents[2] / "web"
    svg = (web_dir / "favicon.svg").read_text()
    assert "<svg" in svg
    ico = (web_dir / "favicon.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00"   # ICO magic
    for page in ("index.html", "timeline.html", "login.html", "settings.html"):
        html = (web_dir / page).read_text()
        assert 'href="favicon.svg"' in html
        assert 'href="favicon.ico"' in html


def test_real_index_has_a_ring_button_wired_to_the_api():
    html = (pathlib.Path(__file__).parents[2] / "web" / "index.html").read_text()
    assert 'device-ring-btn' in html
    assert '/ring' in html


def test_real_ring_button_gives_instant_no_popup_feedback():
    """The pending/failed states must be set synchronously on click -- i.e.
    before the fetch is awaited -- and use no alert()/confirm()/toast."""
    html = (pathlib.Path(__file__).parents[2] / "web" / "index.html").read_text()
    assert "classList.add('pending')" in html
    assert "alert(" not in html and "confirm(" not in html
    css = (pathlib.Path(__file__).parents[2] / "web" / "app.css").read_text()
    assert ".device-ring-btn.pending" in css
    assert ".device-ring-btn.ring-failed" in css


def test_real_timeline_lists_devices_from_the_devices_endpoint():
    html = (pathlib.Path(__file__).parents[2] / "web" / "timeline.html").read_text()
    assert "fetch('/api/devices')" in html


def test_real_pages_carry_the_poll_alert_banner():
    web_dir = pathlib.Path(__file__).parents[2] / "web"
    for page in ("index.html", "timeline.html"):
        assert 'id="poll-alert"' in (web_dir / page).read_text()
    assert ".poll-alert" in (web_dir / "app.css").read_text()
    assert "renderPollAlert" in (web_dir / "app.js").read_text()


def test_real_export_links_are_wired_in_the_frontend():
    web_dir = pathlib.Path(__file__).parents[2] / "web"
    timeline = (web_dir / "timeline.html").read_text()
    assert 'id="export"' in timeline
    assert "/api/export/" in timeline
    assert 'id="exp-track-gpx"' in timeline and 'id="exp-visits-csv"' in timeline
    settings = (web_dir / "settings.html").read_text()
    assert 'id="export-device"' in settings
    assert "/api/export/history" in settings


def test_real_settings_has_a_stale_device_manager():
    settings = (pathlib.Path(__file__).parents[2] / "web" / "settings.html").read_text()
    assert 'id="stale-devices"' in settings
    assert "method: 'DELETE'" in settings
    assert "d.live" in settings                 # only stale devices are listed
    assert "dev_confirm_q" in settings          # inline confirm step, no popup
    assert "alert(" not in settings and "confirm(" not in settings
    app_js = (pathlib.Path(__file__).parents[2] / "web" / "app.js").read_text()
    assert "s_devices:" in app_js and "Alte Geräte" in app_js


def test_real_device_grouping_is_wired_in_the_frontend():
    web_dir = pathlib.Path(__file__).parents[2] / "web"
    index = (web_dir / "index.html").read_text()
    assert 'id="fmm-groups"' in index          # editor datalist
    assert "collapsedGroups" in index          # collapsible + map filter
    assert "fmm.collapsedGroups" in index      # persisted per browser
    timeline = (web_dir / "timeline.html").read_text()
    assert "optgroup" in timeline
    app_js = (web_dir / "app.js").read_text()
    assert "f_group:" in app_js and "Gruppe" in app_js
    assert ".group-header" in (web_dir / "app.css").read_text()


def test_real_timeline_has_day_week_month_range_and_visits_paging():
    web_dir = pathlib.Path(__file__).parents[2] / "web"
    timeline = (web_dir / "timeline.html").read_text()
    assert 'id="range-mode"' in timeline
    for v in ('"day"', '"week"', '"month"', '"range"'):
        assert f'data-value={v}' in timeline
    assert 'id="prev-btn"' in timeline and 'id="anchor"' in timeline
    assert "fmm.rangeMode" in timeline and "fmm.anchor" in timeline
    assert "VISITS_PAGE" in timeline and "visitsExpanded" in timeline
    app_js = (web_dir / "app.js").read_text()
    assert "range_month:" in app_js and "visits_show_more:" in app_js
