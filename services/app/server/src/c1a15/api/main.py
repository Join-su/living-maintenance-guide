"""API 서버(P-06, B-04): 인증 · 알람 등록 · 배정 · 진단 · 잠금 · 지시 · 결과 · 종 목록 · 과거 기록 · 매뉴얼 원문.

화면은 서버가 준 값만 보인다. 판정은 engine, 문장은 llm(fixture 기본), 저장은 append-only 업무 기록 + 감사.
"""

from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, select, text

from c1a15 import config
from c1a15.alarms import ml
from c1a15.api import deps, final, history, lead, precheck, responses, screen, sessions
from c1a15.api.deps import User, conn, current_user, internal_token, tech_user
from c1a15.api.schemas import AlarmBody, CorrectionBody, EventBody, LoginBody, NoteBody, PrecheckBody
from c1a15.db import models as m
from c1a15.deploy import runs
from c1a15.deploy.client import PackUnavailable
from c1a15.engine import rules

PACK = config.pack_settings()  # Pack을 부르는 길 · 배포 서버 주소 — 어긋나면 서버 시작 거절(EV-001 CH-15)
config.r12_inbox()  # R12 받은 편지 폴더 — 절대 경로가 아니면 시작 거절(EV-001 CH-23)
config.kg_inbox()  # 그래프 적재기 받은 편지함(D-543) — 같은 규칙
config.checks()  # 사람 체크 기록 보내기 — off · deploy 밖이면 시작 거절(EV-006 CH-5)
app = FastAPI(title="정비 길잡이 API", version="0.2.0")
app.include_router(lead.router)  # 파트장 API(P-11)


@app.exception_handler(PackUnavailable)
def pack_unavailable(request: Request, exc: PackUnavailable) -> JSONResponse:
    """배포 서버에서 판정을 받지 못함(EV-001 CH-15): 요청 transaction은 되돌려지고(업무 기록 0),
    감사 pack_call_failed는 클라이언트가 따로 남겼다. 화면은 같은 seq로 다시 보낸다(503 = 앱 쪽 코드, D-265)."""
    return JSONResponse(status_code=503, content={"detail": "pack_unavailable", "code": exc.code})


_hasher = PasswordHasher()


@app.get("/api/health")
def health(request: Request) -> dict[str, str | None]:
    # nginx가 앱별 server 블록에서 덮어쓰는 앱 표시를 그대로 돌려준다(EP-C1-WEB-001, 권한 판정 아님).
    return {"status": "ok", "app": request.headers.get("x-app")}


@app.post("/api/login")
def login(
    body: LoginBody, request: Request, response: Response, c: Connection = Depends(conn, scope="function")
) -> dict:
    """사전 등록 계정 · 앱별 역할(R9 · AC-12): 정비자 앱은 정비자만, 파트장 앱은 파트장만."""
    app_id = deps.request_app(request)
    acct = c.execute(select(m.Account.__table__).where(m.Account.user_id == body.user_id)).first()
    ok = False
    if acct:
        try:
            ok = _hasher.verify(acct.password_hash, body.password)
        except (VerifyMismatchError, InvalidHashError):
            ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="bad_credentials")
    if acct.role != app_id:
        sessions.audit_rejection(acct.user_id, "login_rejected", f"app:{app_id}", {"reason": "role_mismatch"})
        raise HTTPException(status_code=403, detail="role_mismatch")
    token = deps.new_session(c, acct.user_id, app_id)
    response.set_cookie(
        deps.COOKIE[app_id], token, httponly=True, samesite="strict", max_age=deps.SESSION_HOURS * 3600, path="/"
    )
    if app_id == "tech":  # R9: 미배정 알람은 다음 로그인 정비자에게
        for (alarm_id,) in c.execute(
            select(m.Alarm.alarm_id).where(m.Alarm.assigned_to.is_(None), m.Alarm.status == "new")
        ).all():
            c.execute(
                m.Alarm.__table__.update()
                .where(m.Alarm.alarm_id == alarm_id)
                .values(assigned_to=acct.user_id, change_seq=deps.next_change_seq(c))
            )
            sessions.audit(
                c, "system", "assign", f"alarm:{alarm_id}", {"to": acct.user_id, "rule": "R9 미배정 → 다음 로그인"}
            )
    sessions.audit(c, acct.user_id, "login", f"app:{app_id}", {})
    return {"user_id": acct.user_id, "display_name": acct.display_name, "role": acct.role}


