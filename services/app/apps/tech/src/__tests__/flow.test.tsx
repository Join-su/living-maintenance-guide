// P-10 component 시험: S-03 실제 API 응답(fixtures/s03-*)으로 T08 · T09 · T10 · T11 · 홈 완료 행 · 종 '답이 온 호출'을 그려
// Prototype v17 구조(영역 · 버튼 · 순서 · 토큰)와 보내는 입력을 대조한다.

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api, Home, Notifications, SessionView } from "@c1a15/ui";
import { ActionScreen, CallScreen, HandoverScreen, MonitorScreen, PathCard, ReportScreen } from "../screens/Flow";
import { dayTime, HomeScreen, NotifyPanel } from "../screens/Home";
import { Session, stepNumber } from "../screens/Session";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
function load<T = SessionView>(name: string): T {
  return structuredClone(fx[`./fixtures/${name}.json`]) as T;
}
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");
const btn = (root: ParentNode, label: string) =>
  [...root.querySelectorAll(".btn")].find((b) => b.textContent?.trim() === label) as HTMLElement;

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("T08 조치 · 재가동", () => {
  // 답장 001-zzx 가 · 나(가이드 14 ~ 16 · 27쪽): 왼쪽 카드 셋(원인 확정 · 조치 · 재가동) · 재가동 뒤 값과 두 버튼은 재가동 카드 안(따로 화면 0),
  // 오른쪽 '지금까지 점검 · 조치'.
  const cards = (root: ParentNode) => [...root.querySelectorAll(".stk > .cd")];
  it("action_a: 원인 확정 카드(원인 칩 · 근거 사례) · 조치 카드(조치 · 절 이름 · 등급) · '조치 완료' → action_done, 경로 끝은 '지금'", () => {
    const s = load("s03-action-a");
    const send = vi.fn();
    const { container } = render(<ActionScreen s={s} busy={false} checked={[]} onCheck={() => {}} send={send} />);
    const [cause, act, ...rest] = cards(container);
    expect(rest).toHaveLength(0);
    expect(texts(cause, ".cd-h > b, .cd-h > .ch")).toEqual(["원인 확정", s.action!.cause_name]);
    expect(cause.querySelector(".cd-h .ch")?.className).toContain("red");
    expect(texts(cause, ".kvr > *")).toEqual(["근거 사례", s.action!.evidence_record_ids.join(" · ")]);
    expect(texts(act, ".act-h > *")).toEqual(["조치 · 4.3.2 흡기 필터 교체", "A단독 가능"]);
    expect(texts(act, ".proc li")).toEqual(s.action!.steps.map((t, i) => `${i + 1}${t}`)); // 답장 001-zzy: 절차 1 ~ 3(매뉴얼 그대로)
    expect(texts(act, ".btn")).toEqual(["조치 완료"]);
    expect(container.querySelector(".done-h")).toBeNull();
    fireEvent.click(btn(container, "조치 완료"));
    expect(send).toHaveBeenCalledWith({ type: "action_done", section: "4.3.2" });
    const tl = [...container.querySelectorAll(".tli")];
    expect(tl.map((t) => t.className)).toEqual(["tli ok", "tli ok", "tli bad", "tli now"]);
    expect(tl.at(-1)!.querySelector(".tt")?.textContent).toBe("지금");
    expect(container.querySelector(".g64 > .cd:last-child .cd-h b")?.textContent).toBe("지금까지 점검 · 조치");
  });

  it("예외 진행 조치(정답지 묶음 ① 17쪽): 원인 카드 '원인 · 예외 · 보고서 예외 칸에서' · 조치 · 예외 조치(C) · 절차 줄 0 · 「조치 완료」", () => {
    const s = load("s03-action-a");
    s.action = { cause_code: "", cause_name: "예외", evidence_record_ids: [], section: "EX-NEW", title: "예외 조치", verb: "조치", grade: "C", minutes: null, done_at: null, checklist: [], checklist_done: false, steps: [] };
    s.step = { ...s.step, kind: "action", section: "EX-NEW", grade: "C" };
    const send = vi.fn();
    const { container } = render(<ActionScreen s={s} busy={false} checked={[]} onCheck={() => {}} send={send} />);
    const [cause, act] = cards(container);
    expect(texts(cause, ".cd-h > b, .cd-h > .ch, .kvr > *")).toEqual(["원인", "예외", "원인", "보고서 예외 칸에서"]);
    expect(act.querySelector(".act-h b")?.textContent).toBe("조치 · 예외 조치");
    expect(act.querySelector(".proc")).toBeNull();
    fireEvent.click(btn(act, "조치 완료"));
    expect(send).toHaveBeenCalledWith({ type: "action_done", section: "EX-NEW" });
  });

  it("checklist_b: B 조치는 체크리스트를 모두 확인해야 「조치 진행」이 열린다(AC-32-C2 · AC-43-C4)", () => {
    const s = load("s03-action-b");
    const a = s.action!;
    const send = vi.fn();
    const onCheck = vi.fn();
    const r = render(<ActionScreen s={s} busy={false} checked={[]} onCheck={onCheck} send={send} />);
    expect(r.container.querySelector(".act .sm")).toBeNull(); // D-528: 「아래 항목을 모두 확인해야 …」 뺌(잠김은 버튼이 보임)
    expect(texts(r.container, ".act .ck")).toEqual(a.checklist);
    const locked = btn(r.container, "조치 진행"); // 가이드 15쪽 꼴: 잠긴 동안도 이름 그대로
    expect(locked.className).toContain("dis");
    fireEvent.click(locked);
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(r.container.querySelectorAll(".act .ck")[0]);
    expect(onCheck).toHaveBeenCalledWith([a.checklist[0]]);
    r.rerender(<ActionScreen s={s} busy={false} checked={a.checklist} onCheck={onCheck} send={send} />);
    fireEvent.click(btn(r.container, "조치 진행"));
    expect(send).toHaveBeenCalledWith({ type: "b_checklist_done", section: "4.3.2", checked: a.checklist });
  });

  it("restart_check: 조치 카드 '완료' 칩 · 재가동 카드(재가동 · 6.1절, B) 체크리스트, 다 확인해야 「재가동」(AC-21-C1)", () => {
    const s = load("s03-restart-check");
    const items = s.restart!.checklist;
    const send = vi.fn();
    const r = render(<ActionScreen s={s} busy={false} checked={items.slice(0, 2)} onCheck={() => {}} send={send} />);
    const [, act, rs] = cards(r.container);
    expect(texts(act, ".act-h > *")).toEqual(["조치 · 4.3.2 흡기 필터 교체", "A단독 가능", "완료"]);
    expect(act.querySelector(".btn")).toBeNull();
    expect(act.querySelectorAll(".proc li").length).toBe(s.action!.steps.length); // 조치 끝난 뒤에도 절차는 그대로(가이드 15쪽)
    expect(texts(rs, ".act-h > *")).toEqual(["재가동 · 6.1절", "B확인 후"]);
    expect(texts(rs, ".ck.on")).toEqual(items.slice(0, 2));
    const locked = btn(rs, "재가동"); // 잠긴 동안도 이름 '재가동'
    expect(locked.className).toContain("dis");
    fireEvent.click(locked);
    expect(send).not.toHaveBeenCalled();
    r.rerender(<ActionScreen s={s} busy={false} checked={items} onCheck={() => {}} send={send} />);
    fireEvent.click(btn(r.container, "재가동"));
    expect(send).toHaveBeenCalledWith({ type: "restart", checked: items });
  });

  it("restart_value: 같은 조치 화면 재가동 카드 안 값(센서 · 큰 값 · 경계) · 「여전히 밖」「경계 이내」 순서 · 설명 문장 0(AC-21-C2)", () => {
    const s = load("s03-restart-value");
    const v = s.restart!.value!;
    const send = vi.fn();
    const { container } = render(<ActionScreen s={s} busy={false} checked={[]} onCheck={() => {}} send={send} />);
    const [cause, act, rs] = cards(container);
    expect(cause.querySelector(".cd-h b")?.textContent).toBe("원인 확정");
    expect(texts(act, ".act-h > *").at(-1)).toBe("완료");
    expect(texts(rs, ".act-h > *")).toEqual(["재가동 · 6.1절", "B확인 후", expect.stringMatching(/^재가동 \d\d:\d\d$/)]);
    expect(rs.querySelector(".ck")).toBeNull();
    expect(texts(rs, ".rvrow > *")).toEqual([v.sensor, v.value_text, `경계 ${v.boundary_text}`]);
    expect(texts(rs, ".choice2 .btn")).toEqual(["여전히 밖", "경계 이내"]);
    expect(container.querySelector(".note")).toBeNull();
    expect(container.querySelector(".stk")?.textContent).not.toContain("재가동 후 값 확인"); // 오른쪽 경로의 지금 줄 이름은 서버 값 그대로
    fireEvent.click(btn(container, "경계 이내"));
    expect(send).toHaveBeenCalledWith({ type: "restart_result", outcome: "within" });
    send.mockClear();
    // D-530 답장 001-zzt 6(가이드 27쪽): 「여전히 밖」 → 확인 창(숙련자 인계 · 취소) — 취소는 창만 닫음
    fireEvent.click(btn(container, "여전히 밖"));
    expect(send).not.toHaveBeenCalled();
    const dlg = container.querySelector('.modal[aria-label="숙련자 인계 확인"]')!;
    expect(dlg.querySelector(".mh b")?.textContent).toBe("재가동 뒤에도 경계 밖입니다. 숙련자에게 인계합니까?");
    expect(texts(dlg, ".mb .btn")).toEqual(["취소", "숙련자 인계"]);
    fireEvent.click(btn(dlg, "취소"));
    expect(container.querySelector(".modal")).toBeNull();
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(btn(container, "여전히 밖"));
    fireEvent.click(btn(container.querySelector(".modal")!, "숙련자 인계"));
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith({ type: "restart_result", outcome: "outside" });
  });
});

