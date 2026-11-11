// 파트장 2 보고서 검토 = 최종본 문서(가이드 31 · 32 · 36쪽): 결정은 승인 · 반려 둘(수정 후 승인 화면 뺌 — 서버 edit 길은 있으나 안 씀).
// 머리 띠 오른쪽 「출력」(버튼만 · 동작 0) · 「반려」 · 「승인」 → 확인 창. 반려는 사유를 적어야 열림(AC-24), 승인 메모는 고를 수 있음.
// 승인 대기가 아니면(승인 · 반려 뒤) 결정 버튼 없이 보기만.

import { useRef, useState } from "react";
import { errorDetail, FinalReport, type Api, type Schemas } from "@c1a15/ui";
import { Bar, LBtn, Modal, SaveError } from "./Frame";

type Final = Schemas["FinalReport"];

export function Review({ api, report, onBack, onDone }: { api: Api; report: Final; onBack: () => void; onDone: () => void }) {
  const [ask, setAsk] = useState<"approve" | "reject" | null>(null);
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = report.status === "awaiting_approval";

  const open = (d: "approve" | "reject") => {
    setMemo("");
    setError(null);
    setAsk(d);
  };
  const saving = useRef(false); // EV-007: 같은 순간 두 번 누름도 한 번만 보냄
  const save = async () => {
    if (!ask || saving.current) return;
    saving.current = true;
    setBusy(true);
    setError(null);
    const { data, error: err } = await api.POST("/api/lead/reports/{report_id}/decision", {
      params: { path: { report_id: report.report_id } },
      body: { decision: ask, edits: {}, memo: memo.trim() || null },
    }).finally(() => {
      saving.current = false; // 예외로 끝나도 풀림(EV-007 review)
    });
    setBusy(false);
    if (data) onDone();
    else setError(errorDetail(err) ?? "save_failed");
  };

  return (
    <>
      <Bar crumb={{ label: "승인 대기", onClick: onBack, here: `${report.equipment_id} 보고서` }}>
        <LBtn cls="g" label="출력" onClick={() => undefined} />
        {pending ? (
          <>
            <LBtn cls="r" label="반려" onClick={() => open("reject")} />
            <LBtn label="승인" onClick={() => open("approve")} />
          </>
        ) : null}
      </Bar>
      <div className="fwrap">
        <FinalReport data={report} />
      </div>
      {ask ? (
        <Modal
          title={`${report.equipment_id} 보고서를 ${ask === "approve" ? "승인" : "반려"}합니까?`}
          okLabel={ask === "approve" ? "승인" : "반려"}
          okOff={busy || (ask === "reject" && !memo.trim())}
          onCancel={() => setAsk(null)}
          onOk={() => void save()}
        >
          <label htmlFor="decision-memo">{ask === "approve" ? "메모 (선택)" : "반려 사유"}</label>
          <textarea id="decision-memo" aria-label={ask === "approve" ? "메모" : "반려 사유"} value={memo} onChange={(e) => setMemo(e.target.value)} />
          <SaveError error={error} />
        </Modal>
      ) : null}
    </>
  );
}
