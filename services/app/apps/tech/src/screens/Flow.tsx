// T08 조치 · 재가동 · 재가동 후 값, T10 파트장 호출(요약 · 대기 · 보냄 · 답 3종), T11 숙련자 인계 · 감시 마감, T09 보고서.
// 값은 세션 응답의 path · action · restart · monitor · call · report(P-09). 화면 문구 외의 값은 만들지 않는다.

import { useState } from "react";
import { dateTime, exActionLine, exCheckLine, GradeBadge, hhmm, Icon, type Instruction, type Schemas, type SessionView } from "@c1a15/ui";
import { SwitchRows } from "./Instruction";
import { EX_NEW, ExWalk, isException, Proc, SecChip } from "./Lock";

type Call = Schemas["CallBlock"];
type Report = Schemas["ReportBlock"];

export function PathCard({ path }: { path: SessionView["path"] }) {
  return (
    <div className="cd ">
      <div className="cd-h">
        <Icon name="history" />
        <b>지금까지 점검 · 조치</b>
      </div>
      <div className="tlw">
        {(path ?? []).map((p, i) => (
          <div className={`tli ${p.status}`} key={i}>
            <span className="tt">{p.status === "now" ? "지금" : p.at ? hhmm(p.at) : ""}</span>
            <span className="td" />
            <div>
              <b>{p.title}</b>
              <span>{p.detail}</span>
              {p.status === "void" ? <span className="ch gray vd">정정으로 무효</span> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// 답장 001-zzx 가(가이드 14쪽): '원인 확정' 카드 — 머리 오른쪽 원인 칩 · '근거 사례' 한 줄.
function CauseCard({ a }: { a: NonNullable<SessionView["action"]> }) {
  if (a.section === EX_NEW)
    // 정답지 17쪽: 예외 진행 = 원인은 보고서 예외 칸에서
    return (
      <div className="cd">
        <div className="cd-h">
          <Icon name="circle-alert" />
          <b>원인</b>
          <span className="ch red">예외</span>
        </div>
        <div className="kvr">
          <span>원인</span>
          <b>보고서 예외 칸에서</b>
        </div>
      </div>
    );
  return (
    <div className="cd">
      <div className="cd-h">
        <Icon name="circle-alert" />
        <b>원인 확정</b>
        <span className="ch red">{a.cause_name}</span>
      </div>
      {a.evidence_record_ids.length ? (
        <div className="kvr">
          <span>근거 사례</span>
          <b>{a.evidence_record_ids.join(" · ")}</b>
        </div>
      ) : null}
    </div>
  );
}

function Checks({ items, on, onToggle }: { items: string[]; on: (x: string) => boolean; onToggle: (x: string) => void }) {
  return (
    <>
      {items.map((item) => (
        <div
          className={`ck ${on(item) ? "on" : ""}`}
          key={item}
          role="checkbox"
          aria-checked={on(item)}
          tabIndex={0}
          onClick={() => onToggle(item)}
        >
          <span className="cb">{on(item) ? <Icon name="check" /> : null}</span>
          {item}
        </div>
      ))}
    </>
  );
}

function Btn({
  label,
  icon,
  lead,
  cls = "lg full",
  off,
  onClick,
}: {
  label: string;
  icon?: string;
  lead?: string; // 이름 앞 아이콘(가이드 24 · 25 · 28쪽 호출 · 인계)
  cls?: string;
  off?: boolean;
  onClick: () => void;
}) {
  return (
    <span className={`btn ${cls}${off ? " dis" : ""}`} role="button" tabIndex={0} aria-disabled={off} onClick={() => !off && onClick()}>
      {lead ? (
        <>
          <Icon name={lead} />{" "}
        </>
      ) : null}
      {label}
      {icon ? (
        <>
          {" "}
          <Icon name={icon} />
        </>
      ) : null}
    </span>
  );
}

// 예외 기록: 자유 글(자리글 = 안내 문구) → 「AI 나눔」(약속 v10: 예외 하나 = 주제 + 점검 목록 + 조치 목록) → 예외 통째로
// 비슷한 것(상위 3 · 예외 | 매뉴얼) 중 같음 하나 / 아님 → exception_review 한 번(같음 = missed 놓침 · 아님 = new_exception 새 예외).
// 정답지 18 · 19쪽: 예외 진행 세션은 보고서 전 팝업에서 적고 「보고서에 적용」 → 보고서 초안 안 '예외 상황' 구역 · [예외 상황 수정].
export const EX_HINT = "점검한 것과 조치한 것을 모두 적어 주세요";
type ExOne = Schemas["ExceptionOne"];
type ExMatch = Schemas["ExceptionSimilar"];
const matchKey = (m: { kind: string; no: string }) => `${m.kind}|${m.no}`;
const exName = (topic?: string | null) => `예외상황-${topic ?? ""}`; // 고침A 11쪽 예외 이름
const matchLabel = (m: ExMatch) => (m.kind === "예외" ? exName(m.name) : `${m.no} ${m.name}`);
// D-642 C: 점검 · 조치 한 줄 = 지시 문장(Pack 문장 · 없으면 무엇 · 어떻게 · 판정 기준) — 보고서 · 파트장 승인 화면과 같은 글
const lineCheck = exCheckLine;
const lineAction = exActionLine;
/** 보고서 구역 한 줄(정답지 21쪽): '1 … · 2 …'. */
const numbered = (xs: string[]) => xs.map((x, i) => `${i + 1} ${x}`).join(" · ");

/** 적용 · 확정한 예외의 칩: 같음 · {그 비슷한 것 이름} / 새 예외 · 예외상황-{주제}(정답지 19 · 20쪽). */
function exChip(ex: ExOne): { cls: string; text: string } {
  if (ex.kind === "missed" && ex.match) {
    const m = (ex.matches ?? []).find((x) => matchKey(x) === matchKey(ex.match!));
    const head = ex.edit ? "같음 + 고침" : "같음"; // 정답지 30쪽 2단계
    return { cls: ex.edit ? "amber" : "gray", text: `${head} · ${m ? matchLabel(m) : `${ex.match.kind} ${ex.match.no}`}` };
  }
  return { cls: "blue", text: `새 예외 · ${exName(ex.topic)}` };
}

/** 팝업 안 나눈 예외 하나(정답지 18쪽): 이름 · 점검 n · 조치 n · 비슷한 것(예외 통째)마다 「같음」 + 「아님」 · 0이면 바로 새 예외. */
// 정답지 30쪽 · same-edit.md 1절: 같음 + 고침/보탬 = 대상 예외 지금 목록(비슷한 것 예외 줄) 위에 점검마다 방법 고침 + 조치 하나 보탬
type ExEdit = { key: string | null; hows: string[]; add: string };
type ExList = { checks: ExOne["checks"]; actions: ExOne["actions"] };
/** 고친 뒤 대상 예외 목록 전체(same-edit.md 1절 edit) — 바뀐 것 없으면 null. */
function editOf(m: ExMatch, e: ExEdit): ExList | null {
  if (!m.checks || !m.actions) return null;
  // 방법을 고친 점검은 옛 지시 문장을 뺌(문장과 방법이 어긋나지 않게 · 화면은 세 칸을 이어 씀)
  const checks = m.checks.map((c, i) => {
    const { instruction, ...rest } = c;
    const how = (e.hows[i] ?? c.how).trim();
    return instruction && how === c.how ? { ...rest, how, instruction } : { ...rest, how };
  });
  const actions = e.add.trim() ? [...m.actions, { what: e.add.trim(), parts: [], minutes: null }] : m.actions;
  const changed = checks.some((c, i) => c.how !== m.checks![i].how) || actions.length !== m.actions.length;
  return changed ? { checks, actions } : null;
}
/** 고침 전 · 뒤 한 줄(정답지 30쪽 파트장 '바뀌기 전 / 뒤 나란히'): '점검 1 무엇 · 방법 / 조치 1 무엇'. */
const listLine = (l: ExList) =>
  `점검 ${numbered(l.checks.map((c) => [c.what, c.how].filter(Boolean).join(" · ")))} / 조치 ${numbered(l.actions.map((a) => a.what))}`;

function ExPopOne({
  ex,
  pick,
  onPick,
  edit,
  onEdit,
}: {
  ex: ExOne;
  pick?: string | null;
  onPick: (v: string | null) => void;
  edit: ExEdit;
  onEdit: (e: ExEdit) => void;
}) {
  const matches = ex.matches ?? [];
  const editTarget = edit.key ? matches.find((m) => matchKey(m) === edit.key && m.checks) : undefined;
  return (
    <div className="exi" data-part="ex-one">
      <span className="exn0">{exName(ex.topic)}</span>
      {ex.checks.map((c, i) => (
        <div className="exl" key={`c${i}`} data-step={`c${i + 1}`}>
          <span className="ch gray">점검 {i + 1}</span>
          {lineCheck(c)}
        </div>
      ))}
      {ex.actions.map((a, i) => (
        <div className="exl" key={`a${i}`} data-step={`a${i + 1}`}>
          <span className="ch gray">조치 {i + 1}</span>
          {lineAction(a)}
        </div>
      ))}
      {matches.length ? (
        <div className="exs exm">
          <span className="exh">비슷한 것 · 예외 통째로 견줌</span>
          {matches.map((m) => (
            <div className="exr" key={matchKey(m)} data-match={matchKey(m)}>
              <span className={`ch ${m.kind === "예외" ? "amber" : "gray"}`}>{m.kind}</span>
              <span className="exn">{matchLabel(m)}</span>
              <Btn
                cls={pick === matchKey(m) ? "on" : "ghost"} // 정답지 30쪽: 고침/보탬이어도 같음은 채움
                label="같음"
                onClick={() => {
                  onEdit({ ...edit, key: null });
                  onPick(matchKey(m));
                }}
              />
              {m.kind === "예외" && m.checks && m.actions ? ( // 정답지 30쪽: 지금 목록이 온 기존 예외만 고침/보탬
                <Btn
                  cls={`ghost xed${edit.key === matchKey(m) && pick === matchKey(m) ? " xon" : ""}`} // 주황 테두리 그대로
                  label="고침/보탬"
                  onClick={() => {
                    if (edit.key !== matchKey(m)) onEdit({ key: matchKey(m), hows: m.checks!.map((c) => c.how), add: "" });
                    onPick(matchKey(m));
                  }}
                />
              ) : null}
            </div>
          ))}
          {editTarget && pick === edit.key ? (
            <div className="exe" data-part="ex-edit">
              <b>고칠 점검방법</b>
              {editTarget.checks!.map((c, i) => (
                <label className="exel" key={i}>
                  {editTarget.checks!.length > 1 ? <span>점검 {i + 1} · {c.what}</span> : null}
                  <input
                    aria-label={`고칠 점검방법 ${i + 1}`}
                    maxLength={500}
                    value={edit.hows[i] ?? ""}
                    onChange={(e) => onEdit({ ...edit, hows: edit.hows.map((h, j) => (j === i ? e.target.value : h)) })}
                  />
                </label>
              ))}
              <b>보탤 조치</b>
              <input aria-label="보탤 조치" maxLength={200} value={edit.add} onChange={(e) => onEdit({ ...edit, add: e.target.value })} />
            </div>
          ) : null}
          <span className="exb">
            <Btn
              cls={pick === null ? "on" : "ghost"}
              label="아님"
              onClick={() => {
                onEdit({ ...edit, key: null });
                onPick(null);
              }}
            />
          </span>
        </div>
      ) : (
        <div className="exs">
          <span className="ch blue">새 예외 · 비슷한 것 없음</span>
        </div>
      )}
    </div>
  );
}

/** 고침A 18쪽 예외 상황 팝업: 상황(자동) · 글 · 「AI 나눔」 · item마다 같음 / 아님 · 결과(자동) · 「보고서에 적용」. */
function ExPopup({
  report,
  busy,
  send,
  onClose,
  onApplied,
}: {
  report: Report;
  busy: boolean;
  send: (body: Record<string, unknown>) => Promise<boolean> | void;
  onClose?: () => void; // 적용한 것이 있을 때만(수정 창) — 처음엔 적용해야 닫힘
  onApplied: () => void;
}) {
  const applied = report.exception_applied;
  const [ex, setEx] = useState(applied?.text ?? report.exception_record?.text ?? report.exception_draft?.text ?? "");
  const split = report.exception_split;
  const fresh = split && !split.error && split.text === ex.trim() ? split.exception : null; // 글이 바뀌면 다시 나눔
  const unsplit = !!split?.error && split.text === ex.trim(); // 나누지 못함 → 글 그대로 새 예외(약속 v10 2절)
  const splitKey = JSON.stringify(fresh);
  const [picks, setPicks] = useState<{ key: string; v: string | null | undefined }>(() => {
    const r = applied?.exception_review;
    return { key: applied && applied.text === ex.trim() ? splitKey : "", v: r ? (r.match ? matchKey(r.match) : null) : undefined };
  });
  const chosen = fresh ? (fresh.matches?.length ? (picks.key === splitKey ? picks.v : undefined) : null) : undefined;
  const [edit, setEdit] = useState<ExEdit>(() => {
    const e = applied?.exception?.edit; // 앞 적용 = 고친 뒤 목록 → 칸 다시 채움(대상 = 그때 비슷한 것 예외 줄)
    const r = applied?.exception_review;
    const t = e && r?.match ? applied?.exception?.matches?.find((m) => matchKey(m) === matchKey(r.match!)) : undefined;
    return e && t
      ? { key: matchKey(t), hows: e.checks.map((c) => c.how), add: e.actions.length > (t.actions?.length ?? 0) ? e.actions[e.actions.length - 1].what : "" }
      : { key: null, hows: [], add: "" };
  });
  const editing = !!edit.key && chosen === edit.key; // 정답지 30쪽 같음 + 고침 — 바뀐 것이 있어야
  const target = editing ? fresh?.matches?.find((x) => matchKey(x) === edit.key) : undefined;
  const edited = target ? editOf(target, edit) : null;
  const ready = !!ex.trim() && (unsplit || (fresh !== null && chosen !== undefined && (!editing || !!edited)));
  const apply = () => {
    if (busy || !ready) return;
    const m = chosen ? fresh?.matches?.find((x) => matchKey(x) === chosen) : undefined;
    const same = m ? { decision: "same", match: { kind: m.kind, no: m.no }, ...(editing && edited ? { edit: edited } : {}) } : null;
    const review = unsplit ? null : same ?? { decision: "not", match: null };
    void Promise.resolve(send({ type: "exception_apply", exception_record: { text: ex.trim() }, exception_review: review })).then(
      (ok) => ok && onApplied(),
    );
  };
  return (
    <>
      <div className="dim" />
      <div className="modal exp" role="dialog" aria-label="예외 상황 기록">
        <div className="mh">
          <b>예외 상황 기록</b>
          <span className="ch gray">예외 진행 세션</span>
        </div>
        <div className="exp-b">
          <div className="khf">
            <span className="sm">
              상황 <span className="ch gray">자동</span>
            </span>
            <div className="khv" data-part="ex-situation">
              {report.exception_auto?.situation}
            </div>
          </div>
          <textarea
            className="memo"
            aria-label="예외 글"
            placeholder={EX_HINT}
            maxLength={2000}
            value={ex}
            onChange={(e) => setEx(e.target.value)}
          />
          <div className="exa">
            {unsplit ? <span className="ch fb">나누지 못함</span> : null}
            {fresh && split?.similar_error ? <span className="ch fb">비슷한 것 못 찾음</span> : null}
            <Btn
              cls="ghost"
              label="AI 나눔"
              icon="sparkles"
              off={busy || !ex.trim()}
              onClick={() => void send({ type: "exception_split", exception_text: ex.trim() })}
            />
          </div>
          {unsplit ? (
            <div className="exg" data-part="ex-items">
              <div className="exs">
                <span className="ch blue">새 예외</span>
              </div>
            </div>
          ) : null}
          {fresh ? (
            <div className="exg" data-part="ex-items">
              <ExPopOne ex={fresh} pick={chosen} onPick={(v) => setPicks({ key: splitKey, v })} edit={edit} onEdit={setEdit} />
            </div>
          ) : null}
          <div className="khf">
            <span className="sm">
              결과 <span className="ch gray">자동</span>
            </span>
            <div className="khv" data-part="ex-result">
              {report.exception_auto?.result}
            </div>
          </div>
        </div>
        <div className="mb">
          {onClose ? (
            <span className="btn ghost" role="button" tabIndex={0} onClick={onClose}>
              취소
            </span>
          ) : null}
          <span className={`btn${busy || !ready ? " dis" : ""}`} role="button" tabIndex={0} onClick={apply}>
            보고서에 적용
          </span>
        </div>
      </div>
    </>
  );
}

/** 정답지 19 · 20쪽 보고서 초안 안 '예외 상황' 구역(넘치면 이 구역만 스크롤): 상황 · 적은 글 · 예외 · 점검 · 조치 · 결과 · [예외 상황 수정]. */
function ExSection({
  situation,
  text,
  exception,
  splitError,
  result,
  legacy,
  onEdit,
  done,
}: {
  situation: string;
  text?: string | null;
  exception?: ExOne | null;
  splitError?: string | null;
  result?: string | null;
  legacy?: { situation: string; action?: string | null } | null;
  onEdit?: () => void;
  done?: boolean; // 확정 뒤 — 보기만(정답지 20쪽)
}) {
  const c = exception ? exChip(exception) : null;
  const t = exception?.edit && exception.match ? exception.matches?.find((m) => matchKey(m) === matchKey(exception.match!)) : undefined;
  const editBefore = t?.checks && t.actions ? { checks: t.checks, actions: t.actions } : null;
  return (
    <div className="rx" data-part="ex-section">
      <div className="rx-h">
        <b>예외 상황</b>
        {onEdit ? <Btn cls="ghost" label="예외 상황 수정" onClick={onEdit} /> : null}
        {done ? <span className="ch gray">확정 · 보기만</span> : null}
      </div>
      <div className="rxs">
        {legacy ? (
          <>
            <div className="rxr">
              <span>어떤 상황이었나</span>
              <span>{legacy.situation}</span>
            </div>
            <div className="rxr">
              <span>무엇을 했나</span>
              <span>{legacy.action}</span>
            </div>
          </>
        ) : (
          <>
            <div className="rxr">
              <span>상황</span>
              <span data-part="ex-situation">{situation}</span>
            </div>
            <div className="rxr">
              <span>적은 글</span>
              <span>{text}</span>
            </div>
            {exception && c ? (
              <>
                <div className="rxr" data-item="exception">
                  <span>예외</span>
                  <span>
                    {exName(exception.topic)} <span className={`ch ${c.cls}`}>{c.text}</span>
                  </span>
                </div>
                <div className="rxr" data-item="checks">
                  <span>점검</span>
                  <span>{numbered(exception.checks.map(lineCheck))}</span>
                </div>
                <div className="rxr" data-item="actions">
                  <span>조치</span>
                  <span>{numbered(exception.actions.map(lineAction))}</span>
                </div>
                {exception.edit ? ( // 정답지 30쪽 2단계 같음 + 고침: 고침 전(그때 대상 목록) · 뒤
                  <>
                    {editBefore ? (
                      <div className="rxr" data-item="edit-before">
                        <span>고침 전</span>
                        <span>{listLine(editBefore)}</span>
                      </div>
                    ) : null}
                    <div className="rxr" data-item="edit-after">
                      <span>고침 뒤</span>
                      <span>{listLine(exception.edit)}</span>
                    </div>
                  </>
                ) : null}
              </>
            ) : null}
            {splitError ? (
              <div className="rxr" data-item="none">
                <span>예외</span>
                <span>
                  <span className="ch blue">나누지 못함 · 새 예외</span>
                </span>
              </div>
            ) : null}
            <div className="rxr">
              <span>결과</span>
              <span data-part="ex-result">{result}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export interface ActionProps {
  s: SessionView;
  busy: boolean;
  checked: string[];
  onCheck: (items: string[]) => void;
  send: (body: Record<string, unknown>) => void;
}

/** T08: 원인 확정 · 조치(A 바로 · B 체크리스트 뒤) · 재가동(체크리스트 → 재가동 뒤 값 · 경계 이내 · 여전히 밖) — 한 화면 카드 셋. */
// 답장 001-zzx 가(가이드 14 ~ 16 · 27쪽): 재가동 뒤 값과 두 버튼은 따로 화면이 아니라 재가동 카드 안 · 버튼 이름은 잠긴 동안도 그대로.
export function ActionScreen({ s, busy, checked, onCheck, send }: ActionProps) {
  const [askOutside, setAskOutside] = useState(false); // D-530 답장 001-zzt 6(가이드 27쪽): 「여전히 밖」 → 숙련자 인계 확인 창
  const a = s.action;
  const r = s.restart;
  const kind = s.step.kind;
  const toggle = (x: string) => onCheck(checked.includes(x) ? checked.filter((y) => y !== x) : [...checked, x]);
  const actionOpen = a && !a.done_at && (kind === "action" || kind === "action_after_checklist");
  const needs = !!a && a.checklist.length > 0 && !a.checklist_done;
  const actionChecks = needs ? a!.checklist : [];
  const restartReady = r && r.checklist.every((x) => checked.includes(x));
  const v = kind === "restart_value" ? r?.value : null;
  return (
    <div className="ap-body">
      <div className="g64">
        <div className="stk">
          {s.correction ? (
            <div className="banner info">
              <Icon name="pencil" />
              <div>
                <b>
                  {s.correction.section} 정정으로 다시 판정했습니다 · {s.correction.at ? hhmm(s.correction.at) : ""}
                </b>
                뒤의 기록은 지우지 않고 '정정으로 무효'로 남겼습니다.
              </div>
            </div>
          ) : null}
          {a ? <CauseCard a={a} /> : null}
          {a ? (
            <div className="cd act">
              <div className="act-h">
                <b>조치 · {a.section === EX_NEW ? a.title : `${a.section} ${a.title}`}</b>
                <GradeBadge grade={a.grade} />
                {a.done_at ? <span className="ch green">완료</span> : null}
              </div>
              {isException(a.section) && s.exception_walk ? <ExWalk walk={s.exception_walk} /> : null}
              {a.steps.length ? <Proc steps={a.steps} /> : null}
              {actionOpen && needs ? <Checks items={actionChecks} on={(x) => checked.includes(x)} onToggle={toggle} /> : null}
              {actionOpen ? (
                needs ? (
                  <Btn
                    label="조치 진행"
                    off={busy || !actionChecks.every((x) => checked.includes(x))}
                    onClick={() => send({ type: "b_checklist_done", section: a.section, checked: a.checklist })}
                  />
                ) : (
                  <Btn label="조치 완료" off={busy} onClick={() => send({ type: "action_done", section: a.section })} />
                )
              ) : null}
            </div>
          ) : null}
          {r && (kind === "restart_check" || v) ? (
            <div className="cd act">
              <div className="act-h">
                <b>
                  {r.title} · {r.section}절
                </b>
                <GradeBadge grade={r.grade} />
                {r.restarted_at ? <span className="ch green">재가동 {hhmm(r.restarted_at)}</span> : null}
              </div>
              {v ? (
                <>
                  <div className="rvrow">
                    <span>{v.sensor}</span>
                    <b className="rv-big">{v.value_text}</b>
                    <span>경계 {v.boundary_text}</span>
                  </div>
                  <div className="choice2">
                    <Btn cls="ghost" label="여전히 밖" off={busy} onClick={() => setAskOutside(true)} />
                    <Btn cls="" label="경계 이내" off={busy} onClick={() => send({ type: "restart_result", outcome: "within" })} />
                  </div>
                </>
              ) : (
                <>
                  <Checks items={r.checklist} on={(x) => checked.includes(x)} onToggle={toggle} />
                  <Btn label="재가동" off={busy || !restartReady} onClick={() => send({ type: "restart", checked: r.checklist })} />
                </>
              )}
            </div>
          ) : null}
        </div>
        <PathCard path={s.path} />
      </div>
      {askOutside ? (
        <>
          <div className="dim" />
          <div className="modal" role="dialog" aria-label="숙련자 인계 확인">
            <div className="mh">
              <Icon name="triangle-alert" />
              <b>재가동 뒤에도 경계 밖입니다. 숙련자에게 인계합니까?</b>
            </div>
            <div className="mb">
              <span className="btn ghost" role="button" tabIndex={0} onClick={() => setAskOutside(false)}>
                취소
              </span>
              <span
                className={`btn warnb${busy ? " dis" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => {
                  if (busy) return;
                  setAskOutside(false);
                  send({ type: "restart_result", outcome: "outside" });
                }}
              >
                숙련자 인계
              </span>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** T11 / monitor_close: 후보가 모두 빠지고 재가동 뒤 경계 이내 → 6.2절 감시 마감 후 보고서. */
export function MonitorScreen({ s, busy, send }: { s: SessionView; busy: boolean; send: ActionProps["send"] }) {
  const mo = s.monitor!;
  return (
    <div className="ap-body">
      <div className="g64">
        <div className="cd">
          <div className="done-h">
            <Icon name="circle-check" />
            <div>
              <span className="sm">이상 없음 · 감시</span>
              <h3>A · B 점검 모두 정상 · 재가동 후 경계 이내</h3>
              <span>{mo.section}절 · 설비를 감시 대상으로 등록하고 다음 교대 시작 때 같은 값을 다시 확인합니다.</span>
            </div>
          </div>
          <div className="act">
            <div className="act-h">
              <b>{mo.title.replace("·", " · ")}</b>
              <GradeBadge grade={mo.grade} />
              <span className="ch blue">{mo.section}절</span>
            </div>
          </div>
          <Btn label="보고서 작성" icon="file-text" off={busy} onClick={() => send({ type: "monitor_close" })} />
          <span className="note">보고서 확정 원인은 "이상 없음 · 감시"입니다. 같은 알람이 다시 오면 이 보고서가 근거로 표시됩니다.</span>
        </div>
        <PathCard path={s.path} />
      </div>
    </div>
  );
}

const ANSWER_LABEL: Record<string, string> = { approved: "승인 후 진행", daytime: "주간 처리", direct: "직접 확인" };
export { ANSWER_LABEL };

function Summary({
  call,
  label,
  editable,
  text,
  onText,
  ro,
}: {
  call: Call;
  label: string;
  editable: boolean;
  text: string | null;
  onText: (t: string | null) => void;
  ro?: boolean; // 보낸 뒤 '보낸 요약'(읽기만 · 연필 표시 뺌)
}) {
  if (ro)
    // G3 대조(덧 4 T10b): 이름표는 칸 밖 위 · 글은 테두리 없는 회색 칸(읽기만)
    return (
      <>
        <span className="sml">{label}</span>
        <div className="pv ro">
          <p>{call.summary.text}</p>
        </div>
      </>
    );
  return (
    <div className={ro ? "pv ro" : "pv"}>
      <span className="sm">{label}</span>
      {editable && text !== null ? (
        <textarea className="memo" aria-label="요약 고치기" value={text} autoFocus onChange={(e) => onText(e.target.value)} />
      ) : (
        <p
          role={editable ? "button" : undefined}
          tabIndex={editable ? 0 : undefined}
          title={editable ? "눌러서 고치기" : undefined}
          onClick={() => editable && onText(call.summary.text)}
        >
          {text ?? call.summary.text}
        </p>
      )}
    </div>
  );
}

export interface CallProps {
  s: SessionView;
  busy: boolean;
  approvedSeen: boolean;
  onApprovedSeen: () => void;
  onReport: () => void;
  send: (body: Record<string, unknown>) => Promise<boolean> | void;
}

/** T10: 다음 점검은 혼자 진행할 수 없음 → 요약(고칠 수 있음) · 파트장 호출 · 호출하지 않고 대기 → 보냄 → 답 3종. */
export function CallScreen({ s, busy, approvedSeen, onApprovedSeen, onReport, send }: CallProps) {
  const call = s.call!;
  const cor = call.correction; // T10 / summary_corrected: 조치 뒤 정정 → 다시 판정 대신 확인 요청(AC-47-C1)
  const [text, setText] = useState<string | null>(null);
  const ex = cor ? null : call.exception_guide; // D-543 예외 안내(약속 v8 · 예외 기록에서 온 매뉴얼 밖 점검)
  const [kf, setKf] = useState<string | null>(null); // 「노하우 등록」 글(열림 · D-556 글 하나)
  const [switching, setSwitching] = useState(false); // D-580 예외도 후보 하나 — 승인 전 [점검 바꾸기]
  const ans = call.answer;
  const ins: Instruction | null | undefined = s.instruction;
  const editable = call.status === "none" || call.status === "waiting";
  const by = ans ? `${ans.by_name ?? ans.by ?? ""} ${ans.at ? hhmm(ans.at) : ""}`.trim() : "";
  const sendCall = () => {
    const body: Record<string, unknown> = { type: "call_send" };
    if (text !== null && text.trim() && text.trim() !== call.summary.text) body.summary = text.trim();
    void send(body);
  };
  const callBtn = <Btn cls="lg full warnb" label="파트장 호출" lead="phone-call" off={busy} onClick={sendCall} />;
  const summary = (
    <Summary call={call} label="파트장에게 보낼 요약" editable={editable} text={text} onText={setText} />
  );
  let right;
  if (ans?.answer === "approved" && ins && !approvedSeen) {
    right = (
      <div className="cd">
        <div className="banner ok">
          <Icon name="circle-check" />
          <div>
            <b>파트장 답 · 승인 후 진행 · {by}</b>
          </div>
        </div>
        <div className="ins-h">
          <span className="sm">점검 {ins.check_no} · 파트장 승인</span>
          <h3>{ins.title}</h3>
          <div className="tags">
            <GradeBadge grade={ins.grade} />
            <span className="ch green">승인됨</span>
            <SecChip section={ins.section} topic={s.exception_topic} />
          </div>
        </div>
        <Proc steps={ins.steps} />
        <Btn label="점검 시작" icon="arrow-right" onClick={onApprovedSeen} />
      </div>
    );
  } else if (ans?.answer === "daytime") {
    right = (
      <div className="cd">
        <div className="banner warn">
          <Icon name="clock" />
          <div>
            <b>주간 처리로 결정 · 저부하 감시 운전</b>
            {by} · "{cor ? `정정 확인 · ${cor.section} ${cor.title}(${cor.before} → ${cor.after})` : `${call.title}(${call.section}, ${call.grade})`}"이 보고서 "미확인 · 요청"에 기록됩니다.
          </div>
        </div>
        {summary}
        <Btn label="보고서 작성" icon="file-text" onClick={onReport} />
      </div>
    );
  } else if (ans?.answer === "direct") {
    right = (
      <div className="cd">
        <div className="banner info">
          <Icon name="eye" />
          <div>
            <b>파트장 직접 확인 · 처리 결과</b>
            {by} · {ans.note ?? "처리 메모 없음"} — 보고서 "미확인 · 요청"에 기록됩니다.
          </div>
        </div>
        {summary}
        {/* 결정 018 ③: 처리 결과와 함께 답이 오므로 보고서로 간다 */}
        <Btn label="보고서 작성" icon="file-text" onClick={onReport} />
      </div>
    );
  } else if (call.status === "sent") {
    // D-546 U4 대조(답장 u4-audit-fix): '보낸 요약'(읽기만) 아래 '파트장 호출 보냄 · 시각 / 답 기다림' 한 줄 · 설명 문장 0
    right = (
      <div className="cd">
        <Summary call={call} label="보낸 요약" editable={false} text={null} onText={() => {}} ro />
        <div className="wait">
          <b>파트장 호출 보냄 · {call.sent_at ? hhmm(call.sent_at) : ""}</b>
          <span>답 기다림</span>
        </div>
      </div>
    );
  } else if (call.status === "waiting") {
    right = (
      <div className="cd">
        <div className="banner warn">
          <Icon name="clock" />
          <div>
            <b>호출하지 않고 대기 중 · {call.waiting_at ? hhmm(call.waiting_at) : ""}</b>
          </div>
        </div>
        {summary}
        {callBtn}
      </div>
    );
  } else {
    right = (
      <div className="cd">
        {summary}
        {callBtn}
        <Btn cls="ghost full" label="호출하지 않고 대기" off={busy} onClick={() => void send({ type: "call_wait" })} />
      </div>
    );
  }
  const icon = (v: string) => (v.startsWith("정상") ? "circle-check" : v.startsWith("이상") ? "circle-alert" : "circle-help");
  const tone = (v: string) => (v.startsWith("정상") ? "ok" : v.startsWith("이상") ? "bad" : "na");
  return (
    <div className="ap-body">
      <div className="g64">
        <div className="cd">
          {cor ? (
            // 답장 001-zzx 사(가이드 24쪽): 작은 줄 · 제목 '정정 확인 · 절 이름' · 칩 '이상 → 정상'
            <div className="warn-h">
              <Icon name="hand" />
              <div>
                <span className="sm">파트장 확인이 필요합니다</span>
                <h3>
                  정정 확인 · {cor.section} {cor.title}
                </h3>
                <div className="tags">
                  <span className="ch gray">
                    {cor.before} → {cor.after}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="warn-h">
              <Icon name="hand" />
              <div>
                <span className="sm">
                  {ex ? "예외 기록에서 온 점검" : call.section === EX_NEW ? "예외 진행" : "다음 점검은 혼자 진행할 수 없습니다"}
                </span>
                <h3>{call.title}</h3>
                <div className="tags">
                  <GradeBadge grade={call.grade} />
                  {ex ? (
                    <span className="ch blue">예외상황-{ex.topic ?? ex.title}</span>
                  ) : (
                    <SecChip section={call.section ?? ""} topic={s.exception_topic} /> // 답 뒤(안내 없음)도 같은 이름
                  )}
                  {call.minutes != null ? <span className="ch gray">약 {call.minutes}분</span> : null}
                </div>
              </div>
            </div>
          )}
          {ex ? <ExWalk walk={s.exception_walk} /> : null}
          {ex ? (
            // 답장 001-zzz10(사용자): 점검방법 글 = 안내 글 + 출처 한 줄(정답지 고침A 26쪽 — 출처 = 그 예외 이름 · 날짜 · 상황)
            <>
              <div className="sub">점검방법</div>
              <div className="pv xkh">
                <p>{ex.text}</p>
                <span className="xsrc">
                  출처 · 예외상황-{ex.topic ?? ex.title}
                  {ex.source
                    ? ` · ${ex.source.equipment_id} · ${dateTime(ex.source.occurred_at).slice(5, 10)} — 상황 '${ex.source.situation}'`
                    : ""}
                </span>
              </div>
              {s.exception_why ? (
                <div className="xwhy" data-part="ex-why">
                  {s.exception_why}
                </div>
              ) : null}
              {editable && s.switch_candidates?.length ? (
                <div className="refs">
                  <span className="lk sw" role="button" tabIndex={0} onClick={() => !busy && setSwitching(true)}>
                    <Icon name="arrow-left-right" />
                    점검 바꾸기
                  </span>
                </div>
              ) : null}
              {switching ? (
                <>
                  <div className="dim" />
                  <div className="modal swm" role="dialog" aria-label="점검 바꾸기">
                    <div className="mh">
                      <Icon name="arrow-left-right" />
                      <b>점검 바꾸기</b>
                    </div>
                    <div className="swl">
                      <SwitchRows
                        cands={s.switch_candidates ?? []}
                        busy={busy}
                        topic={s.exception_topic}
                        onPick={(section) => {
                          setSwitching(false);
                          void send({ type: "switch_check", section });
                        }}
                      />
                    </div>
                    <div className="mb">
                      <Btn cls="ghost" label="취소" onClick={() => setSwitching(false)} />
                    </div>
                  </div>
                </>
              ) : null}
            </>
          ) : null}
          {kf !== null ? (
            // 「점검방법 등록」: 같은 카드 자리에 예외 글 하나(보고서 칸과 같은 꼴) · 열린 동안 목록 둘은 접음
            <div className="xkf">
              <div className="cd-h">
                <b>점검방법 등록</b>
                <span className="ch gray">보고서와 함께 승인</span>
              </div>
              <textarea
                className="memo"
                aria-label="예외 글"
                placeholder={EX_HINT}
                maxLength={2000}
                value={kf}
                onChange={(e) => setKf(e.target.value)}
              />
              <div className="mb">
                <Btn cls="ghost" label="취소" onClick={() => setKf(null)} />
                <Btn
                  cls=""
                  label="등록"
                  off={busy}
                  onClick={() => {
                    void send({ type: "exception_draft", exception_record: { text: kf.trim() } });
                    setKf(null);
                  }}
                />
              </div>
            </div>
          ) : (
            <>
            <div className="sub">지금까지 점검</div>
            <div className="dn2">
              {call.section === EX_NEW ? ( // 정답지 16쪽: 예외 진행은 안전 단계(잠금 확인 등)부터 한 것 모두
                (s.path ?? [])
                  .filter((p) => p.status !== "now")
                  .map((p, i) => (
                    <span key={i}>
                      <Icon name={p.status === "bad" ? "circle-alert" : "circle-check"} className={p.status === "bad" ? "bad" : "ok"} />
                      {p.title} · {p.detail}
                    </span>
                  ))
              ) : call.checks_done.length ? (
                call.checks_done.map((d) => (
                  <span key={d.section}>
                    <Icon name={icon(d.value)} className={tone(d.value)} />
                    {d.title} · {d.value}
                    {d.note ? `(${d.note})` : ""}
                  </span>
                ))
              ) : (
                <span>점검 전</span>
              )}
            </div>
            {ex ? (
              ex.next ? (
                <>
                  <div className="sub">원래 순서 다음</div>
                  <div className="tags xnext">
                    <span className="ch gray">
                      {ex.next.section} {ex.next.title}
                    </span>
                  </div>
                </>
              ) : null
            ) : cor ? null : call.section === EX_NEW ? ( // 정답지 16쪽: 예외 진행 호출 = 승인 전 조치 잠김
              <>
                <div className="sub">조치</div>
                <Btn label="조치 잠김 · 파트장 승인 뒤" off onClick={() => {}} />
              </>
            ) : ( // 정정 확인은 조치 뒤라 남은 후보 칸 없음(가이드 24쪽)
              <>
                <div className="sub">남은 후보</div>
                <div className="tags">
                  {call.candidates.map((c) => (
                    <span className="ch gray" key={c.code}>
                      {c.name} {c.pct}%
                    </span>
                  ))}
                </div>
              </>
            )}
            </>
          )}
          {ex && kf === null ? ( // 답장 001-zzz10(사용자): 왼쪽 카드 오른쪽 아래 — 호출과 따로
            <div className="xkr">
              <Btn
                cls="ghost"
                label="점검방법 등록"
                onClick={() => setKf(s.exception_draft?.text ?? "")}
              />
            </div>
          ) : null}
        </div>
        {right}
      </div>
    </div>
  );
}

/** T11: 숙련자 인계(맞지 않는 소견 · 확인 불가 반복 · 재가동 뒤 밖). 보내면 보고서(확정 원인 '원인 미상'). */
export function HandoverScreen({
  s,
  busy,
  send,
  onReport,
}: {
  s: SessionView;
  busy: boolean;
  send: CallProps["send"];
  onReport?: () => void;
}) {
  const call = s.call!;
  const [text, setText] = useState<string | null>(null);
  const head = s.step.reason === "no_alternative" ? "확인 불가가 반복됨" : "숙련자 인계";
  const ans = call.answer;
  const by = ans ? `${ans.by_name ?? ans.by ?? ""} ${ans.at ? hhmm(ans.at) : ""}`.trim() : "";
  const sendIt = () => {
    const body: Record<string, unknown> = { type: "call_send" };
    if (text !== null && text.trim() && text.trim() !== call.summary.text) body.summary = text.trim();
    void send(body);
  };
  const label = "숙련자 인계 요약"; // D-530 답장 001-zzt 7: 괄호 이름표 뺌(가이드 28쪽)
  const rv = s.restart?.outcome === "outside" ? s.restart.value : null;
  let action;
  if (ans) {
    // 결정 018 ④: 파트장 답(주간 숙련자 배정 · 직접 확인)과 처리 메모 → 보고서
    action = (
      <>
        <div className="banner ok">
          <Icon name="circle-check" />
          <div>
            <b>
              {ans.answer === "assign" ? "주간 숙련자 배정" : "파트장 직접 확인"} · {by}
            </b>
            {ans.note ?? "처리 메모 없음"} — 보고서 "미확인 · 요청"에 기록됩니다.
          </div>
        </div>
        <Summary call={call} label={label} editable={false} text={null} onText={() => {}} />
        <Btn label="보고서 작성" icon="file-text" onClick={() => onReport?.()} />
      </>
    );
  } else if (call.status === "sent") {
    // 결정 018 ④: 보낸 뒤 답 대기 — 파트장 호출과 같은 꼴(답장 u4-audit-fix: '보낸 요약' 아래 '보냄 · 시각 / 답 기다림')
    action = (
      <>
        <Summary call={call} label="보낸 요약" editable={false} text={null} onText={() => {}} ro />
        <div className="wait">
          <b>숙련자 인계 보냄 · {call.sent_at ? hhmm(call.sent_at) : ""}</b>
          <span>답 기다림</span>
        </div>
      </>
    );
  } else {
    action = (
      <>
        <Summary call={call} label={label} editable text={text} onText={setText} />
        <Btn cls="lg full warnb" label="숙련자 인계 보내기" lead="user" off={busy} onClick={sendIt} />
      </>
    );
  }
  return (
    <div className="ap-body">
      <div className="g64">
        {/* 답장 001-zzx 바(가이드 28쪽): 왼쪽 = 한 카드(빨강 머리 · 재가동 뒤 값 줄 + '지금까지 점검 · 조치' 체크 목록), 오른쪽 = 인계 요약 + 보내기 */}
        <div className="cd">
          <div className="warn-h bad">
            <Icon name="user" />
            <div>
              <span className="sm">{head}</span>
              <h3>{call.head}</h3>
              {rv ? (
                <span className="vline">
                  {rv.sensor} {rv.value_text} · 경계 {rv.boundary_text}
                </span>
              ) : null}
            </div>
          </div>
          <div className="sub">지금까지 점검 · 조치</div>
          <div className="cklist">
            {(s.path ?? []).map((p, i) => (
              <div className={`cki ${p.status}`} key={i}>
                <span className="cb">{p.status === "bad" ? "!" : <Icon name="check" />}</span>
                <span className="t">{p.detail ? `${p.title} · ${p.detail}` : p.title}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="cd">{action}</div>
      </div>
    </div>
  );
}

export interface ReportProps {
  report: Report;
  busy: boolean;
  send: (body: Record<string, unknown>) => Promise<boolean> | void;
  onHome: () => void;
  onFinal?: (reportId: number) => void;
}

/** T09: 보고서 8항목(출처 · 고치기), 확정 전 확인 3항목, 확정 → 승인 대기(확정 뒤 수정 없음 — AC-33).
 *  D-528(가이드 정비자 12 · 13 · 반려 34쪽): 반려면 오른쪽 위에 반려 사유 · 「보고서 확정」은 확인 창(확정 · 취소) · 안내 문장 뺌. */
export function ReportScreen({ report, busy, send, onHome, onFinal }: ReportProps) {
  const [edit, setEdit] = useState<{ key: string; text: string } | null>(null);
  const [asking, setAsking] = useState(false);
  // D-538 예외 상황 기록 — 고침A 18 · 19쪽: 예외 진행 세션만 보고서 전 팝업 → 「보고서에 적용」 → 보고서 안 '예외 상황' 구역
  const rec = report.exception_record;
  const applied = report.exception_applied;
  const exSession = !!report.exception_session;
  const rej = report.rejection;
  const draft = report.status === "draft";
  const [popup, setPopup] = useState(draft && exSession && !applied); // 적용 전이면 보고서 전 팝업(자동)
  const exReady = !exSession || !!applied || !draft; // 예외 진행 세션은 적용해야 확정
  const save = () => {
    if (!edit) return;
    const f = report.fields.find((x) => x.key === edit.key);
    setEdit(null);
    if (f && edit.text !== f.text) void send({ type: "report_edit", field: edit.key, text: edit.text });
  };
  const failed = report.checks.filter((c) => !c.ok);
  return (
    <div className="ap-body">
      <div className="g73">
        <div className="cd">
          <div className="cd-h">
            <Icon name="file-text" />
            <b>정비 보고서 초안</b>
            {draft ? (
              <span className={`ch ${rej ? "red" : "amber"}`}>{rej ? "반려 · 고치는 중" : "초안"}</span>
            ) : (
              <span className="ch green">확정 · 승인 대기</span>
            )}
          </div>
          {report.fields.map((f) => (
            <div className={`rf${f.ok ? "" : " fail"}`} key={f.key} data-field={f.key}>
              <span className="rk2">{f.label}</span>
              {edit?.key === f.key ? (
                <textarea
                  className="rv2 memo"
                  aria-label={`${f.label} 고치기`}
                  value={edit.text}
                  autoFocus
                  onChange={(e) => setEdit({ key: f.key, text: e.target.value })}
                  onBlur={save}
                />
              ) : (
                <span className="rv2">
                  {f.key === "evidence_records" && report.low_evidence ? (
                    <>
                      <span className="ch amber">근거 적음</span>{" "}
                    </>
                  ) : null}
                  {f.fallback ? (
                    <>
                      <span className="ch fb">원문으로 대체됨</span>{" "}
                    </>
                  ) : null}
                  {f.text}
                </span>
              )}
              <span className="ch src">{f.source}</span>
              <span
                className="ed"
                role={draft ? "button" : undefined}
                tabIndex={draft ? 0 : undefined}
                aria-label={draft ? `${f.label} 고치기` : undefined}
                onClick={() => draft && !busy && setEdit({ key: f.key, text: f.text })}
              >
                {draft ? <Icon name="pencil" /> : null}
              </span>
            </div>
          ))}
          {draft && applied ? (
            <ExSection
              situation={report.exception_auto?.situation ?? ""}
              text={applied.text}
              exception={applied.exception}
              splitError={applied.split_error}
              result={report.exception_auto?.result}
              onEdit={busy ? undefined : () => setPopup(true)}
            />
          ) : !draft && rec ? (
            <ExSection
              situation={rec.situation}
              text={rec.text}
              exception={rec.exception}
              splitError={rec.split_error}
              result={rec.result}
              legacy={rec.text == null ? { situation: rec.situation, action: rec.action } : null}
              done
            />
          ) : null}
        </div>
        <div>
          {draft && rej ? (
            <div className="banner bad rej" data-part="rejection">
              <Icon name="rotate-ccw" />
              <div>
                <b>
                  반려 사유 · {rej.by_name ?? ""} {rej.at ? hhmm(rej.at) : ""}
                </b>
                {rej.reason}
              </div>
            </div>
          ) : null}
          <div className="cd ">
            <div className="cd-h">
              <Icon name="shield-check" />
              <b>확정 전 확인</b>
            </div>
            <div className="ckl">
              {report.checks.map((c) => (
                <div className={`ck ${c.ok ? "on" : "no"}`} key={c.key}>
                  <span className="cb">
                    <Icon name={c.ok ? "check" : "x"} />
                  </span>
                  {c.label}
                </div>
              ))}
            </div>
            {draft && failed.length ? (
              <div className="banner bad">
                <Icon name="circle-alert" />
                <div>
                  <b>{failed[0].label}</b>
                  {report.messages.join(" ")}
                </div>
              </div>
            ) : null}
            {/* 답장 001-zzx 다(가이드 17 · 19쪽): 「보고서 확정」 · 확정 띠 · 「최종본 보기」「홈으로」는 확정 전 확인 카드 안 */}
            {draft ? (
              <div className="cta2">
                <Btn
                  label="보고서 확정"
                  icon="file-check-2"
                  off={busy || !report.can_confirm || edit !== null || !exReady}
                  onClick={() => setAsking(true)}
                />
              </div>
            ) : (
              <>
                <div className="banner ok">
                  <Icon name="circle-check" />
                  <div>
                    <b>보고서를 확정했습니다 · {report.confirmed_at ? hhmm(report.confirmed_at) : ""}</b>
                  </div>
                </div>
                {report.report_id != null && onFinal ? ( // D-528 가이드 정비자 13: 확정 뒤 「최종본 보기」(새 화면)
                  <Btn cls="lg full" label="최종본 보기" onClick={() => onFinal(report.report_id as number)} />
                ) : null}
                <Btn cls="lg full ghost" label="홈으로" onClick={onHome} />
              </>
            )}
          </div>
        </div>
      </div>
      {asking && draft ? (
        <>
          <div className="dim" />
          <div className="modal" role="dialog" aria-label="보고서 확정">
            <div className="mh">
              <Icon name="file-check-2" />
              <b>보고서를 확정합니까?</b>
            </div>
            <div className="bub ai">
              <div>확정하면 파트장 승인 대기로 넘어갑니다.</div>
            </div>
            <div className="mb">
              <span className="btn ghost" role="button" tabIndex={0} onClick={() => setAsking(false)}>
                취소
              </span>
              <span
                className={`btn${busy || !report.can_confirm || !exReady ? " dis" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => {
                  if (busy || !report.can_confirm || !exReady) return;
                  setAsking(false);
                  void send({ type: "report_confirm" }); // 예외 상황은 「보고서에 적용」한 것을 서버가 함께 확정
                }}
              >
                확정
              </span>
            </div>
          </div>
        </>
      ) : null}
      {popup && draft && exSession ? (
        <ExPopup
          report={report}
          busy={busy}
          send={send}
          onClose={applied ? () => setPopup(false) : undefined}
          onApplied={() => setPopup(false)}
        />
      ) : null}
    </div>
  );
}
