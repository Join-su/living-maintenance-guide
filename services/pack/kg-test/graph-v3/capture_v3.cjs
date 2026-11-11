// D-536 대표 알람 그래프 그림 — graph/capture.cjs(D-534)와 같은 길 · 질의만 바꿈(알람 한 건 둘레: 증상군 → 원인 후보 · 이상 나온 점검 → 원인 · 원인 → 조치).
// PLAYWRIGHT_DIR=<playwright 폴더> node kg-test/graph-v3/capture_v3.cjs <이력 id> <저장 폴더>
// 그림은 그래프 칸(nvl-parent)만 잘라 크게 찍음(D-536 보강 2). 비밀번호는 env 파일에서 메모리로만 · 출력 0. 127.0.0.1 밖 요청은 모두 막고 셈.
const fs = require('fs'), os = require('os'), path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_DIR);
const [id, outDir] = process.argv.slice(2);
const envFile = process.env.KGDB_ENV_FILE || path.join(os.homedir(), '.c1a15-kgdb/neo4j.env');
const [user, pw] = fs.readFileSync(envFile, 'utf8').split('\n').find(l => l.startsWith('NEO4J_AUTH=')).slice(11).split(/\/(.*)/s);
const QUERY = `// 알람 ${id} 둘레 — 증상군의 원인 후보 · 이 알람에서 이상 나온 점검 · 원인 → 조치(관계는 정비 이력에서 셈)
MATCH (r:Record {layer: 'mm3-v1', id: '${id}'})-[ai:ALARM_IN]->(g:Group)
OPTIONAL MATCH p1 = (g)-[:CANDIDATE]->(:Cause)
OPTIONAL MATCH p2 = (r)-[:CHECKED]->(s:Section)-[:INDICATES]->(:Cause)
OPTIONAL MATCH p3 = (r)-[:CONFIRMED]->(:Cause)-[:REMEDIED_BY]->(:Section)
OPTIONAL MATCH p4 = (r)-[:DID]->(:Section)
RETURN r, ai, g, collect(DISTINCT p1), collect(DISTINCT p2), collect(DISTINCT p3), collect(DISTINCT p4)`;
(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1450, height: 1100 }, deviceScaleFactor: 3 });
  let blocked = 0;
  await p.route('**/*', r => {
    const u = new URL(r.request().url());
    if (['127.0.0.1', 'localhost'].includes(u.hostname) || ['data:', 'blob:'].includes(u.protocol)) return r.continue();
    blocked++; return r.abort();
  });
  await p.goto('http://127.0.0.1:27474/browser/', { waitUntil: 'networkidle' });
  await p.selectOption('select', { label: 'bolt://' }).catch(() => {});
  await p.fill('input[name=hostname]', '127.0.0.1:27687');
  await p.fill('input[name=username]', user);
  await p.fill('input[name=password]', pw);
  await p.click('[data-testid=connection-form-submit]');
  await p.waitForSelector('[data-testid=connection-form-submit]', { state: 'detached', timeout: 20000 });
  await p.waitForTimeout(1500);
  await p.getByRole('button', { name: 'Dismiss' }).click({ timeout: 3000 }).catch(() => {});   // 새 기능 안내 닫기
  const editor = p.locator('.cm-content').first();
  await editor.click();
  await p.keyboard.insertText(QUERY);
  await p.click('[data-testid=run-button]');
  await p.getByRole('button', { name: 'Dismiss' }).click({ timeout: 2000 }).catch(() => {});
  await p.waitForTimeout(4000);
  await p.getByRole('button', { name: /fit/i }).first().click({ timeout: 3000 }).catch(() => {});   // 그림 화면에 맞춤
  await p.mouse.move(5, 990);   // 도움말 풍선 치움
  await p.waitForTimeout(2000);
  const file = path.join(outDir, `alarm-${id}.png`);
  const box = await p.locator('[data-testid=nvl-parent]').first().boundingBox();   // 그래프 칸만(화면 틀 · 안내 문구 0)
  await p.screenshot({ path: file, clip: box });
  console.log(JSON.stringify({ file: path.basename(file), blocked }));
  await b.close();
})();
