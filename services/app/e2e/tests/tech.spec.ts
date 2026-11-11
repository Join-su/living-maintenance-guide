// 정비자 앱 E2E(P-08 L3, S-02 2차 cycle): 실제 nginx · API · PostgreSQL · 알람 발생기. fixture 모드, 외부 요청 0.

import { expect, test } from "@playwright/test";
import { alarm, guard, LEAD_PW, login, psql, resetOps, restartApi, shot, simLlmFailure } from "./helpers";

test.beforeEach(() => resetOps());

test("AC-13 · AC-37: 새 알람이 5초 안에 홈(빨간 점 · 종 숫자), 종 목록 항목에서 알람 화면 · 이어서 하기", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  await expect(page.locator(".g2 .empty").first()).toHaveText("배정된 알람이 없습니다");
  await expect(page.locator(".tb-bell i")).toHaveCount(0);
  await page.locator(".tb-bell").click();
  await expect(page.locator(".panel .empty")).toHaveText("알람 · 할 일이 없습니다"); // AC-37-C3
  await page.locator(".catch").click();
  alarm("DEMO-01"); // 등록이 끝난 시각부터 잰다(발생기 시간은 빼고). 홈 조회 주기가 5초라 여유는 조회 한 번 사이
  const t0 = Date.now();
  // 화면 L3 한도 = 조회 주기 5초 + 응답 · 그리기 0.5초(D-322 — ES-16 host 실행은 발생기가 빨리 끝나 다음 조회까지 거의 5초). API 5초 확인은 verify-e2e P-07
  await expect(page.locator(".al .dot.red")).toBeVisible({ timeout: 5_500 });
  await expect(page.locator(".tb-bell i")).toHaveText("1");
  info.annotations.push({ type: "AC-13-C1", description: `${Date.now() - t0}ms` });
  await shot(page, info, "T02-new_alarm");
  await page.locator(".tb-bell").click();
  await expect(page.locator(".panel .grp")).toHaveText(["배정된 새 알람"]);
  await page.locator(".nt-item .btn", { hasText: "알람 보기" }).click();
  await expect(page.locator(".alh h3")).toHaveText("온도 차 저하"); // 알람 화면(T03)
  await page.locator(".tb-home").click();
  await page.locator(".tb-bell").click();
  await expect(page.locator(".panel .grp")).toHaveText(["진행 중인 점검"]);
  await page.locator(".nt-item .btn", { hasText: "이어서 하기" }).click();
  await expect(page.locator(".alh h3")).toHaveText("온도 차 저하"); // 떠난 화면 그대로
  g.check(info);
});

