// 두 앱 공통 화면 조각(Prototype v17 구조 · 승인 CSS class 그대로): 상단 바 · 진행 표시 · 등급 배지 · 기록 카드 · 기록 창.

import type { ReactNode } from "react";
import type { HistoryRecord, Schemas } from "./api";
import { dateTime, num } from "./format";
import { Icon } from "./icons";

export const GRADE_LABEL: Record<string, string> = { A: "단독 가능", B: "확인 후", C: "승인 필요", D: "범위 밖" };

export function GradeBadge({ grade }: { grade: string | null | undefined }) {
  if (!grade) return null;
  return (
    <span className={`gb g${grade}`}>
      <b>{grade}</b>
      {GRADE_LABEL[grade] ?? ""}
    </span>
  );
}

export interface TopBarProps {
  context: string | null; // 현재 알람 표시줄(설비 · 증상 · 발생 시각), 없으면 '진행 중인 알람 없음'
  clock: string;
  homeOn: boolean;
  bell: number;
  user: { name: string; role: string };
  onHome: () => void;
  onBell: () => void;
  onSwitchUser?: () => void; // 피드백 F12: 정비자 앱 [사용자 바꾸기](로그아웃 → 로그인 화면)
}

export function TopBar(p: TopBarProps) {
  return (
    <div className="ap-top">
      <div className="brand">
        <span className="logo">
          <Icon name="map" />
        </span>
        정비 길잡이
      </div>
      {p.context ? (
        <div className="tb-ctx">
          <span className="dot red" />
          {p.context}
        </div>
      ) : (
        <div className="tb-ctx muted">진행 중인 알람 없음</div>
      )}
      <div className="tb-r">
        <span className="tb-clock">
          <Icon name="clock" />
          {p.clock}
        </span>
        <span className={`tb-home${p.homeOn ? " on" : ""}`} role="button" tabIndex={0} onClick={p.onHome}>
          <Icon name="house" />홈
        </span>
        <span className="tb-bell" role="button" tabIndex={0} aria-label="알람 · 할 일" onClick={p.onBell}>
          <Icon name="bell" />
          {p.bell > 0 ? <i>{p.bell}</i> : null}
        </span>
        <span className="tb-user">
          <span className="av">{p.user.name.slice(0, 1)}</span>
          {p.user.name} · {p.user.role}
        </span>
        {p.onSwitchUser ? (
          <span className="tb-switch" role="button" tabIndex={0} onClick={p.onSwitchUser}>
            사용자 바꾸기
          </span>
        ) : null}
      </div>
    </div>
  );
}

const STEPS = ["알람", "진단", "점검", "조치", "보고서"];

/** 진행 표시 5단계. 완료 단계는 눌러 그 단계의 지난 화면(T15)을 연다(pick), 보는 단계는 테두리(view).
 * 지금 단계도 이미 보낸 기록이 있으면 눌러 기록을 본다(생성자 해석 ①) — 표시는 'on' 그대로. */
export function StepBar({
  current,
  view,
  onPick,
  currentPickable,
  done,
}: {
  current: number;
  view?: number | null;
  onPick?: (n: number) => void;
  currentPickable?: boolean;
  /** 지금 단계도 끝남(확정한 보고서 — 가이드 19쪽): 초록 ✓로 그림(누르기는 currentPickable 그대로). */
  done?: boolean;
}) {
  const out: ReactNode[] = [];
  // 지난 단계를 보는 동안(view): 보는 단계가 지금 꼴(파랑) · 그 앞은 끝남 · 그 뒤는 회색(가이드 정비자 23쪽) — 누름은 그대로
  const top = view ?? current;
  STEPS.forEach((label, i) => {
    const n = i + 1;
    const clickable = onPick && n !== view && (n < current || (n === current && currentPickable));
    const pick = clickable ? " pick" : "";
    const cls = n < top ? `sp done${pick}` : n === top ? (done && view == null ? "sp done" : "sp on") : `sp ${pick.trim()}`;
    if (i > 0) out.push(<span key={`l${n}`} className={`spl ${n <= top ? "done" : ""}`} />);
    out.push(
      <div
        key={n}
        className={cls}
        {...(clickable ? { role: "button", tabIndex: 0, onClick: () => onPick(n), "aria-label": `${label} 지난 화면` } : {})}
      >
        <span className="spn">{n < top || (done && view == null && n === current) ? <Icon name="check" /> : n}</span>
        {label}
      </div>,
    );
  });
  return <div className="ap-step">{out}</div>;
}

