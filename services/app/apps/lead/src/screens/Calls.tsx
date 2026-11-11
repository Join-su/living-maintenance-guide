// 파트장 3 호출(가이드 37 · 38쪽): 목록(시각 · 설비 · 알람 · 유형 · 보낸 사람 · 필요한 작업 · 답하기) · 답하기(정비자 요약 · 답 선택지 · 처리 메모 · 답 저장).
// 선택지는 서버가 준다(D등급 호출에는 '승인 후 진행'이 없음 — AC-23-C3). 답을 골라야 「답 저장」이 열린다. 보낸 뒤 정비자 정정 상자(S-05 P-15).

import { useRef, useState } from "react";
import { errorDetail, hhmm, type Api, type Schemas } from "@c1a15/ui";
import { KIND_LABEL } from "./Approvals";
import { Bar, LBtn, SaveError } from "./Frame";

type Calls = Schemas["LeadCalls"];
type Call = Schemas["LeadCall"];

/** 정정 전 → 후: 결과가 바뀌면 결과, 아니면 메모 · 측정값. */
function change(before: Record<string, unknown> | null | undefined, after: Record<string, unknown> | null | undefined): string {
  const b = before ?? {};
  const a = { ...b, ...(after ?? {}) };
  if ("values" in b) return `${String(b.values ?? "")} → ${String(a.values ?? "")}`;
  if (b.value !== a.value) return `${String(b.value)} → ${String(a.value)}`;
  return `메모 '${String(b.memo ?? "")}' → '${String(a.memo ?? "")}'`;
}

export function CallList({ data, onOpen }: { data: Calls; onOpen: (callId: number) => void }) {
  return (
    <>
      <Bar title="호출" />
      <div className="lsect">
        <div className="lsect-h">
          처리할 호출<span className="c">{data.rows.length}건</span>
        </div>
        <table className="lt">
          <tbody>
            <tr>
              <th className="n">시각</th>
              <th>설비 · 알람</th>
              <th>유형</th>
              <th>보낸 사람</th>
              <th>필요한 작업</th>
              <th />
            </tr>
            {data.rows.map((r) => (
              <tr key={r.call_id} className="pick" data-call={r.call_id} onClick={() => onOpen(r.call_id)}>
                <td className="n">{r.sent_at ? hhmm(r.sent_at) : ""}</td>
                <td>
                  <b>{r.equipment_id}</b>
                  <div className="m">{r.group_name}</div>
                </td>
                <td>
                  <span className={`tx ${r.kind === "handover" ? "bad" : "warn"}`}>{KIND_LABEL[r.kind]}</span>
                </td>
                <td>{r.sender_name}</td>
                <td className="w">{r.need}</td>
                <td className="r">
                  <LBtn cls="g" label="답하기" onClick={() => onOpen(r.call_id)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.rows.length === 0 ? <div className="lempty">처리할 호출이 없습니다</div> : null}
      </div>
    </>
  );
}

export function CallDetail({ api, call, onBack, onDone }: { api: Api; call: Call; onBack: () => void; onDone: () => void }) {
  const [answer, setAnswer] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const kind = KIND_LABEL[call.kind];
  const given = call.answer as { answer?: string; note?: string | null } | null;
  const picked = call.answered ? (given?.answer ?? null) : answer;
  const pickedLabel = call.options.find((o) => o.value === given?.answer)?.label ?? given?.answer ?? "";

  const saving = useRef(false); // EV-007: 같은 순간 두 번 누름도 한 번만 보냄
  const save = async () => {
    if (!answer || saving.current) return;
    saving.current = true;
    setBusy(true);
    setError(null);
    const { data, error: err } = await api.POST("/api/lead/calls/{call_id}/answer", {
      params: { path: { call_id: call.call_id } },
      body: { answer: answer as "approved" | "daytime" | "direct" | "assign", note: note.trim() || null },
    }).finally(() => {
      saving.current = false; // 예외로 끝나도 풀림(EV-007 review)
    });
    setBusy(false);
    if (data) onDone();
    else setError(errorDetail(err) ?? "save_failed");
  };

  return (
    <>
      <Bar crumb={{ label: "호출", onClick: onBack, here: `${call.equipment_id} ${kind}` }} />
      <div className="rhd">
        <h2>
          {call.equipment_id} · {kind}
        </h2>
        <span className={`tx ${call.answered ? "" : "warn"}`}>{call.answered ? `답함 · ${pickedLabel}` : "답 기다림"}</span>
      </div>
      <div className="rsub">
        {[call.group_name, call.sender_name, `보냄 ${call.sent_at ? hhmm(call.sent_at) : ""}`].filter(Boolean).join(" · ")}
      </div>
      <div className="cgrid">
        <div className="lsect">
          <div className="lsect-h">정비자가 보낸 요약</div>
          <div className="lsum">{call.summary}</div>
          <div className="lkv" style={{ paddingTop: 0 }}>
            <div>
              <span>필요한 작업</span>
              {call.need}
            </div>
          </div>
          {(call.corrections ?? []).map((x, i) => (
            // L04 / call_corrected: 보낸 뒤 정비자 정정(AC-47-C2 · 결정 019 ② ③) — 새 호출 없이 이 호출에 붙는다
            <div className="lcorr" key={i} data-part="correction">
              <b>보낸 뒤 정비자 정정 · {x.at ? hhmm(x.at) : ""}</b>
              <div>
                {x.section} {x.title} · {change(x.before, x.after)}
              </div>
              <div>사유 · {x.reason}</div>
            </div>
          ))}
        </div>
        <div className="lsect">
          <div className="lsect-h">답하기</div>
          <div className="lopts" role="radiogroup" aria-label="답">
            {call.options.map((o) => (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={picked === o.value}
                className={picked === o.value ? "on" : ""}
                disabled={call.answered}
                onClick={() => setAnswer(o.value)}
              >
                <i />
                {o.label}
              </button>
            ))}
          </div>
          <div style={{ padding: "0 18px" }}>
            <label className="llab" htmlFor="call-note">
              처리 메모
            </label>
            <textarea
              id="call-note"
              className="lta"
              aria-label="처리 메모"
              disabled={call.answered}
              value={call.answered ? (given?.note ?? "") : note}
              onChange={(e) => setNote(e.target.value)}
            />
            <SaveError error={error} />
          </div>
          <div style={{ padding: "14px 18px 18px", textAlign: "right" }}>
            {call.answered ? null : <LBtn label="답 저장" off={busy || !answer} onClick={() => void save()} />}
          </div>
        </div>
      </div>
    </>
  );
}
