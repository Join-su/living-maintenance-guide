// D-538 · D-566 · 정답지 18 ~ 21쪽 예외 상황 기록을 사람 손 순서로: [예외] 바로 조치 → 조치 → 재가동 → 보고서 전 팝업
// '예외 상황 기록'(자동) — 글 하나(자리글 = 안내 문구) → 「AI 나눔」(가짜 배포 서버 — 약속 v10 예외 하나 = 주제 + 점검 2 +
// 조치 2 · 비슷한 것 통째 상위 3) → 「AI 나눔」 한 번 더(같은 창 · D-503 1) → 예외 전체에 한 번 「같음」 / 「아님」 → 「보고서에 적용」
// → 보고서 초안 안 '예외 상황' 구역 → [예외 상황 수정](앞 내용 채운 팝업) → 「취소」 → 「보고서 확정」 → 확인 창 「취소」 → 다시 확정
// → 확정 뒤 구역은 읽기만 → 파트장 최종본 '6. 예외 상황 기록' → 승인 → 정비 이력 원본 층(record.exception_record)에 같이 저장.
// 0.4초 막기는 켠 채(누름 사이 0.8초). SHOT_DIR이면 화면마다 PNG(1280 × 800).

import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { guard, LEAD_PW, LEAD_URL, login, psql, resetOps, restartWithin, shot, toFirstCheck } from "./helpers";

test.beforeEach(() => resetOps());

const TEXT = "점검 필터 덮개 고정 나사를 손으로 확인. 점검 흡기 덕트 이음매. 조치 나사를 새로 끼움. 조치 밴드를 다시 조임";
const CHECKS = "1 필터 덮개 고정 나사를 손으로 확인 · 기준 정상 범위 · 2 흡기 덕트 이음매 · 기준 정상 범위";
const ACTIONS = "1 나사를 새로 끼움 · 2 밴드를 다시 조임";
const NAME = "예외상황-필터 덮개 나사";
const EDIT_HOW = "덮개 고정 나사 네 개가 다 있는지 보고, 풀린 나사가 있으면 손으로 돌려 본다";
const EDIT_ADD = "빠진 나사는 새로 끼우고 남은 나사도 다시 조인다";
const BEFORE_LINE = "점검 1 필터 덮개 고정 나사 · 나사 네 개를 손으로 돌려 본다 / 조치 1 나사를 새로 끼움";
const AFTER_LINE = `점검 1 필터 덮개 고정 나사 · ${EDIT_HOW} / 조치 1 나사를 새로 끼움 · 2 ${EDIT_ADD}`;
const RESULT = "재가동 정상(경계 이내)";

async function snap(page: Page, info: TestInfo, name: string) {
  await shot(page, info, name);
  const dir = process.env.SHOT_DIR;
  if (dir) {
    mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, `${name}.png`) });
  }
}

const pause = (p: Page) => p.waitForTimeout(800);

