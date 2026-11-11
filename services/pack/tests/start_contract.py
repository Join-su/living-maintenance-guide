"""start 기대값 셈 약속(D-492 덧 34 · 013-129) — Pack 시험(PS-02 · 코드 기본 시험)과 받아 넣기 도구(--update-start-expectations)가 같이 씀.
PS-02 승인 기대값 = 옵션 일곱 모두 코드 기본(v15)으로 낸 start 출력(약속 v5 preview 덧칸 밖) — 옵션 선언 값과 상관없음(선언 값 셈은 test_options).
T1 지문(requests · fingerprint)도 같은 약속 — 셈 바꿈 후보는 도구가 start 지문만 다시 씀, judge 지문이 바뀌면 거부(D-492 덧 38 · 013-133 (나′))."""
import hashlib
import json
from pathlib import Path

CODE_DEFAULT = {"query_text": "group_direction", "score_rule": "sum", "near_min": 0.8, "base_score": 0.5, "call_threshold": 50,
                "use_manual": False, "manual_weight": 0.3, "order_signal": "similar",
                **{f"check_first_{g}": "none" for g in ("tw", "pw", "cl", "ld")}}   # 코드 기본(v15) — V9(D-484 덧 14) 선언 값 score_rule = rank_weighted 등 선언 값은 tests/fixtures/declared_options.json
ADDED = ("preview", "manual_support")   # 약속 v5 덧칸 — T1 지문은 이 칸을 뺀 나머지
FINGERPRINTS = "tests/fixtures/v15_start_judge_sha.json"   # T1 지문 = 마지막 승인 값(첫 값 v15 · e708fea) — '_rewrite' 칸 = 도구가 다시 쓴 까닭 · 요청 id · 바뀐 case 수


def code_default(rt):
    """이 Runtime의 옵션 값을 모두 코드 기본(CODE_DEFAULT)으로 — pack/ 파일 손 0(packrt는 run마다 node 선언 값을 읽음)."""
    for g in rt.doc["graphs"].values():
        for n in g["nodes"]:
            opts = (n.get("params") or {}).get("options") or {}
            for k, v in CODE_DEFAULT.items():
                if k in opts:
                    opts[k]["value"] = v
    return rt


def requests(root):
    """T1 요청 60건(이름, 입력) — start 37(c1a15-start · c1a15-order) · judge 23(c1a15-judge-demo · judge_cases)."""
    root, out = Path(root), []
    for suite in ("c1a15-start", "c1a15-order", "c1a15-judge-demo"):
        for line in (root / "pack/evals" / suite / "dataset.jsonl").read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            out.append((f"{suite}/{d['case_id']}", d["input"]))
    for line in (root / "tests/fixtures/judge_cases.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            out.append((f"judge_cases/{c['case']}", {**c["input"], "session_id": "S-1"}))
    return out


def _no_desc(d):   # D-560 Pack 덧층(후보 설명 desc)은 지문 밖 — 판정 값은 그대로 견줌
    rf = d.get("reason_facts") if isinstance(d, dict) else None
    if not isinstance(rf, dict) or not rf.get("alternatives"):
        return d
    return {**d, "reason_facts": {**rf, "alternatives": [{k: v for k, v in a.items() if k != "desc"} for a in rf["alternatives"]]}}


def fingerprint(out):
    return hashlib.sha256(json.dumps({k: (_no_desc(v) if k in ("decision", "preview") else v) for k, v in out.items() if k not in ADDED},
                                     ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def start_output(rt, request):
    """start 출력에서 기대값과 견주는 칸(약속 v5 preview 덧칸은 기대값 밖)."""
    return {k: v for k, v in rt.run(request)["output"].items() if k != "preview"}
