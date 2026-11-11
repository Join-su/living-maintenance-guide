// T04 진단: 원인 후보 순위(이름 = cause_names, A-04) · 비슷한 과거 기록 3건(P-19 ④) · 다음 점검(미리보기, P-06 · P-18).
// 첫 판정이 호출이면 같은 구조에서 강조 카드 값만 바뀐다(T04 / first_call, 예외는 이 상태에서만 — CO-42).
// D-528(가이드 정비자 4): 순위 아래 「근거 n = …」 설명 · 과거 기록 아래 안내 문장 뺌.
// D-637(리허설 F1 · F8 · F2 · 표시 · 글만, 셈 손 0): 확률(판정 출력 probabilities = 이 설비의 지난 같은 알람 기록 원인 비율)과
// 오른쪽 수(evidence = 비슷한 기록 상위 10건 중 그 원인)는 출처가 달라 '근거 n' → '비슷한 기록 n건' · 순위 아래 출처 한 줄 ·
// 100%면 까닭 한 줄 · 지시 카드 이유 아래 고르는 셈 한 줄(rules.best_pick: A · B 점검 중 원인 확률 ÷ 분이 가장 큰 것).
// 리허설 2(R3 · R6, 표시 · 글만): 순위 줄 오른쪽 = 확률 셈에 쓴 '이 설비 기록 n건 중 k건'(Pack 판정 출력 equipment_counts · total 그대로) ·
// 비슷한 기록은 순위 줄에서 빼고 그 칸 이름에 '참고(다른 설비 포함) · 순위 계산에 안 씀' · 출처 줄은 새 셈(원인마다 기본 1건)을 말함.
// 같은 그룹으로 넓혀 셌으면(count_scope group) '같은 그룹 기록 n건 중 k건' · 출처 줄에 까닭.
// n · k 칸이 없으면(옛 Pack · 옛 세션) 숫자 자리 숨김 · 출처 줄은 비율만 · 옛 셈의 100% 까닭 줄은 뺌(옛 값 글 남김 0).
// 리허설 F11: 다음 점검 카드에 예외가 맞은 까닭 · 안 맞은 까닭 한 줄(서버 글 그대로).
// 재시연 3(R1 · R2, 표시 · 글만 · Pack 셈 손 0): 순위 · 고른 이유에서 % 빼고 건수만('지난 같은 알람 n건 중 k건' + 막대 = k ÷ n) ·
// 기록 적으면 '기록 n건 · 확정 아님' · 셈 식 줄 뺌 · 고른 이유 = record_counts 값으로 짠 말 한 줄(n · k 칸이 없으면 서버 글 그대로) ·
// 다른 설비 센서 비슷한 기록은 기본 화면에서 빼고 접힌 '다른 설비 참고'로만.
// 재시연 5(D-663 S1, 표시 · 근거 고르는 곳만 · Pack 셈 · 순위 손 0): 순위 줄 = '40% · 5건 중 2건'만(% = k ÷ n, 기본 점수 없이) ·
// 고른 이유도 짧게('냉각 필터 막힘 40% · 5건 중 2건 · 약 3분') · 근거 칸 = 1위 후보로 확정된 지난 같은 알람 기록(서버 top_cards) ·
// 다른 설비 참고 카드는 화면에서 뺌.

import { useState } from "react";
import {
  GradeBadge,
  Icon,
  RecordCard,
  type SessionWithPreview,
} from "@c1a15/ui";
import { SwitchRows } from "./Instruction";
import { SecChip } from "./Lock";

type Diag = Pick<
  SessionWithPreview,
  | "probabilities"
  | "evidence"
  | "equipment_counts"
  | "equipment_total"
  | "count_scope"
  | "count_prior"
  | "cause_names"
  | "similar_cards"
  | "top_cards"
  | "low_evidence"
  | "preview"
  | "exception_topic"
  | "exception_why"
  | "exception_miss"
>;

// 순서 = Pack 순위(probabilities) 그대로 · 막대 = n · k가 있으면 k ÷ n, 없으면(옛 세션) 그 비율 — 화면 글에 % 0.
export function rankRows(
  s: Diag,
): { code: string; name: string; pct: number; n: number; k: number | null; bar: number }[] {
  const total = s.equipment_counts ? s.equipment_total : null;
  const counts = total != null ? s.equipment_counts : null;
  return Object.entries(s.probabilities)
    .map(([code, pct]) => {
      const k = counts ? (counts[code] ?? 0) : null;
      return {
        code,
        name: s.cause_names[code] ?? code,
        pct,
        n: s.evidence[code] ?? 0,
        k,
        bar: k != null && total ? Math.round((k / total) * 100) : k != null ? 0 : pct,
      };
    })
    .sort((a, b) => b.pct - a.pct);
}

