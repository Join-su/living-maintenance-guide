#!/usr/bin/env bash
# check-public.sh — 공개 저장소 올리기 전 검사(읽기만 · 망 0). 쓰는 법: scripts/check-public.sh [폴더=.] [--history] [--all]
# 끝 코드 0 = FAIL 없음 · 1 = FAIL 있음. 걸린 곳은 파일:줄만(값 출력 0). 규칙 · 범위는 아래 글머리.
set -euo pipefail
command -v python3 >/dev/null || { echo "python3 필요" >&2; exit 2; }
exec python3 -I -B - "$@" <<'PY'
#!/usr/bin/env python3
"""check-public — 공개 저장소에 올리기 전 검사(읽기만 · 망 0 · 쓰기 0 · 걸린 곳은 파일:줄만 · 값 출력 0).

찾는 것(저장소 전체)
  secret.*    비밀값 모양(API 키 · 토큰 · 개인 키 글 · 비밀번호 키 이름 뒤 값 · URL 속 비밀번호).
  personal.*  개인 경로(맥 · 리눅스 · 윈도우 홈) · 이 기기 사용자 이름 · git 이메일 · 이메일 주소.
  server.*    공인 IP · 내부 IP · EC2 · VPS 주소 · ARN 계정 번호 · 운영 서버 포트.
  file.*      .env 원본(example만 허용) · 개인 키 파일 · 봉인 · 정답 자리 · 큰 파일(내용 안 봄).
찾는 것(검사 범위 안만 FAIL · 범위 밖은 건수만 참고)
  internal.*  결정 번호 · EV 번호 · RPT 번호 · 지시 꼬리표 · 세션 핸들 · 조율 폴더 경로 · 조율 낱말 · 세션 이름.
  검사 범위 = 맨 위 README · docs/ · data/ · .env.example · compose 파일 · scripts/ · services/*/docker/ · 화면 글(주석 · 시험 파일 뺌).
있어야 할 것
  need.*      .env.example · .gitignore의 .env · AI4I CC BY 4.0 출처.

쓰는 법
  scripts/check-public.sh [저장소 폴더=.] [--history] [--all] [--names 파일] [--allow 파일] [--json 파일]
  --history  모든 커밋의 파일 내용 · 커밋 글 · 작성자 이메일까지(push 직전 필수).
  --all      내부 번호도 저장소 전체에서 FAIL로 셈.
  허용 파일(기본 <폴더>/.public-check-allow): 한 줄에 '<규칙 glob> <경로 glob>' · # 주석.
끝 코드: 0 = FAIL 없음 · 1 = FAIL 있음 · 2 = 쓰는 법 오류.
"""
import argparse
import fnmatch
import getpass
import ipaddress
import json
import os
import re
import subprocess
import sys

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox"}
ENV_OK = {".env.example", ".env.sample", ".env.template", ".env.dist"}
KEY_FILE_RE = re.compile(r"(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)$|\.(pem|key|p12|pfx|keystore|jks)$", re.I)
SEALED_RE = re.compile(r"(^|/)[^/]*(seal|holdout|hidden|\ubd09\uc778)[^/]*(/|$)|(^|/)cards/test(/|$)|(^|/)kg-test(/|$)", re.I)
PATH_NO_RE = re.compile(r"(?i)(?<![a-z0-9])d[-_]?\d{3,4}(?![0-9])")
DB_FILE_RE = re.compile(r"\.(sqlite3?|db|dump|bak)$", re.I)
MAX_READ = 5 * 1024 * 1024
WARN_SIZE = 50 * 1024 * 1024
FAIL_SIZE = 100 * 1024 * 1024

SCOPE_GLOBS = ["README*", "docs/*", "data/*", ".env.example", "docker-compose*", "compose*.y*ml", "scripts/*",
               "services/*/docker/*", "services/*/Dockerfile*", "services/agentops/web/*", "services/app/apps/*"]
UI_GLOBS = ["services/agentops/web/*", "services/app/apps/*"]
UI_TEST_RE = re.compile(r"(^|/)(__tests__|__mocks__|tests?|e2e)/|\.(test|spec|stories)\.[a-z]+$")
UI_EXT = re.compile(r"\.(js|mjs|cjs|jsx|ts|tsx|vue|svelte|css|scss|html?)$", re.I)

