FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*

# Vendor the upstream library this project builds on, pinned to a specific
# commit. Review this SHA before building and bump it deliberately (it is a
# third-party dependency cloned at build time -- see SECURITY.md).
ARG GFM_UPSTREAM_REF=d46e952
RUN git clone https://github.com/leonboe1/GoogleFindMyTools.git /app/vendor \
    && git -C /app/vendor checkout "${GFM_UPSTREAM_REF}"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # pip-installed files inherit the build host's umask same as COPY does
    # -- a restrictive build-host umask leaves them unreadable to anyone
    # but root, breaking every import for the non-root runtime user
    # (confirmed against the setup-wizard image's identical Dockerfile
    # pattern: surfaces as a confusing ModuleNotFoundError, not an
    # obvious permissions error). This is one of three umask/permission
    # fixes this file shares near-verbatim with setup/Dockerfile (this one,
    # the /app chmod below, and entrypoint.sh's chmod 755) -- duplicated
    # rather than centralized since there's no shared base image between
    # the two yet; keep all three in sync in both files if any changes.
    && chmod -R a+rX /usr/local/lib/python3.11/site-packages /usr/local/bin

COPY service/ /app/service
COPY web/ /app/web

# COPY preserves the source files' permissions as-is, whatever they happened
# to be on the host this was built on. The container starts as root and
# entrypoint.sh drops to a non-root UID before the app runs, so force
# everything world-readable here instead of depending on host umask/ACLs to
# have gotten it right. /app itself is included (not just its children): it
# was implicitly created by the "git clone .../app/vendor" step above,
# before WORKDIR touched it, so its permissions come from whatever umask
# the build host happened to have -- confirmed empirically to matter on a
# build host with a restrictive enough umask that /app ends up
# non-traversable for any UID but root ("can't open file
# '/app/service/main.py': Permission denied"), breaking every non-root
# process's access under it regardless of what's chmod'd underneath.
RUN chmod a+rX /app && chmod -R a+rX /app/vendor /app/service /app/web

# Writable location for the SQLite database (GFM_HISTORY_DB). Created here so
# it also exists for the `build: .` local-dev path even before any volume is
# mounted over it, but left root-owned at build time -- the real owner is
# only known at `docker run`/`compose up` time, so entrypoint.sh chowns it to
# the runtime PUID:PGID on every container start instead. GFM_HISTORY_FILE is
# only used to import a pre-SQLite history.json once on startup, if present.
RUN mkdir -p /data
ENV GFM_HISTORY_DB=/data/history.db \
    GFM_HISTORY_FILE=/data/history.json

COPY entrypoint.sh /entrypoint.sh
# 755, not a bare "+x": COPY preserves the source file's host permissions
# as-is (same reasoning as the chmod -R a+rX above), and "+x" alone only
# adds the execute bit, not read -- a script needs both for a non-owner,
# non-group UID (e.g. a custom PUID/PGID) to be able to run it at all.
RUN chmod 755 /entrypoint.sh

WORKDIR /app/service
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
