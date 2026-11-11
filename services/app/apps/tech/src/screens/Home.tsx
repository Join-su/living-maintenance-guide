// T02 홈 · T12 알람 · 할 일(종 목록). 값은 /api/tech/home · /api/tech/notifications 응답 그대로(P-19 ① · ②, S-03 P-09).
// D-528(가이드 정비자 2): 내 알람 카드에 상태값 한 줄 · 안전 수칙 · 반려 칸 설명 문장 뺌.

import { Fragment, useState } from "react";
import { dateTime, hhmm, Icon, num, shortCause, type Home as HomeData, type Notifications } from "@c1a15/ui";

type Row = HomeData["alarms"][number];
type Precheck = HomeData["prechecks"][number];
export type OnPrecheck = (pattern: string, recordId: string, choice: "look" | "skip", reason?: string) => void;

/** 센서 값 표시: 정수면 천 단위 쉼표, 아니면 소수 한 자리(7.8 K · 1,409 rpm). */
export const val = (v: number, unit: string) => `${num(v, Number.isInteger(v) ? 0 : 1)} ${unit}`;

/** D-546 U5 ML 위험도(0 ~ 1, 소수 둘째 자리). */
export const risk = (v: number | null | undefined) => (v == null ? "" : num(v, 2));

const REPORT: Record<string, { label: string; chip: string }> = {
  awaiting_approval: { label: "승인 대기", chip: "amber" },
  approved: { label: "승인 완료", chip: "green" },
  rejected: { label: "반려", chip: "red" },
};

function rowState(a: Row): { dot: string; text: string; button: string; ghost: boolean } {
  if (a.status === "new") return { dot: "red", text: `${hhmm(a.occurred_at)} · 새 알람`, button: "점검 시작", ghost: false };
  if (a.status === "done") {
    const label = REPORT[a.report_status ?? ""]?.label ?? "";
    return { dot: "green", text: `${hhmm(a.occurred_at)} · 보고서 확정 · ${label}`, button: "보기", ghost: true };
  }
  if (a.report_status === "rejected") // 지시 d530-p33(가이드 33쪽): 반려된 보고서 = 빨간 점 · 「이어서 하기」 주 버튼
    return { dot: "red", text: `${hhmm(a.occurred_at)} · 반려 · 보고서 고치기`, button: "이어서 하기", ghost: false };
  const p = a.progress;
  const n = p && p.check_no > 0 ? `점검 ${p.check_no} / 최대 ${p.check_max} 진행 중` : "진행 중";
  return { dot: "amber", text: `${hhmm(a.occurred_at)} · ${n}`, button: "이어서 하기", ghost: true };
}

/** 새 알람(빨강) → 진행 중 → 완료(이 교대에 확정한 보고서) 순, 같은 상태 안에서는 서버 순서(최근 먼저). */
export function sortedAlarms(h: HomeData): Row[] {
  const rank = (a: Row) => (a.status === "new" ? 0 : a.status === "done" ? 2 : 1);
  return [...h.alarms].sort((a, b) => rank(a) - rank(b));
}

/** D-619 사전 점검 후보(고장 판단 아님 · 설계 4절): 내 알람 아래 · 살펴봄 · 이번엔 제외(기록만).
 *  D-663 S4(재시연 5): 겉은 핵심만('M-04 · 냉각 설정값 미리 확인' + '5건 중 3건') · 눌러야 자세히(앞 조치 · 날짜 · 사례 · 고장 판단 아님 · 버튼). */
