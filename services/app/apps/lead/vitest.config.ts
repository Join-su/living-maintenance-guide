import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// component 시험(P-12): jsdom에서 화면을 그려 Prototype v17 구조(영역 · 버튼 · 순서 · 토큰)를 대조한다.
// fixture = dev 스택의 실제 API 응답(src/__tests__/fixtures, scripts 없이 손으로 만들지 않음).
export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", include: ["src/**/*.test.tsx"], css: false, setupFiles: ["src/__tests__/setup.ts"] },
});
