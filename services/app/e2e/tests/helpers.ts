// E2E 도우미: 앱 쪽 조작(알람 발생기 · 시험 DB · 앱 서버 다시 띄우기)과 외부 요청 차단 · 콘솔 오류 수집.
// EV-001 ES-16(CH-36): 기본 = host 길(scripts/verify-e2e가 환경을 넘김, 시험 DB만 compose `db`).
// EV-001 ES-20(CH-42): E2E_MODE=compose = compose 길(scripts/verify-e2e-compose) — 앱 쪽 조작은 api 컨테이너 안에서.

import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, type Page, type TestInfo } from "@playwright/test";

const ROOT = path.resolve(__dirname, "..", "..");
const compose = (...args: string[]) =>
  execFileSync("docker", ["compose", ...args], { cwd: ROOT, encoding: "utf-8", stdio: ["pipe", "pipe", "pipe"] });
const SERVER = path.join(ROOT, "server");
/** host 앱 쪽 python(앱 서버와 같은 환경 — verify-e2e가 넘긴 DATABASE_URL · DATA_DIR · C1A15_* · 토큰). */
const COMPOSE_MODE = process.env.E2E_MODE === "compose";
const hostPy = (args: string[], input?: Buffer) =>
  COMPOSE_MODE
    ? execFileSync("docker", ["compose", "exec", "-T", "api", "python", ...args], {
        cwd: ROOT,
        encoding: "utf-8",
        input,
        stdio: ["pipe", "pipe", "pipe"],
      })
    : execFileSync(path.join(SERVER, ".venv", "bin", "python"), args, {
        cwd: SERVER,
        encoding: "utf-8",
        input,
        env: { ...process.env, PYTHONPATH: "src" },
        stdio: ["pipe", "pipe", "pipe"],
      });

export const TECH_PW = process.env.C1A15_TECH_PASSWORD ?? "";
export const LEAD_PW = process.env.C1A15_LEAD_PASSWORD ?? "";

export function psql(sql: string): string {
  return compose("exec", "-T", "db", "psql", "-U", "c1a15", "-d", "c1a15", "-tAc", sql).trim();
}

/** 시연 알람 발생기(P-07)로 각본 한 건을 등록하고 알람 ID를 돌려준다. */
export function alarm(scenario: string): number {
  const out = hostPy(["-m", "c1a15.alarms", scenario]);
  const m = /알람 (\d+) 등록/.exec(out);
  if (!m) throw new Error(`알람 등록 실패: ${out}`);
  return Number(m[1]);
}

/** 알람 한 줄(표준 입력 JSON · 시연 alarm.sh와 같은 길) 등록 → 알람 ID. */
export function alarmFrom(line: object): number {
  const out = hostPy(["-m", "c1a15.alarms", "--stdin"], Buffer.from(JSON.stringify(line) + "\n"));
  const m = /"alarm_id": (\d+)/.exec(out);
  if (!m) throw new Error(`알람 등록 실패: ${out.slice(0, 200)}`);
  return Number(m[1]);
}

/** D-570 시연 이력 줄: 넣기(add) · 빼기(remove) — 사람이 시연 전에 치는 명령 그대로. */
export function demoHistory(...args: string[]): void {
  hostPy(["-m", "c1a15.demo_history", ...args]);
}

/** D-619 사전 점검 예시 줄 넣기 — 표준 입력으로 받는 명령 그대로. */
export function demoHistoryInput(input: string, ...args: string[]): void {
  hostPy(["-m", "c1a15.demo_history", ...args], Buffer.from(input));
}

/** 매 시험 전: 운영 기록(알람 · 세션 · 로그인)과 등급표를 처음 상태로. */
export function resetOps(): void {
  psql(
    "delete from ops.audit_log; delete from ops.calls; delete from ops.reports; delete from ops.session_events; " +
      "delete from ops.diag_sessions; delete from ops.alarms; " +
      "delete from ops.login_sessions; update ops.grade_table g set grade = s.initial_grade " +
      "from knowledge.manual_sections s where s.section_id = g.section_id",
  );
}

export function simLlmFailure(sessionId: number, mode: "last" | "all"): void {
  hostPy(["-", String(sessionId), mode], readFileSync(path.join(ROOT, "e2e", "sim_llm_failure.py")));
}

/** 파트장 답 기록을 시험 DB에 넣는다(S-03, 파트장 답 API는 S-04 P-11) — e2e/sim_lead_answer.py. */
export function simLeadAnswer(sessionId: number, answer: "approved" | "daytime" | "direct" | "assign"): void {
  hostPy(["-", String(sessionId), answer], readFileSync(path.join(ROOT, "e2e", "sim_lead_answer.py")));
}

