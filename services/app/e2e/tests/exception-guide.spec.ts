// D-543 구현 2 예외 안내(파트장 승인 전)를 사람 손 순서로 · 약속 v8(D-545 덧 2): 세션 시작(start 결과 exception_guide)
// → 잠금 · 사전 확인 뒤 예외 절 먼저(가짜 배포 서버가 Pack 흉내) → 호출 화면 '예외 기록에서 온 점검' · 점검방법 글 + 출처(앱 원본)
// → 「점검방법 등록」 → 「취소」 → 다시 「점검방법 등록」(D-503 1) → 글 하나(D-556) → 「등록」 → 다시 열면 서버 초안으로 채워짐
// → 「파트장 호출」(호출과 따로) → 보냄 배너 뒤에도 카드 그대로.
// 승인 뒤 길(engine/exception 사본 = c1a15-pack 50b2d74 · d05a64d): 파트장 앱 「승인 후 진행」 → 예외 절 지시(응답 절차 그대로)
// → 이상 → 같은 예외 절 조치 → 재가동 → 경계 이내 → 보고서.
// SHOT_DIR이면 화면마다 PNG(1280 × 800).

import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Browser, type Page, type TestInfo } from "@playwright/test";
import { alarm, confirmReportModal, demoHistory, freshGuardOff, guard, login, loginLead, psql, resetOps, restartWithin, result, shot } from "./helpers";

const DEPLOY = process.env.C1A15_DEPLOY_URL ?? "http://127.0.0.1:18796";
const SIT = "바닥에 절삭유가 고여 있었고 덮개 고정 나사 하나가 빠져 있었음";
const TEXT = "흡기 필터를 교체한 뒤 사흘 안에 같은 알람이 다시 울리면, 덮개 고정 나사 네 개가 다 있는지 먼저 본다.";
const pause = (p: Page) => p.waitForTimeout(800);

async function answer(opts: Record<string, unknown>) {
  const r = await fetch(`${DEPLOY}/_answer`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(opts) });
  expect(r.status).toBe(200);
}

async function snap(page: Page, info: TestInfo, name: string) {
  await shot(page, info, name);
  const dir = process.env.SHOT_DIR;
  if (dir) {
    mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, `${name}.png`) });
  }
}

let rid = "";
const KF = "점검 흡기 덕트 이음매를 손으로 눌러 봄. 조치 덕트 이음매를 다시 끼우고 밴드를 조임";
test.beforeEach(async () => {
  resetOps();
  // 출처 = 앱 원본의 예외 기록 한 건(같은 설비 · 같은 증상군 생성 이력 하나에 예외 기록 칸을 잠시 붙임 — 끝나면 뺌)
  rid = psql(
    "select record_id from knowledge.history_records where equipment_id = 'M-01' and symptom_group = 'CL' " +
      "order by occurred_at desc, record_id desc limit 1",
  );
  psql(
    `update knowledge.history_records set record = record || jsonb_build_object('exception_record', ` +
      `jsonb_build_object('situation', '${SIT}', 'action', '나사를 새로 끼움')) where record_id = '${rid}'`,
  );
  await answer({ exception_guide: guide() });
});

const guide = (extra: Record<string, unknown> = {}) => ({
  section: `EX-${rid}`,
  relation_id: `EX-${rid}`,
  grade: "C",
  title: "필터 덮개 고정 나사 확인",
  topic: "필터 덮개 나사", // 정답지 고침A '예외상황-{주제}'(Pack 칸 임시 이름)
  text: TEXT,
  steps: ["덮개 나사 확인", "빠졌으면 새로 끼움"],
  criteria: "나사 네 개 모두 체결 = 정상",
  basis: [rid],
  ...extra,
});

async function leadPage(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 620 } });
  const page = await ctx.newPage();
  await loginLead(page);
  await expect(page.locator(".lnav")).toBeVisible();
  return page;
}

// D-580(Pack ef9ec74): C 예외는 첫 추천이 아니라 후보 — [점검 바꾸기]에서 사람이 고름
async function pickException(page: Page) {
  await page.locator(".rbtns").waitFor();
  await pause(page);
  await page.locator(".refs .lk", { hasText: "점검 바꾸기" }).click();
  await pause(page);
  await page.locator(`.swm .swc[data-section="EX-${rid}"]`).click();
  await page.locator(".warn-h, .ins-h").first().waitFor();
}