describe("T10 파트장 호출", () => {
  it("summary: 혼자 진행 불가 머리 · 지금까지 점검 · 남은 후보, 요약을 고쳐 보낸다(1.4 · AC-30)", () => {
    const s = load("s03-call-summary");
    const c = s.call!;
    const send = vi.fn();
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={send} />,
    );
    expect(container.querySelector(".warn-h .sm")?.textContent).toBe("다음 점검은 혼자 진행할 수 없습니다");
    expect(container.querySelector(".warn-h h3")?.textContent).toBe(c.title);
    expect(texts(container, ".warn-h .tags > *")).toEqual(["C승인 필요", "매뉴얼 4.4절", `약 ${c.minutes}분`]);
    expect(texts(container, ".sub")).toEqual(["지금까지 점검", "남은 후보"]);
    expect(texts(container, ".dn2 span")).toEqual(c.checks_done.map((d) => `${d.title} · ${d.value}`));
    expect(texts(container, ".tags .ch.gray").slice(1)).toEqual(c.candidates.map((x) => `${x.name} ${x.pct}%`));
    expect(texts(container, ".cd:nth-child(2) > .btn")).toEqual(["파트장 호출", "호출하지 않고 대기"]);
    fireEvent.click(container.querySelector(".pv p")!);
    const ta = container.querySelector(".pv textarea") as HTMLTextAreaElement;
    expect(ta.value).toBe(c.summary.text);
    fireEvent.change(ta, { target: { value: `${c.summary.text} 팬 소음 있음.` } });
    fireEvent.click(btn(container, "파트장 호출"));
    expect(send).toHaveBeenCalledWith({ type: "call_send", summary: `${c.summary.text} 팬 소음 있음.` });
    fireEvent.click(btn(container, "호출하지 않고 대기"));
    expect(send).toHaveBeenCalledWith({ type: "call_wait" });
  });

  // D-543 구현 2 · 답장 001-zzz10(사용자): 예외 안내 = 머리 '예외 기록에서 온 점검' · 노하우 글 + 출처 한 줄 · 원래 순서 다음,
  // 왼쪽 카드 오른쪽 아래 「점검방법 등록」 → 같은 카드 자리에 두 칸(호출과 따로 · exception_draft)
  const exceptionCall = () => {
    const s = load("s03-call-summary");
    Object.assign(s.call!, {
      section: "EX-R-0012",
      title: "필터 덮개 고정 나사 확인",
      minutes: null,
      exception_guide: {
        section: "EX-R-0012",
        relation_id: "EX-R-0012",
        grade: "C",
        title: "필터 덮개 고정 나사 확인",
        text: "덮개 고정 나사 네 개가 다 있는지 먼저 본다.",
        steps: ["덮개 나사 확인"],
        criteria: "나사 네 개 모두 체결 = 정상",
        basis: ["R-0012"],
        next: { section: "4.5", title: "냉각수 수위 확인" },
        source: { record_id: "R-0012", equipment_id: "M-01", occurred_at: "2026-10-28T01:10:00Z", situation: "나사 하나가 빠져 있었음" },
      },
    });
    return s;
  };
  it("파트장 답 뒤 왼쪽 카드 칩(F3 W06): 예외 절이면 '예외 기록 …' — 답 뒤 call에 예외 안내가 없어도 '매뉴얼 EX-…절' 0", () => {
    const s = load("s03-ans-approved");
    Object.assign(s.call!, { section: "EX-R-0012", exception_guide: null });
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={vi.fn()} />,
    );
    const chips = texts(container.querySelector(".g64 > .cd")!, ".warn-h .tags > *");
    expect(chips).toContain("예외 기록 R-0012"); // 세션에 예외 주제가 없으면 기록 번호
    cleanup();
    const named = render(
      <CallScreen s={{ ...s, exception_topic: "필터 덮개 나사" }} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={vi.fn()} />,
    ).container;
    expect(texts(named.querySelector(".g64 > .cd")!, ".warn-h .tags > *")).toContain("예외상황-필터 덮개 나사"); // 고침A 이름
    expect(chips.join(" ")).not.toContain("매뉴얼 EX-");
  });

  it("예외 안내(고침A 26쪽): 머리 · 이름 '예외상황-{주제}' · 점검방법 글 + 출처 · 원래 순서 다음 · 호출 버튼 그대로", () => {
    const s = exceptionCall();
    const send = vi.fn();
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={send} />,
    );
    expect(container.querySelector(".warn-h .sm")?.textContent).toBe("예외 기록에서 온 점검");
    expect(container.querySelector(".warn-h h3")?.textContent).toBe("필터 덮개 고정 나사 확인");
    expect(texts(container, ".warn-h .tags > *")).toEqual(["C승인 필요", "예외상황-필터 덮개 고정 나사 확인"]); // 고침A 26쪽 이름(주제 칸 없으면 이름)
    expect(texts(container, ".sub")).toEqual(["점검방법", "지금까지 점검", "원래 순서 다음"]);
    expect(container.querySelector(".xkh p")?.textContent).toBe("덮개 고정 나사 네 개가 다 있는지 먼저 본다.");
    expect(container.querySelector(".xkh .xsrc")?.textContent).toBe("출처 · 예외상황-필터 덮개 고정 나사 확인 · M-01 · 10-28 — 상황 '나사 하나가 빠져 있었음'");
    expect(texts(container, ".xnext .ch")).toEqual(["4.5 냉각수 수위 확인"]);
    expect(texts(container, ".g64 > .cd:first-child .xkr .btn")).toEqual(["점검방법 등록"]);
    expect(texts(container, ".cd:nth-child(2) > .btn")).toEqual(["파트장 호출", "호출하지 않고 대기"]);
    fireEvent.click(btn(container, "파트장 호출"));
    expect(send).toHaveBeenLastCalledWith({ type: "call_send" });
  });

  // D-566 약속 v10 걸음 길(정답지 26쪽): 호출 카드 머리 아래 '안내 차례 · 적은 순서 그대로' — 점검 둘 → 조치 둘 · 지금 걸음 굵게
  // D-642 C: 한 줄 = 지시 문장(서버 say) · '지금'은 문장 앞 표시로 따로
  const SAY1 = "필터 덮개 고정 나사 네 개를 손으로 돌려 보고, 헐거우면 이상으로 적습니다.";
  const WALK = [
    { step: "c1", side: "check", what: "필터 덮개 고정 나사 확인", say: SAY1, done: false, now: true },
    { step: "c2", side: "check", what: "흡기 덕트 이음매", say: "흡기 덕트 이음매", done: false, now: false },
    { step: "a1", side: "action", what: "나사를 새로 끼움", say: "나사를 새로 끼움 · 부품 M6 나사", done: false, now: false },
    { step: "a2", side: "action", what: "밴드를 다시 조임", say: "밴드를 다시 조임", done: false, now: false },
  ];
  it("예외 안내 걸음 길(정답지 26쪽): '안내 차례 · 적은 순서 그대로' 넷 · 1 점검 … · 지금 · 그 아래 점검방법 · 옛 길(목록 없음)은 차례 0", () => {
    const s = { ...exceptionCall(), exception_walk: WALK, exception_topic: "필터 덮개 나사" };
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={vi.fn()} />,
    );
    const wk = container.querySelector('[data-part="ex-walk"]')!;
    expect(wk.querySelector("b")?.textContent).toBe("안내 차례 · 적은 순서 그대로");
    expect(texts(wk, ".xwr")).toEqual([`지금점검 1 ${SAY1}`, "점검 2 흡기 덕트 이음매", "조치 3 나사를 새로 끼움 · 부품 M6 나사", "조치 4 밴드를 다시 조임"]);
    expect(texts(wk, ".xwr.now .xnow")).toEqual(["지금"]); // '지금'은 문장 앞 표시 하나
    expect(texts(wk, ".xwr.now .xsay")).toEqual([SAY1]); // 문장 안엔 '지금' 0
    expect(texts(wk, ".xnow")).toHaveLength(1);
    expect(wk.nextElementSibling?.textContent).toBe("점검방법"); // 차례 다음 점검방법(26쪽 순서)
    cleanup();
    const old = render(
      <CallScreen s={{ ...exceptionCall(), exception_walk: null }} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={vi.fn()} />,
    ).container;
    expect(old.querySelector('[data-part="ex-walk"]')).toBeNull();
  });

  it("예외 조치 걸음(약속 v10): 조치 카드에 안내 차례(끝낸 걸음 흐리게 · 지금 굵게) · 「조치 완료」 → action_done(절만 · 걸음은 서버가)", () => {
    const s = load("s03-action-a");
    const walk = WALK.map((w, i) => ({ ...w, done: i < 2, now: i === 2 }));
    Object.assign(s, { exception_walk: walk, step: { ...s.step, kind: "action", section: "EX-R-0012", step: "a1", step_index: 3, steps_total: 4 } });
    s.action = { ...s.action!, section: "EX-R-0012", title: "나사를 새로 끼움", cause_name: "예외상황-필터 덮개 나사", steps: ["부품 M6 나사"], done_at: null, checklist: [] };
    const send = vi.fn();
    const { container } = render(<ActionScreen s={s} busy={false} checked={[]} onCheck={() => {}} send={send} />);
    const card = container.querySelector(".cd.act")!;
    expect(texts(card, ".xwr.done")).toEqual([`점검 1 ${SAY1}`, "점검 2 흡기 덕트 이음매"]);
    expect(texts(card, ".xwr.now")).toEqual(["지금조치 3 나사를 새로 끼움 · 부품 M6 나사"]);
    expect(texts(card, ".proc li")).toEqual(["1부품 M6 나사"]);
    fireEvent.click(btn(card, "조치 완료"));
    expect(send).toHaveBeenLastCalledWith({ type: "action_done", section: "EX-R-0012" });
  });

  it("「점검방법 등록」(D-556): 같은 카드에 글 하나(자리글 = 안내 문구) · 목록 접힘 → 등록이면 exception_draft · 취소면 그대로", () => {
    const s = exceptionCall();
    s.exception_draft = { text: "덮개 나사 확인" };
    const send = vi.fn();
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={send} />,
    );
    fireEvent.click(btn(container, "점검방법 등록"));
    expect(container.querySelector(".xkr")).toBeNull();
    expect(texts(container, ".sub")).toEqual(["점검방법"]); // 등록 칸이 열린 동안 목록 둘은 접음
    expect(texts(container, ".xkf .cd-h > *")).toEqual(["점검방법 등록", "보고서와 함께 승인"]);
    const ta = container.querySelector('.xkf textarea[aria-label="예외 글"]') as HTMLTextAreaElement;
    expect([ta.value, ta.placeholder, ta.maxLength]).toEqual(["덮개 나사 확인", "점검한 것과 조치한 것을 모두 적어 주세요", 2000]); // 초안이 있으면 채움
    fireEvent.click(btn(container, "취소"));
    expect(container.querySelector(".xkf")).toBeNull();
    expect(texts(container, ".sub")).toEqual(["점검방법", "지금까지 점검", "원래 순서 다음"]);
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(btn(container, "점검방법 등록"));
    fireEvent.change(container.querySelector('.xkf textarea[aria-label="예외 글"]')!, { target: { value: " 점검 덕트 이음매. 조치 다시 끼움 " } });
    fireEvent.click(btn(container, "등록"));
    expect(send).toHaveBeenCalledWith({ type: "exception_draft", exception_record: { text: "점검 덕트 이음매. 조치 다시 끼움" } });
    expect(container.querySelector(".xkf")).toBeNull();
  });

  it("「점검방법 등록」 초안이 없어도 빈 글 칸이 열림(e2e 사람 손 길)", () => {
    const s = exceptionCall();
    s.exception_draft = null;
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={vi.fn()} />,
    );
    fireEvent.click(btn(container, "점검방법 등록"));
    const ta = container.querySelector('.xkf textarea[aria-label="예외 글"]') as HTMLTextAreaElement;
    expect(ta.value).toBe("");
    expect(container.querySelector(".xkr")).toBeNull();
  });

  it("D-580 예외 안내 호출 화면: [점검 바꾸기] → 매뉴얼 후보 고르면 switch_check · 승인 답 뒤엔 링크 0", () => {
    const s = load("s03-call-summary");
    const guide = {
      section: "EX-R-0012", relation_id: "EX-R-0012", grade: "C", title: "예외상황-덮개", topic: "덮개", text: "덮개를 본다",
      minutes: 5, next_section: null, steps: [], basis: [], source: null, next: null, checks: null, actions: null,
    };
    Object.assign(s.call!, { section: "EX-R-0012", title: "덮개 볼트", exception_guide: guide, status: "none" });
    Object.assign(s, {
      exception_why: "M-01에서 배운 규칙 · 이 기기도 3일 전 같은 정비를 함",
      switch_candidates: [{ section: "4.3", title: "흡기 필터 육안 점검", grade: "A", summary: null, exception: false }],
    });
    const send = vi.fn(() => Promise.resolve(true));
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={send} />,
    );
    expect(container.querySelector('[data-part="ex-why"]')!.textContent).toContain("이 기기도 3일 전");
    fireEvent.click(container.querySelector(".lk.sw")!);
    fireEvent.click(container.querySelector('.swm .swc[data-section="4.3"]')!);
    expect(send).toHaveBeenCalledWith({ type: "switch_check", section: "4.3" });
  });

  it("예외 진행 호출 후 조치(정답지 묶음 ① 16쪽): 머리 '예외 진행' · 예외 조치 · C · '예외' · 조치 잠김 · 파트장 승인 뒤 · 남은 후보 0", () => {
    const s = load("s03-call-summary");
    Object.assign(s.call!, { section: "EX-NEW", title: "예외 조치", grade: "C", minutes: null, exception_guide: null });
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={vi.fn()} />,
    );
    const left = container.querySelector(".g64 > .cd")!;
    expect(left.querySelector(".warn-h .sm")?.textContent).toBe("예외 진행");
    expect(left.querySelector(".warn-h h3")?.textContent).toBe("예외 조치");
    expect(texts(left, ".warn-h .tags > *")).toEqual(["C승인 필요", "예외"]);
    expect(texts(left, ".sub")).toEqual(["지금까지 점검", "조치"]);
    expect(texts(left, ".dn2 > span")).toEqual(s.path.filter((p) => p.status !== "now").map((p) => `${p.title} · ${p.detail}`)); // 안전 단계부터(정답지 16쪽)
    expect(texts(left, ".dn2 > span")[0]).toBe("잠금 확인 · 1.2절");
    expect(btn(left, "조치 잠김 · 파트장 승인 뒤").className).toContain("dis");
    expect(left.querySelector(".xkr")).toBeNull(); // 노하우 등록은 예외 기록 안내에만
  });

  it("예외 안내 출처가 앱 원본에 없으면 기록 번호만 · 다음 없으면 그 줄 없음 · 보통 호출엔 등록 버튼 없음", () => {
    const s = exceptionCall();
    s.call!.exception_guide = { ...s.call!.exception_guide!, source: null, next: null };
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={() => {}} />,
    );
    expect(container.querySelector(".xkh .xsrc")?.textContent).toBe("출처 · 예외상황-필터 덮개 고정 나사 확인");
    expect(texts(container, ".sub")).toEqual(["점검방법", "지금까지 점검"]);
    cleanup();
    const plain = render(
      <CallScreen s={load("s03-call-summary")} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={() => {}} />,
    ).container;
    expect(plain.querySelector(".xkr")).toBeNull();
  });

  it("첫 판정 호출처럼 한 점검이 없으면 '점검 전'", () => {
    const s = load("s03-call-summary");
    s.call!.checks_done = [];
    const { container } = render(
      <CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={() => {}} />,
    );
    expect(texts(container, ".dn2 span")).toEqual(["점검 전"]);
  });

  it("waiting · sent · 답 3종 배너와 다음 행동(AC-30-C2 · AC-23 · AC-08-C2)", () => {
    const view = (name: string, approvedSeen = false) => {
      cleanup();
      const onReport = vi.fn();
      const onSeen = vi.fn();
      const r = render(
        <CallScreen s={load(name)} busy={false} approvedSeen={approvedSeen} onApprovedSeen={onSeen} onReport={onReport} send={() => {}} />,
      );
      return { c: r.container, onReport, onSeen };
    };
    let v = view("s03-call-waiting");
    expect(v.c.querySelector(".banner.warn b")?.textContent).toMatch(/^호출하지 않고 대기 중 · \d\d:\d\d$/);
    expect(texts(v.c, ".cd:nth-child(2) > .btn")).toEqual(["파트장 호출"]);
    v = view("s03-call-sent");
    // 답장 u4-audit-fix: '보낸 요약'(읽기만 · 연필 0) → 아래 '파트장 호출 보냄 · 시각 / 답 기다림' · 위 띠 · 설명 문장 0
    expect(v.c.querySelector(".banner")).toBeNull();
    expect(v.c.querySelector(".cd:nth-child(2) > .pv")?.className).toBe("pv ro");
    expect(v.c.querySelector(".cd:nth-child(2) > .pv")?.previousElementSibling?.className).toBe("sml"); // 이름표는 칸 밖 위(덧 4)
    expect(v.c.querySelector(".sml")?.textContent).toBe("보낸 요약");
    expect(v.c.querySelector(".pv.ro .sm")).toBeNull();
    expect(v.c.querySelector(".cd:nth-child(2) > .pv")?.nextElementSibling?.className).toBe("wait");
    expect(texts(v.c, ".wait > *")[1]).toBe("답 기다림");
    expect(v.c.querySelector(".wait b")?.textContent).toMatch(/^파트장 호출 보냄 · \d\d:\d\d$/);
    expect(v.c.querySelector(".pv p")?.getAttribute("role")).toBeNull(); // 보낸 뒤에는 고치지 않는다
    expect(v.c.querySelectorAll(".cd:nth-child(2) > .btn").length).toBe(0);
    v = view("s03-ans-approved");
    const s = load("s03-ans-approved");
    // 답장 001-zzx 26쪽 정함: 흐름 그대로(그 C 점검 지시) · 띠 = '파트장 답 · 승인 후 진행 · 파트장 · 시각' · 설명 문장 뺌
    expect(v.c.querySelector(".banner.ok b")?.textContent).toMatch(/^파트장 답 · 승인 후 진행 · 박 파트장 \d\d:\d\d$/);
    expect(v.c.querySelector(".banner.ok div")?.textContent).toBe(v.c.querySelector(".banner.ok b")?.textContent);
    expect(v.c.querySelector(".ins-h .sm")?.textContent).toBe(`점검 ${s.instruction!.check_no} · 파트장 승인`);
    expect(texts(v.c, ".ins-h .tags > *")).toEqual(["C승인 필요", "승인됨", "매뉴얼 4.4절"]);
    expect(texts(v.c, ".proc li").length).toBe(s.instruction!.steps.length);
    fireEvent.click(btn(v.c, "점검 시작"));
    expect(v.onSeen).toHaveBeenCalled();
    v = view("s03-ans-daytime");
    expect(v.c.querySelector(".banner.warn b")?.textContent).toBe("주간 처리로 결정 · 저부하 감시 운전");
    expect(v.c.querySelector(".banner.warn div")?.textContent).toContain('"냉각 팬 분해 점검(4.4, C)"이 보고서 "미확인 · 요청"에 기록됩니다.');
    fireEvent.click(btn(v.c, "보고서 작성"));
    expect(v.onReport).toHaveBeenCalled();
    v = view("s03-ans-direct");
    expect(v.c.querySelector(".banner.info b")?.textContent).toBe("파트장 직접 확인 · 처리 결과"); // 결정 018 ③
    fireEvent.click(btn(v.c, "보고서 작성"));
    expect(v.onReport).toHaveBeenCalled();
  });
});

