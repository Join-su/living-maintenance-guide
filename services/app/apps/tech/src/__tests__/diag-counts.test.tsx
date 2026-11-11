// 재시연 3(R1 · R2, 표시 · 글만 · Pack 셈 손 0): 순위 · 이 점검을 고른 이유에서 % 빼고 건수만('지난 같은 알람 n건 중 k건' + 막대).
// 기록이 적으면 '기록 n건 · 확정 아님' 한 줄 · 고른 이유는 말로(record_counts 값으로 짠 한 줄) · 셈 식 줄 뺌.
// 근거 칸 = 순위를 낸 그 설비의 지난 같은 알람 기록(원인별 건수 = 순위 줄) · 다른 설비 센서 비슷한 기록은 접힌 '다른 설비 참고'로만.
// n · k = Pack 판정 출력 record_counts(앱 서버가 equipment_counts · total · count_scope · count_prior로 실음) · 칸이 없으면(옛 세션) 숫자 자리 숨김 — 지어낸 숫자 0.

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SessionWithPreview } from "@c1a15/ui";
import { DiagnosisScreen } from "../screens/Diagnosis";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = (name: string) => structuredClone(fx[`./fixtures/${name}.json`]) as SessionWithPreview;
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");
const widths = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>(".rk .rb i")].map((e) => e.style.width);

// Pack 실제 출력 꼴(record_counts): 확률은 Pack 값 그대로 넣되 화면엔 안 나와야 함
function withCounts(probabilities: Record<string, number>, counts: Record<string, number>, total: number, scope = "equipment", prior = 1) {
  const s = load("session-before-lock");
  s.probabilities = probabilities;
  s.evidence = { "CL-1": 4, "CL-2": 0, "CL-3": 6, "CL-4": 0 };
  Object.assign(s, { equipment_counts: counts, equipment_total: total, count_scope: scope, count_prior: prior });
  return s;
}
// M-01 실제 출력(저장소 그래프 n 5 · 2/1/1/1 → 34/22/22/22) · M-03 시연 그래프(n 6 · 6/0/0/0 → 70/10/10/10)
const m01 = () => withCounts({ "CL-1": 34, "CL-2": 22, "CL-3": 22, "CL-4": 22 }, { "CL-1": 2, "CL-2": 1, "CL-3": 1, "CL-4": 1 }, 5);
const m03 = () => withCounts({ "CL-1": 70, "CL-2": 10, "CL-3": 10, "CL-4": 10 }, { "CL-1": 6, "CL-2": 0, "CL-3": 0, "CL-4": 0 }, 6);

afterEach(cleanup);

