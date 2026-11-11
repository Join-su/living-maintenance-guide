// P-08 component 시험: 실제 API 응답(fixtures)으로 화면을 그려 Prototype v17 구조(영역 · 버튼 · 순서 · 토큰)를 대조한다.

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  RecordModal,
  type HistoryRecord,
  type Home,
  type Instruction,
  type Notifications,
  type SessionView,
  type SessionWithPreview,
} from "@c1a15/ui";
import { emptyDraft } from "../device";
import { AlarmScreen } from "../screens/Alarm";
import { DiagnosisScreen, rankRows } from "../screens/Diagnosis";
import { HomeScreen, NotifyPanel } from "../screens/Home";
import {
  InstructionScreen,
  ManualModal,
  PendingModal,
  plainManual,
  type ManualView,
} from "../screens/Instruction";
import { LockScreen, PrecheckScreen } from "../screens/Lock";
import { stepNumber } from "../screens/Session";

const fx = import.meta.glob("./fixtures/*.json", {
  eager: true,
  import: "default",
}) as Record<string, unknown>;
function load<T>(name: string): T {
  return structuredClone(fx[`./fixtures/${name}.json`]) as T;
}
const texts = (root: ParentNode, sel: string) =>
  [...root.querySelectorAll(sel)].map((e) => e.textContent?.trim() ?? "");

afterEach(cleanup);

describe("T02 홈 · T12 종 목록", () => {
  it("새 알람이 맨 위, 행 = 상태 점 · 설비 · 증상 · 시각 · 버튼, 교대 현황 3칸", () => {
    const home = load<Home>("home-in-progress");
    home.alarms.push({
      ...home.alarms[0],
      alarm_id: 999,
      status: "new",
      session_id: null,
      progress: null,
    });
    const { container } = render(<HomeScreen home={home} onOpen={() => {}} />);
    const rows = [...container.querySelectorAll(".al")];
    expect(rows[0].querySelector(".dot")?.className).toBe("dot red");
    expect(rows[0].querySelector(".btn")?.textContent).toBe("점검 시작");
    const prog = rows.find((r) => r.querySelector(".dot.amber"))!;
    expect(prog.querySelector(".al-m span")?.textContent).toMatch(
      /· 점검 1 \/ 최대 3 진행 중$/,
    );
    expect(prog.querySelector(".btn")?.className).toContain("ghost");
    expect(texts(container, ".cd-h b")).toEqual([
      "내 알람",
      "오늘 교대 · 22:00 ~ 06:00",
      "안전 수칙",
      "최근 내 보고서",
    ]);
    expect(texts(container, ".kt span")).toEqual([
      "처리 완료",
      "진행 중",
      "파트장 호출",
    ]);
    expect(texts(container, ".kt b")).toEqual([
      "0",
      String(home.shift.in_progress),
      "0",
    ]);
  });

  it("빈 홈 · 빈 종 목록 안내(AC-37-C3)", () => {
    const { container } = render(
      <HomeScreen home={load<Home>("home-empty")} onOpen={() => {}} />,
    );
    expect(container.querySelector(".empty")?.textContent).toBe(
      "배정된 알람이 없습니다",
    );
    cleanup();
    const p = render(
      <NotifyPanel
        data={load<Notifications>("notifications-empty")}
        onItem={() => {}}
      />,
    );
    expect(p.container.querySelector(".panel .empty")?.textContent).toBe(
      "알람 · 할 일이 없습니다",
    );
  });

  it("종 목록은 종류별 묶음과 이동 버튼(AC-37-C1 · C2)", () => {
    const onItem = vi.fn();
    const data = load<Notifications>("notifications-list");
    const { container } = render(<NotifyPanel data={data} onItem={onItem} />);
    expect(texts(container, ".grp")).toEqual([
      "배정된 새 알람",
      "진행 중인 점검",
    ]);
    const buttons = [...container.querySelectorAll(".nt-item .btn")];
    expect(buttons.map((b) => b.textContent)).toEqual([
      "알람 보기",
      "이어서 하기",
    ]);
    fireEvent.click(buttons[1]);
    expect(onItem).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "in_progress" }),
    );
  });
});

