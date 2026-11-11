// 약속 v9 [예외] 진행을 사람 손 순서로(정답지 묶음 ① 13 · 15 · 16 · 17 · 22쪽): 첫 점검 안내(4.3) → 「점검 바꾸기」 → 창 「예외」
// → 「호출 후 조치」 / 「바로 조치」(같은 창에서 「취소」 → 다시 · D-503 1). 호출 후 조치 = 절 EX-NEW(C) 파트장 호출 자동 · 조치 잠김
// → 파트장 「승인 후 진행」 → 예외 조치
// → 재가동 → 경계 이내 → 보고서(원인 미상). 바로 조치 = 곧바로 예외 조치 · 파트장 승인 대기 화면 '호출 없이 조치함'
// → 보고서 예외 칸 비슷한 것 빈 목록 = 바로 '새 예외'(D-556 덧 5).
// 가짜 배포 서버 judge = 앱 engine 사본 + Pack custom/switch.py 사본(약속 v9 2절). 0.4초 막기는 켠 채(누름 사이 0.8초).
// SHOT_DIR이면 화면마다 PNG.

import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Browser, type Page, type TestInfo } from "@playwright/test";
import { guard, login, loginLead, psql, resetOps, restartWithin, shot, toFirstCheck } from "./helpers";

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

async function leadPage(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  await loginLead(page);
  await expect(page.locator(".lnav")).toBeVisible();
  return page;
}

/** 첫 점검 안내 → 「점검 바꾸기」 → 「예외」(→ 「취소」 → 다시) → mode 고름. 고르기 전 창을 shotName으로. */
async function startException(page: Page, info: TestInfo, label: "호출 후 조치" | "바로 조치", shotName?: string) {
  await toFirstCheck(page, "DEMO-01");
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  const link = page.locator(".refs .lk", { hasText: "점검 바꾸기" });
  const dlg = page.locator('.modal[aria-label="점검 바꾸기"]');
  await pause(page);
  await link.click();
  await pause(page);
  await dlg.locator(".swc.swx").click();
  await expect(dlg.locator(".mh b")).toHaveText("예외");
  await expect(dlg.locator(".swc > b")).toHaveText(["호출 후 조치", "바로 조치"]);
  await pause(page);
  await dlg.locator(".mb .btn", { hasText: "취소" }).click(); // 같은 창에서 취소 → 다시(D-503 1)
  await expect(dlg).toHaveCount(0);
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  await pause(page);
  await link.click();
  await pause(page);
  await dlg.locator(".swc.swx").click();
  if (shotName) await snap(page, info, shotName);
  await pause(page);
  const sent = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
  await dlg.locator(".swc", { hasText: label }).click();
  expect((await sent).status()).toBe(200);
}

test("[예외] 호출 후 조치: EX-NEW 파트장 호출 → 승인 후 진행 → 예외 조치 → 재가동 → 보고서(원인 미상)", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  await startException(page, info, "호출 후 조치", "E1-exception-modes");
  const left = page.locator(".g64 > .cd").first();
  await expect(left.locator(".warn-h .sm")).toHaveText("예외 진행"); // 정답지 16쪽
  await expect(left.locator(".warn-h h3")).toHaveText("예외 조치");
  await expect(left.locator(".warn-h .tags > *")).toHaveText(["C승인 필요", "예외"]);
  await expect(left.locator(".sub")).toHaveText(["지금까지 점검", "조치"]);
  await expect(left.locator(".dn2 > span").first()).toHaveText("잠금 확인 · 1.2절"); // 16쪽: 안전 단계부터
  await expect(left.locator(".btn", { hasText: "조치 잠김 · 파트장 승인 뒤" })).toHaveClass(/dis/); // 승인 전 조치 잠김
  await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/); // 호출은 자동으로 나감
  await expect(page.locator(".g64 > .cd").nth(1)).toContainText("매뉴얼 후보 밖 — 예외 조치(C등급) 승인 요청"); // 보낸 요약
  await snap(page, info, "E2-exception-call");
  await page.reload(); // 다시 읽어도 그대로
  await expect(left.locator(".warn-h h3")).toHaveText("예외 조치");
  await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.locator(".lnav a", { hasText: "호출" }).click();
  await expect(lead.locator("tr[data-call]")).toHaveCount(1);
  await pause(lead);
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await lead.locator(".lopts button", { hasText: "승인 후 진행" }).click();
  await lead.getByLabel("처리 메모").fill("예외 조치 승인");
  await pause(lead);
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  await page.reload();
  await expect(page.locator(".act-h b").first()).toHaveText("조치 · 예외 조치");
  await expect(page.locator(".stk > .cd").first().locator(".cd-h > b, .cd-h > .ch, .kvr > *")).toHaveText(["원인", "예외", "원인", "보고서 예외 칸에서"]); // 정답지 17쪽
  await snap(page, info, "E3-exception-action");
  await pause(page);
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await pause(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await expect(page.locator(".cta2 .btn", { hasText: "보고서 확정" })).toBeVisible();
  await expect(page.locator('[data-field="cause"] .rv2')).toHaveText("원인 미상");
  await expect(page.locator('.modal[aria-label="예외 상황 기록"]')).toBeVisible(); // 고침A 18쪽: 호출 후 조치도 예외 진행 = 팝업
  await snap(page, info, "E4-exception-report");
  const ev = psql(
    "select string_agg(type || ':' || coalesce(payload->>'mode', payload->>'section', ''), ',' order by seq) from ops.session_events " +
      "where type in ('exception_start', 'call_sent', 'call_answer', 'action_done')",
  );
  expect(ev).toBe("exception_start:call_first,call_sent:EX-NEW,call_answer:EX-NEW,action_done:EX-NEW"); // 승인 = call_answer approved
  expect(psql("select count(*) from ops.session_events where payload->'decision'->>'lead_notice' = 'acted_without_call'")).toBe("0");
  g.check(info);
  lg.check(info);
});

