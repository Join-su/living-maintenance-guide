"""시연 알람 발생기(P-07, B-10 · FLOW-01): 각본 행의 센서 값을 알람 등록 API에 보낸다.

- 각본: data/demo/scenarios.json(R11). 시각은 서버가 등록 때 현재 시각으로 정한다(AC-34-C1).
- API로만 등록한다(DB 직접 쓰기 없음). 주소는 같은 호스트 · compose 내부만(외부 호출 0), 토큰은 환경 변수.
- 등록이 실패하면 오류를 보이고 종료 코드 1(FLOW-01: 다시 실행).
- ML 길 알람 없음 · ML 유형 · Pack 후보(응답 alarm_id 없음, D-529)는 실패가 아님
  — 한 줄 찍고 다음 행, 끝에 요약 한 줄(EV-001 CH-26).
실행: python -m c1a15.alarms DEMO-01 [DEMO-02 ...] | --all | --list
     python -m c1a15.alarms --stdin [--eval]  — 표준 입력 JSON 줄마다 알람 하나(equipment_id · sensor_snapshot · trend
     · data_lineage · product_type),
     --eval이면 평가 모드 알람(P-16 평가 실행기, 이력 추가 끔). 결과는 줄마다 JSON. 이 CLI는 파일을 읽지 않는다.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

LOCAL_HOSTS = {"127.0.0.1", "localhost", "api"}  # api = compose 내부 서비스 이름
DEFAULT_URL = "http://127.0.0.1:8000"


def scenarios_path() -> Path:
    if os.environ.get("C1A15_SCENARIOS"):
        return Path(os.environ["C1A15_SCENARIOS"])
    return Path(os.environ.get("DATA_DIR", "/data")) / "demo" / "scenarios.json"


def load_scenarios(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


def payload(s: dict) -> dict:
    """각본 행 그대로(AC-34-C2). 증상군 · 경계 판정은 서버가 한다."""
    return {
        "scenario_id": s["scenario_id"],
        "equipment_id": s["equipment_id"],
        "sensor_snapshot": s["sensor_snapshot"],
        "trend": s["trend"],
    }


NO_ALARM_TEXT = {
    "below_threshold": "위험도 낮음",
    "ml_type_no_block": "ML 유형 · 경계 표시 없음",
    "low_confidence": "유형 확신 낮음",  # D-529: Pack 후보도 알람 없음
    "rnf": "유형 RNF",
    "model_failure": "예측 실패",
}


def _num(v) -> str:
    return "-" if v is None else f"{v:.2f}"


def ml_text(reason: str, ml: dict | None) -> str:
    """알람 없음 줄의 ML 값: '위험도 0.12 < 문턱 0.50 · <model_release>'(ML 유형 · Pack 후보는 알람 위험도라 '≥')."""
    ml = ml or {}
    sign = "<" if reason == "below_threshold" else "≥"
    return f"위험도 {_num(ml.get('risk'))} {sign} 문턱 {_num(ml.get('threshold'))} · {ml.get('model_release') or '-'}"


def check_url(base: str) -> str:
    parts = urlsplit(base)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
        raise SystemExit(f"알람 등록 주소는 같은 호스트 · compose 내부만 허용: {base}")
    return base.rstrip("/")


def send(base: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{base}/api/internal/alarms",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def from_stdin(*, eval_mode: bool) -> int:
    """P-16: 평가 실행기가 넘긴 알람 입력(고장 라벨 없음)을 그대로 등록한다. 응답은 줄마다 JSON."""
    base = check_url(os.environ.get("C1A15_API_URL", DEFAULT_URL))
    token = os.environ.get("C1A15_INTERNAL_TOKEN", "")
    if not token:
        print("환경 변수 C1A15_INTERNAL_TOKEN 이 필요합니다", file=sys.stderr)
        return 2
    for line in sys.stdin:
        if not line.strip():
            continue
        item = json.loads(line)
        keys = (
            "equipment_id",
            "sensor_snapshot",
            "trend",
            "data_lineage",
            "product_type",
        )  # 출처 · 제품 유형 = deploy 길(CH-37)
        body = {k: item[k] for k in keys if k in item}
        body["eval_mode"] = eval_mode
        try:
            r = send(base, token, body)
        except (urllib.error.URLError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError):  # 서버 거부 이유(detail)만 — 요청 본문 · 토큰은 찍지 않음
                code = f"{e.code} {json.loads(e.read() or b'{}').get('detail')}"
            else:
                code = type(e).__name__
            print(json.dumps({"ref": item.get("ref"), "error": code}, ensure_ascii=False), flush=True)
            continue
        print(json.dumps({"ref": item.get("ref"), **r}, ensure_ascii=False), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1a15.alarms")
    ap.add_argument("scenario_ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--stdin", action="store_true", help="표준 입력 JSON 줄마다 알람 하나")
    ap.add_argument("--eval", action="store_true", help="평가 모드 알람(--stdin과 함께)")
    args = ap.parse_args(argv)
    if args.stdin:
        return from_stdin(eval_mode=args.eval)
    items = load_scenarios(scenarios_path())
    if args.list:
        for s in items:
            print(s["scenario_id"], s["equipment_id"])
        return 0
    by_id = {s["scenario_id"]: s for s in items}
    chosen = items if args.all else [by_id[i] for i in args.scenario_ids if i in by_id]
    unknown = [i for i in args.scenario_ids if i not in by_id]
    if unknown or not chosen:
        print(f"각본에 없는 ID: {unknown or '(지정 없음)'} — --list로 확인", file=sys.stderr)
        return 2
    base = check_url(os.environ.get("C1A15_API_URL", DEFAULT_URL))
    token = os.environ.get("C1A15_INTERNAL_TOKEN", "")
    if not token:
        print("환경 변수 C1A15_INTERNAL_TOKEN 이 필요합니다", file=sys.stderr)
        return 2
    n = {"alarm": 0, "none": 0}
    ml = None  # 요약의 model · 문턱 = 받은 ML 값(알람 응답에는 없음 — 하나도 없으면 '-')
    for s in chosen:
        try:
            r = send(base, token, payload(s))
        except (urllib.error.URLError, OSError) as e:
            detail = e.read().decode() if isinstance(e, urllib.error.HTTPError) else str(e)
            print(f"{s['scenario_id']} 등록 실패: {detail} — 다시 실행하세요", file=sys.stderr)
            return 1
        # ML 길 알람 없음 · ML 유형 · Pack 후보(EV-001 CH-26 · D-529) — 멈추지 않고 다음 행
        if r.get("alarm_id") is None:
            n["none"] += 1
            # 예측 실패(ML 값 없음)는 요약 값을 지우지 않음
            ml = r["ml"] if (r.get("ml") or {}).get("model_release") else ml
            print(
                f"{s['scenario_id']} 알람 없음 · {NO_ALARM_TEXT.get(r['reason'], r['reason'])}"
                f" · {ml_text(r['reason'], r.get('ml'))}"
            )
            continue
        n["alarm"] += 1
        print(
            f"{s['scenario_id']} 알람 {r['alarm_id']} 등록 · 배정 {r['assigned_to'] or '미배정'} · {r['occurred_at']}"
        )
    print(
        f"요약: 알람 {n['alarm']} / 각본 {len(chosen)} · 알람 없음 {n['none']}"
        f" · model {(ml or {}).get('model_release') or '-'} · 문턱 {_num((ml or {}).get('threshold'))}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
