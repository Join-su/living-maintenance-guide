#!/usr/bin/env python3
"""P-16 평가 실행기(FLOW-12): 평가 카드 30건을 평가 모드로 돌려 지표 6개를 목표와 함께 보고한다.

AC-18 · AC-02-C3 · AC-15.
- 평가 카드(eval/cards)를 여는 유일한 코드다.
  진단 처리에는 카드의 알람 입력(고장 라벨 없음)과 점검 결과 · 메모만 API로 돌려준다.
- 알람 등록: host 알람 발생기 CLI(server/.venv, --stdin --eval)에 표준 입력으로 넘긴다(EV-001 CH-37).
  카드 출처 칸(source_release · row_id · split)은 이 실행기만 card_udi_map.json에서 읽어 붙인다(S3-Q9).
  평가 폴더는 제품 코드 · 컨테이너가 읽지 않는다.
- 정비자 처리 = 공개 API(EVAL_TECH_URL), 파트장 답 = 파트장 API(EVAL_LEAD_URL). 사람 판단은 사용자 결정 020 ③ 고정 규칙.
- fixture 모드 · 격리된 시험 스택에서만 쓴다(scripts/eval-run). live 평가는 별도 승인 행동.
- 실패 메시지는 메서드 · 경로 · 상태 코드 · 서버 거부 이유만 찍는다(요청 본문 · 비밀번호 없음).
실행: scripts/eval-run (직접 실행: python3 scripts/eval_run.py --out 보고서.json)
"""

import argparse
import functools
import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_metrics  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CARDS, INPUTS = ROOT / "eval/cards/val/cards.jsonl", ROOT / "eval/cards/val/card_inputs.json"  # 작업 묶음 val 30(EV-001 CH-10) — test 30 숨김 모드는 ES-05
# 카드 출처(lineage) = 카드 → AI4I 행 번호(EV-001 CH-37, S3-Q9 c1a15-eval 003: 이 실행기만 읽음 · 본문엔 세 칸만 · test는 한 번 규칙)
UDI_MAP, SPLIT = ROOT / "eval/cards/val/card_udi_map.json", "val"
SECTIONS = ROOT / "data/manuals/manual_sections.json"
TECHS = ("tech.kim", "tech.lee", "tech.choi", "tech.jung")
TREND_KEY = {"온도 차": "temp_diff_k", "기계 출력": "power_w", "공구 마모 시간": "tool_wear_min", "토크": "torque_nm"}
RESTART = {"경계 이내": "within", "여전히 밖": "outside"}
EXPECTED = {"원인 확정": "confirmed", "이상 없음·감시": "monitor", "숙련자 인계": "unknown"}
MAX_STEPS = 80


class ApiError(RuntimeError):
    pass


