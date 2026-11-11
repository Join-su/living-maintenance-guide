// D-534 그림 탐색 캡처 — 그래프 화면(127.0.0.1:27474)에서 냉각 알람 한 건 둘레 관계를 그려 PNG로.
// PLAYWRIGHT_DIR=<playwright 폴더> node kg-test/graph/capture.cjs <알람 id> <저장 폴더>
// 비밀번호는 env 파일(KGDB_ENV_FILE · 기본 ~/.c1a15-kgdb/neo4j.env)에서 메모리로만 읽어 입력칸에 넣음 · 출력 0.
// 127.0.0.1 밖 요청은 모두 막고 막은 수를 셈.
const fs = require('fs'), os = require('os'), path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_DIR);
const [id, outDir] = process.argv.slice(2);
const envFile = process.env.KGDB_ENV_FILE || path.join(os.homedir(), '.c1a15-kgdb/neo4j.env');
const [user, pw] = fs.readFileSync(envFile, 'utf8').split('\n').find(l => l.startsWith('NEO4J_AUTH=')).slice(11).split(/\/(.*)/s);
const QUERY = `// 냉각 알람 ${id} 둘레 — 같은 설비 앞뒤 · 같은 라인 동시 · 확정 원인 · 조치
MATCH (e:Event {layer: 'rel-v1', id: '${id}'})-[at:AT]->(m:Machine)-[ol:ON_LINE]->(l:Line)
OPTIONAL MATCH p1 = (e)-[:SAME_LINE_BEFORE]->(:Event)-[:AT]->(:Machine)
OPTIONAL MATCH p2 = (e)-[:PRIOR_WORK]->(:Event)-[:DID]->(:Step)
OPTIONAL MATCH p3 = (:Event)-[:PRIOR_WORK]->(e)
OPTIONAL MATCH p4 = (e)-[:CONFIRMED_CAUSE]->(:Cause)-[:REMEDIED_BY]->(:Step)
OPTIONAL MATCH p5 = (e)-[:DID]->(:Step)-[:REQUIRES]->(:Grade)
RETURN e, at, m, ol, l, collect(DISTINCT p1), collect(DISTINCT p2), collect(DISTINCT p3), collect(DISTINCT p4), collect(DISTINCT p5)`;
(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
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
  const file = path.join(outDir, `graph-${id}.png`);
  await p.screenshot({ path: file });
  console.log(JSON.stringify({ file: path.basename(file), blocked }));
  await b.close();
})();
