"""평가 suite 셋 만들기(Pack EV-001 PS-01, 약속 v2) — Pack 밖 개발 도구. Pack 코드는 이 도구도 앱도 import하지 않는다.

  .venv/bin/python tools/make_evals.py <앱 git archive 사본> <앱 run ES-02 vc01 폴더> <분할 v2 csv 폴더>
- c1a15-judge-demo: 있던 10건(시연 각 세션의 첫 '보고서' 판정 입력)에 session_id만 더함 — 기대값 그대로
- c1a15-alarm: 시연 각본 10(경계 길, 센서 5칸 + 제품 등급) — 기대값 = 앱 engine/alarm_rules.detect(각본 7칸)
- c1a15-start: 시연 10 세션의 첫 Run 입력(dataset만) — 기대값은 PS-02(약속 v4)부터 tools/make_start_suite.py가 Pack 출력으로 만듦(D-314)
- c1a15-report는 앱 내보내기(D-290)로 tools/make_report_suite.py가 만듦(PS-05 합성 템플릿은 버림)
고장 라벨은 읽지 않는다(CSV는 UDI → Type만)."""
import csv
import importlib.util
import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1] / "pack" / "evals"
FIVE = ("air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min")
LIN = "ai4i-2020@sha256:dc6630cd9b1f0f853922fad78a1b6436570d3f1ec863f1dd5c4340ac56bc8a8e"
SUITE = """# 평가 suite — Pack revision 밖. {what}
# 만든 도구: tools/make_evals.py(앱 6cf03bd git archive 사본 · 앱 run ES-02 vc01 기록 — eval/ 카드 정답 아님)
suite: {name}
split: fixture
graph: run
dataset: dataset.jsonl
expectations: expectations.jsonl
review_policy: {{default: fixture, missing_fixture: stop_before}}
"""


def jl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main(app, cap, csvdir):
    spec = importlib.util.spec_from_file_location("app_alarm_rules", app / "server/src/c1a15/engine/alarm_rules.py")
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    table = rules.load(app / "data/alarm_rules.json")
    types = {}
    for f in csvdir.glob("ai4i-*.csv"):
        with f.open(encoding="utf-8") as fh:
            types.update({r["UDI"]: r["Type"] for r in csv.DictReader(fh)})
    # 1) 판정 suite — session_id만 더함
    d = EVALS / "c1a15-judge-demo"
    rows = [json.loads(x) for x in (d / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    jl(d / "dataset.jsonl", [{"case_id": r["case_id"], "input": {**r["input"], "session_id": "S-" + r["case_id"]}} for r in rows])
    # 2) 알람 suite · 3) 첫 Run suite
    scen = json.loads((app / "data/demo/scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    first = [json.loads(x) for x in (cap / "capture-e2e.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    first = [r for r in first if not r["events"]]
    assert len(first) == len(scen) == 10
    a_ds, a_ex, s_ds, s_ex = [], [], [], []
    for i, (s, f) in enumerate(zip(scen, first, strict=True), 1):
        cid = f"A{i:02d}"
        ec = {"event_id": f"C1A-E{s['scenario_id'].replace('-', '')}", "source_mode": "sensor_boundary", "equipment_id": s["equipment_id"],
              "sensor_snapshot": {k: float(s["sensor_snapshot"][k]) for k in FIVE}, "alarm": {"source": "none"},
              "data_lineage": {"source_release": LIN, "row_id": int(s["udi"]), "split": "demo"}, "product_type": types[str(s["udi"])]}
        hit = rules.detect(s["sensor_snapshot"], table)
        assert hit and hit["group"] == f["group"], (s["scenario_id"], f["test"])   # 시연 세션 k+1 = 각본 k(증상군으로 확인)
        a_ds.append({"case_id": cid, "input": {"kind": "alarm", "event_card": ec}})
        a_ex.append({"case_id": cid, "final": [{"path": "alarm", "equals": {"event_id": ec["event_id"], "alarm": True, "group": hit["group"], "group_source": "sensor_boundary",
                     "alarm_block": hit["alarm"], "sensor_derived": {"power_w": s["sensor_snapshot"]["power_w"], "temp_diff_k": s["sensor_snapshot"]["temp_diff_k"]},
                     "ml": None, "no_alarm_reason": None}}], "nodes": {"unsupported": [{"reached": False}], "predict_failure": [{"reached": False}]}})
        sim = [{"record_id": x["record_id"], "cause": x.get("cause"), "score": x["score"]} for x in f["similar"]]
        sid = "S-" + s["scenario_id"]
        s_ds.append({"case_id": f"S{i:02d}", "input": {"kind": "start", "session_id": sid, "event_id": ec["event_id"], "group": f["group"],
                     "query": {"direction": hit["alarm"]["direction"], "sensor_snapshot": ec["sensor_snapshot"]}, "similar": sim, "events": [],
                     "grades": f["grades"], "minutes": f["minutes"]}})
        s_ex.append({"case_id": f"S{i:02d}", "final": [{"path": "decision", "equals": f["decision"]}, {"path": "similar", "equals": sim},
                     {"path": "retrieval", "equals": {"source": "request", "vector_db_version": None, "query_from": None, "top_k": 10}}],
                     "nodes": {"unsupported": [{"reached": False}]}})
    for name, ds, ex, what in (("c1a15-alarm", a_ds, a_ex, "시연 각본 10건 알람 입력(경계 길) — 기대값 = 앱 경계 판정"),
                               ("c1a15-start", s_ds, s_ex, "시연 10 세션의 첫 Run(P1 다리) — 기대값 = 앱 inproc 첫 판정 · similar 그대로")):
        (EVALS / name).mkdir(exist_ok=True)
        jl(EVALS / name / "dataset.jsonl", ds)
        if name != "c1a15-start":   # start 기대값 = tools/make_start_suite.py(Pack 리트리버 출력, D-314)
            jl(EVALS / name / "expectations.jsonl", ex)
            (EVALS / name / "suite.yaml").write_text(SUITE.format(what=what, name=name.replace("-", "_")), encoding="utf-8")
    print("suite 셋 · 판정 10 · 알람 10 · 첫 Run 10(c1a15-report는 tools/make_report_suite.py)")


if __name__ == "__main__":
    main(*(Path(a).resolve() for a in sys.argv[1:4]))
