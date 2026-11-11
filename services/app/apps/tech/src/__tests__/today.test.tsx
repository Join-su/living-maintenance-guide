// D-528 정비자 앱(가이드 정비자 2 · 부록 2): 왼쪽 「오늘 밤 알람」 칸 · 내 알람 카드 상태값 한 줄 · 홈 안내 문장 뺌.
// home-d528.json = 시험 DB 실제 응답 한 건(server/tests/test_tech_home_d528.py HOME_FIXTURE_OUT, D-503 2).

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Home } from "@c1a15/ui";
import { HomeScreen, val } from "../screens/Home";
import { TodayPanel } from "../screens/Today";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = <T,>(name: string) => structuredClone(fx[`./fixtures/${name}.json`]) as T;
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");

afterEach(cleanup);

describe("왼쪽 「오늘 밤 알람」 칸", () => {
  it("진행 탭: 새 · 진행 중 알람만 · 계통 · 시각 · 설비 · 알람 · 값 · 경계 · 단계, 누르면 그 알람(점검 시작과 같은 길)", () => {
    const h = load<Home>("home-d528");
    const onOpen = vi.fn();
    const { container } = render(<TodayPanel home={h} current={null} onOpen={onOpen} />);
    const live = h.alarms.filter((a) => a.status !== "done");
    expect(container.querySelector(".n-lh b")?.textContent).toBe("오늘 밤 알람");
    expect(container.querySelector(".n-cnt")?.textContent).toBe(String(live.length));
    expect(texts(container, ".tabs button")).toEqual(["진행", "완료"]);
    const rows = container.querySelectorAll("button.n-li");
    expect(rows.length).toBe(live.length);
    const a = live[0];
    expect(rows[0].querySelector(".n-k")?.textContent).toBe("냉각 계통");
    expect(rows[0].querySelector("b")?.textContent).toBe(`${a.equipment_id} · ${a.group_name}`);
    expect(texts(rows[0], ".n-lv span")).toEqual([`${a.alarm.sensor} ${val(a.alarm.value, a.alarm.unit)}`, `경계 ${val(a.alarm.boundary, a.alarm.unit)}`]);
    expect(rows[0].querySelector(".n-ls")?.textContent).toBe("🚨새 알람");
    fireEvent.click(rows[0]);
    expect(onOpen).toHaveBeenCalledWith(a);
  });

  it("완료 탭: 최근 내 보고서(계통 · 설비 · 원인 · 승인 상태) · 같은 탭 한 번 더 눌러도 그대로 · 진행으로 돌아옴", () => {
    const h = load<Home>("home-d528");
    const { container } = render(<TodayPanel home={h} current={null} onOpen={() => {}} />);
    const done = () => container.querySelectorAll(".n-li.ro");
    fireEvent.click(container.querySelectorAll(".tabs button")[1]);
    fireEvent.click(container.querySelectorAll(".tabs button")[1]);
    expect(done().length).toBe(h.recent_reports.length);
    const r = h.recent_reports[0];
    expect(done()[0].querySelector("b")?.textContent).toBe(`${r.equipment_id} · ${r.cause}`);
    expect(done()[0].querySelector(".n-k")?.textContent).toBe("냉각 계통");
    expect(done()[0].querySelector(".ch")?.textContent).toBe("승인 대기");
    fireEvent.click(container.querySelectorAll(".tabs button")[0]);
    expect(container.querySelectorAll("button.n-li").length).toBe(1);
  });

  it("알람 화면(세션이 막 열려 홈 줄에 세션 번호가 아직 없을 때)도 그 알람 줄을 고른 꼴로 — 지시 d530-p33(가이드 5쪽)", () => {
    const h = load<Home>("home-d528");
    const a = h.alarms.find((x) => x.status === "new")!;
    const { container } = render(<TodayPanel home={h} current={9999} currentAlarm={a.alarm_id} onOpen={() => {}} />);
    expect(container.querySelectorAll("button.n-li.on").length).toBe(1);
    expect(container.querySelector("button.n-li.on b")?.textContent).toBe(`${a.equipment_id} · ${a.group_name}`);
  });

  it("빈 상태: 진행 중인 알람 없음(개수 칸 없음)", () => {
    const h = { ...load<Home>("home-d528"), alarms: [] };
    const { container } = render(<TodayPanel home={h} current={null} onOpen={() => {}} />);
    expect(container.querySelector(".n-le")?.textContent).toBe("진행 중인 알람 없음");
    expect(container.querySelector(".n-cnt")).toBeNull();
  });
});

