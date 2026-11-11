// S-05(P-14 · P-15) E2E: 지난 화면(T15) · 정정 · 메모 덧붙이기 · 다시 판정 · 정정 호출 · 보낸 호출에 붙은 정정(파트장 L04 · L06).
// 실제 nginx · API · PostgreSQL, fixture 모드, 외부 요청 0. 정정 · 답은 모두 실제 화면 조작으로 한다(시험 DB 시뮬레이션 없음).

import { expect, test, type Browser, type Page } from "@playwright/test";
import { alarm, guard, LEAD_URL, login, loginLead, psql, resetOps, restartWithin, result, shot, sidOf, confirmReportModal } from "./helpers";

test.beforeEach(() => resetOps());

async function leadPage(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 620 } });
  const page = await ctx.newPage();
  await loginLead(page);
  await expect(page.locator(".lnav")).toBeVisible();
  return page;
}

/** 홈 → 새 알람 → 진단 → 잠금 → 사전 확인([확인]) → 첫 지시. */
async function toCheck(page: Page, scenario = "DEMO-01"): Promise<number> {
  alarm(scenario);
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".rbtns")).toBeVisible();
  return sidOf(page);
}

const step = (page: Page, n: number) => page.locator(".ap-step .sp").nth(n - 1);
// 답장 001-zzx 마(가이드 22 · 23쪽): 점검 · 조치 지난 화면 = 왼쪽 기록 목록(.pli) + 오른쪽 고른 기록 칸(.pdet · 정정 · 메모는 칸 안 .pform)
const pick = (page: Page, title: string) => page.locator(".pli", { hasText: title }).first().click();
const toNow = (page: Page) => page.locator(".pastbar .btn, .pfoot .btn", { hasText: "지금 단계로" }).click();
const form = (page: Page) => page.locator(".pdet .pform");

test("AC-41 · AC-44: 지난 화면(알람 · 진단 · 점검 기록 목록) → 지금 단계로 입력 그대로, 홈 → 이어서 하기도 그대로", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  await toCheck(page);
  await result(page, "정상", "필터 깨끗함");
  const current = await page.locator(".ins-h h3").textContent();
  await page.locator(".rbtn", { hasText: "정상" }).click(); // 보내지 않은 입력(기기)
  await page.getByLabel("메모").fill("설정값 표시창 정상");
  await step(page, 1).click(); // AC-41-C1: 지나온 단계 → 그때 화면
  await expect(page.locator(".pastbar > b")).toHaveText("지난 화면 · 알람");
  await expect(page.locator(".ap-body.past .alh h3")).toBeVisible();
  await expect(page.locator(".ap-body.past .btn", { hasText: "진단 보기" })).toHaveClass(/dis/);
  await expect(step(page, 1)).toHaveClass(/\bon\b/); // 보는 단계가 지금 꼴(지시 d530-p33 · 가이드 23쪽)
  await shot(page, info, "T15-alarm");
  await step(page, 2).click();
  await expect(page.locator(".pastbar .tt")).toHaveText(/그때의 원인 순위 · 첫 지시$/);
  await expect(page.locator(".ap-body.past .nx h3")).toHaveText("흡기 필터 육안 점검");
  await shot(page, info, "T15-diagnosis");
  await step(page, 3).click(); // 생성자 해석 ①: 지금 단계도 보낸 기록 보기
  await expect(page.locator(".pli.on b")).toHaveText("흡기 필터 육안 점검 · 4.3");
  await expect(page.locator(".pdet .unsent")).toHaveText(/^지금 \d\.\d 입력\(보내지 않음\)은 그대로$/); // CO-39
  await expect(page.locator(".pdet .prow b")).toHaveText("정상 · 메모 '필터 깨끗함'");
  await shot(page, info, "T15-check_current");
  await pick(page, "고온부 확인과 온도 기록"); // AC-41-C2
  await expect(page.locator(".pdet .prow span")).toHaveText("기록한 값");
  await shot(page, info, "T15-precheck");
  await pick(page, "잠금 확인 · 1.2");
  await expect(page.locator(".pdet .prow b")).toHaveText(/^잠금 완료 \d\d:\d\d · 1\.2절$/);
  await shot(page, info, "T15-lock");
  await toNow(page); // AC-41-C3
  await expect(page.locator(".pdet")).toHaveCount(0);
  await expect(page.locator(".ins-h h3")).toHaveText(current!);
  await expect(page.locator(".rbtn.sel")).toHaveText("정상");
  await expect(page.getByLabel("메모")).toHaveValue("설정값 표시창 정상");
  await expect(page.locator(".rbtn.dis")).toHaveCount(0); // AC-41-C4: 돌아온 현재 단계는 잠기지 않고 고칠 수 있다
  await page.locator(".rbtn", { hasText: "이상" }).click();
  await expect(page.locator(".rbtn.sel")).toHaveText("이상");
  await page.getByLabel("메모").fill("설정값 표시창 깜빡임");
  await expect(page.getByLabel("메모")).toHaveValue("설정값 표시창 깜빡임");
  await page.locator(".tb-home").click(); // AC-44: 확인 창 없이 홈 · 진행 중
  await expect(page.locator(".g2")).toBeVisible();
  await expect(page.locator(".al .btn", { hasText: "이어서 하기" })).toBeVisible();
  await page.locator(".al .btn", { hasText: "이어서 하기" }).click();
  await expect(page.locator(".ins-h h3")).toHaveText(current!); // AC-44-C3: 떠난 화면 · 고친 보내지 않은 입력 그대로
  await expect(page.locator(".rbtn.sel")).toHaveText("이상");
  await expect(page.getByLabel("메모")).toHaveValue("설정값 표시창 깜빡임");
  g.check(info);
});

