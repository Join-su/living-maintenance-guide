"""C1-a15 앱의 판정 · 알람 규칙 코드 · 매뉴얼 절을 Pack에 사본으로 옮긴다(EV-001 CH-09 · CH-13, Pack EV-001 PC-02). Pack 밖 개발 도구 — Pack 코드는 이 도구도 앱도 import하지 않는다.

  .venv/bin/python tools/sync_from_app.py <앱 저장소 경로(예: ../c1a15-maint-guide)> <앱 commit>

앱 작업 트리는 읽지 않는다(다른 세션 몫) — 정한 commit의 git 객체(`git show <commit>:<경로>`)만 읽는다.
바꾸는 것은 c1a15.engine import 줄뿐(→ custom, 파일마다 바꾼 줄 수를 기록). 출처는 pack/custom/COPIED_FROM.json(앱 commit · 원본 · 사본 sha256)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[1] / "pack" / "custom"
FILES = [("server/src/c1a15/engine/knowledge.py", "knowledge.py"),
         ("server/src/c1a15/engine/rules.py", "rules.py"),
         ("server/src/c1a15/engine/session.py", "session.py"),
         ("data/manuals/manual_sections.json", "manual_sections.json"),
         ("data/alarm_rules.json", "alarm_rules.json"),   # 예측 칸 · 경계 판정(경계 · train 표준편차, EV-001 CH-04)
         ("server/src/c1a15/engine/alarm_rules.py", "alarm_rules.py"),   # 경계 판정 detect(Pack EV-001 PC-02)
         ("server/src/c1a15/alarms/ml.py", "alarm_route.py"),   # ML 길 route(Q-1 (나)) — 문턱은 요청 settings로만 넘김(threshold()는 부르지 않음)
         ("server/src/c1a15/llm/base.py", "llm_base.py"),   # 보고서 문장 근거 검사가 쓰는 Draft 모양(PC-09)
         ("server/src/c1a15/llm/grounding.py", "report_grounding.py")]   # R8 근거 검사 is_grounded(PC-09) — checked()는 쓰지 않음(다시 쓰기는 그래프가 함)
REWRITE = [("from c1a15.engine.knowledge import", "from custom.knowledge import"),
           ("from c1a15.engine import rules", "from custom import rules"),
           ("from c1a15.llm.base import", "from custom.llm_base import")]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rewrite(text: str) -> tuple[str, int]:
    n = 0
    for a, b in REWRITE:
        n += text.count(a)
        text = text.replace(a, b)
    return text, n


def main(app: Path, ref: str) -> int:
    commit = subprocess.run(["git", "-C", str(app), "rev-parse", "--verify", ref + "^{commit}"], capture_output=True, text=True, check=True).stdout.strip()
    rows = []
    for src, dst in FILES:
        raw = subprocess.run(["git", "-C", str(app), "show", f"{commit}:{src}"], capture_output=True, check=True).stdout
        text, n = rewrite(raw.decode("utf-8")) if dst.endswith(".py") else (None, 0)
        out = text.encode("utf-8") if text is not None else raw
        if dst.endswith(".py") and b"c1a15" in out:
            print(f"{dst}: 바꾼 뒤에도 c1a15 이름이 남음 — 멈춤", file=sys.stderr)
            return 1
        (PACK / dst).write_bytes(out)
        rows.append({"source": src, "source_sha256": sha(raw), "target": f"custom/{dst}", "target_sha256": sha(out),
                     "rewrite": f"import {n}줄(c1a15.engine · c1a15.llm.base → custom)" if dst.endswith(".py") else "없음(바이트 그대로)"})
    doc = {"app_repository": "c1a15-maint-guide", "app_commit": commit, "files": rows,
           "rule": "사본은 이 도구로만 바꾼다. 앱 engine/이 바뀌면 다시 돌리고, 두 길 대조(VC-01)로 확인한다."}
    (PACK / "COPIED_FROM.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"사본 {len(rows)}개 · 앱 commit {commit[:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve(), sys.argv[2]))
