#!/bin/sh
set -e

if [ "$(id -u)" = '0' ]; then
    chown -R "${PUID:-1000}:${PGID:-1000}" /data
    exec gosu "${PUID:-1000}:${PGID:-1000}" "$0" "$@"
fi

exec "$@"