export function RecordCard({ card, onOpen }: { card: Schemas["RecordCard"]; onOpen: (id: string) => void }) {
  return (
    <div className="hc" role="button" tabIndex={0} onClick={() => onOpen(card.record_id)}>
      <div className="hc-t">
        <b>{card.record_id}</b>
        <span>
          {card.equipment_id} · {card.month}월
        </span>
        {card.similarity_pct != null ? <em>유사 {card.similarity_pct}%</em> : null}
      </div>
      <div className="hc-c">{card.outcome_label}</div>
      <span>
        {/* D-663 덧1: 알람 없는 시연 정비 줄 = 정지 시간 칸 없음 → '정비 기록' */}
        {card.downtime_min != null ? `${card.action_name} · 정지 ${card.downtime_min}분` : `${card.action_name} · 정비 기록`}
      </span>
    </div>
  );
}

function sensorsText(s: Record<string, number>): string {
  const parts: string[] = [];
  if (s.air_temperature_k != null) parts.push(`공기 ${s.air_temperature_k.toFixed(1)} K`);
  if (s.process_temperature_k != null) parts.push(`공정 ${s.process_temperature_k.toFixed(1)} K`);
  if (s.rotational_speed_rpm != null) parts.push(`${num(s.rotational_speed_rpm)} rpm`);
  if (s.torque_nm != null) parts.push(`${s.torque_nm.toFixed(1)} Nm`);
  if (s.tool_wear_min != null) parts.push(`마모 ${num(s.tool_wear_min)}분`);
  return parts.join(" · ");
}

function pathText(steps: HistoryRecord["steps"]): string {
  return steps
    .filter((s) => s.name)
    .map((s) => {
      const inner = [s.section, s.kind === "check" ? s.result : null].filter(Boolean).join(", ");
      return inner ? `${s.name}(${inner})` : `${s.name}`;
    })
    .join(" → ");
}

/** T13 과거 기록 창: 닫으면 원래 화면 · 단계 그대로(AC-38). */
export function RecordModal({ record, onClose }: { record: HistoryRecord; onClose: () => void }) {
  const r = record;
  const sym = r.symptom as { name?: string };
  const al = r.alarm as { sensor?: string; value?: number; unit?: string; boundary?: number };
  const oc = r.outcome as { type?: string; cause_name?: string };
  const action = [...r.steps].reverse().find((s) => s.kind === "action")?.name;
  return (
    <>
      <div className="dim" />
      <div className="modal wide" role="dialog" aria-label={`과거 기록 ${r.record_id}`}>
        <div className="mh">
          <Icon name="history" />
          <b>과거 기록 {r.record_id}</b>
          {r.approval ? (
            <span className="ch gray">
              {r.approval.status}
              {r.approval.by ? ` · ${r.approval.by}` : ""}
            </span>
          ) : null}
          <span className="btn ghost sm2 mclose" role="button" tabIndex={0} onClick={onClose}>
            닫기
          </span>
        </div>
        <div className="kv">
          <b>설비</b>
          <span>
            {r.equipment_id}
            {r.equipment_type ? ` · ${r.equipment_type}` : ""}
          </span>
          <b>발생 시각</b>
          <span>{dateTime(r.occurred_at)}</span>
          <b>증상</b>
          <span>
            {sym.name} · {al.sensor} {al.value} {al.unit}(경계 {al.boundary})
          </span>
          <b>센서 값</b>
          <span>{sensorsText(r.sensor_snapshot)}</span>
          <b>점검 경로</b>
          <span>{pathText(r.steps)}</span>
          <b>확정 원인</b>
          <span>{oc.cause_name ?? oc.type}</span>
          <b>조치</b>
          <span>{action ?? "-"}</span>
          <b>정지 시간</b>
          <span>{r.downtime_min}분</span>
        </div>
        <div className="sub">서술</div>
        <p className="ai" style={{ fontSize: 14 }}>
          {r.narrative}
        </p>
      </div>
    </>
  );
}
