"""D-531 시험 데이터 생성 — rules.yaml만 읽어 이력 · 알람 기록 · 시험 사례를 만든다(씨앗 고정 · 같은 규칙 → 같은 바이트).

python kg-test/gen.py [--out <폴더>]   # 기본 = kg-test/data
쓰는 파일: history.jsonl(떼어 둔 사례 뺀 이력) · occurrences.jsonl(모든 알람 — 설비 · 라인 · 시각 · 센서만) · cases.jsonl(시험 사례 + 정답)
출력 글은 개수만(정답 값 0)."""
import argparse
import heapq
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FMT = "%Y-%m-%dT%H:%M"


def load_rules(path=HERE / "rules.yaml"):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def season_of(rules, ts):
    return next(s for s, months in rules["plant"]["seasons"].items() if ts.month in months)


def line_of(rules, machine):
    return next(l for l, ms in rules["plant"]["lines"].items() if machine in ms)


def _poisson(rng, lam):
    k, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _pick(rng, weights):
    keys = sorted(weights)
    return rng.choices(keys, [weights[k] for k in keys])[0]


def sensors(rules, rng, cause, ts, like=None):
    """알람 때 센서 7칸 — 공기 온도는 계절 평균 · 나머지 모양은 원인 signature(X2는 바꾼 부품의 원인 모양)."""
    s = rules["sensors"]
    sig = rules["causes"][like or cause]["signature"]
    air = rng.gauss(s["air_temperature_k"]["mean_by_season"][season_of(rules, ts)], s["air_temperature_k"]["sd"])
    diff = rng.uniform(*sig["temp_diff_k"])
    rpm = rng.uniform(*sig["rotational_speed_rpm"])
    torque = rng.uniform(*sig["torque_nm"])
    wear = rng.uniform(*s["tool_wear_min"]["uniform"])
    diff = min(round(diff, 1), round(s["alarm"]["below"] - 0.1, 1))   # 반올림 뒤에도 경계 아래(알람이 난 기록)
    out = {"air_temperature_k": round(air, 1), "process_temperature_k": round(round(air, 1) + diff, 1), "rotational_speed_rpm": int(round(rpm)),
           "torque_nm": round(torque, 1), "tool_wear_min": int(round(wear))}
    out["temp_diff_k"] = round(out["process_temperature_k"] - out["air_temperature_k"], 1)
    out["power_w"] = int(round(out["torque_nm"] * out["rotational_speed_rpm"] * 2 * math.pi / 60))
    return out


STANDARD_ORDER = ["4.4", "4.1", "4.2", "4.3", "4.5"]   # 현장 정비자가 흔히 하는 순서(소요 분) — 기록의 점검 결과 꼴에만 씀
CHECK_OF = {"C1": "4.1", "C2": "4.2", "C3": "4.3", "C4": "4.4", "C5": "4.5", "X3": "4.6"}
ACTION_OF = {"C1": "5.1", "C2": "5.2", "C3": "5.3", "C4": "5.4", "C5": "5.5", "X1": "6.2", "X2": "6.1", "X3": "6.3"}
MEMO = {   # 메모 글에는 질의 낱말(온도 · 차 · 저하 · 경계 · 아래)을 쓰지 않음 — 낱말 겹침 우연이 유사도 순위를 정하지 않게(dev에서 찾음)
    "C1": ["필터 표면 먼지 많음", "흡기 필터 차압 높음", "필터에 기름때"],
    "C2": ["팬 소리 거칠음", "팬 회전 약함", "팬 베어링 쪽 떨림"],
    "C3": ["탱크 수위 MIN 선 밑", "펌프 밑 물 자국", "이음부 물방울"],
    "C4": ["설정값 기준과 다름", "제어반 초기화 흔적", "목표값 바뀌어 있음"],
    "C5": ["열교환기 핀 막 낌", "입출구 열 빠짐 작음", "열교환기 표면 오염"],
    "X1": ["같은 라인 다른 설비도 같은 시각 알람", "공급 압력 낮음 설비팀 연락", "설비 이상 없음 공급 쪽"],
    "X2": ["교체 뒤 다시 알람 커넥터 방향 거꾸로", "직전 교체 부품 고정 덜 됨", "교체 뒤 조립 확인 필요"],
    "X3": ["직접 잰 값과 표시값 다름", "센서 값만 튐", "다른 값은 정상 센서 의심"],
}


