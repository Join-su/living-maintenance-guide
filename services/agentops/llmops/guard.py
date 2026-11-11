"""Redaction, upload copy and the Agent sandbox (B-08). Every subprocess that opens user code goes through
sandboxed_subprocess(); B-04 (graph reading), B-05 (run executor) and B-06 reuse it.

Sandbox profile (sandbox-exec): user code may read anything and write its own temp dir, but not the LLMOps store
(data dir) or the release's original folder. In isolated-env runs (LLMOPS_TEST_NETWORK_DENY=1) outbound IP network is
denied too, except `localhost`. macOS's `localhost` rule also covers this machine's own interface addresses (UF-S03-1);
external addresses are denied. Env shaping removes every LLMOps key variable (keys.ENV_NAMES) and passes NETGUARD_BLOCK through.

Linux (the VPS container, EV-VPS-01 CHG-V03): there is no sandbox-exec. The same two functions lower the subprocess to the evaluation
account (LLMOPS_EVAL_USER, default packeval) with setpriv; write deny and the DB secret read deny are that account's file permissions,
checked right before each start (the account must not be able to write a write-deny path nor read a DB secret path). The network is
not limited there (D-150: VPS evaluation is fake mode only, the open network is a recorded risk). Capabilities the server holds
(compose SETUID · SETGID · DAC_READ_SEARCH) never reach the subprocess: the uid change clears them, --inh-caps=-all and no_new_privs
keep them away (CHG-V09). Any other system refuses to start user code."""
import os
import pwd
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from .keys import ENV_NAMES

# the key formats and the mask live in the shared redaction of the trace writer (EV-019 CHG-91): one rule set for the run
# records here and for trace events, raw payloads and the Deployment Server's report files
from tracewriter.redact import KEY_PATTERNS, KEY_RE, REDACTED  # noqa: E402,F401 - re-exported for existing callers
NET_TAG = "LLMOPS-ISOLATED"
REPO = Path(__file__).resolve().parents[1]
# The shared DB secrets never reach loaded code (EV-019 PRES-97): these variables are dropped from every Agent/Pack/probe
# environment, and the sandbox refuses to read the DB env file and the DB data folder.
DB_ENV_PREFIXES = ("LLMOPSAGENT_", "POSTGRES_", "PG")
DB_ENV_NAMES = ("LLMOPS_DB_ENV_FILE", "TASK10_LOADER_DSN", "TASK10_PG_TEST_DSN",   # the last two: the Pack side's names (015b)
                "LLMOPS_PACK_CI_TOKEN_FILE")   # EV-VPS-01 CHG-V07: key ①'s token file
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SETPRIV = "/usr/bin/setpriv"             # util-linux, by absolute path (docker/llmops.Dockerfile checks it is there)
EVAL_USER_ENV, EVAL_USER = "LLMOPS_EVAL_USER", "packeval"


class IsolationRefused(RuntimeError):
    """User code is not started: the isolation this system needs cannot be set up (the message says why)."""


def _platform():
    return sys.platform


def db_secret_paths():
    """Paths loaded code must not read: db/.env, db/.data and the configured DB env file (LLMOPS_DB_ENV_FILE)."""
    out = [REPO / "db" / ".env", REPO / "db" / ".data"]
    if os.environ.get("LLMOPS_DB_ENV_FILE"):
        out.append(Path(os.environ["LLMOPS_DB_ENV_FILE"]))
    return out
LOCALHOST_ALLOW = '(allow network-outbound (remote ip "localhost:*"))'


# EV-032 (GAP-003, answer 027-01): a key the provider shows half-masked in its own message — 'Incorrect API key provided:
# sk-proj-****…abcd' — is not the full key, so the exact-value and key-format masks leave its visible end. That whole shape (sk-,
# optional word- parts, three or more '*', the visible end) — or what is left of it after a mask ('[가림]***abcd') — becomes 'sk-***'.
PROVIDER_MASKED_RE = re.compile(r"(?:\bsk-(?:[A-Za-z0-9]+-)*[A-Za-z0-9]*|" + re.escape(REDACTED) + r")\*{3,}[A-Za-z0-9_-]*")


