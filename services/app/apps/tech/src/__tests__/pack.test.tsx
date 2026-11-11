// EV-001 CH-17 · VC-41: 판정 지연 · 실패 안내(배포 서버 길). 기본 길(200)에서는 보이지 않고,
// 3초 넘게 늦으면 '판정 지연', 앱이 503 pack_unavailable을 돌려주면 '판정 서버 연결 실패' + 다시 보내기(같은 seq).

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Api, SessionView } from "@c1a15/ui";
import { device } from "../device";
import { PACK_SLOW_MS, PackNotice, trackPack, type PackState } from "../PackNotice";
import { Session } from "../screens/Session";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = <T,>(n: string) => structuredClone(fx[`./fixtures/${n}.json`]) as T;

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.useRealTimers();
});

const reply = (status: number, data?: unknown, error?: unknown) =>
  Promise.resolve({ data, error, response: { status } as Response });

it("기본 길(200): 안내 줄 없음", async () => {
  const states: PackState[] = [];
  await trackPack(() => reply(200, {}), (s) => states.push(s));
  expect(states).toEqual([null]);
  const { container } = render(<PackNotice state={null} onRetry={() => {}} />);
  expect(container.querySelector(".banner")).toBeNull();
});

it("3초 넘게 늦으면 '판정 지연'(warn), 끝나면 사라짐", async () => {
  vi.useFakeTimers();
  const states: PackState[] = [];
  let done!: (v: unknown) => void;
  const p = trackPack(
    () => new Promise<{ response: Response }>((r) => (done = r as (v: unknown) => void)),
    (s) => states.push(s),
  );
  vi.advanceTimersByTime(PACK_SLOW_MS - 1);
  expect(states).toEqual([]);
  vi.advanceTimersByTime(1);
  expect(states).toEqual(["slow"]);
  done({ response: { status: 200 } as Response });
  await p;
  expect(states).toEqual(["slow", null]);
  const { container } = render(<PackNotice state="slow" onRetry={() => {}} />);
  expect(container.querySelector(".banner.warn")?.textContent).toBe("판정 지연잠시 기다려 주세요.");
});

it("503 pack_unavailable만 '판정 서버 연결 실패' — 다른 503 · 409는 아님", async () => {
  const states: PackState[] = [];
  await trackPack(() => reply(503, undefined, { detail: "pack_unavailable", code: "trace_unavailable" }), (s) => states.push(s));
  await trackPack(() => reply(503, undefined, { detail: "other" }), (s) => states.push(s));
  await trackPack(() => reply(409, undefined, { detail: "stale_seq" }), (s) => states.push(s));
  expect(states).toEqual(["failed", null, null]);
});

it("세션: 결과 보내기가 503 pack_unavailable → 실패 안내 · 다시 보내기는 같은 seq · 같은 본문", async () => {
  const confirm = load<SessionView>("event-confirm");
  const afterNo = load<SessionView>("event-after-no");
  const sid = confirm.session_id;
  device.setLastInstruction(sid, load<SessionView>("event-unable-next").instruction);
  const POST = vi
    .fn()
    .mockImplementationOnce(() => reply(503, undefined, { detail: "pack_unavailable", code: "trace_unavailable" }))
    .mockImplementationOnce(() => reply(200, afterNo));
  const api = {
    GET: vi.fn(() => reply(200, { ...confirm, preview: { kind: "instruct", section: null, grade: null, cause: null } })),
    POST,
  } as unknown as Api;
  const { container } = render(
    <Session api={api} sid={sid} initial={null} onSession={() => {}} onExpired={() => {}} onRecord={() => {}} onManual={() => {}} onHome={() => {}} />,
  );
  await waitFor(() => expect(container.querySelector(".modal .mh b")?.textContent).toBe("입력 내용 확인"));
  await act(async () => {
    fireEvent.click([...container.querySelectorAll(".mb .btn")][0]);
  });
  await waitFor(() => expect(container.querySelector('[data-pack="failed"]')).not.toBeNull());
  expect(container.querySelector('[data-pack="failed"]')?.className).toBe("banner bad");
  const retryBtn = container.querySelector('[data-pack="failed"] .btn') as Element;
  await act(async () => {
    fireEvent.click(retryBtn);
    fireEvent.click(retryBtn); // 두 번 눌러도 다시 보내기는 한 번
  });
  const sent = { params: { path: { sid } }, body: { seq: confirm.last_seq + 1, type: "confirm_answer", yes: false } };
  expect(POST.mock.calls.map((c) => c[1])).toEqual([sent, sent]); // 같은 seq · 같은 본문
  await waitFor(() => expect(container.querySelector("[data-pack]")).toBeNull());
  // 원래 호출자의 뒤처리(확인 '아니요' 뒤 같은 지시 · 보낸 메모가 입력 칸에)가 다시 보내기 뒤에도 이어진다
  await waitFor(() => expect(container.querySelector(".modal")).toBeNull());
  const last = [...confirm.results].reverse()[0];
  expect((container.querySelector("textarea.memo") as HTMLTextAreaElement).value).toBe(last.memo);
});
