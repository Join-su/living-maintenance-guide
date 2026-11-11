// 정비자 앱 E2E(P-10 L3, S-03): 조치 · 재가동 · 호출 · 인계 · 감시 마감 · 보고서. 실제 nginx · API · PostgreSQL.
// 파트장 답 · 정정 기록은 시험 DB 시뮬레이션(S-04 P-11 · S-05 P-14 전, PBC-4 v10 실행 해석 ③). fixture 모드, 외부 요청 0.

import { expect, test, type Page } from "@playwright/test";
import { alarm, guard, login, psql, resetOps, restartWithin, result, shot, apiCorrection, simLeadAnswer, simLlmFailure, simR5Example, toFirstCheck, confirmReportModal, snapTo } from "./helpers";

test.beforeEach(() => resetOps());

test("SC-06 · AC-07 · AC-33 · AC-21 · AC-32-C1: 4.3 이상 → 조치 완료 → 재가동 → 경계 이내 → 보고서 → 근거 없는 수치 → 확정 잠김 → 고쳐 확정 → 승인 대기", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toFirstCheck(page);
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  await result(page, "이상", "기름때가 필터 표면 절반 넘게 덮음");
  await expect(page.locator(".stk .cd-h .ch.red")).toHaveText("냉각 필터 막힘");
  await expect(page.locator(".ap-step .sp.on")).toContainText("조치");
  await shot(page, info, "T08-action_a");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await expect(page.locator(".act .ch.green")).toHaveText("완료"); // 답장 001-zzx 가(가이드 15쪽)
  await shot(page, info, "T08-restart_checklist");
  await restartWithin(page);
  await expect(page.locator(".rvrow > span").last()).toHaveText("경계 8.2 K"); // 재가동 카드 안 값 줄(가이드 16쪽)
  await shot(page, info, "T08-restart_check");
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await expect(page.locator(".rf")).toHaveCount(8);
  await expect(page.locator(".ap-step .sp.on")).toContainText("보고서");
  await expect(page.locator('[data-field="cause"] .rv2')).toHaveText("냉각 필터 막힘");
  await shot(page, info, "T09-draft");
  // AC-07-C1: 근거 없는 수치로 고치면 실패 항목 · 배너 · 확정 잠김
  const action = (await page.locator('[data-field="action"] .rv2').textContent())!;
  await page.locator('[data-field="action"] .ed').click();
  await page.locator('[data-field="action"] textarea').fill(`${action} · 교체 후 온도 차 9.4 K로 회복`);
  await page.locator(".cd-h b", { hasText: "정비 보고서 초안" }).click();
  await expect(page.locator(".rf.fail .rk2")).toHaveText("조치");
  await expect(page.locator(".banner.bad b")).toHaveText("근거 없는 문장 1건");
  await expect(page.locator(".cta2 .btn", { hasText: "보고서 확정" })).toHaveClass(/dis/);
  await shot(page, info, "T09-check_failed");
  await page.locator(".cta2 .btn", { hasText: "보고서 확정" }).click({ force: true });
  await expect(page.locator(".modal")).toHaveCount(0); // 잠긴 확정은 확인 창도 열지 않음
  expect(psql(`select count(*) from ops.reports where session_id = ${sid}`)).toBe("0");
  await page.locator('[data-field="action"] .ed').click();
  await page.locator('[data-field="action"] textarea').fill(action);
  await page.locator(".cd-h b", { hasText: "정비 보고서 초안" }).click();
  await expect(page.locator(".rf.fail")).toHaveCount(0);
  await confirmReportModal(page); // AC-07-C2
  await expect(page.locator(".banner.ok b")).toHaveText(/^보고서를 확정했습니다 · \d\d:\d\d$/);
  await expect(page.locator(".cd-h .ch.green")).toHaveText("확정 · 승인 대기");
  await expect(page.locator(".rf .ed svg")).toHaveCount(0); // AC-33-C1: 확정 뒤 고치기 없음
  expect(psql(`select status from ops.reports where session_id = ${sid}`)).toBe("awaiting_approval");
  await shot(page, info, "T09-confirmed");
  await page.reload(); // AC-07-C2 · AC-33-C1: 승인 대기가 서버에서 다시 읽히고, 다시 열어도 수정 수단 없음
  await expect(page.locator(".cd-h .ch.green")).toHaveText("확정 · 승인 대기");
  await expect(page.locator(".rf .ed svg")).toHaveCount(0);
  await expect(page.locator(".rf textarea")).toHaveCount(0);
  await page.locator('[data-field="cause"] .ed').dispatchEvent("click");
  await expect(page.locator(".rf textarea")).toHaveCount(0);
  await page.getByText("홈으로").click();
  await expect(page.locator(".rl").first()).toContainText("M-01 · 냉각 필터 막힘"); // D-642 D 보고서 한 줄 = 설비 · 원인 이름(시각은 따로 칸)
  await expect(page.locator(".rl .ch").first()).toHaveText("승인 대기");
  await shot(page, info, "T02-recent_report");
  g.check(info);
});