@app.post("/api/logout")
def logout(
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    c: Connection = Depends(conn, scope="function"),
) -> dict:
    token = request.cookies.get(deps.COOKIE[user.app])
    c.execute(
        m.LoginSession.__table__.update()
        .where(m.LoginSession.token_hash == deps.token_hash(token))
        .values(revoked_at=datetime.now(UTC))
    )
    response.delete_cookie(deps.COOKIE[user.app], path="/")
    return {"ok": True}


@app.get(
    "/api/internal/exception-records",
    dependencies=[Depends(internal_token)],
    response_model=responses.ExceptionRecordList,
)
def exception_records(c: Connection = Depends(conn, scope="function")) -> dict:
    """D-538: 승인된 예외 상황 기록(정비 이력 원본 층) — LLMOps ⑥이 읽음. 읽기만."""
    return lead.exception_record_list(c)


@app.get(
    "/api/internal/check-records",
    dependencies=[Depends(internal_token)],
    response_model=responses.CheckRecordList,
)
def check_records(c: Connection = Depends(conn, scope="function")) -> dict:
    """약속 v9 3절 기록 줄(헛걸음 · 놓침 · 새 예외 + 사람이 고른 점검) — LLMOps가 읽음. 읽기만."""
    return sessions.check_record_list(c)


@app.post("/api/internal/alarms", dependencies=[Depends(internal_token)])
def register_alarm(body: AlarmBody, c: Connection = Depends(conn, scope="function")) -> dict:
    """시연 알람 발생기(FLOW-01)만 부른다. 경계 판정 · 증상군 = 서버, 시각 = 지금(AC-34-C1), 배정 = R9."""
    assignments = dict(c.execute(select(m.EquipmentAssignment.equipment_id, m.EquipmentAssignment.user_id)).all())
    if body.equipment_id not in assignments:
        raise HTTPException(status_code=422, detail="unknown_equipment")
    _tool_caution(body)  # D-546 U6: 알람과 따로(알람 없음 응답이어도 남음)
    # 알람을 켤지 · 증상군 = Pack alarm Run(EV-001 ES-08, D-259 (2)) — 앱 안 경계 판정 갈래는 ES-23에서 지움
    hit, snapshot, pack_cols = _alarm_via_pack(c, body)
    if hit is None:
        return pack_cols  # 알람 없음: ML 길 알람 없음 · Pack 후보(D-529) · ML 유형(CH-24 · CH-25)
    to = rules.assign(body.equipment_id, assignments, sessions.open_alarm_counts(c))
    now = datetime.now(UTC)
    alarm_id = c.execute(
        m.Alarm.__table__.insert()
        .values(
            equipment_id=body.equipment_id,
            symptom_group=hit["group"],
            alarm=hit["alarm"],
            sensor_snapshot=snapshot,
            **pack_cols,
            trend=[p.model_dump() for p in body.trend],
            scenario_id=body.scenario_id,
            eval_mode=body.eval_mode,
            occurred_at=now,
            assigned_to=to,
            change_seq=deps.next_change_seq(c),
        )
        .returning(m.Alarm.alarm_id)
    ).scalar_one()
    sessions.audit(
        c,
        "system",
        "alarm",
        f"alarm:{alarm_id}",
        {"assigned_to": to, "scenario_id": body.scenario_id, **({"eval_mode": True} if body.eval_mode else {})},
        body.equipment_id,
    )
    return {"alarm_id": alarm_id, "assigned_to": to, "occurred_at": now.isoformat(), "symptom_group": hit["group"]}