def redact(text, known_secrets=()):
    """Mask exact known secret values (e.g. the LLMOps key), common key formats and keys a provider shows half-masked (GAP-003)
    before anything is recorded."""
    if not text:
        return text
    for s in sorted((s for s in known_secrets if s), key=len, reverse=True):
        text = text.replace(s, REDACTED)
    return PROVIDER_MASKED_RE.sub("sk-***", KEY_RE.sub(REDACTED, text))


def copy_upload(files, uploads_dir):
    """Copy uploaded files {relative_path: bytes} into a new folder under uploads_dir, leaving out every `.env` file.
    Returns (folder, excluded_relative_paths)."""
    dest = Path(uploads_dir) / ("u-" + uuid.uuid4().hex[:10])
    excluded = []
    for rel, data in files.items():
        parts = Path(rel).parts
        if not parts or any(p in ("", ".", "..") for p in parts) or Path(rel).is_absolute():
            raise ValueError(f"잘못된 업로드 경로: {rel}")
        if parts[-1] == ".env":
            excluded.append(rel)
            continue
        target = dest.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    dest.mkdir(parents=True, exist_ok=True)
    return dest, sorted(excluded)


def network_denied():
    return os.environ.get("LLMOPS_TEST_NETWORK_DENY") == "1"


def _ancestors(path):
    """Every folder above a path, up to '/' (EV-046)."""
    out, q = [], Path(path).parent
    while True:
        out.append(str(q))
        if q == q.parent:
            return out
        q = q.parent


def profile(write_deny, network_deny=None, allow_localhost=True):
    """Generate the Agent sandbox profile. allow_localhost=False exists only for the mechanism check (UF-S03-1).
    EV-046 (D-300 · D-302): the rules are by path, so a denied path must not be movable — the DB secrets and the db folder are
    write-denied too, and every folder above a write-denied path or a secret is denied as a node (literal: no rename, no delete;
    writing inside it stays allowed). Otherwise a same-account process renames a folder above and reads or writes the moved path."""
    network_deny = network_denied() if network_deny is None else network_deny
    paths = sorted({os.path.realpath(p) for p in write_deny})
    bad = [p for p in paths if any(ch in p for ch in '"\\\n')]
    if bad:  # a quote/backslash/newline would break the profile string and could undo the write deny
        raise ValueError(f"sandbox에 넣을 수 없는 경로 문자: {bad[0]!r}")
    lines = ["(version 1)", "(allow default)"]
    secret = sorted({os.path.realpath(p) for p in db_secret_paths()})
    if any(any(ch in p for ch in '"\\\n') for p in secret):
        raise ValueError("sandbox에 넣을 수 없는 경로 문자(DB 비밀 경로)")
    lines.append("(deny file-read* " + " ".join(f'(literal "{p}") (subpath "{p}")' for p in secret) + ' (with message "LLMOPS-AGENT-DBSECRET"))')
    sub = sorted(set(paths) | set(secret) | {os.path.realpath(REPO / "db")})
    above = sorted({a for p in sub for a in _ancestors(p)} - set(sub))
    if any(any(ch in p for ch in '"\\\n') for p in above):
        raise ValueError("sandbox에 넣을 수 없는 경로 문자(위쪽 폴더)")
    lines.append("(deny file-write* " + " ".join(f'(subpath "{p}")' for p in sub) + " " + " ".join(f'(literal "{p}")' for p in above)
                 + ' (with message "LLMOPS-AGENT-WRITE"))')
    if network_deny:
        lines.append(f'(deny network-outbound (with message "{NET_TAG}"))')
        if allow_localhost:
            lines.append(LOCALHOST_ALLOW)
        lines.append("(allow network-outbound (remote unix-socket))")
    return "\n".join(lines) + "\n"


RUNTIME_DSN = "LLMOPSAGENT_RUNTIME_DSN"   # the one DB variable a Pack run may get, and only when its config reads PostgreSQL (EV-020 CHG-97)


def scrub_db(env, allow_db=()):
    """The environment without any shared-DB variable (PRES-97), whatever built it. allow_db may name the runtime DSN only."""
    allow_db = tuple(allow_db or ())
    if any(n != RUNTIME_DSN for n in allow_db):
        raise ValueError(f"불러온 코드에 넘길 수 있는 DB 변수는 {RUNTIME_DSN} 하나뿐입니다: {allow_db}")
    out = {k: v for k, v in env.items() if not (k.startswith(DB_ENV_PREFIXES) or k in DB_ENV_NAMES)}
    for n in allow_db:
        if env.get(n):
            out[n] = env[n]
    return out


