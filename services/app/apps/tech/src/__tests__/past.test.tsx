// P-15 component 시험: S-05 실제 API 응답(fixtures/s05-*)으로 T15 지난 화면 · 정정 창 · 확인 창 · 메모 덧붙이기 · 정정 호출(T10 / summary_corrected)을 그려
// packet 부록 template 구조(띠 · 보기 상태 · 잠긴 조작 · 창)와 보내는 입력을 대조한다. 판정 · 반영 방식은 서버 값이다.

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StepBar, type Api, type SessionView } from "@c1a15/ui";
import { CallScreen } from "../screens/Flow";
import { PastScreen, type PastData } from "../screens/Past";
import { Session } from "../screens/Session";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
function load<T = SessionView>(name: string): T {
  return structuredClone(fx[`./fixtures/${name}.json`]) as T;
}
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");
const btn = (root: ParentNode, label: string) =>
  [...root.querySelectorAll(".btn")].find((b) => b.textContent?.trim() === label) as HTMLElement;
const ok = (data: unknown) => Promise.resolve({ data, error: undefined, response: { status: 200 } });

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function past(data: PastData, no: number, idx: number, extra: Partial<Parameters<typeof PastScreen>[0]> = {}) {
  const props = {
    api: { GET: vi.fn(), POST: vi.fn() } as unknown as Api,
    sid: data.session_id,
    s: load("s05-session-open"),
    data,
    no,
    idx,
    onPick: vi.fn(),
    onCurrent: vi.fn(),
    unsent: null,
    onSaved: vi.fn(),
    onRecord: vi.fn(),
    onManual: vi.fn(),
    ...extra,
  };
  return { ...render(<PastScreen {...props} />), props };
}

// 답장 001-zzx 마(가이드 22 · 23쪽): 점검 · 조치 단계 = 왼쪽 '지금까지 점검 · 조치' 기록 목록 + 오른쪽 고른 기록 칸(정정 · 메모는 칸 안) + 「지금 단계로」.
const rowsOf = (h: PastData) => h.steps.filter((st) => st.no === 3 || st.no === 4).flatMap((st) => st.records);

describe("T15 지난 화면 — 두 칸 · 보기 상태(AC-41)", () => {
  it("check: 왼쪽 '지금까지 점검 · 조치' 목록(고른 줄 on) · 오른쪽 보낸 결과 · 「이 기록 정정」 · 「지금 단계로」, 위 띠 · 이전 · 다음 없음", () => {
    const h = load<PastData>("s05-history-open");
    const recs = h.steps[2].records;
    const { container, props } = past(h, 3, recs.length - 1);
    expect(container.querySelector(".pastbar")).toBeNull();
    expect(container.querySelector(".modal")).toBeNull();
    expect(container.querySelector(".g64 > .cd:first-child .cd-h b")?.textContent).toBe("지금까지 점검 · 조치");
    expect(container.querySelectorAll(".pli").length).toBe(rowsOf(h).length);
    expect(texts(container, ".pli.on b")).toEqual(["흡기 필터 육안 점검 · 4.3"]);
    expect(container.querySelector(".pdet .cd-h b")?.textContent).toBe("흡기 필터 육안 점검 · 4.3");
    expect(texts(container, ".pdet .prow > *")).toEqual(["보낸 결과", "정상 · 메모 '필터 깨끗함'"]);
    expect(btn(container, "이 기록 정정")).toBeTruthy();
    expect(btn(container, "이전")).toBeUndefined();
    fireEvent.click(container.querySelectorAll(".pli")[0]);
    expect(props.onPick).toHaveBeenCalledWith(3, 0); // AC-41-C2: 다른 기록
    fireEvent.click(btn(container, "지금 단계로"));
    expect(props.onCurrent).toHaveBeenCalled(); // AC-41-C3
  });

  it("check_current: 지금 지시에 보내지 않은 입력이 있으면 오른쪽 칸에 '그대로' 칩(호박색, CO-39)", () => {
    const h = load<PastData>("s05-history-open");
    const { container } = past(h, 3, h.steps[2].records.length - 1, { unsent: { section: "4.6", title: "냉각 설정값 확인", desc: "정상 · 메모 'x'" } });
    expect(container.querySelector(".pdet .ch.amber.unsent")?.textContent).toBe("지금 4.6 입력(보내지 않음)은 그대로");
  });

  it("alarm · diagnosis: 같은 화면을 보기 상태로(ap-body past, 다음 버튼 잠김), 띠에 「지금 단계로」만", () => {
    const h = load<PastData>("s05-history-open");
    const a = past(h, 1, 0).container;
    expect(a.querySelector(".ap-body.past .alh h3")?.textContent).toBe(h.steps[0].records[0].alarm!.group_name);
    expect(btn(a, "진단 보기").className).toContain("dis");
    expect(texts(a, ".pastbar .pn .btn")).toEqual(["지금 단계로"]);
    cleanup();
    const d = past(h, 2, 0).container;
    expect(d.querySelector(".pastbar .tt")?.textContent).toMatch(/그때의 원인 순위 · 첫 지시$/);
    expect(d.querySelector(".nx h3")?.textContent).toBe("흡기 필터 육안 점검");
    expect(btn(d, "점검 시작").className).toContain("dis");
  });
});

