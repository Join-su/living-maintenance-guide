// 진단 세션 화면 묶음: 잠금 전 T03 → T04 → T05(기기에 둔 떠난 화면), 잠금 뒤는 서버 단계(step)대로
// T05 · T06 · T07 → T08 조치 · 재가동 · T10 호출 · T11 인계 · 감시 마감 · T09 보고서(S-03).
// 입력은 업무 key(세션 + seq)로 보내고, 보내지 않은 입력은 기기에 둔다(홈으로 갔다 와도 그대로).

import { useEffect, useRef, useState } from "react";
import { errorDetail, StepBar, useFreshScreenGuard, type Api, type Instruction, type SessionView, type SessionWithPreview } from "@c1a15/ui";
import { device, emptyDraft, type Draft } from "../device";
import { PackNotice, trackPack, type PackState } from "../PackNotice";
import { usePoll } from "../hooks";
import { AlarmScreen } from "./Alarm";
import { DiagnosisScreen } from "./Diagnosis";
import { ActionScreen, CallScreen, HandoverScreen, MonitorScreen, ReportScreen } from "./Flow";
import { InstructionScreen, NextStageScreen, PendingModal } from "./Instruction";
import { LockScreen, PrecheckScreen } from "./Lock";
import { PastScreen, type PastData } from "./Past";

type Stage = "alarm" | "diagnosis" | "lock";
type Body = { seq: number; type: string; [k: string]: unknown };

const INSTRUCT = new Set(["instruct", "instruct_after_checklist"]);
const PENDING = new Set(["confirm_question", "out_of_scope"]);

const ACTION = new Set(["action", "action_after_checklist", "restart_check", "restart_value"]);
const CALL = new Set(["call", "call_direct"]);

/** 진행 표시: 조치 · 재가동 · 감시 마감 = 4, 보고서 = 5, 그 밖(점검 · 호출 · 인계) = 3, 조치 뒤 호출 = 4. */
export function stepNumber(s: SessionView, stage: Stage, reportOpen = true): number {
  if (!s.locked) return stage === "alarm" ? 1 : stage === "diagnosis" ? 2 : 3;
  const kind = s.step.kind ?? "";
  if ((kind === "report" && reportOpen) || kind === "closed") return 5;
  if (ACTION.has(kind) || kind === "monitor_close" || (CALL.has(kind) && s.action?.done_at)) return 4;
  return 3;
}

export interface SessionProps {
  api: Api;
  sid: number;
  initial: SessionWithPreview | null;
  onSession: (s: SessionWithPreview) => void;
  onExpired: () => void;
  onRecord: (id: string) => void;
  onManual: (section: string) => void;
  onHome: () => void;
  onFinal?: (reportId: number) => void;
}

