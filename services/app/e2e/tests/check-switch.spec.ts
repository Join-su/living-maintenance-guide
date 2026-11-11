// D-556 1단계 [점검 바꾸기]를 사람 손 순서로: 첫 점검 안내(4.3) → 근거 줄 끝 「점검 바꾸기」 → 창(지금 추천 후보) → 「취소」
// → 같은 화면 한 번 더(D-503 1) → 후보 하나 고름 → 그 점검 안내 · 다시 읽어도 그대로 → 결과 보내기 → 다음 판정.
// 약속 v9: judge 사건 switch_check{section 고른 절, from_section 4.3, choice candidate} → Pack(가짜 = engine 사본 + switch.py 사본)이
// 그 점검을 엶 · 기록 줄 헛걸음(wasted_step). 0.4초 막기는 켠 채(누름 사이 0.8초). SHOT_DIR이면 PNG.

import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { guard, login, psql, resetOps, shot, toFirstCheck } from "./helpers";

test.beforeEach(() => resetOps());

async function snap(page: Page, info: TestInfo, name: string) {
  await shot(page, info, name);
  const dir = process.env.SHOT_DIR;
  if (dir) {
    mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, `${name}.png`) });
  }
}

const pause = (p: Page) => p.waitForTimeout(800);

test("점검 바꾸기: 후보 창 → 취소 → 다시 → 고름 → 그 점검 안내(다시 읽어도 그대로) → 결과 → 다음 판정 · 헛걸음 기록", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  await toFirstCheck(page, "DEMO-01");
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  const link = page.locator(".refs .lk", { hasText: "점검 바꾸기" });
  await expect(link).toBeVisible();
  await pause(page);
  await link.click();
  const dlg = page.locator('.modal[aria-label="점검 바꾸기"]');
  await expect(dlg.locator(".swc").first()).toBeVisible();
  const n = await dlg.locator(".swc[data-section]").count();
  expect(n).toBeGreaterThan(0);
  await expect(dlg.locator(".swc.swx > b")).toHaveText("예외"); // 후보 뒤 [예외]
  await snap(page, info, "S1-switch-window");
  await pause(page);
  await dlg.locator(".mb .btn", { hasText: "취소" }).click(); // 같은 화면에서 취소 → 다시(D-503 1)
  await expect(dlg).toHaveCount(0);
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  await pause(page);
  await link.click();
  // A · B 후보 하나(바로 점검 안내가 열리는 것)
  const pick = dlg.locator(".swc", { has: page.locator(".tags b", { hasText: /^[AB]$/ }) }).first();
  const section = (await pick.getAttribute("data-section")) ?? "";
  const title = ((await pick.locator(".swt > b").textContent()) ?? "").trim();
  await expect(dlg.locator(".swc[data-section] .swt > .sws").first()).not.toBeEmpty(); // 고침A 13쪽 제목 아래 설명
  await pause(page);
  const sent = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
  await pick.click();
  expect((await sent).status()).toBe(200);
  await expect(page.locator(".ins-h h3")).toHaveText(title);
  await snap(page, info, "S2-switched");
  await link.click(); // 정답지 14쪽 '다시 바꾸기': 앞 판정 후보 이어 씀(지금 고른 것 빼고)
  await expect(dlg.locator(`.swc[data-section="${section}"]`)).toHaveCount(0);
  expect(await dlg.locator(".swc[data-section]").count()).toBe(n - 1);
  await dlg.locator(".mb .btn", { hasText: "취소" }).click();
  await page.reload(); // 다시 읽어도 고른 점검 그대로
  await expect(page.locator(".ins-h h3")).toHaveText(title);
  expect(psql("select (payload->>'from_section') || '>' || (payload->>'section') || ':' || (payload->>'choice') from ops.session_events where type = 'switch_check'")).toBe(
    `4.3>${section}:candidate`,
  );
  expect(psql("select payload->'decision'->'switched'->>'to' from ops.session_events where type = 'step_opened' order by seq desc limit 1")).toBe(section); // Pack 결정 칸
  // 고른 점검 결과 → 다음 판정(체크리스트가 있으면 먼저)
  const cks = page.locator(".act.pre .ck");
  for (let i = 0; i < (await cks.count()); i++) {
    await pause(page);
    await cks.nth(i).click();
  }
  await pause(page);
  await page.locator(".rbtn", { hasText: "정상" }).click();
  await pause(page);
  const res = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
  await page.getByText("결과 보내기").click();
  expect((await res).status()).toBe(200);
  await expect(page.locator(".ins-h h3")).not.toHaveText(title);
  g.check(info);
});