test("AC-44-C3: 보내지 않은 B 시작 전 체크 · 결과 · 메모가 홈 '이어서 하기'와 종 목록 진행 중 항목 두 경로로 돌아와도 그대로", async ({ page }, info) => {
  const g = guard(page);
  psql("update ops.grade_table set grade = 'B' where section_id = '4.3'");
  await login(page);
  await toCheck(page);
  const items = page.locator(".act.pre .ck");
  const n = await items.count();
  expect(n).toBeGreaterThan(1);
  await items.nth(0).click(); // 일부만 체크(보내지 않음 — 결과 버튼은 아직 잠김)
  await expect(page.locator(".act.pre .ck.on")).toHaveCount(1);
  await expect(page.locator(".rbtn").first()).toHaveClass(/dis/);
  const back = async (via: "home" | "bell") => {
    await page.locator(".tb-home").click();
    await expect(page.locator(".g2")).toBeVisible();
    if (via === "home") {
      await page.locator(".al .btn", { hasText: "이어서 하기" }).click();
    } else {
      await page.locator(".tb-bell").click();
      await expect(page.locator(".panel .grp")).toContainText(["진행 중인 점검"]);
      await page.locator(".nt-item .btn", { hasText: "이어서 하기" }).click();
    }
    await expect(page.locator(".act.pre")).toBeVisible();
  };
  for (const via of ["home", "bell"] as const) {
    await back(via);
    await expect(page.locator(".act.pre .ck.on")).toHaveCount(1); // 체크 상태 그대로
    await expect(page.locator(".act.pre .ck").nth(0)).toHaveClass(/on/);
  }
  for (let i = 1; i < n; i++) await items.nth(i).click();
  await page.locator(".rbtn", { hasText: "이상" }).click();
  await page.getByLabel("메모").fill("기름때가 필터 표면 절반 넘게 덮음");
  for (const via of ["home", "bell"] as const) {
    await back(via);
    await expect(page.locator(".act.pre .ck.on")).toHaveCount(n);
    await expect(page.locator(".rbtn.sel")).toHaveText("이상"); // 결과 · 메모 그대로
    await expect(page.getByLabel("메모")).toHaveValue("기름때가 필터 표면 절반 넘게 덮음");
  }
  g.check(info);
});