def client(base: str):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with op.open(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = json.loads(e.read() or b"{}").get("detail")
            raise ApiError(f"{method} {path} -> {e.code} {detail if isinstance(detail, str) else ''}") from None

    return call


@functools.cache
def source_release() -> str:
    """AI4I 원본 sha256(data/manifest.json) — 앱 deploy.runs.source_release와 같은 값."""
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    return f"ai4i-2020@sha256:{manifest['source']['sha256']}"


def alarm_input(inp: dict, udis: dict | None = None) -> dict:
    """카드 알람 입력 → 등록 본문(설비 · 숫자 센서 값 · 알람 센서 직전 측정 · 출처). 고장 라벨 · 증상군은 넘기지 않는다."""
    key = TREND_KEY[inp["alarm"]["sensor"]]
    udis = udis if udis is not None else json.loads(UDI_MAP.read_text(encoding="utf-8"))
    return {
        "ref": inp["card_id"],
        "equipment_id": inp["equipment_id"],
        "sensor_snapshot": {k: v for k, v in inp["sensor_snapshot"].items() if isinstance(v, (int, float))},
        "trend": [{"at": p["at"], "value": p[key]} for p in inp["recent_12"]],
        "data_lineage": {"source_release": source_release(), "row_id": int(udis[inp["card_id"]]), "split": SPLIT},
    }


def register(inputs: list[dict]) -> dict:
    udis = json.loads(UDI_MAP.read_text(encoding="utf-8"))  # 한 번만 읽음(ES-16 N-10)
    lines = "".join(json.dumps(alarm_input(i, udis), ensure_ascii=False) + "\n" for i in inputs)
    out = subprocess.run(  # host 발생기(앱 서버와 같은 환경 — scripts/eval-run이 넘김, EV-001 CH-37)
        [str(ROOT / "server" / ".venv" / "bin" / "python"), "-m", "c1a15.alarms", "--stdin", "--eval"],
        input=lines,
        capture_output=True,
        text=True,
        cwd=ROOT / "server",
        env={**os.environ, "PYTHONPATH": "src"},
        check=True,
    ).stdout
    return {r["ref"]: r for r in (json.loads(x) for x in out.splitlines() if x.strip())}


class Run:
    def __init__(self, tech_url: str, lead_url: str):
        self.techs = {u: client(tech_url) for u in TECHS}
        for u, t in self.techs.items():
            t("POST", "/api/login", {"user_id": u, "password": os.environ["C1A15_TECH_PASSWORD"]})
        self.lead = client(lead_url)
        self.lead("POST", "/api/login", {"user_id": "lead.park", "password": os.environ["C1A15_LEAD_PASSWORD"]})

    def answer_call(self, rec: dict) -> None:
        """결정 020 ③: 파트장 호출 = 승인 후 진행(D면 주간 처리), 숙련자 인계 = 주간 숙련자 배정."""
        rows = self.lead("GET", "/api/lead/calls")["rows"]  # 카드를 하나씩 돌려 열린 호출은 이 세션 것 하나뿐
        if len(rows) != 1:
            raise ApiError(f"열린 호출 {len(rows)}건")
        row = rows[0]
        d = self.lead("GET", f"/api/lead/calls/{row['call_id']}")
        opts = [o["value"] for o in d["options"]]
        ans = "assign" if "assign" in opts else ("approved" if "approved" in opts else "daytime")
        self.lead("POST", f"/api/lead/calls/{row['call_id']}/answer", {"answer": ans})
        rec["answers"].append(ans)

    def card(self, card: dict, inp: dict, reg: dict, manual: eval_metrics.Manual) -> dict:
        rec = {
            "card_id": card["card_id"],
            "group": inp["symptom_group"]["code"],
            "hidden": card["hidden_cause"],
            "expected_outcome": card["expected_outcome"],
            "calls": 0,
            "answers": [],
            "violations": 0,
            "citations": [],
            "performed_sections": [],
            "remaining_candidate_sections": [],
            "check_minutes_until_confirm": [],
            "candidate_names": [],
            "path": [],
        }
        if "error" in reg:
            raise ApiError(f"알람 등록 {reg['error']}")
        rec["registered_group"] = reg["symptom_group"]
        tech = self.techs[reg["assigned_to"]]

        def ev(s, **b):
            return tech("POST", f"/api/tech/sessions/{s['session_id']}/events", {"seq": s["last_seq"] + 1, **b})

        s = tech("POST", f"/api/tech/alarms/{reg['alarm_id']}/session")
        rec["session_id"] = s["session_id"]
        rec["initial_probabilities"] = s["probabilities"]
        rec["similar_top3_causes"] = [c["cause_code"] for c in s["similar_cards"][:3]]
        rec["candidate_names"] = [s["cause_names"][c] for c in s["probabilities"] if c in s["cause_names"]]
        results, asked, actions, confirmed, sent, counted = card["check_results"], set(), [], False, {}, set()
        self.current = rec
        perf = rec["performed_sections"]
        s = ev(s, type="alarm_ack")  # EV-007: 알람 화면 확인 → 잠금
        s = ev(s, type="lock")
        perf.append("1.2")
        for _ in range(MAX_STEPS):
            st = s["step"]
            k = st["kind"]
            rec["path"].append(k + (f" {st.get('section')}" if st.get("section") else ""))
            if k == "precheck":
                perf.append(st["section"])
                s = ev(s, type="precheck_done", values=card["precheck_memo"])
            elif k in ("instruct", "instruct_after_checklist"):
                ins, sec = s["instruction"], st["section"]
                if k == "instruct_after_checklist" and not ins.get("checklist_done"):
                    s = ev(s, type="b_checklist_done", section=sec, checked=ins["checklist"])
                    continue
                perf.append(sec)
                rec["citations"] += [
                    (x, "instruction") for line in ins.get("steps", []) for x in eval_metrics.cited_sections(line)
                ]
                if (
                    not confirmed and sec not in counted
                ):  # 결정 020 ⑭: 점검 절마다 한 번(결과 다시 입력은 점검을 다시 하는 것이 아님)
                    counted.add(sec)
                    rec["check_minutes_until_confirm"].append(ins["minutes"])
                want = results[sec]
                body = {"value": want["result"]} if sec in asked else {"value": want["result"], "memo": want["memo"]}
                s = ev(s, type="result", section=sec, **body)
                sent[sec] = want["result"]
            elif k == "confirm_question":  # 해석이 카드 결과와 같으면 '네', 아니면 '아니요'(다시 물으면 메모 없이)
                sec = st["section"]
                asked.add(sec)
                s = ev(s, type="confirm_answer", yes=st.get("interpretation") == results[sec]["result"])
            elif k == "out_of_scope":
                s = ev(s, type="out_of_scope_choice", choice="continue")
            elif k in ("action", "action_after_checklist"):
                confirmed = True
                a = s["action"]
                if k == "action_after_checklist" and not a.get("checklist_done"):
                    try:
                        s = ev(s, type="b_checklist_done", section=a["section"], checked=a["checklist"])
                        continue
                    except ApiError:
                        pass  # 이미 체크한 B 시작 전 확인
                s = ev(s, type="action_done", section=a["section"])
                perf.append(a["section"])
                actions.append(a["section"])
            elif k == "restart_check":
                confirmed = True
                perf.append("6.1")
                s = ev(s, type="restart", checked=s["restart"]["checklist"])
            elif k == "restart_value":
                outcome = card["restart"]["after_fix" if actions else "without_fix"]
                s = ev(s, type="restart_result", outcome=RESTART[outcome])
            elif k == "monitor_close":
                perf.append("6.2")
                s = ev(s, type="monitor_close")
            elif k in ("call", "handover"):
                if st.get("section"):
                    perf.append(st["section"])  # 결정 020 ⑫: 호출한 절도 실제 단계
                if st.get("call_status") != "sent":
                    s = ev(s, type="call_send")
                    rec["calls"] += 1
                self.answer_call(rec)
                s = tech("GET", f"/api/tech/sessions/{s['session_id']}")
            elif k == "report":
                rec["outcome"] = st.get("outcome")
                rec["report_cause_code"] = st.get("cause")
                fields = {f["key"]: f["text"] for f in s["report"]["fields"]}
                rec["report_cause"] = fields.get("cause")
                for key, text in fields.items():
                    rec["citations"] += [
                        (x, "unresolved" if key == "unresolved" else "report")
                        for x in eval_metrics.cited_sections(text)
                    ]
                # 끝에 후보로 남은 원인 = '정상'으로 빠지지 않은 원인(결정 020 ⑫ · 015 ④) — 보낸 카드 결과로 센다
                checks = manual.checks[rec["group"]]
                excluded = {c["cause"] for c in checks if sent.get(c["id"]) == "정상"}
                rec["remaining_candidate_sections"] = [c["id"] for c in checks if c["cause"] not in excluded]
                s = ev(s, type="report_confirm")
                rows = self.lead("GET", "/api/lead/overview")[
                    "rows"
                ]  # 승인 대기 목록 위반 수와 같은 계산(결정 020 ⑧, R2)
                row = next(r for r in rows if r["session_id"] == s["session_id"])
                rec["violations"], rec["report_id"] = row["violations"], row["report_id"]
                break
            else:
                raise ApiError(f"규칙에 없는 단계 {k}")
        else:
            raise ApiError(f"{MAX_STEPS}단계 안에 보고서에 이르지 못함")
        rec["outcome_match"] = rec.get("outcome") == EXPECTED[card["expected_outcome"]] and (
            card["hidden_cause"]["type"] != "manual_cause"
            or rec.get("report_cause_code") == card["hidden_cause"]["code"]
        )
        return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tech-url", default=os.environ.get("EVAL_TECH_URL", "http://127.0.0.1:8081"))
    ap.add_argument("--lead-url", default=os.environ.get("EVAL_LEAD_URL", "http://127.0.0.1:8082"))
    args = ap.parse_args(argv)
    cards = [json.loads(x) for x in CARDS.read_text(encoding="utf-8").splitlines() if x.strip()]
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    manual = eval_metrics.Manual(json.loads(SECTIONS.read_text(encoding="utf-8")))
    run = Run(args.tech_url, args.lead_url)
    records = []
    for card, inp in zip(cards, inputs, strict=True):
        assert card["card_id"] == inp["card_id"]
        reg = register([inp])[inp["card_id"]]  # 카드마다 하나씩(앞 카드 세션이 끝난 뒤 배정)
        if "error" not in reg and reg.get("alarm_id") is None:  # ML 길 알람 없음 · 후보 — 세션 0(EV-001 CH-27)
            records.append(
                {
                    "card_id": card["card_id"],
                    "group": inp["symptom_group"]["code"],
                    "hidden": card["hidden_cause"],
                    "registered": False,
                    "reason": reg.get("reason"),
                    **({"ml": reg["ml"]} if reg.get("ml") else {}),
                }
            )
            print(f"{card['card_id']} 등록 안 됨 {reg.get('reason')}", flush=True)
            continue
        try:
            rec = run.card(card, inp, reg, manual)
        except (ApiError, KeyError, StopIteration) as e:
            partial = getattr(run, "current", None) or {}
            rec = {
                "path": partial.get("path", []) if partial.get("card_id") == card["card_id"] else [],
                "card_id": card["card_id"],
                "group": inp["symptom_group"]["code"],
                "hidden": card["hidden_cause"],
                "failed": str(e)[:200],
                "similar_top3_causes": [],
                "initial_probabilities": {},
                "check_minutes_until_confirm": [],
                "citations": [],
                "performed_sections": [],
                "remaining_candidate_sections": [],
                "candidate_names": [],
                "violations": 0,
            }
        records.append(rec)
        print(f"{card['card_id']} {'실패 ' + rec['failed'] if rec.get('failed') else rec.get('outcome')}", flush=True)
    summary = eval_metrics.compute(records, manual)
    report = {
        "artifact": "evaluation_report",
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "fixture",
        "rules": "사용자 결정 020 ③ ⑤ ~ ⑧ ⑩(.vibe-factory/runs/2026-10-11-c1a15-product-r2/sources/intent/020.md)",
        **summary,
        "records": records,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for m in summary["metrics"]:
        print(f"{m['name']}: {m['value']}{m['unit']} (목표 {m['target']}{m['unit']}, {'달성' if m['met'] else '미달'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