function PrecheckCard({ c, onPrecheck }: { c: Precheck; onPrecheck?: OnPrecheck }) {
  const [open, setOpen] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [reason, setReason] = useState("");
  const when = c.days_ago > 0 ? `${c.days_ago}일 전` : "오늘";
  const note = reason.trim();
  const markWord = c.mark ? (c.mark.choice === "look" ? "살펴봄" : "이번엔 제외") : null;
  return (
    <div className="cd pck" data-precheck={c.pattern}>
      <div className="pck-s" role="button" tabIndex={0} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <b className="pck-t">
          {c.equipment_id} · {c.look_at} 미리 확인
        </b>
        <span className="pck-c">
          {c.total}건 중 {c.hits}건
        </span>
        {markWord ? <span className="pck-ms">{markWord}</span> : null}
        <Icon name={open ? "chevron-up" : "chevron-down"} />
      </div>
      {open ? (
        <div className="pck-x">
          {/* D-670: 필요한 말만 — 머리 '주의 필요' + 작게 '고장 판단 아님' · 한 줄(앞 조치 · 뒤 사건 건수) · 긴 문장 0 */}
          <div className="pck-h">
            <b>주의 필요</b>
            <span>고장 판단 아님</span>
          </div>
          <div className="pck-d">
            {when} {c.act_short}({c.section}) · {c.within_days}일 안 {c.follow_short} {c.total}건 중 {c.hits}건
          </div>
          <div className="pck-o">내 담당 설비 · {c.assignee_name}</div>
          <div className="pck-l">살펴볼 곳 · {c.look_at}</div>
          {c.basis.map((b) => (
            <div className="pcb" key={b.record_id}>
              {b.equipment_id} · {dateTime(b.at).slice(5, 10)} ·{" "}
              {b.result === "이어짐" ? `${b.follow_days}일 뒤 이어짐` : b.result}
            </div>
          ))}
          <div className="pck-f">
            {c.mark ? (
              <span className="pck-m">
                {markWord} · {hhmm(c.mark.at)}
                {c.mark.reason ? ` · ${c.mark.reason}` : ""}
              </span>
            ) : (
              <span className="pck-b">
                <span
                  className="btn ghost"
                  role="button"
                  tabIndex={0}
                  onClick={() => onPrecheck?.(c.pattern, c.record_id, "look", undefined)}
                >
                  살펴봄
                </span>
                <span className="btn ghost" role="button" tabIndex={0} onClick={() => setSkipping(true)}>
                  이번엔 제외
                </span>
              </span>
            )}
          </div>
        </div>
      ) : null}
      {skipping ? (
        <>
          <div className="dim" />
          <div className="modal pcm" role="dialog" aria-label="이번엔 제외">
            <div className="mh">
              <b>이번엔 제외</b>
            </div>
            <textarea
              aria-label="이유"
              maxLength={200}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <div className="mb">
              <span className="btn ghost" role="button" tabIndex={0} onClick={() => setSkipping(false)}>
                취소
              </span>
              <span
                className={`btn${note ? "" : " dis"}`}
                role="button"
                tabIndex={0}
                onClick={() => {
                  if (!note) return;
                  setSkipping(false);
                  onPrecheck?.(c.pattern, c.record_id, "skip", note);
                }}
              >
                저장
              </span>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

export function HomeScreen({
  home,
  onOpen,
  onResume,
  onFinal,
  onAckCaution,
  onPrecheck,
}: {
  home: HomeData;
  onOpen: (a: Row) => void;
  onResume?: (sessionId: number) => void;
  onFinal?: (reportId: number) => void;
  onAckCaution?: (cautionId: number) => void;
  onPrecheck?: OnPrecheck;
}) {
  const rows = sortedAlarms(home);
  const rejected = home.recent_reports.find((r) => r.status === "rejected");
  const s = home.shift;
  const prechecks = home.prechecks ?? []; // D-619 사전 점검 후보(내 알람 아래)
  const cautions = home.cautions ?? []; // D-546 U6 공구 교체 주의(알람 아님 · 설비마다 확인 전 한 줄)
  return (
    <div className="ap-body nostep">
      <div className="g2">
        <div>
          {cautions.map((c) => (
            // D-546 U6(꼴 02): 내 알람 카드 바로 위 주황 줄 · 설비마다 한 줄 · 「확인」이면 사라짐(알람 아님)
            <div className="tool" key={c.caution_id} data-caution={c.caution_id}>
              <Icon name="wrench" />
              <div className="tm">
                <b>공구 교체 주의</b>
                <span>
                  {c.equipment_id} · 공구 마모 <em>{num(c.tool_wear_min)}분</em> · 주의 범위 200 ~ 246분
                </span>
              </div>
              <span className="btn ghost" role="button" tabIndex={0} onClick={() => onAckCaution?.(c.caution_id)}>
                확인
              </span>
            </div>
          ))}
          <div className="cd ">
            <div className="cd-h">
              <Icon name="bell" />
              <b>내 알람</b>
              {rows.length ? <span className="cnt">{rows.length}</span> : null}
            </div>
            {rows.length === 0 ? (
              <div className="empty">
                <Icon name="bell" />
                배정된 알람이 없습니다
              </div>
            ) : (
              rows.map((a) => {
                const st = rowState(a);
                return (
                  <div className="al" key={a.alarm_id} data-alarm={a.alarm_id}>
                    <span className={`dot ${st.dot}`} />
                    <div className="al-m">
                      <b>
                        {a.equipment_id} · {a.group_name}
                      </b>
                      <span>{st.text}</span>
                      {a.status === "done" ? null : a.alarm.origin === "ml_type" && a.ml ? ( // D-546 U5(꼴 01): 계통 · 「ML 예측」 · 예측 유형 · 빨간 위험도
                        <div className="n-vals ml">
                          <span className="k">{a.system}</span>
                          <span className="mlk">
                            <Icon name="sparkles" />
                            ML 예측
                          </span>
                          <span>{a.ml.type_name}</span>
                          <b className="bad">위험도 {risk(a.ml.risk)}</b>
                        </div>
                      ) : ( // D-528: 상태값은 글자 한 줄(계통 · 값 · 경계 · 방향)
                        <div className="n-vals">
                          <span className="k">{a.system}</span>
                          <b>{val(a.alarm.value, a.alarm.unit)}</b>
                          <span>{val(a.alarm.boundary, a.alarm.unit)}</span>
                          <span className="n-bd">{a.alarm.direction}</span>
                        </div>
                      )}
                    </div>
                    <span className={`btn ${st.ghost ? "ghost" : ""}`} role="button" tabIndex={0} onClick={() => onOpen(a)}>
                      {st.button}
                    </span>
                  </div>
                );
              })
            )}
          </div>
          {prechecks.length ? ( // 내 알람 아래 · 후보가 많으면 이 칸만 스크롤(화면 높이 고정)
            <div className="pcl">
              {prechecks.map((c) => (
                <PrecheckCard key={`${c.pattern}-${c.record_id}`} c={c} onPrecheck={onPrecheck} />
              ))}
            </div>
          ) : null}
        </div>
        <div>
          <div className="cd ">
            <div className="cd-h">
              <Icon name="clock" />
              <b>
                오늘 교대 · {s.start_text} ~ {s.end_text}
              </b>
            </div>
            <div className="kts">
              <div className="kt">
                <b>{s.done}</b>
                <span>처리 완료</span>
              </div>
              <div className="kt">
                <b>{s.in_progress}</b>
                <span>진행 중</span>
              </div>
              <div className="kt">
                <b>{s.calls}</b>
                <span>파트장 호출</span>
              </div>
            </div>
          </div>
          <div className="cd mt">
            <div className="cd-h">
              <Icon name="shield" />
              <b>안전 수칙</b>
            </div>
            <div className="safe">
              <Icon name="lock" />
              <div>
                <b>잠금을 먼저 확인합니다</b>
              </div>
            </div>
          </div>
          <div className="cd mt">
            <div className="cd-h">
              <Icon name="file-text" />
              <b>최근 내 보고서</b>
            </div>
            {home.recent_reports.length === 0 ? (
              <div className="empty">
                <Icon name="file-text" />
                최근 보고서가 없습니다
              </div>
            ) : (
              home.recent_reports.map((r) => (
                <div className="rl" key={r.report_id}>
                  {/* D-637 F13 · D-642: 설비 · 한 줄 값 | 시각 · 최종본 보기 · 상태(긴 글은 「최종본 보기」 안) */}
                  <span className="rl-t">
                    <b>{r.equipment_id}</b> · {r.line || shortCause(r.cause, 18) /* D-642 R5: 서버 줄 값 */}
                  </span>
                  <span className="rl-r">
                    {/* D-642: 시각은 오른쪽 — 긴 줄이 …로 줄어도 시각 · 상태는 늘 보임 */}
                    {r.confirmed_at ? <span className="rl-w">{dayTime(r.confirmed_at)}</span> : null}
                    {r.status !== "rejected" && onFinal ? ( // D-528 가이드 정비자 14: 최근 내 보고서 줄에서도 최종본
                      <span className="lk2" role="button" tabIndex={0} onClick={() => onFinal(r.report_id)}>
                        최종본 보기
                      </span>
                    ) : null}
                    <span className={`ch ${REPORT[r.status]?.chip ?? "gray"}`}>{REPORT[r.status]?.label ?? r.status}</span>
                  </span>
                </div>
              ))
            )}
            {rejected ? ( // T02 / rejected(결정 017 ①, AC-11-C1): 반려 사유 · 파트장 · 시각 + 이어서 하기
              <div className="banner bad rejb">
                <Icon name="rotate-ccw" />
                <div>
                  <b>
                    반려 사유 · {rejected.rejected_by_name} {rejected.rejected_at ? hhmm(rejected.rejected_at) : ""}
                  </b>
                  {rejected.reject_reason}
                  {/* 답장 001-zzx 다(가이드 33쪽): 「이어서 하기」는 빨강 사유 상자 안 아래 */}
                  <span className="btn ghost full" role="button" tabIndex={0} onClick={() => onResume?.(rejected.session_id)}>
                    이어서 하기
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 최근 보고서 시각: 오늘이면 "00:29", 다른 날이면 "어제 22:44" · "10-11 22:44"(현장 시각). */
export function dayTime(iso: string, now: Date = new Date()): string {
  const d = dateTime(iso).slice(0, 10);
  const today = dateTime(now.toISOString()).slice(0, 10);
  const yesterday = dateTime(new Date(now.getTime() - 86400000).toISOString()).slice(0, 10);
  if (d === today) return hhmm(iso);
  return `${d === yesterday ? "어제" : d.slice(5)} ${hhmm(iso)}`;
}

const ANSWER: Record<string, string> = {
  approved: "승인 후 진행",
  daytime: "주간 처리",
  direct: "직접 확인",
  assign: "주간 숙련자 배정",
};

const GROUPS: { kind: string; label: string; button: string; dot: string }[] = [
  { kind: "new_alarm", label: "배정된 새 알람", button: "알람 보기", dot: "red" },
  { kind: "in_progress", label: "진행 중인 점검", button: "이어서 하기", dot: "amber" },
  { kind: "answered_call", label: "파트장 답이 온 호출", button: "답 보기", dot: "green" },
  { kind: "rejected_report", label: "반려된 보고서", button: "보고서 보기", dot: "red" },
];

type Item = Notifications["items"][number];

function itemText(i: Item): { title: string; sub: string } {
  const when = i.occurred_at ? hhmm(i.occurred_at) : "";
  if (i.kind === "rejected_report") {
    // T12 / list '반려된 보고서'(결정 017 ①): 설비 · 원인 · 시각 / 파트장 · 시각 · 사유
    return {
      title: `${i.equipment_id} · ${i.report_cause} · ${when}`,
      sub: `${i.rejected_by_name ?? ""} ${i.rejected_at ? hhmm(i.rejected_at) : ""} · ${i.reject_reason ?? ""}`.trim(),
    };
  }
  if (i.kind === "answered_call") {
    const by = `${i.answered_by_name ?? i.answered_by ?? ""} ${i.answered_at ? hhmm(i.answered_at) : ""}`.trim();
    return {
      title: `${i.equipment_id} · ${i.call_title}(${i.call_section}, ${i.call_grade})`,
      sub: `${by} · ${ANSWER[i.answer ?? ""] ?? i.answer ?? ""}`,
    };
  }
  const p = i.progress;
  const sub =
    i.kind === "new_alarm"
      ? `${when} · 새 알람`
      : `${when} · ${p && p.check_no > 0 ? `점검 ${p.check_no} / 최대 ${p.check_max}` : "진행 중"}${i.answer ? " · 파트장 답 도착" : ""}`;
  return { title: `${i.equipment_id} · ${i.group_name}`, sub };
}

/** T12 종 목록 패널: 배정된 새 알람 · 진행 중인 점검 · 파트장 답이 온 호출 · 반려된 보고서(AC-37). */
export function NotifyPanel({
  data,
  onItem,
}: {
  data: Notifications;
  onItem: (item: Notifications["items"][number]) => void;
}) {
  return (
    <div className="panel" role="dialog" aria-label="알람 · 할 일">
      <div className="cd-h">
        <Icon name="bell" />
        <b>알람 · 할 일</b>
      </div>
      {data.empty ? (
        <div className="empty">
          <Icon name="bell" />
          알람 · 할 일이 없습니다
        </div>
      ) : (
        GROUPS.map((g) => {
          const items = data.items.filter((i) => i.kind === g.kind);
          if (!items.length) return null;
          return (
            <Fragment key={g.kind}>
              <div className="grp">{g.label}</div>
              {items.map((i) => {
                const t = itemText(i);
                return (
                  <div className="nt-item" key={`${i.kind}-${i.alarm_id}-${i.call_section ?? ""}`}>
                    <span className={`dot ${g.dot}`} />
                    <div className="al-m">
                      <b>{t.title}</b>
                      <span>{t.sub}</span>
                    </div>
                    <span className="btn ghost sm2" role="button" tabIndex={0} onClick={() => onItem(i)}>
                      {g.button}
                    </span>
                  </div>
                );
              })}
            </Fragment>
          );
        })
      )}
    </div>
  );
}
