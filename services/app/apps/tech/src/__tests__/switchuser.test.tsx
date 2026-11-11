// 사람 리허설 피드백 F12: 정비자 앱 머리줄 [사용자 바꾸기] → 서버 로그아웃 → 로그인 화면(다른 정비자로 다시 들어감).

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../App";

const calls: string[] = [];
vi.mock("@c1a15/ui", async (orig) => {
  const ui = await orig<typeof import("@c1a15/ui")>();
  const res = (path: string, status: number) => Promise.resolve({ data: status === 200 ? { ok: true } : undefined, error: undefined, response: new Response(null, { status }) });
  const fake = {
    GET: (path: string) => (calls.push(`GET ${path}`), res(path, 503)),
    POST: (path: string) => (calls.push(`POST ${path}`), res(path, path === "/api/logout" ? 200 : 503)),
  };
  return { ...ui, createApi: () => fake };
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

it("머리줄 [사용자 바꾸기] → POST /api/logout → 기기에 둔 사용자 지움 → 로그인 화면", async () => {
  localStorage.setItem("c1a15.tech.me", JSON.stringify({ user_id: "tech.kim", display_name: "김정비" }));
  const { container, findByRole } = render(<App />);
  const sw = await findByRole("button", { name: "사용자 바꾸기" });
  expect(container.querySelector(".tb-user")?.textContent).toContain("김정비");
  fireEvent.click(sw);
  await waitFor(() => expect(container.querySelector(".lg-card h3")?.textContent).toBe("정비자 로그인"));
  expect(calls).toContain("POST /api/logout");
  expect(localStorage.getItem("c1a15.tech.me")).toBeNull();
});
