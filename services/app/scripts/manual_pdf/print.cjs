// MG-MM 2.0 HTML → PDF(EV-005) — main 작업 트리 Playwright(@playwright/test) · 로컬 chromium(설치 0). file: 밖 요청은 막고 센다.
// 사용: NODE_PATH=<main 트리 node_modules> node print.cjs <html 폴더> <나갈 폴더>
const fs = require("fs");
const path = require("path");
const { chromium } = require("@playwright/test");
const [dir, out] = process.argv.slice(2);
const T = JSON.parse(fs.readFileSync(path.join(__dirname, "pdf_only_text.json"), "utf8"));
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const style = "font-family:'Apple SD Gothic Neo'; font-size:7.5pt; color:#5b6470; width:100%; padding:0 16mm 0 18mm; display:flex; justify-content:space-between;";
(async () => {
  const b = await chromium.launch();
  let blocked = 0;
  const report = { blocked: 0, overflow: {} };
  for (const name of ["cover", "body"]) {
    const p = await b.newPage();
    await p.route("**/*", (r) => (r.request().url().startsWith("file:") ? r.continue() : (blocked++, r.abort())));
    await p.goto("file://" + path.resolve(dir, name + ".html"));
    await p.emulateMedia({ media: "print" });
    report.overflow[name] = await p.evaluate(() => [...document.querySelectorAll("body *")].filter((x) => x.scrollWidth > x.clientWidth + 1 && getComputedStyle(x).overflowX !== "visible").length
      + [...document.querySelectorAll("p, li, td, th, h2, h3, h4, h5, figcaption")].filter((x) => x.scrollWidth > x.clientWidth + 1).length);
    await p.pdf({ path: path.join(out, name + ".pdf"), format: "A4", printBackground: true, displayHeaderFooter: name === "body",
      margin: { top: "18mm", bottom: "18mm", left: "18mm", right: "16mm" },
      headerTemplate: `<div style="${style}"><span>${esc(T.header[0])}</span><span>${esc(T.header[1])}</span></div>`,
      footerTemplate: `<div style="${style}"><span>${esc(T.footer[0])}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>` });
    await p.close();
  }
  await b.close();
  report.blocked = blocked;
  fs.writeFileSync(path.join(out, "print.json"), JSON.stringify(report) + "\n");
  console.log("pdf", out, "막은 요청", blocked);
})();
