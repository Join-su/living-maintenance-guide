// T06 점검 지시 · 결과 입력(지시 하나, 건너뛰기 없음 — AC-35), T07 확인 질문 · 맞지 않는 소견, T14 매뉴얼 원문 창.
// 값은 세션 응답의 instruction(P-06 · P-19 ⑥). 확인 질문 동안은 직전에 받은 지시를 뒤 화면에 흐리게 보인다.
// D-528(가이드 정비자 6 · 7): 메모 이름표 괄호 · 버튼 아래 안내 문장 · 입력 내용 확인 창 둘째 줄 뺌.

import { useState } from "react";
import { GradeBadge, Icon, type Instruction, type SessionView } from "@c1a15/ui";
import type { Draft } from "../device";
import { ExWalk, isException, LockOk, Proc, SecChip, secName } from "./Lock";

const RESULT: { v: "정상" | "이상" | "확인 불가"; icon: string; cls: string }[] = [
  { v: "정상", icon: "circle-check", cls: "ok" },
  { v: "이상", icon: "circle-alert", cls: "bad" },
  { v: "확인 불가", icon: "circle-help", cls: "na" },
];

export interface InstructionProps {
  s: SessionView;
  ins: Instruction;
  draft: Draft;
  busy: boolean;
  repeatDismissed: boolean;
  onDraft: (d: Draft) => void;
  onCheck: (item: string) => void;
  onSend: () => void;
  onManual: () => void;
  onRecord: (id: string) => void;
  onContinueOriginal: () => void;
  onHandover: () => void;
  onSwitch?: (section: string) => void; // 약속 v9 [점검 바꾸기] 후보 고름(switch_check)
  onException?: (mode: "call_first" | "act_now") => void; // 약속 v9 [예외] 호출 후 조치 / 바로 조치(exception_start)
}

const SWITCHABLE = new Set(["instruct", "instruct_after_checklist"]); // 점검 안내 중만(서버 not_switchable과 같음)
const EX_MODES = [
  { mode: "call_first", label: "호출 후 조치" },
  { mode: "act_now", label: "바로 조치" },
] as const;

/** [점검 바꾸기] 후보 줄 — 매뉴얼 · 예외 같은 모양(D-580 · 예외는 칩만 다름). */
export function SwitchRows({
  cands,
  busy,
  topic,
  onPick,
}: {
  cands: NonNullable<SessionView["switch_candidates"]>;
  busy: boolean;
  topic?: string | null;
  onPick: (section: string) => void;
}) {
  return (
    <>
      {cands.map((c) => (
        <span
          key={c.section}
          className={`swc${busy ? " dis" : ""}`}
          role="button"
          tabIndex={0}
          data-section={c.section}
          onClick={() => !busy && onPick(c.section)}
        >
          <span className="swt">
            <b>{c.title}</b>
            {c.summary ? <span className="sws">{c.summary}</span> : null}
          </span>
          <span className="tags">
            {c.grade ? <GradeBadge grade={c.grade} /> : null}
            <SecChip section={c.section} topic={topic} />
          </span>
        </span>
      ))}
    </>
  );
}