test("AC-45 · AC-46: 정정(칸 안 · 사유 필수) → 확인 창(취소하면 그대로) → 다시 판정 · 정정 1회 · 측정값 정정 · 메모 덧붙이기", async ({ page }, info) => {
  const g = guard(page, [/status of 409/]); // 확인 창은 서버의 409 confirm_required로 연다(AC-46-C3)
  await login(page);
  const sid = await toCheck(page);
  await result(page, "정상", "필터 깨끗함");
  await page.locator(".rbtn", { hasText: "정상" }).click();
  await page.getByLabel("메모").fill("수위 MAX 선 가까이");
  await step(page, 3).click();
  await page.locator(".btn", { hasText: "이 기록 정정" }).click();
  const modal = form(page);
  await expect(page.locator(".pdet .cd-h b")).toHaveText("흡기 필터 육안 점검 · 4.3");
  await modal.locator(".rbtn", { hasText: "이상" }).click();
  await modal.getByLabel("정정할 메모").fill("필터 뒷면에 기름때가 절반 넘게 덮음");
  await expect(modal.locator(".btn", { hasText: "정정 저장" })).toHaveClass(/dis/); // AC-45-C2: 사유 전 잠김
  await shot(page, info, "T15-correct");
  await modal.getByLabel("정정 사유").fill("필터 뒷면 기름때를 못 보고 정상으로 눌렀음");
  await modal.locator(".btn", { hasText: "정정 저장" }).click();
  const cm = page.locator('.modal[aria-label="이 정정으로 다음 지시가 바뀝니다"]');
  await expect(cm.locator(".banner.warn b")).toHaveText("4.3 흡기 필터 육안 점검 · 정상 → 이상"); // AC-46-C3
  await expect(cm.locator(".banner.warn div")).toContainText("보내지 않은 입력(정상 · 메모 '수위 MAX 선 가까이')은 지워지고 새 지시가 나옵니다");
  await shot(page, info, "T15-correct_confirm");
  await cm.locator(".btn", { hasText: "취소" }).click(); // AC-46-C4: 그대로
  const before = await page.request.get(`/api/tech/sessions/${sid}`).then((r) => r.json());
  expect(before.step.kind).toBe("instruct");
  await toNow(page); // AC-46-C4: 취소하면 현재 단계의 보내지 않은 입력도 그대로
  await expect(page.locator(".rbtn.sel")).toHaveText("정상");
  await expect(page.getByLabel("메모")).toHaveValue("수위 MAX 선 가까이");
  await step(page, 3).click();
  await page.locator(".btn", { hasText: "이 기록 정정" }).click();
  await modal.locator(".rbtn", { hasText: "이상" }).click();
  await modal.getByLabel("정정 사유").fill("필터 뒷면 기름때를 못 보고 정상으로 눌렀음");
  await modal.locator(".btn", { hasText: "정정 저장" }).click();
  await cm.locator(".btn", { hasText: "정정 저장" }).click();
  await expect(page.locator(".pdet .cd-h .ch.amber")).toHaveText("정정 1회"); // AC-45-C4
  await expect(page.locator(".corr-list s")).toHaveText("정상 · '필터 깨끗함'");
  await shot(page, info, "T15-corrected");
  const after = await page.request.get(`/api/tech/sessions/${sid}`).then((r) => r.json());
  expect([after.step.kind, after.step.section]).toEqual(["action", "4.3.2"]); // AC-46-C1: 정정한 결과로 다시 판정
  await pick(page, "고온부 확인과 온도 기록"); // 사전 확인 측정값 정정(AC-46-C5 — 다음 단계 그대로)
  await page.locator(".btn", { hasText: "이 기록 정정" }).click();
  await expect(modal.locator(".sm").first()).toHaveText("정정할 값");
  await modal.getByLabel("정정할 값").fill("하우징 표면 55 °C · 공기 302.7 K · 공정 310.5 K");
  await modal.getByLabel("정정 사유").fill("온도계 다시 잼");
  await shot(page, info, "T15-correct_value");
  await modal.locator(".btn", { hasText: "정정 저장" }).click();
  await expect(page.locator(".pdet .prow b")).toHaveText("하우징 표면 55 °C · 공기 302.7 K · 공정 310.5 K");
  expect((await page.request.get(`/api/tech/sessions/${sid}`).then((r) => r.json())).step).toEqual(after.step);
  await pick(page, "잠금 확인 · 1.2"); // 잠금 기록: 메모 덧붙이기만(AC-48-C1)
  await expect(page.locator(".btn", { hasText: "이 기록 정정" })).toHaveCount(0);
  await page.locator(".btn", { hasText: "메모 덧붙이기" }).click();
  await page.getByLabel("덧붙일 메모").fill("잠금 표지 2번 패널에 추가로 걺");
  await shot(page, info, "T15-note");
  await form(page).locator(".btn", { hasText: "메모 저장" }).click();
  await expect(page.locator(".corr-list b")).toHaveText("잠금 표지 2번 패널에 추가로 걺");
  await toNow(page);
  await expect(page.locator(".banner.info b")).toHaveText(/^4\.3 정정으로 다시 판정했습니다 · \d\d:\d\d$/); // T08 / corrected_path
  await expect(page.getByLabel("메모")).toHaveCount(0); // 바뀐 지시의 보내지 않은 입력은 지워짐
  g.check(info);
});