test("예외 상황 기록: [예외] 바로 조치 → 보고서 전 팝업 → 적용 → 보고서 안 구역 → 확정 → 파트장 최종본 6번 → 승인 → 이력 원본 층(사람 손 순서)", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page); // 진단 · 잠금까지는 도우미 — 이 시험의 사람 손 순서는 [예외]부터
  await toFirstCheck(page, "DEMO-01");
  await pause(page);
  await page.locator(".refs .lk", { hasText: "점검 바꾸기" }).click();
  const sw = page.locator('.modal[aria-label="점검 바꾸기"]');
  await pause(page);
  await sw.locator(".swc.swx").click();
  await pause(page);
  await sw.locator(".swc", { hasText: "바로 조치" }).click();
  await pause(page);
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await pause(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  // 고침A 18쪽: 예외 진행 세션 = 보고서 전 팝업(자동) · 상황 · 결과 자동 · 설명 문장 0
  const dlg = page.locator('.modal[aria-label="예외 상황 기록"]');
  await expect(dlg.locator(".mh b")).toHaveText("예외 상황 기록");
  await expect(dlg.locator('[data-part="ex-situation"]')).toHaveText(/^M-01 · 온도 차 저하 · .+ · 온도 차 /);
  await expect(dlg.locator('[data-part="ex-result"]')).toHaveText(RESULT);
  const ta = dlg.getByLabel("예외 글", { exact: true });
  await expect(ta).toHaveAttribute("placeholder", "점검한 것과 조치한 것을 모두 적어 주세요");
  await expect(dlg.locator(".mb .btn", { hasText: "보고서에 적용" })).toHaveClass(/dis/); // 빈 글 = 적용 잠김
  await pause(page);
  await ta.click();
  await ta.fill(TEXT);
  const rows = dlg.locator('[data-part="ex-items"] .exi');
  for (let n = 0; n < 2; n++) {
    // 같은 창에서 「AI 나눔」 두 번(D-503 1) — 같은 결과
    await pause(page);
    const sent = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
    await dlg.locator(".exa .btn", { hasText: "AI 나눔" }).click();
    expect((await sent).status()).toBe(200);
    await expect(rows).toHaveCount(1); // 예외 하나(둘로 나누지 않음 · 약속 v10)
  }
  await expect(rows.locator(".exn0")).toHaveText(NAME); // 주제(가짜 = 점검 낱말 1 · 2 · 4)
  await expect(rows.locator(".exl")).toHaveText([
    "점검 1필터 덮개 고정 나사를 손으로 확인 · 기준 정상 범위",
    "점검 2흡기 덕트 이음매 · 기준 정상 범위",
    "조치 1나사를 새로 끼움",
    "조치 2밴드를 다시 조임",
  ]); // 적은 순서 그대로
  await expect(rows.locator(".exh")).toHaveText("비슷한 것 · 예외 통째로 견줌");
  const sim0 = rows.locator(".exr");
  await expect(sim0.first()).toHaveText(`예외${NAME}같음고침/보탬`); // 등록 예외가 맨 위(정답지 30쪽: 기존 예외만 고침/보탬)
  await expect(dlg.locator(".mb .btn", { hasText: "보고서에 적용" })).toHaveClass(/dis/); // 고르기 전 잠김
  await expect(dlg.locator(".mb .btn")).toHaveText(["보고서에 적용"]); // 처음 팝업엔 「취소」 0(정답지 18쪽)
  await pause(page);
  await rows.locator(".exb .btn", { hasText: "아님" }).click();
  await pause(page);
  await sim0.first().locator(".btn", { hasText: "같음" }).click(); // 다시 고름 — 마지막이 이김
  await snap(page, info, "X1-exception-popup");
  await pause(page);
  await dlg.locator(".mb .btn", { hasText: "보고서에 적용" }).click();
  await expect(dlg).toHaveCount(0);
  // 고침A 19쪽: 보고서 초안 안 '예외 상황' 구역 · 칩 이름
  const sec = page.locator('.g73 > .cd').first().locator('[data-part="ex-section"]');
  await expect(sec.locator(".rxr > span:first-child")).toHaveText(["상황", "적은 글", "예외", "점검", "조치", "결과"]);
  await expect(sec.locator(".rxr .ch")).toHaveText([`같음 · ${NAME}`]); // 칩 하나(예외 전체에 한 번)
  await expect(sec.locator('[data-item="checks"] > span').last()).toHaveText(CHECKS);
  await expect(sec.locator('[data-item="actions"] > span').last()).toHaveText(ACTIONS);
  await snap(page, info, "X2-report-section");
  // 정답지 19쪽 · 사용자(D-579): 구역이 화면 안 · 넘치면 그 구역만 스크롤 · 맨 아래 줄(결과)까지 닿음
  const secBox = (await sec.boundingBox())!;
  expect(secBox.y + secBox.height).toBeLessThanOrEqual(800);
  const last = sec.locator(".rxr").last();
  await last.scrollIntoViewIfNeeded();
  const lastBox = (await last.boundingBox())!;
  expect(lastBox.y + lastBox.height).toBeLessThanOrEqual(800);
  await expect(last).toBeInViewport({ ratio: 1 });
  await snap(page, info, "X2b-report-section-scrolled");
  await pause(page);
  await sec.locator(".btn", { hasText: "예외 상황 수정" }).click(); // 같은 화면 한 번 더: 앞 내용 채운 팝업
  await expect(dlg.getByLabel("예외 글", { exact: true })).toHaveValue(TEXT);
  await expect(dlg.locator(".exi .exr").first().locator(".btn").filter({ hasText: /^같음$/ })).not.toHaveClass(/ghost/); // 앞 고름 그대로
  await expect(dlg.locator(".mb .btn")).toHaveText(["취소", "보고서에 적용"]); // 다시 연 팝업만 「취소」(정답지 19쪽)
  await pause(page);
  await dlg.locator(".mb .btn", { hasText: "취소" }).click();
  await expect(dlg).toHaveCount(0);
  await pause(page);
  await page.locator(".cta2 .btn", { hasText: "보고서 확정" }).click();
  const conf = page.locator('.modal[aria-label="보고서 확정"]');
  await pause(page);
  await conf.locator(".btn", { hasText: "취소" }).click();
  await pause(page);
  await page.locator(".cta2 .btn", { hasText: "보고서 확정" }).click();
  await pause(page);
  await conf.locator(".btn").filter({ hasText: /^확정$/ }).click();
  await expect(page.locator(".banner.ok b")).toHaveText(/^보고서를 확정했습니다/);
  await expect(sec.locator(".btn")).toHaveCount(0); // 확정 뒤엔 읽기만
  const situation = (await sec.locator('[data-part="ex-situation"]').textContent()) ?? "";
  expect(situation).toMatch(/^M-01 · 온도 차 저하 · .+ · 온도 차 /);
  await snap(page, info, "X3-confirmed");

  const lead = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  const lg = guard(lead);
  await lead.goto(LEAD_URL + "/");
  await lead.getByLabel("ID").fill("lead.park");
  await lead.getByLabel("비밀번호").fill(LEAD_PW);
  await pause(lead);
  await lead.locator(".lg-card button.lbtn").click();
  await expect(lead.locator("tr[data-report]")).toHaveCount(1);
  await pause(lead);
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await expect(lead.locator(".fdoc h2").last()).toHaveText("6. 예외 상황 기록");
  await expect(lead.locator(".fd-ex th")).toHaveText(["상황", "적은 글", "예외", "점검", "조치", "결과", "설비 · 증상", "센서값", "직전 정비", "AI 첫 안내", "확정 원인"]);
  await expect(lead.locator(".fd-ex td").nth(0)).toHaveText(situation);
  await expect(lead.locator(".fd-ex td").nth(1)).toHaveText(TEXT);
  await expect(lead.locator(".fd-ex td").nth(2)).toHaveText(`${NAME} 같음 · ${NAME}`); // 정답지 21쪽
  await expect(lead.locator(".fd-ex td").nth(3)).toHaveText(CHECKS);
  await expect(lead.locator(".fd-ex td").nth(4)).toHaveText(ACTIONS);
  await expect(lead.locator(".fd-ex td").nth(5)).toHaveText(RESULT);
  await expect(lead.locator(".fd-ex td").nth(9)).toHaveText("4.3 흡기 필터 육안 점검"); // 자동: AI 첫 안내
  await expect(lead.locator(".fd-ex td").nth(10)).toHaveText("원인 미상"); // 자동: 확정 원인(예외 진행 = 원인은 예외 칸)
  await lead.locator(".fd-ex").scrollIntoViewIfNeeded();
  await snap(lead, info, "X4-lead-final-exception");
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "승인" }).click();
  await pause(lead);
  await lead.locator(".lmdl button.lbtn", { hasText: "승인" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");
  // 승인 = 정비 이력 원본 층에 같이(record.exception_record)
  const rid = psql("select report_id from ops.reports"); // 이번 보고서(앞 반복이 남긴 승인 이력과 가름 — 지식 schema는 초기화 밖)
  expect(psql(`select record->'exception_record'->>'situation' from knowledge.history_records where source = 'approved_report' and record->'exception_record'->'context'->>'report_id' = '${rid}'`)).toBe(situation);
  // 승인 기록 파일 칸(약속 v10 6절): exception = 1절 꼴 그대로(주제 · 점검 · 조치만 — llmops-2 적재기는 그 밖 칸 거절) ·
  // exception_review 한 번(같음 = 놓침)
  const er = `from knowledge.history_records where source = 'approved_report' and record->'exception_record'->'context'->>'report_id' = '${rid}'`;
  expect(psql(`select string_agg(k, ',' order by k) from knowledge.history_records, jsonb_object_keys(record->'exception_record'->'exception') k where source = 'approved_report' and record->'exception_record'->'context'->>'report_id' = '${rid}'`)).toBe("actions,checks,topic");
  expect(psql(`select jsonb_array_length(record->'exception_record'->'exception'->'checks') || ',' || jsonb_array_length(record->'exception_record'->'exception'->'actions') ${er}`)).toBe("2,2");
  expect(psql(`select record->'exception_record'->'exception_review'->>'decision' ${er}`)).toBe("same");
  expect(psql(`select (record->'exception_record' ? 'items')::text ${er}`)).toBe("false");
  expect(psql(`select record->'exception_record'->'context'->>'equipment_id' from knowledge.history_records where source = 'approved_report' and record->'exception_record'->'context'->>'report_id' = '${rid}'`)).toBe("M-01");
  if (process.env.SHOT_DIR) {
    // D-503 2: 이력 원본 층에 실제로 저장된 꼴 한 건
    const real = psql(`select record->'exception_record' from knowledge.history_records where source = 'approved_report' and record->'exception_record'->'context'->>'report_id' = '${rid}'`);
    writeFileSync(path.join(process.env.SHOT_DIR, "real-exception-record.json"), JSON.stringify(JSON.parse(real), null, 1));
  }
  g.check(info);
  lg.check(info);
});

