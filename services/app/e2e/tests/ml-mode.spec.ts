// D-546 M3 dev-2 ML 방식(ML만)을 사람 손 순서로 — 앱 서버가 C1A15_ALARM=ml일 때만(실행기 -g @ml, 경계 길 전체는 --grep-invert @ml).
// 가짜 배포 서버 답 = 시험 자료 세 행(ml-mode-cases.json · task10-pack d546 ml-alarm-cases, 모델 release v2 · Pack 알람 길 규칙):
// 공구 마모 217분 · ML 정상(Pack이 공구 마모 경계로 알람 TW — c1a15-pack d05a64d) → 홈 공구 마모 알람(같은 설비 주의 줄 0 — 알람이 이김, D-546 덧 25)
// → 온도 차 경계만 넘음 · ML 정상 → 알람 없음 → ML 유형(HDF)만 → ML 알람 카드 · 알람 둘.
// SHOT_DIR이면 화면마다 PNG(1280 × 800).

import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { guard, login, resetOps, shot, snapTo } from "./helpers";

const API = process.env.C1A15_API_URL ?? "http://127.0.0.1:18000";
const DEPLOY = process.env.C1A15_DEPLOY_URL ?? "http://127.0.0.1:18796";
const TOKEN = process.env.C1A15_INTERNAL_TOKEN ?? "";
type Case = { udi: number; split: string; product_type: string; sensor_snapshot: Record<string, number>; output: unknown };
const CASES: Record<string, Case> = JSON.parse(readFileSync(path.join(__dirname, "ml-mode-cases.json"), "utf-8"));
const pause = (p: Page) => p.waitForTimeout(800);

async function reading(name: string, equipment: string) {
  const c = CASES[name];
  const p = await fetch(`${DEPLOY}/_plan`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify([{ output: c.output }]) });
  expect(p.status).toBe(200);
  const r = await fetch(`${API}/api/internal/alarms`, {
    method: "POST",
    headers: { "content-type": "application/json", "X-Internal-Token": TOKEN },
    body: JSON.stringify({
      equipment_id: equipment,
      sensor_snapshot: c.sensor_snapshot,
      product_type: c.product_type,
      data_lineage: { source_release: "ai4i-2020@sha256:test", row_id: c.udi, split: c.split },
    }),
  });
  expect(r.status).toBe(200);
  return (await r.json()) as { alarm_id: number | null; symptom_group?: string };
}

test.beforeEach(() => resetOps());
test.afterEach(async () => {
  await fetch(`${DEPLOY}/_plan`, { method: "POST", headers: { "content-type": "application/json" }, body: "[]" });
});

test("@ml M3 ML만: 공구 마모 217분(ML 정상) → 공구 마모 알람(주의 줄 0) · 온도 차 경계만 넘음 → 알람 없음 · ML 유형만 → ML 알람", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  expect((await reading("M3-18", "M-03")).symptom_group).toBe("TW"); // ML 정상 + 공구 마모 경계 = Pack 알람 TW
  const al = page.locator(".al");
  await expect(al).toHaveCount(1, { timeout: 15000 });
  await expect(al.first().locator(".al-m > b")).toHaveText("M-03 · 공구 마모 경고");
  await expect(al.first().locator(".n-vals > *")).toHaveText(["공구 계통", "217 분", "215 분", "경계 위"]);
  const row = page.locator(".g2 > div:first-child > .tool");
  await expect(row).toHaveCount(0); // 217은 주의 범위지만 같은 설비 공구 마모 알람이 떠 있음 = 알람이 이김
  await shot(page, info, "M3-tool-wear-alarm");
  await snapTo(page, "M3a-tool-wear-alarm");
  await pause(page);
  expect((await reading("M3-16", "M-01")).alarm_id).toBeNull(); // 온도 차 경계는 넘었지만 ML 정상 = 알람 없음(ML만)
  await page.reload();
  await expect(page.locator(".cd-h b", { hasText: "내 알람" })).toBeVisible();
  await expect(al).toHaveCount(1);
  expect((await reading("M3-01", "M-01")).symptom_group).toBe("CL"); // ML 유형(HDF)만 — 경계 안 넘음
  await expect(al).toHaveCount(2, { timeout: 15000 });
  await expect(page.locator(".al .n-vals.ml").first().locator(":scope > *")).toHaveText(["냉각 계통", "ML 예측", "열 방출 고장(HDF)", "위험도 1.00"]);
  await shot(page, info, "M3-ml-type-alarm");
  await snapTo(page, "M3b-ml-type-alarm");
  await expect(row).toHaveCount(0);
  g.check(info);
});