test("AC-03 L3 · AC-30 · AC-08 · AC-37: R5 계산 예시 → 4.3 → 정상 뒤 57 · 22 · 21 호출 요약 → 대기 → 요약 고쳐 보내기 → 승인 답 → 종 '답 보기' → 4.4 지시", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  let sid = 0;
  await toFirstCheck(
    page,
    "DEMO-01",
    (s) => {
      sid = s;
      simR5Example(s);
    },
    async () => {
      // AC-03-C1(G6-S03 B1): 정비자 앱 진단 화면 1순위 = 냉각 필터 막힘(재시연 3 R2: 화면에 % 0 · 건수만)
      await expect(page.locator(".rk.on b")).toHaveText("냉각 필터 막힘");
      await expect(page.locator(".rk .rv")).toHaveCount(0);
      await shot(page, info, "T04-r5_example");
    },
  );
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검"); // AC-03-C2
  await result(page, "정상");
  await expect(page.locator(".warn-h .sm")).toHaveText("다음 점검은 혼자 진행할 수 없습니다"); // AC-03-C3 · AC-02-C2
  await expect(page.locator(".warn-h h3")).toHaveText("냉각 팬 분해 점검");
  await expect(page.locator(".tags .ch.gray").filter({ hasText: "%" })).toHaveText(["냉각 팬 열화 57%", "냉각수 부족 22%", "냉각 설정 오류 21%"]);
  await expect(page.locator(".rbtns")).toHaveCount(0); // AC-08-C1: 답 전에는 C 지시 내용 없음
  await shot(page, info, "T10-summary");
  await page.getByText("호출하지 않고 대기").click();
  await expect(page.locator(".banner.warn b")).toHaveText(/^호출하지 않고 대기 중 · \d\d:\d\d$/);
  expect(psql(`select count(*) from ops.calls where session_id = ${sid}`)).toBe("0"); // AC-30-C1
  await shot(page, info, "T10-waiting");
  await page.locator(".pv p").click();
  const text = (await page.locator(".pv textarea").inputValue()) + " 정비자 메모: 팬 소음 있음.";
  await page.locator(".pv textarea").fill(text);
  await page.getByText("파트장 호출").click();
  await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
  expect(psql(`select summary from ops.calls where session_id = ${sid}`)).toBe(text);
  await expect(page.locator(".ap .sml")).toHaveText("보낸 요약"); // 이름표 칸 밖 위(답장 g3-audit-fix)
  await expect(page.locator("button.n-li .n-ls").first()).toHaveText(/진행 중 · 파트장 호출$/, { timeout: 10000 }); // 왼쪽 알람 칸
  await shot(page, info, "T10-sent");
  await snapTo(page, "G3b-call-sent-4.4");
  simLeadAnswer(sid, "approved");
  await page.locator(".tb-home").click();
  await page.locator(".tb-bell").click();
  await expect(page.locator(".panel .grp")).toContainText(["파트장 답이 온 호출"]);
  await expect(page.locator(".nt-item", { hasText: "냉각 팬 분해 점검(4.4, C)" })).toContainText("승인 후 진행");
  await page.locator(".nt-item .btn", { hasText: "답 보기" }).click(); // AC-37-C2
  await expect(page.locator(".banner.ok b")).toHaveText(/^파트장 답 · 승인 후 진행 · .+ \d\d:\d\d$/);
  await expect(page.locator(".ins-h .ch.green")).toHaveText("승인됨");
  await shot(page, info, "T10-ans_approved");
  await page.getByText("점검 시작").click();
  await expect(page.locator(".ins-h h3")).toHaveText("냉각 팬 분해 점검"); // AC-08-C2
  await expect(page.locator(".rbtns")).toBeVisible();
  g.check(info);
});

