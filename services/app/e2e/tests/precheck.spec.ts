// D-619 사전 점검 후보를 사람 손 순서로 — D-656: 시연 첫 상태 예시 줄 = 패턴 F(컨트롤러 펌웨어 업데이트 → 7일 안 냉각 설정 오류)만(C 0).
// 첫 상태 카드 0(2/4 · 기준 미만) · 파트장 칸 0 → tech.kim M-02 보통 처리 끝까지(4.3 정상 헛걸음 → 4.6 이상 → 조치 → 재가동 → 확정)
// → 파트장 승인(예외 아님 · 배포 revision 그대로) → 담당 홈 F 1장 '5건 중 3건'(그래프 기다림 0) · 파트장 칸 1건
// → 카드 눌러 자세히(사례 다섯 · D-663 S4) → [이번엔 제외] 이유 → 같은 화면 다시 열기.
// 0.4초 막기는 켠 채(누름 사이 0.8초). SHOT_DIR이면 화면마다 PNG(1280 × 800).

import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import {
  alarmFrom,
  confirmReportModal,
  demoHistory,
  demoHistoryInput,
  guard,
  login,
  LEAD_URL,
  loginLead,
  psql,
  resetOps,
  restartWithin,
  shot,
} from "./helpers";

const DEPLOY = process.env.C1A15_DEPLOY_URL ?? "http://127.0.0.1:18796";
const FW = "7.1"; // D-656: 매뉴얼에 펌웨어 절 0 → 가장 가까운 7.1 전기 계통 작업(앞 줄 이력 글 = 컨트롤러 펌웨어 업데이트)
const pause = (p: Page) => p.waitForTimeout(800);

async function snap(page: Page, info: TestInfo, name: string) {
  await shot(page, info, name);
  const dir = process.env.SHOT_DIR;
  if (dir) {
    mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, `${name}.png`) });
  }
}

async function revision(value: string) {
  const r = await fetch(`${DEPLOY}/_revision`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ revision: value }),
  });
  expect(r.status).toBe(200);
}

const NAMES: Record<string, string> = { [FW]: "전기 계통 작업" };

function line(id: string, eq: string, daysAgo: number, group: string, did: string[], bad: string[], text?: string) {
  const at = new Date(Date.now() - daysAgo * 86400000);
  at.setMilliseconds(0);
  return {
    record_id: id,
    equipment_id: eq,
    occurred_at: at.toISOString(),
    symptom_group: { code: group, name: group },
    sensor_snapshot: {},
    steps: [
      ...bad.map((s) => ({ kind: "check", section: s, result: "이상" })),
      ...did.map((s) => ({ kind: "action", section: s, name: NAMES[s] })),
    ],
    outcome: { type: "원인 미상·숙련자 인계" },
    narrative: did.includes(FW) ? `${eq} 컨트롤러 펌웨어 업데이트 — 전기 담당 작업(7.1 전기 계통 작업).` : `${eq} 정비`,
    ...(text ? { exception_record: { text } } : {}),
  };
}

const LINES = [ // D-656: 패턴 F만(번호는 예시 대본과 같음) — 다른 설비 넷(이어짐 둘 · 이상 없음 둘) · M-02 업데이트 5일 전 · M-04 3일 전
  line("H-P026", "M-05", 60, "PW", [FW], []),
  line("H-P027", "M-05", 55, "CL", [], ["4.6"]),
  line("H-P028", "M-06", 61, "PW", [FW], []),
  line("H-P029", "M-06", 56, "CL", [], ["4.6"]),
  line("H-P030", "M-07", 52, "PW", [FW], []),
  line("H-P031", "M-08", 33, "PW", [FW], []),
  line("H-P032", "M-02", 5, "PW", [FW], []),
  line("H-P033", "M-04", 3, "PW", [FW], []),
];

/** M-02 냉각 알람 — 시연 각본 DEMO-01(냉각 · 온도 차 저하)의 센서 값 · 출처 행 그대로 설비만 M-02(각본 등록과 같은 출처 칸). */
function m02Alarm(): number {
  const data = path.join(__dirname, "..", "..", "data");
  const all = JSON.parse(readFileSync(path.join(data, "demo", "scenarios.json"), "utf-8")).scenarios;
  const d = all.find((x: { scenario_id: string }) => x.scenario_id === "DEMO-01");
  const sha = JSON.parse(readFileSync(path.join(data, "manifest.json"), "utf-8")).source.sha256;
  const lineage = { source_release: `ai4i-2020@sha256:${sha}`, row_id: Number(d.udi), split: "demo" };
  return alarmFrom({ equipment_id: "M-02", sensor_snapshot: d.sensor_snapshot, trend: d.trend, data_lineage: lineage });
}

