// P-17(S-06) 시연 10건 완주 E2E(AC-16): 깨끗한 시험 스택(verify-e2e host 실행 — 무작위 비밀값 · fixture 모드 · OpenAI 키 없음 · 가짜 배포 서버)에서
// 시연 각본 10건을 packet demo_input_table(사용자 결정 020 ②) — 장면 ③ 후보 p13-all-s0.5 표(D-429 rebuild, Pack 리트리버 순서)대로 보고서 확정(감시 · 인계 마감 포함)까지 간다.
// 정비자 입력 · 파트장 답 · 보고서 확정은 모두 두 앱 화면 조작이다(시험 DB 시뮬레이션 없음). 한 건(DEMO-07)은 중간에 앱 서버를 다시 띄운다.
// 입력표 밖 단계가 나오면 시험이 멈춘다(실패). 외부 요청 0 · 콘솔 오류 0.

import { expect, test, type Browser, type Page } from "@playwright/test";
import { alarm, guard, LEAD_URL, login, loginLead, psql, resetOps, restartApi, restartWithin, shot, confirmReportModal } from "./helpers";

test.describe.configure({ mode: "serial" });
test.beforeAll(() => resetOps()); // 시연 한 번 = 깨끗한 스택에서 10건을 이어서

type Step =
  | { check: "정상" | "이상" | "확인 불가"; memo?: string; section?: string }
  | { confirm: true }
  | { act: true }
  | { restart: "경계 이내" | "여전히 밖" }
  | { monitor: true }
  | { call: "승인 후 진행" | "주간 처리"; memo?: string }
  | { handover: true }
  | { unableUntilHandover: true }
  | { restartApi: true };

const MEMO = "기름때가 필터 표면 절반 넘게 덮음";
const DEMO: { id: string; ending: string; cause: string; unresolved?: RegExp; steps: Step[] }[] = [
  { id: "DEMO-01", ending: "원인 확정", cause: "냉각 필터 막힘", steps: [{ check: "이상", memo: MEMO, section: "4.3" }, { act: true }, { restart: "경계 이내" }] },
  {
    id: "DEMO-02",
    ending: "파트장 승인 뒤 확정",
    cause: "냉각 팬 열화",
    steps: [
      { check: "정상", section: "4.3" },
      { check: "정상", section: "4.5" },
      { call: "승인 후 진행" },
      { check: "이상", section: "4.4" },
      { call: "승인 후 진행" },
      { act: true },
      { restart: "경계 이내" },
    ],
  },
  {
    id: "DEMO-03",
    ending: "주간 처리",
    cause: "원인 미상",
    unresolved: /주간 처리 요청 · 주간 조 냉각 팬 점검/, // 파트장 답 · 처리 메모가 미확인 · 요청 행에(최종 통합 검토 비차단 ②)
    steps: [{ check: "정상", section: "4.3" }, { check: "정상", section: "4.5" }, { call: "주간 처리", memo: "주간 조 냉각 팬 점검" }],
  },
  {
    id: "DEMO-04",
    ending: "원인 확정(확인 질문)",
    cause: "냉각 필터 막힘",
    steps: [{ check: "정상", memo: MEMO, section: "4.3" }, { confirm: true }, { act: true }, { restart: "경계 이내" }],
  },
  {
    id: "DEMO-05",
    ending: "숙련자 인계 마감(재가동 여전히 밖)",
    cause: "원인 미상",
    steps: [{ check: "이상", memo: MEMO, section: "4.3" }, { act: true }, { restart: "여전히 밖" }, { handover: true }],
  },
  {
    id: "DEMO-06",
    ending: "이상 없음 · 감시 마감",
    cause: "이상 없음 · 감시",
    steps: [{ check: "정상", section: "3.3" }, { check: "정상", section: "3.5" }, { check: "정상", section: "3.4" }, { restart: "경계 이내" }, { monitor: true }],
  },
  {
    id: "DEMO-07",
    ending: "원인 확정(중간에 api 재시작)",
    cause: "엔코더 신호 이상",
    steps: [
      { restartApi: true },
      { check: "정상", section: "3.3" },
      { check: "정상", section: "3.5" },
      { check: "이상", memo: "커넥터 핀 한쪽이 들떠 있음", section: "3.4" },
      { act: true },
      { restart: "경계 이내" },
    ],
  },
  {
    id: "DEMO-08",
    ending: "이상 없음 · 감시 마감(B 시작 전 체크)",
    cause: "이상 없음 · 감시",
    steps: [{ check: "정상", section: "5.3" }, { check: "정상", section: "5.4" }, { check: "정상", section: "5.5" }, { restart: "경계 이내" }, { monitor: true }],
  },
  {
    id: "DEMO-09",
    ending: "원인 확정",
    cause: "공구 날 손상",
    steps: [{ check: "이상", memo: "날 끝에 이가 빠짐", section: "2.4" }, { act: true }, { restart: "경계 이내" }],
  },
  { id: "DEMO-10", ending: "숙련자 인계 마감(확인 불가 반복)", cause: "원인 미상", steps: [{ unableUntilHandover: true }, { handover: true }] },
];