test("AC-46-C2 · AC-45-C5 · AC-48: 뒤 기록 '정정으로 무효' → 보고서 점검 경로 정정 표기 → 확정 세션 정정 수단 없음 → 반려로 다시 열리면 다시 정정", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toCheck(page);
  await result(page, "정상", "필터 깨끗함");
  await result(page, "정상");
  await step(page, 3).click();
  await pick(page, "흡기 필터 육안 점검 · 4.3");
  await expect(page.locator(".pdet .cd-h b")).toHaveText("흡기 필터 육안 점검 · 4.3");
  await page.locator(".btn", { hasText: "이 기록 정정" }).click();
  const modal = form(page);
  await modal.locator(".rbtn", { hasText: "이상" }).click();
  await modal.getByLabel("정정 사유").fill("뒷면 기름때");
  await modal.locator(".btn", { hasText: "정정 저장" }).click(); // 지금 단계가 지시가 아니면 확인 창 없이 다시 판정
  await page.locator(".pli.void").first().click();
  await expect(page.locator(".pdet .cd-h .ch.gray")).toHaveText("정정으로 무효"); // AC-46-C2: 지우지 않고 무효
  await expect(page.locator(".btn", { hasText: "이 기록 정정" })).toHaveCount(0);
  await toNow(page);
  await expect(page.locator(".tli.void .ch.vd").first()).toHaveText("정정으로 무효");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  const path = page.locator('[data-field="check_path"] .rv2');
  await expect(path).toContainText("정정 1회"); // AC-45-C5 · 결정 019 ⑤
  await expect(path).toContainText("정정으로 무효");
  await shot(page, info, "T09-draft_corrected");
  await confirmReportModal(page);
  await expect(page.locator(".banner.ok")).toBeVisible();
  await step(page, 3).click(); // AC-48-C2: 확정 세션은 정정 수단 없이 안내
  await expect(page.locator(".pdet")).toBeVisible();
  await expect(page.locator(".btn", { hasText: "이 기록 정정" })).toHaveCount(0);
  await expect(page.locator(".pdet .hint")).toHaveText("보고서를 확정한 세션이라 정정할 수 없습니다. 파트장이 반려하면 다시 정정할 수 있습니다.");
  await shot(page, info, "T15-closed");
  await step(page, 4).click();
  await expect(page.locator(".pli.on b").first()).toHaveText("흡기 필터 교체 · 4.3.2");
  await expect(page.locator(".btn", { hasText: "메모 덧붙이기" })).toHaveCount(0);
  await shot(page, info, "T15-action");
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await lead.locator(".lbar button.lbtn", { hasText: "반려" }).click();
  await lead.getByLabel("반려 사유").fill("4.3 기록 다시 확인");
  await lead.locator(".lmdl button.lbtn", { hasText: "반려" }).click();
  await page.goto(`/session/${sid}`); // AC-48-C3: 반려로 다시 열린 세션은 다시 정정
  await page.getByRole("button", { name: "점검 지난 화면" }).click(); // 세션을 받은 뒤에만 눌림 — 자리표시 진행 표시 클릭 경쟁(001-t)
  await pick(page, "흡기 필터 육안 점검 · 4.3");
  await expect(page.locator(".btn", { hasText: "이 기록 정정" })).toBeVisible();
  g.check(info);
  lg.check(info);
});