def _alarm_via_pack(c: Connection, body: AlarmBody) -> tuple[dict | None, dict, dict]:
    """Pack alarm Run(정본 = c1a15-pack 약속 v2): 알람이면 (경계 판정 모양 hit, 센서 7칸, 저장 칸),
    알람 없음 + 사유(낮은 확신 · RNF · 예측 실패 D-275)는 감사 한 줄(D-529 — 파트장 '사람 판단' 지움, 후보 표 쓰기 0).
    ML 길 알람 없음 · ML 유형(경계 표시 없음)도 (None, {}, 응답) — 저장 0 · ML 유형은 감사만(CH-24 · CH-25)."""
    sc = deps.scenarios().get(body.scenario_id or "", {})
    lineage = body.data_lineage
    if lineage is None and sc.get("udi"):
        lineage = {"source_release": runs.source_release(), "row_id": int(sc["udi"]), "split": "demo"}
    if lineage is None:
        raise HTTPException(status_code=422, detail="missing_lineage")
    mode = "ml_predicted" if runs.settings()["alarm"] == "ml" else "sensor_boundary"
    ptype = body.product_type or sc.get("product_type")
    if mode == "ml_predicted" and not ptype:
        raise HTTPException(status_code=422, detail="missing_product_type")
    try:
        req = runs.alarm_body(
            runs.new_event_id(),
            body.equipment_id,
            body.sensor_snapshot,
            mode,
            lineage,
            ptype,
            ml.threshold() if mode == "ml_predicted" else None,
        )
    except KeyError as e:
        raise HTTPException(status_code=422, detail="bad_sensor_snapshot") from e
    event_id = req["event_card"]["event_id"]
    try:
        res = runs.alarm(event_id, req)
    except PackUnavailable as e:
        if (
            e.code == "run_failed" and e.where == "run.predict_failure"
        ):  # D-275: 예측 실패 = Run 실패 → 알람 없음 model_failure
            refs = {"event_id": event_id, "run_id": e.run_id}
            return None, {}, _reject_candidate(c, body, "model_failure", refs, {})
        raise
    out = res.output["alarm"]
    refs = {"event_id": event_id, "run_id": res.run_id, "deployment_id": res.deployment_id}
    # D-530 덧 3: 새 꼴 no_alarm_reason(약속 v6) · 옛 꼴 candidate.reason(약속 v5 — 지금 떠 있는 Pack) 둘 다
    reason = out.get("no_alarm_reason") or (out.get("candidate") or {}).get("reason")
    if reason:
        return None, {}, _reject_candidate(c, body, reason, refs, out)
    if not out["alarm"]:
        if mode == "ml_predicted":  # CH-24: ML 길 알람 없음(위험도 < 문턱) = 정상 결과 · 저장 0(D-237)
            return None, {}, _no_alarm("below_threshold", out)
        raise HTTPException(status_code=422, detail="no_boundary_crossed")  # 지금 기본 길과 같은 응답 · 저장 0
    snapshot = {**req["event_card"]["sensor_snapshot"], **out["sensor_derived"]}  # 화면 · 리트리버 센서 7칸
    if out.get("ml") is not None:  # 없으면 칸을 빼 SQL NULL(JSON null로 저장하지 않음)
        refs["ml"] = out["ml"]
    block = out["alarm_block"]
    if (
        block is None
    ):  # D-546 U5(CH-25를 바꿈): ML 유형으로 정한 알람도 등록 — 알람 꼴은 그 증상군 센서로(origin ml_type)
        block = screen.ml_type_alarm(out["group"], snapshot, deps.alarm_table())
    return {"group": out["group"], "alarm": block}, snapshot, refs


TOOL_CAUTION = (200, 246)  # D-546 U6 공구 교체 주의 범위(분) — 알람 아님(공구 마모 알람 경계 215는 그대로)
# 주의는 공구 교체(마모 < 200 · tool_caution_reset) 뒤부터 셈 — 「확인」하면 다음 교체 전엔 다시 뜨지 않음
_LAST_RESET = (
    "coalesce((select max(r.audit_id) from ops.audit_log r "
    "where r.action = 'tool_caution_reset' and r.equipment_id = {eq}), 0)"
)