describe("D-642 R5 완료 탭 = 서버 줄 값", () => {
  it("예외 보고서 줄", () => {
    const h = load<Home>("home-d528");
    h.recent_reports = h.recent_reports.map((r) => ({ ...r, cause: "원인 미상이며, 잠금 확인(1.2) 및 고온부 확인과 온도 기록(4.2), 흡기 필터 덮개 볼트(EX-R-14001) 이상 없음", line: "예외상황 · 흡기 필터 덮개 볼트 → 덮개 볼트 조임" }));
    const { container } = render(<TodayPanel home={h} current={null} onOpen={() => {}} />);
    fireEvent.click([...container.querySelectorAll(".tabs button")][1]);
    expect(texts(container, ".n-li.ro b")).toEqual(h.recent_reports.map((r) => `${r.equipment_id} · 예외상황 · 흡기 필터 덮개 볼트 → 덮개 볼트 조임`));
  });
});

describe("D-637 F13 완료 탭(최근 내 보고서)", () => {
  it("확정 원인은 짧게 — 괄호 · 근거 번호 없음", () => {
    const h = load<Home>("home-d528");
    h.recent_reports = h.recent_reports.map((r) => ({ ...r, cause: "원인 미상, 잠금 확인(1.2) 및 고온부 확인과 온도 기록(4.2), 흡기 필터 덮개 볼트(EX-R-14001) 이상 없음, 근거 사례 H-0232, H-0197, H-0155 참고" }));
    const { container } = render(<TodayPanel home={h} current={null} onOpen={() => {}} />);
    fireEvent.click([...container.querySelectorAll(".tabs button")][1]);
    const bs = texts(container, ".n-li.ro b");
    expect(bs.length).toBe(h.recent_reports.length);
    bs.forEach((b, i) => expect(b).toBe(`${h.recent_reports[i].equipment_id} · 원인 미상`));
  });
});

describe("홈 내 알람 · 안내 문장", () => {
  it("내 알람 카드 한 줄: 계통 · 값 · 경계 · 방향(완료 행은 없음) · 안전 수칙은 제목만", () => {
    const h = load<Home>("home-d528");
    const { container } = render(<HomeScreen home={h} onOpen={() => {}} />);
    const a = h.alarms.find((x) => x.status === "new")!;
    const row = container.querySelector(`[data-alarm="${a.alarm_id}"]`)!;
    expect(texts(row, ".n-vals > *")).toEqual(["냉각 계통", val(a.alarm.value, a.alarm.unit), val(a.alarm.boundary, a.alarm.unit), "경계 아래"]);
    const done = h.alarms.find((x) => x.status === "done")!;
    expect(container.querySelector(`[data-alarm="${done.alarm_id}"] .n-vals`)).toBeNull();
    expect(container.querySelector(".safe")?.textContent).toBe("잠금을 먼저 확인합니다");
  });

  it("반려 칸: 사유 · 파트장 · 시각 · 「이어서 하기」, 설명 문장 없음", () => {
    const h = load<Home>("home-rejected");
    const r = h.recent_reports.find((x) => x.status === "rejected")!;
    const { container } = render(<HomeScreen home={h} onOpen={() => {}} />);
    expect(container.querySelector(".banner.bad div")?.textContent).toMatch(new RegExp(`^반려 사유 · ${r.rejected_by_name} \\d\\d:\\d\\d${r.reject_reason}이어서 하기$`)); // 답장 001-zzx 다(가이드 33쪽): 「이어서 하기」는 사유 상자 안
    expect(container.textContent).not.toContain("이어서 하기\"로");
    expect(texts(container, ".btn.full")).toEqual(["이어서 하기"]);
  });
});

