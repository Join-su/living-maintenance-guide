// D-546 U6 공구 교체 주의 · U5 ML 알람 카드를 사람 손 순서로(꼴 architecture-diagram d546 01 · 02).
// U6: 센서 값 공구 마모 205분(알람 아님) → 홈 '내 알람' 위 주황 줄 → 「확인」 → 사라짐 · 새로 고침에도 없음
//     → 같은 공구 220분(다시 뜨지 않음 · 215 넘음 = 공구 마모 알람은 그대로) → 교체(150) → 205(M-03 알람이 떠 있어 0 · 알람이 이김)
//     → 다른 설비 M-02 208 = 주의 한 줄(설비마다 값 하나).
// U5: 가짜 배포 서버가 ML 유형만으로 정한 알람(경계 안 넘음)을 냄 → 왼쪽 칸 · 내 알람 · 알람 화면 머리 칩 · AI 해석 두 줄 → 「진단 보기」.
// SHOT_DIR이면 화면마다 PNG(1280 × 800).

import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { guard, login, resetOps, shot } from "./helpers";

const API = process.env.C1A15_API_URL ?? "http://127.0.0.1:18000";
const DEPLOY = process.env.C1A15_DEPLOY_URL ?? "http://127.0.0.1:18796";
const TOKEN = process.env.C1A15_INTERNAL_TOKEN ?? "";
const QUIET = {
  air_temperature_k: 302.5,
  process_temperature_k: 311.6, // 온도 차 9.1 K — 어느 경계도 넘지 않음
  rotational_speed_rpm: 1500,
  torque_nm: 40.0,
};
const pause = (p: Page) => p.waitForTimeout(800);

async function reading(equipment: string, extra: Record<string, number>) {
  const r = await fetch(`${API}/api/internal/alarms`, {
    method: "POST",
    headers: { "content-type": "application/json", "X-Internal-Token": TOKEN },
    body: JSON.stringify({
      equipment_id: equipment,
      sensor_snapshot: { ...QUIET, tool_wear_min: 100, ...extra },
      data_lineage: { source_release: "ai4i-2020@sha256:test", row_id: 1, split: "test" },
    }),
  });
  return r.status;
}

async function snap(page: Page, info: TestInfo, name: string) {
  await shot(page, info, name);
  const dir = process.env.SHOT_DIR;
  if (dir) {
    mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, `${name}.png`) });
  }
}

test.beforeEach(() => resetOps());
test.afterEach(async () => {
  await fetch(`${DEPLOY}/_plan`, { method: "POST", headers: { "content-type": "application/json" }, body: "[]" });
});

