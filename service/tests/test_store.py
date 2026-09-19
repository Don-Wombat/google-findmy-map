import json
import sqlite3

import pytest

from store import LocationStore


def pt(lat=52.5, lon=13.4, time=1000, accuracy=10):
    return {"latitude": lat, "longitude": lon, "time": time, "accuracy": accuracy}


@pytest.fixture
def store(tmp_path):
    s = LocationStore(tmp_path / "history.db")
    yield s
    s.close()


class TestAdd:
    def test_add_then_recent_returns_the_point(self, store):
        store.add("dev", pt(time=1000))
        assert store.recent("dev") == [
            {"time": 1000, "latitude": 52.5, "longitude": 13.4, "accuracy": 10}
        ]

    def test_second_point_at_same_timestamp_is_ignored(self, store):
        store.add("dev", pt(time=1000, lat=52.5))
        store.add("dev", pt(time=1000, lat=99.9))
        points = store.recent("dev")
        assert len(points) == 1 and points[0]["latitude"] == 52.5

    def test_point_without_coordinates_or_time_is_skipped(self, store):
        store.add("dev", {"latitude": None, "longitude": None, "time": 1000})
        store.add("dev", {"latitude": 1, "longitude": 2, "time": None})
        assert store.recent("dev") == []

    def test_history_is_not_capped_at_five(self, store):
        for t in range(1, 11):
            store.add("dev", pt(time=t))
        assert len(store.range("dev", 0, 100)) == 10

    def test_recent_returns_the_newest_n_oldest_first(self, store):
        for t in (1, 2, 3, 4, 5, 6, 7):
            store.add("dev", pt(time=t))
        assert [p["time"] for p in store.recent("dev", 3)] == [5, 6, 7]

    def test_devices_do_not_share_history(self, store):
        store.add("a", pt(time=1))
        store.add("b", pt(time=2))
        assert [p["time"] for p in store.recent("a")] == [1]


class TestRange:
    def test_range_bounds_are_inclusive(self, store):
        for t in (10, 20, 30, 40):
            store.add("dev", pt(time=t))
        assert [p["time"] for p in store.range("dev", 20, 30)] == [20, 30]

    def test_range_with_no_data_in_window_is_empty(self, store):
        store.add("dev", pt(time=100))
        assert store.range("dev", 0, 50) == []


class TestPruning:
    def test_prune_deletes_only_older_points(self, store):
        store.add("dev", pt(time=100))
        store.add("dev", pt(time=200))
        deleted = store.prune_older_than(150)
        assert deleted == 1
        assert [p["time"] for p in store.range("dev", 0, 1000)] == [200]

    def test_prune_returns_zero_when_nothing_is_old_enough(self, store):
        store.add("dev", pt(time=100))
        assert store.prune_older_than(50) == 0
        assert len(store.range("dev", 0, 1000)) == 1

    def test_prune_spans_all_devices(self, store):
        store.add("a", pt(time=1))
        store.add("b", pt(time=1))
        store.add("b", pt(time=1000))
        assert store.prune_older_than(500) == 2
        assert store.range("a", 0, 2000) == []
        assert len(store.range("b", 0, 2000)) == 1


class TestPersistence:
    def test_data_survives_reopening_the_file(self, tmp_path):
        path = tmp_path / "history.db"
        first = LocationStore(path)
        first.add("dev", pt(time=1000))
        first.close()

        second = LocationStore(path)
        assert [p["time"] for p in second.recent("dev")] == [1000]
        second.close()


class TestMigration:
    def test_imports_legacy_json_and_renames_the_file(self, tmp_path):
        legacy = tmp_path / "history.json"
        legacy.write_text(json.dumps({"dev": [pt(time=1), pt(time=2)]}))

        store = LocationStore(tmp_path / "history.db")
        store.migrate_json(legacy)

        assert [p["time"] for p in store.recent("dev")] == [1, 2]
        assert not legacy.exists()
        assert (tmp_path / "history.json.migrated").exists()
        store.close()

    def test_migrating_a_missing_file_is_a_noop(self, store, tmp_path):
        store.migrate_json(tmp_path / "does-not-exist.json")  # must not raise

    def test_migrating_a_corrupt_file_is_a_noop(self, store, tmp_path):
        bad = tmp_path / "history.json"
        bad.write_text("{ not json")
        store.migrate_json(bad)  # must not raise
        assert store.recent("dev") == []