test("AC-23-C1 · C2: 주간 처리 답 → 답 화면 → 보고서 작성 → '미확인 · 요청', 답 전 설비 정지 대기 · 직접 확인 답(결정 018 ③) → '파트장 직접 확인 · 처리 결과'", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toFirstCheck(page, "DEMO-03");
  for (let i = 0; i < 4 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await expect(page.locator(".warn-h h3")).toBeVisible();
  await page.getByText("파트장 호출").click();
  await expect(page.locator(".wait")).toBeVisible(); // 보낸 뒤 꼴(답장 u4-audit-fix)
  simLeadAnswer(sid, "daytime");
  await page.reload();
  await expect(page.locator(".banner.warn b")).toHaveText("주간 처리로 결정 · 저부하 감시 운전");
  await shot(page, info, "T10-ans_daytime");
  await page.getByText("보고서 작성").click();
  await expect(page.locator('[data-field="unresolved"] .rv2')).toContainText("주간 처리 요청");
  await expect(page.locator('[data-field="cause"] .rv2')).toHaveText("원인 미상");
  const sid2 = await toFirstCheckFromHome(page, "DEMO-04");
  for (let i = 0; i < 4 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await page.getByText("파트장 호출").click();
  await expect(page.locator(".wait")).toBeVisible(); // 보낸 뒤 꼴(답장 u4-audit-fix)
  await expect(page.locator(".pv p")).toContainText("설비는 정지 상태로 대기 중"); // 답 전: 보냄 · 설비 정지 대기
  simLeadAnswer(sid2, "direct");
  await page.reload();
  await expect(page.locator(".banner.info b")).toHaveText("파트장 직접 확인 · 처리 결과"); // 결정 018 ③
  await shot(page, info, "T10-ans_direct");
  g.check(info);
});

async function toFirstCheckFromHome(page: Page, scenario: string): Promise<number> {
  await page.locator(".tb-home").click();
  return toFirstCheck(page, scenario);
}

test("AC-19-C3 · AC-20 · AC-21-C3: 확인 불가 반복 · 맞지 않는 소견 · 재가동 뒤 밖 → 숙련자 인계 → 보고서 '원인 미상'", async ({ page }, info) => {
  const g = guard(page);
  psql("update ops.grade_table set grade = 'A' where section_id = '4.4'");
  await login(page);
  const sid = await toFirstCheck(page);
  for (let i = 0; i < 8 && (await page.locator(".rbtns").count()); i++) {
    const t = await page.locator(".ins-h h3").textContent();
    await result(page, t === "흡기 필터 육안 점검" || t === "냉각 팬 분해 점검" ? "확인 불가" : "정상");
  }
  await expect(page.locator(".warn-h .sm")).toHaveText("확인 불가가 반복됨");
  await expect(page.locator(".pv p")).toContainText("확인 불가 2회");
  await expect(page.locator(".cki .t").filter({ hasText: "확인 불가 2회 · 제외" }).first()).toBeVisible(); // 답장 001-zzx 바: 체크 목록
  await shot(page, info, "T11-unable_repeat");
  await page.locator(".btn.warnb", { hasText: "숙련자 인계" }).click();
  await expect(page.locator(".wait b")).toHaveText(/^숙련자 인계 보냄 · \d\d:\d\d$/); // 결정 018 ④: 답 대기
  await expect(page.locator("button.n-li .n-ls").first()).toHaveText(/진행 중 · 숙련자 인계$/, { timeout: 10000 }); // 왼쪽 알람 칸(답장 g3-audit-fix)
  await snapTo(page, "G3a-handover-sent");
  simLeadAnswer(sid, "assign");
  await page.reload();
  await page.locator(".btn", { hasText: "보고서 작성" }).click();
  await expect(page.locator('[data-field="cause"] .rv2')).toHaveText("원인 미상");
  expect(psql(`select kind from ops.calls where session_id = ${sid}`)).toBe("handover");
  psql("update ops.grade_table g set grade = s.initial_grade from knowledge.manual_sections s where s.section_id = g.section_id");
  // AC-20: 맞지 않는 소견 → 인계 선택 → 인계 요약
  await toFirstCheckFromHome(page, "DEMO-02");
  await result(page, "정상", "바닥에서 타는 냄새");
  await page.locator(".modal .btn", { hasText: "숙련자 인계" }).click();
  await expect(page.locator(".warn-h h3")).toHaveText("후보 원인과 맞지 않는 소견");
  await shot(page, info, "T11-expert_handover");
  // AC-21-C3: 원인 확정 · 조치 뒤 재가동이 여전히 밖
  await toFirstCheckFromHome(page, "DEMO-03");
  await result(page, "이상", "기름때");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await page.locator(".choice2 .btn", { hasText: "여전히 밖" }).click();
  await page.locator('.modal[aria-label="숙련자 인계 확인"] .btn', { hasText: "숙련자 인계" }).click(); // D-530 답장 001-zzt 6: 확인 창
  await expect(page.locator(".warn-h h3")).toHaveText("재가동 뒤에도 값이 경계 밖");
  g.check(info);
});

test("AC-22 · AC-32-C2 · AC-43-C4: 모두 정상 → 재가동 → 6.2 감시 마감 → '이상 없음 · 감시', B 조치 체크리스트", async ({ page }, info) => {
  const g = guard(page);
  psql("update ops.grade_table set grade = 'A' where section_id = '4.4'");
  await login(page);
  await toFirstCheck(page);
  for (let i = 0; i < 6 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await expect(page.locator(".stk .ch.red")).toHaveCount(0); // 원인 확정 없이 재가동 확인
  await restartWithin(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await expect(page.locator(".done-h .sm")).toHaveText("이상 없음 · 감시");
  await shot(page, info, "T11-monitor_close");
  await page.getByText("보고서 작성").click();
  await expect(page.locator('[data-field="cause"] .rv2')).toHaveText("이상 없음 · 감시");
  await shot(page, info, "T09-monitor_report");
  psql("update ops.grade_table set grade = 'B' where section_id = '4.3.2'");
  await toFirstCheckFromHome(page, "DEMO-02");
  await result(page, "이상", "기름때");
  await expect(page.locator(".act .sm")).toHaveCount(0); // D-528: 체크리스트 안내 문장 뺌(잠김은 버튼이 보임)
  await expect(page.locator(".act .btn", { hasText: "조치 진행" })).toHaveClass(/dis/);
  const items = page.locator(".act .ck");
  for (let i = 0; i < (await items.count()); i++) await items.nth(i).click();
  await shot(page, info, "T08-checklist_b");
  await page.locator(".act .btn", { hasText: "조치 진행" }).click();
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await expect(page.locator(".act .btn", { hasText: /^재가동$/ })).toBeVisible();
  g.check(info);
});

test("T08 / corrected_path(정정 API — S-05 P-14): 4.3 정정 → 다시 판정 배너 · 뒤 기록 '정정으로 무효' · 조치", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toFirstCheck(page);
  await result(page, "정상");
  await result(page, "정상");
  await apiCorrection(page, sid, "4.3", "이상");
  await page.reload();
  await expect(page.locator(".banner.info b")).toHaveText(/^4\.3 정정으로 다시 판정했습니다 · \d\d:\d\d$/);
  await expect(page.locator(".tli.void .ch.vd").first()).toHaveText("정정으로 무효");
  await expect(page.locator(".tli.bad span").filter({ hasText: /정정 1회\(\d\d:\d\d, 정상 → 이상\)/ })).toBeVisible();
  await expect(page.locator(".act .btn", { hasText: "조치 완료" })).toBeVisible();
  await shot(page, info, "T08-corrected_path");
  g.check(info);
});

test("AC-31-C2 연결(사용자 결정 016 ④): 문장 생성 3회 연속 실패 → '숙련자 인계' → 인계 요약 → 보고서 '원인 미상'", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toFirstCheck(page, "DEMO-05");
  simLlmFailure(sid, "all");
  await page.reload();
  await expect(page.locator(".banner.bad b")).toHaveText("안내 문장 생성이 3회 연속 실패했습니다");
  await page.locator(".choice2 .btn", { hasText: "숙련자 인계" }).click();
  await expect(page.locator(".warn-h h3")).toHaveText("안내 문장 생성이 반복해서 실패함");
  await shot(page, info, "T11-llm_failure");
  await page.locator(".btn.warnb", { hasText: "숙련자 인계" }).click();
  await expect(page.locator(".wait b")).toHaveText(/^숙련자 인계 보냄 · \d\d:\d\d$/); // 결정 018 ④: 답 대기
  simLeadAnswer(sid, "assign");
  await page.reload();
  await page.locator(".btn", { hasText: "보고서 작성" }).click();
  await expect(page.locator('[data-field="cause"] .rv2')).toHaveText("원인 미상");
  expect(psql(`select kind from ops.calls where session_id = ${sid}`)).toBe("handover");
  g.check(info);
});

test("AC-02 L3(P-10): 첫 판정 호출 — T04 / first_call → 잠금 → 사전 확인 → T10 / summary '점검 전', 절차 없음", async ({ page }, info) => {
  const g = guard(page);
  psql("update ops.grade_table set grade = 'C' where section_id in ('4.3', '4.5', '4.6')");
  await login(page);
  alarm("DEMO-03");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.getByText("진단 보기").click();
  await expect(page.locator(".nx h3")).toHaveText("파트장 호출 필요");
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".warn-h .sm")).toHaveText("다음 점검은 혼자 진행할 수 없습니다");
  await expect(page.locator(".dn2 span")).toHaveText(["점검 전"]);
  await expect(page.locator(".pv p")).toContainText("점검 전");
  await expect(page.locator(".proc")).toHaveCount(0);
  await expect(page.locator(".rbtns")).toHaveCount(0);
  await shot(page, info, "T10-summary-first_call-locked");
  g.check(info);
});

