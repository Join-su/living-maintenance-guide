// 보고서 최종본(D-528 · 가이드 정비자 14 · 파트장 2 · 부록 5): 문서 한 장 — 머리 · 진행 넷 · 구역 1 ~ 5 · 꼬리.
// 두 앱이 같은 화면을 부른다(정비자: 확정 뒤 · 홈 최근 보고서, 파트장: 보고서 검토 본문). 값은 서버 최종본 읽기 그대로, 없는 값은 넣지 않는다.
// 스타일은 styles/final-report.css(앱 main.tsx에서 불러옴).

import type { Schemas } from "./api";
import { exActionLine, exCheckLine, hhmm } from "./format";

type Final = Schemas["FinalReport"];

const STATUS: Record<string, { label: string; cls: string }> = {
  awaiting_approval: { label: "승인 대기", cls: "wait" },
  approved: { label: "승인 완료", cls: "ok" },
  rejected: { label: "반려", cls: "bad" },
};

const t = (iso: string | null | undefined) => (iso ? hhmm(iso) : "");

/** 예외 상황 기록 센서값 한 줄: 알람 센서 값 · 경계(서버가 붙인 그대로). */
function alarmText(v: { [key: string]: unknown }): string {
  const a = v.alarm as { sensor?: string; value?: number; unit?: string; boundary?: number; direction?: string } | undefined;
  return a ? `${a.sensor} ${a.value} ${a.unit} · 경계 ${a.boundary} ${a.unit} ${a.direction ?? ""}`.trim() : "—";
}

/** 한 줄로 이음(빈 칸 뺌 · 모두 비면 '-'). */
const join = (xs: (string | false | null | undefined)[]) => xs.filter((x): x is string => !!x).join(" · ") || "-";

type XOne = NonNullable<NonNullable<Schemas["FinalReport"]["exception_record"]>["exception"]>;

/** 6번 예외 칩(정답지 21쪽 — 정비자 보고서 구역과 같은 이름): 같음 · {비슷한 것 이름} / 새 예외 · 예외상황-{주제}. */
function xkLabel(ex: XOne): string {
  if (ex.kind === "missed" && ex.match) {
    const m = (ex.matches ?? []).find((x) => x.kind === ex.match!.kind && x.no === ex.match!.no);
    const name = m ? (m.kind === "예외" ? `예외상황-${m.name}` : `${m.no} ${m.name}`) : `${ex.match.kind} ${ex.match.no}`;
    return `${ex.edit ? "같음 + 고침" : "같음"} · ${name}`; // 정답지 30쪽 2단계
  }
  return `새 예외 · 예외상황-${ex.topic}`;
}

type XList = { checks: XOne["checks"]; actions: XOne["actions"] };
/** 같음 + 고침의 고침 전 = 그때 받은 대상 예외 지금 목록(비슷한 것 예외 줄 · same-edit.md). */
function editBefore(ex: XOne): XList | null {
  const t = ex.match ? (ex.matches ?? []).find((m) => m.kind === ex.match!.kind && m.no === ex.match!.no) : undefined;
  return t?.checks && t.actions ? { checks: t.checks, actions: t.actions } : null;
}
/** 고침 전 · 뒤 한 줄: '점검 1 무엇 · 방법 / 조치 1 무엇'. */
const listLine = (l: XList) =>
  `점검 ${numbered(l.checks.map((c) => [c.what, c.how].filter(Boolean).join(" · ")))} / 조치 ${numbered(l.actions.map((a) => a.what))}`;

/** 점검 · 조치 목록 한 줄(정답지 21쪽): '1 … · 기준 … · 2 …'. */
const numbered = (xs: string[]) => xs.map((x, i) => `${i + 1} ${x}`).join(" · ");