describe("D-546 U5 ML 알람 카드(꼴 01)", () => {
  // ML 유형으로 정한 알람(alarm.origin = ml_type): 경계 값 줄 자리 = 「ML 예측」 · 예측 유형 · 빨간 위험도(설명 문장 0)
  const mlHome = () => {
    const h = load<Home>("home-d528");
    const a = h.alarms.find((x) => x.status !== "done")!;
    a.alarm = { ...a.alarm, origin: "ml_type", crossed: false };
    a.ml = { type: "HDF", type_name: "열 방출 고장(HDF)", risk: 0.82 };
    return { h, a };
  };
  it("왼쪽 칸: 「ML 예측」 · 예측 유형 / 다음 줄 빨간 위험도", () => {
    const { h, a } = mlHome();
    const { container } = render(<TodayPanel home={h} current={null} onOpen={() => {}} />);
    const row = container.querySelector(`button.n-li[data-alarm="${a.alarm_id}"]`)!;
    const lines = [...row.querySelectorAll(".n-lv")];
    expect(texts(lines[0], ":scope > span")).toEqual(["ML 예측", "열 방출 고장(HDF)"]);
    expect(lines[0].querySelector(".mlk .ic")?.getAttribute("class")).toContain("lucide-sparkles");
    expect(texts(lines[1], ":scope > span")).toEqual(["위험도 0.82"]);
    expect(lines[1].querySelector("span")?.className).toBe("bad");
  });
  it("홈 내 알람 값 줄: 계통 · 「ML 예측」 · 예측 유형 · 빨간 위험도 / 경계 알람은 그대로", () => {
    const { h, a } = mlHome();
    const { container } = render(<HomeScreen home={h} onOpen={() => {}} />);
    const vals = container.querySelector(`.al[data-alarm="${a.alarm_id}"] .n-vals`)!;
    expect(vals.className).toBe("n-vals ml");
    expect(texts(vals, ":scope > *")).toEqual([a.system!, "ML 예측", "열 방출 고장(HDF)", "위험도 0.82"]);
    expect(vals.querySelector("b")?.className).toBe("bad");
    const other = h.alarms.find((x) => x.status !== "done" && x.alarm_id !== a.alarm_id);
    if (other) expect(container.querySelector(`.al[data-alarm="${other.alarm_id}"] .n-vals`)?.className).toBe("n-vals");
  });
});

describe("G3 대조: 왼쪽 칸 상태 줄", () => {
  it("보낸 호출 답 전이면 '진행 중 · 파트장 호출' · 숙련자 인계면 '진행 중 · 숙련자 인계'", () => {
    const h = load<Home>("home-in-progress");
    const live = h.alarms.filter((x) => x.status === "in_progress");
    live[0].call_sent = "approval";
    const { container } = render(<TodayPanel home={h} current={null} onOpen={() => {}} />);
    const ls = (id: number) => container.querySelector(`button.n-li[data-alarm="${id}"] .n-ls`)?.textContent ?? "";
    expect(ls(live[0].alarm_id)).toMatch(/진행 중 · 파트장 호출$/);
    cleanup();
    live[0].call_sent = "handover";
    const r = render(<TodayPanel home={h} current={null} onOpen={() => {}} />).container;
    expect(r.querySelector(`button.n-li[data-alarm="${live[0].alarm_id}"] .n-ls`)?.textContent).toMatch(/진행 중 · 숙련자 인계$/);
  });

  it("[예외] 진행 세션이면 '진행 중 · 예외 조치'(정답지 17쪽) · 호출을 보낸 동안은 '파트장 호출'(16쪽)", () => {
    const h = load<Home>("home-in-progress");
    const live = h.alarms.filter((x) => x.status === "in_progress");
    Object.assign(live[0], { call_sent: null, exception_progress: true });
    const { container } = render(<TodayPanel home={h} current={null} onOpen={() => {}} />);
    const ls = (root: ParentNode) => root.querySelector(`button.n-li[data-alarm="${live[0].alarm_id}"] .n-ls`)?.textContent ?? "";
    expect(ls(container)).toMatch(/진행 중 · 예외 조치$/);
    cleanup();
    live[0].call_sent = "approval";
    expect(ls(render(<TodayPanel home={h} current={null} onOpen={() => {}} />).container)).toMatch(/진행 중 · 파트장 호출$/);
  });
});
