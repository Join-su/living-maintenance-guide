// T15 지난 화면(S-05 P-15, AC-41 · 45 · 46 · 48): 진행 표시의 지나온 단계를 누르면 그 단계의 기록을 그때 값으로 보인다.
// 답장 001-zzx 마(가이드 22 · 23쪽): 점검 · 조치 단계는 왼쪽 '지금까지 점검 · 조치' 기록 목록(고른 줄 파란 테) + 오른쪽 고른 기록 칸
// ('보낸 결과' · 「메모 덧붙이기」 또는 「이 기록 정정」 → 같은 칸 안에서 적고 저장) + 「지금 단계로」. 알람 · 진단은 그때 화면을 보기 상태로.
// 정비자가 입력한 결과 · 메모 · 사전 확인 측정값에만 '이 기록 정정', 그 밖의 기록은 '메모 덧붙이기'만(가능 여부는 서버 값).
// 다시 판정으로 열린 지시가 바뀌면 서버가 확인을 요구하고(confirm_required), 확인 창에서 보내지 않은 입력이 지워진다고 알린다.
// 값은 모두 API(/history) 값이고, 앱은 판정 · 정정 반영 방식을 정하지 않는다.

import { useState } from "react";
import { errorDetail, hhmm, Icon, type Api, type Schemas, type SessionView } from "@c1a15/ui";
import { AlarmScreen } from "./Alarm";
import { DiagnosisScreen } from "./Diagnosis";

export type PastData = Schemas["SessionPast"];
type Rec = Schemas["PastRecord"];
type Value = "정상" | "이상" | "확인 불가";
const VALUES: Value[] = ["정상", "이상", "확인 불가"];
const VICON: Record<Value, string> = { 정상: "circle-check", 이상: "circle-alert", "확인 불가": "circle-help" };

function Btn({ label, cls, off, onClick }: { label: string; cls: string; off?: boolean; onClick?: () => void }) {
  return (
    <span className={`btn${cls ? ` ${cls}` : ""}${off ? " dis" : ""}`} role="button" tabIndex={0} aria-disabled={off} onClick={() => !off && onClick?.()}>
      {label}
    </span>
  );
}

function fmtBefore(v: Record<string, unknown> | null | undefined): string {
  if (!v) return "";
  if ("values" in v) return String(v.values ?? "");
  return `${v.value ?? ""}${v.memo ? ` · '${v.memo}'` : ""}`;
}

/** 왼쪽 목록 한 줄: 이름 · 절 + 둘째 줄(결과 · 값 · 시각). 조치 기록은 재가동 줄을 함께(같은 기록). */
function rows(r: Rec): { title: string; sub: string; bad: boolean }[] {
  const at = r.at ? hhmm(r.at) : "";
  const name = (t: string | null | undefined, sec: string | null | undefined) => (sec ? `${t ?? ""} · ${sec}` : (t ?? ""));
  if (r.kind === "check") return [{ title: name(r.title, r.section), sub: fmtValue(r), bad: r.value === "이상" }];
  if (r.kind === "precheck") return [{ title: name(r.title, r.section), sub: r.values ?? "", bad: false }];
  if (r.kind === "lock") return [{ title: name("잠금 확인", "1.2"), sub: `완료 ${at}`, bad: false }];
  if (r.kind === "call") return [{ title: r.call_kind === "handover" ? "숙련자 인계" : "파트장 호출", sub: r.record ?? "", bad: false }];
  const out = [{ title: name(r.title, r.section), sub: `완료 ${at}`, bad: false }];
  if (r.restart) out.push({ title: name("재가동", r.restart.section), sub: `재가동 ${r.restart.at ? hhmm(r.restart.at) : ""}`, bad: false });
  return out;
}

function fmtValue(r: Rec): string {
  return `${r.value ?? ""}${r.memo ? ` · 메모 '${r.memo}'` : ""}`;
}

