"""Signed session tokens for the setup wizard's own access gate.

Near-verbatim duplicate of service/auth.py's token helpers, not imported
from there: this is a separate container/process (no shared package, no
conftest.py in this repo to hang a shared fixture off of -- see the
existing docker test files for the same duplicate-a-small-helper pattern).

Simplified from the original: one bearer token (SETUP_TOKEN, generated once
per container start by entrypoint.sh) stands in for both "username+password"
and the HMAC signing secret. There is no cred_version counter -- restarting
the container already mints a brand new SETUP_TOKEN, which invalidates every
previously issued session cookie for free, since they were signed with the
old, now-forgotten secret.

Standard library only: hmac, secrets, base64, json.
"""

import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256

_SESSION_MAX_AGE = 3600
_CLOCK_SKEW = 60


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), sha256).digest())


def token_matches(candidate: str, secret: str) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate, secret)


def make_session_token(secret: str, *, now: int | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = _b64(json.dumps({"iat": issued}, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(secret, payload)}"


def parse_session_token(token: str, secret: str, *,
                        max_age: int = _SESSION_MAX_AGE, now: int | None = None) -> bool:
    try:
        payload, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(secret, payload)):
            return False
        data = json.loads(_unb64(payload))
        iat = int(data["iat"])
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeEncodeError):
        return False
    age = int(now if now is not None else time.time()) - iat
    return -_CLOCK_SKEW <= age <= max_age