test.beforeEach(async () => {
  resetOps();
  await revision("sha256:e2e-first");
  demoHistory("precheck-remove");
  demoHistory("add");
  demoHistoryInput(LINES.map((l) => JSON.stringify(l)).join("\n"), "precheck-add");
});

test.afterEach(() => {
  demoHistory("precheck-remove");
  demoHistory("remove");
});

async function sendResult(page: Page, value: string): Promise<void> {
  const pre = page.locator(".act.pre .ck:not(.on)");
  while (await pre.count()) await pre.first().click();
  await page.locator(".rbtn", { hasText: value }).click();
  const sent = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
  await page.getByText("결과 보내기").click();
  expect((await sent).status()).toBe(200);
  await expect(page.locator(".rbtn.sel")).toHaveCount(0);
}

/** M-02 보통 처리 끝까지(예외 아님 · AI 나눔 0) → 파트장 승인. 지나온 점검 절을 돌려줌.
 * fake Pack 순서(4.3 → 4.5 → 4.4 호출 → 4.6 → 4.6.2 호출)를 그대로 따라감 — 4.3 바로 다음 4.6은 실제 Pack 몫(리허설). */
async function m02PlainApproved(page: Page, lead: Page, info: TestInfo): Promise<string[]> {
  m02Alarm();
  await page.reload();
  await page.locator(".al", { hasText: "M-02" }).locator(".btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  await page.getByText("진단 보기").click();
  await expect(page.locator('[data-part="ex-why"]')).toHaveCount(0); // 예외 후보 0
  await pause(page);
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".rbtns")).toBeVisible();
  const seen: string[] = [];
  const here = page.url();
  const act = page.locator(".act .btn").filter({ hasText: / (진행|완료)$/ }).first();
  const call = page.getByText("파트장 호출");
  for (let i = 0; i < 10; i++) { // 4.3 정상(헛걸음) … 4.6 이상 · C 절이면 파트장 호출 → 승인 후 진행 · 조치 화면에서 멈춤
    await expect(page.locator(".rbtns").or(call).or(act)).toBeVisible();
    if (await page.locator(".rbtns").count()) {
      const sec = /매뉴얼 ([\d.]+)절/.exec(await page.locator(".ins-h .ch.blue").filter({ hasText: "매뉴얼" }).innerText())![1];
      seen.push(sec);
      await pause(page);
      await sendResult(page, sec === "4.6" ? "이상" : "정상");
    } else if (await call.count()) {
      seen.push("call");
      await pause(page);
      await call.click();
      await expect(page.locator(".wait b")).toHaveText(/^파트장 호출 보냄 · \d\d:\d\d$/);
      await lead.goto(LEAD_URL + "/calls");
      await expect(lead.locator("tr[data-call]")).toHaveCount(1);
      await lead.locator("tr[data-call] button.lbtn", { hasText: "답하기" }).click();
      await lead.locator(".lopts button", { hasText: "승인 후 진행" }).click();
      await lead.locator("button.lbtn", { hasText: "답 저장" }).click();
      await expect(lead.locator(".lempty")).toHaveText("처리할 호출이 없습니다");
      await page.goto(here); // 같은 세션 화면 다시 열기(답 반영)
      const start = page.getByText("점검 시작");
      await expect(start.or(act).or(page.locator(".rbtns"))).toBeVisible();
      if (await start.count()) await start.click();
    } else break;
  }
  expect(seen).toContain("4.6");
  await snap(page, info, "P2-m02-action");
  await expect(act).toBeVisible();
  if (/ 진행$/.test((await act.textContent()) ?? "")) {
    const items = page.locator(".act .ck:not(.on)");
    while (await items.count()) await items.first().click();
    await page.locator(".act .btn", { hasText: / 진행$/ }).first().click();
  }
  await page.locator(".act .btn", { hasText: / 완료$/ }).first().click();
  await restartWithin(page);
  await pause(page);
  await page.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
  await expect(page.locator(".rf")).toHaveCount(8);
  await expect(page.locator('[data-part="ex-section"]')).toHaveCount(0); // 예외 상황 구역 0
  await confirmReportModal(page);
  await expect(page.locator(".banner.ok")).toBeVisible();
  await lead.goto(LEAD_URL + "/"); // 이미 로그인한 파트장 창 — 승인 대기 목록
  await lead.locator("tr[data-report] button.lbtn", { hasText: "검토" }).click();
  await pause(lead);
  await lead.locator(".lbar button.lbtn", { hasText: "승인" }).click();
  await pause(lead);
  await lead.locator(".lmdl button.lbtn", { hasText: "승인" }).click();
  await expect(lead.locator(".lempty", { hasText: "승인 대기" })).toBeVisible();
  return seen;
}

