"""Drives the vendored GoogleFindMyTools Auth chain to populate secrets.json.

Run as a standalone subprocess (see main.py's ``_start_run``), not imported
into the FastAPI process: Selenium's WebDriverWait calls inside the vendored
code block on network I/O in a way that isn't reliably interruptible from
another thread in the same process. A subprocess gives the parent's timeout
watchdog a clean terminate()/kill() target that also takes the Chrome child
process down with it.

Progress is reported as JSON Lines on real stdout (one line per vendor
print() call, wrapped with the current stage), which the parent process
reads incrementally. The vendored code's own print() calls are the only
progress signal it gives us -- there is no hook to plug into instead.
"""

import contextlib
import io
import json
import os
import sys
import time

# Capture the real, unredirected stdout before anything below touches
# sys.stdout -- this is what progress lines actually get written to.
_real_stdout = sys.stdout

_current_stage = {"value": "starting"}


class _JsonLineAdapter(io.TextIOBase):
    """Wraps the vendored code's print() output as one JSON line per line,
    tagged with whatever stage is currently in progress."""

    def __init__(self):
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                _emit(_current_stage["value"], line.strip())
        return len(s)

    def flush(self):
        pass


def _emit(stage: str, msg: str) -> None:
    _real_stdout.write(json.dumps({"ts": time.time(), "stage": stage, "msg": msg}) + "\n")
    _real_stdout.flush()


def _emit_final(ok: bool, error: str | None = None) -> None:
    payload = {"stage": "done" if ok else "failed", "ok": ok}
    if error:
        payload["error"] = error
    _real_stdout.write(json.dumps(payload) + "\n")
    _real_stdout.flush()


def main() -> int:
    # Both request_oauth_account_token_flow() (Auth/auth_flow.py) and
    # _retrieve_shared_key() (KeyBackup/shared_key_retrieval.py) open with a
    # blocking input("Press Enter to continue...") -- there is no flag in
    # the vendored code to skip this, and this container has no attached
    # TTY for it to read from. Must be patched before either is ever
    # called, not just before the browser step itself.
    import builtins
    builtins.input = lambda *a, **k: ""

    vendor_dir = os.environ.get("GFM_VENDOR_DIR", "/app/vendor")
    sys.path.insert(0, vendor_dir)

    adapter = _JsonLineAdapter()
    try:
        with contextlib.redirect_stdout(adapter):
            _current_stage["value"] = "aas_token"
            from Auth.aas_token_retrieval import get_aas_token
            get_aas_token()

            # get_owner_key() transitively calls get_shared_key(), the
            # second potentially-interactive Google login (see the design
            # spec) -- a fresh browser session, not a continuation of the
            # one above.
            _current_stage["value"] = "owner_key"
            from SpotApi.GetEidInfoForE2eeDevices.get_owner_key import get_owner_key
            get_owner_key()

            _current_stage["value"] = "verify"
            from NovaApi.ListDevices.nbe_list_devices import list_devices
            list_devices()
    except Exception as e:  # noqa: BLE001 -- deliberately broad, this is a leaf process
        # Never the raw exception repr: some of these exceptions carry
        # response bodies that could include token material. Type name plus
        # a short, truncated message only.
        _emit_final(False, f"{type(e).__name__}: {str(e)[:200]}")
        return 1

    _emit_final(True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