async function toExceptionCall(page: Page, pick = true) {
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  if (pick) await pickException(page);
}
test.afterEach(async () => {
  psql(`update knowledge.history_records set record = record - 'exception_record' where record_id = '${rid}'`);
  await answer({}); // 다른 시험은 기본 답
});

test("예외 안내: 점검방법 글 + 출처 · 「점검방법 등록」(취소 · 다시 · 등록 · 다시 열면 채워짐) · 「파트장 호출」 따로(사람 손 순서)", async ({ page }, info) => {
  const g = guard(page);
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await pickException(page);
  const left = page.locator(".g64 > .cd").first();
  await expect(left.locator(".warn-h .sm")).toHaveText("예외 기록에서 온 점검"); // 잠금 · 사전 확인 뒤 예외 절 먼저
  await expect(left.locator(".warn-h h3")).toHaveText("필터 덮개 고정 나사 확인");
  await expect(left.locator(".warn-h .tags > *")).toHaveText(["C승인 필요", "예외상황-필터 덮개 나사"]); // 고침A 26쪽
  await expect(left.locator(".xkh p")).toHaveText(TEXT);
  await expect(left.locator(".xkh .xsrc")).toHaveText(new RegExp(`^출처 · 예외상황-필터 덮개 나사 · M-01 · \\d\\d-\\d\\d — 상황 '${SIT}'$`));
  await expect(left.locator(".sub")).toHaveText(["점검방법", "지금까지 점검"]); // 원래 순서 다음 = v8에 칸 없음(질문 001-zzz17)
  await snap(page, info, "G1-exception-guide");
  await pause(page);
  await left.locator(".xkr .btn", { hasText: "점검방법 등록" }).click();
  await expect(left.locator(".xkf")).toBeVisible();
  await expect(left.locator(".sub")).toHaveText(["점검방법"]);
  await pause(page);
  await left.locator(".xkf .btn", { hasText: "취소" }).click(); // 같은 화면에서 취소 → 다시
  await expect(left.locator(".xkf")).toHaveCount(0);
  await expect(left.locator(".sub")).toHaveText(["점검방법", "지금까지 점검"]);
  await pause(page);
  await left.locator(".xkr .btn", { hasText: "점검방법 등록" }).click();
  // D-556 글 하나(자리글 = 안내 문구)
  const kf = left.getByLabel("예외 글", { exact: true });
  await expect(kf).toHaveAttribute("placeholder", "점검한 것과 조치한 것을 모두 적어 주세요");
  await kf.click();
  await kf.fill(KF);
  await snap(page, info, "G2-knowhow-form");
  await pause(page);
  const sent = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
  await left.locator(".xkf .btn").filter({ hasText: /^등록$/ }).click();
  expect((await sent).status()).toBe(200);
  await expect(left.locator(".xkf")).toHaveCount(0);
  await page.reload(); // 서버에 남은 초안으로 다시 열림
  await expect(left.locator(".warn-h .sm")).toHaveText("예외 기록에서 온 점검");
  await pause(page);
  await left.locator(".xkr .btn", { hasText: "점검방법 등록" }).click();
  await expect(left.getByLabel("예외 글", { exact: true })).toHaveValue(KF);
  await pause(page);
  await left.locator(".xkf .btn", { hasText: "취소" }).click();
  await pause(page);
  await page.locator(".g64 > .cd").nth(1).locator(".btn", { hasText: "파트장 호출" }).click();
  await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
  await expect(left.locator(".xkh p")).toHaveText(TEXT); // 보낸 뒤에도 카드 그대로
  await expect(page.locator("button.n-li .n-ls").first()).toHaveText(/진행 중 · 파트장 호출$/, { timeout: 2000 }); // 왼쪽 알람 칸 — 보낸 즉시(5초 주기 전 · 마지막 대조 025)
  await snap(page, info, "G3-call-sent");
  const ev = psql("select string_agg(type, ',' order by seq) from ops.session_events where type in ('exception_draft', 'call_sent')");
  expect(ev).toBe("exception_draft,call_sent");
  g.check(info);
});