SECRET_RULES = [
    ("secret.sk-key", re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|svcacct-|admin-|ant-[a-z0-9]+-)?[A-Za-z0-9_\-]{20,}")),
    ("secret.aws-access-key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("secret.aws-secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("secret.github-token", re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})")),
    ("secret.langsmith-key", re.compile(r"(?<![A-Za-z0-9])lsv2_(?:pt|sk)_[A-Za-z0-9_]{20,}")),
    ("secret.slack-token", re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("secret.google-key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{35}")),
    ("secret.private-key-block", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("secret.jwt", re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]
ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])([A-Za-z0-9_]*(?:password|passwd|secret|api_key|apikey|token|access_key)[A-Za-z0-9_]*)"
    r"[\"']?\s*[:=]\s*[\"']?([^\s\"',;}{)\]]*)")
URL_CRED_RE = re.compile(r"([a-z][a-z0-9+.\-]*)://([^/\s:@\"']+):([^@\s/\"']+)@([^/\s:\"']+)")
PLACEHOLDER_RE = re.compile(
    r"^(|.|..|\$.*|<.*|\{.*|\[.*|%.*|.*\(.*|x+|\*+|\.+|-+|your.*|change.?me.*|example.*|sample.*|dummy.*|fake.*|test.*|"
    r"none|null|nil|true|false|required|optional|secret|password|token|redacted|placeholder|todo|tbd|os\.environ.*|"
    r"env\..*|process\.env\..*|import\.meta\.env\..*|(?-i:[A-Z][A-Z0-9_]+)|getenv.*|settings\..*|config\..*|self\..*|"
    r"args\..*|kwargs.*|str|int|bool|optional\[.*)$", re.I)

HOME_RE = re.compile(r"/(?:Users|home)/([A-Za-z0-9._\-]+)")
WIN_HOME_RE = re.compile(r"[A-Za-z]:\\\\?Users\\\\?([A-Za-z0-9._\-]+)")
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]+)@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})(?![A-Za-z0-9])")
EMAIL_OK_DOMAINS = re.compile(r"(^|\.)(example\.(com|org|net)|localhost|test|invalid|users\.noreply\.github\.com)$", re.I)
EMAIL_OK_LOCAL = re.compile(r"^(no-?reply|noreply\+.*|git|user|you|name|admin|owner-\d+)$", re.I)
LOCAL_TOOL_RE = re.compile(r"Library/Application Suppor[t]|/private/tmp/claud[e]-|\.claud[e]/projects")
IPV4_RE = re.compile(r"(?<![0-9.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![0-9.])")
AWS_HOST_RE = re.compile(r"ec2-\d+-\d+-\d+-\d+|[A-Za-z0-9.\-]+\.compute(?:-1)?\.amazonaws\.com|arn:aws:[^\s:]*:[^\s:]*:\d{12}:")
VPS_HOST_RE = re.compile(r"(?i)[A-Za-z0-9.\-]+\.(?:vultr|linode|linodeusercontent|digitaloceanspaces|hetzner|your-server|contabo|ovh|vps\.ovh|lightsail)\.[a-z]{2,}")
AWS_GENERIC_RE = re.compile(r"amazonaws\.com")
OPS_PORT_RE = re.compile(r"(?<![0-9])876[56](?![0-9])")

# 내부 번호 · 세션 이름(이 대본 글자 자체에는 걸리지 않게 씀)
INTERNAL_RULES = [
    ("internal.decision-no", re.compile(r"(?<![A-Za-z0-9])D-\d{3,4}(?![0-9])")),
    ("internal.ev-no", re.compile(r"(?<![A-Za-z0-9])EV-\d{3}(?![0-9])")),
    ("internal.rpt-no", re.compile(r"(?<![A-Za-z0-9])RPT-?\d+")),
    ("internal.orch-tag", re.compile(r"ORCH-(?:\uc9c0\uc2dc|\ub2f5\uc7a5|\ubcf4\uace0|\uc54c\ub9bc)|INBOX-20\d\d|orch-(?:send|health|policy)")),
    ("internal.session-handle", re.compile(r"term_[0-9a-f]{8}-[0-9a-f]{4}")),
    ("internal.coord-path", re.compile(
        r"orchestratio[n]/|agents/[A-Za-z0-9\-]+/(?:instructions|answers|reports|handoff)|from-directio[n]/|direction-report[s]/|records/overnigh[t]")),
    ("internal.coord-word", re.compile(r"\uc624\ucf00(?!\uc2a4\ud2b8)|\ubc29\ud5a5 ?(?:\uc138\uc158|\ucc3d)")),
    ("internal.dev-tag", re.compile(r"\[dev-\d\]")),
    ("internal.report-no", re.compile(r"\ubcf4\uace0 \d{3}(?![0-9])")),
    ("internal.session-name", re.compile(
        r"(?<![A-Za-z0-9_\-])(?:c1a15-eva[l]|c1a15-pack-dev[2]|c1a15-dev2-dem[o][A-Za-z0-9\-]*|c1a15-maint-guide-eva[l]|"
        r"llmops-lite-v2-dev[2]|llmops-[2]|new-pac[k]|packrt-[1]|task10-pac[k]|eval-idea[l]|llmopsagent-deploy-dem[o])(?![A-Za-z0-9_])")),
]