export interface PastProps {
  api: Api;
  sid: number;
  s: SessionView;
  data: PastData;
  no: number; // 보는 단계(1 ~ 5)
  idx: number;
  onPick: (no: number, idx: number) => void; // 목록에서 다른 기록(점검 · 조치 단계를 함께 보임)
  onCurrent: () => void;
  unsent: { section: string; title: string; desc: string } | null; // 지금 지시의 보내지 않은 입력(기기)
  onSaved: (v: SessionView, clearedDraft: boolean) => void;
  onRecord: (id: string) => void;
  onManual: (section: string) => void;
}

type Form =
  | { kind: "correct"; value: Value | null; memo: string; values: string; reason: string }
  | { kind: "note"; memo: string };
type Confirm = { r: Rec; body: Record<string, unknown>; detail: Record<string, unknown> };

export function PastScreen(p: PastProps) {
  const step = p.data.steps[p.no - 1];
  const recs = step?.records ?? [];
  const r = recs[Math.min(p.idx, recs.length - 1)];
  const [form, setForm] = useState<{ seq: number | null; f: Form } | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [busy, setBusy] = useState(false);
  const closedNote = p.data.closed ? (p.data.closed_note ?? null) : null;
  if (!r) return null;
  const f = form && form.seq === r.seq ? form.f : null; // 다른 기록을 고르면 적던 칸은 닫힘

  const save = async (body: Record<string, unknown>, rr: Rec) => {
    setBusy(true);
    const { data, error, response } = await p.api.POST("/api/tech/sessions/{sid}/corrections", {
      params: { path: { sid: p.sid } },
      body: body as never,
    });
    setBusy(false);
    if (data) {
      setForm(null);
      setConfirm(null);
      p.onSaved(data, !!body.confirmed);
      return;
    }
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    if (response.status === 409 && detail && typeof detail === "object" && (detail as { reason?: string }).reason === "confirm_required")
      setConfirm({ r: rr, body, detail: detail as Record<string, unknown> }); // AC-46-C3
    else if (errorDetail(error)) setConfirm(null);
  };
  const saveNote = async (rr: Rec, memo: string) => {
    setBusy(true);
    const { data } = await p.api.POST("/api/tech/sessions/{sid}/notes", {
      params: { path: { sid: p.sid } },
      body: { target_seq: rr.seq, memo },
    });
    setBusy(false);
    if (data) {
      setForm(null);
      p.onSaved(data, false);
    }
  };

  if (r.kind === "alarm" || r.kind === "diagnosis") {
    // 알람 · 진단: 그때 화면을 보기 상태로(띠 = 지난 화면 · 단계 · 시각 · 「지금 단계로」)
    const shown =
      r.kind === "alarm" && r.alarm ? (
        <AlarmScreen s={{ alarm: r.alarm }} onNext={() => undefined} past />
      ) : r.next ? (
        <DiagnosisScreen
          s={{
            probabilities: r.probabilities ?? {},
            evidence: r.evidence ?? {},
            equipment_counts: r.equipment_counts,
            equipment_total: r.equipment_total,
            count_scope: r.count_scope,
            count_prior: r.count_prior,
            cause_names: r.cause_names ?? {},
            similar_cards: r.similar_cards ?? [],
            top_cards: r.top_cards ?? [],
            low_evidence: !!r.low_evidence,
            preview: r.next,
          }}
          onRecord={p.onRecord}
          onStart={() => undefined}
          past
        />
      ) : null;
    return (
      <>
        <div className="pastbar">
          <Icon name="history" />
          <b>지난 화면 · {step.name}</b>
          <span className="tt">{r.band}</span>
          <span className="pn">
            <span className="btn sm2" role="button" tabIndex={0} onClick={p.onCurrent}>
              지금 단계로 <Icon name="arrow-right" />
            </span>
          </span>
        </div>
        {shown}
      </>
    );
  }

  // 점검 · 조치 단계 기록을 한 목록으로(가이드 22쪽 왼쪽)
  const list = p.data.steps
    .filter((st) => st.no === 3 || st.no === 4)
    .flatMap((st) => st.records.map((x, i) => ({ no: st.no, i, x })));
  const n = r.corrections.length;
  const isPre = r.kind === "precheck";
  const sent = r.kind === "check" ? fmtValue(r) : isPre ? (r.values ?? "") : (r.record ?? "");
  const head = rows(r)[0].title;
  return (
    <>
      <div className="ap-body pl">
        <div className="g64">
          <div className="cd">
            <div className="cd-h">
              <Icon name="history" />
              <b>지금까지 점검 · 조치</b>
            </div>
            <div className="plist">
              {list.map(({ no, i, x }) =>
                rows(x).map((row, j) => (
                  <div
                    key={`${no}-${i}-${j}`}
                    className={`pli${x.seq === r.seq ? " on" : ""}${row.bad ? " bad" : ""}${x.void ? " void" : ""}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => p.onPick(no, i)}
                  >
                    <b>{row.title}</b>
                    <span>{row.sub}</span>
                  </div>
                )),
              )}
            </div>
          </div>
          <div className="cd pdet">
            <div className="cd-h">
              <Icon name="history" />
              <b>{head}</b>
              {n ? <span className="ch amber">정정 {n}회</span> : null}
              {r.void ? <span className="ch gray">정정으로 무효</span> : null}
            </div>
            {p.unsent ? <span className="ch amber unsent">지금 {p.unsent.section} 입력(보내지 않음)은 그대로</span> : null}
            <div className="prow">
              <span>{isPre ? "기록한 값" : r.kind === "check" ? "보낸 결과" : "기록"}</span>
              <b>{sent}</b>
            </div>
            {r.kind === "call" ? (
              <div className="pv">
                <span className="sm">보낸 요약</span>
                <p>{r.summary}</p>
              </div>
            ) : null}
            {r.box?.map((b, i) => (
              <div className="corr-box" key={i}>
                <Icon name="pencil" />
                <b>보낸 뒤 정비자 정정 · {b.at ? hhmm(b.at) : ""}</b>
                <div>
                  {b.section} {b.title} · {fmtBefore(b.before)} → {fmtBefore({ ...b.before, ...b.after })}
                </div>
                <span className="sm">사유 · {b.reason}</span>
              </div>
            ))}
            {n || r.notes.length ? (
              <div className="corr-list">
                {r.corrections.map((c, i) => (
                  <div key={`c${i}`}>
                    <span className="tt">{c.at ? hhmm(c.at) : ""}</span> <s>{fmtBefore(c.before)}</s> →{" "}
                    <b>{fmtBefore(isPre ? c.after : { ...c.before, ...c.after })}</b>
                    <span className="sm">사유 · {c.reason}</span>
                  </div>
                ))}
                {r.notes.map((x, i) => (
                  <div key={`n${i}`}>
                    <span className="tt">{x.at ? hhmm(x.at) : ""}</span> 덧붙인 메모 · <b>{x.memo}</b>
                  </div>
                ))}
              </div>
            ) : null}
            {f?.kind === "note" ? (
              <div className="pform">
                <span className="sm">덧붙일 메모</span>
                <textarea className="memo" aria-label="덧붙일 메모" value={f.memo} autoFocus onChange={(e) => setForm({ seq: r.seq, f: { ...f, memo: e.target.value } })} />
                <div className="mb">
                  <Btn cls="ghost" label="취소" onClick={() => setForm(null)} />
                  <Btn cls="" label="메모 저장" off={busy || !f.memo.trim()} onClick={() => void saveNote(r, f.memo.trim())} />
                </div>
              </div>
            ) : f?.kind === "correct" ? (
              <div className="pform">
                {isPre ? (
                  <>
                    <span className="sm">정정할 값</span>
                    <textarea className="memo" aria-label="정정할 값" value={f.values} onChange={(e) => setForm({ seq: r.seq, f: { ...f, values: e.target.value } })} />
                  </>
                ) : (
                  <>
                    <span className="sm">정정할 결과</span>
                    <div className="rbtns">
                      {VALUES.map((v) => (
                        <span
                          key={v}
                          className={`rbtn${f.value === v ? ` sel ${v === "정상" ? "ok" : v === "이상" ? "bad" : "na"}` : ""}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => setForm({ seq: r.seq, f: { ...f, value: v } })}
                        >
                          <Icon name={VICON[v]} />
                          {v}
                        </span>
                      ))}
                    </div>
                    <span className="sm">정정할 메모</span>
                    <textarea className="memo" aria-label="정정할 메모" value={f.memo} onChange={(e) => setForm({ seq: r.seq, f: { ...f, memo: e.target.value } })} />
                  </>
                )}
                <span className="sm">정정 사유</span>
                <textarea className="memo" aria-label="정정 사유" value={f.reason} onChange={(e) => setForm({ seq: r.seq, f: { ...f, reason: e.target.value } })} />
                <div className="mb">
                  <Btn cls="ghost" label="취소" onClick={() => setForm(null)} />
                  <Btn
                    cls=""
                    label="정정 저장"
                    off={busy || !f.reason.trim()}
                    onClick={() => {
                      const b: Record<string, unknown> = { target_seq: r.seq, reason: f.reason.trim() };
                      if (isPre) b.values = f.values.trim();
                      else {
                        if (f.value && f.value !== r.value) b.value = f.value;
                        if (f.memo.trim() !== (r.memo ?? "")) b.memo = f.memo.trim();
                      }
                      void save(b, r);
                    }}
                  />
                </div>
              </div>
            ) : r.correctable ? (
              <span
                className="btn lg full ghost"
                role="button"
                tabIndex={0}
                onClick={() => setForm({ seq: r.seq, f: { kind: "correct", value: (r.value as Value) ?? null, memo: r.memo ?? "", values: r.values ?? "", reason: "" } })}
              >
                <Icon name="pencil" />
                이 기록 정정
              </span>
            ) : r.notable ? (
              <span className="btn lg full ghost" role="button" tabIndex={0} onClick={() => setForm({ seq: r.seq, f: { kind: "note", memo: "" } })}>
                <Icon name="message-square-plus" />
                메모 덧붙이기
              </span>
            ) : closedNote ? (
              <div className="hint">
                <Icon name="lock" />
                {closedNote}
              </div>
            ) : null}
            <div className="pfoot">
              <span className="btn ghost" role="button" tabIndex={0} onClick={p.onCurrent}>
                지금 단계로
              </span>
            </div>
          </div>
        </div>
      </div>
      {confirm ? (
        <>
          <div className="dim" onClick={() => !busy && setConfirm(null)} />
          <div className="modal" role="dialog" aria-label="이 정정으로 다음 지시가 바뀝니다">
            <div className="mh">
              <Icon name="triangle-alert" />
              <b>이 정정으로 다음 지시가 바뀝니다</b>
            </div>
            <div className="banner warn">
              <Icon name="triangle-alert" />
              <div>
                <b>
                  {confirm.r.section} {confirm.r.title} · {confirm.r.value} → {String((confirm.body.value as string | undefined) ?? confirm.r.value)}
                </b>
                그 점검부터 다시 판정합니다. 지금 지시 {String((confirm.detail.current as { section?: string })?.section ?? "")}{" "}
                {String((confirm.detail.current as { title?: string })?.title ?? "")}
                {p.unsent ? `의 보내지 않은 입력(${p.unsent.desc})은 지워지고` : "은(는) 닫히고"} 새 지시가 나옵니다.
              </div>
            </div>
            <span className="sm">사유 · {String(confirm.body.reason)}</span>
            <div className="mb">
              <Btn cls="ghost" label="취소" onClick={() => setConfirm(null)} />
              <Btn cls="warnb" label="정정 저장" off={busy} onClick={() => void save({ ...confirm.body, confirmed: true }, confirm.r)} />
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}
