// 왼쪽 「오늘 밤 알람」 칸(D-528 가이드 정비자 2 · 부록 2): 모든 정비자 화면 왼쪽 264px. 값은 /api/tech/home 그대로(새 API 0).
// 진행 탭 = 내 알람(새 · 진행 중): 계통 · 시각 · 설비 · 알람 · 센서 값 · 경계 · 단계. 완료 탭 = 최근 내 보고서(승인 상태).

import { useState } from "react";
import { hhmm, Icon, shortCause, type Home as HomeData } from "@c1a15/ui";
import { dayTime, risk, val } from "./Home";

type Row = HomeData["alarms"][number];

const REPORT: Record<string, { label: string; chip: string }> = {
  awaiting_approval: { label: "승인 대기", chip: "amber" },
  approved: { label: "승인 완료", chip: "green" },
  rejected: { label: "반려", chip: "red" },
};

function stage(a: Row): { e: string; text: string } {
  if (a.status === "new") return { e: "🚨", text: "새 알람" };
  if (a.call_sent) return { e: "🔧", text: `진행 중 · ${a.call_sent === "handover" ? "숙련자 인계" : "파트장 호출"}` }; // G3 대조
  if (a.exception_progress) return { e: "🔧", text: "진행 중 · 예외 조치" }; // 정답지 17쪽(약속 v9 [예외] 진행)
  const p = a.progress;
  return { e: "🔧", text: p && p.check_no > 0 ? `진행 중 · 점검 ${p.check_no}/${p.check_max}` : "진행 중" };
}

export function TodayPanel({
  home,
  current,
  currentAlarm = null,
  onOpen,
}: {
  home: HomeData | null;
  current: number | null;
  currentAlarm?: number | null; // 세션이 막 열려 홈 줄에 세션 번호가 아직 없을 때도 그 알람 줄을 고름(가이드 5쪽)
  onOpen: (a: Row) => void;
}) {
  const [tab, setTab] = useState<"live" | "done">("live");
  const live = (home?.alarms ?? []).filter((a) => a.status === "new" || a.status === "in_progress");
  const done = home?.recent_reports ?? [];
  return (
    <aside className="n-lp" aria-label="오늘 밤 알람">
      <div className="n-lh">
        <b>오늘 밤 알람</b>
        {live.length ? <span className="n-cnt">{live.length}</span> : null}
      </div>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "live"} className={tab === "live" ? "on" : ""} onClick={() => setTab("live")}>
          진행
        </button>
        <button type="button" role="tab" aria-selected={tab === "done"} className={tab === "done" ? "on" : ""} onClick={() => setTab("done")}>
          완료
        </button>
      </div>
      {tab === "live" ? (
        live.length ? (
          live.map((a) => {
            const st = stage(a);
            return (
              <button
                type="button"
                key={a.alarm_id}
                className={`n-li${(a.session_id != null && a.session_id === current) || (currentAlarm != null && a.alarm_id === currentAlarm) ? " on" : ""}`}
                data-alarm={a.alarm_id}
                onClick={() => onOpen(a)}
              >
                <div className="n-lr">
                  <span className="n-k">{a.system}</span>
                  <span className="n-t">{hhmm(a.occurred_at)}</span>
                </div>
                <b>
                  {a.equipment_id} · {a.group_name}
                </b>
                {a.alarm.origin === "ml_type" && a.ml ? ( // D-546 U5(꼴 01): 「ML 예측」 · 예측 유형 / 빨간 위험도
                  <>
                    <div className="n-lv">
                      <span className="mlk">
                        <Icon name="sparkles" />
                        ML 예측
                      </span>
                      <span>{a.ml.type_name}</span>
                    </div>
                    <div className="n-lv">
                      <span className="bad">위험도 {risk(a.ml.risk)}</span>
                    </div>
                  </>
                ) : (
                  <div className="n-lv">
                    <span className="bad">
                      {a.alarm.sensor} {val(a.alarm.value, a.alarm.unit)}
                    </span>
                    <span>경계 {val(a.alarm.boundary, a.alarm.unit)}</span>
                  </div>
                )}
                <div className="n-ls">
                  <i className="em">{st.e}</i>
                  {st.text}
                </div>
              </button>
            );
          })
        ) : (
          <div className="n-le">진행 중인 알람 없음</div>
        )
      ) : done.length ? (
        done.map((r) => (
          <div className="n-li ro" key={r.report_id} data-report={r.report_id}>
            <div className="n-lr">
              <span className="n-k">{r.system}</span>
              <span className="n-t">{r.confirmed_at ? dayTime(r.confirmed_at) : ""}</span>
            </div>
            <b>
              {r.equipment_id} · {r.line || shortCause(r.cause, 18) /* D-642 R5: 서버 줄 값(긴 글은 최종본 안) */}
            </b>
            <div className="n-ls">
              <i className="em">📝</i>
              <span className={`ch ${REPORT[r.status]?.chip ?? "gray"}`}>{REPORT[r.status]?.label ?? r.status}</span>
            </div>
          </div>
        ))
      ) : (
        <div className="n-le">최근 보고서가 없습니다</div>
      )}
    </aside>
  );
}