test("첫 end-to-end(FLOW-03 · SC-04): 로그인 → 알람 → 진단 → 잠금 → 확인 불가 → 순위 그대로 · 다른 점검, 새로고침 · API 재시작 뒤 같은 단계", async ({ page }, info) => {
  const g = guard(page, [/status of 502/]); // API 재시작 동안 화면 주기 조회의 502(시연 P-17과 같음, GAP-17)
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await expect(page.locator(".ap-step .sp.on")).toContainText("알람");
  await expect(page.locator(".tbl tr")).toHaveCount(6);
  await shot(page, info, "T03-default");
  // D-663 S1: 근거 칸 = 1위 후보 확정 기록(top_cards) — 셈 칸(record_counts)이 있을 때만 서버가 고름. 가짜 배포 서버는 그 칸을 내지 않아
  // 카드가 0장 → AC-38(카드 → 기록 전체) 길만 보려고 응답의 비슷한 기록 첫 장(실제 기록 번호 · 같은 카드 꼴)을 근거 칸에 넣음(시험 장치).
  await page.route(/\/api\/tech\/sessions\/\d+$/, async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    if (!j.top_cards?.length) j.top_cards = (j.similar_cards ?? []).slice(0, 1).map((c: object) => ({ ...c, similarity_pct: null }));
    await route.fulfill({ response: res, json: j });
  });
  await page.getByText("진단 보기").click();
  // AC-40 문구 · 합 100 · CO-42(기본 진단에는 호출 경고 토큰 없음)
  await expect(page.locator(".rk").first()).toHaveClass(/on/);
  await expect(page.locator(".hint")).toHaveCount(0); // D-528: 「근거 n = …」 · 과거 기록 안내 문장 뺌
  // D-663 S1: 다른 설비 참고 칸 0 · 근거 칸 = 1위 후보 이름
  await expect(page.locator(".rk .rv")).toHaveCount(0);
  await expect(page.locator('[data-part="other-ref"]')).toHaveCount(0);
  const top = (await page.locator(".rk.on b").textContent()) ?? "";
  await expect(page.locator('[data-part="top-ref"] .cd-h b')).toHaveText(`근거 · ${top} 1건`);
  await expect(page.locator(".warnb")).toHaveCount(0);
  await shot(page, info, "T04-default");
  // AC-38: 기록 카드 → 그 기록 전체 → 닫으면 같은 화면
  const rid = await page.locator(".hc-t b").first().textContent();
  await page.locator(".hc").first().click();
  await expect(page.locator(".modal .mh b")).toHaveText(`과거 기록 ${rid}`);
  await expect(page.locator(".modal .kv b")).toHaveText(["설비", "발생 시각", "증상", "센서 값", "점검 경로", "확정 원인", "조치", "정지 시간"]);
  await shot(page, info, "T13-default");
  await page.locator(".mclose").click();
  await expect(page.locator(".modal")).toHaveCount(0);
  await expect(page.locator(".ap-step .sp.on")).toContainText("진단");
  // 잠금 전에는 1.2절만(R1)
  await page.locator(".nx .btn").click();
  await expect(page.locator('.lockm[role="dialog"] .n-lockbox h3')).toHaveText("잠금 확인이 필요합니다"); // D-528: 가운데 큰 창
  await expect(page.locator(".ap-main .ap-body").first()).not.toContainText("판정");
  await shot(page, info, "T05-before_lock");
  await page.getByText("잠금 완료", { exact: true }).click();
  await expect(page.locator(".lockok b")).toHaveText("잠금 확인 완료");
  await shot(page, info, "T05-locked");
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".ins-h .sm")).toHaveText(/점검 1 \/ 최대 \d/);
  const first = await page.locator(".ins-h h3").textContent();
  // AC-35: 지시 하나, 다른 점검으로 넘어가는 수단 없음(대안은 '확인 불가'만)
  await expect(page.locator(".rbtn")).toHaveText(["정상", "이상", "확인 불가"]);
  await expect(page.getByText(/건너뛰|다음 점검으로|다른 점검/)).toHaveCount(0);
  await shot(page, info, "T06-default");
  // AC-39: 매뉴얼 원문 → 닫으면 그대로
  await page.getByText("매뉴얼 원문 보기").click();
  await expect(page.locator(".modal .orig")).toContainText("판정 기준");
  await shot(page, info, "T14-default");
  await page.locator(".mclose").click();
  await expect(page.locator(".ins-h h3")).toHaveText(first ?? "");
  await page.locator(".rbtn", { hasText: "확인 불가" }).click();
  await page.getByLabel("메모").fill("덮개가 열리지 않음");
  await page.getByText("결과 보내기").click();
  await expect(page.locator(".banner.warn b")).toContainText(`· 확인 불가 1회`);
  await expect(page.locator(".ins-h .sm")).toHaveText(/점검 2 \/ 최대 \d/);
  const second = await page.locator(".ins-h h3").textContent();
  expect(second).not.toBe(first);
  await shot(page, info, "T06-unable_next");
  const sid = Number(/\/session\/(\d+)/.exec(page.url())?.[1]);
  const ranked = psql(`select payload->'decision'->'probabilities' from ops.session_events where session_id = ${sid} and type = 'step_opened' order by seq`).split("\n");
  expect(ranked.at(-1)).toBe(ranked.at(-2)); // 확인 불가 → 순위 그대로(AC-04-C1, 서버 기록)
  await page.reload();
  await expect(page.locator(".ins-h h3")).toHaveText(second ?? "");
  await expect(page.locator(".banner.warn b")).toContainText("확인 불가 1회");
  await restartApi(page);
  await page.reload();
  await expect(page.locator(".ins-h h3")).toHaveText(second ?? "");
  await expect(page.locator(".lockok span")).toContainText("1.2절 ·");
  g.check(info);
});