test("예외 안내 승인 뒤: 원래 순서 다음 · 파트장 「승인 후 진행」 → 예외 절 지시 → 이상 → 조치 → 재가동 → 경계 이내 → 보고서(사람 손 순서)", async ({ page, browser }, info) => {
  await answer({ exception_guide: guide({ minutes: 15, next_section: "4.3" }) });
  const g = guard(page);
  await toExceptionCall(page);
  const left = page.locator(".g64 > .cd").first();
  await expect(left.locator(".warn-h h3")).toHaveText("필터 덮개 고정 나사 확인");
  await expect(left.locator(".sub")).toHaveText(["점검방법", "지금까지 점검", "원래 순서 다음"]);
  await expect(left.locator(".xnext .ch")).toHaveText("4.3 흡기 필터 육안 점검"); // v8 next_section
  await expect(left.locator(".warn-h .tags > *")).toContainText(["약 15분"]); // 분 표 EX 줄
  await snap(page, info, "G4-exception-next");
  await page.locator(".g64 > .cd").nth(1).locator(".btn", { hasText: "파트장 호출" }).click();
  await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.locator(".lnav a", { hasText: "호출" }).click();
  await expect(lead.locator("tr[data-call] td").nth(4)).toHaveText(`필터 덮개 고정 나사 확인(EX-${rid}, C)`);
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await lead.locator(".lopts button", { hasText: "승인 후 진행" }).click();
  await lead.getByLabel("처리 메모").fill("덮개 나사 확인 승인");
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  await page.reload();
  await expect(page.locator(".banner.ok b")).toHaveText(/^파트장 답 · 승인 후 진행 · /);
  await pause(page);
  await page.getByText("점검 시작").click();
  await expect(page.locator(".ins-h h3")).toHaveText("필터 덮개 고정 나사 확인"); // 예외 절 지시
  await expect(page.locator(".proc li")).toHaveText(["1덮개 나사 확인", "2빠졌으면 새로 끼움"]); // 응답 절차 그대로
  await expect(page.locator(".ins-h .sm")).toHaveText(/^점검 2 \/ 최대 5/); // 첫 추천을 연 뒤 바꾼 예외 절 = 둘째(덧 34 · D-580) · D-637 F3: 최대 = 매뉴얼 넷(C 포함) + 예외
  await snap(page, info, "G5-exception-instruct");
  await page.reload(); // 같은 화면 다시 열어도 그대로
  await expect(page.locator(".ins-h h3")).toHaveText("필터 덮개 고정 나사 확인");
  await result(page, "이상", "나사 하나 빠짐");
  await expect(page.locator(".act-h b").first()).toHaveText(`조치 · EX-${rid} 필터 덮개 고정 나사 확인`);
  await expect(page.locator(".act .proc li")).toHaveText(["1덮개 나사 확인", "2빠졌으면 새로 끼움"]);
  await snap(page, info, "G6-exception-action");
  await pause(page);
  await page.locator(".btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await page.locator(".btn", { hasText: "경계 이내" }).click();
  await expect(page.locator(".btn", { hasText: "보고서 확정" })).toBeVisible();
  await snap(page, info, "G7-exception-report");
  await confirmReportModal(page);
  await lead.locator(".lnav a", { hasText: "승인 대기" }).click();
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  const sec4 = lead.locator(".fdoc section", { has: lead.locator("h2", { hasText: "4. 조치 결과" }) });
  await expect(sec4.locator("tr", { hasText: "권한 사용" }).locator("td")).toHaveText(/C [1-9]/); // 승인받은 예외 절 점검(덧 34)
  await expect(sec4.locator("tr", { hasText: "파트장 승인" }).locator("td")).toHaveText(`필터 덮개 고정 나사 확인(EX-${rid}, C등급) · 승인 후 진행`);
  await expect(lead.locator(".fd-open")).not.toContainText("필터 덮개 고정 나사 확인"); // 5 미확인 · 주간 요청 아님
  await sec4.scrollIntoViewIfNeeded();
  await snap(lead, info, "G8-lead-final-approved-call");
  const ev = psql(
    `select string_agg(type || ':' || coalesce(payload->>'section', ''), ',' order by seq) from ops.session_events ` +
      `where type in ('call_sent', 'call_answer', 'result', 'action_done') and payload->>'section' like 'EX-%'`,
  );
  expect(ev).toBe(`call_sent:EX-${rid},call_answer:EX-${rid},result:EX-${rid},action_done:EX-${rid}`);
  g.check(info);
  lg.check(info);
});

// D-566 약속 v10 걸음 길(Pack 3502e29 engine 사본 · 정답지 26쪽): 예외 절에 점검 목록 · 조치 목록 → 승인 한 번 →
// 점검 1 → 점검 2 → 조치 1 → 조치 2(결과로 건너뛰지 않음 · 사건 step 칸) → 재가동 → 경계 이내 → 보고서
// D-642 C: 점검 1은 승인된 지시 문장(instruction)이 있는 줄 · 나머지는 세 칸을 이어 쓴 줄
const WALK_SAY = "필터 덮개 고정 나사 네 개를 손으로 돌려 보고, 모두 체결되어 있으면 정상으로 적습니다.";
const WALK_CHECKS = [
  { what: "필터 덮개 고정 나사 확인", how: "손으로 돌려 봄", criteria: "모두 체결 = 정상", minutes: 5, instruction: WALK_SAY },
  { what: "흡기 덕트 이음매", how: "", criteria: "", minutes: null },
];
const WALK_ACTIONS = [
  { what: "나사를 새로 끼움", parts: ["M6 나사"], minutes: 5 },
  { what: "밴드를 다시 조임", parts: [], minutes: null },
];
const WALK_ROWS = [`점검 1 ${WALK_SAY}`, "점검 2 흡기 덕트 이음매", "조치 3 나사를 새로 끼움 · 부품 M6 나사", "조치 4 밴드를 다시 조임"];
const walkNow = (n: number) => WALK_ROWS.map((r, i) => (i === n ? `지금${r}` : r)); // '지금'은 문장 앞 표시

test("예외 안내 걸음마다(정답지 26쪽): 안내 차례 넷 · 승인 한 번 → 점검 1 → 점검 2 → 조치 1 → 조치 2 → 재가동 → 보고서(사람 손 순서)", async ({ page, browser }, info) => {
  await answer({ exception_guide: guide({ minutes: 15, next_section: "4.3", checks: WALK_CHECKS, actions: WALK_ACTIONS }) });
  const g = guard(page);
  await toExceptionCall(page);
  const left = page.locator(".g64 > .cd").first();
  await expect(left.locator(".warn-h .sm")).toHaveText("예외 기록에서 온 점검");
  await expect(left.locator(".warn-h h3")).toHaveText("필터 덮개 고정 나사 확인"); // 첫 걸음 무엇
  const wk = left.locator('[data-part="ex-walk"]');
  await expect(wk.locator("b")).toHaveText("안내 차례 · 적은 순서 그대로");
  await expect(wk.locator(".xwr")).toHaveText(walkNow(0));
  await expect(left.locator(".sub")).toHaveText(["점검방법", "지금까지 점검"]); // 차례 다음 점검방법 · '원래 순서 다음' 0(26쪽)
  await snap(page, info, "W1-walk-call");
  await page.reload(); // 같은 화면 한 번 더(D-503 1)
  await expect(wk.locator(".xwr")).toHaveText(walkNow(0));
  await pause(page);
  await page.locator(".g64 > .cd").nth(1).locator(".btn", { hasText: "파트장 호출" }).click();
  await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.locator(".lnav a", { hasText: "호출" }).click();
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await lead.locator(".lopts button", { hasText: "승인 후 진행" }).click();
  await lead.getByLabel("처리 메모").fill("예외 안내 승인");
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  await page.reload();
  await expect(page.locator(".banner.ok b")).toHaveText(/^파트장 답 · 승인 후 진행 · /);
  await pause(page);
  await page.getByText("점검 시작").click();
  // 점검 1(승인은 예외 전체에 한 번 — 다음 걸음마다 다시 묻지 않음)
  await expect(page.locator(".ins-h h3")).toHaveText(WALK_CHECKS[0].what);
  await expect(page.locator(".proc li")).toHaveText(["1손으로 돌려 봄"]);
  await expect(page.locator('[data-part="ex-walk"] .xwr')).toHaveText(walkNow(0));
  await snap(page, info, "W2-walk-check1");
  await pause(page);
  await result(page, "정상"); // 정상이어도 다음 걸음(건너뛰지 않음)
  await expect(page.locator(".ins-h h3")).toHaveText(WALK_CHECKS[1].what);
  await expect(page.locator('[data-part="ex-walk"] .xwr')).toHaveText(walkNow(1));
  await expect(page.locator('[data-part="ex-walk"] .xwr.done')).toHaveText([WALK_ROWS[0]]);
  await pause(page);
  await result(page, "이상", "이음매 벌어짐");
  await expect(page.locator(".act-h b").first()).toHaveText(`조치 · EX-${rid} ${WALK_ACTIONS[0].what}`);
  await expect(page.locator(".act .proc li")).toHaveText(["1부품 M6 나사"]);
  await expect(page.locator('.act [data-part="ex-walk"] .xwr')).toHaveText(walkNow(2));
  await snap(page, info, "W3-walk-action1");
  await pause(page);
  await page.locator(".btn", { hasText: "조치 완료" }).click();
  await expect(page.locator(".act-h b").first()).toHaveText(`조치 · EX-${rid} ${WALK_ACTIONS[1].what}`);
  await expect(page.locator('.act [data-part="ex-walk"] .xwr')).toHaveText(walkNow(3));
  await pause(page);
  await page.locator(".btn", { hasText: "조치 완료" }).click();
  await restartWithin(page);
  await page.locator(".btn", { hasText: "경계 이내" }).click();
  await expect(page.locator(".btn", { hasText: "보고서 확정" })).toBeVisible(); // 안(정상) → 보고서
  await expect(page.locator('[data-field="check_path"] .rv2')).toContainText(`${WALK_CHECKS[0].what}(EX-${rid}, 정상) → ${WALK_CHECKS[1].what}(EX-${rid}, 이상: 이음매 벌어짐)`);
  await snap(page, info, "W4-walk-report");
  const ev = psql(
    `select string_agg(type || ':' || coalesce(payload->>'step', ''), ',' order by seq) from ops.session_events ` +
      `where type in ('result', 'action_done') and payload->>'section' like 'EX-%'`,
  );
  expect(ev).toBe("result:c1,result:c2,action_done:a1,action_done:a2"); // 사건 step 칸(약속 v10 5절)
  g.check(info);
  lg.check(info);
});

// D-570 실시간 시연 길(Pack 5e76c66 _recent 사본 · 가짜 배포): 앱이 start 요청 recent_work에 그 설비 30일 안 정비를 실음 →
// 예외 조건 '최근 7일 안 그 절 정비' → 시연 이력 줄 넣기 = 같은 증상 알람에 예외 추천 · 빼기 = 같은 알람 추천 0
test("최근 정비 원인 예외(D-570): 시연 줄 넣기 → 알람 → 예외 추천 · 빼기 → 같은 알람 추천 0(사람 손 순서)", async ({ page }, info) => {
  const SEC = "4.4.2"; // 다른 시험 승인 이력과 겹치지 않는 절
  await answer({ exception_guide: guide({ minutes: 15, next_section: "4.3", checks: WALK_CHECKS, actions: WALK_ACTIONS }), exception_recent: { section: SEC, days: 7 } });
  const g = guard(page, [/status of 401/]); // 시험이 중간에 로그인 기록을 비움(resetOps) — 그 뒤 옛 쿠키 401은 시험 탓
  demoHistory("add", "--action", SEC); // M-01 · 지금부터 3일 전
  try {
    await toExceptionCall(page);
    const left = page.locator(".g64 > .cd").first();
    await expect(left.locator(".warn-h .sm")).toHaveText("예외 기록에서 온 점검"); // 예외 추천
    await expect(left.locator('[data-part="ex-walk"] .xwr')).toHaveText(walkNow(0));
    await snap(page, info, "R1-recent-recommended");
    const sent = psql("select payload->'decision'->>'section' from ops.session_events where type = 'step_opened' order by seq desc limit 1");
    expect(sent).toBe(`EX-${rid}`);
  } finally {
    demoHistory("remove");
  }
  resetOps(); // 로그인도 처음으로 — 같은 알람을 처음부터(시연 줄 뺀 뒤)
  await toExceptionCall(page, false);
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검"); // 추천 0 = 원래 첫 점검
  await page.locator(".refs .lk", { hasText: "점검 바꾸기" }).click();
  await expect(page.locator(`.swm .swc[data-section="EX-${rid}"]`)).toHaveCount(0); // 후보에도 없음
  await page.locator(".swm .mb .btn", { hasText: "취소" }).click();
  await expect(page.locator('[data-part="ex-walk"]')).toHaveCount(0);
  await snap(page, info, "R2-no-recent-no-exception");
  g.check(info);
});

// 연습 여섯(28907)에서 받은 진짜 Pack 값: 절 이름 title = '예외상황-{주제}' — 칩 · 이유 · 호출 요약은 첫 걸음 점검 이름(정답지 11쪽)
test("D570글: 진단 셋째 칩 = 첫 점검 이름 · 이유 · 호출 요약 예외 이름 한 번 · 조사 맞춤(사람 손 순서)", async ({ page }, info) => {
  const checks = [{ what: "흡기 필터 덮개 볼트", how: "손으로 돌려 본다", criteria: "", minutes: 5 }];
  const actions = [{ what: "덮개 볼트 다시 조임", parts: [], minutes: 5 }];
  await answer({ preview: true, exception_guide: guide({ title: "예외상황-덮개 볼트 조임", topic: "덮개 볼트 조임", minutes: 5, next_section: "4.3", checks, actions }), preview: true });
  const g = guard(page);
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  const nx = page.locator(".nx");
  const same = async () => {
    await expect(nx.locator("h3")).toHaveText("흡기 필터 육안 점검"); // D-580: 첫 추천 = 매뉴얼
    await nx.locator(".lk.sw").click(); // D-585 예외는 [점검 바꾸기] 창의 후보 줄
    await expect(page.locator('.swm .swc[data-section^="EX-"]')).toContainText("흡기 필터 덮개 볼트");
    await expect(page.locator('.swm .swc[data-section^="EX-"]')).toContainText("예외상황-덮개 볼트 조임");
    await page.locator(".swm .mb .btn", { hasText: "취소" }).click();
  };
  await same();
  await snap(page, info, "T1-diagnosis-real-text");
  await page.reload(); // 같은 화면 한 번 더(D-503 1) — 진단 단계 그대로 열림
  await same();
  await pause(page);
  await nx.locator(".btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await pickException(page);
  const right = page.locator(".g64 > .cd").nth(1);
  await expect(right).toContainText("예외상황-덮개 볼트 조임과 같은 조건 — 흡기 필터 덮개 볼트(C등급) 확인 필요");
  await snap(page, info, "T2-call-summary-real-text");
  g.check(info);
});

test("실제 Pack 예외 preview 꼴(contracts v8 start-exception): 「점검 시작」 → 진단 '다음 점검' = 예외 절 호출 → 잠금 · 사전 확인 → 예외 카드(F1 걷기 결함 덧 33)", async ({ page }, info) => {
  const g = guard(page);
  await answer({ exception_guide: guide({ minutes: 5, next_section: "4.3" }), preview: true }); // Pack처럼 start 결과에 preview = call EX-…
  await login(page);
  alarm("DEMO-01");
  const started = page.waitForResponse((r) => /\/api\/tech\/alarms\/\d+\/session$/.test(r.url()) && r.request().method() === "POST");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  expect((await started).status()).toBe(200); // 고치기 전: 500(KeyError EX-…)
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  const nx = page.locator(".nx");
  await expect(nx.locator("h3")).toHaveText("흡기 필터 육안 점검"); // D-580: 첫 추천 = 매뉴얼 · 예외는 후보 줄
  await expect(nx.locator(".lk.sw")).toBeVisible(); // D-585 후보는 [점검 바꾸기] 창
  await snap(page, info, "G0-diagnosis-exception-preview");
  await pause(page);
  await nx.locator(".btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await pickException(page);
  const left = page.locator(".g64 > .cd").first();
  await expect(left.locator(".warn-h h3")).toHaveText("필터 덮개 고정 나사 확인"); // 같은 예외 절 호출
  await page.reload(); // 같은 화면 한 번 더(D-503 1) — 서버 기록으로 다시 열려도 같은 카드
  await expect(left.locator(".warn-h h3")).toHaveText("필터 덮개 고정 나사 확인");
  g.check(info);
});

test("F3 가이드 그림 일곱(DEMO-04 길): 로그인 · 잠금 창 · 사전 확인 칸 · 파트장 답 창 · 재가동 체크 · 보고서 확정 확인 창 · 파트장 검토 '승인' 자리", async ({ page, browser }, info) => {
  await answer({ exception_guide: guide({ minutes: 5, next_section: "4.3" }), preview: true }); // 실제 Pack 꼴(preview = 예외 절 호출)
  const g = guard(page);
  await freshGuardOff(page);
  await page.goto("/");
  await expect(page.getByLabel("ID")).toBeVisible();
  await snap(page, info, "F3-01-login");
  await login(page);
  alarm("DEMO-04");
  const card = page.locator(".al", { hasText: "M-05" }).filter({ has: page.locator(".btn", { hasText: "점검 시작" }) });
  await card.locator(".btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await page.locator(".nx .btn").click();
  await expect(page.getByText("잠금 완료", { exact: true })).toBeVisible();
  await snap(page, info, "F3-02-lock");
  await page.getByText("잠금 완료", { exact: true }).click();
  await snap(page, info, "F3-03-precheck");
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await pickException(page);
  await page.locator(".g64 > .cd").nth(1).locator(".btn", { hasText: "파트장 호출" }).click();
  await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
  const lead = await leadPage(browser);
  const lg = guard(lead);
  await lead.locator(".lnav a", { hasText: "호출" }).click();
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await lead.locator(".lopts button", { hasText: "승인 후 진행" }).click();
  await lead.getByLabel("처리 메모").fill("덮개 나사 확인 승인");
  await expect(lead.locator("button.lbtn", { hasText: "답 저장" })).toBeVisible();
  await snap(lead, info, "F3-04-lead-answer");
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
  await page.reload();
  await expect(page.locator(".g64 > .cd").first().locator(".warn-h .tags")).toContainText("예외상황-필터 덮개 나사"); // W06 칩(고침 · 고침A 이름)
  await page.getByText("점검 시작").click();
  await result(page, "정상", "덮개 고정 나사 네 개 모두 체결");
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검"); // 원래 순서 다음 4.3
  await result(page, "이상", "기름때가 필터 표면 절반 넘게 덮음");
  await page.locator(".act .btn", { hasText: "조치 완료" }).click();
  await expect(page.locator(".act .ck").first()).toBeVisible();
  await snap(page, info, "F3-05-restart-check");
  await restartWithin(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await page.locator(".cta2 .btn", { hasText: "보고서 확정" }).click();
  const dlg = page.locator('.modal[aria-label="보고서 확정"]');
  await expect(dlg.locator(".btn")).toHaveText(["취소", "확정"]);
  await snap(page, info, "F3-06-report-confirm-modal");
  await dlg.locator(".btn").filter({ hasText: /^확정$/ }).click();
  await lead.locator(".lnav a", { hasText: "승인 대기" }).click();
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await expect(lead.locator(".lbar button.lbtn", { hasText: "승인" })).toBeVisible();
  await snap(lead, info, "F3-07-lead-review-approve");
  g.check(info);
  lg.check(info);
});

test("D580 후보: 진단 다른 후보 · 호출 화면 근거 한 줄 · 예외에서 매뉴얼로 바꿈 · 다시 열어도 예외 줄(사람 손 순서)", async ({ page }, info) => {
  const g = guard(page);
  const matched = { group: "온도 차 저하", equipment_id: "M-01", source_equipment: "M-01", sensors: [], exception: `EX-${rid}`, records: [rid], recent: { section: "4.3.2", days: 7, record_id: "H-0301", at: "2026-11-03T10:00:00+09:00", days_ago: 3.0 } };
  await answer({ exception_guide: guide({ minutes: 15, next_section: "4.3", checks: WALK_CHECKS, actions: WALK_ACTIONS, matched }), preview: true });
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  const why = `이 기기에서 배운 규칙 · 3일 전 같은 정비(4.3.2 · H-0301)`; // 같은 기기(M-01 알람 · 등록도 M-01) · 리허설 F11: 근거 기록 id · 절 · 이력 번호
  await page.locator(".nx .lk.sw").click(); // D-585 진단 카드 [점검 바꾸기]
  await expect(page.locator(`.swm .swc[data-section="EX-${rid}"]`)).toContainText(why);
  await snap(page, info, "C1-diagnosis-candidates");
  await page.locator(".swm .mb .btn", { hasText: "취소" }).click();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await page.locator(".rbtns").waitFor();
  await pause(page);
  await page.locator(".refs .lk", { hasText: "점검 바꾸기" }).click();
  await expect(page.locator(`.swm .swc[data-section="EX-${rid}"]`)).toContainText(why);
  await snap(page, info, "C2-switch-list-with-exception");
  await page.locator(`.swm .swc[data-section="EX-${rid}"]`).click();
  const left = page.locator(".g64 > .cd").first();
  await expect(left.locator('[data-part="ex-why"]')).toHaveText(why);
  await snap(page, info, "C3-exception-call-why");
  await pause(page);
  await left.locator(".lk.sw").click();
  await pause(page);
  await page.locator('.swm .swc[data-section="4.3"]').click();
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  await page.reload(); // 같은 화면 한 번 더(D-503 1)
  await expect(page.locator(".ins-h h3")).toHaveText("흡기 필터 육안 점검");
  await page.locator(".refs .lk", { hasText: "점검 바꾸기" }).click();
  await expect(page.locator(`.swm .swc[data-section="EX-${rid}"]`)).toHaveCount(1);
  await snap(page, info, "C4-back-to-manual-exception-stays");
  g.check(info);
});

test("D585 진단 [점검 바꾸기]: 예외 고름 → 카드에 고른 점검 → 잠금 · 사전 확인 그대로 → 그 예외 호출(사람 손 순서)", async ({ page }, info) => {
  const g = guard(page);
  await answer({ exception_guide: guide({ minutes: 15, next_section: "4.3", checks: WALK_CHECKS, actions: WALK_ACTIONS }), preview: true });
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  const nx = page.locator(".nx");
  await expect(nx.locator('[data-part="check-cands"]')).toHaveCount(0); // 후보 칸 뺌
  await snap(page, info, "D1-diagnosis-menu");
  await pause(page);
  await nx.locator(".lk.sw").click();
  await snap(page, info, "D2-diagnosis-switch-list");
  await pause(page);
  await page.locator(`.swm .swc[data-section="EX-${rid}"]`).click();
  await expect(nx.locator("h3")).toHaveText("필터 덮개 고정 나사 확인"); // 고른 점검이 카드에
  await snap(page, info, "D3-diagnosis-picked");
  await page.reload(); // 같은 화면 한 번 더(D-503 1)
  await expect(page.locator(".nx h3")).toHaveText("필터 덮개 고정 나사 확인");
  await pause(page);
  await page.locator(".nx .btn").click();
  await expect(page.getByText("잠금 완료", { exact: true })).toBeVisible(); // 안전 순서 그대로
  await page.getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  const left = page.locator(".g64 > .cd").first();
  await expect(left.locator(".warn-h h3")).toHaveText("필터 덮개 고정 나사 확인");
  await snap(page, info, "D4-after-safety-picked-exception");
  g.check(info);
});

test("D585 보통 알람도 같게: 예외 없는 세션 진단 [점검 바꾸기] → 매뉴얼 후보 고름 → 안전 순서 뒤 그 점검(사람 손 순서)", async ({ page }, info) => {
  const g = guard(page);
  await answer({});
  await login(page);
  alarm("DEMO-01");
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  const nx = page.locator(".nx");
  await pause(page);
  await nx.locator(".lk.sw").click();
  await expect(page.locator('.swm .swc[data-section^="EX-"]')).toHaveCount(0);
  await snap(page, info, "D5-no-exception-switch-list");
  await pause(page);
  await page.locator('.swm .swc[data-section="4.5"]').click();
  await expect(nx.locator("h3")).toHaveText("냉각수 수위 확인");
  await pause(page);
  await nx.locator(".btn.full").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await pause(page);
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".ins-h h3")).toHaveText("냉각수 수위 확인");
  g.check(info);
});

test("D593 다른 기기: M-03 알람 · 등록 기기 M-01 → 'M-01에서 배운 규칙 · 이 기기도 …'(사람 손 순서)", async ({ page }, info) => {
  const g = guard(page);
  const matched = { group: "온도 차 저하", equipment_id: "M-03", source_equipment: "M-01", sensors: [], exception: `EX-${rid}`, records: [rid], recent: { section: "4.3.2", days: 7, record_id: "H-0302", at: "2026-11-04T10:00:00+09:00", days_ago: 2.0 } };
  await answer({ exception_guide: guide({ minutes: 15, next_section: "4.3", checks: WALK_CHECKS, actions: WALK_ACTIONS, matched }), preview: true });
  await login(page);
  alarm("DEMO-02"); // M-03
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await expect(page.locator(".ap-top, .n-li").first()).toContainText("M-03");
  await pause(page);
  await page.locator(".nx .lk.sw").click();
  await expect(page.locator(`.swm .swc[data-section="EX-${rid}"]`)).toContainText(`M-01에서 배운 규칙 · 이 기기도 2일 전 같은 정비(4.3.2 · H-0302)`);
  await snap(page, info, "C5-other-device-switch-list");
  g.check(info);
});