def _tool_caution(body: AlarmBody) -> None:
    """범위 안이면 주의 한 줄 · 200 아래면 교체 한 줄(앞에 주의가 있을 때만) — 요청과 따로 커밋(알람 없음에도 남음)."""
    wear = body.sensor_snapshot.get("tool_wear_min")
    if wear is None or isinstance(wear, bool) or wear > TOOL_CAUTION[1]:
        return
    eq = body.equipment_id
    with deps.engine().begin() as c2:
        if wear >= TOOL_CAUTION[0]:
            sessions.audit(c2, "system", "tool_caution", f"equipment:{eq}", {"tool_wear_min": wear}, eq)
            return
        open_ = c2.execute(
            text(
                "select exists(select 1 from ops.audit_log where action = 'tool_caution' and equipment_id = :e "
                f"and audit_id > {_LAST_RESET.format(eq=':e')})"
            ),
            {"e": eq},
        ).scalar()
        if open_:
            sessions.audit(c2, "system", "tool_caution_reset", f"equipment:{eq}", {"tool_wear_min": wear}, eq)


def tool_cautions(c: Connection) -> list[dict]:
    """설비마다 마지막 교체 뒤 최근 주의 하나 — 교체 뒤 확인이 있으면 없음. 정비자 모두에게 · 최근 먼저.
    같은 설비에 공구 마모 경계 알람(TW)이 떠 있으면 없음 — 알람이 이김, 끝나면(done) 다시 이 규칙(D-546 덧 25)."""
    last_reset = _LAST_RESET.format(eq="a.equipment_id")
    rows = c.execute(
        text(
            "select distinct on (a.equipment_id) a.audit_id, a.equipment_id, a.at, a.detail from ops.audit_log a "
            f"where a.action = 'tool_caution' and a.audit_id > {last_reset} "
            "and not exists(select 1 from ops.audit_log k where k.action = 'tool_caution_ack' "
            f"and k.equipment_id = a.equipment_id and k.audit_id > {last_reset}) "
            "and not exists(select 1 from ops.alarms al where al.equipment_id = a.equipment_id "
            "and al.symptom_group = 'TW' and al.status in ('new', 'in_progress')) "
            "order by a.equipment_id, a.audit_id desc"
        )
    ).all()
    out = [
        {
            "caution_id": r.audit_id,
            "equipment_id": r.equipment_id,
            "tool_wear_min": r.detail["tool_wear_min"],
            "at": r.at.isoformat(),
        }
        for r in rows
    ]
    return sorted(out, key=lambda x: -x["caution_id"])


@app.post("/api/tech/prechecks/{pattern}/{record_id}", response_model=responses.PrecheckAck)
def tech_precheck_mark(
    pattern: str,
    record_id: str,
    body: PrecheckBody,
    user: User = Depends(tech_user),
    c: Connection = Depends(conn, scope="function"),
):
    """D-619 [살펴봄] · [이번엔 제외]: 기록만(건수 안 바뀜)."""
    return precheck.mark(c, user.user_id, pattern, record_id, body.choice, body.reason)


