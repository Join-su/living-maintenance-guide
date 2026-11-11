"""배포할 Pack 버전을 git에서 꺼내 읽기 전용 사본으로 둔다.

- Pack 저장소는 읽기만 한다: `git rev-parse`(태그·commit → commit), `git archive <commit> pack releases`
- 사본: packs_dir/<저장소 이름>-<commit 12자>/{pack,releases}. 한 번 만든 사본은 바꾸지 않고, 파일은 쓰기 권한을 뺀다
- 실행 설정은 사본 밖(data_dir/execution-config.yaml)에 서버가 만든다(명세 v0.5 3.4 형식, 비밀 값 없음).
  release_roots는 사본의 releases, environment.python은 이 서버의 Python(packrt를 같은 프로세스에서 부르므로).
  store 칸은 config/*.yaml의 execution을 그대로 옮긴다(PostgreSQL backend의 connection·embedding 포함, 검사는 execution.py)"""
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path


class PackSourceError(RuntimeError):
    pass


@dataclass
class PackCopy:
    repo: Path
    ref: str
    commit: str
    tag: str | None
    root: Path
    pack_dir: Path
    releases_dir: Path


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise PackSourceError(f"git {' '.join(args)} 실패: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return r.stdout


def resolve(repo, ref):
    commit = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    try:
        tag = _git(repo, "describe", "--exact-match", "--tags", commit).decode().strip() or None
    except PackSourceError:
        tag = None
    return commit, tag


def _readonly(root):
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            p = os.path.join(dirpath, name)
            os.chmod(p, os.stat(p).st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        os.chmod(dirpath, os.stat(dirpath).st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def materialize(repo, ref, packs_dir, pack_subdir="pack", releases_subdir="releases"):
    repo, packs_dir = Path(repo), Path(packs_dir)
    commit, tag = resolve(repo, ref)
    root = packs_dir / f"{repo.name}-{commit[:12]}"
    if not root.exists():
        packs_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=packs_dir))
        try:
            data = _git(repo, "archive", "--format=tar", commit, pack_subdir, releases_subdir)
            tar_path = tmp / ".archive.tar"
            tar_path.write_bytes(data)
            with tarfile.open(tar_path) as tf:
                tf.extractall(tmp, filter="data")
            tar_path.unlink()
            _readonly(tmp)
            os.chmod(tmp, 0o755)   # 이름을 바꾸려면 맨 위 폴더는 잠시 쓸 수 있어야 함
            os.rename(tmp, root)
            _readonly(root)
        except BaseException:
            for dirpath, _, _ in os.walk(tmp):
                os.chmod(dirpath, 0o755)
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return PackCopy(repo=repo, ref=ref, commit=commit, tag=tag, root=root, pack_dir=root / pack_subdir, releases_dir=root / releases_subdir)


def _scalar(v):
    if isinstance(v, bool) or v is None:
        raise PackSourceError(f"실행 설정 값으로 쓸 수 없습니다: {v!r}")
    s = str(v)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _flow(d):
    if isinstance(d, list):   # R12 R-1: store releases 목록 → 흐름 sequence(따옴표 문자열)
        return "[" + ", ".join(_scalar(x) for x in d) + "]"
    return "{" + ", ".join(f"{k}: {_flow(v) if isinstance(v, (dict, list)) else _scalar(v)}" for k, v in d.items()) + "}"


def write_execution_config(path, copy, execution, extra_roots=None):
    """명세 v0.5 3.4 실행 설정(config_version 1). packrt strict YAML이 읽는 모양(따옴표 문자열, 흐름 mapping)으로 쓴다.
    extra_roots(R12 R-2): Pack 사본 releases 다음에 찾는 곳(이 배포의 쓰기 폴더 — R12가 만든 version). 없으면 지금과 같은 바이트."""
    lines = ["# Deployment Server가 만든 실행 설정 — 비밀 값 없음. 손으로 고치지 않는다(config/*.yaml의 execution을 고친다)",
             "config_version: 1",
             f"entrypoint: {_scalar(execution['entrypoint'])}",
             f"environment: {{kind: local-python, python: {_scalar(sys.executable)}}}",
             f"release_roots: [{', '.join(_scalar(r) for r in [copy.releases_dir, *(extra_roots or [])])}]",
             "stores:" + ("" if execution.get("stores") else " {}")]
    for name, b in (execution.get("stores") or {}).items():
        lines.append(f"  {name}: {_flow(b)}")
    lines.append("models:" + ("" if execution.get("models") else " {}"))
    for name, b in (execution.get("models") or {}).items():
        lines.append(f"  {name}: {_flow(b)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