def is_ip_kind(s):
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return None
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
        return None
    for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "255.255.0.0/16"):
        if ip in ipaddress.ip_network(net):
            return None
    if ip.is_private or ip in ipaddress.ip_network("100.64." + "0.0/10"):
        return "private"
    if ip.is_reserved:
        return None
    return "public"


def looks_strong(v):
    if len(v) < 16:
        return False
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return classes >= 3 or (len(v) >= 32 and classes >= 2)


def blank_keep_lines(m):
    return "\n" * m.group(0).count("\n")


def strip_ui_comments(text):
    text = re.sub(r"<!--.*?-->", blank_keep_lines, text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", blank_keep_lines, text, flags=re.S)
    return re.sub(r"(?m)(^|[\s;{}(),])//.*$", r"\1", text)


def glob_hit(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


class Checker:
    def __init__(self, root, names, allow, user, git_ids, internal_all):
        self.root = root
        self.names = names
        self.allow = allow
        self.user = user
        self.git_ids = git_ids
        self.internal_all = internal_all
        self.hits = []
        self.outside = {}
        self.allowed = 0
        self.stats = {"files": 0, "read": 0, "skipped_binary_or_big": 0, "content_not_read": 0, "history_blobs": 0, "commits": 0}

    def add(self, level, rule, where):
        path = where[5:] if where.startswith("hist:") else where
        path = re.sub(r":(\d+|link)$", "", path).split("@", 1)[0]
        for r, g in self.allow:
            if fnmatch.fnmatch(rule, r) and fnmatch.fnmatch(path, g):
                self.allowed += 1
                return
        self.hits.append({"level": level, "rule": rule, "where": where})

    def check_name(self, rel, size, where=None):
        base = os.path.basename(rel)
        rep = where or rel
        if (base == ".env" or base.startswith(".env.")) and base not in ENV_OK:
            self.add("FAIL", "file.env", rep)
            return False
        if KEY_FILE_RE.search(rel):
            self.add("FAIL", "file.key", rep)
            return False
        if SEALED_RE.search(rel):
            self.add("WARN", "file.sealed-or-answer", rep)
            return False
        if size >= FAIL_SIZE:
            self.add("FAIL", "file.too-big", rep)
            return False
        if size >= WARN_SIZE:
            self.add("WARN", "file.big", rep)
        if DB_FILE_RE.search(rel):
            self.add("WARN", "file.db", rep)
        if PATH_NO_RE.search(rel) and (self.internal_all or glob_hit(rel, SCOPE_GLOBS)):
            self.add("WARN", "internal.decision-no-in-path", rep)
        if base == ".DS_Store":
            self.add("WARN", "file.ds-store", rep)
        return True

    def check_text(self, where_prefix, text, rel=None):
        rel = rel if rel is not None else where_prefix
        in_scope = self.internal_all or rel.startswith("commit-msg") or (
            glob_hit(rel, SCOPE_GLOBS) and not (glob_hit(rel, UI_GLOBS) and UI_TEST_RE.search(rel)))
        itext = strip_ui_comments(text) if (glob_hit(rel, UI_GLOBS) and UI_EXT.search(rel)) else text
        ilines = itext.splitlines()
        for n, line in enumerate(text.splitlines(), 1):
            w = "%s:%d" % (where_prefix, n)
            self.check_line(w, line[:4000])
            iline = ilines[n - 1] if n - 1 < len(ilines) else ""
            self.check_internal(w, iline[:4000], in_scope)

    def check_internal(self, w, line, in_scope):
        rules = list(INTERNAL_RULES) + [("internal.session-name", rx) for _, rx in self.names]
        for rule, rx in rules:
            k = len(rx.findall(line))
            if not k:
                continue
            if in_scope:
                lvl = "WARN" if rule in ("internal.dev-tag", "internal.report-no") else "FAIL"
                for _ in range(k):
                    self.add(lvl, rule, w)
            else:
                self.outside[rule] = self.outside.get(rule, 0) + k

    def check_line(self, w, line):
        for rule, rx in SECRET_RULES:
            for _ in rx.finditer(line):
                self.add("FAIL", rule, w)
        for m in ASSIGN_RE.finditer(line):
            val = m.group(2)
            if PLACEHOLDER_RE.match(val) or any(r.search(val) for _, r in SECRET_RULES):
                continue
            strong = looks_strong(val)
            self.add("FAIL" if strong else "WARN", "secret.assign" if strong else "secret.assign-weak", w)
        for m in URL_CRED_RE.finditer(line):
            pw = m.group(3)
            if PLACEHOLDER_RE.match(pw):
                continue
            self.add("FAIL" if looks_strong(pw) else "WARN", "secret.url-credential", w)
        for m in HOME_RE.finditer(line):
            name = m.group(1)
            if self.user and name == self.user:
                self.add("FAIL", "personal.home-path", w)
            elif m.group(0).startswith("/Users/") and name != "Shared":
                self.add("WARN", "personal.home-path-other", w)
        for _ in WIN_HOME_RE.finditer(line):
            self.add("WARN", "personal.home-path-other", w)
        if self.user and len(self.user) >= 4 and re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(self.user), line):
            self.add("FAIL", "personal.user-name", w)
        for gid in self.git_ids:
            if gid and gid in line:
                self.add("FAIL", "personal.git-identity", w)
        for m in EMAIL_RE.finditer(line):
            local, dom = m.group(1), m.group(2)
            if EMAIL_OK_DOMAINS.search(dom) or EMAIL_OK_LOCAL.match(local) or re.match(r"^\d", dom):
                continue
            if m.group(0) in self.git_ids:
                continue
            self.add("WARN", "personal.email", w)
        if LOCAL_TOOL_RE.search(line):
            self.add("WARN", "personal.local-tool-path", w)
        for m in IPV4_RE.finditer(line):
            kind = is_ip_kind(m.group(1))
            if kind == "public":
                self.add("FAIL", "server.public-ip", w)
            elif kind == "private":
                self.add("WARN", "server.internal-ip", w)
        if AWS_HOST_RE.search(line):
            self.add("FAIL", "server.ec2-host", w)
        elif AWS_GENERIC_RE.search(line):
            self.add("WARN", "server.aws-domain", w)
        if VPS_HOST_RE.search(line):
            self.add("FAIL", "server.vps-host", w)
        if OPS_PORT_RE.search(line):
            self.add("WARN", "server.ops-port", w)

    def scan_file(self, rel):
        full = os.path.join(self.root, rel)
        try:
            st = os.lstat(full)
        except OSError:
            return
        if os.path.islink(full):
            self.check_line(rel + ":link", os.readlink(full))
            return
        if not os.path.isfile(full):
            return
        self.stats["files"] += 1
        if not self.check_name(rel, st.st_size):
            self.stats["content_not_read"] += 1
            return
        if st.st_size > MAX_READ:
            self.stats["skipped_binary_or_big"] += 1
            return
        with open(full, "rb") as f:
            data = f.read()
        if b"\x00" in data[:8192]:
            self.stats["skipped_binary_or_big"] += 1
            return
        self.stats["read"] += 1
        self.check_text(rel, data.decode("utf-8", "replace"))


def git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, check=False).stdout


