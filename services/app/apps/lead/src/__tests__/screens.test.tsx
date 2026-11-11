// 파트장 앱 component 시험(D-528 최종안 · 가이드 파트장 1 ~ 5): 실제 API 응답(fixtures)으로 그려 가이드 구조 · 보내는 요청을 대조한다.
// final-report.json = 시험 DB 실제 응답 한 건(server/tests/test_final_report.py FINAL_FIXTURE_OUT, D-503 2).

import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api, Schemas } from "@c1a15/ui";
import { ApprovalList } from "../screens/Approvals";
import { AuditScreen } from "../screens/Audit";
import { CallDetail, CallList } from "../screens/Calls";
import { Frame, headDate } from "../screens/Frame";
import { GradesScreen } from "../screens/Grades";
import { Login } from "../screens/Login";
import { Review } from "../screens/Review";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
function load<T>(name: string): T {
  return structuredClone(fx[`./fixtures/${name}.json`]) as T;
}
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");
const btn = (root: ParentNode, label: string) =>
  [...root.querySelectorAll("button.lbtn")].find((b) => b.textContent?.trim() === label) as HTMLButtonElement;
const ok = (data: unknown) => Promise.resolve({ data, error: undefined, response: { status: 200 } });
const fail = (status: number, detail: unknown) => Promise.resolve({ data: undefined, error: { detail }, response: { status } });
const noop = () => {};

afterEach(cleanup);

describe("파트장 1 로그인 · 틀", () => {
  it("ID · 비밀번호를 다 넣어야 열림 · 역할이 다르면 거부 줄 · ID 칸 오류(AC-12) · 설명 문장 없음", async () => {
    const api = { POST: vi.fn(() => fail(403, "role_mismatch")) } as unknown as Api;
    const { container } = render(<Login api={api} onDone={noop} />);
    expect(container.querySelector(".lg-card h2")?.textContent).toBe("파트장 로그인");
    expect(btn(container, "로그인").disabled).toBe(true);
    fireEvent.change(container.querySelector('[aria-label="ID"]')!, { target: { value: "tech.kim" } });
    expect(btn(container, "로그인").disabled).toBe(true);
    fireEvent.change(container.querySelector('[aria-label="비밀번호"]')!, { target: { value: "x" } });
    await act(async () => fireEvent.click(btn(container, "로그인")));
    expect(container.querySelector(".lerr b")?.textContent).toBe("파트장 계정만 로그인할 수 있습니다");
    expect(container.querySelector('[aria-label="ID"]')?.className).toBe("err");
    expect(container.textContent).not.toContain("사전 등록된");
  });

  it("왼쪽 메뉴 넷(사람 판단 없음) · 아이콘 0 · 개수는 회색 칸 숫자 · 현재 메뉴 · 메뉴 이동", () => {
    const onMenu = vi.fn();
    const { container } = render(
      <Frame menu="calls" me={{ display_name: "박 파트장" }} counts={{ approvals: 1, calls: 0 }} onMenu={onMenu}>
        <div />
      </Frame>,
    );
    const items = [...container.querySelectorAll(".lnav a")];
    expect(items.map((a) => a.childNodes[0].textContent)).toEqual(["승인 대기", "호출", "권한 등급", "감사 기록"]);
    expect(texts(container, ".lnav a .c")).toEqual(["1"]); // 0이면 칸 없음
    expect(container.querySelector(".lnav a.on")?.textContent).toBe("호출");
    expect(container.querySelectorAll(".lnav svg, .lnav img").length).toBe(0);
    expect(texts(container, ".who b")).toEqual(["박 파트장"]);
    fireEvent.click(items[2]);
    expect(onMenu).toHaveBeenCalledWith("grades");
  });

  it("머리 날짜: '10월 1일(목) 08:12 · 야간 22:00 ~ 06:00'(현장 시각)", () => {
    expect(headDate("2026-09-30T23:12:00Z")).toBe("10월 1일(목) 08:12 · 야간 22:00 ~ 06:00");
  });
});

