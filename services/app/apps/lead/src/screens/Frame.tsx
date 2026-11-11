// 파트장 앱 틀(D-528 최종안 · 가이드 파트장 1 ~ 5): 짙은 왼쪽 메뉴 넷(아이콘 0 · 개수는 회색 칸 숫자) + 아래 사용자, 본문 위 흰 머리 띠.
// 메뉴 이동은 본문만 바꾼다. 머리 띠(제목 · 날짜 · 경로 · 버튼)는 화면마다 Bar로 그린다.

import type { ReactNode } from "react";
import { dateDay } from "@c1a15/ui";

export type Menu = "approvals" | "calls" | "grades" | "audit";

const MENUS: { id: Menu; label: string }[] = [
  { id: "approvals", label: "승인 대기" },
  { id: "calls", label: "호출" },
  { id: "grades", label: "권한 등급" },
  { id: "audit", label: "감사 기록" },
];

/** "10월 31일(토) 14:14 · 야간 22:00 ~ 06:00" — 근무는 야간 고정(결정 014 ④, server screen.py NIGHT_START · NIGHT_END). */
export function headDate(iso: string): string {
  const m = /^\d+-(\d+)-(\d+) \((.)\) (\d\d:\d\d)$/.exec(dateDay(iso));
  return m ? `${Number(m[1])}월 ${Number(m[2])}일(${m[3]}) ${m[4]} · 야간 22:00 ~ 06:00` : "";
}

export function Frame({
  menu,
  me,
  counts,
  onMenu,
  children,
}: {
  menu: Menu;
  me: { display_name: string };
  counts: Partial<Record<Menu, number>>;
  onMenu: (m: Menu) => void;
  children: ReactNode;
}) {
  return (
    <div className="L">
      <nav className="lnav">
        <div className="lbrand">
          정비 길잡이<span>파트장</span>
        </div>
        {MENUS.map((m) => (
          <a key={m.id} className={menu === m.id ? "on" : ""} role="button" tabIndex={0} onClick={() => onMenu(m.id)}>
            {m.label}
            {counts[m.id] ? <span className="c">{counts[m.id]}</span> : null}
          </a>
        ))}
        <div className="who">
          <b>{me.display_name}</b>
          <span>정비 파트장</span>
        </div>
      </nav>
      <main className="lmain">{children}</main>
    </div>
  );
}

/** 흰 머리 띠: 제목 + 날짜, 또는 경로(앞 화면 링크 / 지금) + 오른쪽 버튼. */
export function Bar({
  title,
  date,
  crumb,
  children,
}: {
  title?: string;
  date?: string;
  crumb?: { label: string; onClick: () => void; here: string };
  children?: ReactNode;
}) {
  return (
    <div className="lbar">
      {crumb ? (
        <div className="lcrumb">
          <a role="link" tabIndex={0} onClick={crumb.onClick}>
            {crumb.label}
          </a>{" "}
          / {crumb.here}
        </div>
      ) : (
        <h2>{title}</h2>
      )}
      {date ? <span className="dt">{date}</span> : null}
      {children ? (
        <>
          <span className="sp" />
          {children}
        </>
      ) : null}
    </div>
  );
}

/** 각진 글자 버튼(.lbtn · .g 테두리 · .r 반려). 열리지 않으면 disabled. */
export function LBtn({ label, cls = "", off, onClick }: { label: string; cls?: string; off?: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`lbtn ${cls}`.trim()} disabled={off} onClick={() => !off && onClick()}>
      {label}
    </button>
  );
}

/** 결정 확인 창(.lmdl): 제목 · 내용 · 취소 / 확인 두 버튼. */
export function Modal({
  title,
  children,
  okLabel,
  okOff,
  onCancel,
  onOk,
}: {
  title: string;
  children?: ReactNode;
  okLabel: string;
  okOff?: boolean;
  onCancel: () => void;
  onOk: () => void;
}) {
  return (
    <>
      <div className="dim" onClick={onCancel} />
      <div className="lmdl" role="dialog" aria-label={title}>
        <h4>{title}</h4>
        {children}
        <div className="mb">
          <LBtn cls="g" label="취소" onClick={onCancel} />
          <LBtn label={okLabel} off={okOff} onClick={onOk} />
        </div>
      </div>
    </>
  );
}

/** 저장 실패 줄(지금 앱 문구 그대로 — 저장하지 못했습니다 + 서버 사유). */
export function SaveError({ error }: { error: string | null }) {
  return error ? (
    <div className="lerr" role="alert">
      <b>저장하지 못했습니다</b>
      {error}
    </div>
  ) : null;
}
