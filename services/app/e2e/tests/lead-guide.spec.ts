// D-528 파트장 앱 최종안(가이드 파트장 1 ~ 5 · 29 ~ 41쪽)을 사람 손 순서로: 정비자 보고서 확정 → 파트장 승인 대기(오늘 기록 · 처리할 호출)
// → 보고서 검토 = 최종본 → 「출력」 두 번 → 반려(사유 적어야 열림) → 정비자 고쳐 다시 확정 → 다시 올라옴 → 승인 → 오늘 기록 「보고서 보기」
// → 권한 등급 확인 창(취소) → 감사 기록 「파트장 앱」 칩. 0.4초 막기는 켠 채(누름 사이 0.8초). SHOT_DIR이면 화면마다 PNG(1280 × 800).

import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { guard, LEAD_PW, LEAD_URL, login, resetOps, restartWithin, result, shot, toFirstCheck, confirmReportModal } from "./helpers";

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

test("파트장 최종안: 승인 대기 → 최종본 검토 → 반려 → 다시 확정 → 승인 · 등급 확인 창 · 감사 칩(사람 손 순서)", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  await toFirstCheck(page, "DEMO-01");
  await result(page, "이상", "기름때가 필터 표면 절반 넘게 덮음");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await confirmReportModal(page);
  await expect(page.locator(".banner.ok b")).toHaveText(/^보고서를 확정했습니다/);

  const lead = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  const lg = guard(lead);
  // 파트장 1 로그인: ID · 비밀번호를 다 넣어야 열림
  await lead.goto(LEAD_URL + "/");
  await expect(lead.locator(".lg-card button.lbtn")).toBeDisabled();
  await lead.getByLabel("ID").fill("lead.park");
  await lead.getByLabel("비밀번호").fill(LEAD_PW);
  await snap(lead, info, "P1-login");
  await pause(lead);
  await lead.locator(".lg-card button.lbtn").click();
  // 파트장 2 승인 대기: 요약 셋 → 확정된 보고서 → 오늘 기록 · 처리할 호출
  await expect(lead.locator(".lbar h2")).toHaveText("승인 대기");
  await expect(lead.locator(".lnav a.on")).toHaveText("승인 대기1");
  await expect(lead.locator(".ltiles b")).toHaveText(["1", "0", "0"]);
  await expect(lead.locator("tr[data-report]")).toHaveCount(1);
  const today = lead.locator('[data-part="today"]');
  await expect(today.locator("tr.lsm").first()).toContainText("보고서 확정");
  await expect(today.locator("button.ltx", { hasText: "보고서 보기" })).toBeVisible();
  await expect(lead.locator('[data-part="calls"] .lnone')).toHaveText("처리할 호출이 없습니다");
  await snap(lead, info, "P2-approvals");
  await pause(lead);
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  // 보고서 검토 = 최종본 문서
  await expect(lead.locator(".lcrumb")).toHaveText("승인 대기 / M-01 보고서");
  await expect(lead.locator(".fd-h h1")).toHaveText("정비 보고서 M-01");
  await expect(lead.locator(".fd-st span").last()).toHaveText("승인 대기");
  await expect(lead.locator(".fdoc h2")).toHaveCount(5);
  await snap(lead, info, "P2-review");
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "출력" }).click(); // 버튼만(동작 0)
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "출력" }).click(); // 같은 화면에서 한 번 더
  await expect(lead.locator(".lmdl")).toHaveCount(0);
  await expect(lead.locator(".fd-h h1")).toHaveText("정비 보고서 M-01");
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "반려" }).click();
  const okReject = lead.locator(".lmdl button.lbtn", { hasText: "반려" });
  await expect(okReject).toBeDisabled();
  await lead.getByLabel("반려 사유").fill("4.3 메모에 필터 사진 번호를 적어 다시 확정");
  await snap(lead, info, "P2-reject");
  await pause(lead);
  await okReject.click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");

  // 정비자 2 · 12: 반려 사유 → 보고서 고쳐 다시 확정
  await page.locator(".tb-home").click();
  await expect(page.locator(".banner.bad div")).toContainText("4.3 메모에 필터 사진 번호를 적어 다시 확정");
  await page.locator(".tb-bell").click();
  await page.locator(".nt-item .btn", { hasText: "보고서 보기" }).click();
  await page.locator('[data-field="check_path"] .ed').click();
  const ta = page.locator('[data-field="check_path"] textarea');
  await ta.fill((await ta.inputValue()).replace("이상: 기름때", "이상: 기름때가 필터 표면 절반 넘게 덮음, 뒷면도 확인")); // 새 숫자 0(R8)
  await page.locator(".cd-h b", { hasText: "정비 보고서 초안" }).click();
  await confirmReportModal(page);
  await expect(page.locator(".cd-h .ch.green")).toHaveText("확정 · 승인 대기");

  // 다시 올라옴 → 진행 '정비자 다시 확정' → 승인
  await lead.reload();
  await expect(lead.locator("tr[data-report]")).toHaveCount(1);
  await expect(today.locator("tr.lsm td:nth-child(4)").nth(0)).toHaveText("보고서 확정");
  await expect(today.locator("tr.lsm td:nth-child(4)").nth(1)).toHaveText(/^보고서 /);
  await snap(lead, info, "P2-reconfirmed");
  await pause(lead);
  await lead.locator("tr[data-report]").click(); // 행을 눌러도 엶
  await expect(lead.locator(".fd-steps li").nth(1)).toContainText("정비자 다시 확정");
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "승인" }).click();
  await snap(lead, info, "P2-approve");
  await pause(lead);
  await lead.locator(".lmdl button.lbtn", { hasText: "승인" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");
  await pause(lead);
  await today.locator("button.ltx", { hasText: "보고서 보기" }).first().click(); // 승인 완료 최종본 다시 보기
  await expect(lead.locator(".fd-st span").last()).toHaveText("승인 완료");
  await expect(lead.locator(".fd-steps li.done")).toHaveCount(4);
  await expect(lead.locator(".lbar button.lbtn")).toHaveText(["출력"]);
  await snap(lead, info, "P2-approved-final");
  // 결함(답장 001-zzv-1): 4번 구역 값 칸이 공용 .kv(grid)에 눌려 한 글자 폭 — 값 칸 폭 ≥ 구역 폭의 절반 · 아래까지 내린 그림
  {
    const sec4 = lead.locator(".fdoc section", { hasText: "4. 조치 결과" });
    const secW = (await sec4.boundingBox())!.width;
    const tdW = (await sec4.locator("table td").first().boundingBox())!.width;
    expect(tdW).toBeGreaterThanOrEqual(secW / 2);
    await lead.locator(".fd-f").scrollIntoViewIfNeeded();
    await snap(lead, info, "P2-final-bottom");
  }

  // 파트장 3 호출(빈) · 4 권한 등급 확인 창 · 5 감사 기록 칩
  await pause(lead);
  await lead.locator(".lnav a", { hasText: "호출" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  await snap(lead, info, "P3-calls");
  await pause(lead);
  await lead.locator(".lnav a", { hasText: "권한 등급" }).click();
  await expect(lead.locator(".ltiles b").first()).toHaveText(/^\d+절$/);
  await snap(lead, info, "P4-grades");
  // 결함(답장 001-zzx 자): '등급의 뜻' A ~ D 글자가 칸 이름표 색에 덮여 안 보임 — 검정 네모 흰 글자
  for (const gl of await lead.locator(".lkv .gl").all()) {
    expect(await gl.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(255, 255, 255)");
  }
  await pause(lead);
  await lead.locator('tr[data-section="4.5"] .lseg button', { hasText: "B" }).click();
  await expect(lead.locator(".lmdl h4")).toHaveText("4.5 냉각수 수위 확인 등급을 A → B로 바꿉니까?");
  await snap(lead, info, "P4-grade-modal");
  await pause(lead);
  await lead.locator(".lmdl button.lbtn", { hasText: "취소" }).click();
  await expect(lead.locator(".lmdl")).toHaveCount(0);
  await expect(lead.locator('tr[data-section="4.5"] .lseg button.on')).toHaveText("A");
  await pause(lead);
  await lead.locator(".lnav a", { hasText: "감사 기록" }).click();
  await pause(lead);
  await lead.locator(".lchips button", { hasText: "파트장 앱" }).click();
  const apps = await lead.locator("tr[data-audit] td:nth-child(2)").allTextContents();
  expect(apps.length).toBeGreaterThan(0);
  expect(new Set(apps)).toEqual(new Set(["파트장 앱"]));
  await snap(lead, info, "P5-audit-lead");
  await pause(lead);
  await lead.locator(".lchips button", { hasText: "파트장 앱" }).click(); // 같은 칩 한 번 더
  expect(new Set(await lead.locator("tr[data-audit] td:nth-child(2)").allTextContents())).toEqual(new Set(["파트장 앱"]));
  g.check(info);
  lg.check(info);
});