test("U6 공구 교체 주의: 주황 줄 · 「확인」(두 번째 누름 · 새로 고침) · 같은 공구엔 다시 0 · 교체 뒤 다시 뜸", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  expect(await reading("M-03", { tool_wear_min: 205 })).toBe(422); // 경계 안 넘음 = 알람 없음 응답 그대로
  const row = page.locator(".g2 > div:first-child > .tool");
  await expect(row).toHaveCount(1);
  await expect(row.locator(".tm > *")).toHaveText(["공구 교체 주의", "M-03 · 공구 마모 205분 · 주의 범위 200 ~ 246분"]);
  await expect(row.locator("xpath=following-sibling::div[1]").locator(".cd-h b")).toHaveText("내 알람"); // 내 알람 카드 바로 위
  await expect(page.locator(".al")).toHaveCount(0); // 알람 아님
  await snap(page, info, "U6-home-tool");
  await pause(page);
  const ack = page.waitForResponse((r) => r.url().includes("/api/tech/cautions/") && r.request().method() === "POST");
  await row.locator(".btn", { hasText: "확인" }).click();
  expect((await ack).status()).toBe(200);
  await expect(row).toHaveCount(0);
  await snap(page, info, "U6b-home-tool-ok");
  await page.reload();
  await expect(page.locator(".cd-h b", { hasText: "내 알람" })).toBeVisible();
  await expect(row).toHaveCount(0); // 홈 다시 와도 없음
  expect(await reading("M-03", { tool_wear_min: 220 })).toBe(200); // 같은 공구 · 215 넘음 = 공구 마모 알람
  await expect(page.locator(".al")).toHaveCount(1, { timeout: 15000 });
  await expect(row).toHaveCount(0); // 교체 전엔 다시 뜨지 않음
  await reading("M-03", { tool_wear_min: 150 }); // 공구 교체
  await reading("M-03", { tool_wear_min: 205 }); // 교체 뒤 다시 200 ~ 246 — 그래도 M-03 공구 마모 알람이 떠 있어 주의 0(덧 25 알람이 이김)
  await reading("M-02", { tool_wear_min: 208 }); // 다른 설비 주의만
  await expect(row).toHaveCount(1, { timeout: 15000 });
  await expect(row.locator(".tm > *").nth(1)).toHaveText("M-02 · 공구 마모 208분 · 주의 범위 200 ~ 246분"); // 설비마다 값 하나(M-03 주의 줄 0)
  await expect(page.locator(".al")).toHaveCount(1); // M-03 공구 마모 알람
  await snap(page, info, "G3c-home-tool-and-alarm");
  await pause(page);
  await row.locator(".btn", { hasText: "확인" }).click();
  await row.locator(".btn", { hasText: "확인" }).click({ timeout: 2000 }).catch(() => {}); // 같은 줄 두 번째 누름(사라졌으면 누를 곳 없음)
  await expect(row).toHaveCount(0);
  g.check(info);
});

test("U5 ML 알람: 왼쪽 칸 · 내 알람 · 알람 화면 머리 칩 · AI 해석 두 줄 → 「진단 보기」", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  const ml = {
    model_release: "mr-ai4i-lgbm-large-v2",
    risk: 0.82,
    alarm: true,
    confidence: 0.79,
    threshold: 0.5,
    confirmed: true,
    top_k: [{ type: "HDF", probability: 0.79, group_suggestion: "CL" }],
  };
  const out = {
    event_id: "x",
    alarm: true,
    group: "CL",
    group_source: "ml_type",
    alarm_block: null,
    sensor_derived: { power_w: 6283, temp_diff_k: 9.1 },
    ml,
    no_alarm_reason: null,
  };
  const r = await fetch(`${DEPLOY}/_plan`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify([{ output: { alarm: out } }]) });
  expect(r.status).toBe(200);
  // 센서 5칸 = 시연 각본 DEMO-01(세션 시작이 승인 행과 같게 — 대역 0)
  expect(await reading("M-01", { air_temperature_k: 302.6, process_temperature_k: 310.4, rotational_speed_rpm: 1409.0, torque_nm: 53.3, tool_wear_min: 52.0 })).toBe(200);
  const left = page.locator("button.n-li").first();
  await expect(left.locator(".n-lv").first().locator(":scope > span")).toHaveText(["ML 예측", "열 방출 고장(HDF)"], { timeout: 15000 });
  await expect(left.locator(".n-lv").nth(1)).toHaveText("위험도 0.82");
  const vals = page.locator(".al .n-vals.ml");
  await expect(vals.locator(":scope > *")).toHaveText(["냉각 계통", "ML 예측", "열 방출 고장(HDF)", "위험도 0.82"]);
  await snap(page, info, "U5-home-ml");
  await pause(page);
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await expect(page.locator(".alh .ch")).toHaveText(["증상군 · 냉각 계통", "ML 예측", "센서 이탈 0"]);
  await expect(page.locator(".ai-c .mlr")).toHaveText(["예측 유형열 방출 고장(HDF)", "위험도0.82"]);
  await expect(page.locator(".ai-c .ai")).toHaveCount(0);
  await snap(page, info, "U5-alarm-ml");
  await pause(page);
  await page.getByText("진단 보기").click();
  await expect(page.locator(".nx .btn")).toBeVisible(); // 진단부터 흐름 그대로
  g.check(info);
});