describe("정정(AC-45 · AC-46)", () => {
  it("correct → correct_confirm: 오른쪽 칸 안에서 정정(창 없음) · 사유가 비면 저장 잠김, 열린 지시가 바뀌면 서버 확인 요청 → 확인 창 → confirmed로 저장", async () => {
    const h = load<PastData>("s05-history-open");
    const recs = h.steps[2].records;
    const confirm = load<{ detail: unknown }>("s05-confirm-required");
    const saved = load("s05-session-rejudged");
    const POST = vi
      .fn()
      .mockResolvedValueOnce({ data: undefined, error: { detail: confirm.detail }, response: { status: 409 } })
      .mockResolvedValueOnce({ data: saved, error: undefined, response: { status: 200 } });
    const unsent = { section: "4.6", title: "냉각 설정값 확인", desc: "정상 · 메모 '설정값 정상'" };
    const { container, props } = past(h, 3, recs.length - 1, { api: { GET: vi.fn(), POST } as unknown as Api, unsent });
    fireEvent.click(btn(container, "이 기록 정정"));
    expect(container.querySelector(".modal")).toBeNull();
    const form = container.querySelector(".pdet .pform")!;
    expect(texts(form, ":scope > .sm")).toEqual(["정정할 결과", "정정할 메모", "정정 사유"]);
    expect(btn(form, "정정 저장").className).toContain("dis"); // AC-45-C2
    fireEvent.click([...form.querySelectorAll(".rbtn")].find((b) => b.textContent === "이상")!);
    fireEvent.change(form.querySelector('[aria-label="정정할 메모"]')!, { target: { value: "필터 뒷면에 기름때가 절반 넘게 덮음" } });
    fireEvent.change(form.querySelector('[aria-label="정정 사유"]')!, { target: { value: "뒷면을 못 봄" } });
    await act(async () => fireEvent.click(btn(container.querySelector(".pform")!, "정정 저장")));
    const target = recs.at(-1)!.seq;
    expect(POST.mock.calls[0][1].body).toEqual({ target_seq: target, reason: "뒷면을 못 봄", value: "이상", memo: "필터 뒷면에 기름때가 절반 넘게 덮음" });
    const cm = container.querySelector('.modal[aria-label="이 정정으로 다음 지시가 바뀝니다"]')!;
    expect(cm.querySelector(".banner.warn b")?.textContent).toBe("4.3 흡기 필터 육안 점검 · 정상 → 이상");
    expect(cm.querySelector(".banner.warn div")?.textContent).toContain("지금 지시 4.6 냉각 설정값 확인");
    expect(cm.querySelector(".banner.warn div")?.textContent).toContain("보내지 않은 입력(정상 · 메모 '설정값 정상')은 지워지고 새 지시가 나옵니다");
    expect(btn(cm, "정정 저장").className).toContain("warnb");
    expect(props.onSaved).not.toHaveBeenCalled(); // AC-46-C4: 확인 전에는 아무것도 바뀌지 않음
    await act(async () => fireEvent.click(btn(cm, "정정 저장")));
    expect(POST.mock.calls[1][1].body).toEqual({ ...POST.mock.calls[0][1].body, confirmed: true });
    expect(props.onSaved).toHaveBeenCalledWith(saved, true);
  });

  it("corrected: '정정 1회' 칩 · 이전 값 → 최신 값 · 사유(AC-45-C4), 다시 정정 가능", () => {
    const h = load<PastData>("s05-history-corrected");
    const recs = h.steps[2].records;
    const { container } = past(h, 3, recs.length - 1);
    expect(container.querySelector(".pdet .cd-h .ch.amber")?.textContent).toBe("정정 1회");
    expect(container.querySelector(".pdet .prow b")?.textContent).toBe("이상 · 메모 '필터 뒷면에 기름때가 절반 넘게 덮음'");
    expect(container.querySelector(".corr-list s")?.textContent).toBe("정상 · '필터 깨끗함'");
    expect(container.querySelector(".corr-list b")?.textContent).toBe("이상 · '필터 뒷면에 기름때가 절반 넘게 덮음'");
    expect(container.querySelector(".corr-list .sm")?.textContent).toBe("사유 · 필터 뒷면 기름때를 못 보고 정상으로 눌렀음");
    expect(btn(container, "이 기록 정정")).toBeTruthy();
  });

  it("precheck → correct_value: 측정값 정정(정정할 값 · 사유, 결과 버튼 없음)", async () => {
    const h = load<PastData>("s05-history-open");
    const i = h.steps[2].records.findIndex((r) => r.kind === "precheck");
    const POST = vi.fn(() => ok(load("s05-session-open")));
    const { container } = past(h, 3, i, { api: { GET: vi.fn(), POST } as unknown as Api });
    expect(texts(container, ".pdet .prow > span")).toEqual(["기록한 값"]);
    fireEvent.click(btn(container, "이 기록 정정"));
    const form = container.querySelector(".pform")!;
    expect(texts(form, ":scope > .sm")).toEqual(["정정할 값", "정정 사유"]);
    expect(form.querySelector(".rbtns")).toBeNull();
    fireEvent.change(form.querySelector('[aria-label="정정할 값"]')!, { target: { value: "하우징 표면 55 °C" } });
    fireEvent.change(form.querySelector('[aria-label="정정 사유"]')!, { target: { value: "다시 잼" } });
    await act(async () => fireEvent.click(btn(form, "정정 저장")));
    expect((POST.mock.calls[0] as unknown[])[1]).toMatchObject({ body: { target_seq: h.steps[2].records[i].seq, reason: "다시 잼", values: "하우징 표면 55 °C" } });
  });
});

