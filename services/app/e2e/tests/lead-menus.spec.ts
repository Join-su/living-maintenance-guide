// D-529 · D-528: 파트장 '사람 판단' 메뉴 · 화면을 지움 — 짙은 왼쪽 메뉴 넷(승인 대기 · 호출 · 권한 등급 · 감사 기록)을 사람 손 순서로 한 번씩 누르고
// 마지막에 같은 메뉴를 한 번 더 누름. 0.4초 막기는 켠 채(누름 사이 0.8초). 지운 주소(/judge)로 들어와도 승인 대기 화면.

import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { guard, LEAD_PW, LEAD_URL, resetOps, shot } from "./helpers";

test.beforeEach(() => resetOps());

const MENUS: [string, string][] = [
  ["승인 대기", "승인 대기"],
  ["호출", "호출"],
  ["권한 등급", "권한 등급"],
  ["감사 기록", "감사 기록"],
];

async function snap(page: Page, info: TestInfo, name: string) {
  await shot(page, info, name);
  const dir = process.env.SHOT_DIR;
  if (dir) {
    mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, `${name}.png`) });
  }
}

test("파트장 메뉴 넷 · 사람 판단 없음 · 같은 메뉴 한 번 더 · /judge 주소는 승인 대기", async ({ browser }, info) => {
  const lead = await (await browser.newContext({ viewport: { width: 1280, height: 620 } })).newPage();
  const g = guard(lead);
  await lead.goto(LEAD_URL + "/");
  await lead.getByLabel("ID").fill("lead.park");
  await lead.getByLabel("비밀번호").fill(LEAD_PW);
  await lead.waitForTimeout(800);
  await lead.getByText("로그인", { exact: true }).click();
  await expect(lead.locator(".lnav")).toBeVisible();
  await expect(lead.locator(".lnav a")).toHaveText(MENUS.map(([m]) => m));
  await expect(lead.locator(".lnav")).not.toContainText("사람 판단");
  for (const [i, [menu, title]] of MENUS.entries()) {
    await lead.waitForTimeout(800);
    await lead.locator(".lnav a", { hasText: menu }).click();
    await expect(lead.locator(".lbar h2")).toHaveText(title);
    await expect(lead.locator(".lnav a.on")).toHaveText(menu);
    await snap(lead, info, `L-menu-${i + 1}`);
  }
  await lead.waitForTimeout(800);
  await lead.locator(".lnav a", { hasText: "감사 기록" }).click(); // 같은 화면에서 한 번 더
  await expect(lead.locator(".lbar h2")).toHaveText("감사 기록");
  await lead.goto(LEAD_URL + "/judge"); // 옛 주소(즐겨찾기 · 새로고침)
  await expect(lead.locator(".lbar h2")).toHaveText("승인 대기");
  await expect(lead.locator(".lnav a.on")).toHaveText("승인 대기");
  await snap(lead, info, "L-menu-judge-path");
  g.check(info);
});
