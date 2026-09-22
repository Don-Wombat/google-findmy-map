"""Builds the real Dockerfile and asserts it succeeds -- the same check
CI's separate ``docker-build`` job does, but runnable locally for fast
feedback while iterating on ``service/``, ``web/``, ``requirements.txt`` or
the ``Dockerfile`` itself. Skips cleanly wherever no Docker daemon is
reachable (plain CI runners, most contributors' machines); this repo's own
dev container has a Docker-in-Docker sidecar wired up via ``DOCKER_HOST``
specifically to make this possible.

The build itself happens once per test session, in the ``main_image``
fixture (see ``conftest.py``) -- ``test_docker_entrypoint.py`` reuses the
same built image rather than building its own, which used to double this
image's build time across the docker-marked test suite.
"""

import subprocess

import pytest

pytestmark = pytest.mark.docker


def test_docker_image_builds(main_image):
    """The build itself already happened (and was asserted) in the
    main_image fixture -- this just confirms the resulting tag is a real,
    inspectable image, not e.g. a stale leftover tag."""
    result = subprocess.run(
        ["docker", "image", "inspect", main_image],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0