class TestDeviceSettings:
    def test_no_settings_by_default(self, store):
        assert store.get_settings() == {}

    def test_set_and_read_back_a_name_and_colour(self, store):
        store.set_setting("dev", name="Car", color="#ff0000")
        assert store.get_settings() == {
            "dev": {"name": "Car", "color": "#ff0000", "group": None}}

    def test_setting_is_upserted_not_duplicated(self, store):
        store.set_setting("dev", name="A", color="#111111")
        store.set_setting("dev", name="B", color="#222222")
        assert store.get_settings() == {
            "dev": {"name": "B", "color": "#222222", "group": None}}

    def test_empty_values_are_stored_as_no_override(self, store):
        store.set_setting("dev", name="A", color="#111111")
        store.set_setting("dev", name="", color="")
        assert store.get_settings() == {
            "dev": {"name": None, "color": None, "group": None}}

    def test_settings_survive_reopen(self, tmp_path):
        path = tmp_path / "history.db"
        first = LocationStore(path)
        first.set_setting("dev", name="Car", color="#00ff00")
        first.close()
        second = LocationStore(path)
        assert second.get_settings() == {
            "dev": {"name": "Car", "color": "#00ff00", "group": None}}
        second.close()


class TestKnownDevices:
    def test_empty_by_default(self, store):
        assert store.known_devices() == []

    def test_a_device_with_history_but_no_settings_row(self, store):
        store.add("dev", pt(time=1000))
        assert store.known_devices() == [
            {"id": "dev", "name": "dev", "group": None, "last_seen": 1000, "point_count": 1}
        ]

    def test_last_known_name_is_used_as_a_fallback(self, store):
        store.add("dev", pt(time=1000))
        store.set_last_seen_name("dev", "iPhone")
        assert store.known_devices() == [
            {"id": "dev", "name": "iPhone", "group": None, "last_seen": 1000, "point_count": 1}
        ]

    def test_manual_rename_takes_priority_over_last_known_name(self, store):
        store.add("dev", pt(time=1000))
        store.set_last_seen_name("dev", "iPhone")
        store.set_setting("dev", name="Backpack")
        assert store.known_devices()[0]["name"] == "Backpack"

    def test_renaming_does_not_clobber_the_last_known_name(self, store):
        store.set_last_seen_name("dev", "iPhone")
        store.set_setting("dev", name="Backpack")
        store.set_setting("dev", name="", color="")   # clear the override again
        assert store.known_devices()[0]["name"] == "iPhone"

    def test_a_device_with_only_a_remembered_name_and_no_history(self, store):
        """A device that only ever reported semantic (coordinate-less)
        locations never gets a row in `locations` at all."""
        store.set_last_seen_name("dev", "iPhone")
        assert store.known_devices() == [
            {"id": "dev", "name": "iPhone", "group": None, "last_seen": None, "point_count": 0}
        ]

    def test_empty_name_does_not_overwrite_a_known_one(self, store):
        store.set_last_seen_name("dev", "iPhone")
        store.set_last_seen_name("dev", "")
        assert store.known_devices()[0]["name"] == "iPhone"

    def test_sorted_most_recently_seen_first_then_never_seen_last(self, store):
        store.add("old", pt(time=1000))
        store.add("new", pt(time=5000))
        store.set_last_seen_name("no-history", "Ghost")
        assert [d["id"] for d in store.known_devices()] == ["new", "old", "no-history"]

    def test_migrating_an_existing_db_adds_the_last_known_name_column(self, tmp_path):
        """A DB created before this feature existed has `device_settings`
        without `last_known_name` -- the ALTER TABLE migration must run on
        an existing table, not just at fresh-schema creation time."""
        path = tmp_path / "history.db"
        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE device_settings (device_id TEXT PRIMARY KEY, name TEXT, color TEXT)"
        )
        conn.execute("INSERT INTO device_settings (device_id, name) VALUES ('dev', 'Car')")
        conn.commit()
        conn.close()

        opened = LocationStore(path)
        try:
            opened.set_last_seen_name("dev", "iPhone")   # would raise if the column were missing
            assert opened.known_devices()[0]["name"] == "Car"
        finally:
            opened.close()