describe("파트장 2 승인 대기", () => {
  const props = { onOpen: noop, onCall: noop, onAudit: noop, onCalls: noop };

  it("요약 셋 = 실제 건수(강조는 승인 대기 하나) · 확정된 보고서 표 · 근거 적음 · 위반 칸 · 「검토」(AC-27 · AC-29-C3)", () => {
    const o = load<Schemas["LeadOverview"]>("overview-list");
    const onOpen = vi.fn();
    const { container } = render(<ApprovalList data={o} audit={null} calls={null} {...props} onOpen={onOpen} />);
    expect(container.querySelector(".lbar h2")?.textContent).toBe("승인 대기");
    expect(texts(container, ".ltiles b")).toEqual([String(o.tiles.awaiting), String(o.tiles.calls_open), String(o.tiles.violations)]);
    expect(texts(container, ".ltiles span")).toEqual(["승인 대기", "처리할 호출", "권한 위반"]);
    expect(container.querySelectorAll(".ltiles b.acc").length).toBe(1);
    expect(texts(container, ".lsect-h")[0]).toBe(`확정된 보고서${o.rows.length}건`);
    const rows = [...container.querySelectorAll("tr[data-report]")];
    expect(rows.length).toBe(o.rows.length);
    expect(texts(container, "tr[data-report] .tx.warn")).toEqual(["근거 적음"]);
    expect(texts(container, "tr[data-report] .tx.ok")).toEqual(o.rows.map(() => "위반 0"));
    fireEvent.click(rows[1].querySelector("button.lbtn")!);
    expect(onOpen).toHaveBeenLastCalledWith(o.rows[1].report_id);
    fireEvent.click(rows[2]); // 행을 눌러도 엶
    expect(onOpen).toHaveBeenLastCalledWith(o.rows[2].report_id);
  });

  it("D-642 E: '사전 점검 후보' 칸 — 설비 · 담당 · 앞 조치 · 사례 n건 중 k건 · 살펴볼 것 · 정비자 확인(없으면 빈 안내)", () => {
    const o = load<Schemas["LeadOverview"]>("overview-list");
    const empty = render(<ApprovalList data={{ ...o, prechecks: [] }} audit={null} calls={null} {...props} />);
    const sect0 = empty.container.querySelector('[data-part="prechecks"]')!;
    expect(sect0.querySelector(".lsect-h")?.textContent).toBe("사전 점검 후보0건고장 판단이 아닙니다 · 담당 정비자 홈에도 같은 카드");
    expect(sect0.querySelector(".lempty")?.textContent).toBe("사전 점검 후보가 없습니다");
    cleanup();
    const card = {
      pattern: "A",
      record_id: "H-P009",
      equipment_id: "M-04",
      section: "4.3.2",
      section_title: "흡기 필터 교체",
      days_ago: 2,
      within_days: 7,
      follow: "필터 덮개 고정 문제",
      look_at: "필터 덮개 고정",
      act_short: "흡기 필터 교체",
      follow_short: "필터 덮개 고정 문제",
      total: 5,
      hits: 3,
      clean: 2,
      basis: [],
      mark: null,
      assignee: "tech.jung",
      assignee_name: "정 정비",
    };
    const marked = { ...card, equipment_id: "M-12", mark: { choice: "look", by: "tech.jung", at: "2026-11-08T10:40:00Z", reason: null } };
    const { container } = render(<ApprovalList data={{ ...o, prechecks: [card, marked] }} audit={null} calls={null} {...props} />);
    const sect = container.querySelector('[data-part="prechecks"]')!;
    expect(sect.querySelector(".lsect-h")?.textContent).toBe("사전 점검 후보2건고장 판단이 아닙니다 · 담당 정비자 홈에도 같은 카드");
    // D-663 S4: 줄 겉은 핵심만(설비 · 담당 · '살펴볼 것 미리 확인 · n건 중 k건' · 정비자 확인) · 줄을 누르면 아래 자세히
    expect(texts(sect, "th")).toEqual(["설비", "담당", "사전 점검", "정비자 확인"]);
    const row = sect.querySelector<HTMLElement>("tr[data-precheck]")!;
    expect(texts(row, "td")).toEqual(["M-04", "정 정비", "필터 덮개 고정 미리 확인 · 5건 중 3건", "아직"]);
    expect(texts(sect, "tr[data-precheck] td:last-child")[1]).toBe("살펴봄 · 19:40");
    expect(sect.querySelector("tr.pck-x")).toBeNull();
    expect(sect.textContent).not.toMatch(/H-P|D-\d|2일 전|이어졌고/);
    fireEvent.click(row);
    const x = sect.querySelector("tr.pck-x")!;
    expect(x.previousElementSibling).toBe(row);
    expect(texts(x, ".pck-xl")).toEqual([
      "주의 필요 · 고장 판단 아님", // D-670
      "2일 전 흡기 필터 교체(4.3.2) · 7일 안 필터 덮개 고정 문제 5건 중 3건",
      "살펴볼 곳 · 필터 덮개 고정",
    ]);
    fireEvent.click(row);
    expect(sect.querySelector("tr.pck-x")).toBeNull();
  });

  it("D-642 R2 · R5: 확정된 보고서 한 줄 = 서버 줄 값(예외면 '예외상황 · 점검 → 조치', 아니면 원인 이름) — 원인 문장을 자르지 않음", () => {
    const o = load<Schemas["LeadOverview"]>("overview-list");
    o.rows[0] = { ...o.rows[0], cause: "원인 미상이며, 잠금 확인(1.2) 및 고온부 확인과 온도 기록(4.2), 흡기 필터 덮개 볼트(EX-R-14001) 이상 없음", line: "예외상황 · 흡기 필터 덮개 볼트 → 덮개 볼트 조임" };
    o.rows[1] = { ...o.rows[1], cause: "흡기 필터 표면의 기름때가 절반 이상 덮여 냉각 필터가 막힘(4.3).", line: "냉각 필터 막힘" };
    const { container } = render(<ApprovalList data={o} audit={null} calls={null} {...props} />);
    const cells = texts(container, "tr[data-report] td.cause");
    expect(cells.slice(0, 2)).toEqual(["예외상황 · 흡기 필터 덮개 볼트 → 덮개 볼트 조임", "냉각 필터 막힘"]);
    expect(cells.join(" ")).not.toMatch(/원인 미상이며|EX-R|H-\d/);
  });

  it("D-637 F10: 긴 확정 원인은 칸 안에서 짧게 한 줄(괄호 · 근거 번호 빼고 첫 마디) · 작성 · 확정 칸은 그대로", () => {
    const o = load<Schemas["LeadOverview"]>("overview-list");
    o.rows[0] = { ...o.rows[0], cause: "원인 미상, 잠금 확인(1.2) 및 고온부 확인과 온도 기록(4.2), 흡기 필터 덮개 볼트(EX-R-14001) 이상 없음, 근거 사례 H-0232, H-0197, H-0155 참고" };
    o.rows[1] = { ...o.rows[1], cause: "흡기 필터 표면의 기름때가 절반 이상 덮여 냉각 필터가 막힘(4.3, H-0197, H-0232, H-0155)." };
    const { container } = render(<ApprovalList data={o} audit={null} calls={null} {...props} />);
    const cells = [...container.querySelectorAll("tr[data-report] td.cause")];
    expect(cells.length).toBe(o.rows.length);
    expect(cells[0].textContent).toBe("원인 미상");
    expect(cells[1].textContent).toBe("흡기 필터 표면의 기름때가 절반 이상 덮여 냉각 필터가 막힘");
    for (const c of cells) {
      expect([...(c.textContent ?? "")].length).toBeLessThanOrEqual(40);
      expect(c.textContent).not.toMatch(/H-\d|EX-R|\(/);
    }
    const row0 = container.querySelector("tr[data-report]")!;
    expect(row0.querySelectorAll("td")[2].textContent).toBe(o.rows[0].author_name);
  });

  it("오늘 기록(최근 5줄 · 전체 건수 · 보고서 확정 행은 「보고서 보기」) · 처리할 호출(지금 API 그대로) · 전체 보기 링크", () => {
    const o = load<Schemas["LeadOverview"]>("overview-list");
    const a = load<Schemas["LeadAudit"]>("audit-default");
    a.rows[1] = { ...a.rows[1], action: "보고서 확정", report_id: 6 };
    const cl = load<Schemas["LeadCalls"]>("calls-list");
    const on = { onOpen: vi.fn(), onCall: vi.fn(), onAudit: vi.fn(), onCalls: vi.fn() };
    const { container } = render(<ApprovalList data={o} audit={a} calls={cl} {...on} />);
    const today = container.querySelector('[data-part="today"]')!;
    expect(today.querySelector(".lsect-h")?.textContent).toBe(`오늘 기록${a.rows.length}건`);
    expect(today.querySelectorAll("tr.lsm").length).toBe(5);
    expect(texts(today, "th")).toEqual(["시각", "앱", "사용자", "행동", "대상"]);
    expect(texts(today, "tr.lsm td:nth-child(2)")[0]).toBe("파트장 앱");
    fireEvent.click([...today.querySelectorAll("button.ltx")].find((b) => b.textContent === "보고서 보기")!);
    expect(on.onOpen).toHaveBeenCalledWith(6);
    fireEvent.click([...today.querySelectorAll("button.ltx")].find((b) => b.textContent === "감사 기록 전체 보기")!);
    expect(on.onAudit).toHaveBeenCalled();
    const calls = container.querySelector('[data-part="calls"]')!;
    expect(calls.querySelector(".lsect-h")?.textContent).toBe(`처리할 호출${cl.rows.length}건`);
    expect(texts(calls, "tr[data-call] td:nth-child(3)")).toEqual(["숙련자 인계", "파트장 호출"]);
    fireEvent.click(calls.querySelector("tr[data-call]")!);
    expect(on.onCall).toHaveBeenCalledWith(cl.rows[0].call_id);
    fireEvent.click(calls.querySelector("button.ltx")!);
    expect(on.onCalls).toHaveBeenCalled();
  });

  it("빈 상태 셋: 승인 대기 보고서가 없습니다 · 기록이 없습니다 · 처리할 호출이 없습니다(부록 3)", () => {
    const a = { ...load<Schemas["LeadAudit"]>("audit-default"), rows: [] };
    const { container } = render(<ApprovalList data={load("overview-empty")} audit={a} calls={load("calls-empty")} {...props} />);
    expect(texts(container, ".ltiles b")).toEqual(["0", "0", "0"]);
    expect(container.querySelector(".lempty")?.textContent).toBe("승인 대기 보고서가 없습니다");
    expect(texts(container, ".lnone")).toEqual(["기록이 없습니다", "처리할 호출이 없습니다"]);
  });

  it("호출 없이 조치함(약속 v9 lead_notice): 정비자가 [예외] 바로 조치를 고른 세션이 있으면 한 구역 · 없으면 구역 없음", () => {
    const o = load<Schemas["LeadOverview"]>("overview-list");
    const none = render(<ApprovalList data={o} audit={null} calls={null} {...props} />).container;
    expect(none.querySelector('[data-part="acted"]')).toBeNull();
    cleanup();
    const acted = [{ session_id: 7, equipment_id: "M-01", group_name: "온도 차 저하", tech_name: "김 정비", at: "2026-11-05T05:10:00+00:00" }];
    const { container } = render(<ApprovalList data={{ ...o, acted }} audit={null} calls={null} {...props} />);
    const sec = container.querySelector('[data-part="acted"]')!;
    expect(sec.querySelector(".lsect-h")?.textContent).toBe("호출 없이 조치함1건");
    expect(texts(sec, "th")).toEqual(["설비 · 알람", "정비자", "조치 시작", ""]);
    expect(texts(sec, "tr[data-acted] td")).toEqual(["M-01온도 차 저하", "김 정비", "14:10", "호출 없이 조치함"]);
    expect(sec.querySelector("button, .lbtn, p")).toBeNull(); // 보기만 · 설명 문장 0
  });
});

describe("파트장 2 보고서 검토 = 최종본 문서", () => {
  const report = () => load<Schemas["FinalReport"]>("final-report");

  it("머리 · 진행 넷 · 구역 1 ~ 5 · 꼬리 = 서버 값 그대로 · 「출력」 「반려」 「승인」 · 수정 후 승인 없음", () => {
    const f = report();
    const { container } = render(<Review api={{} as Api} report={f} onBack={noop} onDone={noop} />);
    expect(container.querySelector(".lcrumb")?.textContent).toBe(`승인 대기 / ${f.equipment_id} 보고서`);
    expect(texts(container, ".lbar button.lbtn")).toEqual(["출력", "반려", "승인"]);
    expect(container.querySelector(".fd-h h1")?.textContent).toBe(`정비 보고서 ${f.equipment_id}`);
    expect(texts(container, ".fd-st span")).toEqual([expect.stringMatching(/^잠금 확인 \d\d:\d\d$/), "승인 대기"]);
    expect(texts(container, ".fd-steps li").map((x) => x.replace(/\d\d:\d\d/, "hh:mm"))).toEqual([
      "1초안 자동 작성",
      "2정비자 확정 hh:mm",
      "3파트장 승인",
      "4이력 추가",
    ]);
    expect(container.querySelector(".fd-steps li.now")?.textContent).toBe("3파트장 승인");
    expect(texts(container, "h2")).toEqual(["1. 이상 내용 · 센서 근거", "2. 원인 분석", "3. 조치 경과", "4. 조치 결과", "5. 미확인 · 주간 요청"]);
    const sections = container.querySelectorAll(".fdoc section");
    expect(sections[0].querySelectorAll("tr.hot").length).toBe(f.sensors.filter((s) => s.out_of_range).length);
    expect(texts(sections[1], "tr td:first-child")).toEqual(f.causes.map((c) => c.name));
    expect(texts(sections[1], "tr.hot td:last-child")).toEqual(["확정 · 4.3 점검 이상"]);
    expect(sections[2].querySelectorAll("tr").length - 1).toBe(f.timeline.length);
    expect(texts(sections[2], ".fd-g").length).toBe(f.timeline.filter((x) => x.grade).length);
    expect(texts(sections[3], "tr")).toContain(`권한 사용A ${f.grade_use.A} · B ${f.grade_use.B} · C ${f.grade_use.C}`);
    expect(container.querySelector(".fd-open")?.textContent).toBe(f.unresolved);
    expect(texts(container, ".fd-f b")[1]).toBe("승인 대기");
    expect(container.textContent).not.toContain("수정 후 승인");
  });

  it("예외 상황 기록(D-538): 정비자 두 글 + 자동 정보가 있으면 '6. 예외 상황 기록' 구역(보기만) · 없으면 구역 없음", () => {
    const ctx = {"equipment_id": "M-01", "symptom_group": {"code": "CL", "name": "온도 차 저하"}, "occurred_at": "2026-11-03T05:50:00+00:00", "sensor_values": {"alarm": {"sensor": "온도 차", "value": 7.8, "unit": "K", "boundary": 8.2, "direction": "경계 아래"}, "snapshot": {"temp_diff_k": 7.8}}, "previous_maintenance": {"record_id": "H-0279", "occurred_at": "2026-10-28T01:10:00+09:00", "outcome": "냉각수 부족", "action": "냉각수 보충(4.5.2)"}, "recent_maintenance": [], "ai_first_check": {"section": "4.3", "title": "흡기 필터 육안 점검"}, "confirmed_cause": "냉각 필터 막힘", "report_id": 1};
    const xr = { situation: "바닥에 절삭유가 고여 있었음", action: "나사를 새로 끼우고 바닥을 닦음", context: ctx };
    const c = render(<Review api={{} as Api} report={{ ...report(), exception_record: xr }} onBack={noop} onDone={noop} />).container;
    expect(texts(c, "h2").at(-1)).toBe("6. 예외 상황 기록");
    expect(texts(c, ".fd-ex th")).toEqual(["상황", "조치", "설비 · 증상", "센서값", "직전 정비", "AI 첫 안내", "확정 원인"]);
    const td = texts(c, ".fd-ex td");
    expect(td.slice(0, 2)).toEqual([xr.situation, xr.action]);
    expect(td[2]).toMatch(/^M-01 · 온도 차 저하 · \d\d:\d\d$/);
    expect(td.slice(3)).toEqual(["온도 차 7.8 K · 경계 8.2 K 경계 아래", "H-0279 · 냉각수 부족 · 냉각수 보충(4.5.2)", "4.3 흡기 필터 육안 점검", "냉각 필터 막힘"]);
    expect(c.querySelector(".fd-ex textarea, .fd-ex input")).toBeNull(); // 보기만
    cleanup();
    const none = render(<Review api={{} as Api} report={{ ...report(), exception_record: null }} onBack={noop} onDone={noop} />).container;
    expect(texts(none, "h2")).not.toContain("6. 예외 상황 기록");
    cleanup();
    // 약속 v10 기록(정답지 21쪽): 상황(자동) · 적은 글 · 예외(이름 + 칩) · 점검 목록 · 조치 목록 · 결과(자동)
    const one = {
      topic: "필터 덮개 나사",
      checks: [
        { what: "덮개 나사", how: "", criteria: "정상 범위", minutes: null },
        { what: "덕트 이음매", how: "", criteria: "", minutes: null },
      ],
      actions: [
        { what: "나사 새로 끼움", parts: [], minutes: null },
        { what: "밴드 조임", parts: ["밴드"], minutes: null },
      ],
      matches: [{ kind: "예외", no: "EX-R-0001", name: "필터 덮개 나사", score: 0.7 }],
      decision: "same",
      match: { kind: "예외", no: "EX-R-0001" },
      kind: "missed",
    };
    const split = {
      ...xr,
      action: null,
      situation: "M-01 · 온도 차 저하 · 14:50 ~ 15:20 · 온도 차 7.8 K(경계 8.2 K 미만)",
      text: "점검 덮개 나사. 점검 덕트 이음매. 조치 나사 새로 끼움. 조치 밴드 조임",
      exception: one,
      exception_review: { decision: "same", match: one.match },
      result: "재가동 정상(경계 이내)",
    };
    const s2 = render(<Review api={{} as Api} report={{ ...report(), exception_record: split }} onBack={noop} onDone={noop} />).container;
    expect(texts(s2, ".fd-ex th").slice(0, 6)).toEqual(["상황", "적은 글", "예외", "점검", "조치", "결과"]);
    expect(texts(s2, ".fd-ex td").slice(0, 6)).toEqual([
      split.situation,
      split.text,
      "예외상황-필터 덮개 나사 같음 · 예외상황-필터 덮개 나사",
      "1 덮개 나사 · 기준 정상 범위 · 2 덕트 이음매",
      "1 나사 새로 끼움 · 2 밴드 조임 · 부품 밴드",
      "재가동 정상(경계 이내)",
    ]);
    expect(texts(s2, ".fd-ex .fd-xk")).toEqual(["같음 · 예외상황-필터 덮개 나사"]); // 칩 하나(예외 전체에 한 번)
    cleanup();
    // D-642 C: 파트장 승인 때 AI 나눔 지시 문장(무엇을 · 어떻게 · 판정 기준)이 그대로 보임 — 승인된 문장만 다른 기계에
    const say = "덮개 나사 네 개를 손으로 돌려 보고, 헐거우면 이상으로 적습니다.";
    const said = { ...one, checks: [{ ...one.checks[0], instruction: say }, one.checks[1]], actions: [{ ...one.actions[0], instruction: "헐거운 나사를 새로 끼웁니다." }, one.actions[1]] };
    const sv = render(<Review api={{} as Api} report={{ ...report(), exception_record: { ...split, exception: said } }} onBack={noop} onDone={noop} />).container;
    expect(texts(sv, ".fd-ex td").slice(3, 5)).toEqual([`1 ${say} · 2 덕트 이음매`, "1 헐거운 나사를 새로 끼웁니다. · 2 밴드 조임 · 부품 밴드"]);
    cleanup();
    // 정답지 30쪽 파트장 승인: 칩 '같음 + 고침 · 이름' · 바뀌기 전 / 뒤 나란히(전 = 그때 받은 대상 지금 목록)
    const tgt = { checks: [{ what: "필터 덮개 고정 나사", how: "손으로 돌려 본다", criteria: "", minutes: null }], actions: [{ what: "나사를 새로 끼움", parts: [], minutes: null }] };
    const edit = { checks: [{ ...tgt.checks[0], how: "풀린 나사는 손으로 돌려 본다" }], actions: tgt.actions };
    const ed = { ...split, exception: { ...one, matches: [{ ...one.matches[0], ...tgt }], edit } };
    const s6 = render(<Review api={{} as Api} report={{ ...report(), exception_record: ed }} onBack={noop} onDone={noop} />).container;
    expect(texts(s6, ".fd-ex .fd-xk")).toEqual(["같음 + 고침 · 예외상황-필터 덮개 나사"]);
    expect(texts(s6, ".fd-ex th").slice(0, 8)).toEqual(["상황", "적은 글", "예외", "점검", "조치", "고침 전", "고침 뒤", "결과"]);
    expect(texts(s6, '.fd-ex tr[data-item="edit-before"] td')).toEqual(["점검 1 필터 덮개 고정 나사 · 손으로 돌려 본다 / 조치 1 나사를 새로 끼움"]);
    expect(texts(s6, '.fd-ex tr[data-item="edit-after"] td')).toEqual(["점검 1 필터 덮개 고정 나사 · 풀린 나사는 손으로 돌려 본다 / 조치 1 나사를 새로 끼움"]);
    cleanup();
    // 아님 = 새 예외 · 예외상황-{주제} · 비슷한 것 이름이 없으면 kind no
    const nw = { ...split, exception: { ...one, decision: "not", match: null, kind: "new_exception" } };
    const s4 = render(<Review api={{} as Api} report={{ ...report(), exception_record: nw }} onBack={noop} onDone={noop} />).container;
    expect(texts(s4, ".fd-ex .fd-xk")).toEqual(["새 예외 · 예외상황-필터 덮개 나사"]);
    cleanup();
    const bare = { ...split, exception: { ...one, matches: [] } };
    const s5 = render(<Review api={{} as Api} report={{ ...report(), exception_record: bare }} onBack={noop} onDone={noop} />).container;
    expect(texts(s5, ".fd-ex .fd-xk")).toEqual(["같음 · 예외 EX-R-0001"]);
    cleanup();
    // 나누지 못한 기록(약속 v10 2절): 적은 글 그대로 새 예외 — '예외' 한 줄
    const unsplit = { ...split, exception: null, exception_review: null, split_error: "not_split" };
    const s3 = render(<Review api={{} as Api} report={{ ...report(), exception_record: unsplit }} onBack={noop} onDone={noop} />).container;
    expect(texts(s3, ".fd-ex th").slice(0, 4)).toEqual(["상황", "적은 글", "예외", "결과"]);
    expect(texts(s3, ".fd-ex td")[2]).toBe("나누지 못함 · 새 예외");
  });

  it("4 조치 결과 '파트장 승인'(D-546 덧 34): 승인된 호출이 있으면 권한 사용 아래 한 줄 · 없으면 줄 없음", () => {
    const call = "필터 덮개 고정 나사 확인(EX-R-0001, C등급) · 승인 후 진행";
    const c = render(<Review api={{} as Api} report={{ ...report(), approved_calls: [call] }} onBack={noop} onDone={noop} />).container;
    const sec4 = (root: HTMLElement) => [...root.querySelectorAll("section")].find((x) => x.querySelector("h2")?.textContent === "4. 조치 결과")!;
    expect(texts(sec4(c), "tr").at(-1)).toBe(`파트장 승인${call}`);
    cleanup();
    const none = render(<Review api={{} as Api} report={{ ...report(), approved_calls: [] }} onBack={noop} onDone={noop} />).container;
    expect(texts(sec4(none), "th")).not.toContain("파트장 승인");
  });

  it("3 조치 경과 매뉴얼 칸: 예외 절(EX-…)은 번호 그대로 · 매뉴얼 절은 'n.n절'(덧 34 그림)", () => {
    const f = report();
    const tl = [{ ...f.timeline[0], section: "EX-R-0001" }, { ...f.timeline[0], section: "4.3" }];
    const c = render(<Review api={{} as Api} report={{ ...f, timeline: tl }} onBack={noop} onDone={noop} />).container;
    const sec3 = [...c.querySelectorAll("section")].find((x) => x.querySelector("h2")?.textContent === "3. 조치 경과")!;
    expect(texts(sec3, "tbody tr td:nth-child(4)")).toEqual(["EX-R-0001", "4.3절"]);
  });

  it("승인: 확인 창(메모 고를 수 있음) → 결정 요청 approve · 「출력」은 요청 0", async () => {
    const f = report();
    const api = { POST: vi.fn(() => ok({ status: "approved", history_record_id: "R-0001" })) } as unknown as Api;
    const onDone = vi.fn();
    const { container } = render(<Review api={api} report={f} onBack={noop} onDone={onDone} />);
    fireEvent.click(btn(container, "출력"));
    expect(api.POST).not.toHaveBeenCalled();
    fireEvent.click(btn(container, "승인"));
    const dlg = container.querySelector(".lmdl")!;
    expect(dlg.querySelector("h4")?.textContent).toBe(`${f.equipment_id} 보고서를 승인합니까?`);
    expect(dlg.querySelector("label")?.textContent).toBe("메모 (선택)");
    await act(async () => fireEvent.click(btn(dlg, "승인")));
    expect(api.POST).toHaveBeenCalledWith("/api/lead/reports/{report_id}/decision", {
      params: { path: { report_id: f.report_id } },
      body: { decision: "approve", edits: {}, memo: null },
    });
    expect(onDone).toHaveBeenCalled();
  });

  it("반려: 사유를 적어야 「반려」가 열림(AC-24-C1) · 취소하면 창 닫힘 · 사유를 쓰면 reject", async () => {
    const f = report();
    const api = { POST: vi.fn(() => ok({ status: "rejected" })) } as unknown as Api;
    const { container } = render(<Review api={api} report={f} onBack={noop} onDone={noop} />);
    fireEvent.click(btn(container, "반려"));
    let dlg = container.querySelector(".lmdl")!;
    expect(dlg.querySelector("label")?.textContent).toBe("반려 사유");
    expect(btn(dlg, "반려").disabled).toBe(true);
    fireEvent.click(btn(dlg, "취소"));
    expect(container.querySelector(".lmdl")).toBeNull();
    fireEvent.click(btn(container, "반려"));
    dlg = container.querySelector(".lmdl")!;
    fireEvent.change(dlg.querySelector("textarea")!, { target: { value: "4.3 메모에 필터 사진 번호를 적어 다시 확정" } });
    await act(async () => fireEvent.click(btn(dlg, "반려")));
    expect(api.POST).toHaveBeenCalledWith("/api/lead/reports/{report_id}/decision", {
      params: { path: { report_id: f.report_id } },
      body: { decision: "reject", edits: {}, memo: "4.3 메모에 필터 사진 번호를 적어 다시 확정" },
    });
  });

  it("EV-007: 확인 창 「승인」을 빠르게 세 번 → 결정 요청 한 번", async () => {
    let release: () => void = noop;
    const POST = vi.fn(
      () => new Promise((r) => (release = () => r({ data: { status: "approved" }, error: undefined, response: { status: 200 } }))),
    );
    const onDone = vi.fn();
    const { container } = render(<Review api={{ POST } as unknown as Api} report={report()} onBack={noop} onDone={onDone} />);
    fireEvent.click(btn(container, "승인"));
    const ok3 = btn(container.querySelector(".lmdl")!, "승인");
    fireEvent.click(ok3);
    fireEvent.click(ok3);
    fireEvent.click(ok3);
    expect(POST).toHaveBeenCalledTimes(1);
    await act(async () => release());
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("승인 뒤(보기만): 결정 버튼 없음 · 상태 승인 완료 · 진행 넷 다 끝 · 꼬리에 파트장 · 시각", () => {
    const f = { ...report(), status: "approved", decision: { kind: "approved", by_name: "박 파트장", at: "2026-11-03T05:14:00Z", memo: null } };
    const { container } = render(<Review api={{} as Api} report={f} onBack={noop} onDone={noop} />);
    expect(texts(container, ".lbar button.lbtn")).toEqual(["출력"]);
    expect(texts(container, ".fd-st span")[1]).toBe("승인 완료");
    expect(container.querySelectorAll(".fd-steps li.done").length).toBe(4);
    expect(texts(container, ".fd-f b")[1]).toBe("박 파트장 · 승인 14:14");
  });

  it("반려 뒤 다시 확정: 진행 둘째 칸 '정비자 다시 확정'", () => {
    const f = { ...report(), rejected_before: true };
    const { container } = render(<Review api={{} as Api} report={f} onBack={noop} onDone={noop} />);
    expect(texts(container, ".fd-steps li")[1]).toMatch(/^2정비자 다시 확정 \d\d:\d\d$/);
  });
});

describe("파트장 3 호출", () => {
  it("목록: 시각 · 설비 · 알람 · 유형 칸 · 보낸 사람 · 필요한 작업 · 「답하기」", () => {
    const d = load<Schemas["LeadCalls"]>("calls-list");
    const onOpen = vi.fn();
    const { container } = render(<CallList data={d} onOpen={onOpen} />);
    expect(container.querySelector(".lbar h2")?.textContent).toBe("호출");
    expect(texts(container, ".lt th")).toEqual(["시각", "설비 · 알람", "유형", "보낸 사람", "필요한 작업", ""]);
    expect(texts(container, "td .tx")).toEqual(d.rows.map((r) => (r.kind === "approval" ? "파트장 호출" : "숙련자 인계")));
    fireEvent.click(container.querySelector("tr[data-call] button.lbtn")!);
    expect(onOpen).toHaveBeenCalledWith(d.rows[0].call_id);
  });

  it("답하기: 미리 고른 답 없음 · 골라야 「답 저장」 열림 · 처리 메모 · 답 저장(AC-23)", async () => {
    const call = load<Schemas["LeadCall"]>("call-detail");
    const api = { POST: vi.fn(() => ok({ status: "answered", answer: "daytime" })) } as unknown as Api;
    const onBack = vi.fn();
    const { container } = render(<CallDetail api={api} call={call} onBack={onBack} onDone={noop} />);
    expect(container.querySelector(".lcrumb")?.textContent).toBe(`호출 / ${call.equipment_id} 파트장 호출`);
    expect(container.querySelector(".rhd .tx")?.textContent).toBe("답 기다림");
    expect(texts(container, ".lsect-h")).toEqual(["정비자가 보낸 요약", "답하기"]);
    expect(texts(container, ".lopts button")).toEqual(["승인 후 진행", "주간 처리", "직접 확인"]);
    expect(container.querySelectorAll('.lopts [aria-checked="true"]').length).toBe(0);
    expect(btn(container, "답 저장").disabled).toBe(true);
    fireEvent.click(container.querySelectorAll(".lopts button")[1]);
    expect(container.querySelector(".lopts button.on")?.textContent).toBe("주간 처리");
    fireEvent.change(container.querySelector("textarea.lta")!, { target: { value: "주간 정비 배정" } });
    await act(async () => fireEvent.click(btn(container, "답 저장")));
    expect(api.POST).toHaveBeenCalledWith("/api/lead/calls/{call_id}/answer", {
      params: { path: { call_id: call.call_id } },
      body: { answer: "daytime", note: "주간 정비 배정" },
    });
    fireEvent.click(container.querySelector(".lcrumb a")!);
    expect(onBack).toHaveBeenCalled();
  });

  it("D등급 호출에는 '승인 후 진행'이 없다(AC-23-C3) · 숙련자 인계는 2개", () => {
    let r = render(<CallDetail api={{} as Api} call={load("call-detail-d")} onBack={noop} onDone={noop} />);
    expect(texts(r.container, ".lopts button")).toEqual(["주간 처리", "직접 확인"]);
    cleanup();
    r = render(<CallDetail api={{} as Api} call={load("handover-detail")} onBack={noop} onDone={noop} />);
    expect(texts(r.container, ".lopts button")).toEqual(["주간 숙련자 배정", "직접 확인"]);
    expect(r.container.querySelector(".rhd h2")?.textContent).toContain("숙련자 인계");
  });

  it("답한 뒤 잠김: 고른 답 표시 · 선택지 · 메모 잠김 · 「답 저장」 없음", () => {
    const call = { ...load<Schemas["LeadCall"]>("call-detail"), answered: true, answer: { answer: "direct", note: "현장 확인" } };
    const { container } = render(<CallDetail api={{} as Api} call={call} onBack={noop} onDone={noop} />);
    expect(container.querySelector(".rhd .tx")?.textContent).toBe("답함 · 직접 확인");
    expect(container.querySelector(".lopts button.on")?.textContent).toBe("직접 확인");
    expect([...container.querySelectorAll(".lopts button")].every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
    expect((container.querySelector("textarea.lta") as HTMLTextAreaElement).value).toBe("현장 확인");
    expect(btn(container, "답 저장")).toBeUndefined();
  });

  it("보낸 뒤 정비자 정정 상자(AC-47-C2 · 결정 019 ②): 시각 · 점검 · 전 → 후 · 사유, 요약 칸 안", () => {
    const call = load<Schemas["LeadCall"]>("call-corrected");
    const { container } = render(<CallDetail api={{} as Api} call={call} onBack={noop} onDone={noop} />);
    const box = container.querySelector('[data-part="correction"]')!;
    expect(box.querySelector("b")?.textContent).toMatch(/^보낸 뒤 정비자 정정 · \d\d:\d\d$/);
    expect(texts(box, "div")).toEqual(["4.3 흡기 필터 육안 점검 · 정상 → 이상", "사유 · 필터 뒷면 기름때를 못 보고 정상으로 눌렀음"]);
    cleanup();
    const h = render(<CallDetail api={{} as Api} call={load("handover-corrected")} onBack={noop} onDone={noop} />);
    expect(texts(h.container, '[data-part="correction"] div')[0]).toBe("4.6 냉각 설정값 확인 · 정상 → 이상");
    cleanup();
    const c = render(<CallDetail api={{} as Api} call={load("correction-call-detail")} onBack={noop} onDone={noop} />);
    expect(c.container.querySelector(".lsum")?.textContent).toContain("정정");
    expect(c.container.querySelector('[data-part="correction"]')).toBeNull();
  });
});

describe("파트장 4 권한 등급", () => {
  it("요약 넷 · 등급표 전부(A ~ D 글자 칸 · 처리) · 변경 기록 없음 · 등급의 뜻(결정 018 ②)", () => {
    const g = load<Schemas["LeadGrades"]>("grades-default");
    const { container } = render(<GradesScreen api={{} as Api} data={g} onChanged={noop} />);
    const n = (x: string) => g.rows.filter((r) => r.grade === x).length;
    expect(texts(container, ".ltiles b")).toEqual([`${g.total}절`, `${n("A")}절`, `${n("B")}절`, `${n("C") + n("D")}절`]);
    expect(container.querySelectorAll("tr[data-section]").length).toBe(g.total);
    expect(texts(container, 'tr[data-section="4.4"] .lseg button')).toEqual(["A", "B", "C", "D"]);
    expect(container.querySelector('tr[data-section="4.4"] .lseg button.on')?.textContent).toBe("C");
    expect(texts(container, '[data-part="changes"]')).toEqual(["변경 기록 없음"]);
    expect(texts(container, ".lkv div")).toEqual(["A단독 가능", "B확인 후", "C승인 필요", "D범위 밖"]);
  });

  it("등급 글자 → 확인 창(지금 · 바꿀 등급 · 서버 경고 문구 그대로) → 「바꾸기」 저장(confirmed) · 취소면 요청 0(AC-25-C3)", async () => {
    const g = load<Schemas["LeadGrades"]>("grades-default");
    const raise = load<Schemas["GradeWarning"]>("warning-raise");
    const api = { GET: vi.fn(() => ok(raise)), POST: vi.fn(() => ok({ section: "4.3", before: "A", after: "C" })) } as unknown as Api;
    const onChanged = vi.fn();
    const { container } = render(<GradesScreen api={api} data={g} onChanged={onChanged} />);
    fireEvent.click(container.querySelector('tr[data-section="4.3"] .lseg button.on')!); // 지금 등급은 창 0
    expect(api.GET).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(container.querySelectorAll('tr[data-section="4.3"] .lseg button')[2]));
    expect(api.GET).toHaveBeenCalledWith("/api/lead/grades/{section}/warning", { params: { path: { section: "4.3" }, query: { grade: "C" } } });
    const dlg = container.querySelector(".lmdl")!;
    expect(dlg.querySelector("h4")?.textContent).toBe("4.3 흡기 필터 육안 점검 등급을 A → C로 바꿉니까?");
    expect(texts(dlg, ".vt tr")).toEqual(["지금A 단독 가능", "바꿀 등급C 승인 필요"]);
    expect(dlg.querySelector('[data-part="warning"] b')?.textContent).toBe(raise.head);
    expect(dlg.querySelector('[data-part="warning"]')?.textContent).toContain(raise.body);
    await act(async () => fireEvent.click(btn(dlg, "바꾸기")));
    expect(api.POST).toHaveBeenCalledWith("/api/lead/grades/{section}", { params: { path: { section: "4.3" } }, body: { grade: "C", confirmed: true } });
    expect(onChanged).toHaveBeenCalled();
    cleanup();
    const api2 = { GET: vi.fn(() => ok(load("warning-lower"))), POST: vi.fn() } as unknown as Api;
    const r = render(<GradesScreen api={api2} data={g} onChanged={noop} />);
    await act(async () => fireEvent.click(r.container.querySelectorAll('tr[data-section="4.4"] .lseg button')[1]));
    expect(r.container.querySelector('[data-part="warning"].lower b')?.textContent).toBe("등급을 낮추려 합니다 · 4.4 냉각 팬 분해 점검 C → B");
    fireEvent.click(btn(r.container.querySelector(".lmdl")!, "취소"));
    expect(r.container.querySelector(".lmdl")).toBeNull();
    expect(api2.POST).not.toHaveBeenCalled();
  });

  it("변경 기록: 시각 · 절 · 전 → 후 · 누가(AC-25-C2)", () => {
    const g = load<Schemas["LeadGrades"]>("grades-changed");
    const { container } = render(<GradesScreen api={{} as Api} data={g} onChanged={noop} />);
    expect(texts(container, '[data-part="changes"] div').map((x) => x.replace(/^\d\d:\d\d/, ""))).toEqual([
      "4.3 흡기 필터 육안 점검 · C → A · 박 파트장",
      "4.3 흡기 필터 육안 점검 · A → C · 박 파트장",
    ]);
  });
});

describe("파트장 5 감사 기록", () => {
  it("표(시각 · 앱 · 사용자 · 행동 · 대상) · 칩 「전체 · 정비자 앱 · 파트장 앱」으로 거름 · 시스템 행은 전체에만", () => {
    const a = load<Schemas["LeadAudit"]>("audit-default");
    const onApp = vi.fn();
    const { container, rerender } = render(<AuditScreen data={a} app="all" onApp={onApp} onFilter={noop} />);
    expect(texts(container, ".lt th")).toEqual(["시각", "앱", "사용자", "행동", "대상"]);
    expect(texts(container, ".lchips button")).toEqual(["전체", "정비자 앱", "파트장 앱"]);
    expect(container.querySelectorAll("tr[data-audit]").length).toBe(a.rows.length);
    expect(texts(container, "tr[data-audit] td:nth-child(2)")).toContain("시스템");
    fireEvent.click(container.querySelectorAll(".lchips button")[2]);
    expect(onApp).toHaveBeenCalledWith("파트장");
    rerender(<AuditScreen data={a} app="파트장" onApp={onApp} onFilter={noop} />);
    expect(new Set(texts(container, "tr[data-audit] td:nth-child(2)"))).toEqual(new Set(["파트장 앱"]));
    expect(container.querySelector(".lsect-h")?.textContent).toBe(`기록${a.rows.filter((r) => r.app === "파트장").length}건`);
  });

  it("지금 거르기 셋(설비 · 사용자 · 기간)은 남김 — 고르면 그 조건으로 다시 읽음(AC-26)", () => {
    const a = load<Schemas["LeadAudit"]>("audit-default");
    const onFilter = vi.fn();
    const { container } = render(<AuditScreen data={a} app="all" onApp={noop} onFilter={onFilter} />);
    expect(texts(container, ".lflt > .lsel").map((t) => t.split(" · ")[0])).toEqual(["설비", "사용자", "기간"]);
    fireEvent.click(container.querySelectorAll(".lflt > .lsel")[0]);
    fireEvent.click([...container.querySelectorAll(".dd div")].find((d) => d.textContent === "M-01")!);
    expect(onFilter).toHaveBeenCalledWith({ equipment: "M-01", user_id: null, date: a.date });
    cleanup();
    const f = render(<AuditScreen data={load("audit-filtered")} app="all" onApp={noop} onFilter={noop} />);
    expect(f.container.querySelectorAll(".lflt > .lsel")[0].textContent).toMatch(/^설비 · M-01/);
  });

  it("정정(전 → 후 · 사유) · 메모 덧붙이기 행은 서버 문구 그대로 · 빈 표는 '기록이 없습니다'", () => {
    const a = load<Schemas["LeadAudit"]>("audit-correction");
    const { container } = render(<AuditScreen data={a} app="all" onApp={noop} onFilter={noop} />);
    const acts = texts(container, "tr[data-audit] td:nth-child(4)");
    expect(acts).toContain("정정 · 4.3 결과 정상 → 이상 · 사유 '필터 뒷면 기름때를 못 보고 정상으로 눌렀음'");
    expect(acts).toContain("메모 덧붙이기 · 잠금 완료 1.2");
    cleanup();
    const e = render(<AuditScreen data={{ ...a, rows: [] }} app="all" onApp={noop} onFilter={noop} />);
    expect(e.container.querySelector(".lempty")?.textContent).toBe("기록이 없습니다");
  });
});
