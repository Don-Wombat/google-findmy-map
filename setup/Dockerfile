FROM python:3.11-slim

# nginx (full package, not nginx-light -- nginx-light's default Debian build
# omits ngx_http_auth_request_module, which nginx.conf's /vnc/ gate needs)
# and supervisor coordinate five long-running processes in this one
# container: Xvfb (virtual X display), fluxbox (window manager), x11vnc
# (VNC server), websockify (VNC-over-websocket + noVNC static client), and
# this image's own FastAPI control app. chromium/chromium-driver give
# undetected_chromedriver a real, non-headless browser to drive; gosu is the
# same privilege-drop tool the root Dockerfile uses. curl has no runtime
# role -- it exists so the test suite (test_setup_docker_process.py) can
# probe this container's own HTTP surface via `docker exec ... curl`,
# sidestepping any assumption about whether a published port is reachable
# from the test process (this repo's dev sidecar and the test process are
# not necessarily on the same network -- see that test file's docstring).
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates gosu curl \
      xvfb x11vnc fluxbox \
      chromium chromium-driver \
      novnc websockify \
      nginx supervisor \
    && rm -rf /var/lib/apt/lists/*

# Vendor the same upstream library, pinned to the same commit as the main
# findmy-map image (see the root Dockerfile's identical ARG) -- both
# containers read/write the same secrets.json and must agree on the shape
# the vendored Auth/ package produces. Review this SHA before building and
# bump it deliberately, in lockstep with the root Dockerfile's ARG.
ARG GFM_UPSTREAM_REF=d46e952
RUN git clone https://github.com/leonboe1/GoogleFindMyTools.git /app/vendor \
    && git -C /app/vendor checkout "${GFM_UPSTREAM_REF}"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # pip-installed files inherit the build host's umask same as COPY does
    # -- confirmed empirically that a restrictive build-host umask leaves
    # them unreadable to anyone but root, which breaks `import uvicorn`
    # (and everything else pip installed) for the non-root runtime user
    # with a confusing "ModuleNotFoundError", not a permissions error.
    && chmod -R a+rX /usr/local/lib/python3.11/site-packages /usr/local/bin

COPY app/ /app/setup-app
COPY web/ /app/setup-web

# Same reasoning as the root Dockerfile: COPY preserves host permissions
# as-is, and the container starts as root only long enough to chown the
# secrets file before dropping to PUID:PGID -- force everything
# world-readable here rather than depending on host umask/ACLs. /app
# itself is included (not just its children): it was implicitly created
# by the "git clone .../app/vendor" step above, before WORKDIR touched it,
# so its permissions come from whatever umask the build host happened to
# have -- confirmed empirically to matter (this repo's own dev sidecar has
# a restrictive enough umask that /app ends up non-traversable for any UID
# but root without this, which then breaks every non-root process's
# chdir/open under /app, not just this image's own -- the root Dockerfile
# has the same latent, umask-dependent gap; worth hardening there too, but
# out of scope for this change).
RUN chmod a+rX /app && chmod -R a+rX /app/vendor /app/setup-app /app/setup-web

COPY nginx.conf /etc/nginx/nginx.conf
COPY supervisord.conf /etc/supervisor/conf.d/setup-wizard.conf
# Same COPY-preserves-host-permissions issue as above: supervisord and
# nginx both run as the unprivileged PUID (entrypoint.sh has already
# dropped root by the time CMD starts them), and a build host with a
# restrictive umask left these unreadable to anyone but their owner --
# confirmed empirically (supervisord failed with "could not read config
# file" until this was added).
RUN chmod a+rX /etc/nginx/nginx.conf /etc/supervisor/conf.d/setup-wizard.conf

COPY entrypoint.sh /entrypoint.sh
# 755, not a bare "+x" -- see the root Dockerfile's identical comment: a
# non-owner, non-group PUID needs both read and execute, and COPY alone
# only carries over whatever the source file happened to have on the build
# host.
RUN chmod 755 /entrypoint.sh

EXPOSE 8090
ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/setup-wizard.conf", "-n"]