class TestDeviceGroups:
    def test_set_and_read_back_a_group(self, store):
        store.set_setting("dev", name="Phone", color="", group="Familie")
        assert store.get_settings()["dev"]["group"] == "Familie"

    def test_clearing_the_group_stores_null(self, store):
        store.set_setting("dev", name="Phone", color="", group="Familie")
        store.set_setting("dev", name="Phone", color="", group="")
        assert store.get_settings()["dev"]["group"] is None

    def test_known_devices_carries_the_group(self, store):
        store.add("dev", pt(time=1000))
        store.set_setting("dev", name="Phone", color="", group="Fahrzeuge")
        assert store.known_devices()[0]["group"] == "Fahrzeuge"

    def test_group_survives_a_last_seen_name_write(self, store):
        store.set_setting("dev", name="Phone", color="", group="Familie")
        store.set_last_seen_name("dev", "Pixel 8")   # targeted upsert
        assert store.get_settings()["dev"]["group"] == "Familie"

    def test_last_seen_name_survives_a_group_write(self, store):
        store.set_last_seen_name("dev", "Pixel 8")
        store.set_setting("dev", name="", color="", group="Familie")
        assert store.known_devices()[0]["name"] == "Pixel 8"

    def test_migrating_an_existing_db_adds_the_device_group_column(self, tmp_path):
        path = tmp_path / "history.db"
        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE device_settings "
            "(device_id TEXT PRIMARY KEY, name TEXT, color TEXT, last_known_name TEXT)"
        )
        conn.execute("INSERT INTO device_settings (device_id, name) VALUES ('dev', 'Car')")
        conn.commit()
        conn.close()

        opened = LocationStore(path)
        try:
            opened.set_setting("dev", name="Car", color="", group="Fahrzeuge")
            assert opened.get_settings()["dev"]["group"] == "Fahrzeuge"
        finally:
            opened.close()