test("사전 점검 후보(D-656): 첫 상태 카드 0 → M-02 보통 처리 · 승인 → 담당 홈 F 1장 5건 중 3건(배포 그대로) → 근거 보기 → 이번엔 제외 → 다시 열기(사람 손 순서)", async ({ page, browser }, info) => {
  test.setTimeout(300000);
  const g = guard(page);
  const jung = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  const lead = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  await login(jung, "tech.jung"); // 시험 자료의 M-04 담당(시연 첫 상태는 김 정비 · 데이터만 다름)
  const cards = jung.locator(".pck");
  await expect(cards).toHaveCount(0); // 첫 상태 2/4 — 기준(3건) 미만
  await snap(jung, info, "P1-home-before");
  await loginLead(lead);
  await expect(lead.locator('[data-part="prechecks"] .lsect-h .c')).toHaveText("0건");

  await login(page, "tech.lee"); // 시험 자료의 M-02 담당(시연은 김 정비 · 셈은 처리한 사람과 무관)
  const seen = await m02PlainApproved(page, lead, info);
  expect(seen[0]).toBe("4.3"); // 4.3 헛걸음(4.3 바로 다음 4.6은 실제 Pack 몫 — 리허설에서 봄)
  await jung.reload(); // 배포 revision 그대로 — 그래프 반영 기다림 0
  await expect(cards).toHaveCount(1);
  const b = jung.locator('.pck[data-precheck="F"]');
  // D-663 S4: 겉은 핵심만 · 눌러서 자세히(사람 손 순서)
  await expect(b.locator(".pck-s .pck-t")).toHaveText("M-04 · 냉각 설정값 미리 확인");
  await expect(b.locator(".pck-s .pck-c")).toHaveText("5건 중 3건");
  await expect(b.locator(".pck-n")).toHaveCount(0);
  await snap(jung, info, "P3-home-after");
  await pause(jung);
  await b.locator(".pck-s").click();
  // D-670: 머리 '주의 필요' · 작게 '고장 판단 아님' · 한 줄 · 긴 문장 0
  await expect(b.locator(".pck-h b")).toHaveText("주의 필요");
  await expect(b.locator(".pck-h span")).toHaveText("고장 판단 아님");
  await expect(b.locator(".pck-d")).toHaveText("3일 전 펌웨어 업데이트(7.1) · 7일 안 설정 오류 5건 중 3건");
  await expect(b.locator(".pck-n")).toHaveCount(0);
  await lead.reload();
  await expect(lead.locator('[data-part="prechecks"] .lsect-h .c')).toHaveText("1건");
  await expect(page.locator(".pck")).toHaveCount(0); // M-02 처리한 이 정비는 M-04 담당 아님 — 담당 홈에만

  await expect(b.locator(".pck-l")).toHaveText("살펴볼 곳 · 냉각 설정값");
  await expect(b.locator(".pcb")).toHaveCount(5); // 사례 다섯은 펼친 칸 안
  await snap(jung, info, "P4-basis");

  await pause(jung);
  await b.locator(".pck-b .btn", { hasText: "이번엔 제외" }).click();
  const skip = jung.locator('.modal[aria-label="이번엔 제외"]');
  await expect(skip.locator(".btn", { hasText: "저장" })).toHaveClass(/dis/);
  await skip.getByLabel("이유").fill("이번 주 교체 예정");
  await pause(jung);
  await skip.locator(".btn", { hasText: "저장" }).click();
  await expect(b.locator(".pck-m")).toHaveText(/^이번엔 제외 · \d\d:\d\d · 이번 주 교체 예정$/);
  await jung.reload(); // 같은 화면 다시 열기: 기록 · 건수 그대로(겉은 짧은 표시 · 눌러서 자세히)
  await expect(b.locator(".pck-s .pck-ms")).toHaveText("이번엔 제외");
  await expect(b.locator(".pck-s .pck-c")).toHaveText("5건 중 3건");
  await b.locator(".pck-s").click();
  await expect(b.locator(".pck-m")).toHaveText(/^이번엔 제외 · /);
  await expect(b.locator(".pck-d")).toContainText("5건 중 3건"); // D-670: 긴 문장(.pck-n) 뺌 → 한 줄
  await snap(jung, info, "P5-marked");
  expect(psql("select string_agg(actor || ' ' || action || ' ' || target, ',' order by audit_id) from ops.audit_log where action in ('precheck_look', 'precheck_skip')")).toBe(
    "tech.jung precheck_skip precheck:F:H-P033",
  );
  g.check(info);
});
