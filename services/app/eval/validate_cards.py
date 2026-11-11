#!/usr/bin/env python3
"""평가 카드 검사.

검사 대상(묶음마다 — eval/cards/val · eval/cards/test, EV-001 CH-10): cards.jsonl (작성자 결과), card_inputs.json (작성자에게 준 알람 입력),
card_udi_map.json (카드 ↔ 원본 행, 작성자에게는 주지 않음). 분할 몫: 카드 원본 행 ⊂ val ∪ test(generation/split).

실행: python3 eval/validate_cards.py [val|test ...]   (기본 = 두 묶음, 실패가 있으면 종료 코드 1)
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETS = ("val", "test")
SPLIT = ROOT / "generation/split/ai4i-split-by-udi.csv"
SECTIONS = ROOT / "data/manuals/manual_sections.json"
HISTORY = ROOT / "data/history/maintenance_history.jsonl"

CAUSE_NAMES = {
    "TW-1": "공구 수명 도래", "TW-2": "공구 날 손상", "TW-3": "마모 카운터 미초기화", "TW-4": "공구 편마모",
    "PW-1": "회전 조건 설정 오류", "PW-2": "전원 단자 이완", "PW-3": "엔코더 신호 이상",
    "CL-1": "냉각 필터 막힘", "CL-2": "냉각 팬 열화", "CL-3": "냉각수 부족", "CL-4": "냉각 설정 오류",
    "LD-1": "절삭유 노즐 막힘", "LD-2": "스핀들 베어링 열화", "LD-3": "절삭 조건 과다",
}


def check(sname):
    d = ROOT / "eval/cards" / sname
    errors = []
    cards = [json.loads(l) for l in (d / "cards.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    inputs = json.loads((d / "card_inputs.json").read_text(encoding="utf-8"))
    udi_map = json.loads((d / "card_udi_map.json").read_text(encoding="utf-8"))
    sections = json.loads(SECTIONS.read_text(encoding="utf-8"))
    checks = {}
    for s in sections:
        if s["kind"] == "check":
            checks.setdefault(s["group"], {})[s["id"]] = s

    if len(cards) != len(inputs):
        errors.append(f"카드 수 {len(cards)} ≠ 입력 {len(inputs)}")
    if [c.get("card_id") for c in cards] != [i["card_id"] for i in inputs]:
        errors.append("카드 ID 순서가 입력과 다름")

    types = Counter(c["hidden_cause"]["type"] for c in cards)
    if types != Counter({"manual_cause": 22, "none": 5, "out_of_graph": 3}):
        errors.append(f"정답 유형 건수 {dict(types)}")
    amb = sum(1 for c in cards if c.get("memo_style") == "ambiguous")
    if not 4 <= amb <= 6:
        errors.append(f"ambiguous {amb}건(4~6건이어야 함)")

    seen_memos = {}
    for c, inp in zip(cards, inputs):
        cid, g = c["card_id"], inp["symptom_group"]["code"]
        hc, res = c["hidden_cause"], c["check_results"]
        group_checks = checks[g]
        if set(res) != set(group_checks):
            errors.append(f"{cid}: 점검 키 {sorted(res)} ≠ {sorted(group_checks)}")
            continue
        unable = [k for k, v in res.items() if v["result"] == "확인 불가"]
        if len(unable) > 1:
            errors.append(f"{cid}: 확인 불가 {len(unable)}개")
        if hc["type"] == "manual_cause":
            code = hc.get("code")
            if not code or not code.startswith(g):
                errors.append(f"{cid}: 원인 코드 {code}가 증상군 {g} 밖")
                continue
            decisive = {k for k, s in group_checks.items() if s["cause"] == code}
            for k, v in res.items():
                want = "이상" if k in decisive else None
                if want and v["result"] != want:
                    errors.append(f"{cid}: 결정 점검 {k}가 {v['result']}")
                if not want and v["result"] == "이상":
                    errors.append(f"{cid}: 정답 밖 점검 {k}가 이상")
            if set(unable) & decisive:
                errors.append(f"{cid}: 결정 점검에 확인 불가")
            if c["restart"] != {"after_fix": "경계 이내", "without_fix": "여전히 밖"} or c["expected_outcome"] != "원인 확정":
                errors.append(f"{cid}: manual_cause의 재가동·기대 결과 불일치")
        else:
            if any(v["result"] == "이상" for v in res.values()):
                errors.append(f"{cid}: {hc['type']}인데 이상 결과 있음")
            if hc["type"] == "none" and (c["restart"]["without_fix"] != "경계 이내" or c["expected_outcome"] != "이상 없음·감시"):
                errors.append(f"{cid}: none의 재가동·기대 결과 불일치")
            if hc["type"] == "out_of_graph" and (c["restart"]["without_fix"] != "여전히 밖" or c["expected_outcome"] != "숙련자 인계"):
                errors.append(f"{cid}: out_of_graph의 재가동·기대 결과 불일치")
        for k, v in res.items():
            m = v["memo"].strip()
            for name in CAUSE_NAMES.values():
                if name in m:
                    errors.append(f"{cid} {k}: 메모에 원인 이름 '{name}'")
            if m in seen_memos and seen_memos[m] != cid:
                errors.append(f"{cid} {k}: 메모가 {seen_memos[m]}와 같음")
            seen_memos.setdefault(m, cid)

    hist_udi = {json.loads(l)["source"]["udi"] for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()}
    overlap = set(udi_map.values()) & hist_udi
    if overlap:
        errors.append(f"이력과 겹치는 원본 행 {len(overlap)}건")
    if set(udi_map) != {c["card_id"] for c in cards}:
        errors.append("UDI 표와 카드 ID 불일치")
    with open(SPLIT, encoding="utf-8") as f:
        split = {r["UDI"]: r["split"] for r in csv.DictReader(f)}
    off = sorted(u for u in udi_map.values() if split.get(u) not in ("val", "test"))
    if off:
        errors.append(f"val · test 몫 밖 원본 행 {len(off)}건")
    exc = (json.loads((d / "selection.json").read_text(encoding="utf-8")).get("exception") or {}) if (d / "selection.json").is_file() else {}
    allowed_cross = set(exc.get("to_val", [])) if sname == "val" else set()   # D-246: val만 test 구간 56 행(예외 기록)을 받음
    if exc:   # 예외 기록 자체 검사: 행이 그 구간의 test 몫인지 · to_val + to_test = 전부(겹침 0) · 두 묶음 기록이 같은지
        with open(SPLIT, encoding="utf-8") as f:
            segs = {r["UDI"]: int(r["segment"]) for r in csv.DictReader(f)}
        rows_ = exc.get("cl_alarm_rows", [])
        if any(split.get(u) != "test" or segs.get(u) != exc.get("segment") for u in rows_):
            errors.append("예외 기록 행이 그 구간의 test 몫이 아님")
        if sorted(exc.get("to_val", []) + exc.get("to_test", [])) != sorted(rows_) or set(exc.get("to_val", [])) & set(exc.get("to_test", [])):
            errors.append("예외 기록 to_val · to_test가 행 목록과 맞지 않음")
        other = ROOT / "eval/cards" / ("test" if sname == "val" else "val") / "selection.json"
        if other.is_file() and json.loads(other.read_text(encoding="utf-8")).get("exception") != exc:
            errors.append("val · test selection.json의 예외 기록이 다름")
    cross = sorted(u for u in udi_map.values() if split.get(u) in ("val", "test") and split.get(u) != sname and u not in allowed_cross)
    if cross:
        errors.append(f"다른 몫 원본 행 {len(cross)}건(예외 기록 밖)")

    codes = Counter(c["hidden_cause"].get("code", c["hidden_cause"]["type"]) for c in cards)
    print(f"[{sname}] 정답 유형:", dict(types), "| ambiguous:", amb)
    print(f"[{sname}] 원인 코드별:", dict(sorted(codes.items())))
    if errors:
        print(f"[{sname}] 실패 {len(errors)}건:")
        for e in errors:
            print(" -", e)
        return False
    print(f"[{sname}] 통과: 형식·건수·일관성·메모·이력 분리·분할 몫")
    return True


def main():
    names = sys.argv[1:] or list(SETS)
    sys.exit(0 if all([check(n) for n in names]) else 1)


if __name__ == "__main__":
    main()