describe("정정하지 않는 기록 · 확정 세션(AC-48)", () => {
  it("lock → note: 기록 줄 · 「메모 덧붙이기」 → 칸 안 '덧붙일 메모'(빈 메모면 저장 잠김) → /notes, 덧붙인 메모는 오른쪽 칸에(019 ⑥)", async () => {
    const h = load<PastData>("s05-history-corrected");
    const POST = vi.fn(() => ok(load("s05-session-rejudged")));
    const { container } = past(h, 3, 0, { api: { GET: vi.fn(), POST } as unknown as Api });
    const lock = h.steps[2].records[0];
    expect(container.querySelector(".pdet .cd-h b")?.textContent).toBe("잠금 확인 · 1.2");
    expect(texts(container, ".pdet .prow > *")).toEqual(["기록", lock.record]);
    expect(container.querySelector(".corr-list b")?.textContent).toBe("잠금 표지 2번 패널에 추가로 걺");
    expect(btn(container, "이 기록 정정")).toBeUndefined();
    fireEvent.click(btn(container, "메모 덧붙이기"));
    expect(container.querySelector(".modal")).toBeNull();
    const form = container.querySelector(".pform")!;
    expect(texts(form, ":scope > .sm")).toEqual(["덧붙일 메모"]);
    expect(btn(form, "메모 저장").className).toContain("dis");
    fireEvent.change(form.querySelector('[aria-label="덧붙일 메모"]')!, { target: { value: "표지 하나 더" } });
    await act(async () => fireEvent.click(btn(form, "메모 저장")));
    expect((POST.mock.calls[0] as unknown[])[0]).toBe("/api/tech/sessions/{sid}/notes");
    expect((POST.mock.calls[0] as unknown[])[1]).toMatchObject({ body: { target_seq: lock.seq, memo: "표지 하나 더" } });
  });

  it("call: 호출 기록(보낸 요약) · '보낸 뒤 정비자 정정' 상자 · 메모 덧붙이기만", () => {
    const h = load<PastData>("s05-history-call");
    const recs = h.steps[2].records;
    const i = recs.findIndex((r) => r.kind === "call");
    const { container } = past(h, 3, i);
    expect(container.querySelector(".pdet .cd-h b")?.textContent).toBe("파트장 호출");
    expect(container.querySelector(".pv .sm")?.textContent).toBe("보낸 요약");
    expect(container.querySelector(".corr-box div")?.textContent).toBe("4.3 흡기 필터 육안 점검 · 정상 → 이상");
    expect(btn(container, "메모 덧붙이기")).toBeTruthy();
    expect(btn(container, "이 기록 정정")).toBeUndefined();
  });

  it("handover: 숙련자 인계 기록(결정 019 ②) — 보낸 요약 · 정정 상자, 메모 덧붙이기만(AC-48-C1)", () => {
    const h = load<PastData>("s05-history-handover");
    const recs = h.steps[2].records;
    const i = recs.findIndex((r) => r.kind === "call");
    const { container } = past(h, 3, i);
    expect(container.querySelector(".pdet .cd-h b")?.textContent).toBe("숙련자 인계");
    expect(container.querySelector(".pv .sm")?.textContent).toBe("보낸 요약");
    expect(container.querySelector(".corr-box div")?.textContent).toBe("4.6 냉각 설정값 확인 · 정상 → 이상");
    expect(container.querySelector(".pdet .prow b")?.textContent).toMatch(/^인계 보냄 \d\d:\d\d · 답 기다림$/);
    expect(btn(container, "메모 덧붙이기")).toBeTruthy();
    expect(btn(container, "이 기록 정정")).toBeUndefined();
  });

  it("action: 목록에 조치 · 재가동 두 줄(같은 기록 — 둘 다 on), 기록 줄 · 메모 덧붙이기만", () => {
    const ha = load<PastData>("s05-history-action");
    const a = past(ha, 4, 0).container;
    expect(texts(a, ".pli.on b")).toEqual(["흡기 필터 교체 · 4.3.2", "재가동 · 6.1"]);
    expect(texts(a, ".pli.on span")[1]).toMatch(/^재가동 \d\d:\d\d$/);
    expect(a.querySelector(".pdet .prow b")?.textContent).toMatch(/^조치 완료 \d\d:\d\d · 재가동 \d\d:\d\d · 경계 이내 \d\d:\d\d$/);
    expect(btn(a, "메모 덧붙이기")).toBeTruthy(); // AC-48-C1: 조치 · 재가동(경계 확인 포함) 기록은 메모 덧붙이기만
    expect(btn(a, "이 기록 정정")).toBeUndefined();
  });

  it("closed: 정정 · 메모 덧붙이기 수단 없이 확정 안내", () => {
    const h = load<PastData>("s05-history-closed");
    const recs = h.steps[2].records;
    const { container } = past(h, 3, recs.length - 1);
    expect(btn(container, "이 기록 정정")).toBeUndefined();
    expect(container.querySelector(".pdet .hint")?.textContent).toBe(
      "보고서를 확정한 세션이라 정정할 수 없습니다. 파트장이 반려하면 다시 정정할 수 있습니다.",
    );
    cleanup();
    const lock = past(h, 3, 0).container;
    expect(btn(lock, "메모 덧붙이기")).toBeUndefined();
  });
});