def record(rules, rng, ev):
    """이력 한 건의 점검 결과 · 조치 · 메모(원인에서 나옴)."""
    c = ev["cause"]
    checks = []
    if c in ("C1", "C2", "C3", "C4", "C5"):
        for sec in STANDARD_ORDER:
            checks.append({"section": sec, "result": "이상" if sec == CHECK_OF[c] else "정상"})
            if sec == CHECK_OF[c]:
                break
    elif c == "X3":
        checks = ([{"section": "4.4", "result": "정상"}] if rng.random() < 0.5 else []) + [{"section": "4.6", "result": "이상"}]
    else:   # X1 · X2 — 몇 점검이 정상이고 호출로 끝남
        checks = [{"section": s, "result": "정상"} for s in STANDARD_ORDER[: rng.randint(0, 2)]]
    action = ACTION_OF[c]
    action_note = ""
    if c == "C1" and ev.get("late_filter"):
        action_note = "임시 청소(교체 늦춤)"
    ev.update(check_results=checks, actions=[{"section": action, "note": action_note}] if action_note else [{"section": action}],
              memo=rng.choice(MEMO[c]))
    if c in ("C1", "C2") and not ev.get("late_filter"):
        ev["replaced_part"] = rules["steps"][action]["part"]


def generate(rules):
    rng = random.Random(rules["seed"])
    start = datetime.fromisoformat(rules["plant"]["period"]["start"])
    end = datetime.fromisoformat(rules["plant"]["period"]["end"]) + timedelta(days=1)
    machines = [m for ms in rules["plant"]["lines"].values() for m in ms]
    days = (end - start).days
    base = rules["base_events"]
    events = []

    def add(ts, machine, cause, origin, like=None, **kw):
        if ts >= end:
            return None
        ev = {"ts": ts, "machine": machine, "line": line_of(rules, machine), "group": rules["symptom_group"]["code"],
              "cause": cause, "origin": origin, **kw}
        ev["sensors"] = sensors(rules, rng, cause, ts, like)
        events.append(ev)
        return ev

    # 보통 알람(E1 계절 분포 포함)
    for d in range(days):
        day = start + timedelta(days=d)
        s = season_of(rules, day)
        for _ in range(_poisson(rng, base["per_day_by_season"][s])):
            ts = day + timedelta(minutes=rng.randrange(24 * 60))
            add(ts, rng.choice(machines), _pick(rng, base["cause_by_season"][s]), "base")
    # E2 — 같은 라인 3 ~ 4대 30분 안
    e2 = rules["exceptions"]["E2"]
    for b in range(e2["bursts_per_year"]):
        t0 = start + timedelta(minutes=rng.randrange(days * 24 * 60))
        line = rng.choice(sorted(rules["plant"]["lines"]))
        ms = rng.sample(rules["plant"]["lines"][line], rng.choice(e2["machines"]))
        offs = sorted([0] + [rng.randrange(1, e2["window_min"]) for _ in ms[1:]])
        for i, (m, o) in enumerate(zip(ms, offs)):
            add(t0 + timedelta(minutes=o), m, "X1", "E2", burst=b, burst_pos=i + 1)
    # E5 — 센서 고장
    for _ in range(rules["exceptions"]["E5"]["per_year"]):
        add(start + timedelta(minutes=rng.randrange(days * 24 * 60)), rng.choice(machines), "X3", "E5")
    # 기록 채우기 + 연쇄(E3 · E4)는 시각 순으로 — 자식도 같은 길을 지남
    e3, e4, mixed = rules["exceptions"]["E3"], rules["exceptions"]["E4"], rules["mixed"]
    late_parents, repl_parents = [], []
    queue = [(e["ts"], e["machine"], i) for i, e in enumerate(events)]
    heapq.heapify(queue)

    def push(kid):
        if kid:
            heapq.heappush(queue, (kid["ts"], kid["machine"], len(events) - 1))
        return kid
    while queue:
        ev = events[heapq.heappop(queue)[2]]
        if ev["cause"] == "C1":
            ev["late_filter"] = rng.random() < e3["late_replace_share"]
        record(rules, rng, ev)
        if ev.get("late_filter"):
            late_parents.append(ev)
            if rng.random() < e3["follow_prob"]:
                if push(add(ev["ts"] + timedelta(minutes=rng.randrange(e3["after_days"][0] * 1440, e3["after_days"][1] * 1440)), ev["machine"], "C2", "E3")):
                    ev["is_parent"] = True
        if ev.get("replaced_part"):
            repl_parents.append(ev)
            if rng.random() < e4["prob"]:
                like = "C1" if ev["replaced_part"] == "filter" else "C2"
                if push(add(ev["ts"] + timedelta(minutes=rng.randrange(180, e4["within_days"] * 1440)), ev["machine"], "X2", "E4", like=like)):
                    ev["is_parent"] = True
    # 섞인 사례(일부러) — 예외 모양 안에 보통 원인
    def ordinary(ts, exclude=()):
        w = {k: v for k, v in base["cause_by_season"][season_of(rules, ts)].items() if k not in exclude}
        return _pick(rng, w)
    extra = []
    for p in rng.sample(repl_parents, min(mixed["near_replacement_ordinary"]["per_year"], len(repl_parents))):
        ts = p["ts"] + timedelta(minutes=rng.randrange(180, e4["within_days"] * 1440))
        ev = add(ts, p["machine"], ordinary(ts), "mixed")
        if ev:
            extra.append(ev); p["is_parent"] = True
    for p in rng.sample(late_parents, min(mixed["late_filter_ordinary"]["per_year"], len(late_parents))):
        ts = p["ts"] + timedelta(minutes=rng.randrange(e3["after_days"][0] * 1440, e3["after_days"][1] * 1440))
        ev = add(ts, p["machine"], ordinary(ts, exclude=("C2",)), "mixed")
        if ev:
            extra.append(ev); p["is_parent"] = True
    for ev in extra:   # 섞인 사례는 연쇄를 더 만들지 않음(부품 교체 기록은 남음)
        ev["late_filter"] = False
        record(rules, rng, ev)
    events.sort(key=lambda e: (e["ts"], e["machine"]))
    for i, ev in enumerate(events, start=1):
        ev["record_id"] = f"KG-{i:04d}"
    return events


