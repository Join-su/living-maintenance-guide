// 두 앱 교차 E2E(P-13 L3, S-04): 정비자 앱(8081) · 파트장 앱(8082)을 서로 다른 browser context로 연다.
// 실제 nginx · API · PostgreSQL, fixture 모드, 외부 요청 0. 파트장 답 · 반려 · 승인 · 등급 변경은 실제 파트장 화면으로 한다.

import { expect, test, type Browser, type Page } from "@playwright/test";
import { alarm, guard, LEAD_URL, login, loginLead, psql, resetOps, restartWithin, result, shot, sidOf, TECH_PW, toFirstCheck, confirmReportModal } from "./helpers";

test.beforeEach(() => resetOps());

async function leadPage(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 620 } });
  const page = await ctx.newPage();
  await loginLead(page);
  await expect(page.locator(".lnav")).toBeVisible();
  return page;
}

/** 정비자: 4.3 이상 → 교체 → 재가동 → 경계 이내 → 보고서 확정(승인 대기). */
async function confirmReport(page: Page, scenario = "DEMO-01"): Promise<number> {
  const sid = await toFirstCheck(page, scenario);
  await result(page, "이상", "기름때가 필터 표면 절반 넘게 덮음");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await confirmReportModal(page);
  await expect(page.locator(".banner.ok b")).toHaveText(/^보고서를 확정했습니다/);
  return sid;
}

async function home(page: Page): Promise<void> {
  await page.locator(".tb-home").click();
  await expect(page.locator(".g2")).toBeVisible();
}

