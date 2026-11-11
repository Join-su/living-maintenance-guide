"""Run and trace identifiers (trace contract 3): run_id = "run_" + ULID (sortable by time), trace_id = 32 lowercase hex, never all zero."""
import os
import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32


def _ulid(now=None):
    ms = int((time.time() if now is None else now) * 1000)
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_run_id(now=None):
    return "run_" + _ulid(now)


def new_feedback_id(now=None):
    """A human feedback id (EV-004 CHG-F02): "fb_" + ULID, made the way run ids are. The Deployment Server makes it once per
    request and sends the same id again when it retries (D-182)."""
    return "fb_" + _ulid(now)


def new_trace_id(parent=None):
    """A new trace id, or the trace id of a W3C `traceparent` header when one is given (the run continues that trace)."""
    if parent:
        parts = parent.strip().split("-")
        if len(parts) == 4 and len(parts[1]) == 32 and parts[1] != "0" * 32 and all(c in "0123456789abcdef" for c in parts[1]):
            return parts[1]
    while True:
        t = secrets.token_hex(16)
        if t != "0" * 32:
            return t