export function Session(p: SessionProps) {
  const { api, sid } = p;
  const [s, setS] = useState<SessionWithPreview | null>(p.initial);
  const [stage, setStageState] = useState<Stage>(() => device.stage(sid) ?? "alarm");
  const [draft, setDraftState] = useState<Draft>(() => device.draft(sid));
  const [busy, setBusy] = useState(false);
  const sending = useRef(false); // EV-007: 같은 순간 두 번 누름도 한 번만 보냄(상태 busy는 다음 그리기에야 보임)
  const [pack, setPack] = useState<PackState>(null); // CH-17: 판정 지연 · 실패(배포 서버 길)
  const packRetry = useRef<(() => void) | null>(null); // 다시 보내기(같은 seq) — ref라 두 번 눌러도 한 번만
  const [, bump] = useState(0); // 기기 표시(seen)를 바꾼 뒤 다시 그리기
  const [repeatDismissed, setRepeatDismissed] = useState(false);
  const [lastIns, setLastIns] = useState<Instruction | null>(() => device.lastInstruction<Instruction>(sid));
  const [past, setPast] = useState<{ no: number; idx: number } | null>(null); // T15 지난 화면(S-05 P-15)
  const [hist, setHist] = useState<PastData | null>(null);

  const setStage = (st: Stage) => {
    setStageState(st);
    device.setStage(sid, st);
  };
  const setDraft = (d: Draft) => {
    setDraftState(d);
    device.setDraft(sid, d);
  };
  const accept = (v: SessionView | SessionWithPreview) => {
    setS((prev) => {
      const next = { ...(prev ?? {}), ...v } as SessionWithPreview;
      p.onSession(next);
      return next;
    });
  };

  usePoll(
    async () => {
      const { data, response } = await api.GET("/api/tech/sessions/{sid}", { params: { path: { sid } } });
      if (response.status === 401) p.onExpired();
      if (data) accept(data);
    },
    3000,
    [sid],
  );

  const ins = s?.instruction ?? null;
  useEffect(() => {
    if (!ins) return;
    // 새 지시가 열렸을 때만 보내지 않은 입력을 지운다. 다시 들어온 화면(홈 · 종 목록 → 이어서 하기)은 새 지시가 아니다 —
    // 조치 · 재가동 단계에도 직전 지시가 남아 있어 그 단계의 체크(draft.section = '단계:절')를 지우던 결함(S-06, AC-44-C3)
    const fresh = !lastIns || lastIns.section !== ins.section || lastIns.check_no !== ins.check_no;
    setLastIns(ins);
    device.setLastInstruction(sid, ins);
    if (fresh && draft.section !== ins.section) setDraft({ ...emptyDraft(), precheck: draft.precheck, section: ins.section });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ins?.section, ins?.check_no]);

  const send = async (body: Omit<Body, "seq">): Promise<boolean> => {
    if (!s || busy || sending.current) return false;
    sending.current = true;
    setBusy(true);
    const { data, error, response } = await trackPack(
      () =>
        api.POST("/api/tech/sessions/{sid}/events", {
          params: { path: { sid } },
          body: { seq: s.last_seq + 1, ...body } as never,
        }),
      setPack,
    ).finally(() => {
      sending.current = false; // 연결이 끊겨 예외로 끝나도 풀림(EV-007 review)
    });
    setBusy(false);
    packRetry.current = null;
    if (response.status === 503 && errorDetail(error) === "pack_unavailable") {
      // 판정 서버 연결 실패: '다시 보내기'를 누를 때까지 이 요청을 끝내지 않는다 — 같은 seq · 같은 본문으로 다시 보내고,
      // 그 결과로 원래 호출자의 뒤처리(입력 초안 비우기 등)를 이어 간다
      return new Promise<boolean>((resolve) => {
        packRetry.current = () => void send(body).then(resolve);
      });
    }
    if (response.status === 401) p.onExpired();
    if (data) {
      accept(data);
      return true;
    }
    if (errorDetail(error) === "stale_seq" || response.status === 409) {
      const r = await api.GET("/api/tech/sessions/{sid}", { params: { path: { sid } } });
      if (r.data) accept(r.data);
    }
    return false;
  };

  // EV-007: 알람 확인(서버 기록) 전에는 기기에 남은 단계와 상관없이 알람 화면부터
  const shown: Stage = s && !s.locked && !s.alarm_ack_at ? "alarm" : stage;
  useFreshScreenGuard(
    [shown, s?.locked, s?.step?.kind, s?.step?.section, s?.instruction?.check_no, past?.no, past?.idx].join("|"),
  );
  if (!s) return <StepBar current={1} />;

  const loadHistory = async () => {
    const { data } = await api.GET("/api/tech/sessions/{sid}/history", { params: { path: { sid } } });
    if (data) setHist(data);
    return data ?? null;
  };
  const openPast = async (n: number) => {
    const h = await loadHistory();
    const recs = h?.steps[n - 1]?.records ?? [];
    if (recs.length) setPast({ no: n, idx: recs.length - 1 });
  };

  const kind = s.step.kind ?? "";
  const answer = s.call?.answer?.answer;
  const approvedKey = `approved.${s.call?.section ?? ""}`;
  const approvedSeen = device.seen(sid, approvedKey);
  // 답 화면(T10 / ans_daytime · ans_direct, 숙련자 인계 답 — 결정 018 ③ ④)에서 '보고서 작성'을 눌러야 보고서로 간다
  const answerKey = `answer-report.${s.call?.section ?? s.call?.kind ?? ""}`;
  const answerGate = kind === "report" && !!answer && answer !== "approved" && !device.seen(sid, answerKey);
  const reportOpen = !answerGate;
  const checksKey = `${kind}:${s.step.section ?? ""}`;
  const checked = draft.section === checksKey ? draft.checked : [];
  const flowSend = (b: Record<string, unknown>) => send(b as Omit<Body, "seq">);
  const callScreen = (
    <CallScreen
      s={s}
      busy={busy}
      approvedSeen={approvedSeen}
      onApprovedSeen={() => {
        device.setSeen(sid, approvedKey);
        bump((n) => n + 1);
      }}
      onReport={() => {
        device.setSeen(sid, answerKey);
        bump((n) => n + 1);
      }}
      send={flowSend}
    />
  );

  let body;
  if (!s.locked) {
    if (shown === "alarm")
      body = (
        <AlarmScreen
          s={s}
          busy={busy}
          onNext={() => void send({ type: "alarm_ack" }).then((ok) => ok && setStage("diagnosis"))}
        />
      );
    else if (shown === "diagnosis") body = (
        <DiagnosisScreen
          s={s}
          onRecord={p.onRecord}
          onStart={() => setStage("lock")}
          onSwitch={(section) => {
            void send({ type: "switch_check", section }).then(async (ok) => {
              const { data } = ok ? await api.GET("/api/tech/sessions/{sid}", { params: { path: { sid } } }) : { data: null };
              if (data) accept(data); // 고른 점검을 카드에 바로(이벤트 응답엔 미리보기 칸 없음)
            });
          }}
        />
      );
    else body = <LockScreen s={s} busy={busy} onLock={() => void send({ type: "lock" })} />;
  } else if (s.step.kind === "precheck") {
    body = (
      <PrecheckScreen
        s={s}
        busy={busy}
        onDone={(callUp) =>
          void send(
            callUp
              ? { type: "precheck_done", values: "이상 있음 · 상급자 호출", mode: "call_first" }
              : { type: "precheck_done", values: "확인함" },
          )
        }
      />
    );
  } else if (INSTRUCT.has(kind) && answer === "approved" && s.call && !approvedSeen) {
    body = callScreen; // T10 / ans_approved: 승인 배너와 그 C 점검 지시
  } else if (ACTION.has(kind)) {
    body = (
      <ActionScreen
        s={s}
        busy={busy}
        checked={checked}
        onCheck={(items) => setDraft({ ...draft, section: checksKey, checked: items })}
        send={flowSend}
      />
    );
  } else if (kind === "monitor_close" && s.monitor) {
    body = <MonitorScreen s={s} busy={busy} send={flowSend} />;
  } else if (CALL.has(kind) && s.call) {
    body = callScreen;
  } else if (kind === "handover" && s.call) {
    body = <HandoverScreen s={s} busy={busy} send={flowSend} />;
  } else if ((kind === "report" || kind === "closed") && s.report) {
    const openReport = () => {
      device.setSeen(sid, answerKey);
      bump((n) => n + 1);
    };
    body = reportOpen ? (
      <ReportScreen report={s.report} busy={busy} send={flowSend} onHome={p.onHome} onFinal={p.onFinal} />
    ) : s.call?.kind === "handover" ? (
      <HandoverScreen s={s} busy={busy} send={flowSend} onReport={openReport} />
    ) : (
      callScreen
    );
  } else if (INSTRUCT.has(kind) || PENDING.has(kind)) {
    const shown = ins ?? lastIns;
    const pending = PENDING.has(s.step.kind ?? "");
    body = (
      <>
        {shown ? (
          <InstructionScreen
            s={s}
            ins={shown}
            draft={draft}
            busy={busy || pending}
            repeatDismissed={repeatDismissed}
            onDraft={setDraft}
            onCheck={(item) => {
              const checked = draft.checked.includes(item) ? draft.checked.filter((x) => x !== item) : [...draft.checked, item];
              setDraft({ ...draft, checked });
              if (shown.checklist.every((c) => checked.includes(c)))
                void send({ type: "b_checklist_done", section: shown.section, checked: shown.checklist });
            }}
            onSend={() => {
              const body: Omit<Body, "seq"> = { type: "result", section: shown.section };
              if (draft.value) body.value = draft.value;
              if (draft.memo.trim()) body.memo = draft.memo.trim();
              void send(body).then((ok) => ok && setDraft({ ...emptyDraft(), section: shown.section }));
            }}
            onManual={() => p.onManual(shown.section)}
            onRecord={p.onRecord}
            onContinueOriginal={() => setRepeatDismissed(true)}
            onHandover={() => void send({ type: "handover_request" })}
            onSwitch={(section) => {
              void send({ type: "switch_check", section }).then((ok) => ok && setDraft({ ...emptyDraft(), section }));
            }}
            onException={(mode) => void send({ type: "exception_start", mode })} // 호출 후 조치의 호출은 서버가 바로 보냄
          />
        ) : null}
        {pending ? (
          <PendingModal
            s={s}
            ins={shown}
            busy={busy}
            onConfirm={(yes) => {
              const last = [...s.results].reverse()[0];
              void send({ type: "confirm_answer", yes }).then((ok) => {
                if (ok && !yes) setDraft({ ...emptyDraft(), section: shown?.section ?? null, memo: last?.memo ?? "" });
              });
            }}
            onChoice={(choice) => void send({ type: "out_of_scope_choice", choice })}
          />
        ) : null}
      </>
    );
  } else {
    body = <NextStageScreen s={s} />;
  }
  const current = stepNumber(s, shown, reportOpen);
  if (past && hist) {
    const unsentIns = ins && draft.section === ins.section && (draft.value || draft.memo.trim()) ? ins : null;
    body = (
      <PastScreen
        api={api}
        sid={sid}
        s={s}
        data={hist}
        no={past.no}
        idx={past.idx}
        onPick={(no, i) => setPast({ no, idx: i })} // 답장 001-zzx 마: 목록에서 점검 · 조치 기록을 함께 고름
        onCurrent={() => setPast(null)} // AC-41-C3: 현재 단계와 넣던 입력은 그대로(기기)
        unsent={
          unsentIns
            ? {
                section: unsentIns.section,
                title: unsentIns.title,
                desc: `${draft.value ?? "결과 선택 전"}${draft.memo.trim() ? ` · 메모 '${draft.memo.trim()}'` : ""}`,
              }
            : null
        }
        onSaved={(v, confirmed) => {
          accept(v);
          if (confirmed) setDraft({ ...emptyDraft(), precheck: draft.precheck }); // AC-46-C3: 바뀐 지시의 보내지 않은 입력은 지운다
          void loadHistory();
        }}
        onRecord={p.onRecord}
        onManual={p.onManual}
      />
    );
  }
  return (
    <>
      <StepBar
        current={current}
        view={past?.no ?? null} // 지시 d530-p33(가이드 23쪽): 보는 단계가 지금 꼴(파랑) · 그 뒤는 회색(누름은 그대로)
        onPick={(n) => void openPast(n)} // 잠금 전(진단 · 잠금 · 기록 창)에도 완료 단계는 그때 화면(AC-41-C1 · Prototype T04 · T05 · T13)
        currentPickable={s.locked && current >= 3} // 생성자 해석 ①: 지금 단계도 보낸 기록이 있으면 기록 보기
        done={!past && current === 5 && !!s.report && s.report.status !== "draft"} // 답장 001-zzx 라(가이드 19쪽): 확정한 보고서는 '보고서'도 끝남
      />
      <PackNotice
        state={pack}
        onRetry={() => {
          // 다시 보내는 동안 안내 줄 · 버튼을 먼저 치워 두 번 눌러 같은 seq 요청이 겹치지 않게 한다
          const retry = packRetry.current;
          packRetry.current = null;
          setPack(null);
          retry?.();
        }}
      />
      {body}
    </>
  );
}