export function FinalReport({ data }: { data: Final }) {
  const approved = data.status === "approved";
  const rejected = data.status === "rejected";
  const cb = data.cause_basis; // 답장 001-zzx-2(가이드 20쪽): 확정 근거 = 확정 원인을 정한 점검 결과 · 메모
  const xr = data.exception_record;
  const xc = xr?.context;
  const steps: [string, boolean][] = [
    ["초안 자동 작성", true],
    [`정비자 ${data.rejected_before ? "다시 " : ""}확정${!rejected && data.confirmed_at ? ` ${t(data.confirmed_at)}` : ""}`, !rejected && !!data.confirmed_at],
    [`파트장 승인${approved && data.decision ? ` ${t(data.decision.at)}` : ""}`, approved],
    ["이력 추가", approved],
  ];
  const now = steps.findIndex(([, done]) => !done);
  const st = STATUS[data.status] ?? { label: data.status, cls: "" };
  const lead = data.decision
    ? `${data.decision.by_name} · ${data.decision.kind === "approved" ? "승인" : "반려"} ${t(data.decision.at)}`
    : "승인 대기";
  const g = data.grade_use;
  return (
    <article className="fdoc" data-report={data.report_id}>
      <header className="fd-h">
        <div>
          <h1>정비 보고서 {data.equipment_id}</h1>
          <p>
            {data.equipment_type} · 알람 {t(data.occurred_at)}
            {data.confirmed_at ? ` · 보고서 확정 ${t(data.confirmed_at)}` : ""} · 작성 {data.author_name}({data.author_role})
          </p>
        </div>
        <div className="fd-st">
          {data.lock_at ? <span>잠금 확인 {t(data.lock_at)}</span> : null}
          <span className={st.cls}>{st.label}</span>
        </div>
      </header>
      <ol className="fd-steps">
        {steps.map(([label, done], i) => (
          <li key={i} className={done ? "done" : i === now ? "now" : ""}>
            <b>{i + 1}</b>
            {label}
          </li>
        ))}
      </ol>
      <div className="fd-2">
        <section>
          <h2>1. 이상 내용 · 센서 근거</h2>
          <table>
            <tbody>
              <tr>
                <th>센서</th>
                <th className="n">현재</th>
                <th>평소 범위</th>
                <th>상태</th>
              </tr>
              {data.sensors.map((s) => (
                <tr key={s.name} className={s.out_of_range ? "hot" : ""}>
                  <td>{s.name}</td>
                  <td className="n">
                    <b>{s.value_text}</b>
                  </td>
                  <td>{s.range_text}</td>
                  <td>{s.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.sensor_evidence ? <p className="fd-note">{data.sensor_evidence}</p> : null}
        </section>
        <section>
          <h2>2. 원인 분석</h2>
          <table>
            <tbody>
              <tr>
                <th>원인 후보</th>
                <th className="n">진단 때</th>
                <th className="n">근거</th>
                <th>결과</th>
              </tr>
              {data.causes.map((c) => (
                <tr key={c.name} className={c.confirmed ? "hot" : ""}>
                  <td>{c.name}</td>
                  <td className="n">{c.probability}%</td>
                  <td className="n">{c.evidence}건</td>
                  <td>{c.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="fd-note">
            <b>확정 원인</b> {data.cause}
            {cb ? (
              <>
                {" · "}
                <b>확정 근거</b> {cb.title}({cb.section}) 결과 {cb.value}
                {cb.memo ? ` · 메모 "${cb.memo}"` : ""}
              </>
            ) : null}
            <br />
            <b>근거 사례</b> {data.evidence_records}
          </p>
        </section>
      </div>
      <section>
        <h2>3. 조치 경과</h2>
        <table>
          <tbody>
            <tr>
              <th className="n" style={{ width: 60 }}>
                시각
              </th>
              <th style={{ width: 80 }}>누가</th>
              <th>내용</th>
              <th style={{ width: 70 }}>매뉴얼</th>
              <th style={{ width: 48 }}>등급</th>
            </tr>
            {data.timeline.map((x, i) => (
              <tr key={i}>
                <td className="n">{t(x.at)}</td>
                <td>{x.who}</td>
                <td>{x.text}</td>
                <td>{x.section ? (x.section === "EX-NEW" ? "예외" : x.section.startsWith("EX-") ? x.section : `${x.section}절`) : "—"}</td>
                <td>{x.grade ? <span className="fd-g">{x.grade}</span> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <div className="fd-2">
        <section>
          <h2>4. 조치 결과</h2>
          <table className="fd-kv">
            <tbody>
              <tr>
                <th>조치</th>
                <td>{data.action}</td>
              </tr>
              {data.restart ? (
                <tr>
                  <th>재가동 뒤</th>
                  <td>{data.restart}</td>
                </tr>
              ) : null}
              <tr>
                <th>정지 시간</th>
                <td>{data.downtime}</td>
              </tr>
              <tr>
                <th>권한 사용</th>
                <td>
                  A {g.A ?? 0} · B {g.B ?? 0} · C {g.C ?? 0}
                </td>
              </tr>
              {data.approved_calls?.length ? (
                <tr>
                  <th>파트장 승인</th>
                  <td>{data.approved_calls.join(" / ")}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </section>
        <section>
          <h2>5. 미확인 · 주간 요청</h2>
          <div className="fd-open">{data.unresolved}</div>
        </section>
      </div>
      {xr ? ( // D-538: 예외 상황 기록 — 정비자 글 · 예외 하나(점검 · 조치 목록) · 같음/아님(약속 v10 · 전 기록은 두 글) + 자동 정보(보기만)
        <section>
          <h2>6. 예외 상황 기록</h2>
          <div className="fd-exs">{/* 정답지 고침A 21쪽: 넘치면 이 구역만 스크롤 */}
          <table className="fd-kv fd-ex">
            <tbody>
              <tr>
                <th>상황</th>
                <td>{xr.situation}</td>
              </tr>
              {xr.text != null ? (
                <>
                  <tr>
                    <th>적은 글</th>
                    <td>{xr.text}</td>
                  </tr>
                  {xr.exception ? ( // 예외 하나 = 주제 + 점검 목록 + 조치 목록(약속 v10)
                    <>
                      <tr data-item="exception">
                        <th>예외</th>
                        <td>
                          예외상황-{xr.exception.topic}{" "}
                          <span className={`fd-xk ${xr.exception.kind === "missed" ? (xr.exception.edit ? "miss edit" : "miss") : "new"}`}>{xkLabel(xr.exception)}</span>
                        </td>
                      </tr>
                      <tr data-item="checks">
                        <th>점검</th>
                        <td>{numbered(xr.exception.checks.map(exCheckLine))}</td>
                      </tr>
                      <tr data-item="actions">
                        <th>조치</th>
                        <td>{numbered(xr.exception.actions.map(exActionLine))}</td>
                      </tr>
                      {xr.exception.edit ? ( // 정답지 30쪽 파트장 승인: '같음 + 고침' · 바뀌기 전 / 뒤 나란히
                        <>
                          {editBefore(xr.exception) ? (
                            <tr data-item="edit-before">
                              <th>고침 전</th>
                              <td>{listLine(editBefore(xr.exception)!)}</td>
                            </tr>
                          ) : null}
                          <tr data-item="edit-after">
                            <th>고침 뒤</th>
                            <td>{listLine(xr.exception.edit)}</td>
                          </tr>
                        </>
                      ) : null}
                    </>
                  ) : null}
                  {xr.split_error ? ( // 나누지 못함 → 적은 글 그대로 새 예외(약속 v10 2절)
                    <tr data-item="none">
                      <th>예외</th>
                      <td>
                        <span className="fd-xk new">나누지 못함 · 새 예외</span>
                      </td>
                    </tr>
                  ) : null}
                  <tr>
                    <th>결과</th>
                    <td>{xr.result}</td>
                  </tr>
                </>
              ) : (
                <tr>
                  <th>조치</th>
                  <td>{xr.action}</td>
                </tr>
              )}
              <tr>
                <th>설비 · 증상</th>
                <td>
                  {xc!.equipment_id} · {xc!.symptom_group.name} · {t(xc!.occurred_at)}
                </td>
              </tr>
              <tr>
                <th>센서값</th>
                <td>{alarmText(xc!.sensor_values)}</td>
              </tr>
              <tr>
                <th>직전 정비</th>
                <td>
                  {xc!.previous_maintenance
                    ? [xc!.previous_maintenance.record_id, xc!.previous_maintenance.outcome, xc!.previous_maintenance.action]
                        .filter(Boolean)
                        .join(" · ")
                    : "—"}
                </td>
              </tr>
              <tr>
                <th>AI 첫 안내</th>
                <td>{xc!.ai_first_check ? `${xc!.ai_first_check.section} ${xc!.ai_first_check.title}` : "—"}</td>
              </tr>
              <tr>
                <th>확정 원인</th>
                <td>{xc!.confirmed_cause}</td>
              </tr>
            </tbody>
          </table>
          </div>
        </section>
      ) : null}
      <footer className="fd-f">
        <div>
          <span>작성(정비자)</span>
          <b>
            {data.author_name}
            {data.confirmed_at ? ` · ${t(data.confirmed_at)} 확정` : ""}
          </b>
        </div>
        <div>
          <span>승인(파트장)</span>
          <b>{lead}</b>
        </div>
      </footer>
    </article>
  );
}