describe("T03 알람", () => {
  it("증상 · 계통 칩 · 이탈 수 · 센서 표 5행 · AI 해석 · '진단 보기'가 유일한 다음 행동 · 추이 그래프 · 안내 문장 없음(D-528)", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    const onNext = vi.fn();
    const { container } = render(<AlarmScreen s={s} onNext={onNext} />);
    expect(container.querySelector(".alh h3")?.textContent).toBe(
      s.alarm.group_name,
    );
    expect(texts(container, ".alh .ch")).toEqual([
      `증상군 · ${s.alarm.system}`,
      `센서 ${s.alarm.out_of_range_count}개 이탈`,
    ]);
    expect(container.querySelector("svg.spk")).toBeNull(); // D-528 가이드 정비자 3: 경계 넘음 추이 그래프 뺌
    expect(container.querySelector(".note")).toBeNull(); // 「알람 확인 시각이 기록됩니다」 뺌
    const rows = [...container.querySelectorAll(".tbl tr")].slice(1);
    expect(rows).toHaveLength(5);
    expect(rows[0].className).toBe("down");
    expect(rows.map((r) => r.querySelector("td")?.textContent)).toEqual(
      s.alarm.sensors.map((x) => x.name),
    );
    expect(container.querySelector(".ai")?.textContent).toBe(
      s.alarm.ai_reading.text,
    );
    expect(container.querySelector(".ev")?.textContent).toContain(
      `${s.alarm.similar_count}건`,
    );
    const btns = container.querySelectorAll(".btn");
    expect(btns).toHaveLength(1);
    fireEvent.click(btns[0]);
    expect(onNext).toHaveBeenCalled();
  });
});

describe("D-546 U5 ML 알람 화면(꼴 01)", () => {
  it("머리 칩 증상군 · 「ML 예측」(파랑) · 「센서 이탈 0」(회색) · AI 해석 = 예측 유형 · 위험도 두 줄(글 0) · 진단 보기 그대로", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    s.alarm = {
      ...s.alarm,
      alarm: { ...s.alarm.alarm, origin: "ml_type", crossed: false },
      out_of_range_count: 0,
      ml: { type: "HDF", type_name: "열 방출 고장(HDF)", risk: 0.82 },
    };
    const onNext = vi.fn();
    const { container } = render(<AlarmScreen s={s} onNext={onNext} />);
    expect(texts(container, ".alh .ch")).toEqual([
      `증상군 · ${s.alarm.system}`,
      "ML 예측",
      "센서 이탈 0",
    ]);
    expect(
      [...container.querySelectorAll(".alh .ch")].map((e) => e.className),
    ).toEqual(["ch blue", "ch blue mlc", "ch gray"]);
    expect(
      container.querySelector(".alh .mlc .ic")?.getAttribute("class"),
    ).toContain("lucide-sparkles");
    expect(container.querySelector(".ai")).toBeNull(); // 해석 글 자리 = 두 줄
    expect(
      [...container.querySelectorAll(".ai-c .mlr")].map((r) =>
        texts(r, ":scope > *"),
      ),
    ).toEqual([
      ["예측 유형", "열 방출 고장(HDF)"],
      ["위험도", "0.82"],
    ]);
    expect(container.querySelector(".ai-c .mlr b.bad")?.textContent).toBe(
      "0.82",
    );
    expect(container.querySelector(".ev")?.textContent).toContain(
      `${s.alarm.similar_count}건`,
    );
    fireEvent.click(container.querySelector(".btn")!);
    expect(onNext).toHaveBeenCalled();
  });
});

