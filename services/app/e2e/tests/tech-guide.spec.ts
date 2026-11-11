// D-528 정비자 앱 최종안(가이드 정비자 1 ~ 13 · 반려 33 · 34쪽)을 사람 손 순서로(태블릿 1280 × 800 · 0.4초 막기 켬 · 누름 사이 0.8초):
// 홈(왼쪽 「오늘 밤 알람」 · 내 알람 한 줄) → 알람(추이 그래프 없음) → 진단 → 잠금 큰 창(단계 띠 「진단 ›」으로 갔다 돌아옴) → 사전 확인
// → 점검 → 조치 · 재가동 → 보고서 「보고서 확정」 확인 창(취소 → 다시 → 확정) → 왼쪽 완료 탭 → 파트장 반려 → 홈 반려 칸 → 「이어서 하기」
// → 보고서 오른쪽 위 반려 사유 → 고쳐 다시 확정 → 홈 최근 보고서 「최종본 보기」(정비자 14). SHOT_DIR이면 화면마다 PNG.

import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { alarm, confirmReportModal, guard, LEAD_PW, LEAD_URL, login, resetOps, result, shot } from "./helpers";

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

test("정비자 최종안: 왼쪽 칸 · 한 줄 · 잠금 큰 창 · 확정 확인 창 · 반려 → 다시 확정(사람 손 순서)", async ({ page, browser }, info) => {
  const g = guard(page);
  // 정비자 1 로그인(가이드 3쪽 — 답장 001-zzx 아): 큰 로고 위 · 이름 아래 두 줄
  await page.goto("/");
  const logo = await page.locator(".lg-card .brand .logo").boundingBox();
  const brandName = await page.locator(".lg-card .brand .bn").boundingBox({ timeout: 3000 }).catch(() => null);
  expect(brandName, "로고 아래 이름 줄").not.toBeNull();
  expect(brandName!.y).toBeGreaterThanOrEqual(logo!.y + logo!.height);
  await login(page, undefined, undefined, false); // 막기 켬
  alarm("DEMO-01");
  // 정비자 2 홈: 왼쪽 칸 · 내 알람 한 줄
  const left = page.locator('aside.n-lp[aria-label="오늘 밤 알람"]');
  await expect(left.locator("button.n-li")).toHaveCount(1);
  await expect(left.locator(".n-k")).toHaveText("냉각 계통");
  await expect(left.locator(".n-ls")).toHaveText("🚨새 알람");
  await expect(page.locator(".al .n-vals > *")).toHaveText(["냉각 계통", /^\d+\.\d K$/, "8.2 K", "경계 아래"]);
  await expect(page.locator(".safe")).toHaveText("잠금을 먼저 확인합니다");
  await snap(page, info, "T2-home");
  await pause(page);
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  // 정비자 3 알람: 추이 그래프 · 안내 문장 없음
  await page.waitForURL(/\/session\/\d+/);
  await expect(page.locator(".tbl tr")).toHaveCount(6);
  await expect(page.locator("svg.spk")).toHaveCount(0);
  await expect(left.locator("button.n-li.on")).toHaveCount(1);
  await snap(page, info, "T3-alarm");
  await pause(page);
  await page.getByText("진단 보기").click();
  // 정비자 4 진단
  await expect(page.locator(".rk").first()).toHaveClass(/on/);
  { // 답장 001-zzx-2 3(가이드 6쪽): 두 칸 폭 = 약 1.25 : 1(과거 기록 카드가 왼쪽 칸을 밀어 넓히지 않음)
    const cols = await page.locator(".ap-body > .g64 > *").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().width));
    expect.soft(cols[0] / cols[1], "두 칸 폭 비율").toBeGreaterThan(1.1);
    expect.soft(cols[0] / cols[1], "두 칸 폭 비율").toBeLessThan(1.4);
  }
  await expect(page.locator(".hint")).toHaveCount(0);
  await snap(page, info, "T4-diagnosis");
  await pause(page);
  await page.locator(".nx .btn").click();
  // 정비자 5 잠금 큰 창 · 단계 띠 「진단 ›」만으로 돌아감
  const lock = page.locator('.lockm[role="dialog"]');
  await expect(lock.locator(".proc li")).toHaveCount(4);
  await expect(page.locator(".lockdim")).toBeVisible();
  await snap(page, info, "T5-lock");
  await pause(page);
  await page.locator('.ap-step [aria-label="진단 지난 화면"]').click();
  await expect(lock).toHaveCount(0);
  await expect(page.locator(".rk").first()).toBeVisible();
  await pause(page);
  await page.locator(".pastbar .btn", { hasText: "지금 단계로" }).click();
  await expect(lock).toBeVisible();
  await pause(page);
  await lock.getByText("잠금 완료", { exact: true }).click();
  await expect(page.locator(".lockok b")).toHaveText("잠금 확인 완료");
  await expect(page.locator(".ins-h .sm")).toHaveText("사전 확인");
  await expect(page.locator(".res .note")).toHaveCount(0);
  // 피드백 F9: 사전 확인 = 적는 칸 없이 [확인] · [상급자 호출](둘 다 바로 누를 수 있음)
  await expect(page.locator(".res .btn")).toHaveText(["확인", "상급자 호출"]);
  await expect(page.locator(".res .btn.dis")).toHaveCount(0);
  await expect(page.locator(".res textarea")).toHaveCount(0);
  await snap(page, info, "T5-precheck");
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  // 정비자 6 점검 → 9 조치 · 재가동
  await expect(page.locator(".rbtns")).toBeVisible();
  await expect(page.locator(".res .note")).toHaveCount(0);
  await snap(page, info, "T6-check");
  await pause(page); // 새 화면 직후 0.4초 누름 무시(EV-007) — 사람처럼 잠깐 뒤
  await result(page, "이상", "기름때가 필터 표면 절반 넘게 덮음");
  await pause(page);
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  // 재가동 체크리스트 셋(다 고르기 전 잠김) — 공용 restartWithin은 막기를 끈 시험용이라 여기서는 사람처럼 하나씩
  await expect(page.locator(".act .btn", { hasText: /^재가동$/ })).toHaveClass(/dis/);
  // 답장 001-zzx-2 3(가이드 9 · 15 · 34쪽): 잠긴 버튼 표시 = 선 자물쇠 아이콘(이모지 0) — 사전 확인 잠김이 없어져 여기서 봄
  const locked = page.locator(".act .btn", { hasText: /^재가동$/ });
  expect.soft(await locked.evaluate((el) => getComputedStyle(el, "::before").content), "잠금 표시 이모지 0").not.toContain("🔒");
  expect.soft(await locked.evaluate((el) => getComputedStyle(el, "::before").maskImage || getComputedStyle(el, "::before").webkitMaskImage), "잠금 표시 선 아이콘").toContain("svg");
  const items = page.locator(".act .ck");
  for (let i = 0; i < (await items.count()); i++) {
    await pause(page);
    await items.nth(i).click();
  }
  await pause(page);
  await page.locator(".btn", { hasText: /^재가동$/ }).click();
  await expect(page.locator(".rv-big")).toBeVisible();
  await pause(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  // 정비자 12 · 13 보고서 확정 확인 창
  await expect(page.locator(".cta2 .note")).toHaveCount(0);
  await snap(page, info, "T12-report");
  await pause(page);
  await page.locator(".cta2 .btn", { hasText: "보고서 확정" }).click();
  const dlg = page.locator('.modal[aria-label="보고서 확정"]');
  await expect(dlg.locator(".mh b")).toHaveText("보고서를 확정합니까?");
  await snap(page, info, "T12-confirm-modal");
  await pause(page);
  await dlg.locator(".btn", { hasText: "취소" }).click(); // 취소는 창만 닫음
  await expect(dlg).toHaveCount(0);
  await expect(page.locator(".cd-h .ch.amber")).toHaveText("초안");
  await pause(page);
  await confirmReportModal(page);
  await expect(page.locator(".banner.ok b")).toHaveText(/^보고서를 확정했습니다 · \d\d:\d\d$/);
  await expect(page.locator(".banner.ok div")).toHaveText(/^보고서를 확정했습니다 · \d\d:\d\d$/); // 안내 문장 두 줄 뺌
  // 확정하면 왼쪽 칸도 바로 '진행 중인 알람 없음'(가이드 19쪽 — 홈 5초 주기를 기다리지 않음).
  // 위 띠는 지금 앱 그대로(세션 화면은 그 세션 알람 — 조각 2 남은 차이 1, 답장 001-zzr 받음)
  await expect(left.locator(".n-le")).toHaveText("진행 중인 알람 없음", { timeout: 2000 });
  await snap(page, info, "T13-confirmed");
  // 정비자 14 최종본: 「최종본 보기」 → 문서 · 「출력」(동작 0) 두 번 · 「보고서로」
  await pause(page);
  await page.locator(".btn", { hasText: "최종본 보기" }).click();
  await expect(page).toHaveURL(/\/report\/\d+$/);
  await expect(page.locator(".fd-h h1")).toHaveText("정비 보고서 M-01");
  await expect(page.locator(".fd-st span").last()).toHaveText("승인 대기");
  await expect(page.locator(".fdoc h2")).toHaveCount(5);
  await snap(page, info, "T14-final");
  // 결함(답장 001-zzv-1): 4번 구역 값 칸이 공용 .kv(grid)에 눌려 한 글자 폭 — 값 칸 폭 ≥ 구역 폭의 절반 · 아래까지 내린 그림
  {
    const sec4 = page.locator(".fdoc section", { hasText: "4. 조치 결과" });
    const secW = (await sec4.boundingBox())!.width;
    const tdW = (await sec4.locator("table td").first().boundingBox())!.width;
    expect(tdW).toBeGreaterThanOrEqual(secW / 2);
    await page.locator(".fd-f").scrollIntoViewIfNeeded();
    await snap(page, info, "T14-final-bottom");
  }
  await pause(page);
  await page.locator(".fbar .btn", { hasText: "출력" }).click();
  await pause(page);
  await page.locator(".fbar .btn", { hasText: "출력" }).click();
  await expect(page.locator(".fd-h h1")).toHaveText("정비 보고서 M-01");
  await pause(page);
  await page.locator(".fbar .btn", { hasText: "보고서로" }).click();
  await expect(page.locator(".cd-h .ch.green")).toHaveText("확정 · 승인 대기");
  await pause(page);
  await left.locator(".tabs button", { hasText: "완료" }).click();
  await expect(left.locator(".n-li.ro").first()).toContainText("냉각 필터 막힘");
  await expect(left.locator(".n-li.ro .ch").first()).toHaveText("승인 대기");
  await snap(page, info, "T-left-done");

  // 파트장 반려
  const lead = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  const lg = guard(lead);
  await lead.goto(LEAD_URL + "/");
  await lead.getByLabel("ID").fill("lead.park");
  await lead.getByLabel("비밀번호").fill(LEAD_PW);
  await pause(lead);
  await lead.locator(".lg-card button.lbtn").click();
  await pause(lead);
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "반려" }).click();
  await lead.getByLabel("반려 사유").fill("4.3 메모를 더 자세히 적어 다시 확정");
  await pause(lead);
  await lead.locator(".lmdl button.lbtn", { hasText: "반려" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");

  // 정비자 2(반려 33쪽) · 12(34쪽): 홈 반려 칸 → 이어서 하기 → 반려 사유 오른쪽 위 → 고쳐 다시 확정
  await pause(page);
  await page.locator(".tb-home").click();
  await expect(page.locator(".banner.bad div")).toContainText("4.3 메모를 더 자세히 적어 다시 확정");
  await expect(page.locator(".banner.bad div")).not.toContainText('"이어서 하기"로'); // 설명 문장 0
  await expect(page.locator(".banner.bad .btn")).toHaveText("이어서 하기"); // 답장 001-zzx 다(가이드 33쪽): 버튼은 사유 상자 안
  // 지시 d530-p33(가이드 33쪽): 반려된 알람 카드 = '반려 · 보고서 고치기' · 「이어서 하기」 파란 주 버튼
  await expect(page.locator(".al .al-m > span").first()).toHaveText(/ · 반려 · 보고서 고치기$/);
  await expect(page.locator(".al .btn").first()).not.toHaveClass(/ghost/);
  await snap(page, info, "T33-home-rejected");
  await pause(page);
  await page.locator(".btn.full", { hasText: "이어서 하기" }).click();
  await expect(page.locator(".cd-h .ch.red")).toHaveText("반려 · 고치는 중");
  await expect(page.locator('[data-part="rejection"]')).toContainText("4.3 메모를 더 자세히 적어 다시 확정");
  await snap(page, info, "T34-report-rejected");
  await pause(page);
  await page.locator('[data-field="check_path"] .ed').click();
  const ta = page.locator('[data-field="check_path"] textarea');
  await ta.fill((await ta.inputValue()).replace("이상: 기름때", "이상: 기름때가 필터 표면 절반 넘게 덮음, 뒷면도 확인"));
  await page.locator(".cd-h b", { hasText: "정비 보고서 초안" }).click();
  await pause(page);
  await confirmReportModal(page);
  await expect(page.locator(".cd-h .ch.green")).toHaveText("확정 · 승인 대기");
  await expect(page.locator('[data-part="rejection"]')).toHaveCount(0);
  await lead.reload();
  await expect(lead.locator("tr[data-report]")).toHaveCount(1);
  // 홈 「최근 내 보고서」 줄에서도 최종본 — 다시 확정 표시
  await pause(page);
  await page.locator(".tb-home").click();
  await pause(page);
  await page.locator(".rl .lk2", { hasText: "최종본 보기" }).first().click();
  await expect(page.locator(".fd-steps li").nth(1)).toContainText("정비자 다시 확정");
  await snap(page, info, "T14-final-reconfirmed");
  g.check(info);
  lg.check(info);
});

