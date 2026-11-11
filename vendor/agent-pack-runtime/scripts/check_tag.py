"""Release tag check for CI (agent-pack-runtime, instruction 005): the pushed tag follows the repository tag rule (README '판 · 태그 규칙').

  python scripts/check_tag.py <tag> [--repo DIR]

- the tag name is v<major>.<minor>.<patch>
- it is an annotated tag (a tag object, not a lightweight ref), and it points at the commit checked out (HEAD)
- its message's first line names both package versions as they are in the code at that commit:
  "… packrt <packrt.__version__> · tracewriter <tracewriter.__version__>"
Reads git objects and two source files only (no import, no network). Exit 0 = ok, 1 = rule broken, 2 = usage / not found."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

NAME = re.compile(r"^v\d+\.\d+\.\d+$")
VERSION = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def version_at(repo, commit, path):
    out = git(repo, "show", f"{commit}:{path}")
    m = VERSION.search(out.stdout) if out.returncode == 0 else None
    return m.group(1) if m else None


def check(repo, tag):
    problems = []
    if not NAME.match(tag):
        problems.append(f"태그 이름 {tag!r}이 v<판> 모양이 아닙니다")
    kind = git(repo, "cat-file", "-t", f"refs/tags/{tag}")
    if kind.returncode != 0:
        return 2, [f"태그 {tag}가 없습니다"]
    if kind.stdout.strip() != "tag":
        problems.append(f"태그 {tag}가 주석 태그가 아닙니다({kind.stdout.strip()}) — 태그는 주석 태그로만 찍습니다")
        return 1, problems
    commit = git(repo, "rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if commit != head:
        problems.append(f"태그 {tag}의 commit {commit[:12]}이 받은 commit {head[:12]}과 다릅니다")
    first = git(repo, "for-each-ref", "--format=%(contents:subject)", f"refs/tags/{tag}").stdout.strip()
    for pkg in ("packrt", "tracewriter"):
        v = version_at(repo, commit, f"{pkg}/{pkg}/__init__.py")
        if v is None:
            problems.append(f"{pkg}/{pkg}/__init__.py에서 __version__을 찾지 못했습니다")
        elif not re.search(rf"\b{pkg} {re.escape(v)}(?![\w.])", first):
            problems.append(f"태그 메시지 첫 줄에 '{pkg} {v}'가 없습니다: {first!r}")
    return (1 if problems else 0), problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--repo", default=Path(__file__).resolve().parents[1])
    a = ap.parse_args(argv)
    code, problems = check(a.repo, a.tag)
    for p in problems:
        print(p)
    if code == 0:
        print(f"태그 {a.tag}: 규칙대로입니다(주석 태그 · 받은 commit · 메시지의 두 패키지 판)")
    return code


if __name__ == "__main__":
    sys.exit(main())
