// D-528 정비자 14 보고서 최종본(가이드 19 · 20쪽): 확정 뒤 「최종본 보기」 · 홈 최근 보고서 줄 「최종본 보기」 · 최종본 화면(「보고서로」 · 「출력」).
// final-report.json = 시험 DB 실제 응답 한 건(server/tests/test_final_report.py FINAL_FIXTURE_OUT, 파트장 앱과 같은 값).

import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api, Home, Schemas, SessionView } from "@c1a15/ui";
import { FinalScreen } from "../screens/Final";
import { ReportScreen } from "../screens/Flow";
import { HomeScreen } from "../screens/Home";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = <T,>(name: string) => structuredClone(fx[`./fixtures/${name}.json`]) as T;
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");
const btn = (root: ParentNode, label: string) => [...root.querySelectorAll(".btn")].find((b) => b.textContent?.trim() === label) as HTMLElement;

afterEach(cleanup);

describe("「최종본 보기」 여는 곳 둘", () => {
  it("확정한 보고서: 확정 한 줄 아래 「최종본 보기」 → 그 보고서 번호 · 「홈으로」 그대로", () => {
    const s = load<SessionView>("s03-report-confirmed");
    const report = { ...s.report!, report_id: 6 };
    const onFinal = vi.fn();
    const { container } = render(<ReportScreen report={report} busy={false} send={() => {}} onHome={() => {}} onFinal={onFinal} />);
    expect(texts(container, ".btn.lg")).toEqual(["최종본 보기", "홈으로"]);
    fireEvent.click(btn(container, "최종본 보기"));
    expect(onFinal).toHaveBeenCalledWith(6);
  });

  it("초안(확정 전)에는 「최종본 보기」 없음", () => {
    const s = load<SessionView>("s03-report-draft");
    const { container } = render(<ReportScreen report={{ ...s.report!, report_id: null }} busy={false} send={() => {}} onHome={() => {}} onFinal={() => {}} />);
    expect(btn(container, "최종본 보기")).toBeUndefined();
  });

  it("홈 최근 내 보고서 줄: 확정 · 승인 보고서에 「최종본 보기」(반려 줄은 없음)", () => {
    const h = load<Home>("home-rejected");
    const onFinal = vi.fn();
    const { container } = render(<HomeScreen home={h} onOpen={() => {}} onFinal={onFinal} />);
    const rows = [...container.querySelectorAll(".rl")];
    h.recent_reports.forEach((r, i) => expect(rows[i].querySelector(".lk2") !== null).toBe(r.status !== "rejected"));
    const i = h.recent_reports.findIndex((r) => r.status !== "rejected");
    fireEvent.click(rows[i].querySelector(".lk2")!);
    expect(onFinal).toHaveBeenCalledWith(h.recent_reports[i].report_id);
  });
});

describe("D-642 R5 홈 최근 내 보고서 = 서버 줄 값", () => {
  it("예외로 끝난 보고서는 '예외상황 · 점검 → 조치', 아니면 원인 이름(원인 문장 첫 구절 '원인 미상이며' 0)", () => {
    const h = load<Home>("home-rejected");
    h.recent_reports = h.recent_reports.map((r, i) => ({ ...r, cause: "원인 미상이며, 잠금 확인(1.2) 및 고온부 확인과 온도 기록(4.2), 흡기 필터 덮개 볼트(EX-R-14001) 이상 없음", line: i === 0 ? "예외상황 · 흡기 필터 덮개 볼트 → 덮개 볼트 조임" : "냉각 필터 막힘" }));
    const { container } = render(<HomeScreen home={h} onOpen={() => {}} onFinal={() => {}} />);
    const lines = texts(container, ".rl .rl-t");
    expect(lines[0].startsWith(`${h.recent_reports[0].equipment_id} · 예외상황 · 흡기 필터 덮개 볼트 → 덮개 볼트 조임`)).toBe(true);
    expect(lines[1].startsWith(`${h.recent_reports[1].equipment_id} · 냉각 필터 막힘`)).toBe(true);
    expect(lines.join(" ")).not.toMatch(/원인 미상이며|EX-R/);
  });
});