async function leadPage(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 620 } });
  const page = await ctx.newPage();
  await loginLead(page);
  await expect(page.locator(".lnav")).toBeVisible();
  return page;
}

/** B 시작 전 체크가 있는 지시면 모두 체크한다(결과 버튼이 열림). */
async function bChecklist(page: Page): Promise<void> {
  const items = page.locator(".act.pre .ck:not(.on)");
  while (await items.count()) await items.first().click();
}

/** 조치(T08): B 조치면 시작 전 체크 → '… 진행', 그다음 '… 완료'. */
async function doAction(page: Page): Promise<void> {
  const btn = page.locator(".act .btn").filter({ hasText: / (진행|완료)$/ }).first();
  await expect(btn).toBeVisible();
  if (/ 진행$/.test((await btn.textContent()) ?? "")) {
    const items = page.locator(".act .ck:not(.on)");
    while (await items.count()) await items.first().click();
    await page.locator(".act .btn", { hasText: / 진행$/ }).first().click();
  }
  await page.locator(".act .btn", { hasText: / 완료$/ }).first().click();
}

async function sendResult(page: Page, value: string, memo?: string): Promise<void> {
  await bChecklist(page);
  await page.locator(".rbtn", { hasText: value }).click();
  if (memo) await page.getByLabel("메모").fill(memo);
  const sent = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
  await page.getByText("결과 보내기").click();
  expect((await sent).status()).toBe(200);
}

async function leadAnswer(lead: Page, label: string, memo?: string): Promise<void> {
  await lead.goto(LEAD_URL + "/calls");
  await expect(lead.locator("tr[data-call]")).toHaveCount(1);
  await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
  await lead.locator(".lopts button", { hasText: label }).click();
  if (memo) await lead.getByLabel("처리 메모").fill(memo);
  await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
  await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
}

async function openAnswer(page: Page): Promise<void> {
  await page.locator(".tb-home").click();
  await page.locator(".tb-bell").click();
  await page.locator(".nt-item .btn", { hasText: "답 보기" }).click(); // AC-37-C2: 답이 온 호출 → 답 화면
  await page.waitForURL(/\/session\/\d+/);
}