export function InstructionScreen(p: InstructionProps) {
  const { ins, draft } = p;
  const [switching, setSwitching] = useState<"list" | "exception" | null>(null); // [점검 바꾸기] 창 · [예외] 고름
  const cands = p.s.switch_candidates ?? [];
  const canSwitch = SWITCHABLE.has(p.s.step.kind ?? "") && !!p.onSwitch;
  const needsChecklist = ins.checklist.length > 0 && !ins.checklist_done;
  const ready = !needsChecklist && !p.busy && (draft.value !== null || draft.memo.trim() !== "");
  const unable = ins.unable.filter((u) => u.section !== ins.section); // 앞에서 확인 불가였던 다른 점검
  return (
    <div className="ap-body">
      <div className="g64">
        <div className="cd">
          <LockOk at={p.s.lock_at} />
          {p.s.exception_notes?.length ? ( // D-543 참고 알림: 같은 설비 · 같은 증상군의 승인된 예외 상황 기록(순서는 그대로)
            <div className="banner info xref">
              <Icon name="info" />
              <div>
                <b>
                  {p.s.exception_notes[0].topic // 고침A 12쪽: '이전 예외 기록 · 예외상황-{주제}'
                    ? `이전 예외 기록 · 예외상황-${p.s.exception_notes[0].topic}`
                    : `이전 예외 기록 · ${p.s.alarm.equipment_id} · ${p.s.alarm.group_name}`}
                </b>
                {p.s.exception_notes[0].text}
              </div>
            </div>
          ) : null}
          <div className="ins-h">
            <span className="sm">
              점검 {ins.check_no} / 최대 {ins.check_max}
            </span>
            <h3>{ins.title}</h3>
            <div className="tags">
              <GradeBadge grade={ins.grade} />
              <SecChip section={ins.section} topic={p.s.exception_topic} />
              {ins.minutes != null ? <span className="ch gray">약 {ins.minutes}분</span> : null}
              {ins.fallback ? <span className="ch fb">{ins.fallback_label}</span> : null}
            </div>
          </div>
          {p.s.step.step ? <ExWalk walk={p.s.exception_walk} /> : null}
          {isException(ins.section) && p.s.exception_why ? (
            <div className="xwhy" data-part="ex-why">
              {p.s.exception_why}
            </div>
          ) : null}
          {ins.fallback && !(p.s.recommend_handover && !p.repeatDismissed) ? (
            <div className="banner info">
              <Icon name="info" />
              <div>
                <b>{ins.fallback_label}</b>안내 문장이 근거 검사를 통과하지 못해 매뉴얼 원문을 그대로 보여 줍니다.
              </div>
            </div>
          ) : null}
          {p.s.recommend_handover && !p.repeatDismissed ? (
            <>
              <div className="banner bad">
                <Icon name="triangle-alert" />
                <div>
                  <b>안내 문장 생성이 3회 연속 실패했습니다</b>매뉴얼 원문으로 계속할 수 있습니다. 숙련자 인계를 권합니다.
                </div>
              </div>
              <div className="choice2">
                {/* AC-31-C2 · 사용자 결정 016 ④: 숙련자 인계 요약(T11)으로 */}
                <span className={`btn warnb${p.busy ? " dis" : ""}`} role="button" tabIndex={0} onClick={() => !p.busy && p.onHandover()}>
                  숙련자 인계
                </span>
                <span className="btn ghost" role="button" tabIndex={0} onClick={p.onContinueOriginal}>
                  원문으로 계속
                </span>
              </div>
            </>
          ) : null}
          {unable.map((u) => (
            <div className="banner warn" key={u.section}>
              <Icon name="circle-help" />
              <div>
                <b>
                  {u.section} {u.title} · 확인 불가 {u.count}회
                </b>
                순위는 그대로입니다. 다른 점검을 먼저 합니다. 같은 점검에서 다시 확인 불가가 나오면 그 점검은 제외됩니다.
              </div>
            </div>
          ))}
          {ins.checklist.length > 0 ? (
            <div className="act pre">
              <div className="act-h">
                <b>시작 전 체크리스트</b>
                {ins.checklist_common ? <span className="ch gray">공통 시작 전 확인</span> : null}
              </div>
              <span className="sm">아래 항목을 모두 확인해야 결과 입력이 열립니다</span>
              {ins.checklist.map((item) => {
                const on = ins.checklist_done || draft.checked.includes(item);
                return (
                  <div
                    className={`ck ${on ? "on" : ""}`}
                    key={item}
                    role="checkbox"
                    aria-checked={on}
                    tabIndex={0}
                    onClick={() => !ins.checklist_done && p.onCheck(item)}
                  >
                    <span className="cb">{on ? <Icon name="check" /> : null}</span>
                    {item}
                  </div>
                );
              })}
            </div>
          ) : null}
          {ins.fallback ? <div className="orig">{ins.original}</div> : <Proc steps={ins.steps} />}
          {ins.criterion && !ins.fallback ? (
            <div className="hint">
              <Icon name="info" />
              판정 기준 · {ins.criterion}
            </div>
          ) : null}
          <div className="refs">
            {isException(ins.section) ? null : ( // 약속 v8 예외 절은 매뉴얼 밖 — 원문 없음
              <span className="lk" role="button" tabIndex={0} onClick={p.onManual}>
                <Icon name="book-open" />
                매뉴얼 원문 보기
              </span>
            )}
            {ins.evidence_record_ids.length ? (
              <span className="lk">
                <Icon name="history" />
                근거{" "}
                {ins.evidence_record_ids.map((id, i) => (
                  <span key={id}>
                    {i ? " · " : ""}
                    <span role="button" tabIndex={0} onClick={() => p.onRecord(id)}>
                      {id}
                    </span>
                  </span>
                ))}
              </span>
            ) : null}
            {canSwitch ? ( // D-556: 점검 안내 중 아무 때나 — 지금 추천 후보 · [예외]
              <span className="lk sw" role="button" tabIndex={0} onClick={() => !p.busy && setSwitching("list")}>
                <Icon name="arrow-left-right" />
                점검 바꾸기
              </span>
            ) : null}
          </div>
        </div>
        <div className="cd">
          <div className="res">
            <b className="rt">점검 결과 입력</b>
            <div className="rbtns">
              {RESULT.map((r) => (
                <span
                  key={r.v}
                  className={`rbtn ${draft.value === r.v ? `sel ${r.cls}` : ""}${needsChecklist ? " dis" : ""}`}
                  role="button"
                  tabIndex={0}
                  aria-pressed={draft.value === r.v}
                  onClick={() => !needsChecklist && p.onDraft({ ...draft, value: draft.value === r.v ? null : r.v })}
                >
                  <Icon name={r.icon} />
                  {r.v}
                </span>
              ))}
            </div>
            <span className="sm">메모</span>
            <textarea
              className="memo"
              aria-label="메모"
              value={draft.memo}
              disabled={needsChecklist}
              onChange={(e) => p.onDraft({ ...draft, memo: e.target.value })}
            />
            <span className={`btn lg full${ready ? "" : " dis"}`} role="button" tabIndex={0} onClick={() => ready && p.onSend()}>
              결과 보내기 <Icon name="send" />
            </span>
          </div>
        </div>
      </div>
      {switching ? (
        <>
          <div className="dim" />
          <div className="modal swm" role="dialog" aria-label="점검 바꾸기">
            <div className="mh">
              {switching === "list" ? <Icon name="arrow-left-right" /> : null}
              <b>{switching === "list" ? "점검 바꾸기" : "예외"}</b>
            </div>
            {switching === "list" ? (
              <div className="swl">
                <SwitchRows
                  cands={cands}
                  busy={p.busy}
                  topic={p.s.exception_topic}
                  onPick={(section) => {
                    if (!p.onSwitch) return;
                    setSwitching(null);
                    p.onSwitch(section);
                  }}
                />
                {p.onException ? (
                  <span className={`swc swx${p.busy ? " dis" : ""}`} role="button" tabIndex={0} onClick={() => !p.busy && setSwitching("exception")}>
                    <b>예외</b>
                    <Icon name="chevron-right" />
                  </span>
                ) : null}
              </div>
            ) : (
              <div className="swl">
                {EX_MODES.map((x) => (
                  <span
                    key={x.mode}
                    className={`swc swe${p.busy ? " dis" : ""}`}
                    role="button"
                    tabIndex={0}
                    data-mode={x.mode}
                    onClick={() => {
                      if (p.busy || !p.onException) return;
                      setSwitching(null);
                      p.onException(x.mode);
                    }}
                  >
                    <b>{x.label}</b>
                    {x.mode === "call_first" ? <GradeBadge grade="C" /> : null}
                  </span>
                ))}
              </div>
            )}
            <div className="mb">
              <span className="btn ghost" role="button" tabIndex={0} onClick={() => setSwitching(null)}>
                취소
              </span>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** T07: 메모 해석 확인 질문(R7, AC-05) · 후보 원인과 맞지 않는 소견(AC-20). 고르기 전에는 반영 · 인계하지 않는다. */
export function PendingModal({
  s,
  ins,
  busy,
  onConfirm,
  onChoice,
}: {
  s: SessionView;
  ins: Instruction | null;
  busy: boolean;
  onConfirm: (yes: boolean) => void;
  onChoice: (c: "handover" | "continue") => void;
}) {
  const last = [...s.results].reverse()[0];
  const me = (
    <div className="bub me">{last?.memo}</div> // D-530 답장 001-zzt 2: '내 메모 · …' 이름표 뺌(가이드 13쪽)
  );
  const dis = busy ? " dis" : "";
  if (s.step.kind === "out_of_scope") {
    return (
      <>
        <div className="dim" />
        <div className="modal" role="dialog" aria-label="후보 원인과 맞지 않는 소견">
          <div className="mh">
            <Icon name="triangle-alert" />
            <b>후보 원인과 맞지 않는 소견입니다</b>
          </div>
          {me}
          <div className="bub ai">
            <div>
              "{s.step.quote}"은 남은 후보 원인과 연결되지 않습니다. <b>숙련자 인계를 권합니다.</b>
            </div>
            <span className="sm">순위는 바뀌지 않았습니다. 정비자가 고르기 전에는 인계되지 않습니다.</span>
          </div>
          <div className="mb">
            <span className={`btn ghost${dis}`} role="button" tabIndex={0} onClick={() => !busy && onChoice("continue")}>
              계속 진행
            </span>
            <span className={`btn warnb${dis}`} role="button" tabIndex={0} onClick={() => !busy && onChoice("handover")}>
              숙련자 인계
            </span>
          </div>
        </div>
      </>
    );
  }
  return (
    <>
      <div className="dim" />
      <div className="modal" role="dialog" aria-label="입력 내용 확인">
        <div className="mh">
          <Icon name="sparkles" />
          <b>입력 내용 확인</b>
        </div>
        {me}
        <div className="bub ai">
          <div>
            {ins?.title ?? "이 점검"} 결과를 <b>"{s.step.interpretation ?? "불명"}"</b>으로 이해했습니다. 맞습니까?
          </div>
        </div>
        <div className="mb">
          <span className={`btn ghost${dis}`} role="button" tabIndex={0} onClick={() => !busy && onConfirm(false)}>
            아니요, 다시 입력
          </span>
          <span className={`btn${dis}`} role="button" tabIndex={0} onClick={() => !busy && onConfirm(true)}>
            네, 맞습니다
          </span>
        </div>
      </div>
    </>
  );
}

export interface ManualView {
  section: string;
  title: string;
  text: string;
  grade: string | null;
  minutes: number | null;
}

/** 매뉴얼 원문의 Markdown 표시 기호만 걷어 낸다(내용은 서버 원문 그대로). */
export function plainManual(text: string): string {
  return text
    .split("\n")
    .map((l) => l.replace(/^#{1,6}\s+/, "").replace(/^>\s?/, "").replace(/\*\*/g, ""))
    .join("\n")
    .trim();
}

/** T14 매뉴얼 원문 창(AC-39): 지시된 절만. 닫으면 원래 화면 · 단계 그대로. */
export function ManualModal({ m, onClose }: { m: ManualView; onClose: () => void }) {
  return (
    <>
      <div className="dim" />
      <div className="modal wide" role="dialog" aria-label={`매뉴얼 원문 ${m.section}`}>
        <div className="mh">
          <Icon name="book-open" />
          <b>
            매뉴얼 원문 · {m.section} {m.title}
          </b>
          <GradeBadge grade={m.grade} />
          <span className="btn ghost sm2 mclose" role="button" tabIndex={0} onClick={onClose}>
            닫기
          </span>
        </div>
        <div className="orig">{plainManual(m.text)}</div>
      </div>
    </>
  );
}

/** S-03 이후 화면(호출 요약 · 조치 · 인계 · 재가동 확인)으로 가는 단계. 화면은 그 slice에서 연결한다. */
export function NextStageScreen({ s }: { s: SessionView }) {
  const st = s.step;
  const label: Record<string, string> = {
    call: "파트장 호출 요약",
    action: "조치",
    action_after_checklist: "조치",
    handover: "숙련자 인계 요약",
    restart_check: "재가동 확인",
  };
  return (
    <div className="ap-body">
      <div className="g64">
        <div className="cd">
          <LockOk at={s.lock_at} />
          <div className="banner info">
            <Icon name="info" />
            <div>
              <b>다음: {label[st.kind ?? ""] ?? st.kind}</b>
              {st.section ? secName(st.section) : ""}
              {st.grade ? ` · ${st.grade}등급` : ""}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