describe("T04 진단 · 첫 판정 호출(CO-42: 예외는 그 상태에서만)", () => {
  const structure = (root: Element) =>
    [...root.querySelectorAll(".nx > *")].map(
      (e) => e.tagName + "." + e.className.split(" ")[0],
    );

  it("순위 = 원인 이름 · 막대 · % · 근거 n, 합 100, 기록 카드 3장 · 다음 점검", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    const { container } = render(
      <DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />,
    );
    const rows = rankRows(s);
    expect(texts(container, ".rk b")).toEqual(rows.map((r) => r.name));
    expect(rows.reduce((a, r) => a + r.pct, 0)).toBe(100);
    expect(container.querySelector(".rk")?.className).toBe("rk on");
    // D-663 S1: 근거 칸 = 1위 후보 확정 기록(top_cards) · 다른 설비 비슷한 카드(similar_cards)는 화면에서 뺌
    expect(texts(container, ".hc-t b")).toEqual((s.top_cards ?? []).map((c) => c.record_id));
    expect(container.querySelector('[data-part="other-ref"]')).toBeNull();
    expect(container.querySelector(".nx h3")?.textContent).toBe(
      s.preview.title,
    );
    expect(container.querySelector(".nx .gb")?.className).toBe(
      `gb g${s.preview.grade}`,
    );
    expect(container.querySelector(".why b")?.textContent).toBe(
      "이 점검을 고른 이유",
    );
    expect(container.querySelector(".nx .btn")?.className).toBe("btn lg full");
    expect(container.querySelector(".warnb")).toBeNull();
  });

  it("D-585 진단 카드 [점검 바꾸기]: 창에서 고르면 onSwitch · 고른 점검이 카드에 · 지난 화면엔 메뉴 0 · 후보 칸 0", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    const candidates = [
      {
        section: "4.4",
        title: "냉각 팬 분해 점검",
        grade: "C",
        summary: null,
        exception: false,
      },
    ];
    const v = { ...s, preview: { ...s.preview, candidates } };
    const onSwitch = vi.fn();
    const { container } = render(
      <DiagnosisScreen
        s={v}
        onRecord={() => {}}
        onStart={() => {}}
        onSwitch={onSwitch}
      />,
    );
    expect(container.querySelector('[data-part="check-cands"]')).toBeNull();
    fireEvent.click(container.querySelector(".nx .lk.sw")!);
    fireEvent.click(container.querySelector('.swm .swc[data-section="4.4"]')!);
    expect(onSwitch).toHaveBeenCalledWith("4.4");
    cleanup();
    const picked = {
      ...v,
      preview: {
        ...v.preview,
        picked: { section: "4.4", title: "냉각 팬 분해 점검", grade: "C" },
      },
    };
    const r = render(
      <DiagnosisScreen
        s={picked}
        onRecord={() => {}}
        onStart={() => {}}
        onSwitch={onSwitch}
      />,
    );
    expect(r.container.querySelector(".nx h3")?.textContent).toBe(
      "냉각 팬 분해 점검",
    );
    expect(r.container.querySelector(".nx .why")).toBeNull();
    cleanup();
    const past = render(
      <DiagnosisScreen s={v} onRecord={() => {}} onStart={() => {}} past />,
    );
    expect(past.container.querySelector(".nx .lk.sw")).toBeNull();
  });

  it("F11 진단 카드: M-03은 예외가 왜 맞았나 · M-02는 왜 안 맞았나 한 줄 · 둘 다 없으면 줄 0", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    const why =
      "M-01에서 배운 규칙(근거 R-7001) · 이 기기도 2일 전 같은 정비(4.3.2 · H-0302)";
    const m03 = render(
      <DiagnosisScreen
        s={{ ...s, exception_why: why }}
        onRecord={() => {}}
        onStart={() => {}}
      />,
    );
    expect(
      m03.container.querySelector('.nx [data-part="ex-why"]')?.textContent,
    ).toBe(`예외 후보 · ${why}`);
    expect(m03.container.querySelector('.nx [data-part="ex-miss"]')).toBeNull();
    cleanup();
    const miss =
      "예외 조건 안 맞음 · 최근 7일 같은 정비(4.3.2) 없음 · M-01에서 배운 규칙(근거 R-7001)";
    const m02 = render(
      <DiagnosisScreen
        s={{ ...s, exception_miss: miss }}
        onRecord={() => {}}
        onStart={() => {}}
      />,
    );
    expect(
      m02.container.querySelector('.nx [data-part="ex-miss"]')?.textContent,
    ).toBe(miss);
    expect(m02.container.querySelector('.nx [data-part="ex-why"]')).toBeNull();
    cleanup();
    const none = render(
      <DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />,
    );
    expect(none.container.querySelector('.nx [data-part^="ex-"]')).toBeNull();
  });

  it("첫 판정 호출: 같은 구조, 강조 카드 값만 바뀐다", () => {
    const d = load<SessionWithPreview>("session-before-lock");
    const fc = load<SessionWithPreview>("session-first-call");
    const a = render(
      <DiagnosisScreen s={d} onRecord={() => {}} onStart={() => {}} />,
    );
    const base = structure(a.container.querySelector(".nx")!);
    cleanup();
    const { container } = render(
      <DiagnosisScreen s={fc} onRecord={() => {}} onStart={() => {}} />,
    );
    expect(structure(container.querySelector(".nx")!)).toEqual(base);
    expect(container.querySelector(".nx h3")?.textContent).toBe(
      "파트장 호출 필요",
    );
    expect(container.querySelector(".nx .gb")?.textContent).toBe("C승인 필요");
    expect(texts(container, ".nx .ch")).toEqual([
      `매뉴얼 ${fc.preview.section}절`,
      `${fc.preview.title} · 약 ${fc.preview.minutes}분`,
    ]);
    expect(container.querySelector(".why b")?.textContent).toBe(
      "호출이 필요한 이유",
    );
    expect(container.querySelector(".why span")?.textContent).toBe(
      fc.preview.reason,
    );
    const btn = container.querySelector(".nx .btn")!;
    expect(btn.className).toBe("btn lg full warnb");
    expect(btn.textContent?.trim()).toBe("점검 시작");
  });

  it("이유가 근거 검사에 실패해도 카드 구조 그대로 + '원문으로 대체됨'", () => {
    const fc = load<SessionWithPreview>("session-first-call");
    const base = (() => {
      const r = render(
        <DiagnosisScreen s={fc} onRecord={() => {}} onStart={() => {}} />,
      );
      const out = structure(r.container.querySelector(".nx")!);
      cleanup();
      return out;
    })();
    fc.preview = {
      ...fc.preview,
      reason:
        "냉각 필터 막힘 83% · 4.3절 흡기 필터 육안 점검 · C등급 · 파트장 호출 필요",
      reason_fallback: true,
      reason_label: "원문으로 대체됨",
    };
    const { container } = render(
      <DiagnosisScreen s={fc} onRecord={() => {}} onStart={() => {}} />,
    );
    expect(structure(container.querySelector(".nx")!)).toEqual(base);
    expect(container.querySelector(".nx .ch.fb")?.textContent).toBe(
      "원문으로 대체됨",
    );
  });

  it("근거 적음: 순위 머리 칩 + 카드 배너, 지시는 그대로(AC-29)", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    s.low_evidence = true;
    const { container } = render(
      <DiagnosisScreen s={s} onRecord={() => {}} onStart={() => {}} />,
    );
    expect(container.querySelector(".cd-h .ch.amber")?.textContent).toBe(
      "근거 적음",
    );
    expect(container.querySelector(".nx .banner.warn b")?.textContent).toBe(
      "근거 적음",
    );
    expect(container.querySelector(".nx .btn")?.textContent?.trim()).toBe(
      "점검 시작",
    );
  });

  it("기록 카드를 누르면 그 기록 번호로 연다(AC-38)", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    s.top_cards = s.similar_cards.map((c) => ({ ...c, similarity_pct: null })); // D-663 S1: 근거 칸 카드(1위 후보 확정 기록)
    const onRecord = vi.fn();
    const { container } = render(
      <DiagnosisScreen s={s} onRecord={onRecord} onStart={() => {}} />,
    );
    fireEvent.click(container.querySelectorAll(".hc")[1]);
    expect(onRecord).toHaveBeenCalledWith(s.top_cards[1].record_id);
  });
});