for (const d of DEMO) {
  test(`AC-16 시연 ${d.id} — ${d.ending}: 보고서 확정까지(입력표 · 화면 조작)`, async ({ page, browser }, info) => {
    test.setTimeout(150_000);
    // DEMO-07은 api를 일부러 재시작한다 — 그동안 주기 조회가 nginx 502를 받는 것만 허용(재시작 재개 확인)
    const g = guard(page, d.steps.some((x) => "restartApi" in x) ? [/status of 502/] : []);
    await login(page);
    alarm(d.id);
    await page.locator(".al .btn", { hasText: "점검 시작" }).click();
    await page.waitForURL(/\/session\/\d+/);
    const sid = Number(/\/session\/(\d+)/.exec(page.url())![1]);
    await page.getByText("진단 보기").click();
    await page.locator(".nx .btn").click();
    await page.getByText("잠금 완료", { exact: true }).click();
    await expect(page.locator(".res").getByRole("button", { name: "확인", exact: true })).toBeVisible();
    if (d.steps.some((x) => "restartApi" in x)) {
      await restartApi(page); // 재시작 재개(P-17 verifier): 잠금 뒤 api 재시작 → 같은 단계(사전 확인)로 이어서
      await page.reload();
      await expect(page.locator(".res").getByRole("button", { name: "확인", exact: true })).toBeVisible();
    }
    await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
    await expect(page.locator(".rbtns")).toBeVisible();
    let lead: Page | null = null;
    let lg: ReturnType<typeof guard> | null = null;
    const toLead = async () => {
      if (!lead) {
        lead = await leadPage(browser);
        lg = guard(lead);
      }
      return lead;
    };
    for (const st of d.steps) {
      if ("restartApi" in st) continue; // 위에서 처리
      if ("check" in st) {
        if (st.section) await expect(page.locator(".ins-h .ch.blue").filter({ hasText: "매뉴얼" })).toHaveText(`매뉴얼 ${st.section}절`);
        await sendResult(page, st.check, st.memo);
      } else if ("confirm" in st) {
        await page.locator(".modal .btn", { hasText: "네, 맞습니다" }).click();
        await expect(page.locator(".modal")).toHaveCount(0);
      } else if ("act" in st) {
        await doAction(page);
        await expect(page.locator(".act .ch.green").first()).toHaveText("완료"); // 답장 001-zzx 가(가이드 15쪽)
      } else if ("restart" in st) {
        await restartWithin(page);
        await page.locator(".choice2 .btn", { hasText: st.restart }).click();
        if (st.restart === "여전히 밖") await page.locator('.modal[aria-label="숙련자 인계 확인"] .btn', { hasText: "숙련자 인계" }).click(); // 확인 창(답장 001-zzt 6)
      } else if ("monitor" in st) {
        await expect(page.locator(".done-h .sm")).toHaveText("이상 없음 · 감시");
        await page.getByText("보고서 작성").click();
      } else if ("call" in st) {
        await page.getByText("파트장 호출").click();
        await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
        await leadAnswer(await toLead(), st.call, st.memo);
        await openAnswer(page);
        if (st.call === "승인 후 진행") {
          const start = page.getByText("점검 시작");
          await expect(start.or(page.locator(".act .btn").filter({ hasText: / (진행|완료)$/ }).first())).toBeVisible();
          if (await start.count()) await start.click(); // 승인된 C 점검(T10 / ans_approved) — 조치 승인은 곧바로 조치 화면
        } else {
          await page.getByText("보고서 작성").click();
        }
      } else if ("unableUntilHandover" in st) {
        for (let i = 0; i < 12 && (await page.locator(".rbtns").count()); i++) await sendResult(page, "확인 불가");
        await expect(page.locator(".warn-h .sm")).toHaveText("확인 불가가 반복됨");
      } else if ("handover" in st) {
        await page.locator(".btn.warnb", { hasText: "숙련자 인계" }).click();
        await expect(page.locator(".wait b")).toHaveText(/^숙련자 인계 보냄 · \d\d:\d\d$/);
        await leadAnswer(await toLead(), "주간 숙련자 배정");
        await page.reload();
        await page.getByText("보고서 작성").click();
      }
    }
    await expect(page.locator(".rf")).toHaveCount(8); // 보고서 8항목(T09)
    await expect(page.locator('[data-field="cause"] .rv2')).toHaveText(d.cause);
    if (d.unresolved) await expect(page.locator('[data-field="unresolved"] .rv2')).toHaveText(d.unresolved);
    await confirmReportModal(page);
    await expect(page.locator(".banner.ok")).toBeVisible(); // T09 / confirmed
    expect(psql(`select status from ops.reports where session_id = ${sid}`)).toBe("awaiting_approval");
    await shot(page, info, `T09-confirmed-${d.id}`);
    g.check(info);
    if (lg) (lg as ReturnType<typeof guard>).check(info);
  });
}

test("AC-16-C1 · C2: 시연 10건 모두 승인 대기 보고서, 끝 모양 섞임, 외부 LLM 호출 0(fixture)", async ({ page }, info) => {
  const g = guard(page);
  expect(psql("select count(*) from ops.reports where status = 'awaiting_approval'")).toBe("10");
  expect(psql("select count(distinct scenario_id) from ops.alarms where scenario_id like 'DEMO-%'")).toBe("10");
  expect(psql("select count(*) from ops.calls where kind = 'handover'")).toBe("2");
  expect(psql("select count(*) from ops.calls where kind = 'approval'")).toBe("3");
  await login(page);
  await expect(page.locator(".g2")).toBeVisible();
  g.check(info);
});