test("AC-20 L3 · AC-43 L3: 맞지 않는 소견 → '계속 진행' → 순위 그대로 · 다음 점검, B 점검은 체크리스트 전 결과 입력 잠김", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  await toFirstCheck(page, "DEMO-01");
  const view = () => page.request.get(page.url().replace(/\/session\//, "/api/tech/sessions/")).then((r) => r.json());
  const before = await view();
  await page.locator(".rbtn", { hasText: "정상" }).click();
  await page.getByLabel("메모").fill("바닥에서 타는 냄새");
  await page.getByText("결과 보내기").click();
  await expect(page.locator(".modal")).toBeVisible();
  const asking = await view();
  expect(asking.probabilities).toEqual(before.probabilities); // AC-20-C1: 맞지 않는 소견은 순위를 바꾸지 않음
  expect(asking.step.kind).toBe("out_of_scope"); // AC-20-C3: 고르기 전에는 인계 안 함
  await page.locator(".modal .btn", { hasText: "계속 진행" }).click();
  await expect(page.locator(".modal")).toHaveCount(0);
  await expect(page.locator(".rbtns")).toBeVisible(); // 계속 → 결과 버튼 값대로 다음 점검
  await shot(page, info, "T06-after_continue");
  psql("update ops.grade_table set grade = 'B' where section_id = '4.3'");
  await toFirstCheckFromHome(page, "DEMO-02");
  await expect(page.locator(".act.pre")).toBeVisible();
  await expect(page.locator(".rbtn").first()).toHaveClass(/dis/); // AC-43-C1
  const items = page.locator(".act.pre .ck");
  for (let i = 0; i < (await items.count()); i++) await items.nth(i).click();
  await expect(page.locator(".rbtn").first()).not.toHaveClass(/dis/); // AC-43-C3
  await shot(page, info, "T06-b_precheck_done");
  g.check(info);
});
