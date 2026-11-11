"""V-23: 배포단 .env 확인 — 권한 600, 이름은 정확히 두 개, 값이 llmops db/.env의 같은 이름 값과 같은지(같음/다름만), git 추적 밖인지. 값은 출력하지 않는다.
사용: scripts/py scripts/check-env.py"""
import os, stat, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SRC = Path(os.environ.get("LLMOPS_DB_ENV", HERE.parent / "llmops-lite-v2" / "db" / ".env"))
WANT = ["LLMOPSAGENT_RUNTIME_DSN", "LLMOPSAGENT_TRACE_DSN"]


def parse(p):
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            out[k.strip()] = v.strip()
    return out


env = HERE / ".env"
if not env.is_file():
    print("불일치: .env가 없습니다(scripts/make-env.sh)"); sys.exit(1)
mode = stat.S_IMODE(env.stat().st_mode)
mine, src = parse(env), parse(SRC)
names_ok = sorted(mine) == sorted(WANT)
same = {n: (mine.get(n) == src.get(n) and bool(mine.get(n))) for n in WANT}
tracked = subprocess.run(["git", "-C", str(HERE), "ls-files", "--error-unmatch", ".env"], capture_output=True).returncode == 0
ignored = subprocess.run(["git", "-C", str(HERE), "check-ignore", "-q", ".env"]).returncode == 0
ok = mode == 0o600 and names_ok and all(same.values()) and not tracked and ignored
print(f"권한 {oct(mode)}({'600' if mode == 0o600 else '다름'}), 이름 {sorted(mine)}({'정확히 두 개' if names_ok else '다름'}), "
      f"값 llmops와 {', '.join(f'{n} ' + ('같음' if s else '다름') for n, s in same.items())}, git 추적 {tracked}, .gitignore {ignored}")
print("V-23:", "일치" if ok else "불일치")
sys.exit(0 if ok else 1)