// 기록이 이보다 적으면 '기록 n건 · 확정 아님'(표시만 · Pack 셈과 무관)
export const FEW_RECORDS = 10;

// 셈 범위(Pack record_counts.scope): group = 같은 증상 전체 기록으로 셈 — 기본 건수가 있으면(이 설비 셈) 이 설비 기록이 3건 미만이라 넓힘.
const isGroup = (s: Diag) => s.count_scope === "group";
const shownTotal = (s: Diag) => (s.equipment_counts ? (s.equipment_total ?? null) : null);
// D-663 S1: 순위 줄 · 고른 이유의 짧은 글 = 'k ÷ n %' · 'n건 중 k건'
export const pctOf = (k: number, n: number) => (n > 0 ? Math.round((k / n) * 100) : 0);
export const shortCount = (k: number, n: number) => `${pctOf(k, n)}% · ${n}건 중 ${k}건`;

// 지시 카드(A · B)의 고른 이유 = 건수 말 한 줄 — 호출 · 고른 점검 · 원인 모름 · n · k 칸 없음이면 null(서버 글 그대로).
export function pickWords(s: Diag): string | null {
  const p = s.preview;
  // D-652 12-f: Pack 옵션이 첫 점검을 바꿨으면 정해 둔 글(확률 · 건수 말 0 · 서버 이유 글과 같은 꼴)
  const pb = p.picked_by;
  if (pb && !p.picked) return `먼저 볼 점검으로 정해 둔 ${pb.title}(${pb.section}절) — 원래 ${pb.instead_title}(${pb.instead_of}절)`;
  const n = shownTotal(s);
  if (n == null || p.picked || !p.cause || !p.title || (p.kind !== "instruct" && p.kind !== "instruct_after_checklist")) return null;
  const name = s.cause_names[p.cause] ?? p.cause;
  const k = s.equipment_counts?.[p.cause] ?? 0;
  const mins = p.minutes != null ? ` · 약 ${p.minutes}분` : "";
  return `${name} ${shortCount(k, n)}${mins}`;
}

