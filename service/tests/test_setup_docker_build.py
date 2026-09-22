"""Builds setup/Dockerfile and asserts it succeeds -- the setup-wizard
sibling of test_docker_build.py, same rationale.

The build itself happens once per test session, in the ``setup_image``
fixture (see ``conftest.py``) -- ``test_setup_docker_process.py`` reuses
the same built image rather than building its own.
"""

import subprocess

import pytest

pytestmark = pytest.mark.docker


def test_setup_wizard_image_builds(setup_image):
    """The build itself already happened (and was asserted) in the
    setup_image fixture -- this just confirms the resulting tag is a real,
    inspectable image, not e.g. a stale leftover tag."""
    result = subprocess.run(
        ["docker", "image", "inspect", setup_image],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0