describe("T11 숙련자 인계 · 감시 마감", () => {
  it("unable_repeat: '확인 불가가 반복됨' · 머리 · 인계 요약 · 숙련자 인계 → call_send, 점검 경로(AC-19-C3)", () => {
    const s = load("s03-handover-unable");
    const send = vi.fn();
    const { container } = render(<HandoverScreen s={s} busy={false} send={send} />);
    expect(container.querySelector(".warn-h .sm")?.textContent).toBe("확인 불가가 반복됨");
    expect(container.querySelector(".warn-h h3")?.textContent).toBe(s.call!.head);
    // D-530 답장 001-zzt 7(가이드 28쪽): 괄호 이름표 · 안내 문장 뺌, 왼쪽 = 머리 + 지금까지 점검 · 조치(점검 경로), 오른쪽 = 요약 + 보내기
    expect(container.querySelector(".pv .sm")?.textContent).toBe("숙련자 인계 요약");
    expect(container.querySelector(".pv p")?.textContent).toContain("확인 불가 2회");
    expect(container.querySelector(".note")).toBeNull();
    // 답장 001-zzx 바(가이드 28쪽): 왼쪽 = 한 카드(빨강 머리 + '지금까지 점검 · 조치' 체크 목록 ✓ / !)
    const left = container.querySelector(".g64 > .cd:first-child")!;
    expect(left.querySelector(".warn-h.bad")).not.toBeNull();
    expect(left.querySelector(".sub")?.textContent).toBe("지금까지 점검 · 조치");
    expect(left.querySelector(".tli")).toBeNull();
    expect(texts(left, ".cki .t")).toEqual(s.path!.map((p) => (p.detail ? `${p.title} · ${p.detail}` : p.title)));
    expect(texts(left, ".cki.bad .t")).toContain("흡기 필터 육안 점검 · 확인 불가 2회 · 제외");
    expect(container.querySelector(".g64 > .cd:last-child .pv")).not.toBeNull();
    // 지시 d530-p33(가이드 28쪽): 머리 사람 아이콘 · 버튼 아이콘은 이름 앞(사람)
    expect(left.querySelector(".warn-h > svg")?.getAttribute("class")).toContain("lucide-user");
    expect((btn(container, "숙련자 인계 보내기").firstChild as Element | null)?.getAttribute?.("class")).toContain("lucide-user");
    fireEvent.click(btn(container, "숙련자 인계 보내기"));
    expect(send).toHaveBeenCalledWith({ type: "call_send" });
  });

  it("expert_handover(맞지 않는 소견) · 재가동 뒤 밖: '숙련자 인계' 머리(AC-20 · AC-21-C3)", () => {
    for (const [name, head] of [
      ["s03-handover-oos", "후보 원인과 맞지 않는 소견"],
      ["s03-handover-restart", "재가동 뒤에도 값이 경계 밖"],
    ]) {
      cleanup();
      const s = load(name);
      const { container } = render(<HandoverScreen s={s} busy={false} send={() => {}} />);
      expect(container.querySelector(".warn-h .sm")?.textContent).toBe("숙련자 인계");
      expect(container.querySelector(".warn-h h3")?.textContent).toBe(head);
      // 재가동 뒤 밖이면 머리 아래 값 줄(가이드 28쪽 '온도 차 9.0 K · 경계 8.2 K')
      const v = s.restart?.value;
      expect(container.querySelector(".warn-h .vline")?.textContent ?? null).toBe(
        name === "s03-handover-restart" ? `${v!.sensor} ${v!.value_text} · 경계 ${v!.boundary_text}` : null,
      );
    }
  });

  it("monitor_close: 이상 없음 · 감시 머리 · 6.2절 · '보고서 작성' → monitor_close(AC-22)", () => {
    const s = load("s03-monitor-close");
    const send = vi.fn();
    const { container } = render(<MonitorScreen s={s} busy={false} send={send} />);
    expect(container.querySelector(".done-h .sm")?.textContent).toBe("이상 없음 · 감시");
    expect(texts(container, ".act-h > *")).toEqual(["이상 없음 · 감시 마감", "A단독 가능", "6.2절"]);
    fireEvent.click(btn(container, "보고서 작성"));
    expect(send).toHaveBeenCalledWith({ type: "monitor_close" });
    expect(container.querySelectorAll(".tli.now").length).toBe(0);
  });
});

