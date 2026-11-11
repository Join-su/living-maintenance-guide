#!/usr/bin/env python3
"""정비 길잡이(C1-a15) 데이터를 만든다.

만드는 것
  data/manuals/MG-MM.md            가상 정비 매뉴얼
  data/manuals/manual_sections.json  절 단위 목록(검색 색인·권한 판정용)
  data/alarm_rules.json            알람 경계(학습용 train 행 분포)와 행 배치 규칙
  data/history/maintenance_history.jsonl  학습용(train) 행의 정비 이력 300건
  data/manifest.json               입력·출력 해시와 건수

원칙
  - CSV 에서 고장 라벨 6열은 읽자마자 버린다. 원인은 generation/cause_ratio_table.json(팀 가정)으로만 뽑는다.
  - 행 고르기는 분할 파일(generation/split, D-227)로만 한다: 이력 · 알람 경계 · 평소 범위 · 정규화 = train 행(D-236 (4)). 시간으로 행을 고르지 않는다.
  - 행 시각 · 설비 번호는 지금 배치 규칙(행 순서 유지, 시작점만 옮김) 그대로 붙인다 — 화면에 닿는 시각은 다음 판(CH-02b).
  - 고정 seed 로 다시 돌려도 같은 결과가 나온다.

실행: python3 scripts/build_data.py [--csv PATH]
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path

import manual_spec as M

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT.parent / "manifact_10" / "data" / "raw" / "ai4i2020.csv"
RATIO_TABLE = ROOT / "generation" / "cause_ratio_table.json"
SPLIT_FILE = ROOT / "generation" / "split" / "ai4i-split-by-udi.csv"  # 분할 v2(D-227): UDI → segment · split, 라벨 없음

SEED = 10
N_MACHINES = 20
YEAR_START = dt.datetime(2026, 1, 1)
YEAR_MINUTES = 365 * 24 * 60
HISTORY_END = dt.datetime(2026, 10, 1)  # 배치 규칙의 기준점(더운 구간 위치)만 — 행 고르기에는 쓰지 않음(CH-02a)
TARGET_RECORDS = 300
KST = "+09:00"

FEATURES = {
    "UDI": "udi", "Type": "quality_grade",
    "Air temperature [K]": "air", "Process temperature [K]": "proc",
    "Rotational speed [rpm]": "rpm", "Torque [Nm]": "torque", "Tool wear [min]": "wear",
}
LABEL_COLUMNS = ["Machine failure", "TWF", "HDF", "PWF", "OSF", "RNF"]

# 증상군별 알람 값. 순서는 안전 위험이 큰 쪽부터(동시에 이탈하면 앞쪽이 주 증상군).
ALARM_RULES = [
    {"group": "PW", "sensor": "기계 출력", "key": "power", "unit": "W", "sides": ["low", "high"]},
    {"group": "CL", "sensor": "온도 차", "key": "temp_diff", "unit": "K", "sides": ["low"]},
    {"group": "LD", "sensor": "토크", "key": "torque", "unit": "Nm", "sides": ["high"]},
    {"group": "TW", "sensor": "공구 마모 시간", "key": "wear", "unit": "분", "sides": ["high"]},
]

NIGHT_TECHS = [("김○○", 2), ("이○○", 1), ("최○○", 3), ("정○○", 2)]
DAY_TECHS = [("강○○", 8), ("조○○", 6), ("윤○○", 3), ("장○○", 12)]
SENIOR_YEARS = 5
PART_LEAD = "박○○"
SCOPE_OUT_MINUTES = {"7.1": 30, "7.2": 180}
SCOPE_OUT_BY = {"7.1": "전기 담당", "7.2": "외부 업체"}

# 정비자 메모(교대 일지 표현). 이력 생성에만 쓴다.
NOTES = {
    "2.3": {"정상": ["카운터 값과 누적 가공 시간 차이 5분 이내", "교체 기록 뒤 초기화 기록 있음, 값 일치"],
            "이상": ["교체 기록 뒤 카운터 초기화 기록 없음, 카운터가 누적 가공 시간보다 70분 많음",
                   "카운터가 누적 가공 시간보다 50분 넘게 큼, 지난 교체 때 초기화 누락으로 보임"],
            "확인 불가": ["가공 기록 화면 접속 안 됨"]},
    "2.4": {"정상": ["날 끝 네 곳 깨짐 없음", "치핑 안 보임, 마모 띠 고름"],
            "이상": ["날 끝 한 곳 치핑 약 0.3 mm", "날 두 곳 깨짐 확인"],
            "확인 불가": ["확대경 배터리 방전, 육안으로는 판단 어려움"]},
    "2.5": {"정상": ["시험 가공 치수 공차 이내, 가공면 비교 시편 수준"],
            "이상": ["시험 가공 치수 공차 벗어남, 날 끝은 멀쩡", "가공면이 비교 시편보다 거칠고 치수 한계 근처"],
            "확인 불가": ["시험 소재 재고 없음"]},
    "2.6": {"정상": ["날 네 개 마모 띠 폭 고름, 콜릿 깨끗·흔들림 없음"],
            "이상": ["한 날만 마모 띠가 다른 날의 3배 가까이 넓음, 콜릿에 칩 끼어 공구가 흔들림", "마모 띠 폭이 날마다 크게 다름, 콜릿 홈에 미세 칩"],
            "확인 불가": ["공구 분리 렌치 없음"]},
    "3.3": {"정상": ["회전수·이송 설정 공정 표준과 같음"],
            "이상": ["회전수 설정이 표준보다 15% 높게 들어가 있음", "이송 설정이 다른 제품 값으로 남아 있음"],
            "확인 불가": ["공정 표준표 최신본 못 찾음"]},
    "3.4": {"정상": ["커넥터 단단히 꽂혀 있음, 피복 이상 없음"],
            "이상": ["엔코더 커넥터 반쯤 빠져 있음", "커넥터 헐거움, 손으로 밀면 더 들어감"],
            "확인 불가": ["뒤쪽 커버 고정 볼트 고착"]},
    "3.5": {"정상": ["최근 24시간 경고 0회", "저전압 경고 1회, 기준 미만"],
            "이상": ["저전압 경고 4회 기록", "과전류·저전압 경고 합 3회"],
            "확인 불가": ["이력 화면 오류"]},
    "3.6": {"정상": ["단자 변색·이완 없음"], "이상": ["L2 단자 변색, 체결 풀림", "단자 두 곳 이완, 주변 그을음"]},
    "4.3": {"정상": ["필터 표면 깨끗함", "먼지 약간, 표면 1/4 이하"],
            "이상": ["기름때 두껍게 낌", "필터 표면 대부분 기름때·먼지로 덮임", "먼지가 표면 절반 넘게 덮음, 색이 짙음"],
            "확인 불가": ["커버 나사 마모로 못 엶"]},
    "4.4": {"정상": ["축 유격 없음, 날개 정상"], "이상": ["팬 축 유격 큼, 회전 시 소음", "날개 한 장 깨짐, 베어링 소음"]},
    "4.5": {"정상": ["수위 MIN–MAX 중간", "수위 MAX 근처, 누설 흔적 없음"],
            "이상": ["수위 MIN 선보다 1 cm 아래", "수위 MIN 아래, 누설 흔적은 없음"],
            "확인 불가": ["수위계 창 오염으로 눈금 안 보임"]},
    "4.6": {"정상": ["팬 속도·펌프 설정 표준값과 같음"],
            "이상": ["냉각 팬 속도가 표준보다 한 단계 낮게 설정됨", "절삭유 펌프 설정이 저속으로 바뀌어 있음"],
            "확인 불가": ["설정 화면 암호 걸림"]},
    "5.3": {"정상": ["노즐 분사 줄기 고름"], "이상": ["노즐 한 개 분사 끊김", "노즐 방향 틀어져 날 끝을 벗어남"],
            "확인 불가": ["시험 운전 스위치 고장"]},
    "5.4": {"정상": ["이송·절삭 깊이 공정 표준 이내"], "이상": ["절삭 깊이가 표준보다 20% 깊게 설정됨", "이송 속도가 표준보다 15% 빠름"],
            "확인 불가": ["공정 표준표에 이 소재 조건 없음"]},
    "5.5": {"정상": ["진동 1.6 mm/s, 기준 이내", "진동 2.1 mm/s"], "이상": ["진동 3.4 mm/s, 기준 초과", "진동 3.1 mm/s, 베어링 쪽 소음"],
            "확인 불가": ["진동계 센서 불량"]},
    "5.6": {"정상": ["업체 점검 결과 베어링 정상"], "이상": ["업체 점검 결과 베어링 외륜 마모"]},
}
VERIFY = {"TW": "시험 가공 치수 정상", "PW": "출력 경계 이내", "CL": "온도 차 경계 이내로 돌아옴", "LD": "토크 경계 이내"}
UNABLE_RATE = 0.04
NIGHT_APPROVE_RATE = 0.55


# ── 공통 도구

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:00") + KST


def ro(word):
    """'~로/~으로' 조사."""
    code = ord(word[-1]) - 0xAC00
    if 0 <= code < 11172:
        jong = code % 28
        return word + ("로" if jong in (0, 8) else "으로")
    return word + "(으)로"


def section_map():
    sections = {}
    for s in M.SECTIONS:
        sections[s["id"]] = s
        if "action" in s:
            a = dict(s["action"], kind="action", parent=s["id"], group=s.get("group"))
            sections[a["id"]] = a
    return sections


SEC = section_map()


def fmt(key, v):
    if key == "power":
        return f"{v:,.0f}"
    if key == "wear":
        return f"{v:.0f}"
    return f"{v:.1f}"


# ── 데이터 읽기와 배치

def load_rows(csv_path):
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {FEATURES[k]: raw[k] for k in FEATURES}  # 라벨 열은 여기서 버린다
            assert not set(LABEL_COLUMNS) & set(r)
            for k in ("air", "proc", "rpm", "torque", "wear"):
                r[k] = float(r[k])
            r["temp_diff"] = round(r["proc"] - r["air"], 1)
            r["power"] = round(r["torque"] * r["rpm"] * 2 * math.pi / 60)
            rows.append(r)
    return rows


def quantile(values, p):
    s = sorted(values)
    return s[round(p * (len(s) - 1))]


def place_rows(rows):
    """행 순서를 유지한 채 시작점만 옮겨 설비·시각을 붙인다."""
    n = len(rows)
    low = quantile([r["temp_diff"] for r in rows], 0.025)
    low_idx = [i for i, r in enumerate(rows) if r["temp_diff"] <= low]
    hot_center = low_idx[len(low_idx) // 2]
    target = round((HISTORY_END - YEAR_START).total_seconds() / 60 / YEAR_MINUTES * n)
    offset = (target - hot_center) % n
    placed = []
    for i, r in enumerate(rows):
        pos = (i + offset) % n
        t = YEAR_START + dt.timedelta(minutes=round(pos * YEAR_MINUTES / n))
        placed.append(dict(r, pos=pos, at=t, equipment_id=f"M-{pos % N_MACHINES + 1:02d}"))
    placed.sort(key=lambda r: r["pos"])
    return placed, {"rotation_offset": offset, "hot_spell_center_udi": rows[hot_center]["udi"]}


def thresholds(history_rows):
    th = {}
    for rule in ALARM_RULES:
        vals = [r[rule["key"]] for r in history_rows]
        if "low" in rule["sides"]:
            th[(rule["group"], "low")] = quantile(vals, 0.025)
        if "high" in rule["sides"]:
            th[(rule["group"], "high")] = quantile(vals, 0.975)
    return th


# 화면 센서 표의 '평소 범위'(사용자 결정 014 ①): 알람 경계와 같은 방식 — 학습용(train) 행의 2.5 · 97.5 백분위
NORMAL_RANGE_KEYS = [("air", "air_temperature_k"), ("proc", "process_temperature_k"), ("rpm", "rotational_speed_rpm"),
                     ("torque", "torque_nm"), ("wear", "tool_wear_min"), ("temp_diff", "temp_diff_k"), ("power", "power_w")]


def train_std(history_rows):
    """train 행 센서 값의 표준편차(모집단) — 평소 범위와 같은 칸 이름."""
    out = {}
    for k, snap in NORMAL_RANGE_KEYS:
        vals = [r[k] for r in history_rows]
        mean = sum(vals) / len(vals)
        out[snap] = round(math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)), 4)
    return out


def load_split():
    """분할 파일(D-227): UDI → train · val · test · demo. 라벨은 들어 있지 않다."""
    with open(SPLIT_FILE, encoding="utf-8") as f:
        return {r["UDI"]: r["split"] for r in csv.DictReader(f)}


def normal_ranges(history_rows):
    return {snap: {"low": quantile([r[k] for r in history_rows], 0.025), "high": quantile([r[k] for r in history_rows], 0.975)}
            for k, snap in NORMAL_RANGE_KEYS}


def detect(r, th):
    hits = []
    for rule in ALARM_RULES:
        v = r[rule["key"]]
        for side in rule["sides"]:
            b = th[(rule["group"], side)]
            if (side == "low" and v < b) or (side == "high" and v > b):
                hits.append({"group": rule["group"], "sensor": rule["sensor"], "key": rule["key"], "unit": rule["unit"],
                             "direction": side, "value": v, "boundary": b})
    return hits


# ── 정비 이력 한 건 만들기

class Session:
    def __init__(self, rng, t0, tech):
        self.rng, self.t, self.tech = rng, t0, tech
        self.steps, self.stopped_since, self.downtime = [], t0, 0
        self.escalations = []
        self.actor = tech[0]  # 지금 설비를 맡은 정비자(재가동·감시 운전 기록자)

    def add(self, kind, section=None, minutes=0, result=None, note=None, by=None, extra=None):
        s = SEC.get(section) if section else None
        step = {"at": iso(self.t), "kind": kind}
        if s:
            step.update(section=section, name=s["title"])
            if s.get("grade"):
                step["grade"] = s["grade"]
        if result:
            step["result"] = result
        if note:
            step["note"] = note
        step["by"] = by or self.actor
        if extra:
            step.update(extra)
        self.steps.append(step)
        self.t += dt.timedelta(minutes=minutes)
        return step

    def jitter(self, m):
        return max(1, round(m * self.rng.uniform(0.8, 1.3)))

    def lock(self, by=None):
        if self.stopped_since is None:
            self.stopped_since = self.t
        self.add("lock", "1.2", self.jitter(2), "완료", by=by)

    def run(self, kind, note):
        """재가동 또는 감시 운전: 정지 시간을 마감한다."""
        if kind == "restart":
            self.add("restart", "6.1", self.jitter(3), "완료", note, by=self.actor)
        else:
            self.add("monitor", None, 0, note=note, by=self.actor)
        if self.stopped_since is not None:
            self.downtime += round((self.t - self.stopped_since).total_seconds() / 60)
            self.stopped_since = None

    def wait(self, minutes):
        self.t += dt.timedelta(minutes=minutes)


def pick_note(rng, section, result):
    return rng.choice(NOTES[section][result])


def check_order(group, senior):
    checks = [s for s in M.SECTIONS if s.get("group") == group and s["kind"] == "check"]
    if senior:
        rank = {"A": 0, "B": 1, "C": 2, "D": 3}
        checks.sort(key=lambda s: (rank[s["grade"]], s["minutes"], s["id"]))
    return checks


def day_start(t):
    base = t if t.hour < 6 else t + dt.timedelta(days=1)
    return base.replace(hour=8, minute=30)


def next_work_slot(t, rng):
    """주간 담당자가 올 수 있는 가장 이른 시각."""
    if 6 <= t.hour < 22:
        return t + dt.timedelta(minutes=rng.randint(30, 90))
    return day_start(t)


def make_record(rid, r, alarm, others, ratios, rng):
    g = alarm["group"]
    t0 = r["at"] + dt.timedelta(minutes=rng.randint(0, 40))
    night = t0.hour >= 22 or t0.hour < 6
    weekend = t0.weekday() >= 5
    shift = "야간" if night else ("주말" if weekend else "주간")
    no_lead = shift in ("야간", "주말")
    tech = rng.choice(NIGHT_TECHS if no_lead else DAY_TECHS)
    senior = tech[1] >= SENIOR_YEARS

    table = ratios[g][alarm["direction"]]
    cause = rng.choices(list(table), weights=list(table.values()))[0]

    ses = Session(rng, t0 + dt.timedelta(minutes=rng.randint(2, 8) if no_lead else rng.randint(1, 4)), tech)
    ses.stopped_since = t0
    ses.lock()
    pre = next(s for s in M.SECTIONS if s.get("group") == g and s["kind"] == "precheck")
    ses.add("precheck", pre["id"], ses.jitter(pre["minutes"]), "완료")

    confirmed, pending = None, []  # pending: 아직 못 한 C·D 점검
    for s in check_order(g, senior):
        if s["grade"] in ("C", "D"):
            pending.append(s)
            continue
        if s.get("cause") == cause:
            res = "이상"
        else:
            res = "확인 불가" if rng.random() < UNABLE_RATE and "확인 불가" in NOTES[s["id"]] else "정상"
        ses.add("check", s["id"], ses.jitter(s["minutes"]), res, pick_note(rng, s["id"], res))
        if res == "이상":
            if s["confirms"]:
                confirmed = s
            else:  # 선별 점검: 확정 점검만 남긴다
                pending = [SEC[s["next"]]]
            break

    outcome = None

    def escalate(target, reason):
        """C·D 작업이 필요할 때 호출. 진행 여부(True=지금 진행)를 돌려준다."""
        if target["grade"] == "D" or target.get("kind") == "scope_out":
            who = "외부 업체 요청" if target["id"] in ("5.6", "7.2") else "전기 담당 요청"
            ses.add("call", target["id"], rng.randint(3, 6), note=reason,
                    extra={"call_type": "파트장 호출", "decision": who})
            ses.escalations.append({"type": "파트장 호출", "section": target["id"], "reason": reason, "decision": who})
            return False
        lead_on_site = 6 <= ses.t.hour < 22 and ses.t.weekday() < 5
        if not lead_on_site:
            ok = rng.random() < NIGHT_APPROVE_RATE
            decision = "승인 후 진행" if ok else "주간 처리"
            ses.add("call", target["id"], rng.randint(5, 15), note=reason,
                    extra={"call_type": "파트장 호출", "decision": decision})
        else:
            ok, decision = True, "현장 승인 후 진행"
            ses.add("call", target["id"], rng.randint(2, 6), note=reason,
                    extra={"call_type": "파트장 호출", "decision": decision})
        ses.escalations.append({"type": "파트장 호출", "section": target["id"], "reason": reason, "decision": decision})
        return ok

    def continue_in_day(first_section):
        """주간 처리: 감시 운전으로 넘긴 뒤 다음 주간에 이어서 한다."""
        if ses.stopped_since is not None:
            ses.run("monitor", "저부하 감시 운전으로 넘김")
        ses.t = max(ses.t, day_start(ses.t)) + dt.timedelta(minutes=rng.randint(0, 60))
        ses.actor = rng.choice(DAY_TECHS)[0]
        by = SCOPE_OUT_BY.get(first_section) or ("외부 업체" if first_section == "5.6" else ses.actor)
        ses.lock(by=by)
        return by

    def do_action(cause_code, by=None):
        aid = M.CAUSE_ACTION[cause_code]
        a = SEC[aid]
        if a.get("kind") == "scope_out":
            minutes = SCOPE_OUT_MINUTES[aid]
            by = SCOPE_OUT_BY[aid]
        else:
            minutes = a["minutes"]
        ses.add("action", aid, ses.jitter(minutes), "완료", f"{a['title']} 완료", by=by)

    reason_base = f"{alarm['sensor']} {fmt(alarm['key'], alarm['value'])} {alarm['unit']}"
    if confirmed:
        a = SEC[M.CAUSE_ACTION[cause]]
        if a["grade"] in ("A", "B"):
            do_action(cause)
        elif escalate(a, f"{reason_base}. {M.CAUSES[cause][0]} 확인, 조치 {a['id']}({a['grade']}) 필요"):
            do_action(cause)
        else:
            by = continue_in_day(a["id"])
            do_action(cause, by=by)
        ses.run("restart", VERIFY[g])
        outcome = {"type": "원인 확정", "cause_code": cause, "cause_name": M.CAUSES[cause][0]}
    elif cause == "NONE":
        ses.run("restart", "재가동 후 값 경계 이내")
        ses.add("close", "6.2", ses.jitter(2), note="빠른 점검 모두 정상, 감시로 마감")
        outcome = {"type": "이상 없음·감시"}
    else:
        # 빠른 점검으로 원인이 안 나옴: 선별 점검이 이상이면 확정 점검을, 아니면 남은 C등급 점검을 위해 호출
        screened = any(st.get("result") == "이상" for st in ses.steps if st["kind"] == "check")
        todo = pending if screened else [p for p in pending if p["grade"] == "C"]
        if not screened:
            ses.run("restart", "재가동 후에도 값 경계 밖")
            if todo:
                ses.lock()
        for p in todo:
            if screened:
                reason = f"{reason_base}. 선별 점검 이상, 확정 점검 {p['title']}({p['grade']}) 필요"
            else:
                reason = f"{reason_base}. 빠른 점검 정상, 다음 점검 {p['title']}({p['grade']}) 필요"
            by = None if escalate(p, reason) else continue_in_day(p["id"])
            res = "이상" if p["cause"] == cause else "정상"
            ses.add("check", p["id"], ses.jitter(SCOPE_OUT_MINUTES.get(p["id"], p["minutes"])), res,
                    pick_note(rng, p["id"], res), by=by)
            if res == "이상":
                act = SEC[M.CAUSE_ACTION[cause]]
                if act.get("kind") == "scope_out" and p["grade"] != "D":
                    # 3.6 이상 → 7.1 전기 담당. 설비는 정지 상태로 둔다.
                    ses.add("call", act["id"], rng.randint(3, 6), note=f"{M.CAUSES[cause][0]} 확인, {act['title']} 필요",
                            extra={"call_type": "파트장 호출", "decision": "전기 담당 요청"})
                    ses.escalations.append({"type": "파트장 호출", "section": act["id"],
                                            "reason": f"{M.CAUSES[cause][0]} 확인", "decision": "전기 담당 요청"})
                    slot = next_work_slot(ses.t, rng)
                    if slot.date() != ses.t.date() or not 6 <= ses.t.hour < 22:
                        ses.actor = rng.choice(DAY_TECHS)[0]  # 다음 주간 정비자가 이어받는다
                    ses.t = slot
                elif act["grade"] == "C":
                    reason = f"{reason_base}. {M.CAUSES[cause][0]} 확인, 조치 {act['id']}({act['grade']}) 필요"
                    if not escalate(act, reason):
                        by = continue_in_day(act["id"])
                do_action(cause, by=by)
                outcome = {"type": "원인 확정", "cause_code": cause, "cause_name": M.CAUSES[cause][0]}
                break
        if outcome:
            ses.run("restart", VERIFY[g])
        else:
            ses.add("call", None, rng.randint(3, 6), note=f"{reason_base}. 할 수 있는 점검 모두 정상, 원인 미상",
                    extra={"call_type": "숙련자 인계", "decision": "주간 정비 엔지니어 확인"})
            ses.escalations.append({"type": "숙련자 인계", "section": None,
                                    "reason": "할 수 있는 점검 모두 정상, 원인 미상", "decision": "주간 정비 엔지니어 확인"})
            ses.run("monitor", "저부하 감시 운전, 주간 엔지니어 인계")
            outcome = {"type": "원인 미상·숙련자 인계"}

    unconfirmed = []
    done = {st.get("section") for st in ses.steps}
    for p in pending:
        if p["id"] not in done and (outcome["type"] != "원인 확정" or rng.random() < 0.3):
            unconfirmed.append(f"{p['title']}({p['grade']}등급) 미실시 · 주간 확인 요청")

    refs = sorted({st["section"] for st in ses.steps if st.get("section")}, key=lambda x: [int(n) for n in x.split(".")])
    closed = ses.t
    approved_at = day_start(closed) + dt.timedelta(minutes=rng.randint(0, 60))
    if approved_at <= closed:
        approved_at = closed + dt.timedelta(hours=1)
    review = "승인" if rng.random() > 0.08 else "수정 후 승인"

    rec = {
        "record_id": rid,
        "equipment_id": r["equipment_id"],
        "equipment_type": "회전 가공 설비",
        "quality_grade": r["quality_grade"],
        "occurred_at": iso(t0),
        "closed_at": iso(closed),
        "shift": shift,
        "technician": {"name": tech[0], "years": tech[1]},
        "symptom_group": {"code": g, "name": next(x["name"] for x in M.GROUPS if x["code"] == g)},
        "alarm": {"sensor": alarm["sensor"], "value": alarm["value"], "unit": alarm["unit"],
                  "boundary": round(alarm["boundary"], 1), "direction": "경계 아래" if alarm["direction"] == "low" else "경계 위"},
        "other_out_of_range": [{"sensor": o["sensor"], "value": o["value"], "unit": o["unit"]} for o in others],
        "sensor_snapshot": {
            "air_temperature_k": r["air"], "process_temperature_k": r["proc"], "rotational_speed_rpm": r["rpm"],
            "torque_nm": r["torque"], "tool_wear_min": r["wear"], "temp_diff_k": r["temp_diff"], "power_w": r["power"],
        },
        "steps": ses.steps,
        "outcome": outcome,
        "escalations": ses.escalations,
        "downtime_min": ses.downtime,
        "unconfirmed": unconfirmed,
        "manual_refs": [f"MG-MM {x}" for x in refs],
        "approval": {"status": review, "by": PART_LEAD, "at": iso(approved_at)},
        "source": {"dataset": "ai4i2020", "udi": r["udi"]},
        "provenance": "synthetic_c1a15_from_ai4i2020",
    }
    rec["narrative"] = narrative(rec, alarm, rng)
    return rec


def unit_text(v, unit):
    return f"{v}{unit}" if unit == "분" else f"{v} {unit}"


def narrative(rec, alarm, rng):
    """교대 일지체 서술."""
    t0 = dt.datetime.fromisoformat(rec["occurred_at"][:16])
    k, u = alarm["key"], alarm["unit"]
    head = (f"{t0:%H:%M} {rec['equipment_id']} {rec['symptom_group']['name']} 알람"
            f"({alarm['sensor']} {unit_text(fmt(k, alarm['value']), u)}, 경계 {unit_text(fmt(k, alarm['boundary']), u)})"
            + rng.choice([" 접수.", " 받음.", " 확인."]))
    parts = [head, rng.choice(["잠금 후", "잠금 확인하고", "설비 잠그고"])]
    first, speaker = True, rec["technician"]["name"]
    out = rec["outcome"]

    def handover(by):
        nonlocal speaker, first
        if by != speaker:
            speaker = by
            day = rec["shift"] != "주간" and by not in ("전기 담당", "외부 업체")
            parts.append(f"주간 {by}:" if day else f"{by}:")
            first = True

    for st in rec["steps"]:
        if st["kind"] == "check":
            handover(st["by"])
            lead = "" if first else rng.choice(["이어서 ", "다음 ", "", "그다음 "])
            parts.append(f"{lead}{st['name']}{rng.choice([' — ', ', ', ': '])}{st['note']}.")
            first = False
        elif st["kind"] == "call":
            if st["call_type"] == "숙련자 인계":
                parts.append("할 수 있는 점검 모두 정상이라 원인 미상으로 숙련자 인계.")
            else:
                parts.append(f"{st.get('name', '')}({st.get('grade', '')}) 필요해 파트장 호출 → {st['decision']}.")
        elif st["kind"] == "action":
            handover(st["by"])
            parts.append(f"{ro(out['cause_name'])} 판단, {st['name']}.")
        elif st["kind"] == "restart":
            handover(st["by"])
            parts.append(f"재가동, {st['note']}.")
        elif st["kind"] == "monitor":
            parts.append(f"{st['note']}.")
        elif st["kind"] == "close":
            parts.append("감시로 마감.")
    parts.append(f"정지 {rec['downtime_min']}분.")
    if rec["unconfirmed"]:
        parts.append("미확인: " + "; ".join(rec["unconfirmed"]) + ".")
    return " ".join(parts)


# ── 매뉴얼 렌더링

CHAPTERS = {1: "공통 규칙", 6: "재가동과 마감", 7: "범위 밖 작업"}
for _g in M.GROUPS:
    CHAPTERS[_g["chapter"]] = f"{_g['name']} ({_g['system']})"


def grade_text(g):
    return f"{g} {M.GRADES[g][0]}"


def render_overview(group, th):
    g = next(x for x in M.GROUPS if x["code"] == group)
    rule = next(x for x in ALARM_RULES if x["group"] == group)
    bounds = []
    for side in rule["sides"]:
        b = th[(group, side)]
        bounds.append(f"{'하위' if side == 'low' else '상위'} 경계 {fmt(rule['key'], b)} {rule['unit']}")
    checks = [s for s in M.SECTIONS if s.get("group") == group and s["kind"] == "check"]
    causes = [c for c in M.CAUSES if c.startswith(group)]
    lines = [f"**알람 조건** {g['alarm']}. 현재 경계: {', '.join(bounds)} (부록 C).", "",
             "**원인 후보**", "", "| 코드 | 원인 | 설명 | 확정 점검 |", "|---|---|---|---|"]
    for c in causes:
        conf = [s["id"] for s in checks if s["cause"] == c and s["confirms"]]
        lines.append(f"| {c} | {M.CAUSES[c][0]} | {M.CAUSES[c][1]} | {', '.join(conf)} |")
    lines += ["", "**점검 목록**", "", "| 절 | 점검 | 권한 등급 | 소요 | 연결 원인 |", "|---|---|---|---|---|"]
    for s in checks:
        kind = "확정" if s["confirms"] else "선별"
        lines.append(f"| {s['id']} | {s['title']} | {grade_text(s['grade'])} | 약 {s['minutes']}분 | {s['cause']} {M.CAUSES[s['cause']][0]}({kind}) |")
    lines += ["", "**이상 없음 기준** A·B 점검이 모두 정상이고 재가동 후 값이 경계 이내면 6.2 절로 감시 마감한다.",
              "", "**호출** C등급 점검·조치가 다음 차례면 파트장 호출, 점검을 모두 해도 원인이 없거나 맞지 않는 소견이면 숙련자 인계(1.4)."]
    return lines


def render_section(s, th):
    lines = [f"### {s['id']} {s['title']}", ""]
    if s["kind"] == "info":
        if s.get("group"):
            lines += render_overview(s["group"], th)
        else:
            lines += [f"- {b}" for b in s["body"]]
        return lines
    if s["kind"] == "scope_out":
        lines += [f"**권한 등급** {grade_text(s['grade'])}", ""] + [f"- {b}" for b in s["body"]]
        return lines
    meta = [f"**권한 등급** {grade_text(s['grade'])}", f"**소요** 약 {s['minutes']}분"]
    if s["kind"] == "check":
        kind = "확정" if s["confirms"] else "선별"
        meta.append(f"**연결 원인** {s['cause']} {M.CAUSES[s['cause']][0]}({kind})")
    if s["kind"] == "precheck":
        meta.append("**원인을 가르지 않는 사전 확인**")
    lines += [" · ".join(meta), ""]
    if s.get("rule"):
        lines += [f"> **규칙** {s['rule']}", ""]
    if s.get("checklist"):
        lines += ["**시작 전 체크리스트**", ""] + [f"- [ ] {c}" for c in s["checklist"]] + [""]
    if s["kind"] == "check":
        lines += [f"#### {s['id']}.1 점검 방법", ""]
    lines += [f"{i}. {step}" for i, step in enumerate(s["steps"], 1)] + [""]
    if s["kind"] == "check":
        lines += [f"**판정 기준** {s['abnormal']}", ""]
        if s.get("next"):
            lines += [f"**다음** \"이상\"이면 {s['next']} 절이 필요하다.", ""]
        if s.get("action"):
            a = s["action"]
            lines += [f"#### {a['id']} 조치: {a['title']}", "",
                      f"**권한 등급** {grade_text(a['grade'])} · **소요** 약 {a['minutes']}분", ""]
            if a.get("checklist"):
                lines += ["**시작 전 체크리스트**", ""] + [f"- [ ] {c}" for c in a["checklist"]] + [""]
            lines += [f"{i}. {step}" for i, step in enumerate(a["steps"], 1)] + [""]
        elif s.get("action_ref"):
            lines += [f"**조치** 원인이 확정되면 {s['action_ref']} 절을 따른다.", ""]
        if s["id"] in M.FIELDS:  # v2: 끝 블록 — 빈 줄 없이 붙여 지시 · 조치 블록 끝이 v1과 같게(screen._block은 다음 '\n####'에서 자름)
            while lines and lines[-1] == "":
                lines.pop()
            f = M.FIELDS[s["id"]]
            lines += ["#### 준비 · 측정 · 기록", ""] + [f"- {label}: {f[key]}" for key, label in M.FIELD_LABELS] + [""]
    return lines


def render_manual(th):
    d = M.DOC
    out = ["---", f"doc_id: {d['doc_id']}", f"title: \"{d['title']}\"", f"revision: \"{d['revision']}\"",
           f"issued: \"{d['issued']}\"", "doc_type: equipment_manual", "provenance: synthetic_manual", "---", "",
           f"# {d['title']}", "",
           "> 가상 매뉴얼(synthetic_manual). 캡스톤 과제 10 제안 C1-a15(정비 길잡이)용으로 만든 문서이며 실제 설비의 정비 절차가 아니다. "
           "알람 경계는 UCI AI4I 2020 센서 값 중 학습용(train) 행의 분포로 정했고, UCI 가 고장 라벨을 만들 때 쓴 조건 수치는 쓰지 않았다. "
           "원인·점검·소요 분·권한 등급은 모두 팀 가정이다.", ""]
    chunks = {}
    current = None
    for s in M.SECTIONS:
        ch = int(s["id"].split(".")[0])
        if ch != current:
            current = ch
            out += [f"## {ch}. {CHAPTERS[ch]}", ""]
        lines = render_section(s, th)
        chunks[s["id"]] = "\n".join(lines).strip()
        out += lines if lines[-1] == "" else lines + [""]
    out += render_appendices(th)
    return "\n".join(out).rstrip() + "\n", chunks


def render_appendices(th):
    out = ["## 부록 A. 원인–점검 표", "", "| 원인 | 증상군 | 선별 점검 | 확정 점검 | 조치 |", "|---|---|---|---|---|"]
    for c, (name, _) in M.CAUSES.items():
        g = next(x for x in M.GROUPS if x["code"] == c[:2])
        checks = [s for s in M.SECTIONS if s.get("cause") == c]
        scr = ", ".join(s["id"] for s in checks if not s["confirms"]) or "—"
        conf = ", ".join(s["id"] for s in checks if s["confirms"])
        aid = M.CAUSE_ACTION[c]
        out.append(f"| {c} {name} | {g['name']} | {scr} | {conf} | {aid} {SEC[aid]['title']} |")
    out += ["", "## 부록 B. 권한 등급표", "", "정비 파트장이 정한다. 바꾸면 변경 기록에 남긴다.", "",
            "| 절 | 작업 | 등급 | 소요 |", "|---|---|---|---|"]
    for sid, s in SEC.items():
        if s.get("grade"):
            m = s.get("minutes") or SCOPE_OUT_MINUTES.get(sid)
            out.append(f"| {sid} | {s['title']} | {grade_text(s['grade'])} | {'약 ' + str(m) + '분' if m else '—'} |")
    out += ["", "## 부록 C. 알람 경계", "", "학습용(train) 행의 센서 값 분포로 계산했다. 데이터 · 분할이 바뀌면 다시 계산한다.", "",
            "| 증상군 | 값 | 방향 | 경계 |", "|---|---|---|---|"]
    for rule in ALARM_RULES:
        g = next(x for x in M.GROUPS if x["code"] == rule["group"])
        for side in rule["sides"]:
            out.append(f"| {g['name']} | {rule['sensor']} | {'하위 2.5% 미만' if side == 'low' else '상위 2.5% 초과'} | "
                       f"{fmt(rule['key'], th[(rule['group'], side)])} {rule['unit']} |")
    out += ["", "## 부록 D. 개정 이력", "", "| 개정 | 날짜 | 내용 |", "|---|---|---|"] + [f"| {r} | {d} | {t} |" for r, d, t in M.REVISIONS] + [""]
    for letter, title, note, blocks in M.APPENDICES:  # v2: 부록 E ~ J — 절이 아님, '#' 머리 없이 굵은 글 소제목(Pack corpus '### ' 절 나눔 그대로)
        out += [f"## 부록 {letter}. {title}", "", f"> {note}", ""]
        for sub, body in blocks:
            out += [f"**{sub}**", ""]
            if isinstance(body, dict):
                head, rows = body["table"]
                out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows] + [""]
            else:
                out += [f"- {x}" for x in body] + [""]
    return out


def section_index(chunks):
    items = []
    for s in M.SECTIONS:
        item = {"id": s["id"], "title": s["title"], "kind": s["kind"], "chapter": int(s["id"].split(".")[0]),
                "group": s.get("group"), "grade": s.get("grade"), "minutes": s.get("minutes") or SCOPE_OUT_MINUTES.get(s["id"]),
                "cause": s.get("cause"), "confirms": s.get("confirms"),
                "action": (s.get("action") or {}).get("id") or s.get("action_ref"), "next": s.get("next"),
                "text": chunks[s["id"]]}
        items.append({k: v for k, v in item.items() if v is not None})
        if s.get("action"):
            a = s["action"]
            items.append({"id": a["id"], "title": a["title"], "kind": "action", "chapter": item["chapter"],
                          "group": s.get("group"), "grade": a["grade"], "minutes": a["minutes"], "parent": s["id"]})
    return items


# ── 검사

def validate(records, manual_md, items, train_udis):
    ids = {i["id"] for i in items}
    for rec in records:
        for ref in rec["manual_refs"]:
            assert ref.split(" ")[1] in ids, ref
        assert rec["source"]["udi"] in train_udis, rec["record_id"]  # 이력 = train 행만(D-236 (4))
        assert rec["steps"][0]["kind"] == "lock", rec["record_id"]  # 잠금이 언제나 처음
        called = set()
        for st in rec["steps"]:
            if st["kind"] == "call" and st.get("section"):
                called.add(st["section"])
            if st["kind"] in ("check", "action") and st.get("grade") in ("C", "D"):
                # C·D 작업은 호출 기록 뒤에만 나온다(5.6 이 호출되면 7.2 도 그 요청에 포함)
                assert st["section"] in called or (st["section"] == "7.2" and "5.6" in called), (rec["record_id"], st)
    for forbidden in ("8.6 K", "1,380", "3,500", "9,000", "11,000", "12,000", "13,000", "240분"):
        assert forbidden not in manual_md, forbidden
    for c in M.CAUSES:
        assert any(s.get("cause") == c and s.get("confirms") for s in M.SECTIONS), c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    placed, placement = place_rows(rows)
    split = load_split()
    assert set(split) == {r["udi"] for r in placed}, "분할 파일과 CSV 행이 다름"
    hist_rows = [r for r in placed if split[r["udi"]] == "train"]
    th = thresholds(hist_rows)

    alarms = []
    for r in hist_rows:
        hits = detect(r, th)
        if hits:
            alarms.append((r, hits[0], hits[1:]))
    by_group = {g["code"]: [a for a in alarms if a[1]["group"] == g["code"]] for g in M.GROUPS}

    rng = random.Random(SEED)
    keep = by_group["CL"] + by_group["LD"]
    rest = TARGET_RECORDS - len(keep)
    pw_n = round(rest * len(by_group["PW"]) / (len(by_group["PW"]) + len(by_group["TW"])))
    keep += rng.sample(by_group["PW"], pw_n) + rng.sample(by_group["TW"], rest - pw_n)
    keep.sort(key=lambda a: a[0]["pos"])

    ratios = json.loads(RATIO_TABLE.read_text(encoding="utf-8"))
    records = []
    for n, (r, alarm, others) in enumerate(keep, 1):
        rec_rng = random.Random(SEED * 100000 + int(r["udi"]))
        records.append(make_record(f"H-{n:04d}", r, alarm, others, ratios, rec_rng))

    manual_md, chunks = render_manual(th)
    items = section_index(chunks)
    validate(records, manual_md, items, {r["udi"] for r in hist_rows})

    data = ROOT / "data"
    (data / "manuals").mkdir(parents=True, exist_ok=True)
    (data / "history").mkdir(parents=True, exist_ok=True)
    (data / "manuals" / "MG-MM.md").write_text(manual_md, encoding="utf-8")
    (data / "manuals" / "manual_sections.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(data / "history" / "maintenance_history.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    rules = {
        "boundary_rows": {"split_file": str(SPLIT_FILE.relative_to(ROOT)), "split_file_sha256": sha256(SPLIT_FILE), "split": "train", "rows": len(hist_rows)},
        "placement": dict(placement, machines=N_MACHINES, year=2026, rule="행 순서 유지, 시작점 이동, 설비 = 위치 % 20 + 1",
                          minutes_per_row=round(YEAR_MINUTES / len(rows), 2)),
        "priority": [r["group"] for r in ALARM_RULES],
        "rules": [{"group": r["group"], "sensor": r["sensor"], "key": r["key"], "unit": r["unit"],
                   "boundaries": {side: th[(r["group"], side)] for side in r["sides"]}} for r in ALARM_RULES],
        "train_alarms": {g: len(v) for g, v in by_group.items()},
        "normal_ranges": normal_ranges(hist_rows),
        "train_std": train_std(hist_rows),  # ML 자리 모델(CH-04)이 읽는 train 행 표준편차
    }
    (data / "alarm_rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")

    outputs = ["manuals/MG-MM.md", "manuals/manual_sections.json", "history/maintenance_history.jsonl", "alarm_rules.json"]
    manifest = {
        "source": {"name": "UCI AI4I 2020 Predictive Maintenance Dataset", "license": "CC BY 4.0",
                   "sha256": sha256(args.csv), "label_columns_dropped": LABEL_COLUMNS},
        "seed": SEED,
        "generation_inputs": {"generation/cause_ratio_table.json": sha256(RATIO_TABLE), str(SPLIT_FILE.relative_to(ROOT)): sha256(SPLIT_FILE)},
        "history": {
            "total": len(records),
            "by_symptom_group": dict(Counter(r["symptom_group"]["code"] for r in records)),
            "by_outcome": dict(Counter(r["outcome"]["type"] for r in records)),
            "by_cause": dict(sorted(Counter(r["outcome"].get("cause_code", "-") for r in records).items())),
            "by_shift": dict(Counter(r["shift"] for r in records)),
            "with_escalation": sum(1 for r in records if r["escalations"]),
        },
        "manual": {"sections": len(M.SECTIONS), "symptom_group_sections": sum(1 for s in M.SECTIONS if s.get("group")),
                   "checks": sum(1 for s in M.SECTIONS if s["kind"] == "check"), "causes": len(M.CAUSES)},
        "files": {f"data/{p}": sha256(data / p) for p in outputs},
    }
    (data / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(manifest["history"], ensure_ascii=False, indent=1))
    print(json.dumps(manifest["manual"], ensure_ascii=False))


if __name__ == "__main__":
    main()