describe("T05 잠금 · 사전 확인", () => {
  it("잠금 전: 가운데 큰 창(1.2절 절차 · 「잠금 완료」 하나) · 뒤 칸에는 지시 내용이 없다(R1 · D-528 가이드 정비자 5)", () => {
    const s = load<SessionWithPreview>("session-before-lock");
    const { container } = render(
      <LockScreen s={s} busy={false} onLock={() => {}} />,
    );
    const dlg = container.querySelector('.lockm[role="dialog"]')!;
    expect(container.querySelector(".lockdim")).not.toBeNull();
    expect(texts(dlg, ".n-lockbox ~ .proc li").length).toBe(4);
    expect(dlg.querySelector(".n-lockbox span")?.textContent).toBe(
      "매뉴얼 1.2절 · 잠금 확인",
    );
    expect(container.querySelector(".ap-body")?.textContent).toBe(""); // 뒤 칸 = 빈 카드 둘
    expect(texts(container, ".btn")).toEqual(["잠금 완료"]);
    expect(container.querySelector(".note, .hint")).toBeNull(); // 「누른 시각이 기록되고 …」 · 가린 칸 안내 뺌
    expect(stepNumber(s, "lock")).toBe(3);
  });

  it("잠금 뒤 사전 확인: 잠금 표시줄 · 등급 · 절 · 분 · 「점수 없이 항상 먼저」 · 다음 안내 줄 없음(D-528 가이드 정비자 5)", () => {
    const s = load<SessionWithPreview>("session-precheck");
    const { container } = render(
      <PrecheckScreen s={s} busy={false} onDone={() => {}} />,
    );
    expect(container.querySelector(".lockok b")?.textContent).toBe(
      "잠금 확인 완료",
    );
    expect(container.querySelector(".ins-h .sm")?.textContent).toBe(
      "사전 확인",
    );
    expect(container.querySelector(".ins-h .gb")?.textContent).toBe(
      "A단독 가능",
    );
    expect(container.querySelector(".res .note")).toBeNull();
    expect(texts(container, ".res .btn")).toEqual(["확인", "상급자 호출"]); // 피드백 F9: 적는 칸 없이 두 단추
    expect(container.querySelector(".res textarea")).toBeNull();
  });
});

function instr(
  s: SessionView,
  over: Partial<Parameters<typeof InstructionScreen>[0]> = {},
) {
  const props = {
    s,
    ins: s.instruction as Instruction,
    draft: emptyDraft(),
    busy: false,
    repeatDismissed: false,
    onDraft: vi.fn(),
    onCheck: vi.fn(),
    onSend: vi.fn(),
    onManual: vi.fn(),
    onRecord: vi.fn(),
    onContinueOriginal: vi.fn(),
    onHandover: vi.fn(),
    ...over,
  };
  return { ...render(<InstructionScreen {...props} />), props };
}

