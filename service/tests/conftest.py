"""Shared Docker-build fixtures for the docker-marked tests.

This repo otherwise deliberately avoids a conftest.py / shared fixtures
between test files -- each docker test file used to duplicate its own
~10-line `_docker_available()`/apt-sandbox-workaround helpers rather than
import them from a sibling, on the grounds that the coupling wasn't worth
it for so little code (see the git history of test_docker_entrypoint.py
and test_setup_docker_build.py).

This file is a deliberate, narrow exception to that: the main findmy-map
image and the setup-wizard image were each being built TWICE -- once in
their own "does it build" test (test_docker_build.py /
test_setup_docker_build.py) and again, independently, in their own "does
it actually run correctly" test (test_docker_entrypoint.py /
test_setup_docker_process.py) -- which roughly doubled the docker-marked
tests' wall time for no extra coverage (both tests already asserted the
exact same build succeeds; only the *following* run/verify step differed).
Session-scoped fixtures here build each image exactly once and hand the
resulting tag to both tests that need it.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
SETUP_DIR = REPO_ROOT / "setup"

# apt-get's HTTP acquire method drops from root to the unprivileged "_apt"
# user before fetching anything (Debian's apt sandbox, on by default since
# apt 2.x). Empirically confirmed (2026-09-20) that this sandboxed user
# cannot resolve/reach the network at all through this repo's nested
# Docker-in-Docker sidecar (claude-code-docker) specifically -- plain
# root-run network I/O in the SAME image (git clone, pip install) is
# unaffected, and neither the DNS servers, IPv6, nor the container's
# seccomp profile turned out to be the cause; only skipping the sandbox
# (running the fetch as root, which every RUN step in these Dockerfiles
# already is before any USER switch) does. This is a quirk of the local
# dev sidecar's capability nesting, not of the project or its Dockerfiles,
# so the fix is applied ONLY to these verification copies, never to the
# real, checked-in Dockerfiles that CI and users actually build.
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


def _build_patched_image(build_dir, image_tag, timeout):
    dockerfile = (build_dir / "Dockerfile").read_text()
    patched = _APT_SANDBOX_WORKAROUND.sub(
        r"apt-get -o APT::Sandbox::User=root \1", dockerfile,
    )
    assert patched != dockerfile, "expected at least one apt-get call to patch"

    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile") as f:
        f.write(patched)
        f.flush()
        result = subprocess.run(
            ["docker", "build", "-f", f.name, "-t", image_tag, str(build_dir)],
            capture_output=True, text=True, timeout=timeout,
        )
        assert result.returncode == 0, (
            "docker build failed:\n"
            f"--- stdout (tail) ---\n{result.stdout[-4000:]}\n"
            f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
        )


@pytest.fixture(scope="session")
def main_image():
    """Tag of the real Dockerfile, built once per test session. Shared by
    test_docker_build.py and test_docker_entrypoint.py."""
    if not _docker_available():
        pytest.skip("no Docker daemon reachable")
    image_tag = "findmy-map:pytest-session"
    _build_patched_image(REPO_ROOT, image_tag, timeout=600)
    yield image_tag
    subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)


@pytest.fixture(scope="session")
def setup_image():
    """Tag of setup/Dockerfile, built once per test session. Shared by
    test_setup_docker_build.py and test_setup_docker_process.py."""
    if not _docker_available():
        pytest.skip("no Docker daemon reachable")
    image_tag = "findmy-map-setup:pytest-session"
    _build_patched_image(SETUP_DIR, image_tag, timeout=900)
    yield image_tag
    subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)
