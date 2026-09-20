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
RUN pip install --no-cache-dir -r requirements.txt

COPY service/ /app/service
COPY web/ /app/web

# COPY preserves the source files' permissions as-is, whatever they happened
# to be on the host this was built on. The container starts as root and
# entrypoint.sh drops to a non-root UID before the app runs, so force
# everything world-readable here instead of depending on host umask/ACLs to
# have gotten it right.
RUN chmod -R a+rX /app/vendor /app/service /app/web

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