def agent_env(base=None, drop=ENV_NAMES, extra=None):
    env = dict(os.environ if base is None else base)
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # user code must not try to write .pyc into the (write-denied) original folder
    for k in drop:
        env.pop(k, None)
    env.update(extra or {})
    return scrub_db(env)


def eval_account():
    """(name, uid, gid) of the evaluation account on Linux. Raises IsolationRefused without it, or when it is root."""
    name = os.environ.get(EVAL_USER_ENV) or EVAL_USER
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        raise IsolationRefused(f"평가 계정 {name}이 없어 실행하지 않습니다") from None
    if pw.pw_uid == 0 or pw.pw_gid == 0:
        raise IsolationRefused(f"평가 계정 {name}이 root 권한이라 실행하지 않습니다")
    return name, pw.pw_uid, pw.pw_gid


def _account_may(path, uid, gid, bits):
    """Whether an account (uid, gid, no other groups — setpriv --clear-groups) gets `bits` (read 4 · write 2) on path by its mode.
    An owner may always: it can change the mode itself (review VS-2 M-1)."""
    st = os.stat(path)
    if st.st_uid == uid:
        return True
    shift = 3 if st.st_gid == gid else 0
    return bool((st.st_mode >> shift) & bits)


def account_check(write_deny, uid, gid):
    """The reason user code may not start under (uid, gid), or None: a write-deny path (or, when it does not exist yet, the nearest
    folder that does) the account could write or owns, or a DB secret path it could read or owns. Computed for the evaluation account,
    not for the server (a root server would pass every check). Only the listed paths themselves are checked, not what is inside them:
    the image and compose keep those trees root-owned without group/other write (docker/README.md 'Isolation conditions')."""
    for p in sorted({os.path.realpath(x) for x in write_deny}):
        q = Path(p)
        while not q.exists() and q != q.parent:
            q = q.parent
        if _account_may(q, uid, gid, 2):
            return f"평가 계정이 {q}에 쓸 수 있어 실행하지 않습니다"
    for p in db_secret_paths():
        if os.path.exists(p) and _account_may(p, uid, gid, 4):
            return f"평가 계정이 DB 비밀 파일 {p}을 읽을 수 있어 실행하지 않습니다"
    return None


def isolate(cmd, write_deny, network_deny=None, allow_localhost=True):
    """The command that starts user code isolated on this system (macOS: sandbox-exec profile; Linux: setpriv to the evaluation
    account). Raises IsolationRefused when that is not possible."""
    plat = _platform()
    if plat == "darwin":
        return [SANDBOX_EXEC, "-p", profile(write_deny, network_deny=network_deny, allow_localhost=allow_localhost), *cmd]
    if plat.startswith("linux"):
        _, uid, gid = eval_account()
        why = account_check(write_deny, uid, gid)
        if why:
            raise IsolationRefused(why)
        return [SETPRIV, f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--no-new-privs", "--inh-caps=-all", "--", *cmd]
    raise IsolationRefused("이 운영체제에서는 격리 실행기를 쓸 수 없어 실행하지 않습니다")


def sandboxed_subprocess(cmd, cwd, write_deny, timeout=60, env=None, network_deny=None, allow_localhost=True, **kw):
    """Run user code under the Agent sandbox. write_deny: store (data dir) and the release's original folder."""
    return subprocess.run(isolate(cmd, write_deny, network_deny=network_deny, allow_localhost=allow_localhost), cwd=cwd,
                          env=scrub_db(agent_env() if env is None else env), capture_output=True, text=True, timeout=timeout, **kw)



def sandboxed_popen(cmd, cwd, write_deny, env, network_deny=None, allow_db=()):
    """Streaming variant for the run executor: own process group (so timeout/cancel can stop every child), line-buffered."""
    return subprocess.Popen(isolate(cmd, write_deny, network_deny=network_deny), cwd=cwd, env=scrub_db(env, allow_db), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
