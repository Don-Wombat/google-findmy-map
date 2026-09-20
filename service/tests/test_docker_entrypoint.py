"""Builds the real image and runs it to verify entrypoint.sh's chown +
privilege-drop mechanics: /data gets chowned to the requested PUID:PGID and
the process actually ends up running as that user -- the thing that used
to be a separate `findmy-map-init` service. Skips cleanly wherever no
Docker daemon is reachable, same as test_docker_build.py.

Uses a bind mount to a host path that does NOT exist yet (Docker auto-
creates it as root when the container starts), matching the real
docker-compose.yml deployment exactly -- not a named volume. Two reasons:

1. This test's own process and the Docker daemon it talks to (DOCKER_HOST)
   are not necessarily the same machine/filesystem -- a bind mount's host
   path is resolved by the DAEMON, not by this process, so stat()-ing a
   local tmp_path after a `docker run -v <local path>:/data` would
   silently check the wrong filesystem entirely wherever DOCKER_HOST points
   at a remote/nested daemon (exactly this repo's dev setup). Ownership is
   therefore checked via a second container, not a local stat() call, and
   cleanup of the host-side path also goes through a container.
2. A NAMED volume was tried first and found to race against Docker's
   "seed a fresh volume from the image's directory of the same path"
   step in this specific nested Docker-in-Docker sidecar: entrypoint.sh's
   chown would run, but the volume-seeding copy would then silently
   overwrite it back to the image's original root ownership by the time
   the container exited -- reproducible every time on a volume that had
   never been attached to any container before, but never on a bind mount
   (which has no such image-seeding step at all) or on a volume that had
   already been used once. Since the real docker-compose.yml uses a bind
   mount, not a named volume, that race does not apply to production --
   confirmed empirically by running this exact scenario against a bind
   mount to a fresh, nonexistent host path, which worked correctly on the
   very first run. Kept as a bind mount here so the test matches what
   actually ships, rather than a volume type that turned out to exercise a
   difference dev-sidecar-only failure mode unrelated to entrypoint.sh.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
import uuid

import pytest

pytestmark = pytest.mark.docker

IMAGE_TAG = "findmy-map:pytest-entrypoint-check"
REPO_ROOT = pathlib.Path(__file__).parents[2]

# Same nested-DinD apt-sandbox quirk as test_docker_build.py (see that file
# for the full explanation) -- it blocks fetching gosu too, not just git/
# ca-certificates, so the same verification-only patch is needed here.
# Duplicated rather than imported: it's a ~10-line helper and this repo has
# no conftest.py to share fixtures through, so importing across test files
# would be a new coupling for very little.
_APT_SANDBOX_WORKAROUND = re.compile(r"\bapt-get (update|install)\b")


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
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    patched = _APT_SANDBOX_WORKAROUND.sub(
        r"apt-get -o APT::Sandbox::User=root \1", dockerfile,
    )
    assert patched != dockerfile, "expected at least one apt-get call to patch"

    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile") as f:
        f.write(patched)
        f.flush()
        result = subprocess.run(
            ["docker", "build", "-f", f.name, "-t", IMAGE_TAG, str(REPO_ROOT)],
            capture_output=True, text=True, timeout=600,
        )
        assert result.returncode == 0, (
            "docker build failed:\n"
            f"--- stdout (tail) ---\n{result.stdout[-4000:]}\n"
            f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
        )


@pytest.mark.skipif(not _docker_available(), reason="no Docker daemon reachable")
def test_entrypoint_chowns_data_and_drops_privileges():
    _build_patched_image()
    # A path the daemon has never seen before -- Docker auto-creates it as
    # root on first use, exactly like a brand new GFM_DATA_DIR on a real
    # deployment host. Lives under /tmp on whatever machine DOCKER_HOST
    # actually points at, not under this test process's own filesystem.
    host_path = f"/tmp/findmy-map-pytest-{uuid.uuid4().hex[:8]}"
    try:
        # Trailing "id -u" overrides only CMD, not ENTRYPOINT -- so
        # entrypoint.sh's chown-then-gosu-drop logic still runs first, we
        # just skip booting the full FastAPI app afterward.
        result = subprocess.run(
            ["docker", "run", "--rm",
             "-e", "PUID=1234", "-e", "PGID=1234",
             "-v", f"{host_path}:/data",
             IMAGE_TAG, "id", "-u"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        assert result.stdout.strip() == "1234"

        # Check ownership from a second, short-lived container attached to
        # the same host path -- not a local stat(), see module docstring.
        stat_result = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{host_path}:/data",
             "--entrypoint", "stat", IMAGE_TAG, "-c", "%u:%g", "/data"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        assert stat_result.stdout.strip() == "1234:1234"
    finally:
        # host_path lives on the daemon's filesystem, not this process's --
        # remove it via a throwaway container, not shutil.rmtree.
        subprocess.run(
            ["docker", "run", "--rm", "-v", "/tmp:/hosttmp",
             "--entrypoint", "rm", IMAGE_TAG, "-rf", f"/hosttmp/{pathlib.Path(host_path).name}"],
            capture_output=True,
        )
        subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True)
