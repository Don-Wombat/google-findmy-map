"""Builds setup/Dockerfile and asserts it succeeds -- the setup-wizard
sibling of test_docker_build.py, same rationale and same apt-sandbox
workaround (duplicated rather than imported: this repo has no conftest.py
to share fixtures through, and it's a ~10-line helper -- see
test_docker_build.py's own docstring for the fuller explanation).
"""

import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.docker

IMAGE_TAG = "findmy-map-setup:pytest-build-check"
REPO_ROOT = pathlib.Path(__file__).parents[2]
SETUP_DIR = REPO_ROOT / "setup"

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
def test_setup_wizard_image_builds():
    dockerfile = (SETUP_DIR / "Dockerfile").read_text()
    patched = _APT_SANDBOX_WORKAROUND.sub(
        r"apt-get -o APT::Sandbox::User=root \1", dockerfile,
    )
    assert patched != dockerfile, "expected at least one apt-get call to patch"

    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile") as f:
        f.write(patched)
        f.flush()
        try:
            result = subprocess.run(
                ["docker", "build", "-f", f.name, "-t", IMAGE_TAG, str(SETUP_DIR)],
                capture_output=True, text=True, timeout=900,
            )
            assert result.returncode == 0, (
                "docker build failed:\n"
                f"--- stdout (tail) ---\n{result.stdout[-4000:]}\n"
                f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
            )
        finally:
            subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True)