test("두 번째 점검이 열리면 왼쪽 칸도 바로 '점검 2/3' · 매뉴얼 원문 창은 가운데 · 단계 띠도 어둡게(가이드 10 · 12쪽, D-530 답장 001-zzt 1 · 3 · 001-zzx-2 1)", async ({ page }, info) => {
  const g = guard(page);
  await login(page, undefined, undefined, false);
  alarm("DEMO-02");
  const left = page.locator('aside.n-lp[aria-label="오늘 밤 알람"]');
  await pause(page);
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await pause(page);
  await page.getByText("진단 보기").click();
  await pause(page);
  await page.locator(".nx .btn").click();
  await pause(page);
  await page.locator(".lockm").getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".rbtns")).toBeVisible();
  // 매뉴얼 원문 창: 가운데 · 640px 안팎 · 단계 띠도 창 뒤 어둠 아래(답장 001-zzx-2 1 — 앞 '밝게' 바로잡음)
  await pause(page);
  await page.locator(".refs .lk", { hasText: "매뉴얼 원문" }).click();
  const box = (await page.locator('.modal.wide[aria-label^="매뉴얼 원문"]').boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(660);
  expect(Math.abs(box.x + box.width / 2 - 640)).toBeLessThanOrEqual(20);
  const step = (await page.locator(".ap-step").boundingBox())!;
  const top = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest(".ap-step") !== null, [step.x + 40, step.y + step.height / 2]);
  expect.soft(top, "단계 띠도 어둠 아래").toBe(false); // 창이 떠 있는 동안 누를 수 있는 것은 창 안뿐
  await snap(page, info, "T10-manual");
  await pause(page);
  await page.locator(".modal.wide .btn", { hasText: "닫기" }).click();
  // 첫 점검 정상 → 두 번째 점검: 왼쪽 칸 단계도 바로(홈 5초 주기를 기다리지 않음)
  await pause(page);
  await result(page, "정상");
  await expect(page.locator(".ins-h .sm")).toHaveText(/^점검 2 \/ 최대 \d$/);
  await expect(left.locator(".n-ls")).toHaveText(/진행 중 · 점검 2\/\d$/, { timeout: 2000 });
  await snap(page, info, "T12-second-check");
  g.check(info);
});