describe("T09 보고서", () => {
  const LABELS = ["설비 · 알람", "센서 근거", "점검 경로", "확정 원인", "근거 사례", "조치", "정지 시간", "미확인 · 요청"];

  it("draft: 8항목(출처 칩 · 고치기), 확정 전 확인 3항목 통과, 「보고서 확정」 → 확인 창 → 「확정」(R10 · AC-07-C2 · D-528)", () => {
    const s = load("s03-report-draft");
    const send = vi.fn();
    const { container } = render(<ReportScreen report={s.report!} busy={false} send={send} onHome={() => {}} />);
    expect(texts(container, ".cd-h b, .cd-h .ch")).toEqual(["정비 보고서 초안", "초안", "확정 전 확인"]); // 예외 없던 세션 = 예외 칸 0(고침A 19쪽)
    expect(texts(container, ".rk2")).toEqual(LABELS);
    expect(texts(container, ".rf .ch.src")).toEqual(["자동", "센서", "입력", "입력", "기록", "매뉴얼", "자동", "입력"]);
    expect(container.querySelectorAll(".rf .ed svg").length).toBe(8);
    expect(texts(container, ".ckl .ck.on")).toEqual(["근거 없는 문장 없음", "권한 등급 위반 없음", "필수 8항목 모두 채워짐"]);
    expect(container.querySelector(".cta2 .note")).toBeNull(); // 버튼 아래 안내 문장 뺌
    expect(texts(container, ".g73 > div:last-child > .cd:not(.kh) .btn")).toEqual(["보고서 확정"]); // 답장 001-zzx 다(가이드 17쪽): 확정 전 확인 카드 안(예외 칸 「AI 나눔」은 따로)
    fireEvent.click(btn(container, "보고서 확정"));
    expect(send).not.toHaveBeenCalled(); // 바로 확정하지 않고 확인 창(가이드 정비자 12)
    const dlg = container.querySelector('.modal[aria-label="보고서 확정"]')!;
    expect(dlg.querySelector(".mh b")?.textContent).toBe("보고서를 확정합니까?");
    expect(texts(dlg, ".mb .btn")).toEqual(["취소", "확정"]);
    fireEvent.click(btn(dlg, "취소")); // 취소는 창만 닫음
    expect(container.querySelector(".modal")).toBeNull();
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(btn(container, "보고서 확정"));
    fireEvent.click(btn(container.querySelector(".modal")!, "확정"));
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith({ type: "report_confirm" });
    expect(container.querySelector(".modal")).toBeNull();
  });

  // 정답지 18 · 19쪽: 예외 진행 세션만 보고서 전 팝업(예외 상황 기록) → 「보고서에 적용」 → 보고서 초안 안 '예외 상황' 구역
  const AUTO = { situation: "M-01 · 온도 차 저하 · 14:50 ~ 15:20 · 온도 차 7.8 K(경계 8.2 K 미만)", result: "재가동 정상(경계 이내)" };
  const EXT = "점검 덮개 나사. 점검 덕트 이음매. 조치 나사 새로 끼움. 조치 밴드 조임";
  const SIM = [
    { kind: "예외", no: "EX-R-0001", name: "필터 덮개 나사", score: 0.7 },
    { kind: "매뉴얼", no: "4.3", name: "흡기 필터 육안 점검", score: 0.3 },
  ];
  // 약속 v10: 예외 하나 = 주제 + 점검 목록 + 조치 목록(짝 0)
  const EX1 = {
    topic: "필터 덮개 나사",
    checks: [
      { what: "덮개 나사", how: "덮개 나사", criteria: "정상 범위", minutes: null },
      { what: "덕트 이음매", how: "", criteria: "정상 범위", minutes: null },
    ],
    actions: [
      { what: "나사 새로 끼움", parts: ["M6 나사"], minutes: null },
      { what: "밴드 조임", parts: [], minutes: null },
    ],
    matches: SIM,
  };
  const SAME = { decision: "same", match: { kind: "예외", no: "EX-R-0001" } };
  const exReport = (over: Record<string, unknown> = {}) => ({ ...load("s03-report-draft").report!, exception_auto: AUTO, exception_session: true, ...over });
  const popup = (root: ParentNode) => root.querySelector('.modal[aria-label="예외 상황 기록"]');

  it("예외 진행 세션(정답지 18쪽): 보고서 전 팝업 자동 · 상황 · 글(자리글 = 안내 문구) · 「AI 나눔」 · 예외 하나(이름 · 점검 n · 조치 n) · 비슷한 것(통째)마다 같음 / 아님 한 번 · 고르면 「보고서에 적용」 · 처음엔 「취소」 0 · 설명 문장 0", () => {
    const send = vi.fn(() => Promise.resolve(true));
    const r = render(<ReportScreen report={exReport()} busy={false} send={send} onHome={() => {}} />);
    const dlg = popup(r.container)!;
    expect(dlg.querySelector(".mh b")?.textContent).toBe("예외 상황 기록");
    expect(dlg.querySelector('[data-part="ex-situation"]')?.textContent).toBe(AUTO.situation);
    expect(dlg.querySelector('[data-part="ex-result"]')?.textContent).toBe(AUTO.result);
    expect(dlg.querySelector("p, .note")).toBeNull();
    expect(texts(dlg, ".mb .btn")).toEqual(["보고서에 적용"]); // 처음엔 닫기 0 — 적용해야 보고서로
    const ta = dlg.querySelector('textarea[aria-label="예외 글"]') as HTMLTextAreaElement;
    expect([ta.value, ta.placeholder]).toEqual(["", "점검한 것과 조치한 것을 모두 적어 주세요"]);
    expect(btn(dlg, "AI 나눔").className).toContain("dis"); // 빈 글이면 AI 나눔 · 적용 잠김
    expect(btn(dlg, "보고서에 적용").className).toContain("dis");
    fireEvent.change(ta, { target: { value: ` ${EXT} ` } });
    fireEvent.click(btn(dlg, "AI 나눔"));
    expect(send).toHaveBeenLastCalledWith({ type: "exception_split", exception_text: EXT });
    r.rerender(<ReportScreen report={exReport({ exception_split: { text: EXT, exception: EX1, error: null, similar_error: null } })} busy={false} send={send} onHome={() => {}} />);
    const ones = [...popup(r.container)!.querySelectorAll('[data-part="ex-items"] .exi')];
    expect(ones.length).toBe(1); // 예외를 둘로 나누지 않음
    const one = ones[0];
    expect(one.querySelector(".exn0")?.textContent).toBe("예외상황-필터 덮개 나사");
    expect([...one.querySelectorAll(".exl")].map((x) => x.textContent)).toEqual([
      "점검 1덮개 나사 · 기준 정상 범위",
      "점검 2덕트 이음매 · 기준 정상 범위",
      "조치 1나사 새로 끼움 · 부품 M6 나사",
      "조치 2밴드 조임",
    ]); // 적은 순서 그대로 · 점검 다음 조치
    expect([...one.querySelectorAll(".exl")].map((x) => x.getAttribute("data-step"))).toEqual(["c1", "c2", "a1", "a2"]);
    // D-642 C: AI 나눔이 지시 문장(무엇을 · 어떻게 · 판정 기준)을 함께 주면 '보고서에 적용' 전 줄에 그 문장 그대로
    const say = "덮개 나사 네 개를 손으로 돌려 보고, 헐거우면 이상으로 적습니다.";
    const EXS = { ...EX1, checks: [{ ...EX1.checks[0], instruction: say }, EX1.checks[1]] };
    r.rerender(<ReportScreen report={exReport({ exception_split: { text: EXT, exception: EXS, error: null, similar_error: null } })} busy={false} send={send} onHome={() => {}} />);
    expect(texts(popup(r.container)!, '[data-part="ex-items"] .exi .exl').slice(0, 2)).toEqual([`점검 1${say}`, "점검 2덕트 이음매 · 기준 정상 범위"]);
    r.rerender(<ReportScreen report={exReport({ exception_split: { text: EXT, exception: EX1, error: null, similar_error: null } })} busy={false} send={send} onHome={() => {}} />);
    expect(one.querySelector(".exh")?.textContent).toBe("비슷한 것 · 예외 통째로 견줌");
    expect([...one.querySelectorAll(".exr")].map((x) => texts(x, ".ch, .exn, .btn"))).toEqual([
      ["예외", "예외상황-필터 덮개 나사", "같음"], // 지금 목록이 안 온 예외 줄 = 고침/보탬 0(30쪽은 따로 시험)
      ["매뉴얼", "4.3 흡기 필터 육안 점검", "같음"],
    ]);
    expect(btn(popup(r.container)!, "보고서에 적용").className).toContain("dis"); // 고르기 전 잠김
    fireEvent.click(btn(one, "아님"));
    fireEvent.click(btn(one.querySelector(".exr")!, "같음")); // 같은 창에서 다시 고름 — 마지막이 이김
    fireEvent.click(btn(popup(r.container)!, "보고서에 적용"));
    expect(send).toHaveBeenLastCalledWith({ type: "exception_apply", exception_record: { text: EXT }, exception_review: SAME });
    fireEvent.click(btn(one, "아님"));
    fireEvent.click(btn(popup(r.container)!, "보고서에 적용"));
    expect(send).toHaveBeenLastCalledWith({
      type: "exception_apply",
      exception_record: { text: EXT },
      exception_review: { decision: "not", match: null },
    });
  });

  // 정답지 30쪽 · same-edit.md: 비슷한 것 예외 줄에 대상 예외 지금 목록(Pack 선택 칸) → 같음 + 고침/보탬 = 고친 뒤 목록 전체
  const TGT = {
    checks: [{ what: "필터 덮개 고정 나사", how: "나사 네 개를 손으로 돌려 본다", criteria: "네 개 모두 체결 = 정상", minutes: null }],
    actions: [{ what: "나사를 새로 끼움", parts: [], minutes: null }],
  };
  const EXT1 = { ...EX1, matches: [{ ...SIM[0], ...TGT }, SIM[1]] };
  const HOW = "덮개 고정 나사 네 개가 다 있는지 보고, 풀린 나사가 있으면 손으로 돌려 본다";
  const ADD = "빠진 나사는 새로 끼우고 남은 나사도 다시 조인다";
  const EDIT = { checks: [{ ...TGT.checks[0], how: HOW }], actions: [...TGT.actions, { what: ADD, parts: [], minutes: null }] };
  it("같음 + 고침(정답지 30쪽): 지금 목록이 온 예외 줄만 「고침/보탬」 → 점검마다 방법 칸(미리 채움) + 보탤 조치 → 바뀐 것 0이면 잠김 → 적용 = 같음 + edit 목록 전체 · 「같음」이면 칸 닫힘", () => {
    const send = vi.fn(() => Promise.resolve(true));
    const r = render(<ReportScreen report={exReport({ exception_draft: { text: EXT }, exception_split: { text: EXT, exception: EXT1, error: null, similar_error: null } })} busy={false} send={send} onHome={() => {}} />);
    const dlg = popup(r.container)!;
    const rows = [...dlg.querySelectorAll(".exr")];
    expect(rows.map((x) => texts(x, ".btn"))).toEqual([["같음", "고침/보탬"], ["같음"]]); // 매뉴얼 절은 고침 0
    fireEvent.click(btn(rows[0], "고침/보탬"));
    expect([btn(rows[0], "같음").className.includes("on"), btn(rows[0], "고침/보탬").className.includes("ghost xed xon")]).toEqual([true, true]);
    const box = dlg.querySelector('[data-part="ex-edit"]')!;
    expect(texts(box, "b")).toEqual(["고칠 점검방법", "보탤 조치"]);
    const how = box.querySelector('input[aria-label="고칠 점검방법 1"]') as HTMLInputElement;
    expect(how.value).toBe(TGT.checks[0].how); // 대상 지금 방법 미리 채움
    expect(btn(dlg, "보고서에 적용").className).toContain("dis"); // 바뀐 것 0 = 잠김
    fireEvent.change(how, { target: { value: ` ${HOW} ` } });
    fireEvent.change(box.querySelector('input[aria-label="보탤 조치"]')!, { target: { value: ADD } });
    fireEvent.click(btn(dlg, "보고서에 적용"));
    expect(send).toHaveBeenLastCalledWith({ type: "exception_apply", exception_record: { text: EXT }, exception_review: { ...SAME, edit: EDIT } });
    fireEvent.click(btn(rows[0], "같음")); // 고침 없이 같음 = 1단계 그대로
    expect(dlg.querySelector('[data-part="ex-edit"]')).toBeNull();
    fireEvent.click(btn(dlg, "보고서에 적용"));
    expect(send).toHaveBeenLastCalledWith({ type: "exception_apply", exception_record: { text: EXT }, exception_review: SAME });
    cleanup();
    const bare = render(<ReportScreen report={exReport({ exception_draft: { text: EXT }, exception_split: { text: EXT, exception: EX1, error: null, similar_error: null } })} busy={false} send={send} onHome={() => {}} />).container;
    expect(texts(popup(bare)!.querySelector(".exr")!, ".btn")).toEqual(["같음"]); // 지금 목록 안 온 줄은 고침 0
  });

  it("같음 + 고침 적용 뒤(정답지 30쪽): 보고서 구역 칩 '같음 + 고침 · 이름' · 고침 전 / 고침 뒤 줄 · 수정 팝업은 앞 글 그대로", () => {
    const applied = { text: EXT, exception: { ...EXT1, edit: EDIT, decision: "same", match: SAME.match, kind: "missed" }, exception_review: { ...SAME, edit: EDIT }, split_error: null };
    const r = render(<ReportScreen report={exReport({ exception_applied: applied, exception_split: { text: EXT, exception: EXT1, error: null, similar_error: null } })} busy={false} send={vi.fn()} onHome={() => {}} />);
    const sec = r.container.querySelector('[data-part="ex-section"]')!;
    expect(texts(sec, ".rxr .ch")).toEqual(["같음 + 고침 · 예외상황-필터 덮개 나사"]);
    expect(texts(sec, ".rxr > span:first-child")).toEqual(["상황", "적은 글", "예외", "점검", "조치", "고침 전", "고침 뒤", "결과"]);
    expect(sec.querySelector('[data-item="edit-before"] > span:last-child')?.textContent).toBe(`점검 1 필터 덮개 고정 나사 · ${TGT.checks[0].how} / 조치 1 나사를 새로 끼움`);
    expect(sec.querySelector('[data-item="edit-after"] > span:last-child')?.textContent).toBe(`점검 1 필터 덮개 고정 나사 · ${HOW} / 조치 1 나사를 새로 끼움 · 2 ${ADD}`);
    fireEvent.click(btn(sec, "예외 상황 수정"));
    const dlg = popup(r.container)!;
    expect((dlg.querySelector('input[aria-label="고칠 점검방법 1"]') as HTMLInputElement).value).toBe(HOW);
    expect((dlg.querySelector('input[aria-label="보탤 조치"]') as HTMLInputElement).value).toBe(ADD);
    expect(btn(dlg.querySelector(".exr")!, "같음").className).toContain("on"); // 정답지 30쪽: 같음 채움 + 고침/보탬 주황 테두리
    expect(btn(dlg.querySelector(".exr")!, "고침/보탬").className).toContain("xon");
  });

  it("비슷한 것 0(정답지 18쪽): 고를 것 0 · 바로 새 예외 · 적용 가능(exception_review = 아님)", () => {
    const send = vi.fn(() => Promise.resolve(true));
    const r = render(<ReportScreen report={exReport({ exception_draft: { text: EXT }, exception_split: { text: EXT, exception: { ...EX1, matches: [] }, error: null, similar_error: null } })} busy={false} send={send} onHome={() => {}} />);
    const dlg = popup(r.container)!;
    expect(texts(dlg, ".exi .exs .ch")).toEqual(["새 예외 · 비슷한 것 없음"]);
    expect(dlg.querySelector(".exi .btn")).toBeNull();
    fireEvent.click(btn(dlg, "보고서에 적용"));
    expect(send).toHaveBeenLastCalledWith({
      type: "exception_apply",
      exception_record: { text: EXT },
      exception_review: { decision: "not", match: null },
    });
  });

  it("적용 뒤(정답지 19쪽): 보고서 초안 안 '예외 상황' 구역 · 예외 칩(같음 · 이름) · 점검 · 조치 목록 · 오른쪽 블록 0 · [예외 상황 수정] = 앞 내용 채운 팝업(「취소」 있음) · 확정은 적용한 것", () => {
    const send = vi.fn(() => Promise.resolve(true));
    const applied = { text: EXT, exception: { ...EX1, decision: "same", match: SAME.match, kind: "missed" }, exception_review: SAME, split_error: null };
    const rep = exReport({ exception_applied: applied, exception_split: { text: EXT, exception: EX1, error: null, similar_error: null } });
    const r = render(<ReportScreen report={rep} busy={false} send={send} onHome={() => {}} />);
    expect(popup(r.container)).toBeNull(); // 적용한 것이 있으면 팝업 자동 0
    const sec = r.container.querySelector('.g73 > .cd:first-child [data-part="ex-section"]')!; // 가운데 보고서 초안 안
    expect(sec.querySelector(".rx-h b")?.textContent).toBe("예외 상황");
    expect(texts(sec, ".rxr > span:first-child")).toEqual(["상황", "적은 글", "예외", "점검", "조치", "결과"]);
    expect(texts(sec, ".rxr .ch")).toEqual(["같음 · 예외상황-필터 덮개 나사"]);
    expect(sec.querySelector('[data-item="exception"] > span:last-child')?.textContent).toBe("예외상황-필터 덮개 나사 같음 · 예외상황-필터 덮개 나사");
    expect(sec.querySelector('[data-item="checks"] > span:last-child')?.textContent).toBe("1 덮개 나사 · 기준 정상 범위 · 2 덕트 이음매 · 기준 정상 범위");
    expect(sec.querySelector('[data-item="actions"] > span:last-child')?.textContent).toBe("1 나사 새로 끼움 · 부품 M6 나사 · 2 밴드 조임");
    expect(r.container.querySelectorAll(".g73 > div:last-child .cd-h b").length).toBe(1); // 오른쪽 = 확정 전 확인만
    expect(btn(r.container, "보고서 확정").className).not.toContain("dis");
    fireEvent.click(btn(sec, "예외 상황 수정"));
    const dlg = popup(r.container)!;
    expect((dlg.querySelector('textarea[aria-label="예외 글"]') as HTMLTextAreaElement).value).toBe(EXT); // 앞 내용 그대로
    expect(btn(dlg.querySelector(".exi .exr")!, "같음").className).not.toContain("ghost"); // 앞 고름 그대로
    expect(texts(dlg, ".mb .btn")).toEqual(["취소", "보고서에 적용"]);
    fireEvent.click(btn(dlg, "취소"));
    expect(popup(r.container)).toBeNull();
    fireEvent.click(btn(r.container, "보고서 확정"));
    fireEvent.click(btn(r.container.querySelector('.modal[aria-label="보고서 확정"]')!, "확정"));
    expect(send).toHaveBeenLastCalledWith({ type: "report_confirm" }); // 서버가 적용한 것을 함께 확정
  });

  it("예외 진행 세션은 적용 전 확정 잠김 · 예외 없던 세션은 팝업 · 예외 구역 0(정답지 18 · 19쪽)", () => {
    const r = render(<ReportScreen report={exReport()} busy={false} send={vi.fn()} onHome={() => {}} />);
    expect(btn(r.container, "보고서 확정").className).toContain("dis");
    cleanup();
    const plain = render(<ReportScreen report={{ ...load("s03-report-draft").report!, exception_auto: AUTO }} busy={false} send={vi.fn()} onHome={() => {}} />).container;
    expect(popup(plain)).toBeNull();
    expect(plain.querySelector('[data-part="ex-section"]')).toBeNull();
    expect(btn(plain, "보고서 확정").className).not.toContain("dis");
  });

  it.each([
    ["Run 실패(provider 오류 · 시간 넘김)", "pack_unavailable"],
    ["점검만 · 조치만(한쪽 0)", "not_split"],
    ["꼴 밖", "invalid_output"],
  ])("나누지 못함(약속 v10 2절 · %s): 팝업에 '나누지 못함' · '새 예외' · 고를 것 0 → 글 그대로 적용(exception_review null) · 다시 나눌 수 있음", (_, error) => {
    const send = vi.fn(() => Promise.resolve(true));
    const r = render(<ReportScreen report={exReport({ exception_draft: { text: EXT }, exception_split: { text: EXT, exception: null, error, similar_error: null } })} busy={false} send={send} onHome={() => {}} />);
    const dlg = popup(r.container)!;
    expect(texts(dlg, ".exa .ch")).toEqual(["나누지 못함"]);
    expect(texts(dlg, '[data-part="ex-items"] .ch')).toEqual(["새 예외"]);
    expect(btn(dlg, "AI 나눔").className).not.toContain("dis");
    fireEvent.click(btn(dlg, "보고서에 적용"));
    expect(send).toHaveBeenLastCalledWith({ type: "exception_apply", exception_record: { text: EXT }, exception_review: null });
  });

  it("확정 뒤 · 나누지 못한 기록 · 옛 기록: 보고서 안 구역 읽기만(수정 단추 0)", () => {
    const c = load("s03-report-confirmed");
    const CTX = {"equipment_id": "M-01", "symptom_group": {"code": "CL", "name": "온도 차 저하"}, "occurred_at": "2026-11-03T05:50:00+00:00", "sensor_values": {"alarm": {"sensor": "온도 차", "value": 7.8, "unit": "K", "boundary": 8.2, "direction": "경계 아래"}, "snapshot": {"temp_diff_k": 7.8}}, "previous_maintenance": null, "recent_maintenance": [], "ai_first_check": null, "confirmed_cause": "원인 미상", "report_id": 1};
    const REC = { ...AUTO, text: EXT, context: CTX, exception: null, exception_review: null, split_error: "not_split" };
    const done = render(<ReportScreen report={{ ...c.report!, exception_record: REC }} busy={false} send={() => {}} onHome={() => {}} />).container;
    const sec = done.querySelector('[data-part="ex-section"]')!;
    expect(texts(sec, ".rxr > span:first-child")).toEqual(["상황", "적은 글", "예외", "결과"]);
    expect(texts(sec, ".rx-h .ch")).toEqual(["확정 · 보기만"]); // 정답지 20쪽
    expect(texts(sec, ".rxs .ch")).toEqual(["나누지 못함 · 새 예외"]);
    expect(sec.querySelector(".btn")).toBeNull();
    cleanup();
    const OLD = { situation: "바닥에 절삭유가 고여 있었음", action: "나사를 새로 끼움", context: CTX }; // D-556 전 기록(두 글)
    const old = render(<ReportScreen report={{ ...c.report!, exception_record: OLD }} busy={false} send={() => {}} onHome={() => {}} />).container;
    expect(texts(old, '[data-part="ex-section"] .rxr > span')).toEqual(["어떤 상황이었나", OLD.situation, "무엇을 했나", OLD.action]);
  });

  it("반려 뒤(정답지 20쪽): 앞 확정 기록이 '예외 상황' 구역에 다시 · [예외 상황 수정] · 팝업 자동 0 · 확정 가능", () => {
    const applied = {
      text: EXT,
      exception: { ...EX1, matches: [], decision: "not", match: null, kind: "new_exception" },
      exception_review: { decision: "not", match: null },
      split_error: null,
    };
    const rej = { reason: "조치 글 다시", by: "lead.park", by_name: "박 파트장", at: "2026-11-05T03:40:00+00:00" };
    const r = render(<ReportScreen report={exReport({ rejection: rej, exception_applied: applied })} busy={false} send={vi.fn()} onHome={() => {}} />);
    expect(popup(r.container)).toBeNull();
    expect(r.container.querySelector('[data-part="rejection"]')).not.toBeNull();
    const sec = r.container.querySelector('[data-part="ex-section"]')!;
    expect(texts(sec, ".rx-h .btn")).toEqual(["예외 상황 수정"]);
    expect(texts(sec, ".rxr .ch")).toEqual(["새 예외 · 예외상황-필터 덮개 나사"]);
    expect(btn(r.container, "보고서 확정").className).not.toContain("dis");
  });

  it("「점검방법 등록」 초안(D-543)이 있으면 팝업 예외 글 칸에 미리 채움", () => {
    const r = render(<ReportScreen report={exReport({ exception_draft: { text: "덮개 고정 나사 · 새로 끼움" } })} busy={false} send={() => {}} onHome={() => {}} />);
    expect((popup(r.container)!.querySelector('textarea[aria-label="예외 글"]') as HTMLTextAreaElement).value).toBe("덮개 고정 나사 · 새로 끼움");
  });

  it("고치기: 연필 → 글 상자 → 벗어나면 report_edit(바뀐 경우만)", () => {
    const s = load("s03-report-draft");
    const send = vi.fn();
    const { container } = render(<ReportScreen report={s.report!} busy={false} send={send} onHome={() => {}} />);
    fireEvent.click(container.querySelector('[data-field="unresolved"] .ed')!);
    const ta = container.querySelector('[data-field="unresolved"] textarea') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "냉각 팬 소음 · 주간 확인 요청" } });
    expect(btn(container, "보고서 확정").className).toContain("dis"); // 고치는 중에는 확정하지 않는다
    fireEvent.blur(ta);
    expect(send).toHaveBeenCalledWith({ type: "report_edit", field: "unresolved", text: "냉각 팬 소음 · 주간 확인 요청" });
  });

  it("check_failed: 실패 항목 줄 · ✕ 확인 · 실패 배너 · 확정 잠김(AC-07-C1)", () => {
    const s = load("s03-report-failed");
    const send = vi.fn();
    const { container } = render(<ReportScreen report={s.report!} busy={false} send={send} onHome={() => {}} />);
    expect(container.querySelector(".rf.fail .rk2")?.textContent).toBe("조치");
    expect(texts(container, ".ckl .ck.no")).toEqual(["근거 없는 문장 1건"]);
    expect(container.querySelector(".banner.bad b")?.textContent).toBe("근거 없는 문장 1건");
    expect(container.querySelector(".banner.bad div")?.textContent).toContain('"교체 후 온도 차 9.4 K로 회복"');
    const confirm = btn(container, "보고서 확정");
    expect(confirm.className).toContain("dis");
    fireEvent.click(confirm);
    expect(send).not.toHaveBeenCalled();
    expect(container.querySelector(".modal")).toBeNull(); // 잠긴 「보고서 확정」은 창도 열지 않음
  });

  it("confirmed: 확정 · 승인 대기 칩, 고치기 없음, 확정 배너 · 홈으로(AC-33)", () => {
    const s = load("s03-report-confirmed");
    const onHome = vi.fn();
    const { container } = render(<ReportScreen report={s.report!} busy={false} send={() => {}} onHome={onHome} />);
    expect(container.querySelector(".cd-h .ch.green")?.textContent).toBe("확정 · 승인 대기");
    expect(container.querySelectorAll(".rf .ed svg").length).toBe(0);
    fireEvent.click(container.querySelector(".rf .ed")!);
    expect(container.querySelector(".rf textarea")).toBeNull();
    expect(container.querySelector(".banner.ok b")?.textContent).toMatch(/^보고서를 확정했습니다 · \d\d:\d\d$/);
    // 답장 001-zzx 다(가이드 19쪽): 확정 띠 · 「홈으로」는 확정 전 확인 카드 안
    const chk = container.querySelector(".g73 > div:last-child > .cd")!;
    expect(chk.querySelector(".cd-h b")?.textContent).toBe("확정 전 확인");
    expect(chk.querySelector(".banner.ok")).not.toBeNull();
    expect(texts(chk, ".btn")).toEqual(["홈으로"]);
    fireEvent.click(btn(container, "홈으로"));
    expect(onHome).toHaveBeenCalled();
  });

  it("low_evidence · monitor_report · 원인 미상: 근거 적음 칩 · 확정 원인 값(AC-29-C2 · AC-22 · AC-21-C3)", () => {
    let r = render(<ReportScreen report={load("s03-report-low-evidence").report!} busy={false} send={() => {}} onHome={() => {}} />);
    expect(r.container.querySelector('[data-field="evidence_records"] .rv2 .ch.amber')?.textContent).toBe("근거 적음");
    cleanup();
    r = render(<ReportScreen report={load("s03-monitor-report").report!} busy={false} send={() => {}} onHome={() => {}} />);
    expect(r.container.querySelector('[data-field="cause"] .rv2')?.textContent).toBe("이상 없음 · 감시");
    cleanup();
    r = render(<ReportScreen report={load("s03-report-unknown").report!} busy={false} send={() => {}} onHome={() => {}} />);
    expect(r.container.querySelector('[data-field="cause"] .rv2')?.textContent).toBe("원인 미상");
  });

  it("원문 대체된 원인 · 조치 칸에만 '원문으로 대체됨' 표지(EV-001 CH-21 · VC-46)", () => {
    const base = load("s03-report-draft").report!;
    const report = { ...base, fields: base.fields.map((f) => (f.key === "cause" ? { ...f, fallback: true } : f.key === "action" ? { ...f, fallback: false } : f)) };
    const { container } = render(<ReportScreen report={report} busy={false} send={() => {}} onHome={() => {}} />);
    expect(texts(container, '[data-field="cause"] .ch.fb')).toEqual(["원문으로 대체됨"]);
    expect(container.querySelectorAll(".ch.fb")).toHaveLength(1); // 조치(false) · 나머지 6칸(칸 없음)은 표지 없음
    expect(container.querySelector('[data-field="cause"] .rv2')?.textContent).toBe(`원문으로 대체됨 ${base.fields.find((f) => f.key === "cause")!.text}`);
  });
});

