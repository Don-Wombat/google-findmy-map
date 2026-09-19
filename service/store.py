"""Full per-device location history, backed by SQLite.

The main map only needs the last few fixes, but the timeline page needs
the complete track. Keeping every fix in a JSON file that is rewritten on
every poll doesn't scale, so history lives in a single SQLite database
(``sqlite3`` is in the standard library -- no new dependency).

One fix per (device, timestamp) is stored; a second report for a
timestamp already on file is ignored.
"""

import json
import logging
import secrets
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("findmy-map")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    device_id TEXT    NOT NULL,
    ts        INTEGER NOT NULL,
    lat       REAL    NOT NULL,
    lon       REAL    NOT NULL,
    accuracy  REAL,
    PRIMARY KEY (device_id, ts)
);

CREATE TABLE IF NOT EXISTS device_settings (
    device_id       TEXT PRIMARY KEY,
    name            TEXT,   -- NULL: no name override, use the polled name
    color           TEXT,   -- NULL: no colour override, fall back to env/palette
    last_known_name TEXT,   -- last name Google reported for this device, kept
                            -- even after it drops out of the live poll list
    device_group    TEXT    -- NULL: ungrouped. ("group" is a SQL keyword; the
                            -- API / UI key for this is "group")
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    qlat       REAL NOT NULL,   -- query lat/lon rounded to 4 decimals (~11 m)
    qlon       REAL NOT NULL,
    label      TEXT,            -- short human label
    address    TEXT,            -- full display name
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (qlat, qlon)
);

CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_GEOCODE_PRECISION = 4


