// D-536 사용자용 문서 HTML → PDF — 앱 저장소 playwright를 읽기로만(PLAYWRIGHT_DIR) · file: 밖 요청은 막고 셈.
// PLAYWRIGHT_DIR=<playwright 폴더> node kg-test/graph-v3/doc_pdf.cjs <html> <pdf>
const path = require("path");
const { chromium } = require(process.env.PLAYWRIGHT_DIR);
const [src, out] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  let blocked = 0;
  const p = await b.newPage();
  await p.route("**/*", (r) => (r.request().url().startsWith("file:") ? r.continue() : (blocked++, r.abort())));
  await p.goto("file://" + path.resolve(src), { waitUntil: "load" });
  await p.pdf({ path: out, format: "A4", printBackground: true, preferCSSPageSize: true });
  await b.close();
  console.log(JSON.stringify({ pdf: path.basename(out), blocked }));
})();