describe("R2 순위: % 빼고 건수 + 막대", () => {
  it("M-03: '지난 같은 알람 6건 중 6건' · 막대 = 건수 비율(100%) · 화면 글에 % 0 · '기록 6건 · 확정 아님'", () => {
    const { container } = render(<DiagnosisScreen s={m03()} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(container, ".rk b")).toEqual(["냉각 필터 막힘", "냉각 팬 열화", "냉각수 부족", "냉각 설정 오류"]);
    expect(texts(container, ".rk .rs")).toEqual([
      "100% · 6건 중 6건",
      "0% · 6건 중 0건",
      "0% · 6건 중 0건",
      "0% · 6건 중 0건",
    ]);
    expect(widths(container)).toEqual(["100%", "0%", "0%", "0%"]);
    expect(container.querySelectorAll(".rk .rv").length).toBe(0);
    expect(container.querySelector('[data-part="rank-few"]')?.textContent).toBe("기록 6건 · 확정 아님");
    expect(container.querySelector('[data-part="rank-source"]')).toBeNull(); // 셈 식 줄 0
    const rank = container.querySelector(".cd")!.textContent ?? "";
    expect(rank).not.toMatch(/확률|기본 1건|70%|비슷한/); // D-663: % = k ÷ n만(Pack 값 0)
  });

  it("M-01: 2/1/1/1 · 막대 40 · 20 · 20 · 20 · 순서는 Pack 순위 그대로", () => {
    const { container } = render(<DiagnosisScreen s={m01()} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(container, ".rk .rs")).toEqual([
      "40% · 5건 중 2건",
      "20% · 5건 중 1건",
      "20% · 5건 중 1건",
      "20% · 5건 중 1건",
    ]);
    expect(widths(container)).toEqual(["40%", "20%", "20%", "20%"]);
    expect(container.querySelector('[data-part="rank-few"]')?.textContent).toBe("기록 5건 · 확정 아님");
  });

  it("기록이 많으면(10건 이상) 확정 아님 줄 0 · 넓혀 센 셈(group)은 '같은 증상 전체 기록 n건 중 k건' + 까닭 한 줄", () => {
    const s = withCounts({ "CL-1": 52, "CL-2": 23, "CL-3": 10, "CL-4": 15 }, { "CL-1": 57, "CL-2": 25, "CL-3": 10, "CL-4": 16 }, 108, "group", 1);
    const { container } = render(<DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(container, ".rk .rs")).toEqual([
      "53% · 108건 중 57건",
      "23% · 108건 중 25건",
      "15% · 108건 중 16건",
      "9% · 108건 중 10건",
    ]);
    expect(container.querySelector('[data-part="rank-few"]')).toBeNull();
    expect(container.querySelector('[data-part="rank-wide"]')?.textContent).toBe("이 설비 기록이 3건 미만이라 같은 증상 전체 기록으로 셈");
    expect(container.querySelector(".cd")!.textContent ?? "").not.toMatch(/52%/);
  });

  it("n · k 칸이 없으면(옛 세션) 숫자 자리 숨김 · % 0 · 확정 아님 줄 0", () => {
    const s = m01();
    const old = { ...s, equipment_counts: undefined, equipment_total: undefined } as SessionWithPreview;
    const { container } = render(<DiagnosisScreen s={old} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(container, ".rk .rs")).toEqual([]);
    expect(container.querySelector(".cd")!.textContent ?? "").not.toMatch(/%/);
    expect(container.querySelector('[data-part="rank-few"]')).toBeNull();
  });
});