describe("진행 표시 · 점검 경로", () => {
  it("조치 · 재가동 · 감시 마감 = 4, 보고서 = 5, 호출 · 인계 = 3", () => {
    expect(stepNumber(load("s03-action-a"), "lock")).toBe(4);
    expect(stepNumber(load("s03-restart-value"), "lock")).toBe(4);
    expect(stepNumber(load("s03-monitor-close"), "lock")).toBe(4);
    expect(stepNumber(load("s03-report-draft"), "lock")).toBe(5);
    expect(stepNumber(load("s03-report-confirmed"), "lock")).toBe(5);
    expect(stepNumber(load("s03-call-summary"), "lock")).toBe(3);
    expect(stepNumber(load("s03-handover-unable"), "lock")).toBe(3);
    expect(stepNumber(load("s03-ans-daytime"), "lock", false)).toBe(3); // 답 화면에서 '보고서 작성' 전
  });

  it("정정으로 무효인 기록은 지우지 않고 회색 점 · '정정으로 무효'", () => {
    const path = [
      { at: "2026-10-13T17:20:00+00:00", title: "냉각수 수위 확인", detail: "정상", status: "void" },
      { at: null, title: "흡기 필터 교체", detail: "4.3.2절", status: "now" },
    ];
    const { container } = render(<PathCard path={path} />);
    expect(container.querySelector(".tli.void .ch.gray.vd")?.textContent).toBe("정정으로 무효");
  });
});