def list_files(root):
    if os.path.isdir(os.path.join(root, ".git")):
        out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return sorted(p for p in out.decode("utf-8", "replace").split("\0") if p), True
    files = []
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in fs:
            files.append(os.path.relpath(os.path.join(d, f), root))
    return sorted(files), False


def scan_history(ck, root):
    out = git(root, "rev-list", "--all", "--objects").decode("utf-8", "replace")
    seen = {}
    for ln in out.splitlines():
        parts = ln.split(" ", 1)
        if len(parts) == 2 and parts[0] not in seen:
            seen[parts[0]] = parts[1]
    if seen:
        p = subprocess.run(["git", "-C", root, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                           input="\n".join(seen).encode(), capture_output=True, check=False)
        for ln in p.stdout.decode().splitlines():
            oid, typ, size = ln.split()
            if typ != "blob":
                continue
            path = seen[oid]
            where = "hist:%s@%s" % (path, oid[:7])
            if not ck.check_name(path, int(size), where) or int(size) > MAX_READ:
                continue
            data = subprocess.run(["git", "-C", root, "cat-file", "blob", oid], capture_output=True, check=False).stdout
            if b"\x00" in data[:8192]:
                continue
            ck.stats["history_blobs"] += 1
            ck.check_text(where, data.decode("utf-8", "replace"), rel=path)
    log = git(root, "log", "--all", "--format=%H%x00%ae%x00%ce%x00%an%x00%B%x01").decode("utf-8", "replace")
    authors = set()
    for rec in log.split("\x01"):
        f = rec.strip("\n").split("\x00")
        if len(f) < 5:
            continue
        ck.stats["commits"] += 1
        authors.update([f[1], f[2]])
        ck.check_text("commit-msg@%s" % f[0][:7], f[4])
    for a in sorted(authors):
        if "@" in a and not EMAIL_OK_DOMAINS.search(a.split("@", 1)[1]):
            ck.add("WARN", "personal.commit-author-email", "commit-author")


def check_needs(ck, root, files):
    if not any(os.path.basename(f) in ENV_OK for f in files):
        ck.add("WARN", "need.env-example", ".")
    gi = os.path.join(root, ".gitignore")
    txt = open(gi, encoding="utf-8", errors="replace").read() if os.path.isfile(gi) else ""
    if not re.search(r"(?m)^\s*/?\.env(\*|\.\*)?\s*$", txt):
        ck.add("WARN", "need.gitignore-env", ".gitignore")
    for f in files:
        if re.search(r"(?i)(readme|license|notice|attribution|^data/)", f) and os.path.isfile(os.path.join(root, f)):
            try:
                t = open(os.path.join(root, f), encoding="utf-8", errors="replace").read(MAX_READ)
            except OSError:
                continue
            if re.search(r"AI4I", t) and re.search(r"CC[ -]BY[ -]4\.0", t):
                return
    ck.add("WARN", "need.ai4i-attribution", ".")


def load_names(path):
    out = []
    if path:
        for ln in open(path, encoding="utf-8"):
            ln = ln.split("#", 1)[0].strip()
            if ln:
                out.append((ln, re.compile(r"(?<![A-Za-z0-9_\-])%s(?![A-Za-z0-9_])" % re.escape(ln))))
    return out


def load_allow(path):
    out = []
    if path and os.path.isfile(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.split("#", 1)[0].split()
            if len(ln) >= 2:
                out.append((ln[0], ln[1]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check-public.sh", description="공개 저장소 올리기 전 검사(읽기만)")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--all", action="store_true", help="내부 번호를 저장소 전체에서 FAIL로")
    ap.add_argument("--names")
    ap.add_argument("--allow")
    ap.add_argument("--json")
    ap.add_argument("--max-show", type=int, default=50, help="규칙마다 보일 자리 수(0 = 건수만)")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        print("폴더 없음", file=sys.stderr)
        return 2
    gids = []
    v = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, check=False).stdout.decode().strip()
    if v:
        gids.append(v)
    ck = Checker(root, load_names(a.names), load_allow(a.allow or os.path.join(root, ".public-check-allow")),
                 getpass.getuser(), gids, a.all)
    files, is_git = list_files(root)
    for rel in files:
        ck.scan_file(rel)
    check_needs(ck, root, files)
    if a.history:
        if is_git:
            scan_history(ck, root)
        else:
            ck.add("WARN", "need.git", ".")

    by_rule = {}
    for h in ck.hits:
        by_rule.setdefault((h["level"], h["rule"]), []).append(h)
    nfail = sum(1 for h in ck.hits if h["level"] == "FAIL")
    filekey = lambda h: re.sub(r":(\d+|link)$", "", h["where"])
    failfiles = len({filekey(h) for h in ck.hits if h["level"] == "FAIL"})
    s = ck.stats
    print("check-public · git=%s · 파일 %d · 읽음 %d · 이진/큼 %d · 내용 안 봄 %d · 이력 blob %d · 커밋 %d"
          % ("예" if is_git else "아니오", s["files"], s["read"], s["skipped_binary_or_big"], s["content_not_read"],
             s["history_blobs"], s["commits"]))
    for (lvl, rule) in sorted(by_rule, key=lambda k: (k[0] != "FAIL", k[1])):
        hs = by_rule[(lvl, rule)]
        print("%s %s %d건 · 파일 %d" % (lvl, rule, len(hs), len({filekey(h) for h in hs})))
        for h in hs[: a.max_show]:
            print("   " + h["where"])
        if len(hs) > a.max_show > 0:
            print("   … %d건 더" % (len(hs) - a.max_show))
    if ck.outside:
        print("참고(범위 밖 · FAIL 아님): " + " · ".join("%s %d" % kv for kv in sorted(ck.outside.items())))
    print("합: FAIL %d(파일 %d) · WARN %d · 허용 %d" % (nfail, failfiles, len(ck.hits) - nfail, ck.allowed))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"stats": s, "fail": nfail, "hits": ck.hits, "outside": ck.outside, "allowed": ck.allowed},
                      f, ensure_ascii=False, indent=1)
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
PY
