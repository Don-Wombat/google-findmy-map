#!/bin/sh
set -e

SECRETS_PATH=/app/vendor/Auth/secrets.json

if [ "$(id -u)" = '0' ]; then
    # If GFM_SECRETS_FILE points at a host path that did not exist BEFORE
    # `docker compose up`, Docker's bind-mount machinery has already
    # created a DIRECTORY there by the time this script runs -- that
    # decision is made daemon-side, before the container even starts, and
    # nothing inside the container can turn it into a file mount after the
    # fact. Detect that and fail loudly with the actual fix, rather than
    # silently continuing (every downstream Auth/token_cache.py write
    # would otherwise crash on "Is a directory" deep inside the login
    # flow, with no clue what actually went wrong).
    if [ -d "$SECRETS_PATH" ]; then
        echo "ERROR: $SECRETS_PATH is a directory, not a file." >&2
        echo "This means GFM_SECRETS_FILE pointed at a host path that didn't" >&2
        echo "exist yet -- Docker created a directory there instead of a file." >&2
        echo "Fix, on the HOST (not in this container):" >&2
        echo "  rmdir '<that host path>'   # remove Docker's empty directory" >&2
        echo "  echo '{}' > '<that host path>'   # pre-create it as a file" >&2
        echo "Then restart this container. See README 'Generating secrets.json" >&2
        echo "with the setup wizard' for the full first-run steps." >&2
        exit 1
    fi
    # Only reachable if nothing was ever mounted over this path at all
    # (e.g. running this image directly without the documented volume) --
    # a normal docker-compose.yml deployment always hits the directory
    # case above instead when the host file doesn't pre-exist.
    if [ ! -e "$SECRETS_PATH" ]; then
        mkdir -p "$(dirname "$SECRETS_PATH")"
        echo '{}' > "$SECRETS_PATH"
    fi
    chown "${PUID:-1000}:${PGID:-1000}" "$SECRETS_PATH"

    # fluxbox's default startup tries to restore a wallpaper via fbsetbg,
    # which pops up an xmessage dialog ("I can't find an app to set the
    # wallpaper with...") right in the middle of the streamed browser view
    # when none is configured -- confusing for whoever is trying to log in.
    # session.screen0.rootCommand: (empty) is fluxbox's documented way to
    # disable that attempt entirely. Recreated every start since /tmp's
    # persistence isn't guaranteed across container recreation.
    mkdir -p /tmp/.fluxbox
    printf 'session.screen0.rootCommand:\n' > /tmp/.fluxbox/init
    chown -R "${PUID:-1000}:${PGID:-1000}" /tmp/.fluxbox

    SETUP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    export SETUP_TOKEN
    echo "=============================================================="
    echo " Setup wizard ready. Open within ${GFM_SETUP_TOKEN_TTL:-900}s:"
    echo "   http://<host>/?token=${SETUP_TOKEN}"
    echo "=============================================================="

    exec gosu "${PUID:-1000}:${PGID:-1000}" "$0" "$@"
fi

# Reached on the second invocation, now running as the non-root PUID --
# NOT set before the gosu call above: gosu resets HOME to match the
# target UID's /etc/passwd entry regardless of what was exported earlier
# (confirmed empirically -- an export right before "exec gosu ..." was
# silently discarded), defaulting to "/" when, as here, that UID has no
# passwd entry at all. "/" isn't writable by this non-root PUID, which
# broke undetected_chromedriver's driver-patcher cache ("Permission
# denied: '/.local'") and left fluxbox unable to read/write its own
# config ("//.fluxbox", HOME resolving to empty). /tmp is writable by any
# UID and this container is ephemeral by design (see SECURITY.md), so
# nothing here needs to persist across restarts.
export HOME=/tmp
exec "$@"