describe("T02 홈 완료 행 · 최근 보고서 · T12 답이 온 호출", () => {
  it("이 교대 확정 보고서: 초록 점 '보고서 확정 · 승인 대기' · 보기, 처리 완료 · 호출 수, 최근 보고서 칩(PBC-4 v8 해석 ③)", () => {
    const home = load<Home>("home-done");
    const onOpen = vi.fn();
    const { container } = render(<HomeScreen home={home} onOpen={onOpen} />);
    const rows = [...container.querySelectorAll(".al")];
    expect(rows.map((r) => r.querySelector(".dot")?.className)).toEqual(["dot red", "dot amber", "dot green"]);
    expect(rows[2].querySelector(".al-m span")?.textContent).toMatch(/^\d\d:\d\d · 보고서 확정 · 승인 대기$/);
    fireEvent.click(rows[2].querySelector(".btn")!);
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ status: "done", report_session_id: home.recent_reports[0].session_id }));
    expect(texts(container, ".kt b")).toEqual(["1", "1", "1"]);
    expect(texts(container, ".rl .rl-t")[0]).toBe("M-01 · 냉각 필터 막힘"); // D-642: 시각은 오른쪽 칸
    expect(texts(container, ".rl .rl-r .rl-w")[0]).toMatch(/^\d{2}:\d{2}$|^어제 |^\d{2}-\d{2} /);
    expect(texts(container, ".rl .ch")).toEqual(["승인 대기"]);
    expect(container.querySelector(".rl .ch")?.className).toBe("ch amber");
  });

  it("최근 보고서 시각: 오늘은 시:분, 어제는 '어제 시:분'", () => {
    const now = new Date("2026-10-13T18:00:00+09:00");
    expect(dayTime("2026-10-13T00:29:00+09:00", now)).toBe("00:29");
    expect(dayTime("2026-10-12T22:44:00+09:00", now)).toBe("어제 22:44");
  });

  it("종 목록: 진행 중 '· 파트장 답 도착', '파트장 답이 온 호출' 묶음 · 답 보기(AC-37-C1 · C2)", () => {
    const data = load<Notifications>("notifications-answered");
    const onItem = vi.fn();
    const { container } = render(<NotifyPanel data={data} onItem={onItem} />);
    expect(texts(container, ".grp")).toEqual(["진행 중인 점검", "파트장 답이 온 호출"]);
    expect(texts(container, ".nt-item .al-m span").filter((t) => / · 파트장 답 도착$/.test(t)).length).toBe(
      data.items.filter((i) => i.kind === "in_progress" && i.answer).length,
    );
    const last = [...container.querySelectorAll(".nt-item")].at(-1)!;
    expect(last.querySelector(".dot")?.className).toBe("dot green");
    expect(last.querySelector("b")?.textContent).toBe("M-04 · 냉각 팬 분해 점검(4.4, C)");
    expect(last.querySelector(".al-m span")?.textContent).toMatch(/^박 파트장 \d\d:\d\d · 승인 후 진행$/);
    fireEvent.click(last.querySelector(".btn")!);
    expect(onItem).toHaveBeenCalledWith(expect.objectContaining({ kind: "answered_call", session_id: data.items.at(-1)!.session_id }));
  });
});