test("AC-14: 앱을 닫았다 다시 열어도 '이어서 하기'로 같은 단계 · 잠금 시각 · 입력 결과", async ({ browser }, info) => {
  const ctx1 = await browser.newContext();
  const page = await ctx1.newPage();
  const g1 = guard(page);
  await login(page);
  alarm("DEMO-02");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.getByText("진단 보기").click();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await page.locator(".rbtn", { hasText: "확인 불가" }).click();
  await page.getByText("결과 보내기").click();
  await expect(page.locator(".banner.warn")).toBeVisible();
  const step = await page.locator(".ins-h h3").textContent();
  const lockAt = await page.locator(".lockok span").textContent();
  g1.check(info);
  await ctx1.close(); // 앱 닫기
  const ctx2 = await browser.newContext();
  const page2 = await ctx2.newPage();
  const g2 = guard(page2);
  await login(page2);
  await page2.locator(".al .btn", { hasText: "이어서 하기" }).click();
  await expect(page2.locator(".ins-h h3")).toHaveText(step ?? "");
  await expect(page2.locator(".lockok span")).toHaveText(lockAt ?? "");
  await expect(page2.locator(".banner.warn b")).toContainText("확인 불가 1회");
  await shot(page2, info, "AC-14-reopened");
  g2.check(info);
  await ctx2.close();
});

test("AC-02(P-08): 첫 판정 호출 — T04 / first_call → 잠금 → T05 / locked(다음 안내 줄 없음 — D-528), 절차 · 판정 기준 없음", async ({ page }, info) => {
  const g = guard(page);
  psql("update ops.grade_table set grade = 'C' where section_id in ('4.3', '4.5', '4.6')");
  await login(page);
  alarm("DEMO-03");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.getByText("진단 보기").click();
  await expect(page.locator(".nx h3")).toHaveText("파트장 호출 필요");
  await expect(page.locator(".nx .gb")).toHaveClass("gb gC");
  await expect(page.locator(".why b")).toHaveText("호출이 필요한 이유");
  await expect(page.locator(".nx .btn")).toHaveClass(/warnb/);
  await expect(page.locator(".nx .btn")).toContainText("점검 시작");
  await expect(page.locator(".proc")).toHaveCount(0);
  await shot(page, info, "T04-first_call");
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await expect(page.locator(".res .note")).toHaveCount(0); // D-528: 「다음: …」 안내 줄 뺌
  await expect(page.locator(".ins-h h3")).toHaveText("고온부 확인과 온도 기록"); // 잠금 우선 → 사전 확인
  await expect(page.getByText("판정 기준")).toHaveCount(0);
  await shot(page, info, "T05-locked-first_call");
  g.check(info);
});

test("AC-31: LLM 실패 시뮬레이션 세션 — T06 / fallback(원문 대체) · T06 / llm_repeat(인계 권함), 진행은 계속", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  alarm("DEMO-04");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.getByText("진단 보기").click();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".rbtns")).toBeVisible();
  const sid = Number(/\/session\/(\d+)/.exec(page.url())?.[1]);
  simLlmFailure(sid, "last");
  await page.reload();
  await expect(page.locator(".ins-h .ch.fb")).toHaveText("원문으로 대체됨");
  await expect(page.locator(".banner.info b")).toHaveText("원문으로 대체됨");
  await expect(page.locator(".orig")).toContainText("점검 방법");
  await shot(page, info, "T06-fallback");
  simLlmFailure(sid, "all");
  await page.reload();
  await expect(page.locator(".banner.bad b")).toHaveText("안내 문장 생성이 3회 연속 실패했습니다");
  await shot(page, info, "T06-llm_repeat");
  await page.getByRole("button", { name: "원문으로 계속" }).click();
  await expect(page.locator(".banner.bad")).toHaveCount(0);
  await page.locator(".rbtn", { hasText: "정상" }).click();
  await page.getByText("결과 보내기").click();
  await expect(page.locator(".ins-h .sm")).toHaveText(/점검 2 \/ 최대 \d/); // AC-31-C1: 다음 단계로 진행
  g.check(info);
});

test("AC-12(정비자 앱 부분): 파트장 계정은 거부 배너, 정비자 계정은 로그인", async ({ page }, info) => {
  const g = guard(page, [/status of 403/]);
  await login(page, "lead.park", LEAD_PW);
  await expect(page.locator(".banner.bad b")).toHaveText("정비자 계정만 로그인할 수 있습니다");
  await expect(page.locator(".inp.err")).toHaveCount(1);
  await shot(page, info, "T01-role_error");
  await login(page);
  await expect(page.locator(".g2")).toBeVisible();
  g.check(info);
});
