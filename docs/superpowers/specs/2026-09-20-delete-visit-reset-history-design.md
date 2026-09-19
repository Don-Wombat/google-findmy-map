# Design: delete a visited place; reset a device's history

Two independent features. No new runtime dependency; both build on the
existing `LocationStore`/`service/main.py` conventions.

---

## 1. Delete a visited place

### Problem

The timeline's "Visited places" list has no way to remove a wrong or
unwanted entry — a GPS glitch that got clustered into a false stay, or a
place the operator simply doesn't want recorded. There is currently no
delete affordance anywhere on that list.

### Approach

A visited place is not a stored row: `visits.detect_visits()` computes it
fresh on every `GET /api/visits` request from the raw `locations` points in
the requested window (`LocationStore.range()`). There is nothing to delete
except those underlying points, so "delete this visit" is implemented as:
hard-delete the device's `locations` rows with `start_ts <= ts <= end_ts`
for exactly that visit's `[start, end]` window.

- `LocationStore.delete_range(device_id, start_ts, end_ts) -> int` — a
  single `DELETE ... WHERE device_id = ? AND ts BETWEEN ? AND ?`, mirroring
  the existing `range()` reader's WHERE clause. One statement, so (unlike
  `delete_device()`) no rollback pairing is needed.
- `DELETE /api/history?device=&start=&end=` (`block_cross_site`, all three
  params **required** — no defaulting to "the whole account" the way the
  read/export endpoints' 0-to-now fallback does; a destructive range delete
  should never silently apply beyond what was asked for). 422 if
  `end < start`.
- A device's cached in-memory track (`_state["devices"]`, refreshed each
  poll cycle) must be corrected without re-triggering
  `augment_device()`'s unconditional `store.add()` of the last polled fix
  — that would immediately re-insert exactly the point(s) just deleted
  whenever the deleted window reaches the device's current fix. A new
  `_refresh_device_track(device_id)` helper patches only the cached
  `history`/`last_location_time` fields from a fresh `store.recent()` call
  instead of calling `_augment_all()`.
- Frontend: each `.visit` row on the timeline page gets a small delete
  button. Clicking it swaps the row to an inline confirm (no native
  `confirm()`) restating the place, time window and point count, mirroring
  `settings.html`'s stale-device delete flow. Deleting reloads the whole
  view (`show()`) so the track and the visits list both reflect the
  now-shorter history.

### Non-goals

No soft-delete / undo / "hide without removing" — matches the existing
device-delete precedent, which is also a hard, irreversible delete.

---

## 2. Reset a device's history

### Problem

The only existing way to clear a device's location data is
`DELETE /api/devices/{id}`, which also wipes its `device_settings` row
(name, colour, group) and refuses to run while the device is still live.
There is no way to just start a device's track over while keeping its
identity/customisation, and no way to do that for a device that is still
actively reporting.

### Approach

- `LocationStore.reset_device_history(device_id) -> int` — a single
  `DELETE FROM locations WHERE device_id = ?`, leaving `device_settings`
  untouched. Contrast with `delete_device()`, which deletes from both
  tables.
- `DELETE /api/devices/{device_id}/history` (`block_cross_site`). No 409 /
  liveness restriction — resetting a live device's history is meaningful
  (it simply starts re-accumulating on the next poll), unlike a full
  device delete, which would be pointless on a live device since it would
  immediately reappear. Also calls `_refresh_device_track()` for the same
  resurrection-avoidance reason as feature 1.
- Frontend: a new "Reset device history" section on the settings page,
  between "Data export" and "Old devices" — a device `<select>` populated
  with **every** device (live and stale alike, same population as the
  export picker) plus a danger-styled reset button with the same
  inline-confirm pattern as the stale-device delete flow, restating the
  device name and its current point count.

### Non-goals

No liveness gate, by design (see Approach). No option to reset a bounded
date range from this control — that's what deleting individual visits (or
the existing full-history export before resetting) is for.

---

## Shared implementation note

Both features route their post-delete state refresh through the same new
`_refresh_device_track()` helper in `service/main.py` rather than
`update_device()`'s `_augment_all(_state["devices"])`, specifically because
`augment_device()` (`service/augment.py`) unconditionally re-persists a
device's last cached poll fix via `store.add()` — harmless when only
settings changed, but would resurrect exactly the row(s) a delete just
removed.
