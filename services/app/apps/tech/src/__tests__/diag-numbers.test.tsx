// 사람 리허설 피드백(11-08) F1 · F8 · F2 → 재시연 3(R2): 옛 세션(n · k 칸 없음)의 진단 화면. 셈 손 0(표시 · 글만).
// 순위에 % 0 · 오른쪽 수 자리 0 · 셈 식 줄 0 · 옛 셈의 100% 까닭 줄 0 · 고른 이유는 서버 글 그대로(말 줄은 n · k가 있을 때만).

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SessionWithPreview } from "@c1a15/ui";
import { DiagnosisScreen } from "../screens/Diagnosis";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = (name: string) => structuredClone(fx[`./fixtures/${name}.json`]) as SessionWithPreview;
const texts = (root: ParentNode, sel: string) => [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");

// 리허설 화면 그대로의 숫자(img-01 M-01 · img-09 M-03)
function shot(probabilities: Record<string, number>): SessionWithPreview {
  const s = load("session-before-lock");
  s.probabilities = probabilities;
  s.evidence = { "CL-1": 4, "CL-2": 0, "CL-3": 6, "CL-4": 0 };
  s.preview = { ...s.preview, reason: `냉각 필터 막힘 가능성이 ${probabilities["CL-1"]}%로 가장 높고, 4.3절 점검은 약 3분 · A등급입니다.` };
  return s;
}
const M01 = { "CL-1": 40, "CL-2": 20, "CL-3": 20, "CL-4": 20 };
const M03 = { "CL-1": 100, "CL-2": 0, "CL-3": 0, "CL-4": 0 };

afterEach(cleanup);

describe("옛 세션 순위: % · 오른쪽 수 · 셈 식 줄 · 100% 까닭 줄 0", () => {
  it("순위 이름 · 막대만 · '근거 n' · '비슷한 기록' 글자 0", () => {
    const { container } = render(<DiagnosisScreen s={shot(M01)} onRecord={() => {}} onStart={() => {}} />);
    expect(texts(container, ".rk b")).toEqual(["냉각 필터 막힘", "냉각 팬 열화", "냉각수 부족", "냉각 설정 오류"]);
    expect(texts(container, ".rk .rs")).toEqual([]);
    expect(container.querySelectorAll(".rk .rv").length).toBe(0);
    expect(container.querySelector('[data-part="rank-source"]')).toBeNull();
    const rank = container.querySelector(".cd")!.textContent ?? "";
    expect(rank).not.toMatch(/%|근거 \d|비슷한/);
  });

  it("옛 세션이 100%여도 까닭 줄 0 · 지난 화면(T15)도 같음 · 화면 글에 내부 번호 0", () => {
    const { container } = render(<DiagnosisScreen s={shot(M03)} onRecord={() => {}} onStart={() => {}} past />);
    expect(container.querySelector('[data-part="rank-full"]')).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/100%인 까닭|모두 이 원인|D-\d|F\d\b/);
  });
});

describe("옛 세션 이 점검을 고른 이유: 서버 글 그대로 · 셈 줄 0", () => {
  it("지시 카드 · B 등급 모두 이유 글 그대로 · 고르는 셈 줄 0", () => {
    const s = shot(M01);
    const a = render(<DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />);
    expect(a.container.querySelector(".why span")?.textContent).toBe(s.preview.reason);
    expect(a.container.querySelector('[data-part="pick-rule"]')).toBeNull();
    expect(a.container.textContent ?? "").not.toMatch(/고르는 셈|÷/);
    cleanup();
    const b = shot(M01);
    b.preview = { ...b.preview, kind: "instruct_after_checklist", grade: "B", minutes: null };
    const rb = render(<DiagnosisScreen s={b} onRecord={() => {}} onStart={() => {}} />);
    expect(rb.container.querySelector('[data-part="pick-rule"]')).toBeNull();
  });

  it("호출 카드 제목은 그대로", () => {
    const fc = load("session-first-call");
    const a = render(<DiagnosisScreen s={fc} onRecord={() => {}} onStart={() => {}} />);
    expect(a.container.querySelector(".why b")?.textContent).toBe("호출이 필요한 이유");
    expect(a.container.querySelector('[data-part="pick-rule"]')).toBeNull();
  });
});