describe("D-546 U6 공구 교체 주의", () => {
  // 꼴 02(architecture-diagram d546): 알람 아님 · 내 알람 카드 바로 위 주황 줄 · 설비마다 한 줄 · 「확인」(설명 문장 0)
  it("설비마다 한 줄(공구 아이콘 · 이름 · 설비 · 마모 · 주의 범위) · 「확인」 → onAckCaution(두 번째 누름도 그대로) · 없으면 줄 0", () => {
    const home = load<Home>("home-in-progress");
    home.cautions = [
      { caution_id: 41, equipment_id: "M-03", tool_wear_min: 214, at: "2026-11-04T01:10:00Z" },
      { caution_id: 40, equipment_id: "M-05", tool_wear_min: 203, at: "2026-11-04T01:02:00Z" },
    ];
    const onAck = vi.fn();
    const { container } = render(<HomeScreen home={home} onOpen={() => {}} onAckCaution={onAck} />);
    const col = container.querySelector(".g2 > div:first-child")!;
    const rows = [...col.querySelectorAll(":scope > .tool")];
    expect(rows).toHaveLength(2);
    expect(rows[1].nextElementSibling?.querySelector(".cd-h b")?.textContent).toBe("내 알람"); // 내 알람 카드 바로 위
    expect(rows[0].querySelector(".ic")?.getAttribute("class")).toContain("lucide-wrench");
    expect(texts(rows[0], ".tm > *")).toEqual(["공구 교체 주의", "M-03 · 공구 마모 214분 · 주의 범위 200 ~ 246분"]);
    expect(rows[0].querySelector(".tm em")?.textContent).toBe("214분");
    expect(texts(col, ".tool .btn")).toEqual(["확인", "확인"]);
    fireEvent.click(rows[0].querySelector(".btn")!);
    fireEvent.click(rows[0].querySelector(".btn")!);
    expect(onAck.mock.calls).toEqual([[41], [41]]);
    cleanup();
    const none = render(<HomeScreen home={{ ...home, cautions: [] }} onOpen={() => {}} />).container;
    expect(none.querySelector(".tool")).toBeNull();
  });
});

describe("D-619 사전 점검 후보", () => {
  const card = (over = {}) => ({
    pattern: "A",
    record_id: "H-P009",
    equipment_id: "M-04",
    section: "4.3.2",
    section_title: "흡기 필터 교체",
    days_ago: 2,
    within_days: 7,
    follow: "필터 덮개 고정 문제",
    look_at: "필터 덮개 고정",
    act_short: "흡기 필터 교체", // D-670: 화면용 짧은 말(패턴에 없으면 앞 조치 이름 · 뒤 사건 이름 그대로)
    follow_short: "필터 덮개 고정 문제",
    total: 5,
    hits: 3,
    clean: 2,
    basis: [
      { record_id: "H-P001", equipment_id: "M-05", at: "2026-08-09T01:00:00Z", result: "이어짐", follow_days: 5 },
      { record_id: "H-P005", equipment_id: "M-07", at: "2026-09-18T01:00:00Z", result: "이상 없음", follow_days: null },
    ],
    mark: null,
    assignee: "tech.jung",
    assignee_name: "정비자 정",
    ...over,
  });

  // D-663 S4(재시연 5): 카드 겉은 핵심만('M-04 · 냉각 설정값 미리 확인' + '5건 중 3건') · 눌러야 자세히(앞 조치 · 날짜 · 사례 · 고장 판단 아님 · 살펴봄 · 이번엔 제외)
  it("D-663 S4: 겉 = '설비 · 살펴볼 것 미리 확인' + 'n건 중 k건'만 · 누르면 자세히(앞 조치 · 사례 · 고장 판단 아님) · 다시 누르면 접힘 · 없으면 칸 0", () => {
    const home = load<Home>("home-in-progress");
    home.prechecks = [card()];
    const { container } = render(<HomeScreen home={home} onOpen={() => {}} />);
    const col = container.querySelector(".g2 > div:first-child")!;
    const pc = col.querySelector(".pck")!;
    expect(col.querySelector(".pcl")?.previousElementSibling?.querySelector(".cd-h b")?.textContent).toBe("내 알람");
    expect(pc.getAttribute("data-precheck")).toBe("A");
    const face = pc.querySelector<HTMLElement>(".pck-s")!;
    expect(face.querySelector(".pck-t")?.textContent).toBe("M-04 · 필터 덮개 고정 미리 확인");
    expect(face.querySelector(".pck-c")?.textContent).toBe("5건 중 3건");
    expect(face.getAttribute("aria-expanded")).toBe("false");
    expect(pc.querySelector(".pck-x")).toBeNull(); // 접힘: 긴 글 · 버튼 0
    expect(pc.textContent).not.toMatch(/고장 판단|이어졌고|살펴봄|이번엔 제외|일 전/);
    fireEvent.click(face);
    expect(face.getAttribute("aria-expanded")).toBe("true");
    const x = pc.querySelector(".pck-x")!;
    // D-670: 머리 '주의 필요' + 작게 '고장 판단 아님' · 한 줄 '2일 전 앞 조치(절) · 7일 안 뒤 사건 n건 중 k건' · 긴 문장 0
    expect(texts(x, ".pck-h > *")).toEqual(["주의 필요", "고장 판단 아님"]);
    expect(x.querySelector(".pck-d")?.textContent).toBe("2일 전 흡기 필터 교체(4.3.2) · 7일 안 필터 덮개 고정 문제 5건 중 3건");
    expect(x.querySelector(".pck-o")?.textContent).toBe("내 담당 설비 · 정비자 정"); // D-642 E ③ 담당 표시
    expect(x.querySelector(".pck-n")).toBeNull();
    expect(x.textContent).not.toMatch(/한번 살펴봐|같은 조건 사례|이어졌고|이상 없었습니다/);
    expect(x.querySelector(".pck-l")?.textContent).toBe("살펴볼 곳 · 필터 덮개 고정");
    expect(texts(x, ".pcb")).toEqual(["M-05 · 08-09 · 5일 뒤 이어짐", "M-07 · 09-18 · 이상 없음"]); // 사례는 펼친 칸 안(창 0)
    expect(container.querySelector(".modal")).toBeNull();
    expect(container.textContent).not.toContain("근거 보기");
    expect(container.textContent).not.toContain("연쇄");
    expect(container.textContent).not.toContain("예시");
    fireEvent.click(face);
    expect(pc.querySelector(".pck-x")).toBeNull();
    cleanup();
    const none = render(<HomeScreen home={{ ...home, prechecks: [] }} onOpen={() => {}} />).container;
    expect(none.querySelector(".pck")).toBeNull();
  });

  it("[살펴봄] 바로 기록 · [이번엔 제외]는 이유를 적어야 저장 · 기록 뒤엔 버튼 대신 한 줄(펼친 칸) · 겉엔 짧은 표시", () => {
    const home = load<Home>("home-in-progress");
    home.prechecks = [card()];
    const onMark = vi.fn();
    const { container } = render(<HomeScreen home={home} onOpen={() => {}} onPrecheck={onMark} />);
    fireEvent.click(container.querySelector(".pck-s")!);
    const buttons = () => [...container.querySelectorAll(".pck-b .btn")];
    expect(buttons().map((b) => b.textContent)).toEqual(["살펴봄", "이번엔 제외"]);
    fireEvent.click(buttons()[0]);
    expect(onMark.mock.calls).toEqual([["A", "H-P009", "look", undefined]]);
    fireEvent.click(buttons()[1]);
    const dlg = container.querySelector('.modal[aria-label="이번엔 제외"]')!;
    const save = () => [...dlg.querySelectorAll(".btn")].find((b) => b.textContent === "저장")!;
    expect(save().className).toContain("dis");
    fireEvent.click(save());
    expect(onMark).toHaveBeenCalledTimes(1);
    fireEvent.change(dlg.querySelector("textarea")!, { target: { value: "이번 주 교체 예정" } });
    fireEvent.click(save());
    expect(onMark.mock.calls[1]).toEqual(["A", "H-P009", "skip", "이번 주 교체 예정"]);
    cleanup();
    home.prechecks = [card({ mark: { choice: "skip", by: "tech.jung", at: "2026-11-07T01:10:00Z", reason: "이번 주 교체 예정" } })];
    const done = render(<HomeScreen home={home} onOpen={() => {}} />).container;
    expect(done.querySelector(".pck-s .pck-ms")?.textContent).toBe("이번엔 제외");
    fireEvent.click(done.querySelector(".pck-s")!);
    expect(done.querySelector(".pck-b .btn")).toBeNull();
    expect(done.querySelector(".pck-m")?.textContent).toBe("이번엔 제외 · 10:10 · 이번 주 교체 예정");
  });
});