class TestDeleteDevice:
    def test_removes_history_and_settings_and_reports_the_count(self, store):
        for t in (100, 200, 300):
            store.add("dev", pt(time=t))
        store.set_setting("dev", name="Old", color="", group="G")

        removed = store.delete_device("dev")

        assert removed == 3
        assert store.range("dev", 0, 1000) == []
        assert store.get_settings() == {}
        assert store.known_devices() == []

    def test_only_touches_the_named_device(self, store):
        store.add("a", pt(time=1))
        store.add("b", pt(time=2))
        store.set_setting("a", name="A")
        store.set_setting("b", name="B")

        store.delete_device("a")

        assert [d["id"] for d in store.known_devices()] == ["b"]
        assert store.get_settings() == {"b": {"name": "B", "color": None, "group": None}}

    def test_deleting_an_unknown_device_is_a_harmless_noop(self, store):
        store.add("real", pt(time=1))
        assert store.delete_device("ghost") == 0
        assert [d["id"] for d in store.known_devices()] == ["real"]

    def test_point_count_is_reported_by_known_devices(self, store):
        for t in (1, 2, 3, 4):
            store.add("dev", pt(time=t))
        assert store.known_devices()[0]["point_count"] == 4

    def test_a_failure_partway_through_rolls_back_the_first_delete(self, store, monkeypatch):
        """delete_device() runs two DELETEs under one commit. If the second
        one fails, the first must not be left applied-but-uncommitted on the
        connection -- it needs an explicit rollback()."""
        store.add("dev", pt(time=100))
        store.set_setting("dev", name="Old")

        # sqlite3.Connection.execute is a C-level, read-only attribute on an
        # immutable type -- can't be monkeypatched directly. Swap in a thin
        # proxy on store._conn instead (a plain Python attribute) that fails
        # on the second execute() and forwards everything else untouched.
        class FlakyConn:
            def __init__(self, real):
                self._real = real
                self.calls = 0

            def execute(self, sql, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:  # the device_settings DELETE
                    raise sqlite3.Error("simulated failure")
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(store, "_conn", FlakyConn(store._conn))
        removed = store.delete_device("dev")
        monkeypatch.undo()

        assert removed == 0
        # The first DELETE (locations) must have been rolled back, not just
        # left uncommitted -- otherwise this read would already see it gone.
        assert store.range("dev", 0, 1000) != []
        assert store.get_settings() == {"dev": {"name": "Old", "color": None, "group": None}}


class TestDeleteRange:
    def test_deletes_only_points_within_the_inclusive_range(self, store):
        for t in (100, 200, 300, 400):
            store.add("dev", pt(time=t))
        assert store.delete_range("dev", 200, 300) == 2
        assert [p["time"] for p in store.range("dev", 0, 1000)] == [100, 400]

    def test_only_touches_the_named_device(self, store):
        store.add("a", pt(time=100))
        store.add("b", pt(time=100))
        store.delete_range("a", 0, 1000)
        assert store.range("a", 0, 1000) == []
        assert store.range("b", 0, 1000) != []

    def test_leaves_device_settings_untouched(self, store):
        store.add("dev", pt(time=100))
        store.set_setting("dev", name="Kept")
        store.delete_range("dev", 0, 1000)
        assert store.get_settings() == {"dev": {"name": "Kept", "color": None, "group": None}}

    def test_deleting_outside_the_stored_range_is_a_harmless_noop(self, store):
        store.add("dev", pt(time=100))
        assert store.delete_range("dev", 500, 600) == 0
        assert store.range("dev", 0, 1000) != []


class TestResetDeviceHistory:
    def test_removes_all_history_but_keeps_settings(self, store):
        for t in (100, 200, 300):
            store.add("dev", pt(time=t))
        store.set_setting("dev", name="Kept", color="#112233", group="G")
        assert store.reset_device_history("dev") == 3
        assert store.range("dev", 0, 1000) == []
        assert store.get_settings() == {"dev": {"name": "Kept", "color": "#112233", "group": "G"}}

    def test_only_touches_the_named_device(self, store):
        store.add("a", pt(time=1))
        store.add("b", pt(time=2))
        store.reset_device_history("a")
        assert store.range("a", 0, 1000) == []
        assert store.range("b", 0, 1000) != []

    def test_resetting_an_unknown_device_is_a_harmless_noop(self, store):
        store.add("real", pt(time=1))
        assert store.reset_device_history("ghost") == 0
        assert store.range("real", 0, 1000) != []


class TestGeocodeCache:
    def test_miss_returns_none(self, store):
        assert store.geocode_get(52.5, 13.4) is None

    def test_put_then_get_roundtrip(self, store):
        store.geocode_put(52.52001, 13.40502, "Main St 1", "Main St 1, 10115 Berlin")
        got = store.geocode_get(52.52001, 13.40502)
        assert got["label"] == "Main St 1"
        assert got["address"] == "Main St 1, 10115 Berlin"
        assert isinstance(got["fetched_at"], int)

    def test_coordinates_are_rounded_so_nearby_lookups_hit(self, store):
        store.geocode_put(52.520011, 13.405021, "X", "X full")
        # ~5 m away -> rounds to the same 4-decimal key
        assert store.geocode_get(52.520044, 13.405049)["label"] == "X"

    def test_put_is_upserted(self, store):
        store.geocode_put(52.5, 13.4, "old", "old full")
        store.geocode_put(52.5, 13.4, "new", "new full")
        assert store.geocode_get(52.5, 13.4)["label"] == "new"

    def test_negative_entry_has_null_label_but_a_timestamp(self, store):
        store.geocode_put(52.5, 13.4, None, None)
        got = store.geocode_get(52.5, 13.4)
        assert got["label"] is None and isinstance(got["fetched_at"], int)


class TestConfig:
    def test_set_then_get_roundtrip(self, store):
        store.set_config("auth_enabled", "1")
        assert store.get_config("auth_enabled") == "1"

    def test_get_missing_returns_default(self, store):
        assert store.get_config("missing") is None
        assert store.get_config("missing", "0") == "0"

    def test_set_config_overwrites(self, store):
        store.set_config("k", "a")
        store.set_config("k", "b")
        assert store.get_config("k") == "b"

    def test_get_config_many_returns_only_present_keys(self, store):
        store.set_config("a", "1")
        store.set_config("b", "2")
        assert store.get_config_many(["a", "b", "c"]) == {"a": "1", "b": "2"}

    def test_session_secret_is_generated_and_stable(self, store):
        s = store.session_secret()
        assert isinstance(s, str) and len(s) == 64
        assert store.session_secret() == s

    def test_session_secret_is_64_lowercase_hex(self, store):
        s = store.session_secret()
        assert len(s) == 64
        assert all(c in "0123456789abcdef" for c in s)

    def test_session_secret_survives_reopen(self, tmp_path):
        first = LocationStore(tmp_path / "history.db")
        secret = first.session_secret()
        first.close()
        second = LocationStore(tmp_path / "history.db")
        try:
            assert second.session_secret() == secret
        finally:
            second.close()


class TestReadonlyFallback:
    def test_unwritable_path_falls_back_to_in_memory(self, tmp_path):
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            store = LocationStore(ro / "history.db")
            store.add("dev", pt(time=1))
            assert [p["time"] for p in store.recent("dev")] == [1]
        finally:
            ro.chmod(0o700)