def patterns(rules, ev, events_by_machine, events_by_line):
    """규칙에서 나온 예외 모양(생성 쪽 갈래 나눔에만 씀 — 방식 3은 methods.py에서 따로 셈)."""
    t = ev["ts"]
    e2, e3, e4 = rules["exceptions"]["E2"], rules["exceptions"]["E3"], rules["exceptions"]["E4"]
    same_line = [o for o in events_by_line[ev["line"]] if o is not ev and o["machine"] != ev["machine"]
                 and t - timedelta(minutes=e2["window_min"]) <= o["ts"] <= t]
    near_repl = [o for o in events_by_machine[ev["machine"]] if o.get("replaced_part") and t - timedelta(days=e4["within_days"]) <= o["ts"] < t]
    late = [o for o in events_by_machine[ev["machine"]] if o.get("late_filter")
            and t - timedelta(days=e3["after_days"][1]) <= o["ts"] <= t - timedelta(days=e3["after_days"][0])]
    contra = ev["sensors"]["rotational_speed_rpm"] >= 1450
    return {"E2": len(same_line) >= 2, "E3": bool(late), "E4": bool(near_repl), "E5": contra}


def category(rules, ev, pat):
    o = ev["origin"]
    if o == "E2":
        return "E2" if pat["E2"] else "E2_early"
    if o in ("E3", "E4", "E5"):
        return o
    if any(pat.values()):
        return "mixed"
    return "E1" if season_of(rules, ev["ts"]) in ("summer", "winter") else "normal"


def split(rules, events):
    by_m, by_l = {}, {}
    for e in events:
        by_m.setdefault(e["machine"], []).append(e)
        by_l.setdefault(e["line"], []).append(e)
    for e in events:
        e["category"] = category(rules, e, patterns(rules, e, by_m, by_l))
    h = rules["holdout"]
    rng = random.Random(rules["seed"] + 1)
    frm = datetime.fromisoformat(h["from"])
    picked = []
    for cat in sorted(h["counts"]):
        pool = [e for e in events if e["category"] == cat and e["ts"] >= frm and not e.get("is_parent")]
        if len(pool) < h["counts"][cat]:
            raise SystemExit(f"떼어 둘 사례 모자람: {cat} {len(pool)} < {h['counts'][cat]}")
        picked += rng.sample(pool, h["counts"][cat])
    test_ids = {e["record_id"] for e in picked}
    return [e for e in events if e["record_id"] not in test_ids], sorted(picked, key=lambda e: e["record_id"])


def _row(e, keys):
    out = {k: e[k] for k in keys if k in e}
    out["ts"] = e["ts"].strftime(FMT)
    return out


HIST_KEYS = ["record_id", "machine", "line", "group", "sensors", "check_results", "cause", "actions", "memo"]
OCC_KEYS = ["record_id", "machine", "line", "group", "sensors"]


def write(rules, out):
    events = generate(rules)
    history, cases = split(rules, events)
    out.mkdir(parents=True, exist_ok=True)
    names = {c: rules["causes"][c]["name"] for c in rules["causes"]}

    def dump(name, rows):
        (out / name).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    dump("history.jsonl", [{**_row(e, HIST_KEYS), "cause_name": names[e["cause"]], "in_manual": rules["causes"][e["cause"]]["in_manual"]} for e in history])
    dump("occurrences.jsonl", [_row(e, OCC_KEYS) for e in events])
    gold = rules["gold_by_cause"]
    dump("cases.jsonl", [{**_row(e, OCC_KEYS), "category": e["category"], "gold": {"cause": e["cause"], **gold[e["cause"]]}} for e in cases])
    return {"events": len(events), "history": len(history), "cases": len(cases),
            "case_categories": {c: sum(1 for e in cases if e["category"] == c) for c in sorted({e["category"] for e in cases})}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=str(HERE / "rules.yaml"))
    ap.add_argument("--out", default=str(HERE / "data"))
    a = ap.parse_args()
    print(json.dumps(write(load_rules(a.rules), Path(a.out)), ensure_ascii=False))