describe("D-637 F13 홈 최근 내 보고서 한 줄", () => {
  it("설비 · 확정 원인 짧게 · 시각 · 상태만(긴 글 · 근거 번호는 줄에 없음, 「최종본 보기」 안에서)", () => {
    const h = load<Home>("home-rejected");
    h.recent_reports = h.recent_reports.map((r, i) => ({ ...r, cause: i === 0 ? "원인 미상, 잠금 확인(1.2) 및 고온부 확인과 온도 기록(4.2), 흡기 필터 덮개 볼트(EX-R-14001) 이상 없음, 근거 사례 H-0232, H-0197, H-0155 참고" : "흡기 필터 표면의 기름때가 절반 이상 덮여 냉각 필터가 막힘(4.3, H-0197, H-0232, H-0155)." }));
    const { container } = render(<HomeScreen home={h} onOpen={() => {}} onFinal={() => {}} />);
    const rows = [...container.querySelectorAll(".rl")];
    expect(rows.length).toBe(h.recent_reports.length);
    rows.forEach((row, i) => {
      const r = h.recent_reports[i];
      const line = row.querySelector(".rl-t")?.textContent ?? "";
      expect(line.startsWith(`${r.equipment_id} · `)).toBe(true);
      expect(line).not.toMatch(/H-\d|EX-R|\(/);
      expect([...line.split(" · ")[1]].length).toBeLessThanOrEqual(18);
      expect(row.querySelector(".ch")).not.toBeNull();
    });
    expect(rows[0].querySelector(".rl-t")?.textContent).toMatch(/^.+ · 원인 미상( · .+)?$/);
    expect(rows[1].querySelector(".rl-t")?.textContent).toMatch(/^.+ · …[^·]*냉각 필터가 막힘( · .+)?$/);
  });
});

describe("정비자 최종본 화면", () => {
  it("본인 보고서 최종본: 문서(파트장과 같은 꼴) · 「보고서로」 → 그 세션 · 「출력」은 동작 0", async () => {
    const f = load<Schemas["FinalReport"]>("final-report");
    const GET = vi.fn(() => Promise.resolve({ data: f, error: undefined, response: { status: 200 } }));
    const onReport = vi.fn();
    let r: ReturnType<typeof render>;
    await act(async () => {
      r = render(<FinalScreen api={{ GET } as unknown as Api} rid={f.report_id} onReport={onReport} />);
    });
    const { container } = r!;
    expect(GET).toHaveBeenCalledWith("/api/tech/reports/{report_id}/final", { params: { path: { report_id: f.report_id } } });
    expect(texts(container, ".fbar .btn")).toEqual(["보고서로", "출력"]);
    expect(container.querySelector(".fd-h h1")?.textContent).toBe(`정비 보고서 ${f.equipment_id}`);
    expect(texts(container, ".fdoc h2")).toEqual(["1. 이상 내용 · 센서 근거", "2. 원인 분석", "3. 조치 경과", "4. 조치 결과", "5. 미확인 · 주간 요청"]);
    // 답장 001-zzx-2(가이드 20 · 21 · 31쪽): 머리 '작성 이름(근무)' · 2번 구역 '확정 근거' 줄 · 4번 재가동 뒤 경계 값
    expect(container.querySelector(".fd-h p")?.textContent).toMatch(/ · 작성 김 정비\(야간 정비\)$/);
    expect(texts(container, ".fd-note").join(" ")).toContain('확정 원인 냉각 필터 막힘 · 확정 근거 흡기 필터 육안 점검(4.3) 결과 이상 · 메모 "기름때가 필터 표면 절반 넘게 덮음"');
    expect(container.textContent).toContain("온도 차 9.0 K · 경계 8.2 K 이내");
    expect(container.querySelector(".fd-ex")).toBeNull(); // 예외 상황 기록이 없으면 6번 구역 없음
    fireEvent.click(btn(container, "출력"));
    fireEvent.click(btn(container, "출력"));
    expect(GET).toHaveBeenCalledTimes(1);
    fireEvent.click(btn(container, "보고서로"));
    expect(onReport).toHaveBeenCalledWith(f.session_id);
  });

  it("불러오기 실패(남의 보고서 403 등): 실패 줄 · 문서 없음 · 「보고서로」 잠김", async () => {
    const GET = vi.fn(() => Promise.resolve({ data: undefined, error: { detail: "not_own_report" }, response: { status: 403 } }));
    let r: ReturnType<typeof render>;
    await act(async () => {
      r = render(<FinalScreen api={{ GET } as unknown as Api} rid={9} onReport={() => {}} />);
    });
    expect(r!.container.querySelector(".banner.bad b")?.textContent).toBe("최종본을 불러오지 못했습니다");
    expect(r!.container.querySelector(".fdoc")).toBeNull();
    expect(btn(r!.container, "보고서로").className).toContain("dis");
  });
});
