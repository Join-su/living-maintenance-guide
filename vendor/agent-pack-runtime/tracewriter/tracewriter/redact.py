"""Shared redaction (trace contract 6): exact secret values given as an argument and common key formats are masked in every
string inside a value (dict keys and values, list items). Environment variables are never read here. The hash of a stored raw
value is the hash of the bytes after this function (REDACTION_POLICY_VERSION names the rule set)."""
import re

REDACTED = "[가림]"
REDACTION_POLICY_VERSION = "redact-1"
KEY_PATTERNS = [r"sk-ant-[A-Za-z0-9_-]{8,}", r"sk-[A-Za-z0-9_-]{8,}", r"AKIA[0-9A-Z]{12,}", r"ghp_[A-Za-z0-9]{20,}",
                r"xox[bp]-[A-Za-z0-9-]{8,}", r"AIza[0-9A-Za-z_-]{20,}"]
# a key starts a token: no letter or digit right before it, so ordinary words ('task-runner-v2', 'risk-assessment') stay as written
KEY_RE = re.compile("(?<![A-Za-z0-9])(?:" + "|".join(KEY_PATTERNS) + ")")
# GAP-003 (llmops EV-032 0854e72, packrt instruction 014): a key the provider shows half-masked in its own message — 'Incorrect API
# key provided: sk-proj-****…abcd' — is not the full key, so the exact-value and key-format masks leave its visible end. That whole
# shape (sk-, optional word- parts, three or more '*', the visible end) — or what is left of it after a mask ('[가림]***abcd') —
# becomes 'sk-***'. Text with plain asterisks stays as written.
PROVIDER_MASKED_RE = re.compile(r"(?:\bsk-(?:[A-Za-z0-9]+-)*[A-Za-z0-9]*|" + re.escape(REDACTED) + r")\*{3,}[A-Za-z0-9_-]*")
PROVIDER_MASKED = "sk-***"


def redact_text(text, secrets=()):
    """Mask exact secret values (longest first), common key formats and keys a provider shows half-masked (GAP-003) in one
    string."""
    if not text:
        return text
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, REDACTED)
    return PROVIDER_MASKED_RE.sub(PROVIDER_MASKED, KEY_RE.sub(REDACTED, text))


def redact(value, *, secrets=()):
    """(redacted copy of value, REDACTION_POLICY_VERSION). Non-string scalars stay as they are."""
    secrets = [s for s in secrets if isinstance(s, str) and s]

    def walk(v):
        if isinstance(v, str):
            return redact_text(v, secrets)
        if isinstance(v, dict):
            return {walk(k) if isinstance(k, str) else k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return v
    return walk(value), REDACTION_POLICY_VERSION
