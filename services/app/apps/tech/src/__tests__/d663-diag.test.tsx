// D-663 S1(재시연 5 · 표시 · 근거 고르는 곳만 · Pack 셈 · 순위 손 0): 순위 줄 = '40% · 5건 중 2건'(기본 점수 없이 k ÷ n) ·
// 이 점검을 고른 이유도 짧게 · 근거 칸 = 1위 후보로 확정된 지난 같은 알람 기록 카드(서버 top_cards) · 다른 설비 참고 카드는 화면에서 뺌.

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SessionWithPreview } from "@c1a15/ui";
import { DiagnosisScreen } from "../screens/Diagnosis";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = (name: string) => structuredClone(fx[`./fixtures/${name}.json`]) as SessionWithPreview;
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");

const card = (id: string, month: number) => ({
  record_id: id, equipment_id: "M-01", month, similarity_pct: null, outcome_type: "원인 확정",
  cause_code: "CL-1", outcome_label: "냉각 필터 막힘", action_name: "흡기 필터 교체", downtime_min: 30,
});
function withCounts(probabilities: Record<string, number>, counts: Record<string, number>, total: number, scope = "equipment") {
  const s = load("session-before-lock");
  s.probabilities = probabilities;
  Object.assign(s, { equipment_counts: counts, equipment_total: total, count_scope: scope, count_prior: 1 });
  return s;
}
// M-01 실제 출력(Pack 34/22/22/22 · 이 설비 원인 확정 5건 2/1/1/1) · 근거 카드 = 서버가 이력에서 고른 냉각 필터 막힘 2건
const m01 = () => Object.assign(withCounts({ "CL-1": 34, "CL-2": 22, "CL-3": 22, "CL-4": 22 }, { "CL-1": 2, "CL-2": 1, "CL-3": 1, "CL-4": 1 }, 5), {
  top_cards: [card("H-0210", 9), card("H-0191", 9)],
});

afterEach(cleanup);

describe("S1 순위 줄 = '40% · 5건 중 2건'만", () => {
  it("M-01: % = k ÷ n(기본 점수 없이) · '지난 같은 알람' 문구 0 · 순서는 Pack 순위 그대로", () => {
    const { container } = render(<DiagnosisScreen s={m01()} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(container, ".rk b")[0]).toBe("냉각 필터 막힘");
    expect(texts(container, ".rk .rs")).toEqual(["40% · 5건 중 2건", "20% · 5건 중 1건", "20% · 5건 중 1건", "20% · 5건 중 1건"]);
    expect(container.textContent ?? "").not.toMatch(/지난 같은 알람|34%|22%/);
  });

  it("M-03 6건 중 6건 = 100% · 넓혀 센 셈도 같은 꼴(까닭 줄은 그대로)", () => {
    const a = render(<DiagnosisScreen s={withCounts({ "CL-1": 70, "CL-2": 10, "CL-3": 10, "CL-4": 10 }, { "CL-1": 6, "CL-2": 0, "CL-3": 0, "CL-4": 0 }, 6)} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(a.container, ".rk .rs")[0]).toBe("100% · 6건 중 6건");
    cleanup();
    const g = withCounts({ "CL-1": 52, "CL-2": 23, "CL-3": 10, "CL-4": 15 }, { "CL-1": 57, "CL-2": 25, "CL-3": 10, "CL-4": 16 }, 108, "group");
    const b = render(<DiagnosisScreen s={g} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(b.container, ".rk .rs")).toEqual(["53% · 108건 중 57건", "23% · 108건 중 25건", "15% · 108건 중 16건", "9% · 108건 중 10건"]);
    expect(b.container.querySelector('[data-part="rank-wide"]')?.textContent).toBe("이 설비 기록이 3건 미만이라 같은 증상 전체 기록으로 셈");
  });

  it("이 점검을 고른 이유 = '냉각 필터 막힘 40% · 5건 중 2건 · 약 3분'", () => {
    const { container } = render(<DiagnosisScreen s={m01()} onRecord={() => {}} onStart={() => {}} />);
    expect(container.querySelector('[data-part="pick-rule"]')?.textContent).toBe("냉각 필터 막힘 40% · 5건 중 2건 · 약 3분");
  });
});

describe("S1 근거 칸 = 1위 후보로 확정된 기록 카드", () => {
  it("M-01: '근거 · 냉각 필터 막힘 2건' · 카드 2장(H-0210 · H-0191) · 유사 % 0 · 다른 설비 참고 칸 0", () => {
    const s = m01();
    const { container } = render(<DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />);
    const top = container.querySelector('[data-part="top-ref"]')!;
    expect(top.querySelector(".cd-h b")?.textContent).toBe("근거 · 냉각 필터 막힘 2건");
    expect(texts(top, ".hc-t b")).toEqual(["H-0210", "H-0191"]);
    expect(top.textContent ?? "").not.toMatch(/유사/);
    expect(container.querySelector('[data-part="other-ref"]')).toBeNull();
    for (const c of s.similar_cards) expect(container.textContent ?? "").not.toContain(c.record_id);
    expect(container.textContent ?? "").not.toMatch(/다른 설비/);
  });

  it("D-663 덧1 M-03: 카드 수 = k 6 · 알람 없는 시연 정비 줄(H-0302)도 카드 — 정지 시간 칸 비움 · '정비 기록'", () => {
    const demo = { ...card("H-0302", 11), equipment_id: "M-03", downtime_min: null };
    const s = Object.assign(withCounts({ "CL-1": 70, "CL-2": 10, "CL-3": 10, "CL-4": 10 }, { "CL-1": 6, "CL-2": 0, "CL-3": 0, "CL-4": 0 }, 6), {
      top_cards: [demo, ...["H-0246", "H-0227", "H-0212", "H-0193", "H-0174"].map((id) => ({ ...card(id, 9), equipment_id: "M-03" }))],
    });
    const { container } = render(<DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />);
    const top = container.querySelector('[data-part="top-ref"]')!;
    expect(top.querySelector(".cd-h b")?.textContent).toBe("근거 · 냉각 필터 막힘 6건");
    expect(top.querySelectorAll(".hc")).toHaveLength(6);
    const first = top.querySelector(".hc")!;
    expect(first.querySelector(".hc-t b")?.textContent).toBe("H-0302");
    expect(first.querySelector(".hc > span")?.textContent).toBe("흡기 필터 교체 · 정비 기록");
    expect(top.textContent ?? "").not.toMatch(/null|정지 분/);
  });

  it("넓혀 센 셈에서 카드가 k보다 적으면 '최근 n건' · 카드 없음(옛 세션)이면 근거 칸 0", () => {
    const g = Object.assign(withCounts({ "CL-1": 52, "CL-2": 23, "CL-3": 10, "CL-4": 15 }, { "CL-1": 57, "CL-2": 25, "CL-3": 10, "CL-4": 16 }, 108, "group"), {
      top_cards: [card("H-0300", 10), card("H-0210", 9), card("H-0191", 9)],
    });
    const a = render(<DiagnosisScreen s={g} onRecord={() => {}} onStart={() => {}} />);
    expect(a.container.querySelector('[data-part="top-ref"] .cd-h b')?.textContent).toBe("근거 · 냉각 필터 막힘 최근 3건");
    cleanup();
    const old = { ...m01(), top_cards: undefined } as unknown as SessionWithPreview;
    const b = render(<DiagnosisScreen s={old} onRecord={() => {}} onStart={() => {}} />);
    expect(b.container.querySelector('[data-part="top-ref"]')).toBeNull();
    expect(b.container.querySelector('[data-part="other-ref"]')).toBeNull();
  });
});
