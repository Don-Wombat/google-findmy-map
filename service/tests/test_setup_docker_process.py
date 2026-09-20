"""Builds the setup-wizard image and runs it to verify the container's own
process supervision, chown/privilege-drop, and access gate actually work --
the setup-wizard sibling of test_docker_entrypoint.py. Skips cleanly
wherever no Docker daemon is reachable, same as the other Docker tests.

Does NOT test the actual interactive Google login (impossible to automate,
see setup/app/run_flow.py and test_setup_wizard_logic.py's module
docstring) -- only that the container boots all five supervised processes,
the token gate correctly rejects/accepts, and /vnc/ is gated the same way.

All HTTP checks go through `docker exec <container> curl ...` rather than a
published host port: this repo's dev sidecar (DOCKER_HOST) is not
necessarily on the same network as this test process (see
test_docker_entrypoint.py's docstring for the identical daemon-vs-test-
process filesystem caveat, which applies to networking here too) -- `docker
exec` runs inside the container's own network namespace regardless, so it
sidesteps the question entirely. This is also a closer match to how the
container is actually deployed: no published port at all, reachable only
via docker-compose.yml's proxy-net (see that file's comments).
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid

import pytest

pytestmark = pytest.mark.docker

IMAGE_TAG = "findmy-map-setup:pytest-process-check"
REPO_ROOT = pathlib.Path(__file__).parents[2]
SETUP_DIR = REPO_ROOT / "setup"

_APT_SANDBOX_WORKAROUND = re.compile(r"\bapt-get (update|install)\b")
_TOKEN_RE = re.compile(r"token=(\S+)")
_SET_COOKIE_RE = re.compile(r"set-cookie:\s*wiz_session=([^;\r\n]+)", re.IGNORECASE)


def _docker_available():
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _build_patched_image():
    dockerfile = (SETUP_DIR / "Dockerfile").read_text()
    patched = _APT_SANDBOX_WORKAROUND.sub(
        r"apt-get -o APT::Sandbox::User=root \1", dockerfile,
    )
    assert patched != dockerfile, "expected at least one apt-get call to patch"

    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile") as f:
        f.write(patched)
        f.flush()
        result = subprocess.run(
            ["docker", "build", "-f", f.name, "-t", IMAGE_TAG, str(SETUP_DIR)],
            capture_output=True, text=True, timeout=900,
        )
        assert result.returncode == 0, (
            "docker build failed:\n"
            f"--- stdout (tail) ---\n{result.stdout[-4000:]}\n"
            f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
        )


def _exec_curl(container_id, *args):
    result = subprocess.run(
        ["docker", "exec", container_id, "curl", "-s", *args],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def _status_code(container_id, path):
    out = _exec_curl(container_id, "-o", "/dev/null", "-w", "%{http_code}",
                      f"http://127.0.0.1:8090{path}")
    return out.strip()


@pytest.mark.skipif(not _docker_available(), reason="no Docker daemon reachable")
def test_setup_wizard_gate_and_processes():
    _build_patched_image()

    # Pre-create the secrets file on the HOST side of the eventual bind
    # mount, via a throwaway container (not locally -- see module
    # docstring): entrypoint.sh deliberately fails fast rather than trying
    # to fix this itself, since by the time it runs, Docker has already
    # decided file-vs-directory for the bind mount (see entrypoint.sh's
    # comments). A real deployment must do this exact pre-step once too.
    host_path = f"/tmp/findmy-map-setup-pytest-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "--rm", "-v", "/tmp:/hosttmp",
         "--entrypoint", "sh", IMAGE_TAG, "-c",
         f"echo '{{}}' > /hosttmp/{pathlib.Path(host_path).name}"],
        capture_output=True, text=True, timeout=30, check=True,
    )

    container_id = None
    try:
        run_result = subprocess.run(
            ["docker", "run", "-d",
             "-e", "PUID=1234", "-e", "PGID=1234",
             "-e", "GFM_SETUP_TOKEN_TTL=900",
             "-e", "GFM_SETUP_RUN_TIMEOUT_SECONDS=900",
             "-v", f"{host_path}:/app/vendor/Auth/secrets.json:rw",
             IMAGE_TAG],
            capture_output=True, text=True, timeout=30, check=True,
        )
        container_id = run_result.stdout.strip()

        # nginx + supervisord + Xvfb startup isn't instant -- poll rather
        # than assume readiness immediately. "000" is curl's %{http_code}
        # for a connection failure (nginx not listening yet); "502"/"503"
        # is nginx up but its wizard upstream (uvicorn) not accepting
        # connections yet -- both are transient, expected startup states,
        # not truthy-but-final like a real status code.
        _NOT_READY = {"000", "502", "503"}
        deadline = time.time() + 60
        status = "000"
        while time.time() < deadline and status in _NOT_READY:
            status = _status_code(container_id, "/")
            if status in _NOT_READY:
                time.sleep(1)
        assert status == "401", f"expected the gate to reject an unauthenticated request, got {status!r}"

        logs = subprocess.run(
            ["docker", "logs", container_id],
            capture_output=True, text=True, timeout=10,
        ).stdout
        match = _TOKEN_RE.search(logs)
        assert match, f"setup token banner not found in container logs:\n{logs}"
        token = match.group(1)

        redeem = _exec_curl(container_id, "-i", f"http://127.0.0.1:8090/?token={token}")
        assert "302" in redeem.splitlines()[0], f"expected a 302 redirect, got: {redeem.splitlines()[0]}"
        cookie_match = _SET_COOKIE_RE.search(redeem)
        assert cookie_match, f"no wiz_session cookie in redeem response:\n{redeem}"
        cookie = cookie_match.group(1)

        authed_status = _exec_curl(
            container_id, "-o", "/dev/null", "-w", "%{http_code}",
            "--cookie", f"wiz_session={cookie}", "http://127.0.0.1:8090/api/status",
        ).strip()
        assert authed_status == "200"

        vnc_unauthed = _status_code(container_id, "/vnc/")
        assert vnc_unauthed == "401", f"/vnc/ should be gated the same way, got {vnc_unauthed!r}"

        vnc_authed = _exec_curl(
            container_id, "-o", "/dev/null", "-w", "%{http_code}",
            "--cookie", f"wiz_session={cookie}", "http://127.0.0.1:8090/vnc/",
        ).strip()
        # Not asserting a full VNC protocol handshake here, just that
        # websockify/noVNC behind the gate is actually reachable once
        # authenticated -- a connection failure or 5xx would mean nginx's
        # proxy_pass to it is broken, not just that the exact status code
        # differs from some assumed value.
        assert vnc_authed and vnc_authed[0] in "23", f"/vnc/ did not respond once authenticated: {vnc_authed!r}"

        stat_result = subprocess.run(
            ["docker", "exec", container_id, "stat", "-c", "%u:%g",
             "/app/vendor/Auth/secrets.json"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        assert stat_result.stdout.strip() == "1234:1234"
    finally:
        if container_id:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
        subprocess.run(
            ["docker", "run", "--rm", "-v", "/tmp:/hosttmp",
             "--entrypoint", "rm", IMAGE_TAG, "-f", f"/hosttmp/{pathlib.Path(host_path).name}"],
            capture_output=True,
        )
        subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True)