/** 점검 결과 정정(다시 판정)을 정정 API로 만든다(S-05 P-14 — 화면 정정은 tech-s05 E2E). */
export async function apiCorrection(page: Page, sessionId: number, section: string, value: string): Promise<void> {
  const s = await page.request.get(`/api/tech/sessions/${sessionId}`).then((r) => r.json());
  const target = s.results.find((r: { section: string }) => r.section === section).seq;
  const r = await page.request.post(`/api/tech/sessions/${sessionId}/corrections`, {
    data: { target_seq: target, value, reason: "시험 정정", confirmed: true },
  });
  expect(r.status()).toBe(200);
}

/** 명세 R5 계산 예시 입력(AC-03): 그 세션의 비슷한 기록 = CL-1 2건(0.96, 0.91) · CL-2 1건(0.84), 기록 번호는 실제 이력. */
export function simR5Example(sessionId: number): void {
  const ids = (cause: string, n: number) =>
    psql(
      `select record_id from knowledge.history_records where record->'outcome'->>'cause_code' = '${cause}' order by record_id limit ${n}`,
    ).split("\n");
  const [a, b] = ids("CL-1", 2);
  const [c] = ids("CL-2", 1);
  const similar = JSON.stringify([
    { record_id: a, cause: "CL-1", score: 0.96 },
    { record_id: b, cause: "CL-1", score: 0.91 },
    { record_id: c, cause: "CL-2", score: 0.84 },
  ]);
  psql(`update ops.diag_sessions set "similar" = '${similar}'::jsonb where session_id = ${sessionId}`);
  // 다음 판정 = 배포 서버 judge Run이 저장된 similar를 그대로 받음(LangGraph checkpoint 지울 것 없음 — EV-001 ES-23)
  // 진단 화면 숫자 = Pack start 판정 출력(EV-011 CH-02 · 04) — 이 similar로 Pack(기본 옵션)이 낼 start 판정 값을 같이 넣음
  const nums = `'{"probabilities": {"CL-1": 50, "CL-2": 28, "CL-3": 11, "CL-4": 11}, "evidence": {"CL-1": 2, "CL-2": 1, "CL-3": 0, "CL-4": 0}}'::jsonb`;
  psql(
    `update ops.session_events set payload = jsonb_set(payload, '{decision}', payload->'decision' || ${nums}) where session_id = ${sessionId} and type = 'step_opened'`,
  );
  psql(`update ops.session_events set payload = payload || ${nums} where session_id = ${sessionId} and type = 'preview_shown'`);
}

export async function restartApi(page: Page): Promise<void> {
  if (COMPOSE_MODE) compose("restart", "api");
  else execFileSync(path.join(ROOT, "scripts", "e2e-host-api"), ["restart"], { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] });
  for (let i = 0; i < 60; i++) {
    const ok = await page.request.get("/api/health").then((r) => r.ok()).catch(() => false);
    if (ok) return;
    await page.waitForTimeout(1000);
  }
  throw new Error("api 재시작 뒤 응답 없음");
}

/** 외부 요청을 막고 세며, 콘솔 오류를 모은다. allow: 시험이 일부러 만드는 오류(예: 역할 거부 403). */
export function guard(page: Page, allow: RegExp[] = []) {
  const external: string[] = [];
  const errors: string[] = [];
  void page.route("**/*", (route) => {
    const u = new URL(route.request().url());
    if (u.hostname !== "127.0.0.1" && u.hostname !== "localhost") {
      external.push(u.href);
      return route.abort();
    }
    return route.continue();
  });
  page.on("console", (m) => m.type() === "error" && !allow.some((a) => a.test(m.text())) && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  return {
    check: (info: TestInfo) => {
      void info.attach("external-requests", { body: JSON.stringify(external), contentType: "application/json" });
      expect(external, "외부 요청 0건").toEqual([]);
      expect(errors, "콘솔 오류 0건").toEqual([]);
    },
  };
}

/** EV-007: 새 화면 직후 0.4초 누름 무시를 끔 — 시험은 화면이 뜨자마자 누르므로. 막기 자체는 safety.spec이 켠 채로 봄. */
export async function freshGuardOff(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __C1A15_FRESH_MS__: number }).__C1A15_FRESH_MS__ = 0;
  });
}