test("AC-47-C2 · L04 / call_corrected · L06 / correction: 보낸 호출 뒤 정정 → 새 호출 · 다시 판정 없이 파트장 정정 상자 · 감사 정정 행", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toCheck(page, "DEMO-03");
  for (let i = 0; i < 4 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await page.getByText("파트장 호출").click();
  await expect(page.locator(".wait")).toBeVisible(); // 보낸 뒤 꼴(답장 u4-audit-fix)
  await step(page, 3).click();
  await pick(page, "흡기 필터 육안 점검 · 4.3");
  await page.locator(".btn", { hasText: "이 기록 정정" }).click();
  const modal = form(page);
  await modal.locator(".rbtn", { hasText: "이상" }).click();
  await modal.getByLabel("정정 사유").fill("필터 뒷면 기름때를 못 보고 정상으로 눌렀음");
  await modal.locator(".btn", { hasText: "정정 저장" }).click();
  await expect(page.locator(".pdet .cd-h .ch.amber")).toHaveText("정정 1회");
  const s = await page.request.get(`/api/tech/sessions/${sid}`).then((r) => r.json());
  expect([s.step.kind, s.call.status]).toEqual(["call", "sent"]); // 다시 판정하지 않음 · 새 호출 없음
  await pick(page, "파트장 호출"); // 보낸 호출 기록
  await expect(page.locator(".pdet .cd-h b")).toHaveText("파트장 호출");
  await expect(page.locator(".corr-box div")).toHaveText("4.3 흡기 필터 육안 점검 · 정상 → 이상"); // 지난 화면 호출 기록에도 상자
  await shot(page, info, "T15-call");
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.goto(LEAD_URL + "/calls");
  await expect(lead.locator("tr[data-call]")).toHaveCount(1);
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await expect(lead.locator('[data-part="correction"] div')).toHaveText(["4.3 흡기 필터 육안 점검 · 정상 → 이상", "사유 · 필터 뒷면 기름때를 못 보고 정상으로 눌렀음"]);
  await shot(lead, info, "L04-call_corrected");
  await lead.goto(LEAD_URL + "/audit"); // AC-45-C3: 정정 기록이 파트장 감사 기록에
  await expect(lead.locator(".lt td", { hasText: "정정 · 4.3 결과 정상 → 이상 · 사유 '필터 뒷면 기름때를 못 보고 정상으로 눌렀음'" })).toBeVisible();
  await shot(lead, info, "L06-correction");
  g.check(info);
  lg.check(info);
});

test("AC-47-C1 · 결정 012 ② · 019 ⑧: 조치 뒤 정정 → 정정 호출 요약 → 파트장 '승인 후 진행'(정정 때 하던 단계로) → 재가동 확인", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toCheck(page);
  await result(page, "이상", "기름때");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await expect(page.locator(".act .btn", { hasText: /^재가동$/ })).toBeVisible();
  await step(page, 3).click();
  await page.locator(".btn", { hasText: "이 기록 정정" }).click();
  const modal = form(page);
  await modal.locator(".rbtn", { hasText: "정상" }).click();
  await modal.getByLabel("정정 사유").fill("다시 보니 깨끗함");
  await modal.locator(".btn", { hasText: "정정 저장" }).click();
  await toNow(page);
  await expect(page.locator(".warn-h .sm")).toHaveText("파트장 확인이 필요합니다"); // T10 / summary_corrected · 답장 001-zzx 사(가이드 24쪽)
  await expect(page.locator(".warn-h h3")).toHaveText("정정 확인 · 4.3 흡기 필터 육안 점검");
  await expect(page.locator(".warn-h .tags .ch")).toHaveText(["이상 → 정상"]);
  await expect(page.locator(".hint")).toHaveCount(0);
  await shot(page, info, "T10-summary_corrected");
  await page.getByText("파트장 호출").click();
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.goto(LEAD_URL + "/calls");
  await expect(lead.locator("tr[data-call] td").nth(4)).toHaveText("정정 확인 · 4.3 흡기 필터 육안 점검(이상 → 정상)");
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await expect(lead.locator(".lkv")).toContainText("정정 확인 · 4.3 흡기 필터 육안 점검(이상 → 정상)");
  await lead.locator(".lopts button", { hasText: "승인 후 진행" }).click();
  await shot(lead, info, "L04-correction_call_detail");
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click(); // 승인 후 진행(골라야 열림 — D-528)
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  await page.reload();
  await expect(page.locator(".act .btn", { hasText: /^재가동$/ })).toBeVisible(); // 새 작업 없이 정정 때 하던 단계(재가동 확인)
  const s = await page.request.get(`/api/tech/sessions/${sid}`).then((r) => r.json());
  expect(s.step.kind).toBe("restart_check");
  g.check(info);
  lg.check(info);
});
