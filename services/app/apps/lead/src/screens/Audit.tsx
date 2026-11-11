// 파트장 5 감사 기록(가이드 41쪽): 정비자 앱 · 파트장 앱 기록을 시각순으로 · 「전체 · 정비자 앱 · 파트장 앱」 칩(머리 띠).
// 지금 거르기 셋(설비 · 사용자 · 기간 — 바꾸면 즉시 서버가 거름, AC-26-C2)은 남김(D-530 답장 001-zzo 1). 칩은 받은 행을 앱으로만 거른다.
// 행동 문구 · 대상 · 사용자 이름은 서버가 준다. 시스템 행은 「전체」에만.

import { useState } from "react";
import { dateTime, hhmm, type Schemas } from "@c1a15/ui";
import { appLabel } from "./Approvals";
import { Bar } from "./Frame";

type Audit = Schemas["LeadAudit"];
export type AuditFilter = { equipment: string | null; user_id: string | null; date: string | null };
export type AuditApp = "all" | "정비자" | "파트장";
const CHIPS: { id: AuditApp; label: string }[] = [
  { id: "all", label: "전체" },
  { id: "정비자", label: "정비자 앱" },
  { id: "파트장", label: "파트장 앱" },
];

function Select({
  label,
  value,
  options,
  onPick,
}: {
  label: string;
  value: string;
  options: { value: string | null; label: string }[];
  onPick: (v: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="lsel" role="button" tabIndex={0} aria-label={`${label} 고르기`} onClick={() => setOpen(!open)}>
      {label} · {value}
      {open ? (
        <div className="dd">
          {options.map((o) => (
            <div
              key={o.label}
              role="option"
              aria-selected={o.label === value}
              className={o.label === value ? "on" : ""}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onPick(o.value);
              }}
            >
              {o.label}
            </div>
          ))}
        </div>
      ) : null}
    </span>
  );
}

function days(today: string): string[] {
  const d = new Date(`${today}T12:00:00+09:00`);
  return Array.from({ length: 7 }, (_, i) => new Date(d.getTime() - i * 86400000).toISOString().slice(0, 10));
}

export function AuditScreen({
  data,
  app,
  onApp,
  onFilter,
}: {
  data: Audit;
  app: AuditApp;
  onApp: (a: AuditApp) => void;
  onFilter: (f: AuditFilter) => void;
}) {
  const f: AuditFilter = { equipment: data.filters.equipment ?? null, user_id: data.filters.user_id ?? null, date: data.date };
  const userName = data.options.users.find((u) => u.user_id === f.user_id)?.name ?? "전체";
  const rows = data.rows.filter((r) => app === "all" || r.app === app);
  return (
    <>
      <Bar title="감사 기록">
        <div className="lchips" role="radiogroup" aria-label="앱 거르기">
          {CHIPS.map((c) => (
            <button key={c.id} type="button" role="radio" aria-checked={app === c.id} className={app === c.id ? "on" : ""} onClick={() => onApp(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
      </Bar>
      <div className="lflt">
        <Select
          label="설비"
          value={f.equipment ?? "전체"}
          options={[{ value: null, label: "전체" }, ...data.options.equipment.map((e) => ({ value: e, label: e }))]}
          onPick={(v) => onFilter({ ...f, equipment: v })}
        />
        <Select
          label="사용자"
          value={userName}
          options={[{ value: null, label: "전체" }, ...data.options.users.map((u) => ({ value: u.user_id, label: u.name }))]}
          onPick={(v) => onFilter({ ...f, user_id: v })}
        />
        <Select
          label="기간"
          value={data.date}
          options={days(dateTime(data.now).slice(0, 10)).map((d) => ({ value: d, label: d }))}
          onPick={(v) => onFilter({ ...f, date: v })}
        />
      </div>
      <div className="lsect">
        <div className="lsect-h">
          기록<span className="c">{rows.length}건</span>
        </div>
        <table className="lt">
          <tbody>
            <tr>
              <th className="n">시각</th>
              <th>앱</th>
              <th>사용자</th>
              <th>행동</th>
              <th>대상</th>
            </tr>
            {rows.map((r) => (
              <tr key={r.audit_id} data-audit={r.audit_id}>
                <td className="n">{hhmm(r.at)}</td>
                <td>{appLabel(r.app)}</td>
                <td>{r.user_name}</td>
                <td className="w">{r.action}</td>
                <td>{r.target}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length ? null : <div className="lempty">기록이 없습니다</div>}
      </div>
    </>
  );
}