export async function login(page: Page, user = "tech.kim", pw = TECH_PW, guardOff = true): Promise<void> {
  if (guardOff) await freshGuardOff(page);
  await page.goto("/");
  await page.getByLabel("ID").fill(user);
  await page.getByLabel("비밀번호").fill(pw);
  // 로그인 응답(세션 commit)까지 기다림 — 바로 뒤 알람 발생기(R9 배정 = 로그인한 정비자)와 겨루지 않게(ES-16 host 실행에서 드러남)
  const done = page.waitForResponse((r) => r.url().endsWith("/api/login") && r.request().method() === "POST");
  if (!guardOff) await page.waitForTimeout(500); // 막기를 켠 시험: 첫 화면 뒤 0.4초 안 누름은 무시되므로 사람처럼 잠깐 뒤
  await page.getByText("로그인", { exact: true }).click();
  await done;
}

export async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  const root = (await page.locator(".ap").count()) ? ".ap" : (await page.locator(".L").count()) ? ".L" : ".wb"; // 정비자 앱 · 파트장 앱(D-528 .L) 화면 틀
  await info.attach(name, { body: await page.locator(root).first().screenshot(), contentType: "image/png" });
}

/** SHOT_DIR이면 화면 전체 PNG(1280 × 800) — 설계 대조용 그림. */
export async function snapTo(page: Page, name: string): Promise<void> {
  const dir = process.env.SHOT_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${name}.png`) });
}

// ---------------- 정비자 앱 조작(S-03 · S-04 E2E 공통)

export const sidOf = (page: Page) => Number(/\/session\/(\d+)/.exec(page.url())![1]);

/** 홈 → 새 알람 '점검 시작' → 진단 → 잠금 → 사전 확인 → 첫 지시(T06). */
export async function toFirstCheck(
  page: Page,
  scenario = "DEMO-01",
  before?: (sid: number) => void,
  onDiagnosis?: () => Promise<void>,
): Promise<number> {
  alarm(scenario);
  await page.locator(".al .btn", { hasText: "점검 시작" }).click();
  await page.waitForURL(/\/session\/\d+/);
  const sid = sidOf(page);
  if (before) {
    before(sid);
    await page.reload();
  }
  await page.getByText("진단 보기").click();
  if (onDiagnosis) await onDiagnosis();
  await page.locator(".nx .btn").click();
  await page.getByText("잠금 완료", { exact: true }).click();
  await page.locator(".res").getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.locator(".rbtns")).toBeVisible();
  return sid;
}

/** 결과 버튼 · 메모 → 결과 보내기, 서버 응답(다음 단계)을 받을 때까지 기다린다. */
export async function result(page: Page, value: "정상" | "이상" | "확인 불가", memo?: string): Promise<void> {
  await page.locator(".rbtn", { hasText: value }).click();
  if (memo) await page.getByLabel("메모").fill(memo);
  const sent = page.waitForResponse((r) => r.url().endsWith("/events") && r.request().method() === "POST");
  await page.getByText("결과 보내기").click();
  expect((await sent).status()).toBe(200);
  // 응답 뒤 화면이 다음 단계를 그릴 때까지(보낸 선택 표시가 지워짐) — 그리기 전에 .rbtns를 세면 반복이 한 번 더 돎(GAP-13)
  await expect(page.locator(".rbtn.sel")).toHaveCount(0);
}

/** 「보고서 확정」 → 확인 창 「확정」(D-528 가이드 정비자 12 — 지금 앱은 바로 확정하던 것). */
export async function confirmReportModal(page: Page): Promise<void> {
  await page.locator(".cta2 .btn", { hasText: "보고서 확정" }).click();
  await page.locator('.modal[aria-label="보고서 확정"] .btn').filter({ hasText: /^확정$/ }).click();
}

export async function restartWithin(page: Page): Promise<void> {
  const items = page.locator(".act .ck");
  await expect(page.locator(".act .btn", { hasText: /^재가동$/ })).toHaveClass(/dis/); // AC-21-C1
  for (let i = 0; i < (await items.count()); i++) await items.nth(i).click();
  await page.locator(".btn", { hasText: /^재가동$/ }).click();
  await expect(page.locator(".rv-big")).toBeVisible(); // AC-21-C2
}


/** 파트장 앱(8082) — 정비자 앱과 다른 browser context에서 연다. */
export const LEAD_URL = process.env.LEAD_URL ?? "http://127.0.0.1:8082";

export async function loginLead(page: Page, user = "lead.park", pw = LEAD_PW): Promise<void> {
  await freshGuardOff(page);
  await page.goto(LEAD_URL + "/");
  await page.getByLabel("ID").fill(user);
  await page.getByLabel("비밀번호").fill(pw);
  await page.getByText("로그인", { exact: true }).click();
}
