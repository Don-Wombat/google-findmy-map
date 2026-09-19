# Plan: delete a visited place; reset a device's history

**Spec:** `docs/superpowers/specs/2026-09-20-delete-visit-reset-history-design.md`

Two independent features, implemented backend-first. Each lands as
`feat:` (store) → `test:` → `feat:` (endpoint) → `test:` → `feat:`
(frontend) → `docs:` commits. Full suite must stay green
(`python -m pytest -q`).

---

## Shared: `_refresh_device_track()` helper

### `service/main.py`

- Import: `from augment import augment_device` →
  `from augment import augment_device, RECENT_TRACK_LENGTH`.
- New function near `_augment_all()`:
  ```python
  def _refresh_device_track(device_id):
      with _state_lock:
          for d in _state["devices"]:
              if d["id"] == device_id:
                  track = _store.recent(device_id, RECENT_TRACK_LENGTH)
                  d["history"] = track
                  d["last_location_time"] = track[-1]["time"] if track else d.get("time")
                  break
  ```
  Both new endpoints call this instead of `_augment_all(_state["devices"])`
  after their delete, to avoid `augment_device()`'s `store.add()`
  resurrecting the just-deleted fix.

---

## Feature 1 — delete a visited place

### `service/store.py`

- `delete_range(self, device_id, start_ts, end_ts)` — single
  `DELETE FROM locations WHERE device_id = ? AND ts >= ? AND ts <= ?`,
  `commit()`, return `cur.rowcount`. Added after `delete_device()`.

### `service/main.py`

- `DELETE /api/history` (`dependencies=[Depends(block_cross_site)]`),
  params `device: str, start: int, end: int` (all required). 422 if
  `end < start`. Calls `_store.delete_range()` then
  `_refresh_device_track(device)`. Returns
  `{"device", "start", "end", "points"}`. Placed after `get_history()`.

### Frontend — `web/timeline.html`

- Extract the inline `.visit` row markup in `renderVisits()` into a
  `visitRow(v, n, when, place, pending, deviceId, data)` function.
- Add a delete button (`.icon-btn.visit-del`) per row. Click → inline
  confirm (`.visit-confirm` text + `.visit-actions` with `.btn-danger` go /
  plain cancel), restating place + time window + point count via
  `visit_confirm_q`.
- `cancel` → `renderVisits(data)`. `go` → `fetch('/api/history?...',
  {method: 'DELETE'})`, disable buttons in flight, generic `login_error` on
  failure, `show()` on success (redraws track + visits list together).

### `web/app.css`

- `.visit-del`, `.visit.confirming`, `.visit-confirm`, `.visit-actions` —
  modeled on `.stale-confirm`/`.stale-actions`.

### `web/app.js` — new STRINGS (EN / DE)

- `visit_delete`: "Delete this visit" / "Diesen Besuch löschen"
- `visit_confirm_q`: "Permanently delete the visit to "{place}" ({when},
  {n} points)? This also shortens the track for that period — this cannot
  be undone." / "Den Besuch bei „{place}" ({when}, {n} Punkte) endgültig
  löschen? Dadurch wird auch der Track für diesen Zeitraum verkürzt — das
  lässt sich nicht rückgängig machen."
- `visit_confirm_go`: "Delete" / "Löschen"

### Docs

- `README.md`: timeline paragraph gains a sentence that visits are
  deletable and that this shortens the track; endpoint list gains
  `DELETE /api/history`.
- `SECURITY.md`: cross-site-blocked list gains `DELETE /api/history`; new
  bullet on the explicit-range-only (no silent default) policy.

### Tests

- `service/tests/test_store.py::TestDeleteRange` — deletes only points in
  `[start, end]`; only touches the named device; leaves
  `device_settings` untouched; no-op outside the stored range.
