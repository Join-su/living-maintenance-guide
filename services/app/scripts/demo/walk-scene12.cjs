// 사본 예행 1 · 장면 ① ② (안내서 docs/rehearsal-2026-10-28/rehearsal-5scenes.html '장면 ① · ②' 1 ~ 7행) — 사람 순서 그대로.
// 스택 = scripts/demo/stack.sh <WALK_KIND>(copy 기본 18181 · 18182 / real 8081 · 8082). 0.4초 막기는 켠 채(사람처럼 누름 사이 0.8초).
// 행마다 '보여야 할 것' ↔ 본 것 ○ / × + 그림. ×여도 멈추지 않고 다음 행(막혀서 못 가면 그때 멈춤).
// 사용: [WALK_KIND=real] node scripts/demo/walk-scene12.cjs <그림 폴더>   (비밀번호는 스택 파일에서 읽고 찍지 않음)
const { chromium } = require("@playwright/test"); // 저장소 뿌리 node_modules
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const OUT = process.argv[2];
// 스택 = WALK_KIND(copy 기본 · real = 시연 앱 8081/8082) — 반영 순서서 4절
const KIND = process.env.WALK_KIND || "copy";
const STACK = path.join(__dirname, "stack.sh");
const env = Object.fromEntries(
  fs.readFileSync(path.join(process.env.TMPDIR || "/tmp", `c1a15-demo-${KIND}`, "stack.env"), "utf-8")
    .split("\n").filter((l) => l.includes("=")).map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
);
const TECH = process.env.WALK_TECH || (KIND === "real" ? "http://127.0.0.1:8081" : "http://127.0.0.1:18181");
const LEAD = process.env.WALK_LEAD || (KIND === "real" ? "http://127.0.0.1:8082" : "http://127.0.0.1:18182");
const MEMO = process.env.WALK_MEMO || "기름때가 필터 표면 절반 넘게 덮음"; // 001-zn 이름 메모 예행: WALK_MEMO
const rows = [];
const pause = (ms = 800) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toTimeString().slice(0, 8);

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  return `${name}.png`;
}
function row(no, todo, want, seen, ok, pic) {
  rows.push({ no, todo, want, seen, ok: ok ? "○" : "×", pic, at: now() });
  console.log(`${no} ${ok ? "○" : "×"} ${seen}`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const tech = await (await browser.newContext({ viewport: { width: 1280, height: 760 } })).newPage();
  const lead = await (await browser.newContext({ viewport: { width: 1280, height: 620 } })).newPage();
  let stopped = null;
  try {
    // 준비 4행(로그인) — 안내서 순서: 브라우저 창을 먼저 열고 그다음 알람
    await tech.goto(TECH + "/");
    await pause();
    await tech.getByLabel("ID").fill("tech.kim");
    await tech.getByLabel("비밀번호").fill(env.C1A15_TECH_PASSWORD);
    await pause();
    await tech.getByText("로그인", { exact: true }).click();
    await tech.locator(".g2").waitFor({ timeout: 15000 });
    await lead.goto(LEAD + "/");
    await pause();
    await lead.getByLabel("ID").fill("lead.park");
    await lead.getByLabel("비밀번호").fill(env.C1A15_LEAD_PASSWORD);
    await pause();
    await lead.getByText("로그인", { exact: true }).click();
    await lead.locator(".wb-side").waitFor({ timeout: 15000 });

    // 1 알람 내기
    const out = execFileSync("bash", [STACK, KIND, "alarm", "DEMO-01"], { encoding: "utf-8" });
    const card = tech.locator(".al", { hasText: "M-01 · 온도 차 저하" }).first();
    await card.waitFor({ timeout: 15000 });
    row(1, "알람 내기(alarm DEMO-01)", "정비자 홈에 M-01 · 온도 차 저하 카드", `${out.trim().split("\n").pop()} · 홈 카드 '${(await card.innerText()).split("\n")[0]}'`, true, await shot(tech, "1-home-card"));

    // 2 카드 → 점검 시작 → 진단 보기
    await pause();
    await card.locator(".btn", { hasText: "점검 시작" }).click();
    await tech.waitForURL(/\/session\/\d+/);
    await pause();
    await tech.getByText("진단 보기").click();
    await tech.locator(".rk").first().waitFor({ timeout: 15000 });
    const ranks = (await tech.locator(".rk").allInnerTexts()).map((t) => t.replace(/\s+/g, " ").trim());
    const why2 = (await tech.locator("div", { hasText: "이 점검을 고른 이유" }).last().innerText()).replace(/\s+/g, " ").trim();
    const ok2 = /냉각수 부족.*57%/.test(ranks[0] ?? "") && /냉각 필터 막힘.*31%/.test(ranks[1] ?? ""); // 안내서 숫자(약 57% · 약 31%)
    row(2, "카드 → 점검 시작 → 진단 보기", "1위 냉각수 부족(약 57%) · 2위 냉각 필터 막힘(약 31%)", `1위 '${ranks[0]}' · 2위 '${ranks[1]}' · 이유 '${why2}'`, ok2, await shot(tech, "2-diagnosis"));

    // 3 점검 시작 → 잠금 완료 → 사전 확인 값 → 사전 확인 완료
    await pause();
    await tech.locator(".nx .btn").click();
    await pause();
    await tech.getByText("잠금 완료", { exact: true }).click();
    await tech.getByLabel("기록할 값").waitFor({ timeout: 15000 });
    const pre = (await tech.locator(".ins-h .ch.blue").first().innerText()).trim();
    fs.writeFileSync(path.join(OUT, "3a-precheck.txt"), (await tech.locator("body").innerText()) + "\n");
    await pause();
    await tech.getByLabel("기록할 값").fill("하우징 표면 52 °C · 공기 302.6 K · 공정 310.4 K");
    await pause();
    await tech.getByText("사전 확인 완료").click();
    await tech.locator(".rbtns").waitFor({ timeout: 30000 });
    const sec3 = (await tech.locator(".ins-h .ch.blue").first().innerText()).trim();
    const title3 = (await tech.locator(".ins-h h3").first().innerText()).trim();
    row(3, "점검 시작 → 잠금 완료 → 사전 확인 값 → 사전 확인 완료", "첫 지시 4.5 냉각수 수위", `사전 확인 ${pre} → 첫 지시 ${sec3} · ${title3}`, /4\.5/.test(sec3) && /4\.2/.test(pre), await shot(tech, "3-first-instruction"));


    // 3-m 매뉴얼 원문 보기(D-465 · 안내서 밖 대조 행 — 개정 2.0 원문이 보이는지)
    await pause();
    await tech.locator(".refs .lk", { hasText: "매뉴얼 원문 보기" }).click();
    await tech.locator(".modal .orig").waitFor({ timeout: 15000 });
    const orig3m = (await tech.locator(".modal .orig").innerText()).trim();
    const head3m = (await tech.locator(".modal .mh").innerText()).replace(/\s+/g, " ").trim();
    fs.writeFileSync(path.join(OUT, "3m-manual-45.txt"), head3m + "\n" + orig3m + "\n");
    row("3-m", "매뉴얼 원문 보기 → 닫기", "4.5 원문 개정 2.0(끝 줄 '관련: 부록 E … 부록 H 냉각수')", `${head3m} · 끝 줄 '${orig3m.split("\n").pop()}'`, /4\.5/.test(head3m) && /부록 H/.test(orig3m), await shot(tech, "3m-manual-45"));
    await pause();
    await tech.locator(".modal .mclose").click();
    await pause();

    // 4 4.5 정상
    await pause();
    await tech.locator(".rbtn", { hasText: "정상" }).click();
    await pause();
    await tech.getByText("결과 보내기").click();
    await tech.locator(".ins-h .ch.blue", { hasText: "4.3" }).first().waitFor({ timeout: 30000 }).catch(() => {});
    const sec4 = (await tech.locator(".ins-h .ch.blue").first().innerText()).trim();
    const title4 = (await tech.locator(".ins-h h3").first().innerText()).trim();
    row(4, "4.5 결과 정상", "다음 지시 4.3 흡기 필터 육안 점검(4.5는 헛걸음)", `${sec4} · ${title4}`, /4\.3/.test(sec4) && /흡기 필터/.test(title4), await shot(tech, "4-next-43"));

    // 5 4.3 이상 + 메모 → 조치 → 재가동 경계 이내 → 보고서 확정
    await pause();
    await tech.locator(".rbtn", { hasText: "이상" }).click();
    await tech.getByLabel("메모").fill(MEMO);
    await pause();
    await tech.getByText("결과 보내기").click();
    const actBtn = tech.locator(".act .btn").filter({ hasText: / (진행|완료)$/ }).first();
    await actBtn.waitFor({ timeout: 30000 });
    await pause();
    if (/ 진행$/.test((await actBtn.textContent()) ?? "")) {
      const items = tech.locator(".act .ck:not(.on)");
      while (await items.count()) {
        await items.first().click();
        await pause(); // 0.4초 막기보다 길게(사본 예행 1에서 0.3초 간격 체크가 삼켜짐)
      }
      await tech.locator(".act .btn", { hasText: / 진행$/ }).first().click();
      await pause();
    }
    await tech.locator(".act .btn", { hasText: / 완료$/ }).first().click();
    await tech.getByText("체크리스트 확인 후 재가동").waitFor({ timeout: 30000 });
    await pause(1200); // 화면이 막 바뀐 0.4초 안의 첫 체크는 삼켜짐(사본 예행 2 · 10-29)
    const cks = tech.locator(".act .ck");
    for (let i = 0; i < (await cks.count()); i++) {
      await cks.nth(i).click();
      await pause(); // 0.4초 막기보다 길게(사본 예행 1에서 0.3초 간격 체크가 삼켜짐)
    }
    await pause();
    await tech.locator(".btn", { hasText: /^재가동$/ }).click();
    await tech.locator(".rv-big").waitFor({ timeout: 30000 });
    await pause();
    await tech.locator(".choice2 .btn", { hasText: "경계 이내" }).click();
    await tech.locator('[data-field="cause"] .rv2').waitFor({ timeout: 30000 });
    const cause = (await tech.locator('[data-field="cause"] .rv2').innerText()).trim();
    await shot(tech, "5a-report-draft");
    fs.writeFileSync(path.join(OUT, "5a-report-draft.txt"), (await tech.locator("body").innerText()) + "\n"); // 보고서 글 대조(D-465)
    await pause();
    await tech.locator(".cta2 .btn", { hasText: "보고서 확정" }).click();
    await tech.locator(".banner.ok").waitFor({ timeout: 15000 });
    row(5, "4.3 이상 + 메모 → 조치 → 재가동 경계 이내 → 보고서 확정", "확정 원인 냉각 필터 막힘", `확정 원인 '${cause}' · 보고서 확정 띠`, cause === "냉각 필터 막힘", await shot(tech, "5b-report-confirmed"));

    // 6 파트장: 승인 대기 → 검토 → 그대로 승인 → 결정 저장
    await lead.goto(LEAD + "/");
    await lead.locator("tr[data-report] .btn", { hasText: "검토" }).first().waitFor({ timeout: 20000 });
    await pause();
    await lead.locator("tr[data-report] .btn", { hasText: "검토" }).first().click();
    await lead.locator(".dopt", { hasText: "그대로 승인" }).waitFor({ timeout: 15000 });
    await pause();
    await lead.locator(".dopt", { hasText: "그대로 승인" }).click();
    await shot(lead, "6a-lead-review");
    await pause();
    await lead.locator(".btn", { hasText: "결정 저장" }).click();
    await lead.locator(".empty").waitFor({ timeout: 15000 }).catch(() => {});
    const emptyText = (await lead.locator(".empty").count()) ? (await lead.locator(".empty").innerText()).trim() : "(목록 남음)";
    row(6, "파트장: 승인 대기 → 검토 → 그대로 승인 → 결정 저장", "(화면 변화 없음) — 체크 기록이 감", `승인 대기 목록 '${emptyText}'`, /없습니다/.test(emptyText), await shot(lead, "6b-lead-after"));

    // 7 체크 기록 확인
    await pause(1500);
    const chk = execFileSync("bash", [STACK, KIND, "check"], { encoding: "utf-8" }).trim();
    fs.writeFileSync(path.join(OUT, "7-check.txt"), chk + "\n");
    const ok7 = /check_record_sent CR-\d+ 201 \["S1", "S3"\]/.test(chk);
    row(7, "체크 기록 확인(check)", 'check_record_sent CR-<번호> 201 ["S1", "S3"]', chk.replace(/\n/g, " / "), ok7, "7-check.txt");
  } catch (e) {
    stopped = `${now()} 막힘: ${String(e.message).split("\n")[0]}`;
    console.log(stopped);
    await shot(tech, "stopped-tech").catch(() => {});
    await shot(lead, "stopped-lead").catch(() => {});
  } finally {
    fs.writeFileSync(path.join(OUT, "rows.json"), JSON.stringify({ rows, stopped }, null, 1) + "\n");
    await browser.close();
  }
})();