describe("진행 표시 · 세션 연결(AC-41 · 생성자 해석 ①)", () => {
  it("StepBar: 완료 단계 pick(누르면 그 단계), 보는 단계 view, 지금 단계는 'on' 그대로(기록이 있으면 누름)", () => {
    const onPick = vi.fn();
    const { container } = render(<StepBar current={3} view={2} onPick={onPick} currentPickable />);
    // 지시 d530-p33(가이드 23쪽): 보는 단계(2)가 지금 꼴 · 그 뒤 지금 단계(3)는 회색이되 누름(기록이 있으면)
    expect([...container.querySelectorAll(".sp")].map((e) => e.className)).toEqual(["sp done pick", "sp on", "sp pick", "sp ", "sp "]);
    fireEvent.click(container.querySelectorAll(".sp")[0]);
    fireEvent.click(container.querySelectorAll(".sp")[2]);
    fireEvent.click(container.querySelectorAll(".sp")[3]);
    expect(onPick.mock.calls).toEqual([[1], [3]]);
  });

  it("잠금 전 세션(T04 진단 · T05 잠금 전): 완료 단계도 pick — 누르면 그 단계 지난 화면(AC-41-C1, S-06 전 화면 대조에서 발견)", async () => {
    // EV-007: 진단 화면에 온 세션은 알람 확인 기록이 있음(fixture는 그 전 응답 — 칸만 덧붙임)
    const view = { ...load<SessionView>("session-before-lock"), alarm_ack_at: "2026-10-27T08:00:00+00:00" };
    const h = load<PastData>("s05-history-open");
    const GET = vi.fn((path: string) => ok(path.endsWith("/history") ? h : view));
    const api = { GET, POST: vi.fn() } as unknown as Api;
    localStorage.setItem(`c1a15.tech.stage.${view.session_id}`, JSON.stringify("diagnosis"));
    const { container } = render(
      <Session api={api} sid={view.session_id} initial={view as never} onSession={() => {}} onExpired={() => {}} onRecord={() => {}} onManual={() => {}} onHome={() => {}} />,
    );
    expect([...container.querySelectorAll(".sp")].map((e) => e.className).slice(0, 2)).toEqual(["sp done pick", "sp on"]);
    await act(async () => fireEvent.click(container.querySelectorAll(".sp")[0]));
    await waitFor(() => expect(container.querySelector(".pastbar b")?.textContent).toBe("지난 화면 · 알람"));
    expect(container.querySelectorAll(".sp")[0].className).toBe("sp on"); // 보는 단계(알람)가 지금 꼴(가이드 23쪽)
    expect(container.querySelectorAll(".sp")[1].className).toBe("sp "); // 잠금 전 지금 단계는 누르지 않음(보낸 기록 없음)
  });

  it("세션: 지나온 단계를 누르면 /history로 그 단계 마지막 기록, '지금 단계로'면 지금 지시와 보내지 않은 입력 그대로(AC-41-C3)", async () => {
    const view = load<SessionView>("s05-session-open");
    const h = load<PastData>("s05-history-open");
    const GET = vi.fn((path: string) => ok(path.endsWith("/history") ? h : view));
    const api = { GET, POST: vi.fn() } as unknown as Api;
    localStorage.setItem(`c1a15.tech.draft.${view.session_id}`, JSON.stringify({ section: view.instruction!.section, value: "정상", memo: "설정값 정상", precheck: "", checked: [] }));
    const { container } = render(
      <Session api={api} sid={view.session_id} initial={null} onSession={() => {}} onExpired={() => {}} onRecord={() => {}} onManual={() => {}} onHome={() => {}} />,
    );
    await waitFor(() => expect(container.querySelector(".ins-h h3")?.textContent).toBe(view.instruction!.title));
    await act(async () => fireEvent.click(container.querySelectorAll(".sp")[1]));
    await waitFor(() => expect(container.querySelector(".pastbar b")?.textContent).toBe("지난 화면 · 진단"));
    // 지시 d530-p33(가이드 23쪽): 지난 단계를 보는 동안 단계 띠는 그 단계가 지금(파랑) · 뒤 단계는 회색
    expect([...container.querySelectorAll(".sp")].map((e) => e.className)).toEqual(["sp done pick", "sp on", "sp pick", "sp ", "sp "]);
    await act(async () => fireEvent.click(container.querySelectorAll(".sp")[2])); // 지금 단계도 보낸 기록이 있으면 기록 보기(①)
    await waitFor(() => expect(container.querySelector(".pdet")).not.toBeNull());
    expect(container.querySelector(".pdet .unsent")?.textContent).toBe(`지금 ${view.instruction!.section} 입력(보내지 않음)은 그대로`);
    await act(async () => fireEvent.click(btn(container, "지금 단계로")));
    expect(container.querySelector(".pdet")).toBeNull();
    expect(container.querySelector(".rbtn.sel")?.textContent).toBe("정상");
    expect((container.querySelector('[aria-label="메모"]') as HTMLTextAreaElement | null)?.value).toBe("설정값 정상");
  });
});