test("AC-27 · AC-09(SC-33, A-07 확인 문장) · AC-29-C3: 승인 대기 목록(빈 목록 → 행) → 검토 → 그대로 승인 → 이력 R-번호 · 다음 알람 진단 · R-번호 기록", async ({ page, browser }, info) => {
  const g = guard(page);
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다"); // AC-27-C3
  await shot(lead, info, "L02-empty");
  await login(page);
  const sid = await confirmReport(page);
  await lead.reload();
  await expect(lead.locator("tr[data-report]")).toHaveCount(1);
  await expect(lead.locator(".ltiles b")).toHaveText(["1", "0", "0"]); // AC-27-C1
  await expect(lead.locator("tr[data-report] td").nth(1)).toHaveText("냉각 필터 막힘");
  await shot(lead, info, "L02-list");
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  // D-528: 보고서 검토 = 최종본 문서(구역 1 ~ 5) · 결정은 승인 · 반려 둘
  await expect(lead.locator(".fdoc h2")).toHaveText(["1. 이상 내용 · 센서 근거", "2. 원인 분석", "3. 조치 경과", "4. 조치 결과", "5. 미확인 · 주간 요청"]);
  await expect(lead.locator(".fdoc tr.hot td").last()).toHaveText("확정 · 4.3 점검 이상");
  await shot(lead, info, "L03-review");
  await lead.locator(".lbar button.lbtn", { hasText: "승인" }).click();
  await lead.locator(".lmdl button.lbtn", { hasText: "승인" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");
  const rid = Number(psql(`select report_id from ops.reports where session_id = ${sid}`));
  const hid = `R-${String(rid).padStart(4, "0")}`;
  expect(psql(`select source from knowledge.history_records where record_id = '${hid}'`)).toBe("approved_report"); // AC-09-C1 이력 한 건(다음 진단 후보에 드는 것은 배포 서버 R12 — EV-001 ES-17 CH-32)
  await home(page);
  await expect(page.locator(".rl .ch").first()).toHaveText("승인 완료");
  // 다음 같은 증상군 알람(G6-S04 revise 사용자 결정): 진단 화면 카드(상위 10건 중 score ≥ 0.5)를 기록한다 — 후보는 Pack start 출력이고,
  // 승인 기록이 후보에 드는 것은 배포 서버 R12 뒤(ES-17 CH-32 — 가짜 배포 서버에는 R12가 없어 카드에 없음). 기록 창이 쓰는 같은 API로 R-번호가 열린다.
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  // D-663 S1: 다른 설비 비슷한 기록 카드는 화면에서 뺌(응답 similar_cards는 그대로 — 아래 기록만)
  await expect(page.locator('[data-part="other-ref"]')).toHaveCount(0);
  const next = await page.request.get(`/api/tech/sessions/${sidOf(page)}`).then((r) => r.json());
  const cards: string[] = next.similar_cards.map((c: { record_id: string }) => c.record_id);
  expect(cards.length).toBeGreaterThan(0);
  // 카드에 R-번호가 있는지는 단언하지 않고 기록만 한다(들어오면 바람직한 변화 — G6-S04-2 비차단 ②)
  await info.attach("next-alarm-cards", { body: JSON.stringify({ hid, cards, contains: cards.includes(hid) }), contentType: "application/json" });
  await shot(page, info, "T04-next_alarm-after_approve");
  const rec = await page.request.get(`/api/records/${hid}`);
  expect(rec.status()).toBe(200);
  expect((await rec.json()).source).toBe("approved_report");
  g.check(info);
  lg.check(info);
});

test("교차 ① AC-08 · AC-42 · AC-37: C 호출 → 파트장 '승인 후 진행' → 종 '답 보기' → 4.4 지시 → 정상 → 남은 후보 다음 지시", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toFirstCheck(page, "DEMO-03");
  for (let i = 0; i < 4 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await expect(page.locator(".warn-h h3")).toHaveText("냉각 팬 분해 점검");
  await page.getByText("파트장 호출").click();
  await expect(page.locator(".wait")).toBeVisible(); // 보낸 뒤 꼴(답장 u4-audit-fix)
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await expect(lead.locator(".ltiles b").nth(1)).toHaveText("1");
  await lead.locator(".lnav a", { hasText: "호출" }).click();
  await expect(lead.locator("tr[data-call] td").nth(4)).toHaveText("냉각 팬 분해 점검(4.4, C)");
  await shot(lead, info, "L04-list");
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await expect(lead.locator(".lopts button")).toHaveText(["승인 후 진행", "주간 처리", "직접 확인"]);
  await expect(lead.locator(".lbtn", { hasText: "답 저장" })).toBeDisabled(); // D-528: 답을 골라야 열림
  await shot(lead, info, "L04-call_detail");
  await lead.locator(".lopts button", { hasText: "승인 후 진행" }).click();
  await lead.getByLabel("처리 메모").fill("팬 점검 승인");
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  expect(psql(`select answer->>'by' from ops.calls where session_id = ${sid}`)).toBe("lead.park"); // AC-08-C2: 승인자
  await home(page);
  await page.locator(".tb-bell").click();
  await expect(page.locator(".panel .grp")).toContainText(["파트장 답이 온 호출"]);
  await page.locator(".nt-item .btn", { hasText: "답 보기" }).click();
  await expect(page.locator(".banner.ok b")).toHaveText(/^파트장 답 · 승인 후 진행 · /);
  await page.getByText("점검 시작").click();
  await expect(page.locator(".ins-h h3")).toHaveText("냉각 팬 분해 점검");
  await result(page, "정상");
  await expect(page.locator(".rbtns, .warn-h, .act").first()).toBeVisible(); // AC-42-C2: 남은 후보의 다음 단계
  const step = await page.request.get(`/api/tech/sessions/${sid}`).then((r) => r.json());
  expect(step.step.section).not.toBe("4.4");
  await shot(page, info, "T06-second_check-after_approved");
  g.check(info);
  lg.check(info);
});

test("교차 AC-23: 파트장 답 주간 처리 · 직접 확인 → 정비자 답 화면 · 보고서 미확인 · 요청에 기록, D 호출에는 '승인 후 진행' 없음", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  await toFirstCheck(page, "DEMO-03");
  for (let i = 0; i < 4 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await page.getByText("파트장 호출").click();
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.goto(LEAD_URL + "/calls");
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await lead.locator(".lopts button", { hasText: "직접 확인" }).click();
  await lead.getByLabel("처리 메모").fill("팬 베어링 소음 확인, 주간 교체 예정");
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await page.reload();
  await expect(page.locator(".banner.info b")).toHaveText("파트장 직접 확인 · 처리 결과");
  await page.locator(".btn", { hasText: "보고서 작성" }).click();
  await expect(page.locator('[data-field="unresolved"] .rv2')).toContainText("파트장 직접 확인 · 팬 베어링 소음 확인, 주간 교체 예정");
  // D 등급 호출
  psql("update ops.grade_table set grade = 'D' where section_id = '4.4'");
  await home(page);
  await toFirstCheck(page, "DEMO-04");
  for (let i = 0; i < 4 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await page.getByText("파트장 호출").click();
  await lead.goto(LEAD_URL + "/calls");
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await expect(lead.locator(".lopts button")).toHaveText(["주간 처리", "직접 확인"]); // AC-23-C3
  await shot(lead, info, "L04-call_detail_d");
  await lead.locator(".lopts button", { hasText: "주간 처리" }).click();
  await lead.getByLabel("처리 메모").fill("주간 정비 배정");
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await page.reload();
  await expect(page.locator(".banner.warn b")).toHaveText("주간 처리로 결정 · 저부하 감시 운전"); // AC-23-C1
  await page.locator(".btn", { hasText: "보고서 작성" }).click();
  await expect(page.locator('[data-field="unresolved"] .rv2')).toContainText("주간 처리 요청 · 주간 정비 배정");
  g.check(info);
  lg.check(info);
});

test("교차 ③ AC-25: 파트장 등급 올림 · 낮춤 경고 → 확인해야 저장 → 진행 중 세션 다음 지시에 반영 · 열린 화면 그대로 · 변경 기록", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await toFirstCheck(page, "DEMO-01");
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  const opened = await page.locator(".ins-h").innerText();
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.locator(".lnav a", { hasText: "권한 등급" }).click();
  await expect(lead.locator('[data-part="changes"]')).toHaveText("변경 기록 없음");
  await shot(lead, info, "L05-default");
  await lead.locator('tr[data-section="4.6"] .lseg button', { hasText: "C" }).click();
  await expect(lead.locator(".lmdl h4")).toHaveText("4.6 냉각 설정값 확인 등급을 A → C로 바꿉니까?");
  await expect(lead.locator('.lmdl [data-part="warning"] b')).toHaveText("등급을 올리려 합니다 · 4.6 냉각 설정값 확인 A → C");
  await shot(lead, info, "L05-raise_warning");
  await lead.locator(".lmdl button.lbtn", { hasText: "취소" }).click(); // 취소하면 저장 안 됨
  expect(psql("select grade from ops.grade_table where section_id = '4.6'")).toBe("A");
  await lead.locator('tr[data-section="4.6"] .lseg button', { hasText: "C" }).click();
  await lead.locator(".lmdl button.lbtn", { hasText: "바꾸기" }).click();
  await expect(lead.locator('[data-part="changes"] div').filter({ hasText: "A → C" })).toBeVisible(); // AC-25-C2 변경 기록
  await lead.locator('tr[data-section="4.4"] .lseg button', { hasText: "B" }).click();
  await expect(lead.locator('.lmdl [data-part="warning"] b')).toHaveText("등급을 낮추려 합니다 · 4.4 냉각 팬 분해 점검 C → B");
  await shot(lead, info, "L05-lower_warning");
  await lead.locator(".lmdl button.lbtn", { hasText: "바꾸기" }).click();
  await expect(lead.locator('[data-part="changes"] div').filter({ hasText: "C → B" })).toBeVisible();
  // 열린 지시의 그 절(4.3)도 올림 — 이미 열린 화면은 그대로여야 한다(AC-25-C4)
  await lead.locator('tr[data-section="4.3"] .lseg button', { hasText: "C" }).click();
  await lead.locator(".lmdl button.lbtn", { hasText: "바꾸기" }).click();
  await expect(lead.locator('[data-part="changes"] div').filter({ hasText: "A → C" })).toHaveCount(2);
  await page.reload();
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  expect(await page.locator(".ins-h").innerText()).toBe(opened); // AC-25-C4: 열린 지시 화면(제목 · 등급 표시) 그대로
  await shot(page, info, "T06-open_after_grade_change");
  await result(page, "정상");
  const s = await page.request.get(`/api/tech/sessions/${sid}`).then((r) => r.json());
  expect(s.step.section).not.toBe("4.6"); // AC-25-C1: 4.6이 C가 되어 다음 판정에서 지시되지 않음
  expect(s.instruction?.section).not.toBe("4.6");
  g.check(info);
  lg.check(info);
});

test("SC-35 · AC-24 · AC-11 · AC-37: 반려 사유 없으면 저장 안 됨 → 사유 반려 → 정비자 홈 반려 · 사유 · 종 '반려된 보고서' → 고쳐 다시 확정 → 승인 대기 목록에 다시", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  const sid = await confirmReport(page);
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await lead.locator(".lbar button.lbtn", { hasText: "반려" }).click();
  await expect(lead.locator(".lmdl button.lbtn", { hasText: "반려" })).toBeDisabled(); // AC-24-C1: 사유를 적어야 열림
  await lead.locator(".lmdl button.lbtn", { hasText: "반려" }).click({ force: true });
  await shot(lead, info, "L03-reject");
  expect(psql(`select status from ops.reports where session_id = ${sid}`)).toBe("awaiting_approval"); // AC-24-C1
  await home(page);
  await expect(page.locator(".banner.bad")).toHaveCount(0);
  await lead.getByLabel("반려 사유").fill("필터 표면 상태를 메모로 다시 적어 주세요");
  await lead.locator(".lmdl button.lbtn", { hasText: "반려" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");
  await page.reload();
  await expect(page.locator(".rl .ch").first()).toHaveText("반려"); // AC-11-C1
  await expect(page.locator(".banner.bad div")).toContainText("필터 표면 상태를 메모로 다시 적어 주세요");
  await shot(page, info, "T02-rejected");
  await page.locator(".tb-bell").click();
  await expect(page.locator(".panel .grp")).toContainText(["반려된 보고서"]);
  await shot(page, info, "T12-list-rejected");
  await page.locator(".nt-item .btn", { hasText: "보고서 보기" }).click(); // AC-37-C2: 반려 보고서가 열림
  await expect(page.locator(".cd-h .ch.red")).toHaveText("반려 · 고치는 중"); // D-528: 반려 뒤 다시 열린 초안
  await expect(page.locator('[data-part="rejection"]')).toContainText("필터 표면 상태를 메모로 다시 적어 주세요");
  await page.locator('[data-field="check_path"] .ed').click();
  const ta = page.locator('[data-field="check_path"] textarea');
  await ta.fill((await ta.inputValue()).replace("이상: 기름때", "이상: 기름때가 필터 표면 절반 넘게 덮음, 뒷면도 확인"));
  await page.locator(".cd-h b", { hasText: "정비 보고서 초안" }).click();
  await confirmReportModal(page); // AC-11-C2
  await expect(page.locator(".cd-h .ch.green")).toHaveText("확정 · 승인 대기");
  await lead.reload();
  await expect(lead.locator("tr[data-report]")).toHaveCount(1);
  g.check(info);
  lg.check(info);
});

test("AC-37: 종 목록 4종류(새 알람 · 진행 중 · 답이 온 호출 · 반려된 보고서) · 각 이동, AC-29-C3 근거 적음 행, AC-12 역할 거부 · AC-26 감사 필터", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  // 근거 적음 세션(S-02 · S-03과 같은 score 시뮬레이션) — 보고서 확정 전에 넣어야 확정한 보고서에 근거 적음이 남는다
  // AC-29: 진단(T04 / low_evidence) 배너 · 칩 → 다음 지시 그대로 나옴 → 보고서 근거 사례 줄 칩(파생 T09 / low_evidence) → 파트장 목록 행
  const lowEvidence = (sid: number) => {
    // 냉각 필터 막힘(CL-1) 기록 2건만 높게 — 시험 자료(Pack 후보)가 바뀌어도 첫 지시 4.3 · 근거 적음이 같게(D-429 p13에서 앞 2건이 CL-3라 4.5로 갔음)
    psql(`update ops.diag_sessions set "similar" = (select jsonb_agg(jsonb_set(e, '{score}', to_jsonb(case when k <= 2 then 0.9 else 0.3 end)) order by i) from (select e, i, row_number() over (order by (e->>'cause' = 'CL-1') desc, i) as k from jsonb_array_elements("similar") with ordinality as t(e, i)) x) where session_id = ${sid}`);
  };
  const sidR = await toFirstCheck(page, "DEMO-02", lowEvidence, async () => {
    await expect(page.locator(".banner.warn b")).toHaveText("근거 적음"); // AC-29-C1
    await shot(page, info, "T04-low_evidence");
  });
  await expect(page.locator(".ins-h h3")).toBeVisible(); // AC-29-C4: 근거 적음이어도 다음 점검 지시가 나옴
  const ins = await page.request.get(`/api/tech/sessions/${sidR}`).then((r) => r.json());
  expect(ins.low_evidence).toBe(true);
  expect(ins.step.kind).toMatch(/^instruct/);
  await result(page, "이상", "기름때");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await expect(page.locator('[data-field="evidence_records"] .ch.amber')).toHaveText("근거 적음"); // AC-29-C2
  await shot(page, info, "T09-low_evidence");
  await confirmReportModal(page);
  await expect(page.locator(".banner.ok")).toBeVisible();
  await home(page);
  await toFirstCheck(page, "DEMO-03");
  for (let i = 0; i < 4 && (await page.locator(".rbtns").count()); i++) await result(page, "정상");
  await page.getByText("파트장 호출").click();
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await expect(lead.locator("tr[data-report] .tx.warn")).toHaveText("근거 적음"); // AC-29-C3
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await lead.locator(".lbar button.lbtn", { hasText: "반려" }).click();
  await lead.getByLabel("반려 사유").fill("근거 기록을 다시 확인해 주세요");
  await lead.locator(".lmdl button.lbtn", { hasText: "반려" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");
  await lead.goto(LEAD_URL + "/calls");
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await lead.locator(".lopts button", { hasText: "주간 처리" }).click();
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  alarm("DEMO-04");
  await home(page);
  await page.waitForTimeout(5500);
  await page.locator(".tb-bell").click();
  await expect(page.locator(".panel .grp")).toHaveText(["배정된 새 알람", "진행 중인 점검", "파트장 답이 온 호출", "반려된 보고서"]); // AC-37-C1
  await shot(page, info, "T12-list");
  await page.locator(".nt-item .btn", { hasText: "답 보기" }).click();
  await expect(page.locator(".banner.warn b")).toHaveText("주간 처리로 결정 · 저부하 감시 운전");
  await home(page);
  await page.locator(".tb-bell").click();
  await page.locator(".nt-item .btn", { hasText: "알람 보기" }).click();
  await expect(page.locator(".alh h3")).toBeVisible();
  // AC-12 L3: 파트장 앱에 정비자 계정 → 거부 화면(L01 / role_error)
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 620 } });
  const other = await ctx.newPage();
  const og = guard(other, [/403/]);
  await loginLead(other, "tech.kim", TECH_PW);
  await expect(other.locator(".lerr b")).toHaveText("파트장 계정만 로그인할 수 있습니다");
  await shot(other, info, "L01-role_error");
  // AC-26 L3: 감사 기록 필터
  await lead.goto(LEAD_URL + "/audit");
  await expect(lead.locator(".lt td").filter({ hasText: "보고서 반려" }).first()).toBeVisible();
  await shot(lead, info, "L06-default");
  const eq = psql(`select a.equipment_id from ops.diag_sessions s join ops.alarms a using (alarm_id) where s.session_id = ${sidR}`);
  await lead.locator(".lflt > .lsel").nth(0).click();
  await lead.locator(".dd div", { hasText: eq }).click();
  await expect(lead.locator(".lflt > .lsel").nth(0)).toContainText(`설비 · ${eq}`);
  await shot(lead, info, "L06-filtered");
  for (const t of await lead.locator(".lt tr td:nth-child(5)").allTextContents()) expect(t.startsWith(`${eq} `)).toBe(true);
  g.check(info);
  lg.check(info);
  og.check(info);
});