def _migrate_schema(conn):
    """Add columns introduced after the initial ``CREATE TABLE`` to a DB that
    already exists -- ``CREATE TABLE IF NOT EXISTS`` is a no-op on those."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(device_settings)")}
    if "last_known_name" not in cols:
        conn.execute("ALTER TABLE device_settings ADD COLUMN last_known_name TEXT")
    if "device_group" not in cols:
        conn.execute("ALTER TABLE device_settings ADD COLUMN device_group TEXT")


class LocationStore:
    def __init__(self, path):
        self._lock = threading.Lock()
        self._write_warned = False
        self._session_secret = None
        self._conn = self._connect(str(path))

    def _connect(self, path):
        try:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            _migrate_schema(conn)
            conn.commit()
            return conn
        except sqlite3.Error as exc:
            log.error(
                "Cannot open history DB at %s (%s); using an in-memory DB -- "
                "the location history will NOT survive a restart.",
                path,
                exc,
            )
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.executescript(_SCHEMA)
            _migrate_schema(conn)
            conn.commit()
            return conn

    def close(self):
        with self._lock:
            self._conn.close()

    # -- writes ---------------------------------------------------------

    def add(self, device_id, point):
        """Store one geo fix. Points missing coordinates or a time are ignored."""
        lat = point.get("latitude")
        lon = point.get("longitude")
        ts = point.get("time")
        if lat is None or lon is None or ts is None:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO locations "
                    "(device_id, ts, lat, lon, accuracy) VALUES (?, ?, ?, ?, ?)",
                    (device_id, int(ts), float(lat), float(lon), point.get("accuracy")),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                if not self._write_warned:
                    log.warning(
                        "Could not write to history DB: %s (further write "
                        "errors suppressed).",
                        exc,
                    )
                    self._write_warned = True

    def prune_older_than(self, cutoff_ts):
        """Delete location fixes older than ``cutoff_ts`` (unix seconds).

        Returns the number of rows deleted. Only ``locations`` is pruned --
        it is the table that grows unbounded; ``geocode_cache`` is small and
        self-limiting (one row per rounded coordinate cell).
        """
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM locations WHERE ts < ?", (int(cutoff_ts),)
                )
                self._conn.commit()
                return cur.rowcount
            except sqlite3.Error as exc:
                log.warning("Could not prune history: %s", exc)
                return 0

    def delete_device(self, device_id):
        """Forget a device entirely: its whole location history and its
        name / colour / group overrides. Returns the number of location
        fixes removed. The shared geocode cache is coordinate-keyed, not
        per-device, so it is left alone.
        """
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM locations WHERE device_id = ?", (device_id,)
                )
                self._conn.execute(
                    "DELETE FROM device_settings WHERE device_id = ?", (device_id,)
                )
                self._conn.commit()
                return cur.rowcount
            except sqlite3.Error as exc:
                # The two DELETEs above must succeed together or not at all --
                # without this rollback, a failure on the second statement
                # would leave the first one applied-but-uncommitted on this
                # connection, silently reported to the caller as "0 removed".
                self._conn.rollback()
                log.warning("Could not delete device %r: %s", device_id, exc)
                return 0

    def delete_range(self, device_id, start_ts, end_ts):
        """Delete a device's location fixes with start_ts <= ts <= end_ts
        (inclusive, matching range()'s window). Used to delete a detected
        visit, which is never a stored row itself -- see
        visits.detect_visits(). Returns the number of fixes removed. A
        single statement, unlike delete_device(), so no rollback pairing
        is needed.
        """
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM locations WHERE device_id = ? AND ts >= ? AND ts <= ?",
                    (device_id, int(start_ts), int(end_ts)),
                )
                self._conn.commit()
                return cur.rowcount
            except sqlite3.Error as exc:
                log.warning("Could not delete location range for %r: %s", device_id, exc)
                return 0

    def reset_device_history(self, device_id):
        """Delete every location fix for a device, keeping its
        device_settings row (name / colour / group overrides) untouched --
        the key difference from delete_device(), which wipes both. Returns
        the number of fixes removed.
        """
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM locations WHERE device_id = ?", (device_id,)
                )
                self._conn.commit()
                return cur.rowcount
            except sqlite3.Error as exc:
                log.warning("Could not reset history for device %r: %s", device_id, exc)
                return 0

    def set_setting(self, device_id, name=None, color=None, group=None):
        """Upsert a device's display-name / pin-colour / group override.

        Empty strings are treated as "no override" and stored as NULL. All
        three columns are (re)written on every call -- the web UI editor
        always submits the full set.
        """
        name = name or None
        color = color or None
        group = group or None
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO device_settings (device_id, name, color, device_group) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(device_id) DO UPDATE SET "
                    "name = ?, color = ?, device_group = ?",
                    (device_id, name, color, group, name, color, group),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                if not self._write_warned:
                    log.warning(
                        "Could not write device settings: %s (further write "
                        "errors suppressed).",
                        exc,
                    )
                    self._write_warned = True

    def get_settings(self):
        """All device overrides as
        ``{device_id: {"name": ..., "color": ..., "group": ...}}``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT device_id, name, color, device_group FROM device_settings"
            ).fetchall()
        return {r[0]: {"name": r[1], "color": r[2], "group": r[3]} for r in rows}

    def set_last_seen_name(self, device_id, name):
        """Remember the name Google most recently reported for a device.

        Called on every poll so the name survives the device later dropping
        out of the live poll list (see :meth:`known_devices`). Distinct from
        the ``name`` column, which is the user's manual override.
        """
        if not name:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO device_settings (device_id, last_known_name) "
                    "VALUES (?, ?) "
                    "ON CONFLICT(device_id) DO UPDATE SET last_known_name = ?",
                    (device_id, name, name),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                if not self._write_warned:
                    log.warning(
                        "Could not write last-known device name: %s (further "
                        "write errors suppressed).",
                        exc,
                    )
                    self._write_warned = True

    def known_devices(self):
        """Every device ever seen:
        ``[{"id", "name", "group", "last_seen", "point_count"}, ...]``.

        Includes devices no longer returned by the live poll, as long as
        they have history and/or a remembered name. ``name`` prefers a
        manual override, then the last name Google reported, then the raw
        device id. ``last_seen`` is ``None`` for a device that only ever
        reported semantic (coordinate-less) locations. Sorted most
        recently seen first.
        """
        with self._lock:
            loc_rows = self._conn.execute(
                "SELECT device_id, MAX(ts), COUNT(*) FROM locations GROUP BY device_id"
            ).fetchall()
            settings_rows = self._conn.execute(
                "SELECT device_id, name, last_known_name, device_group FROM device_settings"
            ).fetchall()
        last_seen = {r[0]: r[1] for r in loc_rows}
        counts = {r[0]: r[2] for r in loc_rows}
        settings = {r[0]: {"name": r[1], "last_known_name": r[2], "group": r[3]}
                    for r in settings_rows}
        out = []
        for device_id in set(last_seen) | set(settings):
            s = settings.get(device_id, {})
            name = s.get("name") or s.get("last_known_name") or device_id
            out.append({
                "id": device_id, "name": name, "group": s.get("group"),
                "last_seen": last_seen.get(device_id),
                "point_count": counts.get(device_id, 0),
            })
        out.sort(key=lambda d: (d["last_seen"] is None, -(d["last_seen"] or 0)))
        return out

    # -- application config (auth) ---------------------------------------

    def get_config(self, key, default=None):
        """Value for a config key, or ``default`` if it is not set."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_config WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row is not None else default

    def get_config_many(self, keys):
        """``{key: value}`` for the given keys that are set (missing keys omitted)."""
        if not keys:
            return {}
        placeholders = ",".join("?" * len(keys))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT key, value FROM app_config WHERE key IN ({placeholders})",
                tuple(keys),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def set_config(self, key, value):
        """Upsert one config key."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO app_config (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = ?",
                    (key, value, value),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                if not self._write_warned:
                    log.warning("Could not write app config: %s", exc)
                    self._write_warned = True

    def session_secret(self):
        """The HMAC secret for session tokens; generated and stored on first use.

        Memoised for the life of the process: if persisting it fails (read-only
        volume, disk full) we must keep using the *same* in-memory secret, or
        every request would mint a new one and no session cookie would ever
        verify -- an endless redirect back to the login page.
        """
        if self._session_secret is None:
            secret = self.get_config("session_secret")
            if not secret:
                secret = secrets.token_hex(32)
                self.set_config("session_secret", secret)  # best-effort persist
            self._session_secret = secret
        return self._session_secret

    # -- reverse-geocode cache ---------------------------------------------

    def geocode_get(self, lat, lon):
        """Cached entry for a coordinate (rounded), or ``None`` on a miss.

        A row with ``label``/``address`` both ``None`` is a *negative* cache
        entry (a lookup that failed); ``fetched_at`` lets callers decide when
        to retry it.
        """
        qlat = round(lat, _GEOCODE_PRECISION)
        qlon = round(lon, _GEOCODE_PRECISION)
        with self._lock:
            row = self._conn.execute(
                "SELECT label, address, fetched_at FROM geocode_cache "
                "WHERE qlat = ? AND qlon = ?",
                (qlat, qlon),
            ).fetchone()
        if row is None:
            return None
        return {"label": row[0], "address": row[1], "fetched_at": row[2]}

    def geocode_put(self, lat, lon, label, address):
        qlat = round(lat, _GEOCODE_PRECISION)
        qlon = round(lon, _GEOCODE_PRECISION)
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO geocode_cache (qlat, qlon, label, address, fetched_at) "
                    "VALUES (?, ?, ?, ?, strftime('%s','now')) "
                    "ON CONFLICT(qlat, qlon) DO UPDATE SET "
                    "label = excluded.label, address = excluded.address, "
                    "fetched_at = excluded.fetched_at",
                    (qlat, qlon, label, address),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                if not self._write_warned:
                    log.warning("Could not write geocode cache: %s", exc)
                    self._write_warned = True

    def migrate_json(self, json_path):
        """Import a legacy ``history.json`` (``{device_id: [points]}``) once.

        The file is renamed to ``*.migrated`` afterwards so it is imported
        only a single time. Missing or unreadable files are ignored.
        """
        json_path = Path(json_path)
        if not json_path.exists():
            return
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read legacy history file %s: %s", json_path, exc)
            return

        if isinstance(data, dict):
            imported = 0
            for device_id, points in data.items():
                for point in points or []:
                    self.add(device_id, point)
                    imported += 1
            log.info("Migrated %d legacy history point(s) into %s.", imported, "the DB")

        try:
            json_path.rename(json_path.with_name(json_path.name + ".migrated"))
        except OSError as exc:
            log.warning("Could not rename migrated history file %s: %s", json_path, exc)

    # -- reads ---------------------------------------------------------

    def recent(self, device_id, n=5):
        """The newest ``n`` fixes for a device, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, lat, lon, accuracy FROM locations "
                "WHERE device_id = ? ORDER BY ts DESC LIMIT ?",
                (device_id, n),
            ).fetchall()
        return [_row_to_point(r) for r in reversed(rows)]

    def range(self, device_id, start_ts, end_ts):
        """All fixes for a device with ``start_ts <= ts <= end_ts``, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, lat, lon, accuracy FROM locations "
                "WHERE device_id = ? AND ts >= ? AND ts <= ? ORDER BY ts ASC",
                (device_id, int(start_ts), int(end_ts)),
            ).fetchall()
        return [_row_to_point(r) for r in rows]


def _row_to_point(row):
    ts, lat, lon, accuracy = row
    return {"time": ts, "latitude": lat, "longitude": lon, "accuracy": accuracy}