describe("T06 점검 지시 · 결과 입력", () => {
  it("예외 점검 걸음(약속 v10 · 정답지 26쪽): 지시 머리 아래 안내 차례 · 지금 걸음 굵게 — 걸음 아닌 지시엔 차례 0", () => {
    const s = load<SessionView>("event-instruct");
    const walk = [
      {
        step: "c1",
        side: "check",
        what: "필터 덮개 고정 나사 확인",
        say: "필터 덮개 고정 나사 확인",
        done: true,
        now: false,
      },
      {
        step: "c2",
        side: "check",
        what: "흡기 덕트 이음매",
        say: "흡기 덕트 이음매",
        done: false,
        now: true,
      },
      {
        step: "a1",
        side: "action",
        what: "나사를 새로 끼움",
        say: "나사를 새로 끼움",
        done: false,
        now: false,
      },
    ];
    const ws = {
      ...s,
      exception_walk: walk,
      step: {
        ...s.step,
        section: "EX-R-0012",
        step: "c2",
        step_index: 2,
        steps_total: 3,
      },
    };
    const { container } = instr(ws, {
      ins: {
        ...(s.instruction as Instruction),
        section: "EX-R-0012",
        title: "흡기 덕트 이음매",
        steps: [],
      },
    });
    expect(container.querySelector(".ins-h h3")?.textContent).toBe(
      "흡기 덕트 이음매",
    );
    expect(texts(container, ".xwr")).toEqual([
      "점검 1 필터 덮개 고정 나사 확인",
      "지금점검 2 흡기 덕트 이음매",
      "조치 3 나사를 새로 끼움",
    ]);
    cleanup();
    const plain = instr({ ...s, exception_walk: walk }).container; // 걸음 칸 없는 지시(밖 → 원래 순서 4.3)
    expect(plain.querySelector('[data-part="ex-walk"]')).toBeNull();
  });

  it("[점검 바꾸기](D-556 · 약속 v9): 점검 안내 중 근거 줄 끝 링크 → 창(후보 줄: 제목 · 등급 · 절 + [예외]) → 고르면 onSwitch · 취소면 그대로 · [예외] → 호출 후 조치 / 바로 조치 → onException · 후보 0이어도 [예외]", () => {
    const s = load<SessionView>("event-instruct");
    expect(s.step.kind).toBe("instruct");
    const cands = [
      {
        section: "4.4",
        title: "냉각 팬 회전 확인",
        grade: "B",
        summary: "팬 모듈을 꺼내 축 유격을 본다",
        exception: false,
      },
      {
        section: "4.6",
        title: "냉각 설정값 확인",
        grade: "C",
        summary: null,
        exception: false,
      },
    ];
    const onSwitch = vi.fn();
    const onException = vi.fn();
    const { container } = instr(
      { ...s, switch_candidates: cands },
      { onSwitch, onException },
    );
    const link = [...container.querySelectorAll(".refs .lk")].find(
      (x) => x.textContent === "점검 바꾸기",
    )!;
    expect(link).toBeTruthy();
    fireEvent.click(link);
    const dlg = () =>
      container.querySelector('.modal[aria-label="점검 바꾸기"]')!;
    expect(texts(dlg(), ".swt > b")).toEqual([
      "냉각 팬 회전 확인",
      "냉각 설정값 확인",
    ]);
    expect(texts(dlg(), ".swt > .sws")).toEqual([
      "팬 모듈을 꺼내 축 유격을 본다",
    ]); // 고침A 13쪽 제목 아래 설명(있는 후보만)
    expect(texts(dlg(), ".swc.swx > b")).toEqual(["예외"]);
    expect(
      [...dlg().querySelectorAll(".swc[data-section]")].map((x) =>
        x.getAttribute("data-section"),
      ),
    ).toEqual(["4.4", "4.6"]);
    expect(dlg().querySelector(".note, p")).toBeNull(); // 설명 문장 0
    fireEvent.click(
      [...dlg().querySelectorAll(".mb .btn")].find(
        (x) => x.textContent === "취소",
      )!,
    );
    expect(container.querySelector(".modal")).toBeNull();
    expect(onSwitch).not.toHaveBeenCalled();
    fireEvent.click(link); // 같은 화면 한 번 더
    fireEvent.click(container.querySelector('.swc[data-section="4.6"]')!);
    expect(onSwitch).toHaveBeenCalledWith("4.6");
    expect(container.querySelector(".modal")).toBeNull();
    // [예외] → 둘 중 하나(호출 후 조치 = C 승인 필요) · 취소면 그대로
    fireEvent.click(link);
    fireEvent.click(dlg().querySelector(".swc.swx")!);
    expect(dlg().querySelector(".mh b")?.textContent).toBe("예외");
    expect(texts(dlg(), ".swc > b")).toEqual(["호출 후 조치", "바로 조치"]);
    expect(
      [...dlg().querySelectorAll(".swc")].map((x) =>
        x.getAttribute("data-mode"),
      ),
    ).toEqual(["call_first", "act_now"]);
    expect(dlg().querySelectorAll(".mh svg, .swc > b svg")).toHaveLength(0); // 정답지 15쪽: 이름만(아이콘 0)
    expect(
      dlg().querySelector('.swc[data-mode="call_first"] .gb'),
    ).toBeTruthy();
    fireEvent.click(
      [...dlg().querySelectorAll(".mb .btn")].find(
        (x) => x.textContent === "취소",
      )!,
    );
    expect(onException).not.toHaveBeenCalled();
    fireEvent.click(link);
    fireEvent.click(dlg().querySelector(".swc.swx")!);
    fireEvent.click(dlg().querySelector('.swc[data-mode="act_now"]')!);
    expect(onException).toHaveBeenCalledWith("act_now");
    expect(container.querySelector(".modal")).toBeNull();
    cleanup();
    const open = instr(
      { ...s, switch_candidates: cands },
      { onSwitch, onException },
    ); // 13쪽 보내는 중: 후보 · 예외 흐림 · 눌러도 0
    fireEvent.click(
      [...open.container.querySelectorAll(".refs .lk")].find(
        (x) => x.textContent === "점검 바꾸기",
      )!,
    );
    open.rerender(<InstructionScreen {...open.props} busy={true} />);
    expect(
      [...open.container.querySelectorAll(".modal .swc")].map((x) =>
        x.classList.contains("dis"),
      ),
    ).toEqual([true, true, true]);
    fireEvent.click(open.container.querySelector(".modal .swc.swx")!);
    expect(open.container.querySelector(".modal .mh b")?.textContent).toBe(
      "점검 바꾸기",
    ); // 예외 고름으로 안 바뀜
    cleanup();
    const none = instr(
      { ...s, switch_candidates: [] },
      { onSwitch, onException },
    ).container; // 후보 0 → 창엔 [예외]만
    fireEvent.click(
      [...none.querySelectorAll(".refs .lk")].find(
        (x) => x.textContent === "점검 바꾸기",
      )!,
    );
    expect(texts(none, ".modal .swc > b")).toEqual(["예외"]);
    cleanup();
    const pre = instr(
      { ...s, step: { ...s.step, kind: "precheck" } },
      { onSwitch, onException },
    ).container; // 점검 안내 밖 = 링크 0
    expect(
      [...pre.querySelectorAll(".refs .lk")].map((x) => x.textContent),
    ).not.toContain("점검 바꾸기");
  });

  it("참고 알림(D-543 · 답장 001-zzz9 다 = A): 같은 설비 · 같은 증상군 승인 예외 기록이 있으면 잠금 줄 아래 한 줄(순서 그대로) · 없으면 0", () => {
    const s = load<SessionView>("event-instruct");
    // D-554 ④: 보일 한 줄은 서버 text(칸 기록 = 칸을 이은 글 · 전 기록 = 상황 글)
    const note = {
      record_id: "R-0012",
      occurred_at: "2026-10-28T01:10:00+00:00",
      situation: "M-01 · 온도 차 저하 · 14:50 ~ 15:20",
      action: "점검 덮개 나사 · 5분. 조치 나사 새로 끼움",
      text: "점검 덮개 나사 · 5분. 조치 나사 새로 끼움",
    };
    const withNote = instr({ ...s, exception_notes: [note] }).container;
    const b = withNote.querySelector(".banner.info.xref")!;
    expect(b.querySelector("b")?.textContent).toBe(
      `이전 예외 기록 · ${s.alarm.equipment_id} · ${s.alarm.group_name}`,
    );
    expect(b.querySelector("div")?.textContent).toBe(
      `이전 예외 기록 · ${s.alarm.equipment_id} · ${s.alarm.group_name}${note.text}`,
    );
    expect(b.previousElementSibling?.className).toBe("lockok"); // 잠금 줄 바로 아래 · 점검 머리 위
    expect(b.nextElementSibling?.className).toBe("ins-h");
    cleanup();
    expect(
      instr({ ...s, exception_notes: [] }).container.querySelector(".xref"),
    ).toBeNull();
  });

  it("점검 n / 최대 m · 절차 · 판정 기준 · 원문 · 근거, 결과 3버튼, 고르기 전에는 보내기 잠김", () => {
    const s = load<SessionView>("event-instruct");
    const { container, props } = instr(s);
    const ins = s.instruction!;
    expect(container.querySelector(".ins-h .sm")?.textContent).toBe(
      `점검 ${ins.check_no} / 최대 ${ins.check_max}`,
    );
    expect(
      texts(container, ".proc li").map((t) => t.replace(/^\d+/, "")),
    ).toEqual(ins.steps);
    expect(container.querySelector(".hint")?.textContent).toBe(
      `판정 기준 · ${ins.criterion}`,
    );
    expect(texts(container, ".refs .lk")[1]).toBe(
      `근거 ${ins.evidence_record_ids.join(" · ")}`,
    );
    expect(texts(container, ".rbtn")).toEqual(["정상", "이상", "확인 불가"]);
    expect(container.querySelector(".res .btn")?.className).toContain("dis");
    fireEvent.click(container.querySelectorAll(".rbtn")[0]);
    expect(props.onDraft).toHaveBeenCalledWith(
      expect.objectContaining({ value: "정상" }),
    );
    fireEvent.click(container.querySelector(".refs .lk")!);
    expect(props.onManual).toHaveBeenCalled();
  });

  it("고른 결과는 sel 토큰, 보내기가 열린다(지시 밖 점검 · 건너뛰기 수단 없음 — AC-35)", () => {
    const s = load<SessionView>("event-instruct");
    const { container, props } = instr(s, {
      draft: {
        ...emptyDraft(),
        section: s.instruction!.section,
        value: "확인 불가",
      },
    });
    expect(container.querySelector(".rbtn.sel")?.className).toBe("rbtn sel na");
    const send = container.querySelector(".res .btn")!;
    expect(send.className).toBe("btn lg full");
    fireEvent.click(send);
    expect(props.onSend).toHaveBeenCalled();
    expect(texts(container, ".btn")).not.toContain("건너뛰기");
  });

  it("B 점검: 시작 전 체크리스트를 모두 확인해야 결과 입력이 열린다", () => {
    const s = load<SessionView>("event-b-checklist");
    const { container, props } = instr(s);
    expect(container.querySelector(".act.pre .act-h b")?.textContent).toBe(
      "시작 전 체크리스트",
    );
    expect(
      container.querySelector(".act.pre .act-h .ch.gray")?.textContent,
    ).toBe("공통 시작 전 확인");
    expect(texts(container, ".ck")).toEqual(s.instruction!.checklist);
    expect(
      [...container.querySelectorAll(".rbtn")].every((b) =>
        b.className.includes("dis"),
      ),
    ).toBe(true);
    expect(container.querySelector(".res .btn")?.className).toContain("dis");
    expect(container.querySelector(".res .note")).toBeNull(); // D-528: 버튼 아래 안내 문장 뺌(잠김은 버튼 · 결과 칸이 보임)
    fireEvent.click(container.querySelectorAll(".ck")[0]);
    expect(props.onCheck).toHaveBeenCalledWith(s.instruction!.checklist[0]);
  });

  it("확인 불가 뒤 다른 점검: 그 점검 이름 · 횟수 배너(AC-19-C1)", () => {
    const s = load<SessionView>("event-unable-next");
    const { container } = instr(s);
    const u = s.instruction!.unable[0];
    expect(container.querySelector(".banner.warn b")?.textContent).toBe(
      `${u.section} ${u.title} · 확인 불가 ${u.count}회`,
    );
  });

  it("지시문 근거 검사 실패(T06 / fallback): 칩 · 안내 배너 · 절 원문 블록, 결과 입력은 그대로(AC-31-C1)", () => {
    const s = load<SessionView>("session-fallback");
    const { container } = instr(s);
    expect(container.querySelector(".ins-h .ch.fb")?.textContent).toBe(
      "원문으로 대체됨",
    );
    expect(container.querySelector(".banner.info b")?.textContent).toBe(
      "원문으로 대체됨",
    );
    expect(container.querySelector(".orig")?.textContent).toBe(
      s.instruction!.original,
    );
    expect(container.querySelector(".proc")).toBeNull();
    expect(texts(container, ".rbtn")).toEqual(["정상", "이상", "확인 불가"]);
    expect(texts(container, ".refs .lk")[0]).toBe("매뉴얼 원문 보기");
  });

  it("약속 v8 예외 절 지시(승인 뒤): 칩 '예외 기록 H-0244' · 매뉴얼 원문 보기 없음(매뉴얼 밖 절)", () => {
    const s = load<SessionView>("event-instruct");
    s.instruction = {
      ...s.instruction!,
      section: "EX-H-0244",
      title: "필터 덮개 고정 나사 확인",
    };
    const { container } = instr(s);
    expect(texts(container, ".ins-h .ch.blue")).toEqual(["예외 기록 H-0244"]);
    expect(texts(container, ".refs .lk")).not.toContain("매뉴얼 원문 보기");
  });

  it("3회 연속 실패(T06 / llm_repeat): 실제 응답에서 인계 권함 배너 · 두 선택 · 원문", () => {
    const s = load<SessionView>("session-llm-repeat");
    expect(s.recommend_handover).toBe(true);
    const { container } = instr(s);
    expect(container.querySelector(".banner.bad b")?.textContent).toBe(
      "안내 문장 생성이 3회 연속 실패했습니다",
    );
    expect(container.querySelector(".banner.info")).toBeNull();
    expect(container.querySelector(".orig")).not.toBeNull();
    expect(container.querySelector(".ins-h .ch.fb")).not.toBeNull();
  });

  it("3회 연속 실패: 인계 권함 배너, '원문으로 계속'은 배너를 닫는다(AC-31-C2)", () => {
    const s = load<SessionView>("event-instruct");
    s.recommend_handover = true;
    const { container, props } = instr(s);
    expect(container.querySelector(".banner.bad b")?.textContent).toBe(
      "안내 문장 생성이 3회 연속 실패했습니다",
    );
    expect(texts(container, ".choice2 .btn")).toEqual([
      "숙련자 인계",
      "원문으로 계속",
    ]);
    fireEvent.click(container.querySelectorAll(".choice2 .btn")[0]); // 사용자 결정 016 ④: 숙련자 인계 요약으로
    expect(props.onHandover).toHaveBeenCalled();
    fireEvent.click(container.querySelectorAll(".choice2 .btn")[1]);
    expect(props.onContinueOriginal).toHaveBeenCalled();
  });
});

