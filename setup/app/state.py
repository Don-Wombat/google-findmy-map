"""Run-state machine and a bounded log buffer for one wizard run at a time.

Same lock-guarded-dict idiom service/main.py uses for its own poll state
(_state / _state_lock), scaled down: this process only ever runs one flow at
a time, so a single dict plus a single Lock is enough -- no need for the
main app's richer per-device state.
"""

import threading
import time
from collections import deque

_lock = threading.Lock()
_log: deque[dict] = deque(maxlen=500)
_state = {
    "phase": "idle",  # idle -> running -> {success, failed, timed_out}
    "error": None,
    "started_at": None,
}


def snapshot() -> dict:
    with _lock:
        return dict(_state)


def log_events() -> list[dict]:
    with _lock:
        return list(_log)


def append_log(stage: str, msg: str) -> None:
    with _lock:
        _log.append({"ts": time.time(), "stage": stage, "msg": msg})


def try_start() -> bool:
    """Transition idle/success/failed/timed_out -> running. False if a run
    is already in progress (the single-run lock)."""
    with _lock:
        if _state["phase"] == "running":
            return False
        _state["phase"] = "running"
        _state["error"] = None
        _state["started_at"] = time.time()
        _log.clear()
        return True


def finish(phase: str, error: str | None = None) -> None:
    with _lock:
        _state["phase"] = phase
        _state["error"] = error