// 정답지 30쪽 2단계 '같음 + 고침'(same-edit.md · D-560 덧 10): 비슷한 것 예외 줄 = 대상 예외 지금 목록(가짜 = Pack 흉내) →
// 「고침/보탬」 → 점검마다 방법 칸(미리 채움) + 보탤 조치 → 「보고서에 적용」 → 보고서 구역 '같음 + 고침' · 고침 전 / 뒤 → 확정 →
// 파트장 6번 같은 줄 → 승인 → 이력 원본 층 exception_review.edit(고친 뒤 목록 전체)
test("같음 + 고침(정답지 30쪽): 기존 예외 「고침/보탬」 → 고칠 글 → 적용 → 보고서 구역 → 확정 → 파트장 6번 → 승인(사람 손 순서)", async ({ page, browser }, info) => {
  const g = guard(page);
  await login(page);
  await toFirstCheck(page, "DEMO-01");
  await pause(page);
  await page.locator(".refs .lk", { hasText: "점검 바꾸기" }).click();
  const sw = page.locator('.modal[aria-label="점검 바꾸기"]');
  await sw.locator(".swc.swx").click();
  await pause(page);
  await sw.locator(".swc", { hasText: "바로 조치" }).click();
  await pause(page);
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await pause(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  const dlg = page.locator('.modal[aria-label="예외 상황 기록"]');
  const ta = dlg.getByLabel("예외 글", { exact: true });
  await ta.click();
  await ta.fill(TEXT);
  await pause(page);
  await dlg.locator(".exa .btn", { hasText: "AI 나눔" }).click();
  const row = dlg.locator(".exi .exr").first();
  await expect(row).toHaveText(`예외${NAME}같음고침/보탬`);
  await pause(page);
  await row.locator(".btn", { hasText: "고침/보탬" }).click();
  const box = dlg.locator('[data-part="ex-edit"]');
  await expect(box.locator("b")).toHaveText(["고칠 점검방법", "보탤 조치"]);
  await expect(dlg.locator(".mb .btn", { hasText: "보고서에 적용" })).toHaveClass(/dis/); // 바뀐 것 0 = 잠김
  await expect(box.getByLabel("고칠 점검방법 1")).toHaveValue("나사 네 개를 손으로 돌려 본다"); // 대상 지금 방법
  await box.getByLabel("고칠 점검방법 1").fill(EDIT_HOW);
  await box.getByLabel("보탤 조치").fill(EDIT_ADD);
  await snap(page, info, "S1-same-edit-popup");
  await pause(page);
  await dlg.locator(".mb .btn", { hasText: "보고서에 적용" }).click();
  await expect(dlg).toHaveCount(0);
  const sec = page.locator(".g73 > .cd").first().locator('[data-part="ex-section"]');
  await expect(sec.locator(".rxr .ch")).toHaveText([`같음 + 고침 · ${NAME}`]);
  await expect(sec.locator('[data-item="edit-before"] > span').last()).toHaveText(BEFORE_LINE);
  await expect(sec.locator('[data-item="edit-after"] > span').last()).toHaveText(AFTER_LINE);
  await pause(page);
  await sec.locator(".btn", { hasText: "예외 상황 수정" }).click(); // 같은 화면 한 번 더: 앞 글 그대로
  await expect(dlg.getByLabel("고칠 점검방법 1")).toHaveValue(EDIT_HOW);
  await pause(page);
  await dlg.locator(".mb .btn", { hasText: "취소" }).click();
  await snap(page, info, "S2-same-edit-section");
  await pause(page);
  await page.locator(".cta2 .btn", { hasText: "보고서 확정" }).click();
  await pause(page);
  await page.locator('.modal[aria-label="보고서 확정"] .btn').filter({ hasText: /^확정$/ }).click();
  await expect(page.locator(".banner.ok b")).toHaveText(/^보고서를 확정했습니다/);
  const lead = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  const lg = guard(lead);
  await lead.goto(LEAD_URL + "/");
  await lead.getByLabel("ID").fill("lead.park");
  await lead.getByLabel("비밀번호").fill(LEAD_PW);
  await pause(lead);
  await lead.locator(".lg-card button.lbtn").click();
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await expect(lead.locator(".fd-ex .fd-xk")).toHaveText([`같음 + 고침 · ${NAME}`]);
  await expect(lead.locator('.fd-ex tr[data-item="edit-before"] td')).toHaveText(BEFORE_LINE); // 바뀌기 전 / 뒤 나란히
  await expect(lead.locator('.fd-ex tr[data-item="edit-after"] td')).toHaveText(AFTER_LINE);
  await lead.locator(".fd-ex").scrollIntoViewIfNeeded();
  await snap(lead, info, "S3-same-edit-lead");
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "승인" }).click();
  await pause(lead);
  await lead.locator(".lmdl button.lbtn", { hasText: "승인" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toHaveText("승인 대기 보고서가 없습니다");
  const rid = psql("select report_id from ops.reports");
  const er = `from knowledge.history_records where source = 'approved_report' and record->'exception_record'->'context'->>'report_id' = '${rid}'`;
  expect(psql(`select record->'exception_record'->'exception_review'->'edit'->'checks'->0->>'how' ${er}`)).toBe(EDIT_HOW); // same-edit.md 1절
  expect(psql(`select record->'exception_record'->'exception_review'->'edit'->'actions'->1->>'what' ${er}`)).toBe(EDIT_ADD); // 보탠 조치 = 목록 끝
  expect(psql(`select string_agg(k, ',' order by k) from knowledge.history_records, jsonb_object_keys(record->'exception_record'->'exception') k where source = 'approved_report' and record->'exception_record'->'context'->>'report_id' = '${rid}'`)).toBe("actions,checks,topic"); // 적재기 꼴 그대로
  g.check(info);
  lg.check(info);
});
