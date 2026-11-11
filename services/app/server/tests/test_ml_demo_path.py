# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-13(시험 DB): 시연 알람 = ML 길(D-301 (2) · D-304) — 알람 없음 = 정상 결과 · 발생기 계속 · 기대값 = 자료 파일.

VC-49(응답 · 저장 · 발생기) · VC-51(기대값 = c1a15-pack이 뜬 실제 Pack alarm 출력 · '모델 바꿈' 두 방향).
시험 코드에 알람 수를 박지 않는다(PR-30) — 기대 알람 = 자료에서 `alarm` 참 · `alarm_block` 있음인 행.
"""

import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

import fake_deploy
from c1a15.alarms import __main__ as cli
from c1a15.engine import alarm_rules
from test_api import SERVER, TECH_PW, api, db, login  # noqa: F401
from test_deploy_path import alarm_rows, count, deploy, register_sc, reset_ops, scenarios  # noqa: F401

pytestmark = pytest.mark.db


def kinds(path=None):
    """자료 행(udi) → 앱 결과 갈래(preview v14 O 표)."""
    out = {}
    for udi, a in fake_deploy.ml_alarm_outputs(path).items():
        reason = a.get("no_alarm_reason") or (a.get("candidate") or {}).get("reason")  # D-530 덧 3: 새 꼴 · 약속 v5 꼴
        if reason:  # D-529: Pack 알람 없음 + 사유 그대로 + 감사 한 줄
            out[udi] = reason
        elif not a["alarm"]:
            out[udi] = "below_threshold"
        else:  # D-546 U5: ML 유형으로 정한 알람(alarm_block 없음)도 등록
            out[udi] = "alarm"
    return out


@pytest.fixture
def ml(deploy, monkeypatch):
    from c1a15.deploy import runs

    monkeypatch.setenv("C1A15_ALARM", "ml")
    runs.settings.cache_clear()
    yield deploy
    runs.settings.cache_clear()


def run_generator(api, monkeypatch, capsys):
    internal = TestClient(api)

    def send(base, token, body):
        r = internal.post("/api/internal/alarms", json=body, headers={"X-Internal-Token": token})
        if r.status_code >= 400:
            raise urllib.error.HTTPError(base, r.status_code, "x", {}, io.BytesIO(r.content))
        return r.json()

    monkeypatch.setenv("C1A15_SCENARIOS", str(SERVER.parent / "data" / "demo" / "scenarios.json"))
    monkeypatch.setattr(cli, "send", send)
    code = cli.main(["--all"])
    return code, capsys.readouterr()


def check_against_data(api, db, monkeypatch, capsys, path=None):
    """같은 시험 코드 — 자료만 바뀌어도(모델 바꿈) 기대값을 자료에서 다시 셈."""
    reset_ops(db)
    login(api, "tech", "tech.kim", TECH_PW)
    want = kinds(path)
    mls = [a["ml"] for a in fake_deploy.ml_alarm_outputs(path).values() if a.get("ml")]
    model, th = mls[0]["model_release"], mls[0]["threshold"]
    by_sc = {s["scenario_id"]: s["udi"] for s in scenarios()}
    audit_before = count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'")
    code, cap = run_generator(api, monkeypatch, capsys)
    assert code == 0 and cap.err == ""
    registered = {by_sc[r["scenario_id"]] for r in alarm_rows(db)}
    assert registered == {u for u, k in want.items() if k == "alarm"}  # 등록 알람 집합 = 자료 기대 알람
    assert count(db, "select count(*) from ops.alarm_candidates") == 0  # D-529: 후보 표 쓰기 0
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'") - audit_before == sum(
        k not in ("alarm", "below_threshold") for k in want.values()
    )  # PR-29: 알람 없음 저장 0 · ML 유형 · Pack 후보는 감사만
    lines = {ln.split(" ", 1)[0]: ln for ln in cap.out.splitlines() if ln.startswith("DEMO-")}
    assert set(lines) == set(by_sc)  # 발생기가 멈추지 않고 모든 행을 보냄(GAP-19)
    for sc, udi in by_sc.items():
        k = want[udi]
        if k == "alarm":
            assert " 등록 · 배정 " in lines[sc]
        else:
            text_ = cli.NO_ALARM_TEXT[k]
            assert f" 알람 없음 · {text_} · 위험도 " in lines[sc] and lines[sc].endswith(f" · {model}")
    n_none = sum(k != "alarm" for k in want.values())
    n_alarm = sum(k == "alarm" for k in want.values())
    summary = cap.out.splitlines()[-1]
    assert summary.startswith(f"요약: 알람 {n_alarm} / 각본 {len(by_sc)} · 알람 없음 {n_none} · model ")
    if n_none:
        assert summary.endswith(f" · model {model} · 문턱 {th:.2f}")
    return want


def test_vc51_ml_demo_registers_exactly_the_data_alarms(api, db, ml, monkeypatch, capsys):
    """VC-51 · VC-49: 각본 10행 ML 길 — 등록 알람 = 자료 기대 알람 · 알람 없음은 200 · 발생기 계속 · 요약."""
    check_against_data(api, db, monkeypatch, capsys)
    alarm_bodies = [p["body"] for p in ml.posts() if p["body"]["kind"] == "alarm"]
    assert len(alarm_bodies) == len(scenarios())  # PR-08: 행마다 alarm Run 한 번(더 부르지 않음)
    assert all(b["event_card"]["source_mode"] == "ml_predicted" for b in alarm_bodies)


def flipped(tmp_path, direction):
    """'모델 바꿈' 사본(critic A14-N2 — 약속 v3 · Pack 실제 모양대로)."""
    rows = [json.loads(x) for x in fake_deploy.ML_ALARM_DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    want = "alarm" if direction == "off" else "below_threshold"
    k = kinds()
    i = next(i for i, r in enumerate(rows) if k[str(r["row_id"])] == want)
    a = rows[i]["output"]["alarm"]
    th = a["ml"]["threshold"]
    if direction == "off":  # ① 알람 → 알람 없음: alarm_block은 Pack처럼 둠(경계는 넘었음 — 앱은 alarm 거짓을 먼저 봄)
        a.update(alarm=False, group=None, group_source=None)
        a["ml"] = {
            **{k_: v for k_, v in a["ml"].items() if k_ != "group_source"},
            "alarm": False,
            "risk": round(th - 0.01, 6),
        }
    else:  # ② 알람 없음 → 알람: 경계 규칙 값으로 채움 — 경계를 안 넘은 행이면 사본을 만들지 않고 분명히 실패
        snap = {**rows[i]["request"]["event_card"]["sensor_snapshot"], **a["sensor_derived"]}
        hit = alarm_rules.detect(snap, alarm_rules.load(SERVER.parent / "data" / "alarm_rules.json"))
        assert hit is not None, f"row {rows[i]['row_id']}: 경계를 넘지 않는 행으로는 ② 사본을 만들 수 없음"
        a.update(alarm=True, group=hit["group"], group_source="sensor_boundary", alarm_block=hit["alarm"])
        a["ml"] = {**a["ml"], "alarm": True, "risk": round(th + 0.01, 6), "group_source": "sensor_boundary"}
    p = tmp_path / f"ml_{direction}.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p, str(rows[i]["row_id"])


@pytest.mark.parametrize("direction", ["off", "on"])
def test_pr30_model_swap_needs_no_test_change(api, db, ml, monkeypatch, capsys, tmp_path, direction):
    """PR-30: 자료 한 행을 약속 v3대로 뒤집은 사본 — 같은 시험 코드가 기대값을 자료에서 다시 세어 통과."""
    p, udi = flipped(tmp_path, direction)
    assert kinds()[udi] != kinds(p)[udi]  # 정말 바뀐 사본(원본 자료와 비교)
    monkeypatch.setattr(fake_deploy, "ML_ALARM_DATA", p)
    check_against_data(api, db, monkeypatch, capsys, p)


def test_vc49_ml_type_no_block_is_registered(api, db, ml, monkeypatch):
    """D-546 U5(CH-25를 바꿈): ML 유형(alarm_block 없음) = 알람 등록(origin ml_type) · 거절 감사 0."""
    reset_ops(db)
    rows = fake_deploy.ml_alarm_outputs()
    sc = scenarios()[0]
    a = rows[sc["udi"]]
    th = a["ml"]["threshold"]
    ml_out = {**a["ml"], "alarm": True, "risk": round(th + 0.2, 6), "group_source": "ml_type"}
    out = {**a, "alarm": True, "group": "CL", "group_source": "ml_type", "alarm_block": None, "ml": ml_out}
    ml.plan = [{"output": {"alarm": out}}]
    before = count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'")
    r = register_sc(api, sc)
    assert r.status_code == 200 and r.json()["alarm_id"] and r.json()["symptom_group"] == "CL"
    (row,) = alarm_rows(db)
    assert row["alarm"]["origin"] == "ml_type" and row["alarm"]["crossed"] is False and row["ml"] == ml_out
    assert count(db, "select count(*) from ops.alarm_candidates") == 0
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'") == before


def test_vc49_boundary_modes_keep_422(api, db, deploy, monkeypatch):
    """PR-28: deploy 길 경계 모드(C1A15_ALARM=boundary)의 '알람 없음'은 422 no_boundary_crossed 그대로."""
    reset_ops(db)
    sc = scenarios()[0]
    out = {**fake_deploy.ml_alarm_outputs()[sc["udi"]], "alarm": False, "ml": None}
    deploy.plan = [{"output": {"alarm": out}}]
    r = register_sc(api, sc)
    assert r.status_code == 422 and r.json()["detail"] == "no_boundary_crossed" and alarm_rows(db) == []


def test_vc49_generator_stops_on_real_failure(api, db, ml, monkeypatch, capsys):
    """PR-31 · FLOW-01: 연결 실패는 그대로 종료 코드 1 · 요약 없음 — 알람 없음만 계속."""
    reset_ops(db)
    monkeypatch.setenv("C1A15_SCENARIOS", str(SERVER.parent / "data" / "demo" / "scenarios.json"))
    monkeypatch.setenv("C1A15_INTERNAL_TOKEN", "test-internal-token")

    def down(*_):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(cli, "send", down)
    assert cli.main(["--all"]) == 1
    cap = capsys.readouterr()
    assert "등록 실패" in cap.err and "요약:" not in cap.out


def test_vc49_below_threshold_response_body(api, db, ml):
    """VC-49 · CH-24: 알람 없음 응답 body 전체 = preview v14 L ① — 저장 0(알람 · 후보 · 감사)."""
    reset_ops(db)
    want = kinds()
    sc = next(s for s in scenarios() if want[s["udi"]] == "below_threshold")
    a = fake_deploy.ml_alarm_outputs()[sc["udi"]]
    audits = count(db, "select count(*) from ops.audit_log")
    r = register_sc(api, sc)
    assert r.status_code == 200
    assert r.json() == {
        "alarm_id": None,
        "reason": "below_threshold",
        "ml": {k: a["ml"][k] for k in ("model_release", "risk", "threshold")},
    }
    assert alarm_rows(db) == [] and count(db, "select count(*) from ops.alarm_candidates") == 0
    assert count(db, "select count(*) from ops.audit_log") == audits  # D-237: 기록도 없음


def test_vc49_generator_stops_on_error_after_no_alarm_lines(api, db, ml, monkeypatch, capsys):
    """PR-31: 알람 없음 줄 뒤에 HTTP 오류가 나면 거기서 종료 코드 1 · 요약 없음(알람 없음만 계속)."""
    reset_ops(db)
    login(api, "tech", "tech.kim", TECH_PW)
    want = kinds()
    items = scenarios()
    first_none = next(i for i, s in enumerate(items) if want[s["udi"]] == "below_threshold")
    internal = TestClient(api)
    sent = []

    def send(base, token, body):
        sent.append(body["scenario_id"])
        if len(sent) == first_none + 2:  # 알람 없음 줄 하나 뒤의 행에서 서버 오류
            raise urllib.error.HTTPError(base, 503, "x", {}, io.BytesIO(b'{"detail": "pack_unavailable"}'))
        return internal.post("/api/internal/alarms", json=body, headers={"X-Internal-Token": token}).json()

    monkeypatch.setenv("C1A15_SCENARIOS", str(SERVER.parent / "data" / "demo" / "scenarios.json"))
    monkeypatch.setattr(cli, "send", send)
    assert cli.main(["--all"]) == 1
    cap = capsys.readouterr()
    assert len(sent) == first_none + 2 and f"{items[first_none]['scenario_id']} 알람 없음 · 위험도 낮음" in cap.out
    assert "등록 실패" in cap.err and "pack_unavailable" in cap.err and "요약:" not in cap.out
