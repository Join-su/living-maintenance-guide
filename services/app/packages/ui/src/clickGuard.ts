// EV-007(D-422 · D-423): 새 화면이 뜬 직후 같은 자리 누름 무시 — 빠른 여러 번 누름이 다음 화면 버튼까지 누르지 않게.
// 화면마다 첫 버튼 자리를 옮기면 설명서 화면(정답)이 바뀌므로, 자리는 그대로 두고 화면이 바뀐 뒤 잠깐만 누름을 받지 않는다.
// 서버가 차례 밖 요청을 거절하는 것이 먼저이고, 이것은 그 앞의 화면 쪽 막기다.
import { useEffect } from "react";

export const FRESH_SCREEN_MS = 400;

let changedAt = 0;
let installed = false;
// e2e는 페이지 시작 스크립트로 window.__C1A15_FRESH_MS__ = 0을 줄 수 있음(helpers.freshGuardOff) — 없으면 0.4초
let freshMs = (globalThis as { __C1A15_FRESH_MS__?: number }).__C1A15_FRESH_MS__ ?? FRESH_SCREEN_MS;

/** component 시험(jsdom)은 그리자마자 누르므로 0으로 둔다(시험 준비 파일) — 막기 자체는 따로 시험. */
export function setFreshScreenMs(ms: number): void {
  freshMs = ms;
}

function swallow(e: Event) {
  // 진행 단추(버튼)만 — 체크 칸 · 고르기 · 글자 입력은 그대로(D-470: 재가동 화면이 막 뜬 뒤 첫 체크가 삼켜짐)
  const onButton = e.target instanceof Element && e.target.closest('[role="button"], button');
  if (!onButton) return;
  if (e instanceof KeyboardEvent && e.key !== "Enter" && e.key !== " ") return;
  if (performance.now() - changedAt < freshMs) {
    e.stopPropagation();
    e.preventDefault();
  }
}

/** key가 바뀔 때(= 다른 화면 · 다른 단계)부터 FRESH_SCREEN_MS 동안 버튼 누름 · 버튼 위 Enter · Space를 무시한다. */
export function useFreshScreenGuard(key: string): void {
  useEffect(() => {
    if (!installed) {
      document.addEventListener("click", swallow, true);
      document.addEventListener("keydown", swallow, true);
      installed = true;
    }
  }, []);
  useEffect(() => {
    changedAt = performance.now();
  }, [key]);
}