describe("T10 / summary_corrected — 조치 뒤 정정 호출(AC-47-C1 · 결정 019 ①)", () => {
  it("머리(가이드 24쪽 — 답장 001-zzx 사): 작은 줄 '파트장 확인이 필요합니다' · 제목 '정정 확인 · 절 이름' · 칩 '이상 → 정상' · 지금까지 점검 · 안내 문장 0 · 파트장 호출 / 대기", () => {
    const s = load("s05-session-correction-call");
    const send = vi.fn();
    const { container } = render(<CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={() => {}} send={send} />);
    expect(container.querySelector(".warn-h .sm")?.textContent).toBe("파트장 확인이 필요합니다");
    expect(container.querySelector(".warn-h h3")?.textContent).toBe("정정 확인 · 4.3 흡기 필터 육안 점검");
    expect(texts(container, ".warn-h .tags .ch")).toEqual(["이상 → 정상"]);
    expect(texts(container, ".sub")).toEqual(["지금까지 점검"]);
    expect(texts(container, ".dn2 span")).toEqual(["흡기 필터 육안 점검 · 정상(정정 1회, 원래 이상)"]);
    expect(container.querySelector(".hint")).toBeNull();
    expect(container.querySelector(".pv p")?.textContent).toContain("결과를 이상 → 정상으로 정정");
    expect((btn(container, "파트장 호출").firstChild as Element | null)?.getAttribute?.("class")).toContain("lucide-phone-call"); // 아이콘은 이름 앞(가이드 24 · 25쪽)
    fireEvent.click(btn(container, "파트장 호출"));
    expect(send).toHaveBeenCalledWith({ type: "call_send" });
  });

  it("답(주간 처리 · 직접 확인): 답 배너에 '정정 확인 · …' · 보고서 작성(019 ① ⑧)", () => {
    for (const name of ["daytime", "direct"]) {
      const s = load(`s05-session-correction-ans-${name}`);
      const onReport = vi.fn();
      const { container } = render(<CallScreen s={s} busy={false} approvedSeen={false} onApprovedSeen={() => {}} onReport={onReport} send={vi.fn()} />);
      const banner = container.querySelector(".banner")!;
      if (name === "daytime") expect(banner.textContent).toContain("정정 확인 · 4.3 흡기 필터 육안 점검(이상 → 정상)");
      else expect(banner.querySelector("b")?.textContent).toBe("파트장 직접 확인 · 처리 결과");
      fireEvent.click(btn(container, "보고서 작성"));
      expect(onReport).toHaveBeenCalled();
      cleanup();
    }
  });
});