@app.post("/api/tech/cautions/{caution_id}/ack", response_model=responses.ToolCautionAck)
def tech_caution_ack(
    caution_id: int, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """D-546 U6: 공구 교체 주의 「확인」 — 그 설비 주의가 다음 공구 교체 전까지 사라짐(같은 확인 두 번째는 그대로)."""
    eq = c.execute(
        select(m.AuditEntry.equipment_id).where(
            m.AuditEntry.audit_id == caution_id, m.AuditEntry.action == "tool_caution"
        )
    ).scalar()
    if eq is None:
        raise HTTPException(status_code=404, detail="no_caution")
    last_reset = _LAST_RESET.format(eq=":e")
    done = c.execute(
        text(
            f"select :i <= {last_reset} or exists(select 1 from ops.audit_log where action = 'tool_caution_ack' "
            f"and equipment_id = :e and audit_id > {last_reset})"
        ),
        {"e": eq, "i": caution_id},
    ).scalar()
    if not done:
        sessions.audit(c, user.user_id, "tool_caution_ack", f"equipment:{eq}", {"caution_id": caution_id}, eq)
    return {"caution_id": caution_id, "equipment_id": eq}


def _no_alarm(reason: str, out: dict) -> dict:
    """ML 길에서 알람을 만들지 않은 정상 결과(EV-001 CH-24 · CH-25 · D-529) — 발생기 · 평가 실행기가 읽고 계속한다."""
    ml = out.get("ml") or {}
    return {
        "alarm_id": None,
        "reason": reason,
        "ml": {k: ml.get(k) for k in ("model_release", "risk", "threshold")},
    }


def _reject_candidate(c: Connection, body: AlarmBody, reason: str, refs: dict, out: dict) -> dict:
    """Pack 알람 없음 사유(확신 낮음 · RNF · 예측 실패)는 감사 한 줄(D-529) — 대상 = 설비 · 시각(감사 행 시각)."""
    sessions.audit(
        c,
        "system",
        "alarm_rejected",
        f"event:{refs['event_id']}",
        {"reason": reason, **refs},
        body.equipment_id,
    )
    return _no_alarm(reason, out)


def _alarm_view(a) -> dict:
    return {
        "alarm_id": a.alarm_id,
        "equipment_id": a.equipment_id,
        "symptom_group": a.symptom_group,
        "group_name": deps.group_names()[a.symptom_group],  # P-19: 알람 행 증상 이름
        "system": deps.group_systems()[a.symptom_group],  # D-528 내 알람 한 줄 · 왼쪽 칸 계통(부록 4)
        "alarm": a.alarm,
        "sensor_snapshot": a.sensor_snapshot,
        "trend": a.trend,
        "occurred_at": a.occurred_at.isoformat(),
        "status": a.status,
        "change_seq": a.change_seq,
        "ml": screen.ml_view(a.ml),  # D-546 U5
    }


@app.get("/api/tech/home", response_model=responses.Home)
def tech_home(since: int = 0, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")) -> dict:
    """주기 조회(EP-C1-SYNC-001): 내 알람과 진행 중 세션, 변경 순번."""
    start, end = screen.shift_window()
    reports = c.execute(  # 내 확정 보고서(S-03): 최근 · 이 교대 완료 수 · 완료 알람 행
        select(m.Report.__table__, m.DiagSession.alarm_id, m.Alarm.equipment_id, m.Alarm.symptom_group)
        .join(m.DiagSession, m.DiagSession.session_id == m.Report.session_id)
        .join(m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id)
        .where(m.Report.author == user.user_id)
        .order_by(m.Report.confirmed_at.desc())
    ).all()
    names = dict(c.execute(select(m.Account.user_id, m.Account.display_name)).all())
    in_shift = [  # 처리 완료 = 이 교대에 확정해 반려되지 않은 내 보고서
        r for r in reports if r.status != "rejected" and r.confirmed_at and start <= r.confirmed_at < end
    ]
    done_alarm_ids = [r.alarm_id for r in in_shift]
    rows = c.execute(
        select(m.Alarm.__table__)
        .where(
            m.Alarm.assigned_to == user.user_id,
            m.Alarm.status.in_(["new", "in_progress"]) | m.Alarm.alarm_id.in_(done_alarm_ids or [-1]),
        )
        .order_by(m.Alarm.occurred_at.desc())
    ).all()
    alarms = [_alarm_view(a) for a in rows]
    report_of = {r.alarm_id: r for r in reports}
    for a in alarms:  # 완료 알람 행(T02 초록 점): 보고서 상태 · 세션
        r = report_of.get(a["alarm_id"])
        a["report_status"] = r.status if r else None
        a["report_session_id"] = r.session_id if r else None
    calls = c.execute(
        select(m.Call.call_id).where(m.Call.sent_by == user.user_id, m.Call.sent_at >= start, m.Call.sent_at < end)
    ).all()
    in_progress = []
    sent_of: dict[int, str | None] = {}
    ex_of: dict[int, bool] = {}
    mine = select(m.DiagSession.__table__).where(
        m.DiagSession.user_id == user.user_id, m.DiagSession.status == "in_progress"
    )
    for s in c.execute(mine).all():
        row = m.DiagSession(**s._mapping)
        prog = sessions.progress(c, row)
        in_progress.append({"session_id": s.session_id, "alarm_id": s.alarm_id, "progress": prog})
        sent_of[s.alarm_id] = sessions.call_sent(c, row)  # G3 왼쪽 칸 '진행 중 · 파트장 호출'
        ex_of[s.alarm_id] = sessions.in_exception(c, row)  # 정답지 17쪽 '진행 중 · 예외 조치'
    by_alarm = {s["alarm_id"]: s for s in in_progress}
    for a in alarms:  # P-19 ①: 진행 중 알람 행의 '점검 n / 최대 m'
        a["session_id"] = by_alarm[a["alarm_id"]]["session_id"] if a["alarm_id"] in by_alarm else None
        a["progress"] = by_alarm[a["alarm_id"]]["progress"] if a["alarm_id"] in by_alarm else None
        a["call_sent"] = sent_of.get(a["alarm_id"])
        a["exception_progress"] = ex_of.get(a["alarm_id"], False)
    latest = max([a["change_seq"] for a in alarms] + [since])
    return {
        "alarms": alarms,
        "new_since": [a for a in alarms if a["change_seq"] > since],
        "in_progress": in_progress,
        "change_seq": latest,
        "shift": screen.shift_summary(len(in_progress), done=len(in_shift), calls=len(calls)),  # 결정 014 ④
        "cautions": tool_cautions(c),  # D-546 U6 공구 교체 주의
        "prechecks": precheck.cards(c, user.user_id),  # D-619 사전 점검 후보(화면 열 때 셈)
        "recent_reports": [  # 최근 내 보고서(상태 칩), 최근 3건
            {
                "report_id": r.report_id,
                "session_id": r.session_id,
                "equipment_id": r.equipment_id,
                "system": deps.group_systems()[r.symptom_group],  # D-528 왼쪽 칸 완료 탭 계통
                "cause": next((f["text"] for f in r.fields if f["key"] == "cause"), ""),
                "line": sessions.report_line(c, r.session_id),  # D-642 R5
                "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
                "status": r.status,
                # AC-11-C1: 반려 · 사유(반려 기록은 S-04 P-11이 쓴다)
                **(
                    {
                        "reject_reason": r.rejection.get("reason"),
                        "rejected_by_name": names.get(r.rejection.get("by")),
                        "rejected_at": r.rejection.get("at"),
                    }
                    if r.status == "rejected" and r.rejection
                    else {}
                ),
            }
            for r in reports[:3]
        ],
    }


@app.get("/api/tech/reports/{report_id}/final", response_model=responses.FinalReport)
def tech_report_final(
    report_id: int, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """D-528 정비자 보고서 최종본(가이드 정비자 14): 파트장 검토와 같은 문서 값.

    본인이 작성한 보고서만(D-530 답장 001-zzq) — 남의 보고서는 403 · 거부 감사."""
    author = c.execute(select(m.Report.author).where(m.Report.report_id == report_id)).scalar()
    if author is None:
        raise HTTPException(status_code=404, detail="no_report")
    if author != user.user_id:
        sessions.audit_rejection(user.user_id, "reject", f"report:{report_id}", {"reason": "not_own_report"})
        raise HTTPException(status_code=403, detail="not_own_report")
    return final.final_report(c, report_id)


@app.get("/api/tech/notifications", response_model=responses.Notifications, response_model_exclude_unset=True)
def tech_notifications(user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")) -> dict:
    """종 목록(FLOW-13, AC-37): 배정된 새 알람 · 진행 중 점검 · 답이 온 호출 · 반려된 보고서.

    뒤 두 종류는 S-03 · S-04에서 채운다.
    """
    home = tech_home(0, user, c)
    alarms = {a["alarm_id"]: a for a in home["alarms"]}

    def row(a: dict) -> dict:  # P-19 ②: 항목 행 = 설비 · 증상 · 시각
        return {"equipment_id": a["equipment_id"], "group_name": a["group_name"], "occurred_at": a["occurred_at"]}

    items = [{"kind": "new_alarm", "alarm_id": a["alarm_id"], **row(a)} for a in home["alarms"] if a["status"] == "new"]
    items += [
        {"kind": "in_progress", "session_id": s["session_id"], "alarm_id": s["alarm_id"], "progress": s["progress"]}
        | (row(alarms[s["alarm_id"]]) if s["alarm_id"] in alarms else {})
        for s in home["in_progress"]
    ]
    k = deps.manual()
    answered = c.execute(  # 파트장 답이 온 호출(AC-37, 답은 S-04 P-11이 쓴다)
        select(m.Call.__table__, m.DiagSession.alarm_id)
        .join(m.DiagSession, m.DiagSession.session_id == m.Call.session_id)
        .where(m.DiagSession.user_id == user.user_id, m.DiagSession.status == "in_progress", m.Call.answer.is_not(None))
        .order_by(m.Call.answered_at.desc())
    ).all()
    names = dict(c.execute(select(m.Account.user_id, m.Account.display_name)).all())
    answered_sessions = {cl.session_id for cl in answered}
    for it in items:  # T12: 진행 중 항목 '· 파트장 답 도착'
        if it["kind"] == "in_progress" and it["session_id"] in answered_sessions:
            it["answer"] = next(cl.answer.get("answer") for cl in answered if cl.session_id == it["session_id"])
    for cl in answered:
        a = alarms.get(cl.alarm_id)
        items.append(
            {
                "kind": "answered_call",
                "alarm_id": cl.alarm_id,
                "session_id": cl.session_id,
                "call_section": cl.section,
                "call_title": k.sections[cl.section]["title"] if cl.section in k.sections else None,
                "call_grade": cl.grade,
                "answer": cl.answer.get("answer"),
                "answered_by": cl.answer.get("by"),
                "answered_by_name": names.get(cl.answer.get("by")),
                "answered_at": cl.answered_at.isoformat() if cl.answered_at else None,
            }
            | (
                {"equipment_id": a["equipment_id"], "group_name": a["group_name"], "occurred_at": a["occurred_at"]}
                if a
                else {}
            )
        )
    rejected = c.execute(  # 반려된 보고서(AC-37 · AC-11, 반려는 S-04 P-11이 쓴다)
        select(m.Report.__table__, m.DiagSession.alarm_id)
        .join(m.DiagSession, m.DiagSession.session_id == m.Report.session_id)
        .where(m.Report.author == user.user_id, m.Report.status == "rejected")
        .order_by(m.Report.updated_at.desc())
    ).all()
    for r in rejected:
        a = alarms.get(r.alarm_id)
        rj = r.rejection or {}
        items.append(
            {
                "kind": "rejected_report",
                "alarm_id": r.alarm_id,
                "session_id": r.session_id,
                "report_cause": next((f["text"] for f in r.fields if f["key"] == "cause"), None),
                "reject_reason": rj.get("reason"),
                "rejected_by_name": names.get(rj.get("by")),
                "rejected_at": rj.get("at"),
            }
            | (row(a) if a else {})
        )
    return {"items": items, "empty": not items}


@app.post(
    "/api/tech/alarms/{alarm_id}/session",
    response_model=responses.SessionWithPreview,
    response_model_exclude_unset=True,
)
def start_session(
    alarm_id: int, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    row = c.execute(select(m.Alarm.__table__).where(m.Alarm.alarm_id == alarm_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="no_alarm")
    if row.assigned_to != user.user_id:
        raise HTTPException(status_code=403, detail="not_assigned")
    sid = sessions.create(c, m.Alarm(**row._mapping), user.user_id)
    s = sessions.session_for(c, sid, user.user_id)
    return {**sessions.view(c, s), "preview": sessions.preview(s, c)}


@app.get("/api/tech/sessions/{sid}", response_model=responses.SessionWithPreview, response_model_exclude_unset=True)
def get_session(sid: int, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")) -> dict:
    s = sessions.session_for(c, sid, user.user_id)
    return {**sessions.view(c, s), "preview": sessions.preview(s, c)}


@app.post("/api/tech/sessions/{sid}/events", response_model=responses.SessionView, response_model_exclude_unset=True)
def post_event(
    sid: int, body: EventBody, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    s = sessions.session_for(c, sid, user.user_id)
    return sessions.post_event(c, s, user.user_id, body.model_dump(exclude_none=True))


@app.get("/api/tech/sessions/{sid}/history", response_model=responses.SessionPast, response_model_exclude_unset=True)
def get_history(sid: int, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")) -> dict:
    """T15 지난 화면(S-05 P-14): 진행 표시 5단계의 제출 기록 · 그때 값 · 정정 · 덧붙인 메모."""
    s = sessions.session_for(c, sid, user.user_id)
    return history.history(c, s)


@app.post(
    "/api/tech/sessions/{sid}/corrections", response_model=responses.SessionView, response_model_exclude_unset=True
)
def post_correction(
    sid: int, body: CorrectionBody, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """T15 정정(S-05 P-14): 덧붙임 기록 · 감사 · 다시 판정 또는 정정 호출 · 보낸 호출에 붙임."""
    s = sessions.session_for(c, sid, user.user_id)
    return sessions.post_correction(c, s, user.user_id, body.model_dump(exclude_unset=True))


@app.post("/api/tech/sessions/{sid}/notes", response_model=responses.SessionView, response_model_exclude_unset=True)
def post_note(
    sid: int, body: NoteBody, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """T15 메모 덧붙이기(S-05 P-14)."""
    s = sessions.session_for(c, sid, user.user_id)
    return sessions.post_note(c, s, user.user_id, body.model_dump())


@app.get("/api/tech/sessions/{sid}/manual/{section}")
def manual_section(
    sid: int, section: str, user: User = Depends(tech_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """매뉴얼 원문(AC-39): 잠금 뒤, 이 세션에서 지시된 절만(R1 · R2). 절차 · 판정 기준 · 현재 등급 · 소요 분."""
    s = sessions.session_for(c, sid, user.user_id)
    if not sessions.view(c, s)["locked"]:
        raise HTTPException(status_code=409, detail="not_locked")
    k = deps.manual()
    if section not in k.sections:
        raise HTTPException(status_code=404, detail="no_section")
    if section not in sessions.instructed_sections(sessions.events_of(c, sid)):  # R2: 지시되지 않은 C · D 절 원문 금지
        raise HTTPException(status_code=409, detail="not_instructed")
    g, minutes = deps.grades(c)
    sec = k.sections[section]
    return {
        "section": section,
        "title": sec["title"],
        "text": sec["text"],
        "grade": g.get(section),
        "minutes": minutes.get(section),
    }


@app.get("/api/records/{record_id}", response_model=responses.Record)
def record(record_id: str, user: User = Depends(current_user), c: Connection = Depends(conn, scope="function")) -> dict:
    """과거 기록 전체(AC-38): 설비 · 발생 시각 · 증상 · 센서 값 · 점검 경로 · 확정 원인 · 조치 · 정지 시간 · 서술."""
    row = c.execute(
        select(m.HistoryRecord.record, m.HistoryRecord.source, m.HistoryRecord.provenance).where(
            m.HistoryRecord.record_id == record_id
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="no_record")
    r = row.record
    return {
        "record_id": record_id,
        "equipment_id": r["equipment_id"],
        "equipment_type": r.get("equipment_type"),  # P-19 ⑦: 설비 종류 · 승인 · 출처
        "approval": r.get("approval"),
        "source": row.source,
        "provenance": row.provenance,
        "occurred_at": r["occurred_at"],
        "symptom": r["symptom_group"],
        "alarm": r["alarm"],
        "sensor_snapshot": r["sensor_snapshot"],
        "steps": r["steps"],
        "outcome": r["outcome"],
        "downtime_min": r["downtime_min"],
        "narrative": r["narrative"],
    }
