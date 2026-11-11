// P-08 세션 화면 동작: 확인 질문 '아니요' 뒤 같은 점검 입력으로 돌아오고 보낸 메모가 입력 칸에 되돌아온다(AC-05-C1 화면 부분).

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Api, SessionView } from "@c1a15/ui";
import { device } from "../device";
import { Session } from "../screens/Session";

const fx = import.meta.glob("./fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
const load = <T,>(n: string) => structuredClone(fx[`./fixtures/${n}.json`]) as T;

afterEach(() => {
  cleanup();
  localStorage.clear();
});

it("확인 질문 '아니요' → 같은 지시 · 보낸 메모가 입력 칸에, 보낸 요청은 confirm_answer yes=false", async () => {
  const confirm = load<SessionView>("event-confirm");
  const afterNo = load<SessionView>("event-after-no");
  const lastIns = load<SessionView>("event-unable-next").instruction;
  const sid = confirm.session_id;
  device.setLastInstruction(sid, lastIns);
  const ok = (data: unknown) => Promise.resolve({ data, error: undefined, response: { status: 200 } });
  const api = {
    GET: vi.fn(() => ok({ ...confirm, preview: { kind: "instruct", section: null, grade: null, cause: null } })),
    POST: vi.fn(() => ok(afterNo)),
  } as unknown as Api;
  const { container } = render(
    <Session api={api} sid={sid} initial={null} onSession={() => {}} onExpired={() => {}} onRecord={() => {}} onManual={() => {}} onHome={() => {}} />,
  );
  await waitFor(() => expect(container.querySelector(".modal .mh b")?.textContent).toBe("입력 내용 확인"));
  await act(async () => {
    fireEvent.click([...container.querySelectorAll(".mb .btn")][0]);
  });
  expect(api.POST).toHaveBeenCalledWith("/api/tech/sessions/{sid}/events", {
    params: { path: { sid } },
    body: { seq: confirm.last_seq + 1, type: "confirm_answer", yes: false },
  });
  await waitFor(() => expect(container.querySelector(".modal")).toBeNull());
  const memo = container.querySelector("textarea.memo") as HTMLTextAreaElement;
  const last = [...confirm.results].reverse()[0];
  expect(container.querySelector(".ins-h h3")?.textContent).toBe(afterNo.instruction!.title);
  expect(memo.value).toBe(last.memo);
  expect(container.querySelector(".rbtn.sel")).toBeNull(); // 버튼 선택은 비어 있다(결과 버튼 없음으로 보낸 입력)
});