test("[예외] 바로 조치: 곧바로 예외 조치 · 파트장 승인 대기 '호출 없이 조치함' · 보고서 비슷한 것 0 = 새 예외", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  await startException(page, info, "바로 조치");
  await expect(page.locator(".act-h b").first()).toHaveText("조치 · 예외 조치"); // 호출 없이
  await expect(page.locator(".stk > .cd").first().locator(".cd-h > b, .cd-h > .ch, .kvr > *")).toHaveText(["원인", "예외", "원인", "보고서 예외 칸에서"]); // 정답지 17쪽
  await expect(page.locator(".act .btn", { hasText: "조치 완료" })).toBeVisible();
  await expect(page.locator(".wait")).toHaveCount(0); // 정비자 화면엔 '호출 없이 조치함' 표시 0(22쪽은 파트장 쪽)
  await expect(page.locator("button.n-li .n-ls").first()).toHaveText(/진행 중 · 예외 조치$/); // 17쪽 왼쪽 알람 칸
  await expect(page.locator(".tli.now > div > *")).toHaveText(["예외 조치", "예외"]); // 17쪽 오른쪽 '지금 예외 조치 · 예외'
  await snap(page, info, "E5-act-now");
  const lead = await leadPage(browser);
  const lg = guard(lead);
  const sec = lead.locator('[data-part="acted"]');
  await expect(sec.locator(".lsect-h")).toHaveText("호출 없이 조치함1건");
  await expect(sec.locator("tr[data-acted] td")).toHaveText([/^M-01/, "김 정비", /^\d\d:\d\d$/, "호출 없이 조치함"]);
  await sec.scrollIntoViewIfNeeded();
  await snap(lead, info, "E6-lead-acted");
  expect(psql("select payload->'decision'->'exception_progress'->>'mode' from ops.session_events where payload->'decision'->>'lead_notice' = 'acted_without_call' order by seq limit 1")).toBe("act_now");
  // 조치 → 재가동 → 보고서 예외 칸: 비슷한 것 빈 목록이면 같음 / 아님 없이 바로 '새 예외'(D-556 덧 5)
  await pause(page);
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await pause(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  const dlg = page.locator('.modal[aria-label="예외 상황 기록"]'); // 고침A 18쪽: 예외 진행 세션 = 보고서 전 팝업
  const ta = dlg.getByLabel("예외 글", { exact: true });
  await ta.click();
  await ta.fill("점검 바닥 배수구 거름망. 조치 거름망을 걸레로 닦음");
  await pause(page);
  await dlg.locator(".exa .btn", { hasText: "AI 나눔" }).click();
  const row = dlg.locator('[data-part="ex-items"] .exi');
  await expect(row).toHaveCount(1);
  await expect(row.locator(".exs .ch")).toHaveText("새 예외 · 비슷한 것 없음");
  await expect(row.locator(".exr, .btn")).toHaveCount(0);
  await expect(dlg.locator(".mb .btn", { hasText: "보고서에 적용" })).not.toHaveClass(/dis/);
  await snap(page, info, "X5-empty-similar");
  g.check(info);
  lg.check(info);
});
