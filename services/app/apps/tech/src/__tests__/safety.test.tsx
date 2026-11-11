// EV-007(D-422 · D-423) 화면 쪽 막기: 빠른 여러 번 누름으로 알람 · 사전 확인을 건너뛰지 않음.
// 서버 거절(test_safety_click.py)이 먼저이고, 여기는 화면이 같은 요청을 겹쳐 보내지 않는지 · 새 화면 직후 누름을 받지 않는지를 본다.

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { setFreshScreenMs, useFreshScreenGuard, type Api, type SessionView } from "@c1a15/ui";
import { ActionScreen } from "../screens/Flow";
import { Session } from "../screens/Session";
import { useState } from "react";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = <T,>(n: string) => structuredClone(fx[`./fixtures/${n}.json`]) as T;
const ok = (data: unknown) => Promise.resolve({ data, error: undefined, response: { status: 200 } });

afterEach(() => {
  cleanup();
  localStorage.clear();
  setFreshScreenMs(0);
  vi.restoreAllMocks();
});

const session = (api: Api, view: SessionView) =>
  render(
    <Session api={api} sid={view.session_id} initial={view as never} onSession={() => {}} onExpired={() => {}} onRecord={() => {}} onManual={() => {}} onHome={() => {}} />,
  );

it("새 화면 직후 0.4초 누름 무시 — 지나면 받음, 화면이 바뀌면 다시 0.4초", () => {
  setFreshScreenMs(400);
  let now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  const hits: string[] = [];
  function Two() {
    const [k, setK] = useState("a");
    useFreshScreenGuard(k);
    return (
      <span role="button" tabIndex={0} onClick={() => (hits.push(k), setK(k === "a" ? "b" : "a"))}>
        다음
      </span>
    );
  }
  const { getByRole } = render(<Two />);
  fireEvent.click(getByRole("button")); // 그린 직후
  expect(hits).toEqual([]);
  now += 401;
  fireEvent.click(getByRole("button"));
  expect(hits).toEqual(["a"]); // 받음 → 화면 b
  fireEvent.click(getByRole("button")); // b가 뜬 직후(같은 자리)
  expect(hits).toEqual(["a"]);
  now += 401;
  fireEvent.click(getByRole("button"));
  expect(hits).toEqual(["a", "b"]);
});

it("새 화면 직후 막기는 진행 단추에만 — 재가동 체크(체크 칸)는 화면이 막 바뀌어도 바로 받음(D-470)", () => {
  setFreshScreenMs(400);
  let now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  const s = load<SessionView>("s03-restart-check");
  const items = s.restart!.checklist;
  const onCheck = vi.fn();
  const send = vi.fn();
  function Restart() {
    useFreshScreenGuard("restart_check");
    return <ActionScreen s={s} busy={false} checked={items} onCheck={onCheck} send={send} />;
  }
  const { container } = render(<Restart />);
  fireEvent.click(container.querySelector(".act .ck") as Element); // 화면이 뜬 직후 첫 체크
  expect(onCheck).toHaveBeenCalledTimes(1);
  const restart = [...container.querySelectorAll(".btn")].find((b) => b.textContent?.trim() === "재가동") as Element;
  fireEvent.click(restart); // 진행 단추는 그대로 0.4초 막음
  expect(send).not.toHaveBeenCalled();
  now += 401;
  fireEvent.click(restart);
  expect(send).toHaveBeenCalledWith({ type: "restart", checked: items });
});

it("알람 확인 전 세션은 기기에 남은 단계가 잠금이어도 알람 화면부터", () => {
  const view = load<SessionView>("session-before-lock");
  expect(view.alarm_ack_at ?? null).toBeNull();
  localStorage.setItem(`c1a15.tech.stage.${view.session_id}`, JSON.stringify("lock"));
  const api = { GET: vi.fn(() => ok(view)), POST: vi.fn() } as unknown as Api;
  const { container } = session(api, view);
  expect(container.querySelector(".ai-c .cta .btn")?.textContent).toContain("진단 보기"); // 답장 001-zzx 다(가이드 5쪽): AI 해석 카드 안
  expect(container.textContent).not.toContain("잠금 완료");
});

it("'진단 보기' 세 번 빠르게 → 알람 확인 요청 한 번 · 응답 뒤에만 진단 화면(잠금 화면까지 가지 않음)", async () => {
  const view = load<SessionView>("session-before-lock");
  let release: (v: unknown) => void = () => {};
  const acked = { ...view, last_seq: view.last_seq + 1, alarm_ack_at: "2026-10-27T08:00:00+00:00" };
  const POST = vi.fn(() => new Promise((r) => (release = () => r({ data: acked, error: undefined, response: { status: 200 } }))));
  const api = { GET: vi.fn(() => ok(view)), POST } as unknown as Api;
  const { container } = session(api, view);
  const btn = () => container.querySelector(".cta .btn") as Element;
  fireEvent.click(btn());
  fireEvent.click(btn());
  fireEvent.click(btn());
  expect(POST).toHaveBeenCalledTimes(1);
  expect(POST).toHaveBeenCalledWith("/api/tech/sessions/{sid}/events", {
    params: { path: { sid: view.session_id } },
    body: { seq: view.last_seq + 1, type: "alarm_ack" },
  });
  expect(btn().className).toContain("dis"); // 보내는 동안 잠김
  await act(async () => release(null));
  await waitFor(() => expect(container.textContent).toContain("점검 시작")); // 진단 화면
  expect(container.textContent).not.toContain("잠금 완료");
});

it("사전 확인(피드백 F9): 기록할 값 칸 없음 · [확인] → 값 '확인함' · [상급자 호출] → 호출 후 조치 길(mode call_first)", async () => {
  const view = { ...load<SessionView>("event-precheck"), preview: { kind: "instruct", section: null, grade: null, cause: null } } as SessionView;
  expect(view.step.kind).toBe("precheck");
  const POST = vi.fn(() => ok(view));
  const api = { GET: vi.fn(() => ok(view)), POST } as unknown as Api;
  const { container } = session(api, view);
  expect(container.querySelector("textarea")).toBeNull();
  const b = (label: string) => [...container.querySelectorAll(".res .btn")].find((x) => x.textContent?.trim() === label) as Element;
  await act(async () => fireEvent.click(b("확인")));
  expect(POST).toHaveBeenLastCalledWith("/api/tech/sessions/{sid}/events", {
    params: { path: { sid: view.session_id } },
    body: { seq: view.last_seq + 1, type: "precheck_done", values: "확인함" },
  });
  await act(async () => fireEvent.click(b("상급자 호출")));
  expect(POST).toHaveBeenLastCalledWith("/api/tech/sessions/{sid}/events", {
    params: { path: { sid: view.session_id } },
    body: { seq: view.last_seq + 1, type: "precheck_done", values: "이상 있음 · 상급자 호출", mode: "call_first" },
  });
});