describe("R2 이 점검을 고른 이유: 말로 한 줄(건수 · 분) · % 0", () => {
  it("M-03: '지난 같은 알람 6건 모두 냉각 필터 막힘 · 약 3분이면 확인' · 서버 이유 글(% 들어 있음)은 안 보임", () => {
    const s = m03();
    const { container } = render(<DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />);
    expect(container.querySelector('[data-part="pick-rule"]')?.textContent).toBe(
      "냉각 필터 막힘 100% · 6건 중 6건 · 약 3분",
    );
    const why = container.querySelector(".why")!.textContent ?? "";
    expect(why).not.toMatch(/÷|고르는 셈|70%/);
    expect(why).not.toContain(s.preview.reason);
  });

  it("M-01: 'n건 중 k건' · 분을 모르면 분 부분만 뺌 · 넓혀 센 셈은 그 말로", () => {
    const s = m01();
    const a = render(<DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />);
    expect(a.container.querySelector('[data-part="pick-rule"]')?.textContent).toBe(
      "냉각 필터 막힘 40% · 5건 중 2건 · 약 3분",
    );
    cleanup();
    const b = m01();
    b.preview = { ...b.preview, kind: "instruct_after_checklist", grade: "B", minutes: null };
    const rb = render(<DiagnosisScreen s={b} onRecord={() => {}} onStart={() => {}} />);
    expect(rb.container.querySelector('[data-part="pick-rule"]')?.textContent).toBe(
      "냉각 필터 막힘 40% · 5건 중 2건",
    );
    cleanup();
    const g = withCounts({ "CL-1": 52, "CL-2": 23, "CL-3": 10, "CL-4": 15 }, { "CL-1": 57, "CL-2": 25, "CL-3": 10, "CL-4": 16 }, 108, "group", 1);
    const rg = render(<DiagnosisScreen s={g} onRecord={() => {}} onStart={() => {}} />);
    expect(rg.container.querySelector('[data-part="pick-rule"]')?.textContent).toBe(
      "냉각 필터 막힘 53% · 108건 중 57건 · 약 3분",
    );
  });

  it("D-652 12-f: Pack 옵션이 첫 점검을 바꿨으면(picked_by) 확률 · 건수 말 대신 '먼저 볼 점검으로 정해 둔 … — 원래 …'", () => {
    const s = withCounts({ "CL-1": 20, "CL-2": 20, "CL-3": 10, "CL-4": 50 }, { "CL-1": 1, "CL-2": 1, "CL-3": 0, "CL-4": 4 }, 6);
    const pb = { option: "check_first_cl", section: "4.3", title: "흡기 필터 육안 점검", instead_of: "4.6", instead_title: "냉각 설정값 확인" };
    const opt = { ...s, preview: { ...s.preview, section: "4.3", title: "흡기 필터 육안 점검", picked_by: pb } };
    const r = render(<DiagnosisScreen s={opt} onRecord={() => {}} onStart={() => {}} />);
    expect(r.container.querySelector('[data-part="pick-rule"]')?.textContent).toBe(
      "먼저 볼 점검으로 정해 둔 흡기 필터 육안 점검(4.3절) — 원래 냉각 설정값 확인(4.6절)",
    );
  });

  it("호출 카드 · 진단에서 고른 점검 · 원인 모름 · 옛 세션은 말 줄 0(서버 이유 글 그대로)", () => {
    const fc = load("session-first-call");
    const a = render(<DiagnosisScreen s={fc} onRecord={() => {}} onStart={() => {}} />);
    expect(a.container.querySelector('[data-part="pick-rule"]')).toBeNull();
    cleanup();
    const s = m03();
    const picked = { ...s, preview: { ...s.preview, picked: { section: "4.4", title: "냉각 팬 분해 점검", grade: "C" } } };
    const b = render(<DiagnosisScreen s={picked} onRecord={() => {}} onStart={() => {}} />);
    expect(b.container.querySelector('[data-part="pick-rule"]')).toBeNull();
    cleanup();
    const nocause = { ...s, preview: { ...s.preview, cause: null } };
    const c = render(<DiagnosisScreen s={nocause} onRecord={() => {}} onStart={() => {}} />);
    expect(c.container.querySelector(".why span")?.textContent).toBe(s.preview.reason);
    cleanup();
    const old = { ...s, equipment_counts: undefined, equipment_total: undefined } as SessionWithPreview;
    const d = render(<DiagnosisScreen s={old} onRecord={() => {}} onStart={() => {}} />);
    expect(d.container.querySelector('[data-part="pick-rule"]')).toBeNull();
    expect(d.container.querySelector(".why span")?.textContent).toBe(s.preview.reason);
  });
});

describe("R1 근거 칸(D-663): 다른 설비 센서 비슷한 기록은 화면에서 뺌", () => {
  it("접힌 '다른 설비 참고' 칸도 0 · 비슷한 과거 기록 글 0", () => {
    const s = m03();
    const { container } = render(<DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />);
    expect(container.querySelector('details[data-part="other-ref"]')).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/비슷한 과거 기록|다른 설비 참고/);
  });

  it("지난 화면도 같은 글 · 화면 글에 내부 번호 0", () => {
    const { container } = render(<DiagnosisScreen s={m03()} onRecord={() => {}} onStart={() => {}} past />);
    expect(texts(container, ".rk .rs")[0]).toBe("100% · 6건 중 6건");
    expect(container.textContent ?? "").not.toMatch(/D-\d|R\d\b|F\d\b/);
  });
});