export function NextCard({
  s,
  onStart,
  past,
  onSwitch,
}: {
  s: Diag;
  onStart: () => void;
  past?: boolean;
  onSwitch?: (section: string) => void; // D-585 진단에서 고름 — 잠금 · 사전 확인 뒤 그 점검
}) {
  const p = s.preview;
  const pick = p.picked;
  const call = !pick && p.kind === "call";
  const [switching, setSwitching] = useState(false);
  const words = pickWords(s);
  // F11: 예외 후보가 왜 맞았나(배운 기기 · 근거 기록 · 이 기기 같은 정비 · 며칠 전) · 같은 증상인데 왜 안 맞았나(최근 같은 정비 없음)
  const exLine = s.exception_why ? (
    <span className="xwhy" data-part="ex-why">
      예외 후보 · {s.exception_why}
    </span>
  ) : s.exception_miss ? (
    <span className="xwhy" data-part="ex-miss">
      {s.exception_miss}
    </span>
  ) : null;
  return (
    <div className="cd hl">
      <div className="nx">
        <span className="sm">다음 점검</span>
        <h3>{pick ? pick.title : call ? "파트장 호출 필요" : p.title}</h3>
        <div className="tags">
          <GradeBadge grade={pick ? pick.grade : p.grade} />
          {(pick ?? p).section ? (
            <SecChip
              section={(pick ?? p).section ?? ""}
              topic={s.exception_topic}
            />
          ) : null}
          {!pick && p.minutes != null ? (
            <span className="ch gray">
              {call ? `${p.title} · 약 ${p.minutes}분` : `약 ${p.minutes}분`}
            </span>
          ) : null}
          {!pick && p.reason_fallback ? (
            <span className="ch fb">{p.reason_label}</span>
          ) : null}
        </div>
        {p.reason && !pick ? (
          <div className="why">
            <b>{call ? "호출이 필요한 이유" : "이 점검을 고른 이유"}</b>
            {words ? <span data-part="pick-rule">{words}</span> : <span>{p.reason}</span>}
            {exLine}
          </div>
        ) : (
          exLine
        )}
        {!past && onSwitch && p.candidates?.length ? (
          <div className="refs">
            <span
              className="lk sw"
              role="button"
              tabIndex={0}
              onClick={() => setSwitching(true)}
            >
              <Icon name="arrow-left-right" />
              점검 바꾸기
            </span>
          </div>
        ) : null}
        {s.low_evidence ? (
          <div className="banner warn">
            <Icon name="triangle-alert" />
            <div>
              <b>근거 적음</b>비슷한 과거 기록이 3건 미만입니다. 지시는 그대로
              따르되, 결과 메모를 더 자세히 적으십시오.
            </div>
          </div>
        ) : null}
        <span
          className={
            past
              ? `btn lg dis full${call ? " warnb" : ""}`
              : `btn lg full${call ? " warnb" : ""}`
          }
          role="button"
          tabIndex={0}
          onClick={past ? undefined : onStart}
        >
          점검 시작 <Icon name="arrow-right" />
        </span>
      </div>
      {switching && onSwitch ? (
        <>
          <div className="dim" />
          <div className="modal swm" role="dialog" aria-label="점검 바꾸기">
            <div className="mh">
              <Icon name="arrow-left-right" />
              <b>점검 바꾸기</b>
            </div>
            <div className="swl">
              <SwitchRows
                cands={(p.candidates ?? []).filter(
                  (c) => c.section !== pick?.section,
                )}
                busy={false}
                topic={s.exception_topic}
                onPick={(section) => {
                  setSwitching(false);
                  onSwitch(section);
                }}
              />
            </div>
            <div className="mb">
              <span
                className="btn ghost"
                role="button"
                tabIndex={0}
                onClick={() => setSwitching(false)}
              >
                취소
              </span>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

export function DiagnosisScreen({
  s,
  onRecord,
  onStart,
  past,
  onSwitch,
}: {
  s: Diag;
  onRecord: (id: string) => void;
  onStart: () => void;
  past?: boolean; // T15 / diagnosis: 그때의 원인 순위 · 첫 지시, 조작은 잠김
  onSwitch?: (section: string) => void;
}) {
  const rows = rankRows(s);
  const n = shownTotal(s);
  const top = rows[0];
  return (
    <div className={past ? "ap-body past" : "ap-body"}>
      <div className="g64">
        <div>
          <div className="cd ">
            <div className="cd-h">
              <Icon name="list-checks" />
              <b>원인 후보 순위</b>
              {s.low_evidence ? (
                <span className="ch amber">근거 적음</span>
              ) : null}
            </div>
            {rows.map((r, i) => (
              <div className={`rk ${i === 0 ? "on" : ""}`} key={r.code} style={{ gridTemplateColumns: "28px 150px 1fr 196px" }}>
                <span className="rn">{i + 1}</span>
                <b>{r.name}</b>
                <span className="rb">
                  <i style={{ width: `${r.bar}%` }} />
                </span>
                {r.k != null && n != null ? <span className="rs">{shortCount(r.k, n)}</span> : null}
              </div>
            ))}
            {rows.length && n != null && isGroup(s) && (s.count_prior ?? 0) > 0 ? (
              <div className="rs" data-part="rank-wide" style={{ marginTop: 10 }}>
                이 설비 기록이 3건 미만이라 같은 증상 전체 기록으로 셈
              </div>
            ) : null}
            {rows.length && n != null && n < FEW_RECORDS ? (
              <div className="rs" data-part="rank-few" style={{ marginTop: 10 }}>
                기록 {n}건 · 확정 아님
              </div>
            ) : null}
          </div>
          {top && s.top_cards?.length ? (
            <div className="cd mt" data-part="top-ref" style={{ flex: "none" }}>
              <div className="cd-h">
                <Icon name="history" />
                <b>
                  근거 · {top.name}{" "}
                  {top.k != null && s.top_cards.length < top.k ? `최근 ${s.top_cards.length}건` : `${s.top_cards.length}건`}
                </b>
              </div>
              <div className="hcs">
                {s.top_cards.map((c) => (
                  <RecordCard key={c.record_id} card={c} onOpen={onRecord} />
                ))}
              </div>
            </div>
          ) : null}
        </div>
        <NextCard s={s} onStart={onStart} past={past} onSwitch={onSwitch} />
      </div>
    </div>
  );
}