describe("세션 화면 넘김(기기 표시)", () => {
  const mount = (view: SessionView) => {
    const ok = (data: unknown) => Promise.resolve({ data, error: undefined, response: { status: 200 } });
    const api = { GET: vi.fn(() => ok(view)), POST: vi.fn(() => ok(view)) } as unknown as Api;
    return render(
      <Session
        api={api}
        sid={view.session_id}
        initial={null}
        onSession={() => {}}
        onExpired={() => {}}
        onRecord={() => {}}
        onManual={() => {}}
        onHome={() => {}}
      />,
    );
  };

  it("확정한 보고서: 단계 띠 '보고서'도 끝남(초록 ✓ · 다섯 모두 done) — 답장 001-zzx 라(가이드 19쪽), 초안은 지금(on)", async () => {
    const done = mount(load("s03-report-confirmed"));
    await waitFor(() => expect(done.container.querySelector(".banner.ok")).not.toBeNull());
    expect([...done.container.querySelectorAll(".ap-step .sp")].map((e) => e.className.split(" ")[1])).toEqual(["done", "done", "done", "done", "done"]);
    expect(done.container.querySelectorAll(".ap-step .sp .spn svg").length).toBe(5);
    cleanup();
    const draft = mount(load("s03-report-draft"));
    await waitFor(() => expect(draft.container.querySelector(".cta2")).not.toBeNull());
    expect(draft.container.querySelector(".sp.on")?.textContent).toBe("5보고서");
  });

  it("승인 답: 승인 화면 → '점검 시작' → 그 C 점검 지시 · 결과 입력(T10 / ans_approved → T06)", async () => {
    const { container } = mount(load("s03-ans-approved"));
    await waitFor(() => expect(container.querySelector(".banner.ok")).not.toBeNull());
    await act(async () => fireEvent.click(btn(container, "점검 시작")));
    expect(container.querySelector(".rbtns")).not.toBeNull();
    expect(container.querySelector(".ins-h h3")?.textContent).toBe("냉각 팬 분해 점검");
  });

  it("주간 처리 답: 답 화면 → '보고서 작성' → 보고서(T10 / ans_daytime → T09)", async () => {
    const { container } = mount(load("s03-ans-daytime"));
    await waitFor(() => expect(container.querySelector(".banner.warn")).not.toBeNull());
    expect(container.querySelector(".sp.on")?.textContent).toBe("3점검");
    await act(async () => fireEvent.click(btn(container, "보고서 작성")));
    expect(container.querySelector(".rf")).not.toBeNull();
    expect(container.querySelector(".sp.on")?.textContent).toBe("5보고서");
    expect(container.querySelector('[data-field="unresolved"] .rv2')?.textContent).toContain("주간 처리 요청");
  });
});

describe("S-04 정비자 쪽: 반려 · 숙련자 인계 답 · 직접 확인(결정 017 ① · 018 ③ ④)", () => {
  it("T02 / rejected: 최근 보고서 반려 칩 · 반려 사유 배너(파트장 · 시각) · '이어서 하기'(AC-11-C1)", () => {
    const home = load<Home>("home-rejected");
    const onResume = vi.fn();
    const { container } = render(<HomeScreen home={home} onOpen={() => {}} onResume={onResume} />);
    const rej = home.recent_reports.find((r) => r.status === "rejected")!;
    expect(container.querySelector(".rl .ch.red")?.textContent).toBe("반려");
    expect(container.querySelector(".banner.bad b")?.textContent).toMatch(new RegExp(`^반려 사유 · ${rej.rejected_by_name} \\d\\d:\\d\\d$`));
    expect(container.querySelector(".banner.bad div")?.textContent).toContain(rej.reject_reason!);
    fireEvent.click(container.querySelector(".banner.bad .btn.ghost.full")!); // 답장 001-zzx 다(가이드 33쪽): 사유 상자 안 · 알람 행의 '이어서 하기'와 다른 버튼
    expect(onResume).toHaveBeenCalledWith(rej.session_id);
    // 지시 d530-p33(가이드 33쪽): 반려된 보고서의 내 알람 카드 = 빨간 점 · '반려 · 보고서 고치기' · 「이어서 하기」 파란 주 버튼
    const card = container.querySelector(`.al[data-alarm="${home.alarms.find((a) => a.report_status === "rejected")!.alarm_id}"]`)!;
    expect(card.querySelector(".dot")?.className).toBe("dot red");
    expect(card.querySelector(".al-m > span")?.textContent).toMatch(/^\d\d:\d\d · 반려 · 보고서 고치기$/);
    expect(card.querySelector(".btn")?.className).toBe("btn ");
    const other = container.querySelector('.al[data-alarm="42"]')!; // 반려 아닌 진행 중은 그대로
    expect(other.querySelector(".al-m > span")?.textContent).toMatch(/점검 2 \/ 최대 3 진행 중$/);
  });

  it("T12: '반려된 보고서' 묶음 · '보고서 보기' → 그 세션(AC-37-C1 · C2)", () => {
    const data = load<Notifications>("notifications-rejected");
    const onItem = vi.fn();
    const { container } = render(<NotifyPanel data={data} onItem={onItem} />);
    expect(texts(container, ".grp")).toEqual(["진행 중인 점검", "파트장 답이 온 호출", "반려된 보고서"]);
    const last = [...container.querySelectorAll(".nt-item")].at(-1)!;
    expect(last.querySelector(".dot")?.className).toBe("dot red");
    expect(last.querySelector(".al-m span")?.textContent).toMatch(/· 필터 표면 상태를 메모로 다시 적어 주세요$/);
    fireEvent.click(last.querySelector(".btn")!);
    expect(onItem).toHaveBeenCalledWith(expect.objectContaining({ kind: "rejected_report" }));
  });

  it("숙련자 인계 보낸 뒤 · 답 온 뒤(결정 018 ④): 보냄 배너 · 답 대기 → 답 배너 · '보고서 작성'", () => {
    const sent = load("s04-handover-sent");
    let r = render(<HandoverScreen s={sent} busy={false} send={() => {}} />);
    expect(r.container.querySelector(".banner")).toBeNull(); // 호출과 같은 꼴(답장 u4-audit-fix)
    expect(r.container.querySelector(".sml")?.textContent).toBe("보낸 요약");
    expect(r.container.querySelector(".pv.ro .sm")).toBeNull();
    expect(texts(r.container, ".wait > *")).toEqual([expect.stringMatching(/^숙련자 인계 보냄 · \d\d:\d\d$/), "답 기다림"]);
    expect([...r.container.querySelectorAll(".btn")].length).toBe(0);
    cleanup();
    const onReport = vi.fn();
    r = render(<HandoverScreen s={load("s04-handover-answered")} busy={false} send={() => {}} onReport={onReport} />);
    expect(r.container.querySelector(".banner.ok b")?.textContent).toMatch(/^주간 숙련자 배정 · 박 파트장 \d\d:\d\d$/);
    fireEvent.click(btn(r.container, "보고서 작성"));
    expect(onReport).toHaveBeenCalled();
  });

  it("반려 뒤 다시 열린 보고서: 「반려 · 고치는 중」 · 오른쪽 위 반려 사유(파트장 · 시각) · 고칠 수 있음 · 확정 가능(AC-11-C2 · D-528 가이드 34쪽)", () => {
    const s = load("s04-report-reopened");
    const { container } = render(<ReportScreen report={s.report!} busy={false} send={() => {}} onHome={() => {}} />);
    expect(container.querySelector(".cd-h .ch.red")?.textContent).toBe("반려 · 고치는 중");
    const rej = container.querySelector('[data-part="rejection"]')!;
    expect(rej.querySelector("b")?.textContent).toMatch(/^반려 사유 · 박 파트장 \d\d:\d\d$/);
    expect(rej.textContent).toContain(s.report!.rejection!.reason!);
    expect(container.querySelectorAll(".rf .ed svg").length).toBe(8);
    expect(btn(container, "보고서 확정").className).not.toContain("dis");
  });
});