describe("T07 확인 질문 · 맞지 않는 소견", () => {
  it("메모만 보낸 결과 → 해석 확인, '아니요'와 '네' 순서(AC-05)", () => {
    const s = load<SessionView>("event-confirm");
    const ins = load<SessionView>("event-unable-next").instruction!;
    const onConfirm = vi.fn();
    const { container } = render(
      <PendingModal
        s={s}
        ins={ins}
        busy={false}
        onConfirm={onConfirm}
        onChoice={() => {}}
      />,
    );
    expect(container.querySelector(".dim")).not.toBeNull();
    expect(container.querySelector(".bub.me .sm")).toBeNull(); // D-530 답장 001-zzt 2: 말칸 위 '내 메모 · …' 이름표 뺌(가이드 13쪽)
    expect(container.querySelector(".bub.me")?.textContent).toBe(
      [...s.results].reverse()[0]?.memo ?? "",
    );
    expect(container.querySelector(".bub.ai b")?.textContent).toBe(
      `"${s.step.interpretation}"`,
    );
    expect(texts(container, ".mb .btn")).toEqual([
      "아니요, 다시 입력",
      "네, 맞습니다",
    ]);
    fireEvent.click(container.querySelectorAll(".mb .btn")[0]);
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("후보 원인 밖 소견 → 인계 권함, 고르기 전에는 인계 안 함(AC-20)", () => {
    const s = load<SessionView>("event-out-of-scope");
    const onChoice = vi.fn();
    const { container } = render(
      <PendingModal
        s={s}
        ins={null}
        busy={false}
        onConfirm={() => {}}
        onChoice={onChoice}
      />,
    );
    expect(container.querySelector(".mh b")?.textContent).toBe(
      "후보 원인과 맞지 않는 소견입니다",
    );
    expect(container.querySelector(".bub.ai div")?.textContent).toContain(
      `"${s.step.quote}"`,
    );
    expect(texts(container, ".mb .btn")).toEqual(["계속 진행", "숙련자 인계"]);
    fireEvent.click(container.querySelectorAll(".mb .btn")[1]);
    expect(onChoice).toHaveBeenCalledWith("handover");
  });
});

describe("T13 과거 기록 · T14 매뉴얼 원문", () => {
  it("기록 전체: 설비 · 발생 시각 · 증상 · 센서 값 · 점검 경로 · 확정 원인 · 조치 · 정지 시간 · 서술, 닫기(AC-38)", () => {
    const r = load<HistoryRecord>("record");
    const onClose = vi.fn();
    const { container } = render(<RecordModal record={r} onClose={onClose} />);
    expect(container.querySelector(".mh b")?.textContent).toBe(
      `과거 기록 ${r.record_id}`,
    );
    expect(texts(container, ".kv b")).toEqual([
      "설비",
      "발생 시각",
      "증상",
      "센서 값",
      "점검 경로",
      "확정 원인",
      "조치",
      "정지 시간",
    ]);
    expect(container.querySelector(".ai")?.textContent).toBe(r.narrative);
    fireEvent.click(container.querySelector(".mclose")!);
    expect(onClose).toHaveBeenCalled();
  });

  it("매뉴얼 원문: 지시된 절의 원문 · 등급, Markdown 기호 없이(AC-39)", () => {
    const m = load<ManualView>("manual");
    const { container } = render(<ManualModal m={m} onClose={() => {}} />);
    expect(container.querySelector(".mh b")?.textContent).toBe(
      `매뉴얼 원문 · ${m.section} ${m.title}`,
    );
    const body = container.querySelector(".orig")?.textContent ?? "";
    expect(body).toBe(plainManual(m.text));
    expect(body).not.toMatch(/#|\*\*/);
    expect(body).toContain("판정 기준");
  });
});
