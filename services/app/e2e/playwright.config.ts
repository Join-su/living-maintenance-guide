import { defineConfig } from "@playwright/test";

// 정비자 앱 E2E(P-08 L3): host 화면 중계(scripts/host_web.py 8081 · 8082)에 붙는다. scripts/verify-e2e가 스택을 띄우고 값(env)을 넘긴다.
// 브라우저는 로컬 캐시 chromium(Playwright 1.63.0 = chromium 1243)만 쓰고 내려받지 않는다. 외부 요청은 시험마다 막고 센다.
export default defineConfig({
  testDir: "tests",
  workers: 1,
  fullyParallel: false,
  timeout: 90_000,
  expect: { timeout: 8_000 },
  grep: process.env.C1A15_ALARM === "ml" ? /@ml/ : undefined, // D-546 ML 길 장면은 앱 서버가 ml일 때만 · 경계 길이면 뺌
  grepInvert: process.env.C1A15_ALARM === "ml" ? undefined : /@ml/,
  reporter: [["list"], ["json", { outputFile: process.env.E2E_REPORT ?? "test-results/report.json" }]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://127.0.0.1:8081",
    viewport: { width: 1280, height: 800 }, // D-528: 정비자 태블릿 1280 × 800(왼쪽 칸 264 + 본문)
    trace: "off",
    screenshot: "only-on-failure",
  },
});