- `service/tests/test_api.py::TestDeleteHistoryRange` — deletes points in
  range and returns the count; 422 on `end < start`; 422 on missing
  `start`/`end`; no-op 200 on unknown device; blocked cross-site; leaves
  `device_settings` untouched; **regression test**: seed a live device via
  `_seed_one_device`, delete exactly its cached last fix's timestamp,
  assert `/api/devices`-equivalent in-memory `history` is now empty (proves
  `_refresh_device_track` doesn't resurrect it).

---

## Feature 2 — reset a device's history

### `service/store.py`

- `reset_device_history(self, device_id)` — single
  `DELETE FROM locations WHERE device_id = ?`, `commit()`, return
  `cur.rowcount`. Added after `delete_range()`.

### `service/main.py`

- `DELETE /api/devices/{device_id}/history`
  (`dependencies=[Depends(block_cross_site)]`), no params, no
  liveness/409 check. Calls `_store.reset_device_history()` then
  `_refresh_device_track(device_id)`. Returns `{"device", "points"}`
  (deliberately not `{"deleted": ...}` — the device itself isn't deleted).
  Placed after `delete_device()`.

### Frontend — `web/settings.html`

- New `<section class="settings-section">` between "Data export" and "Old
  devices": heading `s_reset_history`, hint `reset_history_hint`, a
  `<select id="reset-device">` populated in `loadDevices()` with the same
  "every device" loop already used for `export-device`, and a
  `<div id="reset-history-box">` that holds either the reset button or its
  inline confirm.
- Selecting a device shows a `.btn-danger` "Reset history" button
  (`reset_history_button`). Clicking it shows an inline confirm (reusing
  `.stale-confirm`/`.stale-actions`) with `reset_confirm_q` (name + point
  count + "name/colour/group are kept"), go (`reset_confirm_go`) / cancel.
  `go` → `fetch('/api/devices/{id}/history', {method: 'DELETE'})` →
  `loadDevices()` on success (refreshes point counts everywhere on the
  page).

### `web/app.js` — new STRINGS (EN / DE)

- `s_reset_history`: "Reset device history" / "Verlauf eines Geräts
  zurücksetzen"
- `reset_history_hint`: "Delete a device's location history while keeping
  its name, colour and group. This cannot be undone." / "Löscht den
  Standortverlauf eines Geräts, behält aber Name, Farbe und Gruppe. Das
  lässt sich nicht rückgängig machen."
- `reset_history_button`: "Reset history" / "Verlauf zurücksetzen"
- `reset_confirm_q`: "Permanently delete all {n} location points for
  "{name}"? Its name, colour and group are kept. This cannot be undone." /
  "Alle {n} Standortpunkte von „{name}" endgültig löschen? Name, Farbe und
  Gruppe bleiben erhalten. Das lässt sich nicht rückgängig machen."
- `reset_confirm_go`: "Reset" / "Zurücksetzen"

### Docs

- `README.md`: new bullet near "Old devices" describing the reset control;
  endpoint list gains `DELETE /api/devices/{id}/history`; settings-page
  feature list gains "device-history reset".
- `SECURITY.md`: cross-site-blocked list gains the new endpoint; new bullet
  noting it has no liveness restriction but leaves `device_settings`
  intact (contrast with `DELETE /api/devices/{id}`).

### Tests

- `service/tests/test_store.py::TestResetDeviceHistory` — removes all
  points, keeps `device_settings` (name/colour/group all survive); only
  touches the named device; no-op on unknown device.
- `service/tests/test_api.py::TestResetDeviceHistoryEndpoint` — clears
  history and keeps settings; succeeds on a **live** device with no 409;
  same resurrection regression test as Feature 1; no-op 200 on unknown
  device; blocked cross-site.
- `test_real_timeline_has_a_visit_delete_affordance` /
  `test_real_settings_has_a_reset_history_control` — confirm the new
  markup/i18n keys exist in the real HTML/JS files and that no
  `alert()`/`confirm()` was introduced.

---

## Verification

- `python -m pytest -q` — full suite green.
- `python -m pytest service/tests/test_store.py -k "DeleteRange or ResetDeviceHistory" -q`
- `python -m pytest service/tests/test_api.py -k "DeleteHistoryRange or ResetDeviceHistoryEndpoint" -q`
- Manual click-through with synthetic data (per
  `.claude/.claude/rules/findmy-map-public-assets.md`): delete a visit on
  the timeline → it disappears and the track shortens; reset a device's
  history in settings → point count drops to 0, name/colour/group survive.
