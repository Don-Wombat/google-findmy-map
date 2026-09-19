"""Builds the real Dockerfile and asserts it succeeds -- the same check
CI's separate ``docker-build`` job does, but runnable locally for fast
feedback while iterating on ``service/``, ``web/``, ``requirements.txt`` or
the ``Dockerfile`` itself. Skips cleanly wherever no Docker daemon is
reachable (plain CI runners, most contributors' machines); this repo's own
dev container has a Docker-in-Docker sidecar wired up via ``DOCKER_HOST``
specifically to make this possible.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.docker

IMAGE_TAG = "findmy-map:pytest-build-check"
REPO_ROOT = pathlib.Path(__file__).parents[2]

# apt-get's HTTP acquire method drops from root to the unprivileged "_apt"
# user before fetching anything (Debian's apt sandbox, on by default since
# apt 2.x). Empirically confirmed (2026-09-20) that this sandboxed user
# cannot resolve/reach the network at all through this repo's nested
# Docker-in-Docker sidecar (claude-code-docker) specifically -- plain
# root-run network I/O in the SAME image (git clone, pip install) is
# unaffected, and neither the DNS servers, IPv6, nor the container's
# seccomp profile turned out to be the cause; only skipping the sandbox
# (running the fetch as root, which every RUN step in this Dockerfile
# already is before any USER switch) does. This is a quirk of the local
# dev sidecar's capability nesting, not of the project or its Dockerfile,
# so the fix is applied ONLY to this verification copy, never to the real,
# checked-in Dockerfile that CI and users actually build.
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


@pytest.mark.skipif(not _docker_available(), reason="no Docker daemon reachable")
def test_docker_image_builds():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    patched = _APT_SANDBOX_WORKAROUND.sub(
        r"apt-get -o APT::Sandbox::User=root \1", dockerfile,
    )
    assert patched != dockerfile, "expected at least one apt-get call to patch"

    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile") as f:
        f.write(patched)
        f.flush()
        try:
            result = subprocess.run(
                ["docker", "build", "-f", f.name, "-t", IMAGE_TAG, str(REPO_ROOT)],
                capture_output=True, text=True, timeout=600,
            )
            assert result.returncode == 0, (
                "docker build failed:\n"
                f"--- stdout (tail) ---\n{result.stdout[-4000:]}\n"
                f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
            )
        finally:
            # Best-effort: don't let a leftover tagged image pile up in the
            # sidecar's storage volume run over run. Ignored if the build
            # failed before anything was tagged.
            subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True)
