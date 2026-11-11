// S-06 이어받은 E2E(G6-S05-2 비차단 ①): 조치 B 시작 전 체크 · 재가동 체크리스트를 일부만 체크한 채
// 홈 '이어서 하기' · 종 목록 '이어서 하기' 두 경로로 돌아와도 체크 상태가 그대로(AC-44-C3). 실제 nginx · API · PostgreSQL, 외부 요청 0.

import { expect, test, type Page } from "@playwright/test";
import { alarm, guard, login, psql, resetOps } from "./helpers";

test.beforeEach(() => resetOps());

async function back(page: Page, via: "home" | "bell"): Promise<void> {
  await page.locator(".tb-home").click();
  await expect(page.locator(".g2")).toBeVisible();
  if (via === "home") {
    await page.locator(".al .btn", { hasText: "이어서 하기" }).click();
  } else {
    await page.locator(".tb-bell").click();
    await expect(page.locator(".panel .grp")).toContainText(["진행 중인 점검"]);
    await page.locator(".nt-item .btn", { hasText: "이어서 하기" }).click();
  }
  await page.waitForURL(/\/session\/\d+/);
}

test("AC-44-C3(S-05 이어받음): 조치 B 시작 전 체크 · 재가동 체크리스트 일부 체크 → 홈 · 종 목록 두 경로로 돌아와도 그대로", async ({ page }, info) => {
  const g = guard(page);
  psql("update ops.grade_table set grade = 'B' where section_id = '4.3.2'");
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await page.locator(".rbtn", { hasText: "이상" }).click();
  await page.getByText("결과 보내기").click();
  // 조치 B 시작 전 체크(T08 / checklist_b): 하나만 체크
  const act = page.locator(".act .ck");
  await expect(act.first()).toBeVisible();
  const n = await act.count();
  expect(n).toBeGreaterThan(1);
  await act.nth(0).click();
  await expect(page.locator(".act .ck.on")).toHaveCount(1);
  for (const via of ["home", "bell"] as const) {
    await back(page, via);
    await expect(page.locator(".act .ck.on")).toHaveCount(1); // 보내지 않은 체크 그대로
    await expect(page.locator(".act .ck").nth(0)).toHaveClass(/on/);
  }
  for (let i = 1; i < n; i++) await act.nth(i).click();
  await page.locator(".act .btn", { hasText: / 진행$/ }).first().click(); // B 조치: 시작 전 체크 보냄
  await page.locator(".act .btn", { hasText: / 완료$/ }).first().click();
  // 재가동 체크리스트(T08 / restart_checklist): 하나만 체크
  await expect(page.locator(".act .btn", { hasText: /^재가동$/ })).toHaveClass(/dis/);
  const rs = page.locator(".act .ck:not(.on)");
  const before = await page.locator(".act .ck.on").count();
  await rs.first().click();
  await expect(page.locator(".act .ck.on")).toHaveCount(before + 1);
  for (const via of ["home", "bell"] as const) {
    await back(page, via);
    await expect(page.locator(".act .btn", { hasText: /^재가동$/ })).toBeVisible();
    await expect(page.locator(".act .ck.on")).toHaveCount(before + 1); // 재가동 체크도 그대로
  }
  g.check(info);
});

test("AC-41-C1(S-06 전 화면 대조에서 발견): 잠금 전(진단 화면)에도 진행 표시 완료 단계 '알람'을 누르면 그때 화면 → 지금 단계로", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await expect(page.locator(".ap-step .sp").nth(0)).toHaveClass(/pick/); // T04: 완료 단계는 누를 수 있음(Prototype)
  await page.locator(".ap-step .sp").nth(0).click();
  await expect(page.locator(".pastbar > b")).toHaveText("지난 화면 · 알람");
  await page.locator(".pastbar .btn", { hasText: "지금 단계로" }).click();
  await expect(page.locator(".pastbar")).toHaveCount(0);
  await expect(page.locator(".nx .btn")).toBeVisible(); // 진단 화면 그대로(잠금 전 단계 유지)
  g.check(info);
});
