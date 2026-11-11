/* LLMOps — UI ported from the approved seed (coding-plan/v5/seed/app.js, prototype v2: G2A structure + G2B direction-b).
   Data comes from the local API (127.0.0.1). Screens are connected slice by slice (plan v10); a screen whose slice is
   not built yet shows a "later step" notice instead of running on fixtures. No key value is ever rendered or stored. */
"use strict";
const SPEED = 250;                       // fixture-era constant, unused until the run slices are ported
const TIMERS = {};
let S = { projects: [], examples: [] };
let SEQ = 1;
const UI = { menu: false, modal: null, itab: "project", node: null, caseIdx: 0, iter: 1, runId: null, seg: "gen", setId: null, gen: null, genErr: null,
  fileErr: null, dataErr: null, direct: "", newp: null, reg: null, cmpInput: null, cmpSel: null, metric: "LLM 점수", cmpRun: {}, cmpTouched: {}, sameTried: {}, monSel: null, monInput: null, monCase: 0, monEnlarged: null,
  monErr: false, briefId: null, briefDraft: null, verdictErr: false, runNotice: null, workChanged: {} };
/* D-610: an internal decision number never reads on the screen. The stored lines keep theirs (a record is not rewritten, D-492 덧 18's way);
   the screen's reads of the wheel, the comparison and the state drop '(D-98)' · '(D-121 · D-122)' · '(D-525 덧 10 · 24)' and the ', D-99' in
   '(draft, D-99)'. A person's own words with a number inside — '(사용자 D-484)' — stay, and so does what is not a screen's read (a file's text). */
const SCREEN_READ = /^\/api\/state\b|\/auto(\/|$|\?)|\/compare(\/|$|\?)/;
const noDecision = t => t.replace(/\s*\(D-\d+(?:[^()]|\([^()]*\))*\)/g, "").replace(/,\s*D-\d+[^()]*\)/g, ")");
const plainWords = v => typeof v === "string" ? noDecision(v) : Array.isArray(v) ? v.map(plainWords) :
  v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, plainWords(x)])) : v;
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { "content-type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || r.statusText); e.detail = j.detail; e.status = r.status; e.body = j; throw e; }
  return method === "GET" && SCREEN_READ.test(url) ? plainWords(j) : j;
}
/* EV-058 CHG-241: refreshes are numbered in the order they were sent; an answer older than the state already on screen (a newer refresh,
   or a save's answer put in by markWrite) is never drawn — when nothing newer is on the way it asks once more, and its promise resolves */
let REFRESH_SEQ = 0, REFRESH_FLOOR = 0;
function markWrite() { REFRESH_FLOOR = REFRESH_SEQ; }
async function refresh() {
  const seq = ++REFRESH_SEQ;
  const [st, examples, builtin, settings] = await Promise.all([api("GET", "/api/state"), api("GET", "/api/examples"), api("GET", "/api/builtin-releases"), api("GET", "/api/settings")]);
  if (seq <= REFRESH_FLOOR) return seq === REFRESH_SEQ ? refresh() : undefined;
  REFRESH_FLOOR = seq;
  for (const p of st.projects) { p.runs = p.runs.map(adaptRun); p.verdicts = Object.fromEntries(p.runs.map(r => [r.id, r.verdicts])); }
  S = { ...st, examples, builtin, settings };
  for (const p of S.projects) for (const r of p.runs) followRun(p, r);
  render();
}
/* EV-022 review E-S-04-R1-01: while a deploy approval is in progress, read the state again every few seconds — the steps move on
   screen and LLMOps writes its deployment row when it reads `tagged` (S-20). Stops at switched or a failure. */
setInterval(() => {
  if (UI.polling || typeof route !== "function") return;
  const { s } = route(), p = s[0] === "p" ? proj(s[1]) : null;
  if (!p) return;
  const live = p.releases.some(r => r.version && r.version.rollout && !r.version.rollout.failed && r.version.state !== "운영" && r.version.rollout.stage !== "switched") ||
    p.releases.some(r => r.version && r.version.rollback && !r.version.rollback.failed && !r.version.rollback.done);   // EV-SUI-01: a rollback in progress
  if (live) { UI.polling = true; refresh().catch(() => {}).finally(() => { UI.polling = false; }); }
  if (UI.kg && UI.kg.data && UI.kg.key.startsWith(p.id + "|")) {   // D-570: the graph signals move while the page stays open — drawn again only when they changed
    const key = UI.kg.key, [pid, rid] = key.split("|");
    api("GET", `/api/projects/${pid}/releases/${rid}/kg`).then(d => { if (UI.kg && UI.kg.key === key && JSON.stringify(d) !== JSON.stringify(UI.kg.data)) { UI.kg.data = d; render(); } }).catch(() => {});
  }
}, 3000);
function save() {}                        // persistence is server-side (B-03)
function nid(prefix) { return `${prefix}-${SEQ++}`; }
function esc(x) { return String(x ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
// EV-075 (D-432 덧 6 R1 · R2): the short words show, the whole text when the mouse is over them (dotted underline · title, shown in the page's bubble: Tip)
function hv(word, whole, id) { return `<span class="hv" tabindex="0"${id ? ` id="${id}"` : ""} title="${esc(whole)}">${esc(word)}</span>`; }   // D-432 덧 8: the keyboard reaches it too
// EV-075 R5: a node's role in plain words for summary lines — a retriever by its store, others by their kind (the graph keeps the ids)
const VERDICT_WORD = { adopt: "채택", hold: "보류", reject: "버림" };   // EV-075 (v2 18쪽): the verdict words; the stored value on mouse-over
// D-470 덧 21 (보고 070 덧 2 답): the Run tab's 권고 and its stored rule ('… → hold') read the words, the stored text on mouse-over
const recWord = v => VERDICT_WORD[v] ? hv(VERDICT_WORD[v], v) : esc(v);
const ruleWords = t => { const w = String(t ?? "").replace(/→ (adopt|hold|reject)\b/g, (_, v) => "→ " + VERDICT_WORD[v]); return w === String(t ?? "") ? esc(w) : hv(w, t); };
const STORE_WORD = { history: "이력", manuals: "매뉴얼", manual: "매뉴얼" };
function roleWords(rel, id) {   // a node without such words keeps its id (a Pack node-name label file would give them — 자료 필요)
  const n = ((rel && rel.graph && rel.graph.nodes) || []).find(x => x.name === id);
  if (!n || !n.kind) return id;
  const k = String(n.kind).split("@")[0], st = (n.params || {}).store;
  return k === "retrieve" && st ? `${STORE_WORD[st] || st} 리트리버` : id;
}
// EV-075 SUITE (v2 7 · 20쪽): an evaluation suite named <pack>_val_gold_<node> / <pack>_test_confirm_<node>[_confirm] reads by the node's
// role and which test it is; other names as they are. The name itself on mouse-over (callers wrap it in hv)
function suiteWords(rel, name) {
  const m = /^[a-z0-9]+_(val_gold|test_confirm)_(.+?)(?:_confirm)?$/.exec(String(name || ""));
  return m ? `${roleWords(rel, m[2])} · ${m[1] === "val_gold" ? "작업 시험" : "확인 시험"}` : String(name || "");
}
// EV-075 COMPARE (v2 25쪽): a metric by what it means; its column name on mouse-over
const METRIC_WORD = { latency: "속도", "timeout 수": "시간 초과", token: "토큰" };
function metricWords(p, k) {
  const m = /^리트리버 (\S+) (recall@10|hit@10|사람 확인)$/.exec(String(k));
  if (!m) return METRIC_WORD[k] || k;
  const rel = (p.releases || []).find(r => r.graph && r.graph.nodes.some(n => n.name === m[1]));
  const who = roleWords(rel, m[1]).replace(/ 리트리버$/, "");
  return `${who} ${m[2] === "recall@10" ? "정답 찾음(상위 10)" : m[2] === "hit@10" ? "정답 1개 이상(상위 10)" : "정답(사람 확인)"}`;
}
const mword = (p, k) => !p ? esc(k) : metricWords(p, k) === k ? esc(k) : hv(metricWords(p, k), k);
const NONE_DASH = /^(기대값 없음|꺼짐|기대 case 없음|사람 확인 표본 없음)$/;
const dash = h => NONE_DASH.test(plainText(h).trim()) ? hv("—", plainText(h).trim()) : h;
// EV-075 R3 (v2 22쪽): an object input's summary line — its first short words (kind · group · direction …), the value opens on a click
function inputSummary(v) {
  const words = [];
  const walk = (o, d) => { for (const x of Object.values(o || {})) { if (words.length >= 4) return; if (typeof x === "string" && x.length <= 24) words.push(x); else if (x && typeof x === "object" && !Array.isArray(x) && d < 1) walk(x, d + 1); } };
  walk(v, 0);
  return words.length ? words.join(" · ") : Array.isArray(v) ? `목록 ${v.length}개` : Object.keys(v || {}).join(" · ");
}
// D-432 덧 7: a case input's words — the operational list's alarm words when the version's label file has them (the same rule as
// llmops/opsruns.input_summary, F-06), else the R3 summary
function caseWords(rel, v) {
  const sp = rel && rel.inputSummary, words = [];
  if (sp && v && typeof v === "object" && !Array.isArray(v)) {
    const st = v[sp.state] && typeof v[sp.state] === "object" && !Array.isArray(v[sp.state]) ? v[sp.state] : v;
    for (const f of sp.fields) {
      let x = st;
      for (const k of f.split(".")) x = x && typeof x === "object" && !Array.isArray(x) ? x[k] : undefined;
      if (!((typeof x === "string" && x !== "") || typeof x === "number")) continue;
      const last = f.split(".").pop(), name = ((sp.names || {})[last] || (sp.names || {})[f] || {})[String(x)];
      words.push(name && last === "group" ? `${x} ${name}` : name || String(x));
    }
  }
  return words.length ? words.join(" · ") : inputSummary(v);
}
// D-456 · D-457 B: the decider alias field — it starts with the alias this browser wrote last (else the server's last_by, else the
// Project's latest verdict alias) and
// can be changed; the alias rule's refusal shows right under it, only this field red (the other problems stay where they were)
const BY_ERR = /^결정자는 별명 ID/, LAST_BY = "llmops.lastBy";
function lastBy(p) {
  try { const x = localStorage.getItem(LAST_BY); if (x) return x; } catch (e) { /* storage off: the server's, then the Project's verdicts */ }
  if (S.last_by) return S.last_by;   // a first browser: the last deploy approval · rollback alias (packrt 139 (가): this browser's comes first)
  const hs = ((p && p.runs) || []).flatMap(r => r.verdicts || []).filter(h => h.by);
  return hs.length ? hs.reduce((a, b) => (String(b.at) > String(a.at) ? b : a)).by : "";
}
function keepBy(by) { try { if (by) localStorage.setItem(LAST_BY, by); } catch (e) { /* storage off */ } }
function byField(id, value, errs, tip) {
  const mine = (errs || []).filter(x => BY_ERR.test(x));
  return `<div class="field"><label for="${id}" title="${esc(tip)}">결정자 별명</label><input id="${id}" value="${esc(value)}"${mine.length ? ' style="border-color:#dc2626" aria-invalid="true"' : ""}>` +
    (mine.length ? `<div class="small" style="color:#dc2626" id="${id}-error">${mine.map(esc).join("<br>")}</div>` : "") + `</div>`;
}
const notBy = errs => (errs || []).filter(x => !BY_ERR.test(x));
// EV-075 MON (v2 30쪽): a check result '불통과(rule: detail …)' reads '불통과 · <rules>' — the whole on mouse-over
const checkWords = t => { const m = /^불통과\((.*)\)$/.exec(String(t)); return m ? hv(`불통과 · ${[...new Set(m[1].split(/, (?=[a-z_]+[:)]|[a-z_]+$)/).map(x => x.split(":")[0].trim()))].join(" · ")}`, t) : esc(t); };
// a change line '<node> <param> · <what>' as '<role> <what>' (the line itself on mouse-over)
const changeWords = (rel, line) => String(line).replace(/^(\S+) (?:[a-z_.\[\]0-9]+ · )?/, (m, id) => roleWords(rel, id) !== id ? `${roleWords(rel, id)} ` : m);
// EV-075 (R3): a value that does not fit one line folds — its first line (the summary) always shows, the rest opens on a click and
// stays open across renders (UI.folds). `body` is HTML; a plain value goes in fold.pre.
function fold(key, summary, body) {
  const open = !!(UI.folds && UI.folds[key]);
  return `<div class="fold" data-fold="${esc(key)}"><span class="fold-h" data-act="fold" data-k="${esc(key)}">${open ? "▾" : "▸"} ${esc(summary)}</span>${open ? `<div class="fold-b">${body}</div>` : ""}</div>`;
}
fold.pre = v => `<pre class="fold-pre">${esc(typeof v === "string" ? (() => { try { return JSON.stringify(JSON.parse(v), null, 1); } catch (e) { return v; } })() : JSON.stringify(v, null, 1))}</pre>`;
function now() { const d = new Date(); return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`; }
// LLMOps keys per provider (EV-005): status only; a value never reaches the UI
const PROVIDER_IDS = ["openai", "anthropic", "gemini"];
const PROVIDER_ICON = {   // simple drawings in the page itself (D-2): no logo files, no outside request
  openai: `<svg class="picon" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#111"/><g fill="none" stroke="#fff" stroke-width="1.5">${[0, 60, 120].map(a => `<ellipse cx="12" cy="12" rx="7" ry="3" transform="rotate(${a} 12 12)"/>`).join("")}</g></svg>`,
  anthropic: `<svg class="picon" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#f3e7dc"/><path d="M6.5 18 11 6h2l4.5 12h-2.4l-1.1-3H9.9l-1.1 3zm4.1-5h2.8L12 9.1z" fill="#c4643c"/></svg>`,
  gemini: `<svg class="picon" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#eef1fe"/><path d="M12 3c.6 4.6 3.4 7.4 9 9-5.6 1.6-8.4 4.4-9 9-.6-4.6-3.4-7.4-9-9 5.6-1.6 8.4-4.4 9-9z" fill="#5b7cf4"/><path d="M12 8c.3 2 1.6 3.4 4 4-2.4.6-3.7 2-4 4-.3-2-1.6-3.4-4-4 2.4-.6 3.7-2 4-4z" fill="#a78bfa"/></svg>`,
};
function provOf(id) { return ((S.settings && S.settings.providers) || []).find(x => x.id === id); }
function projLlm(sc) {   // the Project's provider and model; none chosen → OpenAI and its default model (D-5)
  const id = sc && PROVIDER_IDS.includes(sc.llm_provider) ? sc.llm_provider : "openai", pv = provOf(id);
  return { id, name: pv ? pv.name : "OpenAI", model: (sc && sc.llm_model) || (pv ? pv.default_model : "gpt-4.1-mini") };
}
function hasKey(id = "openai") { const pv = provOf(id); return pv ? pv.status !== "none" : !!S.settings && S.settings.key !== "none"; }
const STOPPED = "검토 전 멈춤(2차 범위)", SUITE = "평가 suite", SUITE_NOTE = "값 없음 — 평가 suite Run은 scorecard 참고";   // EV-002
const runKind = i => i.id === "data" ? "data" : i.kind === SUITE ? "suite" : "set";
function proj(id) { return S.projects.find(p => p.id === id); }
function relOf(p, id) { return p.releases.find(r => r.id === id); }
function runOf(p, id) { return p.runs.find(r => r.id === id); }
/* EV-064 CHG-CF-4 · 6 (D-406 ③): the Run a release's screen shows — this version's opened Run, else its last Run (Run · Project
   tabs, 'Run 취소', 실행 멈춤) — and, while the 판정 tab is open, the Run that is judged: this version's opened Run, else its latest
   same-condition evaluation (the S-18 pair), else its last Run. The 판정 tab shows, warns about and saves that one Run. */
function shownRun(p, rel) {
  const opened = UI.runId && runOf(p, UI.runId);
  if (opened && opened.rel === rel.id) return opened;
  const mine = p.runs.filter(r => r.rel === rel.id);
  return mine[mine.length - 1];
}
function judgeRun(p, rel) {
  const opened = UI.runId && runOf(p, UI.runId);
  if (opened && opened.rel === rel.id) return opened;
  const pairs = ((rel.version || {}).judge || {}).pairs || {}, paired = p.runs.filter(r => r.rel === rel.id && pairs[r.uid]);
  return paired.length ? paired[paired.length - 1] : shownRun(p, rel);
}
function go(h) { location.hash = h; }
/* EV-012 CHG-70: a suite's registration tag — S-n (order of registration in the Project) and the registration time;
   a later registration of the same content (Pack id, entrypoint, suite_revision, dataset_hash) names the earliest one */
const pad2 = v => String(v).padStart(2, "0");
const regTime = t => { const d = new Date(t * 1000); return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; };
const suiteKey = s => [s.pack_id, s.graph, s.suite_revision, s.dataset_hash].join("|");
function suiteInfo(p, id) {
  const list = p.suites || [], i = list.findIndex(s => s.id === id);
  if (i < 0) return null;
  const same = list.map((x, j) => j).filter(j => suiteKey(list[j]) === suiteKey(list[i]));
  const df = list[i].derived_from, k = df ? list.findIndex(s => s.id === df.suite) : -1;   // EV-025 OPS-3: S-k + 올린 case m개
  return { seq: i + 1, tag: `S-${i + 1} · ${regTime(list[i].created_at)} 등록` + (df ? ` · ${k >= 0 ? `S-${k + 1}` : "기준 suite"}` + (df.wheel ? `의 case ${df.kept ?? "?"}개 + 개선이 올린 case ${df.added ?? 0}개 · ✦ 자동` : ` + 올린 case ${(df.promotions || []).length}개`) : ""), sameAs: same[0] < i ? `S-${same[0] + 1}과 같은 내용` : null, group: same.map(j => `S-${j + 1}`) };
}
function inputsOf(p, rel) {
  const list = [];
  if (p.data) list.push({ kind: "검증데이터", id: "data", name: p.data.name, n: p.data.cases.length, src: "파일" });
  for (const s of p.sets) list.push({ kind: "입력값 세트", id: s.id, name: s.name, n: s.cases.length, src: s.source + (s.release ? ` · ${s.release}` : "") + (s.provider ? ` · ${s.provider}` : "") });
  for (const s of p.suites || []) if (!rel || (rel.pack && rel.pack.id === s.pack_id))   // a suite runs only on a release of its Pack (EV-002 CHG-11)
    list.push((si => ({ kind: SUITE, id: s.id, name: s.name, n: s.cases.length, tag: si.tag,
      src: `Pack ${s.pack_id} · evals/${s.folder} · ${s.split} · revision ${s.suite_revision.slice(7, 15)}` + (si.sameAs ? ` · ${si.sameAs}` : "") }))(suiteInfo(p, s.id)));
  return list;
}
function casesOfInput(p, input) {
  if (input.id === "data") return p.data.cases.map(c => ({ input: c.input, expected: c.expected, forbidden: c.forbidden }));
  if (input.kind === SUITE) return (p.suites.find(x => x.id === input.id) || { cases: [] }).cases.map(c => ({ input: c.input }));
  const s = p.sets.find(x => x.id === input.id);
  return s.cases.map(c => ({ input: c }));
}
function scoringLabel(sc) { return sc.skip ? "생략" : [sc.rule && "규칙", sc.llm && "LLM"].filter(Boolean).join(" + "); }
function ruleText(p) { return `기준 집계 ≥ ${p.rule.pass.toFixed(2)} · 기준 미만 → ${VERDICT_WORD[p.rule.below] || p.rule.below} · 실패·timeout 있음 → 보류 · 모두 실패 → 버림 · 점수 없음 → 권고 없음`; }

/* ------------------------------------------------------------------ parsing uploaded files (AC-02, AC-33-C1, AC-38) */
function parseFile(name, text, needExpected) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (!["json", "csv"].includes(ext)) return { error: `JSON/CSV 파일만 올릴 수 있습니다 (받은 파일: ${name})` };
  let rows = [];
  if (ext === "json") {
    try { rows = JSON.parse(text); } catch (e) { return { error: `JSON을 읽을 수 없습니다 — ${e.message}` }; }
    if (!Array.isArray(rows)) return { error: "JSON은 case 배열이어야 합니다" };
    rows = rows.map((r, i) => ({ line: i + 1, unit: "case", r: typeof r === "string" ? { input: r } : r }));
  } else {
    const lines = text.split(/\r?\n/).filter(l => l.trim());
    const head = (lines.shift() || "").split(",").map(s => s.trim());
    rows = lines.map((l, i) => { const v = l.split(","); const o = {}; head.forEach((h, j) => o[h] = (v[j] || "").trim()); return { line: i + 2, unit: "줄", r: o }; });
  }
  if (!rows.length) return { error: "case가 하나도 없습니다" };
  for (const { line, unit, r } of rows) {
    const where = unit === "줄" ? `${line}번째 줄` : `${line}번째 case`;
    const emptyIn = r.input === undefined || r.input === null || (!(typeof r.input === "object" && !Array.isArray(r.input)) && String(r.input).trim() === "");
    if (emptyIn) return { error: `${where}에 '입력(input)' 필드가 없습니다` };
    if (needExpected && (r.expected === undefined || String(r.expected).trim() === "")) return { error: `${where}에 '기대 정답(expected)' 필드가 없습니다` };
  }
  const inp = v => (v && typeof v === "object" && !Array.isArray(v)) ? v : String(v);   // JSON objects stay objects (EV-002 CHG-03)
  return { cases: rows.map(({ r }) => needExpected ? { input: inp(r.input), expected: String(r.expected), forbidden: String(r.forbidden || "") } : inp(r.input)) };
}

/* ------------------------------------------------------------------ graph */
/* EV-012 CHG-72/73: a Pack node's role as a flowchart symbol (ISO 5807 family). router = decision diamond, retrieve = stored
   data cylinder, custom_code = predefined process, validate = hexagon (borrowed preparation symbol for a check), human_review =
   manual operation, render = document, llm·predict and nodes without a kind (LangGraph code releases) = process box */
const NODE_SHAPE = { router: "decision", retrieve: "database", human_review: "manual", render: "document", custom_code: "predefined", validate: "preparation", llm: "process", predict: "process" };
const LEGEND = [["decision", "분기", "router"], ["database", "리트리버", "retrieve"], ["process", "처리", "llm · predict"], ["predefined", "Pack 함수", "custom_code"],
  ["preparation", "검사", "validate"], ["manual", "사람 검토", "human_review"], ["document", "결과 문서", "render"]];
const kindOf = n => (n.kind || "").split("@")[0];
const shapeOf = n => NODE_SHAPE[kindOf(n)] || "process";
// where the name and the state line sit in each shape: left offset, name y, state y (the diamond centres both)
const NODE_TEXT = { process: [8, 14, 27], database: [8, 19, 30], predefined: [11, 14, 27], preparation: [12, 14, 27], manual: [11, 14, 26], document: [8, 14, 25] };
// outline of a 90x34 node at (x, y); only the diamond reaches past the box (15 px each side, 5 px up and down), inside the layout gaps
function nodeOutline(shape, x, y, paint, line) {
  switch (shape) {
    case "decision": return `<polygon data-outline points="${x - 15},${y + 17} ${x + 45},${y - 5} ${x + 105},${y + 17} ${x + 45},${y + 39}" ${paint}/>`;
    case "database": return `<path data-outline d="M${x},${y + 5} a45,5 0 0,1 90,0 v24 a45,5 0 0,1 -90,0 z" ${paint}/><path class="deco" d="M${x},${y + 5} a45,5 0 0,0 90,0" fill="none" ${line}/>`;
    case "predefined": return `<rect data-outline x="${x}" y="${y}" width="90" height="34" rx="2" style="rx:2px" ${paint}/><path class="deco" d="M${x + 6},${y} v34 M${x + 84},${y} v34" fill="none" ${line}/>`;
    case "preparation": return `<polygon data-outline points="${x + 10},${y} ${x + 80},${y} ${x + 90},${y + 17} ${x + 80},${y + 34} ${x + 10},${y + 34} ${x},${y + 17}" ${paint}/>`;
    case "manual": return `<polygon data-outline points="${x},${y} ${x + 90},${y} ${x + 80},${y + 34} ${x + 10},${y + 34}" ${paint}/>`;
    case "document": return `<path data-outline d="M${x},${y} h90 v28 q-22.5,-10 -45,0 t-45,0 z" ${paint}/>`;   // D-459: the wave dips under the left text, not through it
    default: return `<rect data-outline x="${x}" y="${y}" width="90" height="34" rx="6" style="rx:6px" ${paint}/>`;   // a plain box: radius 14 on 34 px read as a terminator pill
  }
}
function legendIcon(shape) {
  const p = 'fill="#fff" stroke="#667085" stroke-width="1.2"', l = 'stroke="#667085" stroke-width="1.2"';
  const g = { decision: `<polygon points="1,8 12,1 23,8 12,15" ${p}/>`, database: `<path d="M2,4 a10,2.5 0 0,1 20,0 v8 a10,2.5 0 0,1 -20,0 z" ${p}/><path d="M2,4 a10,2.5 0 0,0 20,0" fill="none" ${l}/>`,
    process: `<rect x="2" y="2" width="20" height="12" style="rx:3px" ${p}/>`, predefined: `<rect x="2" y="2" width="20" height="12" style="rx:0" ${p}/><path d="M5,2 v12 M19,2 v12" ${l}/>`,
    preparation: `<polygon points="5,2 19,2 22,8 19,14 5,14 2,8" ${p}/>`, manual: `<polygon points="2,2 22,2 19,14 5,14" ${p}/>`, document: `<path d="M2,2 h20 v10 q-5,-3 -10,0 t-10,0 z" ${p}/>` }[shape];
  return `<svg width="24" height="16" viewBox="0 0 24 16" aria-hidden="true">${g}</svg>`;
}
/* the shapes this graph uses, under a canvas graph; none for a graph without node kinds (LangGraph code releases) */
function graphLegend(g) {
  if (!g.nodes.some(n => n.kind)) return "";
  const used = new Set(g.nodes.map(shapeOf));
  return `<div class="small muted graph-legend" id="graph-legend">${LEGEND.filter(([s]) => used.has(s)).map(([s, l, k]) => `<span title="${esc(k)}">${legendIcon(s)}${l}</span>`).join("")}</div>` +
    `<div class="small muted" id="graph-legend-colors">${hv("주황 = 바뀐 곳", "◇ = 분기 router · 파랑 = packrt 표준 node · 보라 = Pack 함수 node · 주황 = 출발 버전에서 바뀐 node · 주황 점선 = 새 node(반영 전)")}</div>`;   // manual p.5 · 11 · EV-075 GRAPH
}
/* EV-013 CHG-76: a node name wider than its shape's text room (from the name's start to the outline, inner bar or slant, less 2-4 px;
   SVG units) is drawn at the room's width, squeezed to at most 75% of its natural width; a longer name is cut with … first
   (the whole name stays in data-node and the node detail). The width is the name as drawn: bold 12px
   (svg text[font-weight="700"]{font-size:12px}) in the page font. The diamond keeps its EV-012 rule. */
const NAME_ROOM = { process: 78, database: 78, predefined: 70, preparation: 70, manual: 72, document: 78 };
const MIN_SQUEEZE = 0.75;
let nameCtx = null, nameFont = null;
function nameWidth(s) {
  if (!nameCtx) { nameCtx = document.createElement("canvas").getContext("2d"); nameFont = `700 12px ${getComputedStyle(document.body).fontFamily}`; }
  nameCtx.font = nameFont;
  return nameCtx.measureText(s).width;
}
function fitName(name, room) {
  if (nameWidth(name) <= room) return [name, ""];
  let s = name;
  if (nameWidth(name) * MIN_SQUEEZE > room) { while (s.length > 1 && nameWidth(s + "…") * MIN_SQUEEZE > room) s = s.slice(0, -1); s += "…"; }
  return [s, ` textLength="${room}" lengthAdjust="spacingAndGlyphs"`];
}
/* E-S-09 L1 · L3 (manual p.19 · 20, '정리 규칙 … 선 겹침 최소'): an Agent Pack graph draws each line in horizontal and vertical
   pieces through the gaps between nodes. Of the ways from one node side to another (side to side through a near gap or the outer
   gap, bottom to top, side to top), the one through no node, START · END or branch name with the fewest crossings is chosen, so a
   line from the end of a folded row goes down the fold's outer gap into the first node below. The branch name sits beside the
   line's longest piece where it overlaps nothing. Returns {edge id: {pts, label: {x, y, anchor} | null}}. */
const nodeBox = n => shapeOf(n) === "decision" ? [n.x - 15, n.y - 5, n.x + 105, n.y + 39] : [n.x, n.y, n.x + 90, n.y + 34];
const labelWidth = s => [...String(s)].reduce((w, c) => w + (c.charCodeAt(0) > 255 ? 10 : 5.6), 0);
function segs(pts) { const out = []; for (let i = 1; i < pts.length; i++) out.push([pts[i - 1], pts[i]]); return out; }
function segHitsBox([[x1, y1], [x2, y2]], [l, t, r, b], pad = 1) {
  const [ax, bx] = [Math.min(x1, x2), Math.max(x1, x2)], [ay, by] = [Math.min(y1, y2), Math.max(y1, y2)];
  return ax < r - pad && bx > l + pad && ay < b - pad && by > t + pad;
}
function segCross([[a1, b1], [a2, b2]], [[c1, d1], [c2, d2]]) {   // one horizontal and one vertical piece crossing inside both
  const h1 = b1 === b2, h2 = d1 === d2;
  if (h1 === h2) return false;
  const [[hx1, hy], [hx2]] = h1 ? [[a1, b1], [a2, b2]] : [[c1, d1], [c2, d2]], [[vx, vy1], [, vy2]] = h1 ? [[c1, d1], [c2, d2]] : [[a1, b1], [a2, b2]];
  return vx > Math.min(hx1, hx2) + 0.5 && vx < Math.max(hx1, hx2) - 0.5 && hy > Math.min(vy1, vy2) + 0.5 && hy < Math.max(vy1, vy2) - 0.5;
}
function segOverlap([[a1, b1], [a2, b2]], [[c1, d1], [c2, d2]]) {   // two pieces on the same line (2 px) sharing more than 2 px
  if (b1 === b2 && d1 === d2 && Math.abs(b1 - d1) < 3) return Math.min(Math.max(a1, a2), Math.max(c1, c2)) - Math.max(Math.min(a1, a2), Math.min(c1, c2)) > 2;
  if (a1 === a2 && c1 === c2 && Math.abs(a1 - c1) < 3) return Math.min(Math.max(b1, b2), Math.max(d1, d2)) - Math.max(Math.min(b1, b2), Math.min(d1, d2)) > 2;
  return false;
}
function packRoutes(g, obstacles = []) {
  const by = Object.fromEntries(g.nodes.map(n => [n.name, n])), boxes = g.nodes.map(n => [n.name, nodeBox(n)]);
  const routed = [], out = {};
  const cands = (S, T) => {
    const [sl, st, sr, sb] = nodeBox(S), [tl, tt, tr, tb] = nodeBox(T), sy = S.y + 17, ty = T.y + 17, sx = S.x + 45, tx = T.x + 45, list = [];
    const dedup = pts => pts.filter((p, i) => !i || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
    const via = (x0, y0, gx, x1, y1) => y0 === y1 ? [[x0, y0], [x1, y1]] : dedup([[x0, y0], [gx, y0], [gx, y1], [x1, y1]]);
    for (const o of [0, 6, -6, 12, -12]) {
      if (tl > sr + 8) for (const gx of [sr + 12, (sr + tl) / 2, tl - 12].map(v => Math.round(v + o)).filter(v => v > sr + 4 && v < tl - 4)) list.push(via(sr, sy, gx, tl, ty));
      if (tr < sl - 8) for (const gx of [sl - 12, (sl + tr) / 2, tr + 12].map(v => Math.round(v + o)).filter(v => v < sl - 4 && v > tr + 4)) list.push(via(sl, sy, gx, tr, ty));
      list.push(via(sr, sy, Math.max(sr, tr) + 14 + Math.abs(o), tr, ty), via(sl, sy, Math.min(sl, tl) - 14 - Math.abs(o), tl, ty));
      if (tt > sb + 8) for (const ym of [sb + 12, (sb + tt) / 2, tt - 12]) { const y = Math.round(ym + o); list.push(sx === tx ? [[sx, sb], [tx, tt]] : [[sx, sb], [sx, y], [tx, y], [tx, tt]]); }
      if (tb < st - 8) for (const ym of [st - 12, (st + tb) / 2, tb + 12]) { const y = Math.round(ym + o); list.push(sx === tx ? [[sx, st], [tx, tb]] : [[sx, st], [sx, y], [tx, y], [tx, tb]]); }
      if (sx !== tx) { const yb = Math.max(sb, tb) + 12 + Math.abs(o), ya = Math.min(st, tt) - 12 - Math.abs(o); list.push([[sx, sb], [sx, yb], [tx, yb], [tx, tb]], [[sx, st], [sx, ya], [tx, ya], [tx, tt]]); }   // under · over
    }
    if (tt > sy) { if (tx > sr) list.push([[sr, sy], [tx, sy], [tx, tt]]); if (tx < sl) list.push([[sl, sy], [tx, sy], [tx, tt]]); }
    if (tb < sy) { if (tx > sr) list.push([[sr, sy], [tx, sy], [tx, tb]]); if (tx < sl) list.push([[sl, sy], [tx, sy], [tx, tb]]); }
    return list;
  };
  const score = (pts, e) => {
    let s = 0;
    for (const sg of segs(pts)) {
      for (const [name, b] of boxes) if (name !== e.source && name !== e.target && segHitsBox(sg, b)) s += 1000;
      for (const b of obstacles) if (segHitsBox(sg, b)) s += 1000;
      for (const r of routed) {
        if (r.e.source === e.source || r.e.target === e.target) continue;   // lines from one node (or into one) share their side
        for (const rs of segs(r.pts)) { if (segCross(sg, rs)) s += 100; if (segOverlap(sg, rs)) s += 60; }
      }
      s += Math.abs(sg[1][0] - sg[0][0]) / 40 + Math.abs(sg[1][1] - sg[0][1]) / 40;
    }
    return s + (pts.length - 2) * 3;
  };
  const edges = g.edges.filter(e => e.kind !== "loop" && by[e.source] && by[e.target]);
  const dist = e => Math.abs(by[e.source].x - by[e.target].x) + Math.abs(by[e.source].y - by[e.target].y);
  const pick = e => { let best = null, bs = Infinity; for (const pts of cands(by[e.source], by[e.target])) { const s = score(pts, e); if (s < bs) { bs = s; best = pts; } } return best; };
  for (const e of [...edges].sort((a, b) => dist(a) - dist(b))) routed.push({ e, pts: pick(e) });
  for (let pass = 0; pass < 3; pass++) for (let i = 0; i < routed.length; i++) {   // then each line again with all the others in place
    const [r] = routed.splice(i, 1); routed.splice(i, 0, { e: r.e, pts: pick(r.e) });
  }
  // branch names: beside the longest piece (right of a vertical one, above a horizontal one), else the next free spot
  const taken = [...boxes.map(([, b]) => b), ...obstacles];
  for (const { e, pts } of routed) {
    let label = null;
    if (e.label) {
      // a name too long for the gap goes on lines split at ' · ', then at spaces (L3: the whole name shows)
      const lines = [[e.label], ...(e.label.includes(" · ") ? [e.label.split(" · ")] : []), ...(/\S \S/.test(e.label.replace(/ · /g, "·")) ? [e.label.replace(/ · /g, "·").split(" ").map(x => x.replace(/·/g, " · "))] : [])];
      let placed = null;
      // answer 022-02: when no spot beside the line is free (a short line between two boxes, as in the P04 one-row preview), the
      // name is lifted row by row above a horizontal piece instead of being drawn under a node box; spots that were free stay as they were
      for (const lift of [0, 1, 2, 3, 4]) {
      for (const ls of lines) {
      const w = Math.max(...ls.map(labelWidth)), h = ls.length * 11, spots = [], up = 11 * lift;
      const ss = segs(pts).sort((a, b) => (Math.abs(b[1][0] - b[0][0]) + Math.abs(b[1][1] - b[0][1])) - (Math.abs(a[1][0] - a[0][0]) + Math.abs(a[1][1] - a[0][1])));
      for (const [[x1, y1], [x2, y2]] of ss) {
        if (x1 === x2) { if (lift) continue; const lo = Math.min(y1, y2), hi = Math.max(y1, y2); for (const f of [0.5, 0.25, 0.75]) { const y = lo + (hi - lo) * f - h / 2 + 9; spots.push([x1 + 4, y, "start", [x1 + 3, y - 9, x1 + 5 + w, y - 9 + h]], [x1 - 4, y, "end", [x1 - 5 - w, y - 9, x1 - 3, y - 9 + h]]); } }
        else { const lo = Math.min(x1, x2), hi = Math.max(x1, x2); for (const f of [0.5, 0.25, 0.75]) { const x = lo + (hi - lo) * f; spots.push([x, y1 - 4 - h + 11 - up, "middle", [x - w / 2 - 1, y1 - 3 - h - up, x + w / 2 + 1, y1 - 2 - up]]); if (!lift) spots.push([x, y1 + 12, "middle", [x - w / 2 - 1, y1 + 2, x + w / 2 + 1, y1 + 3 + h]]); } }
      }
      const free = spots.find(([, , , b]) => b[0] >= 0 && b[1] >= 0 && !taken.some(t => b[0] < t[2] && b[2] > t[0] && b[1] < t[3] && b[3] > t[1]) &&
        !routed.some(r => r.e !== e && segs(r.pts).some(sg => segHitsBox(sg, b, 0))));
      if (free || !placed) placed = [...(free || spots[0] || [pts[0][0], pts[0][1] - 4, "start", null]), ls, !!free];
      if (free) break;
      }
      if (placed[5]) break;
      }
      const [x, y, anchor, b, ls, ok] = placed;
      label = { x: Math.round(x), y: Math.round(y), anchor, lines: ls, ok, box: b };
      if (b) taken.push(b);
    }
    out[`${e.source}>${e.target}`] = { pts, label };
  }
  return out;
}
/* the crossings between lines of different nodes (L1: 0 after ⌗ 노드 정리) */
function routeCrossings(routes) {
  const list = Object.entries(routes).map(([id, r]) => ({ ends: id.split(">"), pts: r.pts }));
  let n = 0;
  for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
    if (list[i].ends.some(x => list[j].ends.includes(x))) continue;
    for (const a of segs(list[i].pts)) for (const b of segs(list[j].pts)) if (segCross(a, b)) n++;
  }
  return n;
}
/* D-459 (user 10-29): an Agent Pack graph is drawn in the one tidy layout (web/tidy.js) on every screen — the workbench, the wheel's
   detail, the registration preview and the compared Runs: nodes, START · END, every line on its own track with an arrowhead and the
   branch names where nothing covers them. A graph already laid out (g.tidy) is drawn as it is. */
function tidied(g) {
  if (!g || g.tidy || !g.nodes || !g.nodes.length || !g.nodes.some(n => n.kind) || typeof Tidy === "undefined") return g;
  const t = Tidy.layout(g, { nameWidth: labelWidth, isDecision: n => shapeOf(n) === "decision" });
  return { ...g, nodes: t.nodes, width: t.width, height: t.height, tidy: t };
}
const ARROWS = `<defs>${[["g", "#9ca3af"], ["b", "#3056d3"]].map(([k, c]) => `<marker id="ah-${k}" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="10" markerHeight="10" orient="auto"><path d="M0,0.5 L8,4 L0,7.5 z" fill="${c}"/></marker>`).join("")}</defs>`;
/* ORCH 231 ①: a left-to-right graph wider than its card shows that it goes on — while its right part is out of sight the svg says
   data-more and its visible right edge fades (--fade-at); read again on every scroll, size change and new drawing */
const MORE_SEEN = typeof WeakSet !== "undefined" ? new WeakSet() : null;
function markMore() {
  for (const svg of document.querySelectorAll('svg[data-graph][data-flow="lr"]')) {
    const c = svg.parentElement, more = c.scrollLeft + c.clientWidth < c.scrollWidth - 2;
    if (MORE_SEEN && markMore.ro && !MORE_SEEN.has(c)) { MORE_SEEN.add(c); markMore.ro.observe(c); }   // a card that gets its size later (shown after drawing)
    svg.toggleAttribute("data-more", more);
    if (more) svg.style.setProperty("--fade-at", `${c.scrollLeft + c.clientWidth - (svg.getBoundingClientRect().left - c.getBoundingClientRect().left + c.scrollLeft)}px`);
  }
}
if (typeof document !== "undefined" && typeof MutationObserver !== "undefined") {
  let queued = false;
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; markMore(); }); } };
  document.addEventListener("scroll", later, true);
  addEventListener("resize", later);
  new MutationObserver(markMore).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-flow", "data-tidy", "class"] });   // at once, before the next paint (a graph whose marks come after it is drawn too)
  if (typeof ResizeObserver !== "undefined") markMore.ro = new ResizeObserver(later);
}
function graphSVG(g, hl = {}, scale = 1.6) {
  g = tidied(g);
  const pos = {};
  for (const n of g.nodes) pos[n.name] = [n.x, n.y];
  const st = hl.state || {}, passed = hl.passed || new Set(), counts = hl.counts || {};
  // merge 3 (manual p.5 · 11, fork B lead items 2 · 3): an Agent Pack graph shows START · END and, before any Run colour, standard
  // nodes in blue, Pack function nodes in purple with the kind under the name. START · END are drawn only (not nodes: no popover)
  const pk = g.nodes.some(n => n.kind), idleAll = !Object.keys(st).length;
  const term = pk ? (g.tidy ? { start: g.tidy.startTargets, end: Object.keys(g.tidy.ends) } : termOf(g)) : { start: [], end: [] };
  // END beside a node that leads nowhere: right, else below, else above — the first spot no node (or START) covers
  const busy = (x, y) => g.nodes.some(o => x < o.x + 96 && x + 58 > o.x - 4 && y < o.y + 40 && y + 30 > o.y - 6);
  // L2 (manual p.11 · 20): END right beside the branch's last node (the tidy layout gives such a node its own row), else left, below, above
  const endPos = g.tidy ? Object.entries(g.tidy.ends) : term.end.map(n => { const [x, y] = pos[n], spots = [[x + 114, y + 5], [x - 76, y + 5], [x + 19, y + 58], [x + 19, y - 50], [x + 114, y - 36], [x + 114, y + 46], [x + 19, y + 120], [x + 210, y + 5]];
    return [n, spots.find(([sx, sy]) => sy >= 0 && !busy(sx, sy) && !term.start.some(s => Math.abs(pos[s][0] - 70 - sx) < 60 && Math.abs(pos[s][1] + 4 - sy) < 30)) || [x + 19, y + 58]]; });
  const left = g.tidy ? 0 : Math.min(term.start.length ? Math.min(0, Math.min(...term.start.map(n => pos[n][0])) - 70) : 0, ...endPos.map(([, [ex]]) => ex - 10));
  const routes = g.tidy ? g.tidy.routes : pk ? packRoutes(g, [...term.start.map(n => [pos[n][0] - 70, pos[n][1] + 4, pos[n][0], pos[n][1] + 28]), ...endPos.map(([n, [ex, ey]]) => [Math.min(ex, pos[n][0] + 90), ey, Math.max(ex + 52, pos[n][0]), ey + 24])]) : null;
  const reach = routes ? Object.values(routes).flatMap(r => [...r.pts, ...(r.label && r.label.box ? [[r.label.box[2], r.label.box[3]]] : [])]) : [];   // lines and names past the nodes
  const W = Math.max(g.width, ...endPos.map(([, [x]]) => x + 60), ...reach.map(([x]) => x + 8)), H = Math.max(g.height, ...endPos.map(([, [, y]]) => y + 34), ...reach.map(([, y]) => y + 8));
  // an Agent Pack graph keeps its own height (from its width — no empty band above and below the folded rows, E-S-09)
  const s = [`<svg viewBox="${left} 0 ${W - left} ${H}" width="100%"${pk ? "" : ` height="${Math.round(H * scale)}"`} style="background:#f8fafc;border-radius:4px;--gw:${W - left}px${pk ? ";height:auto" : ""}" data-graph="${g.id}"${g.tidy ? ` data-tidy="1" data-flow="${g.tidy.flow || "tb"}"` : ""}${routes ? ` data-crossings="${g.tidy ? g.tidy.crossings : routeCrossings(routes)}"` : ""}>`];
  const pill = (name, x, y) => `<g class="graph-node graph-term" data-node="${name}" data-term="1"><rect x="${x}" y="${y}" width="52" height="24" rx="12" fill="#1f2937"/><text x="${x + 26}" y="${y + 16}" text-anchor="middle" font-size="10" font-weight="700" fill="#fff">${name}</text></g>`;
  if (pk) s.push(ARROWS);   // D-459: every line ends in an arrowhead
  const arrow = hot => ` marker-end="url(#ah-${hot ? "b" : "g"})"`;
  const dOf = r => r.d || `M${r.pts.map(p => p.join(",")).join(" L")}`;   // D-467: the tidy layout gives each line its own curve
  const ports = [];   // D-467: a small dot where each line leaves (drawn over the lines, under the nodes)
  const port = (r, hot) => { if (r && r.port) ports.push(`<circle class="edge-port" cx="${r.port[0]}" cy="${r.port[1]}" r="2.6" fill="${hot ? "#3056d3" : "#9ca3af"}"/>`); };
  if (g.tidy) {   // D-459: START · END lines on their own tracks too
    for (const n of term.start) { const r = routes[`START>${n}`]; if (r) { s.push(`<path data-edge="START>${esc(n)}" data-kind="plain" d="${dOf(r)}" stroke="#9ca3af" stroke-width="1.3" fill="none"${arrow(false)}/>`); port(r, false); } }
    for (const [n] of endPos) { const r = routes[`${n}>END`]; if (r) { s.push(`<path data-edge="${esc(n)}>END" data-kind="plain" d="${dOf(r)}" stroke="#9ca3af" stroke-width="1.3" fill="none"${arrow(false)}/>`); port(r, false); } }
  } else {
    for (const n of term.start) { const [x, y] = pos[n]; s.push(`<line data-edge="START>${esc(n)}" data-kind="plain" x1="${x - 18}" y1="${y + 16}" x2="${x}" y2="${y + 16}" stroke="#9ca3af" stroke-width="1.3"/>`); }
    for (const [n, [ex, ey]] of endPos) { const [x, y] = pos[n]; s.push(ex > x + 90 ? `<line data-edge="${esc(n)}>END" data-kind="plain" x1="${x + 90}" y1="${y + 17}" x2="${ex}" y2="${ey + 12}" stroke="#9ca3af" stroke-width="1.3"/>` : ex + 52 < x ? `<line data-edge="${esc(n)}>END" data-kind="plain" x1="${x}" y1="${y + 17}" x2="${ex + 52}" y2="${ey + 12}" stroke="#9ca3af" stroke-width="1.3"/>` : `<line data-edge="${esc(n)}>END" data-kind="plain" x1="${x + 45}" y1="${y + 34}" x2="${ex + 26}" y2="${ey}" stroke="#9ca3af" stroke-width="1.3"/>`); }
  }
  for (const e of g.edges) {
    const a = e.source, b = e.target, [x1, y1] = pos[a], [x2, y2] = pos[b], hot = passed.has(`${a}>${b}`);
    const col = hot ? "#3056d3" : "#9ca3af", w = hot ? 2.4 : 1.3;   // passed edge: the approved accent (EV-013 CHG-78)
    const attrs = `data-edge="${esc(a)}>${esc(b)}" data-kind="${e.kind || "plain"}" data-passed="${hot}"${e.blank ? ' data-blank="1" stroke-dasharray="4 3"' : ""}`, r = routes && routes[`${a}>${b}`];
    if (e.kind === "loop") s.push(`<path ${attrs} d="M${x1 + 40},${y1 + 34} C${x1 + 40},${y1 + 70} ${x2 + 40},${y2 + 80} ${x2 + 40},${y2 + 34}" stroke="${col}" stroke-width="${w}" fill="none" stroke-dasharray="4 3"${pk ? arrow(hot) : ""}/>`);
    else if (r) { s.push(`<path ${attrs}${r.back ? ' data-back="1"' : ""} d="${dOf(r)}" stroke="${col}" stroke-width="${w}" fill="none"${e.kind === "par" ? ' stroke-dasharray="7 3"' : ""}${pk ? arrow(hot) : ""}/>`); if (g.tidy) port(r, hot); }
    else s.push(`<line ${attrs} x1="${x1 + 90}" y1="${y1 + 16}" x2="${x2}" y2="${y2 + 16}" stroke="${col}" stroke-width="${w}"${e.kind === "par" ? ' stroke-dasharray="7 3"' : ""}${pk ? arrow(hot) : ""}/>`);
    if (e.label && r && r.label) s.push(`<text class="edge-label" data-label="${esc(e.label)}" x="${r.label.x}" y="${r.label.y}" text-anchor="${r.label.anchor}" font-size="10" fill="#b45309">${r.label.lines.length > 1 ? r.label.lines.map((l, i) => `<tspan x="${r.label.x}" dy="${i ? 11 : 0}">${esc(l)}</tspan>`).join("") : esc(e.label)}</text>`);
    else if (e.label) s.push(`<text class="edge-label" x="${(x1 + x2) / 2 + 40}" y="${(y1 + y2) / 2 + (e.kind === "loop" ? 60 : 10)}" font-size="10" fill="#b45309">${esc(e.label)}</text>`);
  }
  s.push(...ports);
  const look = { live: ["#dbeafe", "#3056d3", 2.5, "실행 중"], done: ["#fff", "#059669", 1.4, "완료"], fail: ["#fff4ed", "#c4320a", 2, "실패"], timeout: ["#fff4ed", "#c4320a", 2, "timeout"], idle: ["#fff", "#9ca3af", 1.2, "대기"], diverge: ["#fef2f2", "#dc2626", 2.6, "갈린 곳"] };
  for (const n of g.nodes) {
    const { name, x, y } = n, k = hl.diverge && hl.diverge.has(name) ? "diverge" : st[name] || "idle", shape = shapeOf(n);
    let [fill, stroke, sw, sub] = look[k];
    if (pk && n.kind && k === "idle" && idleAll && !n.dynamic) { stroke = kindOf(n) === "custom_code" ? "#7a5af8" : "#3056d3"; sw = 1.4; sub = n.kind; }
    if (n.blank) { stroke = "#b54708"; sw = 1.6; sub = "종류 미정"; }   // EV-073: a blank node (D-443)
    const dash = (k === "idle" && Object.keys(st).length) || n.dynamic || n.blank ? ' stroke-dasharray="4 3"' : "";
    const paint = `fill="${fill}" stroke="${stroke}" stroke-width="${sw}"${dash}`, line = `stroke="${stroke}" stroke-width="${Math.min(sw, 1.4)}"`;
    if (sub === "대기") sub = "";   // EV-075 GRAPH: an idle node shows its name only
    const [shown, fit] = shape === "decision" ? [name, ""] : fitName(name, NAME_ROOM[shape]);
    const label = shape === "decision"   // the diamond centres its lines; a long name is fitted to 70 px (EV-012 G-1: 11 px here)
      ? `<text x="${x + 45}" y="${y + 15}" text-anchor="middle" font-size="11" font-weight="700" style="font-size:11px"${name.length > 11 ? ' textLength="70" lengthAdjust="spacingAndGlyphs"' : ""}>${esc(name)}</text><text x="${x + 45}" y="${y + 28}" text-anchor="middle" font-size="9.5" fill="${stroke}">${n.dynamic ? "동적 구성" : sub}</text>`
      : (([dx, ny, sy]) => `<text x="${x + dx}" y="${y + ny}" font-size="11" font-weight="700"${fit}>${esc(shown)}</text><text x="${x + dx}" y="${y + sy}" font-size="9.5" fill="${stroke}">${n.dynamic ? "동적 구성" : sub}</text>`)(NODE_TEXT[shape]);
    s.push(`<g class="graph-node${hl.sel === name ? " sel" : ""}${hl.suspect === name ? " suspect" : ""}${hl.responsible === name ? " responsible" : ""}${hl.changed && hl.changed.includes(name) ? " cv-chg" : ""}" data-act="node" data-node="${esc(name)}" data-node-id="${n.id}" data-state="${k}" data-shape="${shape}"${n.kind ? ` data-kind="${esc(kindOf(n))}"` : ""}${n.dynamic ? ' data-dynamic="true"' : ""}>` +
      nodeOutline(shape, x, y, paint, line) + label +
      (counts[name] > 1 ? `<text class="loopn" x="${x + 68}" y="${y + 14}" font-size="10" fill="#7c3aed" data-count="${counts[name]}">×${counts[name]}</text>` : "") + "</g>");
  }
  if (g.tidy) s.push(pill("START", g.tidy.start[0], g.tidy.start[1]));   // D-459: one START at the top of the tidy layout
  else for (const n of term.start) { const [x, y] = pos[n]; s.push(pill("START", x - 70, y + 4)); }
  for (const [, [ex, ey]] of endPos) s.push(pill("END", ex, ey));
  s.push("</svg>");
  return s.join("");
}
/* answer 019-05 A: a branch line shows the label file's name for its key ('true' → '근거 충분'), the key itself without one */
function branchNamed(g, bl) {
  if (!bl || !Object.keys(bl).length) return g;
  return { ...g, edges: g.edges.map(e => e.label && bl[e.source] ? { ...e, label: [...new Set(e.label.split(" · ").map(k => bl[e.source][k] || k))].join(" · ") } : e) };
}
/* START's targets (the server's 'start' list, else nodes nothing leads to) and the nodes that lead nowhere (→ END) */
function termOf(g) {
  const names = new Set(g.nodes.map(n => n.name)), into = new Set(g.edges.filter(e => e.kind !== "loop").map(e => e.target)), from = new Set(g.edges.map(e => e.source));
  const start = (g.start && g.start.length ? g.start : g.nodes.map(n => n.name).filter(n => !into.has(n))).filter(n => names.has(n));
  const waiting = new Set(g.pending || []);   // a new node waiting for its next connection has no END yet (manual p.14)
  return { start, end: g.nodes.map(n => n.name).filter(n => !from.has(n) && !waiting.has(n)) };
}
function graphCounts(g) { const c = g.counts; return `node ${c.node} · edge ${c.edge} · 조건부 ${c["조건부"]} · loop ${c.loop} · 병렬 ${c["병렬"]}`; }
function highlightFor(run, ci) {
  const c = run.cases[ci];
  if (!c || !c.sim) return {};
  const state = {}, counts = {}, look = { done: "done", fail: "fail", timeout: "timeout", live: "live", cancelled: "idle" };
  for (const v of c.sim.visits) { state[v.node] = look[v.status] || "idle"; counts[v.node] = (counts[v.node] || 0) + 1; }
  return { state, counts, passed: new Set(c.sim.edges) };
}

/* ------------------------------------------------------------------ runs */
/* Scores, diagnoses and the recommendation are computed by the server when a Run ends and stored with it (P-07).
   A Run stored without them (made before scoring existed) shows no score and no recommendation. */
const NO_SUMMARY = { agg: { none: "점수 없음" }, rec: { none: "권고 없음", rule: "점수 없음 → 권고 없음" }, fails: 0, tos: 0 };
/* Runs come from the server (B-05). A live Run is followed through its event stream; every event re-reads the Run record,
   so the screen always shows the stored record (AC-30-C5). */
function adaptRun(r) {
  const cases = r.cases.map(c => ({ ...c, sim: c.state === "대기" ? null : { visits: c.visits, edges: c.passed, error: c.error, final: c.final, ms: c.ms || 0, tokens: c.tokens ?? null, tools: c.tools || [] }, events: c.visits }));
  const running = cases.findIndex(c => c.state === "실행 중");
  const ci = running >= 0 ? running : Math.max(0, cases.map(c => c.state !== "대기").lastIndexOf(true));  // between cases: the last finished one
  return { ...r, uid: r.id, id: r.label, cases, cur: { ci, vi: cases[ci] ? cases[ci].visits.length : 0 } };
}
function putRun(p, r) { const run = adaptRun(r), i = p.runs.findIndex(x => x.uid === run.uid); if (i >= 0) p.runs[i] = run; else p.runs.push(run); p.verdicts[run.id] = run.verdicts; return run; }
const STREAMS = {};
function followRun(p, run) {
  if (STREAMS[run.uid] || run.status !== "실행 중") return;
  const es = new EventSource(`/api/runs/${run.uid}/events`);
  STREAMS[run.uid] = es;
  let pending = false, ended = false;
  // answer 019l B1: when the Run ends, the version state is read again (the same-condition pairing, S-22 recommendation and the
  // adopt gate come from /api/state) — so 판정 right after 실행 ▶ shows the pairing without reloading the page
  // EV-064 CHG-CF-1: and the comparison reads its default pair again
  const pull = () => { if (pending) return; pending = true; api("GET", `/api/runs/${run.uid}`).then(r => { pending = false; putRun(proj(p.id) || p, r);   // EV-058 CHG-242: the state on screen now
    if (r.status !== "실행 중" && !ended) { ended = true; UI.cmpStale = true; refresh().catch(() => render()); } else render(); }).catch(() => { pending = false; }); };
  es.onmessage = ev => { const e = JSON.parse(ev.data); pull(); if (e.type === "run_end") { es.close(); delete STREAMS[run.uid]; setTimeout(pull, 50); } };
  es.addEventListener("end", () => { es.close(); delete STREAMS[run.uid]; pull(); });
  es.onerror = () => { es.close(); delete STREAMS[run.uid]; setTimeout(() => { pull(); const cp = proj(p.id) || p, cur = cp.runs.find(x => x.uid === run.uid); if (cur && cur.status === "실행 중") followRun(cp, cur); }, 500); };  // display can drop; the run continues
}
/* EV-064 CHG-CF-2 (D-406 ①): a new Run takes back what was chosen on P09 for this Project — the input, the picked Runs, the checks
   (and which ones a person changed) — so the comparison opens on the latest Run's input with its same-condition pair */
function forgetPicks(p) {
  const rels = new Set(p.releases.map(r => r.id));
  for (const k of Object.keys(UI.cmpRun)) if (rels.has(k.split("|")[0])) delete UI.cmpRun[k];
  for (const o of [UI.cmpSel || {}, UI.cmpTouched]) for (const k of Object.keys(o)) if (rels.has(k.slice(k.lastIndexOf("|") + 1))) delete o[k];
  UI.cmpInput = null; UI.cmpStale = true;
}
function startRun(p, rel, input) {
  return api("POST", `/api/projects/${p.id}/runs`, { release: rel.id, input: { kind: runKind(input), id: input.id } }).then(r => { const cp = proj(p.id) || p, run = putRun(cp, r); forgetPicks(cp); markWrite(); followRun(cp, run); return run; });   // EV-064: the picks go once the Run exists (slice review B1)   // EV-058 CHG-242
}
function stopRun(run) { return api("POST", `/api/runs/${run.uid}/cancel`); }

/* ------------------------------------------------------------------ project creation */
/* ------------------------------------------------------------------ shell */
function sidebar(p, active) {
  return `<nav class="side"><div class="brand">LLMOps</div>${p ? `<div class="proj" data-act="go" data-to="#/p/${p.id}">${esc(p.name)} ▾</div>` : ""}<div class="grp">전체</div>` +
    `<a class="${active === "projects" ? "on" : ""}" data-act="go" data-to="#/">Projects</a><div class="foot"><a class="${active === "settings" ? "on" : ""}" data-act="go" data-to="#/settings">설정</a></div></nav>`;
}
function shell(p, active, crumb, body) {
  return sidebar(p, active) + `<div class="content"><div class="bar"><span class="crumb">${crumb}</span></div><main>${body}</main></div>`;
}
function crumbP(p, extra) { return `<span data-act="go" data-to="#/">Projects</span> › <b data-act="go" data-to="#/p/${p.id}">${esc(p.name)}</b>${extra ? " › " + extra : ""}`; }

/* ------------------------------------------------------------------ P01 / P02 */
function pageProjects() {
  const head = `<section class="rg flat" data-region="R-01"><div class="row"><h1>Projects</h1><span class="sp"></span><span class="btn" data-act="menu" id="example-btn" title="예시로 시작 ▾ (기술지원 문서 RAG · 긴급 계약 메일 회신)">예시로 시작 ▾</span><span class="btn p" data-act="go" data-to="#/new" id="new-project">+ 새 Project</span></div>` +
    (UI.menu ? `<div class="rg band" id="example-menu" style="margin-top:8px">` + S.examples.map(x => `<div class="lrow" data-act="example" data-name="${esc(x.name)}"><b>${esc(x.name)}</b><span class="small muted">${esc(x.desc)} · 검증데이터 ${x.cases} case · release ${x.releases.length}</span></div>`).join("") + "</div>" : "") +
    `<div class="row" style="margin-top:10px"><input style="flex:1" placeholder="Project 찾기" id="psearch"><span class="muted small">최근 수정 순 ▾</span></div></section>`;
  let body;
  if (!S.projects.length) body = head + `<section class="rg flat" data-region="R-02"><div class="empty" id="projects-empty"><h2>아직 Project가 없습니다</h2><div class="muted">Agent 코드를 평가하려면 Project부터 만드세요.</div><div class="row" style="justify-content:center;margin-top:12px"><span class="btn p" data-act="go" data-to="#/new">+ 새 Project</span></div><div style="margin-top:8px"><span class="link" data-act="menu">예시로 시작하기</span> <span class="small muted">(기술지원 문서 RAG · 긴급 계약 메일 회신)</span></div></div></section>`;
  else body = head + `<section class="rg flat" data-region="R-02"><div class="small muted" style="margin-bottom:6px">Project ${S.projects.length}개</div><div class="cards3" id="project-cards">` + S.projects.map(p => {
    const wait = p.runs.filter(r => r.summary && !(p.verdicts[r.id] || []).length).length, last = p.runs[p.runs.length - 1];
    return `<div class="card" data-act="go" data-to="#/p/${p.id}" data-project="${esc(p.name)}"><div class="row"><b>${esc(p.name)}</b><span class="sp"></span><span class="muted">⋯</span></div><div class="small muted" style="margin:4px 0 10px">${p.data ? `검증데이터 ${p.data.cases.length} case` : "검증데이터 없음"}${p.sets.length ? ` · 입력값 세트 ${p.sets.length}` : ""}</div>` +
      `<div class="small">release ${p.releases.length} · 판정 대기 ${wait}</div><div class="small muted">${hv(last ? `최근 실행 ${last.status}` : "실행 없음", `채점 ${scoringLabel(p.scoring)} · 최근 Run ${last ? last.id + " " + last.status : "없음"}`)}</div></div>`;
  }).join("") + "</div></section>";
  return shell(null, "projects", "<b>Projects</b>", body);
}
function newProjectModal() {
  const n = UI.newp;
  const chip = (k, label) => `<span class="chip${n.open === k ? " on" : ""}" data-act="chip" data-k="${k}">${label} ▾</span>`;
  let sect = "";
  if (n.open === "data") sect = `<section class="rg ${n.dataErr ? "err" : "dash"}" data-region="R-02"><h3>검증데이터 (선택)</h3><div class="drop">검증데이터 파일(JSON/CSV)을 고르세요 <input type="file" id="np-file" accept=".json,.csv,.txt"></div><div class="small muted" style="margin-top:4px">${hv("비워도 됩니다", "case 필드: input(필수), expected(필수), forbidden(선택) · 비우면 검증데이터 없이 만듭니다")}</div>` +
    (n.dataErr ? `<div class="rg err small" id="np-data-error" style="margin-top:6px"><b>올릴 수 없음</b> — ${esc(n.dataErr)}</div>` : "") +
    (n.data ? `<table style="margin-top:6px"><tr><th>입력</th><th>기대 정답</th><th>금지 문구</th></tr>${n.data.cases.slice(0, 3).map(c => `<tr><td>${esc(typeof c.input === "string" ? c.input : JSON.stringify(c.input))}</td><td>${esc(c.expected)}</td><td>${esc(c.forbidden || "—")}</td></tr>`).join("")}</table><div class="small" id="np-data-count">case ${n.data.cases.length}개 확인 · ${esc(n.data.name)}</div>` : "") +
    `<div style="margin-top:6px"><span class="link" data-act="np-skip" id="np-skip">검증데이터 없이 만들기</span></div></section>`;
  if (n.open === "score") {
    const pv = provOf(n.prov), llmOn = n.score === "llm" || n.score === "both";
    // LLM scoring on: provider and model of this Project (EV-005 CHG-27); field names avoid the word "mode" (no mode control)
    const llm = !llmOn ? "" : `<div id="np-llm" style="margin-top:10px;display:grid;gap:8px"><div class="row"><span class="small muted" style="width:64px">provider</span>` +
      PROVIDER_IDS.map(id => { const x = provOf(id); return `<label class="pchoice${n.prov === id ? " on" : ""}"><input type="radio" name="np-prov" value="${id}"${n.prov === id ? " checked" : ""} data-act="np-prov"> ${PROVIDER_ICON[id]} ${esc(x ? x.name : id)}</label>`; }).join("") +
      `</div><div class="row"><span class="small muted" style="width:64px">모델</span><input id="np-llm-name" value="${esc(n.llmName)}" style="width:240px" autocomplete="off"><span class="small muted">provider를 바꾸면 기본값으로</span></div>` +
      `<div class="small" id="np-llm-key">키 상태 · ${esc(pv ? pv.name : n.prov)}: ${esc(pv ? pv.status_label : "")}${pv && pv.status === "none" ? ` — <span class="link" data-act="go" data-to="#/settings">설정에서 등록</span>` : ""}</div></div>`;
    sect = `<section class="rg dash" data-region="R-02"><h3>채점 방식</h3>` + [["skip", "생략"], ["rule", "규칙"], ["llm", "LLM"], ["both", "규칙 + LLM"]].map(([k, l]) => `<label style="margin-right:12px"><input type="radio" name="np-score" value="${k}"${n.score === k ? " checked" : ""} data-act="np-score"> ${l}</label>`).join("") + llm + "</section>";
  }
  if (n.open === "rule") sect = `<section class="rg dash" data-region="R-02"><h3>권고 기준</h3><div class="small">집계 점수 ≥ <input id="np-pass" value="${n.pass}" style="width:60px"> 이면 채택 · 기준 미만이면 <select id="np-below"><option value="hold"${n.below === "hold" ? " selected" : ""}>보류</option><option value="reject"${n.below === "reject" ? " selected" : ""}>버림</option></select></div><div class="small muted">실패·timeout 있음 → 보류 · 모두 실패 → 버림 · 점수 없음 → 권고 없음</div></section>`;
  const body = `<input id="np-name" style="font-size:16px;border:0;padding:0" placeholder="Project 이름" value="${esc(n.name)}"><input id="np-desc" style="border:0;padding:0" placeholder="설명 (선택)" value="${esc(n.desc)}">` +
    `<div class="row">${chip("data", "검증데이터 (선택)")}${chip("score", "채점: " + ({ skip: "생략", rule: "규칙", llm: "LLM", both: "규칙 + LLM" }[n.score]))}${chip("rule", `통과 기준 ${Math.round(Number(n.pass) * 100)}%`)}</div>${sect}`;
  return `<div class="scrim"><div class="modal" data-region="R-01"><div class="mh"><span class="muted">Projects ›</span><b>새 Project</b><span class="sp"></span><span class="muted" data-act="np-cancel">✕</span></div><div class="mb">${body}</div>` +
    `<div class="mf"><span></span><span class="sp"></span><span class="btn" data-act="np-cancel" id="np-cancel">취소</span><span class="btn p${n.dataErr ? " off" : ""}" data-act="np-create" id="np-create">Project 만들기</span></div></div></div>`;
}

/* ------------------------------------------------------------------ workbench (P03~P09, P11~P13) */
function workbench(p, tabOn, center, itabs, itabOn, insp, right) {
  const rel = p.releases.map(r => `<span class="wtab${r.id === tabOn ? " on" : ""}" data-act="go" data-to="#/p/${p.id}/r/${r.id}" data-tab="${esc(r.name)}"><span style="color:${r.color}">●</span> ${esc(r.name)}${r.version && r.version.from && r.version.from.auto ? '<span class="small" data-auto-tab> ✦ 자동</span>' : ""}${vbadge(r.version && r.version.badge, r.version && r.version.state === "승인됨" && r.version.rollout ? rolloutWord(r.version) : r.version && r.version.auto_rec === "reject" && r.version.state === "평가 중" ? `권고 ${VERDICT_WORD.reject} (자동)` : "")}${p.edit && p.edit.release === r.id ? `<span class="vb" data-vb="edit" data-badge-edit="1">✎ 편집 중</span>` : ""}${r.id === tabOn ? ` <span class="muted" data-act="rel-del" data-rel="${r.id}" title="release 삭제" id="rel-menu">⋯</span>` : ""}</span>`).join("");
  const other = [["inputs", "실행 입력"], ["compare", "비교"], ["briefs", "요청서"], ...(p.auto && p.auto.on ? [["auto", "✦ 자동 개선"]] : [])].map(([k, l]) => `<span class="wtab${tabOn === k ? " on" : ""}" data-act="go" data-to="#/p/${p.id}/${k}" data-tab="${l}">${l}</span>`).join("");
  const head = `<div class="wtabs">${rel}${tabOn === "new" && UI.reg && UI.reg.name ? `<span class="wtab">${esc(UI.reg.name)} (등록 중)</span>` : ""}<span class="wtab${tabOn === "new" ? " on" : ""}" data-act="go" data-to="#/p/${p.id}/new-release" data-tab="+ release">＋ release</span><span class="wsep"></span>${other}<span class="sp"></span>${right || ""}</div>`;
  const tabsHtml = `<div class="row" style="border-bottom:1px solid #e5e7eb;margin-bottom:8px">` + itabs.map(([k, l]) => `<span data-act="itab" data-k="${k}" style="padding:4px 0;margin-right:14px;font-size:12px;${k === itabOn ? "color:#1d4ed8;border-bottom:2px solid #3056d3;font-weight:600" : "color:#6b7280"}">${l}</span>`).join("") + "</div>";
  return `<section class="rg flat" data-region="R-01"><div class="wb">${head}<div class="wbody"><div class="wcenter" data-region="R-02">${center}</div><div class="insp" data-region="R-03">${tabsHtml}${insp}</div></div></div></section>`;
}
const RTABS = [["project", "Project"], ["exec", "실행"], ["run", "Run"], ["verdict", "판정"]];
const RTABS_EDIT = [["project", "Project"], ["change", "변경"], ["run", "Run"], ["verdict", "판정"]];   // EV-022 p.9: an Agent Pack release being edited
/* EV-SUI-01 CHG-S04 (10-13 C · D-155): a review record in words — an automatic one says that no person reviewed it */
const REVIEW_ACTION = { approve: "승인", edit: "고침", escalate: "escalate", reject: "반려" };
const REVIEW_ACTOR = { auto: "자동(사람 검토 아님)", fixture: "정해 둔 답", model: "모델", human: "사람" };
function reviewText(r) {
  return [REVIEW_ACTION[r.action] || r.action || "", REVIEW_ACTOR[r.actor_kind] || r.actor_kind || "", r.reason || ""].filter(Boolean).join(" · ");
}
function autoActionsText(a) {
  const order = [["approve", "승인"], ["escalate", "escalate"], ["reject", "반려"], ["edit", "고침"]];
  const parts = order.filter(([k]) => a && a[k]).map(([k, n]) => `${n} ${a[k]}`).concat(Object.keys(a || {}).filter(k => !order.some(([o]) => o === k)).map(k => `${k} ${a[k]}`));
  return parts.length ? `(${parts.join(" · ")})` : "";
}
/* EV-022 CHG-107: the version state of an Agent Pack release, computed by the server (llmops/versions.py) */
const VB = { "운영": "op", "평가 중": "ev", "승인됨": "ok", "거절": "no", "이전": "old", "승인 대기": "await" };   // EV-035 CHG-185: the 7th, the wheel's versions only
function rolloutWord(v) {   // D-624 4번: a Pack refusal of the base reads as not deployed because the base moved, not as a broken deploy
  const ro = v && v.rollout;
  if (!ro) return "";
  if (!ro.failed) return "배포 진행 중";
  return /^거부\(base\)/.test(ro.fail_reason || "") ? "배포 안 됨 · 기준 바뀜" : "배포 실패";
}
function vbadge(st, more) { return st ? `<span class="vb" data-vb="${VB[st] || "ev"}" data-badge="${esc(st)}"${more ? ` data-badge-more="${esc(more)}"` : ""}>${esc(st)}${more ? ` · ${esc(more)}` : ""}</span>` : ""; }   // H5: 승인됨 · 배포 진행 중 (S-16)
/* the band over the graph of a version that is not being edited here (manual p.5 · 23 · 26 · 27 · 29); while it is being
   edited the edit band of web/popover.js takes this place */
function versionBand(p, rel) {
  const v = rel.version;
  if (!v) return "";
  const from = v.from ? (relOf(p, v.from.rid) || { name: v.from.release, id: null }) : null, ap = v.approval;
  const bits = [from ? `${v.from.at ? esc(v.from.at) + " " : ""}${esc(from.name)}에서 확정` : "", `revision ${revShort(v.revision)}`,
    ap ? `${VERDICT_WORD.adopt}${ap.by ? `(${esc(ap.by)})` : ""} · ${esc(ap.at || "")} · 평가 ${esc((runOf(p, ap.label) || {}).id || ap.label || "")}${ap.compare ? ` · 비교 ${esc(ap.compare.name)} ${esc(ap.compare.label)}` : ""}` : "",
    v.detail ? esc(v.detail) : "",
    v.state === "운영" ? "운영 버전 — 고치면 편집이 시작되고 확정 때 새 버전" : v.lock_reason ? (v.detail === v.lock_reason ? "고치면 확정 때 새 버전" : `${esc(v.lock_reason)} — 고치면 확정 때 새 버전`) : from ? `고정(고치려면 ${esc(rel.name)}에서 새 편집)` : ""].filter(Boolean);
  if (v.from && v.from.auto && !["운영", "이전"].includes(v.state)) bits.unshift(`<span data-auto-band>${hv(`✦ 자동 개선 A-${v.from.auto.wheel}`, `✦ 자동 개선 A-${v.from.auto.wheel} · 가설 ${v.from.auto.label}`)}</span>`);   // EV-035 CHG-183 · EV-075 F-05: short, the whole on mouse-over
  if (v.await) bits.splice(1, 0, `<span data-await-band>${hv(`권고 ${VERDICT_WORD[v.auto_rec] || VERDICT_WORD.adopt}`, `판정 권고 ${esc(v.auto_rec || "adopt")}(${recBy(v.await)}) · 평가 ${v.await.label || ""} · 비교 ${(v.await.compare || {}).name || ""} ${(v.await.compare || {}).label || ""}`)}</span>`);   // EV-035 CHG-185
  if (v.deployment) bits.unshift(`배포 ${esc(v.deployment.deployment_id)} · 태그 ${esc(v.deployment.tag)}${v.state === "운영" ? ` · ${esc(v.deployment.at)}부터` : ""}`);
  if (["운영", "이전"].includes(v.state)) bits.splice(0, bits.length, ...[v.deployment ? `배포 ${esc(v.deployment.deployment_id)} · 태그 ${esc(v.deployment.tag)}${v.state === "운영" ? ` · ${esc(v.deployment.at)}부터` : ""}` : "", `revision ${revShort(v.revision)}`,
    v.state === "운영" ? "운영 버전 — 고치면 편집이 시작되고 확정 때 새 버전" : "이전 배포 버전 — 보기만"].filter(Boolean));   // p.5 · 29: deployment and revision only
  const rb = v.state === "운영" ? v.rollback : null;   // EV-SUI-01 (D-140): the rollback from this 운영 version, in progress or failed
  if (v.state === "운영" && v.op_note) bits.push(`<span data-op-note>${esc(v.op_note)}</span>`);
  if (rb) bits.push(rb.failed ? `<span data-rb-state="failed">되돌리기 실패 — ${esc(rb.reason || "이유 없음")}</span>` : `<span data-rb-state="live">${esc(rb.target_name)}으로 되돌리는 중</span>`);
  const ro = ["운영", "이전"].includes(v.state) ? null : v.rollout;
  const stepRow = (list, id) => `<span class="dep-steps" id="${id}">${list.map((x, i) => `<span class="dep-step" data-step="${i + 1}" data-state="${x.state}">${{ done: "✓", active: "…", failed: "✕", wait: "○" }[x.state]} ${i + 1} ${esc(x.name)}</span>`).join("")}</span>`;
  const steps = ro ? stepRow(ro.steps, "vb-steps") : rb && !rb.failed ? stepRow(rb.steps, "vb-rb-steps") : "";
  const btns = (from && from.id && v.state !== "운영" ? `<span class="btn" data-act="vb-diff" data-a="${from.id}" data-b="${rel.id}" id="vb-diff">변경 내용(diff)</span>` : "") +
    ((v.state === "승인됨" || v.await) && !ro && !v.moved_to ? `<span class="btn p" data-act="dep-open" data-rel="${rel.id}" id="vb-deploy">배포 승인 ▶</span>` : "") +   // EV-035 CHG-186: also on 승인 대기
    (v.moved_to ? `<span class="small muted" id="vb-moved">이 변경은 ${esc(v.moved_to)}에 옮겨 배포 승인했습니다</span>` : "") +   // D-687 12-e 덧: the server refuses a second approval here
    (ro && v.state !== "운영" ? `<span class="btn" data-act="dep-open" data-rel="${rel.id}" id="vb-deploy-steps">배포 단계</span>` : "") +
    (v.state === "운영" ? `<span class="btn" data-act="op-runs" id="vb-op-runs">운영 Run 보기</span>` : "") +
    // D-652 6-a: an earlier version's operational Runs and the people's checks on them, for viewing only (no promote, no label form)
    (v.state === "이전" ? `<span class="btn" data-act="op-runs" data-rel="${rel.id}" id="vb-op-runs-past" title="이 버전이 운영이던 때의 운영 Run · 사람 체크">운영 Run 보기(보기만)</span>` : "") +
    (v.state === "운영" && rb && !rb.failed ? `<span class="btn" data-act="rb-open" data-rel="${rel.id}" id="vb-rollback-steps">되돌리기 단계</span>` : "") +
    (v.state === "운영" && v.rollback_target && !(rb && !rb.failed) ? `<span class="btn" data-act="rb-open" data-rel="${rel.id}" id="vb-rollback">${esc(v.rollback_target.name)}로 되돌리기</span>` : "") +
    (v.state === "운영" && v.rollback_elsewhere ? `<span class="small muted" id="vb-rollback-elsewhere">되돌리기는 배포 승인을 받은 Project ${esc(v.rollback_elsewhere)}에서 합니다</span>` : "") +   // EV-034 D-168
    (v.from && v.from.auto && v.auto_rec && v.state === "평가 중" ? `<span class="btn" data-act="go" data-to="#/p/${p.id}/compare/A-${esc(v.from.auto.wheel)}" id="vb-auto-compare">비교 보기 ›</span>` :   // EV-035 (supplement 10 · 11/26): the comparison the wheel opened
      from && from.id && v.state === "평가 중" ? `<span class="btn" data-act="vb-diff" data-a="${from.id}" data-b="${rel.id}" id="vb-compare">${esc(from.name)}와 비교</span>` : "") +   // D-652 덧 O-4: straight to the code difference
    (v.fixed_flow && p.auto && p.auto.on ? `<span class="link small" data-act="auto-ff-open" data-rel="${rel.id}" data-name="${esc(rel.name)}" data-to="#/p/${p.id}/auto" id="ver-ff" style="margin-left:8px">고정 흐름 ${esc(v.fixed_flow)} ›</span>` : "");   // D-485: any Pack version's fixed-flow window
  // EV-075 BAND (v2 6쪽): a 운영 version reads '운영 중' and its deployment line '<시각>부터', an earlier one '이전 버전 · 보기만' — the whole line on mouse-over
  const plain = bits.filter(b => !String(b).startsWith("<")), marks = bits.filter(b => String(b).startsWith("<"));
  const since = v.deployment && /(\d\d-\d\d \d\d:\d\d)/.exec(String(v.deployment.at || ""));
  const words = v.state === "운영" && v.deployment ? hv(`${since ? since[1] : v.deployment.at}부터`, plain.join(" · "), "vb-since")
    : v.state === "이전" ? hv("이전 버전 · 보기만", plain.join(" · "), "vb-since")
    : ap && from && v.from.at ? hv(`${v.from.at} · ${from.name}에서 확정 · 채택됨`, plain.join(" · "), "vb-since")   // DEPLOY (v2 35쪽)
    : from ? hv(`${v.from.at ? v.from.at + " · " : ""}${from.name}에서 확정`, plain.join(" · "), "vb-since") : plain.join(" · ");   // EV-075 F-05 · C-2: name · state · short words
  return `<div class="vband" id="version-band" data-state="${esc(v.state)}"><b>${esc(rel.name)} · ${esc(v.badge === "운영" ? "운영 중" : v.badge)}</b><span class="muted">${[words, ...marks].filter(Boolean).join(" · ")}</span>${steps}<span class="sp"></span>${btns}</div>`;
}

function versionInsp(p, rel) {
  const v = rel && rel.version;
  if (!v) return "";
  const key = rel.id + "|" + v.revision;
  if (!UI.revs || UI.revs.key !== key) {
    UI.revs = { key, list: null };
    api("GET", `/api/projects/${p.id}/releases/${rel.id}/revisions`).then(d => { if (UI.revs && UI.revs.key === key) { UI.revs.list = d.revisions; render(); } }).catch(() => {});
  }
  const rows = p.releases.filter(r => r.version).map(r => `<tr data-version="${esc(r.name)}"${r.id === rel.id ? ' class="on"' : ""}><td>${esc(r.name)}</td><td>${vbadge(r.version.badge)}</td><td>${revShort(r.version.revision)}</td><td>${esc((r.version.deployment && r.version.deployment.at) || (r.version.from && r.version.from.at) || "")}</td></tr>`).join("");
  const earlier = (UI.revs.list || []).filter(x => !x.current).map(x => {
    const runs = p.runs.filter(r => r.rel === rel.id && r.manifest && r.manifest.pack_revision === x.revision);
    return `<tr data-revision="${esc(x.revision)}"><td>${esc(rel.name)}</td><td class="muted">이전 revision</td><td>${revShort(x.revision)}</td><td><span class="link" data-act="rev-view" data-rev="${esc(x.revision)}">보기</span> · <span class="link" data-act="rev-edit" data-rel="${rel.id}" data-rev="${esc(x.revision)}">편집으로 가져오기</span></td></tr>` +
      (UI.revOpen === x.revision ? `<tr><td colspan="4" class="small muted" data-rev-runs>${runs.length ? runs.map(r => `<span class="link" data-act="open-run" data-run="${r.id}">${esc(r.id)}</span>`).join(" · ") : "이 revision의 Run 없음"}</td></tr>` : "");
  }).join("");
  const ro = v.rollout, dep = v.deployment, rbIn = v.rolled_back, rbOut = v.state === "운영" ? v.rollback : null;
  const recRow = (x, attr, pre) => { const d = x.detail ? stepDetail(x.detail, x.state) : "";   // EV-075 (v2 6쪽 · F-04): the step's plain words, the whole line on mouse-over
    return `<div class="small" ${attr} data-state="${x.state}" title="${esc(`${pre}${x.name}${x.detail ? " · " + x.detail : ""}`)}">${{ done: "✓", active: "…", failed: "✕", wait: "○" }[x.state]} ${esc(pre)}${esc(STEP_NAME[x.name] || x.name)}${d ? " · " + d : ""}</div>`; };
  const record = (ro ? ro.steps.map(x => recRow(x, "data-dep-step", "")).join("")
    : dep ? `<div class="small">✓ ${esc(dep.deployment_id)} · 태그 ${esc(dep.tag)} · ${esc(dep.at)}</div>` : `<div class="small muted">아직 배포 기록 없음</div>`) +
    (v.data_changes || []).map(x => `<div class="small" data-data-change="${esc(x.version)}">✓ 자료 바꿈 · 자동(R12) · 보고서 ${x.reports}건 · ${esc(x.store)} ${esc(x.previous)} → ${esc(x.version)} · ${esc(x.at)}</div>`).join("") +
    (rbIn ? `<div class="small" data-rb-record="${esc(rbIn.rollback_id)}">✓ 되돌리기 ${esc(rbIn.rollback_id)} · ✓ 3단계 · ${esc(rbIn.from_name)} → ${esc(rbIn.target_name)} · ${esc(rbIn.decided_by)} · ${esc(rbIn.at)}</div>` : "") +
    (rbOut ? rbOut.steps.map(x => recRow(x, "data-rb-step", "되돌리기 · ")).join("") : "");
  const opRuns = "";
  return `<h3>Version 이력</h3><div style="overflow-x:auto"><table class="small" id="version-history"><tr><th>Version</th><th>상태</th><th>revision</th><th>시각</th></tr>${rows}${earlier}</table></div>` +
    (UI.revErr ? `<div class="rg err small" id="rev-error">${esc(UI.revErr)}</div>` : "") + kgVersions(kgOf(p, rel)) +
    `<h3 style="margin-top:10px">배포 기록</h3><div id="deploy-record">${record}</div>${opRuns}<hr style="border:0;border-top:1px solid #eee;margin:10px 0">`;
}
/* D-560 (dev-2 · 정답지 32쪽): the deploy side's graph signals (GET …/kg, LLMOPS_KG_SIGNAL_DIR) — a '그래프 버전' table under
   Version 이력 and a band over the page for each approval that did not reach the graph; nothing when there is none */
function kgOf(p, rel) {
  if (!rel || !rel.pack) return null;
  const key = p.id + "|" + rel.id;
  if (!UI.kg || UI.kg.key !== key) {
    UI.kg = { key, data: null };
    api("GET", `/api/projects/${p.id}/releases/${rel.id}/kg`).then(d => { if (UI.kg && UI.kg.key === key) { UI.kg.data = d; render(); } }).catch(() => {});
  }
  return UI.kg.data;
}
const KG_CHECK = [["extract_match", "추출"], ["impact", "간섭"], ["no_break", "유지"]];   // D-663 S2: the words only (keys stay)
const kgMarks = c => c ? KG_CHECK.map(([k, w]) => `${c[k] === true ? "○" : c[k] === false ? "×" : "—"}${w}`).join(" ") : "—";
const kgGate = r => r.no_gate ? "안 돎(예외 없음)" : kgMarks(r.checks);   // D-637 F7: an approval with no new exception went past no gate
const kgAt = at => at ? String(at).slice(5, 16).replace("T", " ") : "—";
const KG_VB = { "운영": "op", "이전": "old", "반영 안 됨": "no" };   // the graph's own states (not a version badge — VB keeps its six)
/* D-581: a row of the table opens what changed from the graph before it (kg/diff · the Pack repository's tags) */
const kgFromTo = x => `${esc(x.name)} ${x.before} → ${x.after}`;
function kgDiffRow(r) {
  const s = UI.kgDiff;
  if (!s || !r.version || s.version !== r.version) return "";
  const d = s.data, rel = (w, xs) => (xs || []).map(x => `<div>${w} ${esc(x.id)} · ${esc(x.where || "")}</div>`).join("");
  const links = d && (d.links.every(x => x.before === x.after) ? `<div>${d.links.map(x => esc(x.name)).join(" · ")} 같음</div>` : d.links.map(x => `<div>${kgFromTo(x)}</div>`).join(""));
  const body = s.err ? esc(s.err) : !d ? "읽는 중" : `<div><b>${esc(d.before)} → ${esc(d.after)}</b></div>${d.counts.map(x => `<div>${kgFromTo(x)}</div>`).join("")}${links}${rel("새 관계", d.new)}${rel("바뀐 관계", d.changed)}`;
  return `<tr data-kg-diff="${esc(r.version)}"><td colspan="4">${body}</td></tr>`;
}
function kgDiffOpen(version) {
  if (UI.kgDiff && UI.kgDiff.version === version) { UI.kgDiff = null; render(); return; }
  const [pid, rid] = (UI.kg && UI.kg.key || "|").split("|");
  UI.kgDiff = { version }; render();
  const put = x => { if (UI.kgDiff && UI.kgDiff.version === version) { UI.kgDiff = { version, ...x }; render(); } };
  api("GET", `/api/projects/${pid}/releases/${rid}/kg/diff?version=${encodeURIComponent(version)}`).then(data => put({ data })).catch(e => put({ err: e.message }));
}
function kgVersions(k) {
  if (!k || !(k.versions || []).length) return "";
  const tags = k.versions.filter(r => r.version).map(r => r.version);
  const rows = k.versions.map(r => { const open = r.version && tags.indexOf(r.version) > 0; return `<tr data-kg-version="${esc(r.version || "")}" data-kg-state="${esc(r.state)}"${open ? ` data-act="kg-diff" data-kg-tag="${esc(r.version)}" style="cursor:pointer" title="누르면 앞 그래프와 차이"` : ""}><td>${open ? `<span class="link">${esc(r.version)}</span>` : esc(r.version || "—")}</td><td><span class="vb" data-vb="${KG_VB[r.state] || "old"}">${esc(r.state)}</span></td><td>${kgGate(r)}</td><td>${kgAt(r.at)}</td></tr>` + kgDiffRow(r); }).join("");
  return `<h3 style="margin-top:10px">그래프 버전</h3><div style="overflow-x:auto"><table class="small" id="kg-versions"><tr><th>버전</th><th>상태</th><th>관문</th><th>시각</th></tr>${rows}</table></div>`;
}
/* D-560 겹침 (정답지 31쪽): the two exceptions side by side · the count · 유지 / 바꿈 / 합침 (합침 = a draft the person trims, then 확정) */
const KG_CHOICE = { keep: "유지", replace: "바꿈", merge: "합침" };
const kgItems = (xs, side, drop) => `<ol style="margin:2px 0 2px 18px;padding:0">${(xs || []).map((c, i) => `<li>${esc(c.what || "")}${c.how ? ` — ${esc(c.how)}` : ""}${drop ? ` <span class="btn" data-act="kg-draft-drop" data-side="${side}" data-i="${i}">빼기</span>` : ""}</li>`).join("")}</ol>`;
const kgSide = (w, s) => `<div style="flex:1;min-width:0;border:1px solid #eee;border-radius:6px;padding:6px" data-kg-side="${w}"><b>${w} · ${esc(s.topic || s.name || s.no || "")}</b>` +
  `<div>점검</div>${kgItems(s.checks)}<div>조치</div>${kgItems(s.actions)}<div class="muted">근거 ${esc((s.records || []).join(" · ") || "—")}</div></div>`;
function kgOverlap(x) {
  const pr = x.overlap[0], d = UI.kgDraft && UI.kgDraft.report === x.report_id ? UI.kgDraft : null;
  const head = `<div style="margin:6px 0 2px"><b>겹침</b> · ${esc(pr.from)} → ${esc(pr.to)} · 과거 알람 ${pr.n}건</div>`;
  const sides = `<div style="display:flex;gap:8px" data-kg-pair="${esc(x.report_id)}">${kgSide("기존", pr.from_section || {})}${kgSide("새", pr.to_section || {})}</div>`;
  const done = x.choice ? `<div data-kg-choice="${esc(x.choice.choice)}">${esc([KG_CHOICE[x.choice.choice] || x.choice.choice, x.choice.file_id].filter(Boolean).join(" · "))}</div>` : "";
  const draft = d ? `<div style="border:1px solid #eee;border-radius:6px;padding:6px;margin-top:4px" id="kg-draft"><b>합침 초안${d.fake ? "(가짜)" : ""}</b><div>점검</div>${kgItems(d.checks, "checks", true)}` +
    `<div>조치</div>${kgItems(d.actions, "actions", true)}<span class="btn" data-act="kg-merge-ok" data-kg-report="${esc(x.report_id)}">확정</span> <span class="btn" data-act="kg-merge-cancel">취소</span></div>` : "";
  const btns = x.choice || d ? "" : `<div style="margin-top:4px">${["keep", "replace", "merge"].map(c => `<span class="btn" data-act="kg-${c}" data-kg-report="${esc(x.report_id)}">${KG_CHOICE[c]}</span>`).join(" ")}</div>`;
  return head + sides + done + btns + draft;
}
function kgChoose(report, choice, extra) {
  const [pid, rid] = (UI.kg && UI.kg.key || "|").split("|");
  return api("POST", `/api/projects/${pid}/releases/${rid}/kg/overlap`, { report_id: report, choice, ...(extra || {}) })
    .then(() => { UI.kgDraft = null; UI.kgErr = null; UI.kg = null; render(); }).catch(e => { UI.kgErr = e.message; render(); });
}
function kgBand(k) {
  const held = (k && k.held) || [];
  const start = ((k && k.checking) || []).map(c => `<div class="rg small" id="kg-start"><b>새 예외 → 관문 시험 시작</b> · ${esc([c.report_id, c.graph ? `그래프 ${c.graph}` : "", kgAt(c.at)].filter(Boolean).join(" · "))}</div>`).join("");
  const said = h => h.report_id === k.last.report_id && h.state === "반영 안 됨";   // the band below already says it
  const last = !start && k && k.last && !held.some(said) && k.last;   // D-579: the same place after the gate, until the next new exception
  const stay = last ? `<div class="rg small" id="kg-last"><b>${last.graph && last.no_gate ? `새 예외 없음 → 이력만 더함 → 새 그래프 ${esc(last.graph)}` : `새 예외 → ${last.graph ? `관문 통과 → 새 그래프 ${esc(last.graph)}` : "KG 반영 안 됨"}`}</b> · ${esc([last.report_id, kgAt(last.at)].filter(Boolean).join(" · "))}</div>` : "";
  if (!held.length) return start + stay;
  const n = w => held.filter(h => h.state === w).length;
  const title = [["반영 안 됨", "KG 반영 안 됨"], ["멈춤", "멈춤"]].filter(([w]) => n(w)).map(([w, t]) => `${t} ${n(w)}건`).join(" · ");
  const h = held[held.length - 1];
  const line = [h.report_id, `${h.fixes ? `고침 ${h.fixes}번 뒤에도 ` : ""}${h.reason_word}`, h.graph ? `그래프는 ${h.graph} 그대로` : ""].filter(Boolean).join(" · ");
  const open = UI.kgOpen ? held.map(x => `<div class="small" data-kg-held="${esc(x.report_id)}">${[x.report_id, x.state, x.graph ? `지금 그래프 ${x.graph}` : "",
    `시험 차례 ${x.rounds ? `${x.rounds}번(처음 1 + 고침 ${x.fixes})` : "0"}`, x.reason_word, `관문 ${kgMarks(x.checks)}`, kgAt(x.at)].filter(Boolean).map(esc).join(" · ")}</div>` +
    (x.overlap ? kgOverlap(x) : "")).join("") + (UI.kgErr ? `<div class="small" id="kg-err">${esc(UI.kgErr)}</div>` : "") : "";
  return start + stay + `<div class="rg err small" id="kg-band"><div style="display:flex;gap:10px;align-items:center"><b>${esc(title)}</b><span>${esc(line)}</span><span style="flex:1"></span>` +
    `<span class="btn" data-act="kg-open">${UI.kgOpen ? "닫기" : "보기"}</span></div>${open ? `<div style="margin-top:6px">${open}</div>` : ""}</div>`;
}
/* D-633 (줄인 안): the schema places the signals already carry (GET …/kg 'schema' — a held 나누지 못함 · the gate's hold or a relation it
   turned off) — one band under the KG band, a line per case saying what is missing; nothing when there is none */
function kgSchema(k) {
  const xs = (k && k.schema) || [];
  if (!xs.length) return "";
  return `<div class="rg small" id="kg-schema"><b>스키마 조정 필요 의심 ${xs.length}건</b>` +
    xs.map(x => `<div data-kg-schema="${esc(x.report_id)}">${esc([x.report_id, x.place, x.lack].filter(Boolean).join(" · "))}</div>`).join("") + `</div>`;
}
function projectInsp(p) {
  return `<h3>평가 기준</h3><div class="small" id="pi-data">${p.data ? `검증데이터 ${esc(p.data.name)} · ${p.data.cases.length} case` : `검증데이터 없음 · <span class="link" data-act="go" data-to="#/p/${p.id}/inputs?seg=data">검증데이터 올리기</span>`}</div>` +
    // EV-075 (v2 6쪽): the scoring as short words (whole on mouse-over); empty or count-only sections are left out
    `<div class="small" id="pi-scoring">채점 ${scoringLabel(p.scoring)}</div>` +   // kept also for 채점 생략 (AC-01-C2 label — a result word)
    (p.scoring.llm ? (L => `<div class="small" id="pi-llm">${hv("LLM 채점", `LLM 채점 · ${L.name} · ${L.model}`)}</div>`)(projLlm(p.scoring)) : "") +
    `<div class="small" id="pi-rule">${hv(`통과 기준 ${Math.round(p.rule.pass * 100)}%`, ruleText(p))}</div>` +
    (inputsOf(p).length ? "" : `<h3 style="margin-top:10px">실행 입력</h3><div class="small">실행 입력이 없습니다 · <span class="link" data-act="go" data-to="#/p/${p.id}/inputs">실행 입력 탭</span></div>`) +
    (p.briefs.length ? `<h3 style="margin-top:10px">요청서</h3><div class="small">${p.briefs.map(b => `<span class="link" data-act="go" data-to="#/p/${p.id}/briefs/${b.id}">${esc(b.title)}</span> · ${b.at}`).join("<br>")}</div>` : "") +
    (p.activity.length ? `<h3 style="margin-top:10px">활동</h3><div class="small muted" id="activity">${p.activity.slice(0, 8).map(a => a.run ? `<div class="link" data-act="open-run" data-run="${a.run}">${a.at} ${esc(a.text)}</div>` : `<div>${a.at} ${esc(a.text)}</div>`).join("")}</div>` : "") +
    `<div style="margin-top:12px"><span class="btn d" data-act="proj-del" id="proj-del">Project 삭제</span></div>`;
}
function pageProject(p, relId) {
  if (!p.releases.length) {
    const center = `<div class="rg band" data-region="R-04" id="no-release"><b>release를 등록하면 그래프가 보입니다</b><div class="helpcards" style="margin-top:8px">` +
      `<div class="helpcard" data-act="reg-start" data-how="local"><b>로컬 폴더 경로</b><br>내 컴퓨터의 LangGraph 코드</div><div class="helpcard" data-act="reg-start" data-how="upload"><b>파일·폴더 업로드</b><br>복사본을 보관</div><div class="helpcard" data-act="reg-start" data-how="builtin"><b>내장 예시</b><br><span class="btn">예시 불러오기</span></div></div></div>`;
    return shell(p, "", crumbP(p), workbench(p, "new", center, [["project", "Project"]], "project", projectInsp(p)));
  }
  const rel = relOf(p, relId) || p.releases[0];
  const run = UI.itab === "verdict" ? judgeRun(p, rel) : shownRun(p, rel);   // EV-064 CHG-CF-4: the 판정 tab's screen is the judged Run
  const pk = rel.pack;
  const editHere = !!(p.edit && p.edit.release === rel.id);
  // EV-022 manual p.9 · 10 · 21 · 22: while this Agent Pack release is being edited the right tabs are Project · 변경 · Run · 판정 —
  // 변경 holds the edit's change list (web/popover.js) where 실행 was (an edit is not run, p.9)
  const editTabs = !!(pk && editHere);
  const itab = editTabs && UI.itab === "exec" ? "change" : !editTabs && UI.itab === "change" ? "exec" : UI.itab;
  // EV-075 F-05: the version line reads the version name · its state · runtime; the whole (Pack · revision · 실행 설정) on mouse-over
  const identWhole = pk ? `Agent Pack · ${pk.id} · ${rel.name}${editHere ? ` 편집 중 · 변경 ${p.edit.lines}개 · 출발 ${rel.name} ${pk.revision.slice(7, 15)}` : `${rel.version ? " " + rel.version.badge : ""} · revision ${pk.revision.slice(7, 15)} · 실행 설정 ${(pk.config_sha256 || "").slice(0, 4)}`}${pk.runtime ? ` · runtime ${pk.runtime.version}` : ""}` : "";
  let hl = {}, note = pk ? `<span id="pack-ident" class="hv" title="${esc(identWhole)}">${esc(`${rel.name}${editHere ? " 편집 중" : rel.version ? " " + rel.version.badge : ""}${pk.runtime ? ` · runtime ${pk.runtime.version}` : ""}`)}</span>`
    : `${esc(rel.name)} · ${esc(rel.how)}${rel.how === "로컬 경로" ? " " + esc(rel.path) : ""} · 코드 ${rel.code} · 그래프 ${rel.graph.id}`;
  const packNote = pk ? note : null;   // H3 (manual p.5 · 9 · 23 · 26 · 27): the Agent Pack line always shows; a Run's note goes under it
  if (run && !(pk && editHere) && (itab === "run" || itab === "verdict" || itab === "project")) {   // H4: an edit has no Run colours (p.9)
    const ci = Math.min(UI.caseIdx, run.cases.length - 1);
    if (run.status === "실행 중") { const lc = run.cases[run.cur.ci] ? run.cur.ci : run.cases.length - 1; hl = highlightFor(run, lc); note = `실행 입력: ${esc(run.input.name)} · case ${lc + 1} 실행 중`; }
    else if (run.cases[ci] && run.cases[ci].sim) { hl = highlightFor(run, ci); note = `보이는 길: 평가 ${run.id}의 ${ci + 1}번${UI.node ? ` · 선택 node ${UI.node}` : ""}`; }
  }
  hl.sel = UI.node;
  // D-689 (llmops-2 343): the note is for a Run in fake mode only — a real-mode Run said it too
  const fake = run && run.status === "실행 중" && run.modeKey === "fake" ? `<div class="note" id="fake-note">가짜 provider로 실행 중입니다. 호출 주소를 따르지 않는 코드는 실제 모델을 부를 수 있습니다.</div>` : "";
  const wc = pk && UI.workChanged[rel.id], same = typeof wc === "string" ? relOf(p, (p.releases.find(x => x.name === wc) || {}).id) : null;
  const sameTag = same && same.version && same.version.deployment && same.version.deployment.tag;
  // answer 019-A merge 3 §5: the folder now holds a later version of this Project — no 'register again' (that would make a copy of it)
  const notices = (wc ? `<div class="note" id="pack-work-changed">${same ? hv(`작업 폴더 = ${same.name}${sameTag ? `(태그 ${sameTag})` : ""}`, `작업 폴더는 이후 버전 ${same.name}${sameTag ? `(태그 ${sameTag})` : ""}과 같습니다 — 이 버전(${rel.name})은 등록 때 사본으로 보고 · 실행합니다`) : "작업 폴더 바뀜 · 새 버전으로 등록 필요"}</div>` : "") +
    (!pk && rel.changed_since_run ? `<div class="note" id="code-changed">마지막 Run 이후 코드가 바뀜</div>` : "") +
    (rel.readError ? `<div class="rg err small" id="reread-error"><b>코드를 다시 읽지 못함</b> — ${esc(rel.readError)} · 마지막으로 읽은 그래프를 보여 줍니다</div>` : "") +
    (rel.graph.mismatch.length ? `<div class="note" id="graph-mismatch">선언 그래프와 실행 그래프가 다릅니다 — ${rel.graph.mismatch.map(esc).join(" · ")}</div>` : "");
  const cx = edCtx(p, rel);   // EV-022: the canvas (web/canvas.js) and popover (web/popover.js) hooks
  if (UI.opsRun && pk && rel.version && ["운영", "이전"].includes(rel.version.state) && !editHere) return pageOpsRun(p, rel, cx, itab, notices, packNote);   // EV-025 p.30
  const center = fake + notices + (Popover.editBand(cx) || versionBand(p, rel)) + (k => kgBand(k) + kgSchema(k))(kgOf(p, rel)) + Canvas.decorate(graphSVG(branchNamed(Canvas.layout(rel.graph, cx), rel.branchLabels), hl), cx) + graphLegend(rel.graph) + Canvas.editHint(cx) + `<div class="small muted" id="canvas-note">${packNote || note}</div>` + (packNote && note !== packNote ? `<div class="small muted" id="run-note">${note}</div>` : "") + Popover.html(cx);
  let insp = "";
  if (itab === "project") insp = versionInsp(p, rel) + projectInsp(p);
  if (itab === "exec") insp = execInsp(p, rel);
  if (itab === "change") insp = Popover.changeList(cx);
  if (itab === "run") insp = runInsp(p, rel, run);
  if (itab === "verdict") insp = verdictInsp(p, run, rel);
  const editingHere = !!(p.edit && p.edit.release === rel.id);   // EV-022 manual p.9: an edit has no Run — 실행 ▶ · 확정한 버전에서
  const runBtn = editingHere ? `<span class="btn p off" id="run-btn" data-editing="1" title="실행 ▶ · 확정한 버전에서 — 편집 중인 버전은 실행하지 않습니다">실행 ▶</span>`
    : `<span class="btn p${inputsOf(p).length ? "" : " off"}" data-act="run" id="run-btn">실행 ▶</span>`;
  return shell(p, "", crumbP(p, `<b>${esc(rel.name)}</b>${run && !["exec", "change", "project"].includes(itab) ? " › " + run.id : ""}`), workbench(p, rel.id, center, editTabs ? RTABS_EDIT : RTABS, itab, insp, runBtn));
}
/* EV-025 (manual p.30, CHG-122): the 운영 Run screen of the 운영 version — the Run 이력 table over the graph, the chosen
   operational Run's node flow on it, and the chosen Run in the Run tab. No popover here (Q-3: the chosen node's input and
   output come under the graph, OPS-2); every other screen keeps its popover. */
function pageOpsRun(p, rel, cx, itab, notices, packNote) {
  OpsRun.load(cx, rel);
  const hl = OpsRun.highlight(cx);
  hl.sel = UI.node;
  // OPS-2 (p.30 ②): the chosen node's box beside the graph, its input · output under it
  const graph = Canvas.decorate(graphSVG(branchNamed(Canvas.layout(rel.graph, cx), rel.branchLabels), hl), cx), box = OpsRun.nodeBox(cx, rel);
  const center = notices + versionBand(p, rel) + OpsRun.table(cx, rel) + OpsRun.runNote(cx, rel) +
    (box ? `<div class="row" style="align-items:flex-start;gap:10px"><div style="flex:1;min-width:0;overflow-x:auto">${graph}</div><div style="width:260px;flex:none">${box}</div></div>` : graph) +
    OpsRun.tabs(cx, rel) + graphLegend(rel.graph) + `<div class="small muted" id="canvas-note">${packNote}</div>`;
  const insp = itab === "run" ? OpsRun.insp(cx, rel) : itab === "project" ? versionInsp(p, rel) + projectInsp(p) : itab === "exec" ? execInsp(p, rel) : verdictInsp(p, null);
  const runBtn = `<span class="btn p${inputsOf(p).length ? "" : " off"}" data-act="run" id="run-btn">실행 ▶</span>`;
  return shell(p, "", crumbP(p, `<b>${esc(rel.name)} (${esc(rel.version.state)})</b> › Run`), workbench(p, rel.id, center, RTABS, itab, insp, runBtn));
}
/* EV-022: what the canvas and popover files get from the page (plan v5: they read state only through this and the API). */
function edCtx(p, rel) { return { p, rel, ui: UI, api, esc, render, go, refresh }; }
/* EV-022 E-S-02 (CHG-109, manual p.21 · 22 · 43): the change view of the open edit and the confirm window. The edit itself is
   made in the node popover (web/popover.js) and on the canvas (web/canvas.js); this page and window only read it and confirm. */
function editDiffLoad(p) {
  const key = [p.id, p.edit && p.edit.edit_id, p.edit && p.edit.revision, (p.edit && p.edit.lines) || 0].join("|");   // D-551 덧 1 ②: the edit itself, not only its line count
  if (UI.ediff && UI.ediff.key === key) return UI.ediff;
  UI.ediff = { key, data: null, err: null };
  api("GET", `/api/projects/${p.id}/edit/diff`).then(d => { if (UI.ediff && UI.ediff.key === key) { UI.ediff.data = d; render(); } })
    .catch(e => { if (UI.ediff && UI.ediff.key === key) { UI.ediff.err = e.message; render(); } });
  return UI.ediff;
}
/* why confirming is locked: the check's problems and each node still waiting for its next connection (manual p.14 · 22, 31 #5) */
function confirmReasons(e) { return [...((e && e.problems) || []), ...(((e && e.pending) || []).map(n => `node '${n}'에 다음 연결이 필요합니다`))]; }
function pageEditDiff(p, rid) {
  const rel = relOf(p, rid);
  if (!rel || !rel.pack) { go(`#/p/${p.id}`); return ""; }
  const crumb = crumbP(p, `<span data-act="go" data-to="#/p/${p.id}/r/${rid}">${esc(rel.name)}</span> › 편집 › <b>변경 내용</b>`);
  const back = `<span class="btn" data-act="go" data-to="#/p/${p.id}/r/${rid}" id="ed-back">그래프로 돌아가기</span>`;
  if (!p.edit || p.edit.release !== rid) return shell(p, "", crumb, workbench(p, rid, `<div class="empty" id="ed-none"><b>편집이 없습니다</b><div class="row" style="margin-top:6px">${back}</div></div>`, [["diff", "변경 내용"]], "diff", ""));
  const st = editDiffLoad(p), d = st.data;
  let center;
  if (st.err) center = `<div class="rg err small" id="ed-diff-error">${esc(st.err)}</div>`;
  else if (!d) center = `<div class="small muted" data-loading>불러오는 중…</div>`;
  else {
    const short = x => esc((x || "").replace(/^sha256:/, "").slice(0, 8));
    const files = d.files.length ? d.files.map(f => `<tr data-file="${esc(f.path)}"><td>${esc(f.path)}</td><td>${hv(`${f.hunks.length}곳 · ${f.lines}줄`, `${f.hunks.map(h => `${h.old_start}행`).join(" · ")} ${f.lines}줄`)}</td><td>${esc(d.nodes.join(" · ") || "—")}</td><td>${esc(f.path === "pack.yaml" ? (d.lines.map(l => `${l.kind === "value" ? (l.label || l.summary.split(" · ")[0]) : l.summary} ${l.kind === "value" ? "수정" : l.sign === "−" ? "지움" : "더함"}`).join(" · ") || "—") : "파일")}</td></tr>`).join("")
      : `<tr><td colspan="4" class="muted">바뀐 파일 없음</td></tr>`;
    const others = d.files.some(f => f.path !== "pack.yaml") ? "" : `<tr class="muted"><td>${hv("그 밖의 파일", "prompts/ · templates/ · schemas/ · custom/")}</td><td>없음</td><td>—</td><td>—</td></tr>`;
    const ctx = (x, n) => `<span class="nd-ctx">${n}    ${esc(x)}</span>`;
    const hunks = d.files.map(f => `<div class="ed-file" data-hunks="${esc(f.path)}">` + f.hunks.map(h =>
      `<div class="ed-hunk" data-hunk-id="${esc(h.id || "")}"><span class="muted ed-hhead">${esc(f.path)} @@ ${h.ctx_start || h.old_start},${h.ctx_count || h.old.length} @@${h.id ? ` — id: ${esc(h.id)}` : ""}</span>${(h.before || []).map((x, i) => ctx(x, h.ctx_start + i)).join("")}${h.old.map((x, i) => `<span class="nd-del">${h.old_start + i}  - ${esc(x)}</span>`).join("")}${h.new.map((x, i) => `<span class="nd-add">${h.new_start + i}  + ${esc(x)}</span>`).join("")}${(h.after || []).map((x, i) => ctx(x, h.old_start + h.old.length + i)).join("")}</div>`).join("") + `</div>`).join("");
    const pd = d.pack_diff || [], pdOpen = UI.edPackDiff;
    const pdHtml = pdOpen ? `<div id="ed-pack-diff" style="overflow-x:auto;margin-top:6px">${pd.length ? `<table class="small" style="white-space:nowrap;min-width:max-content">${pd.map(c => `<tr data-diff-path="${esc(c.path)}"><td>${esc(c.path)}</td><td>${esc(c.change)}</td><td>${c.before !== undefined && c.after !== undefined ? `${esc(c.before)} → ${esc(c.after)}` : esc(c.after ?? c.before ?? "")}</td></tr>`).join("")}</table>` : `<span class="small muted">차이 없음</span>`}</div>` : "";
    const why = confirmReasons(p.edit);
    const probs = why.length ? `<div class="rg err small" id="ed-problems"><b>확정할 수 없습니다 — 고칠 것</b><ul>${why.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "";
    const ir = (d.ir_diff && d.ir_diff.graphs) || {}, g = Object.values(ir)[0] || {};
    const structure = (g.added_nodes || []).length || (g.removed_nodes || []).length || (g.edges_added || []).length || (g.edges_removed || []).length
      ? `구조 변화 — node ＋${(g.added_nodes || []).length} −${(g.removed_nodes || []).length} · 연결 ＋${(g.edges_added || []).length} −${(g.edges_removed || []).length}` : "구조 변화 없음";
    center = `<div id="edit-diff"><div class="small" id="ed-head"><b>편집의 변경 내용</b> · ${esc(d.base.release)} ${short(d.base.revision)} → 편집본 ${short(d.revision)} · <span class="link" data-act="ed-packdiff" id="ed-packdiff" title="기존 revision 차이 보기(pack-diff)">버전끼리 코드 차이</span></div>${pdHtml}` +
      `<table id="ed-files"><tr><th>파일</th><th>바뀐 줄</th><th>node</th><th>종류</th></tr>${files}${others}</table>${hunks}${probs}` +
      `<div class="small muted" id="ed-structure">node 차이 — ${structure} · 변경 목록 ${d.lines.length}개${d.lines.length ? `(${esc([...new Set(d.lines.map(l => l.node).filter(Boolean))].join(" · "))})` : ""} · ${d.check.ok ? "흐름 검사 통과" : "검사 문제 있음"}</div>` +
      `<div class="row" style="margin-top:8px">${back}<span class="btn p${d.check.ok && !(d.check.pending || []).length ? "" : " off"}" data-act="ed-confirm-open" id="ed-confirm-open">확정…</span></div></div>`;
  }
  // manual p.21 · 22: the edit band stays on top and the right side keeps the 변경 tab with the change list
  const cx = edCtx(p, rel);
  return shell(p, "", crumb, workbench(p, rid, (Popover.editBand(cx) || "") + center, RTABS_EDIT, "change", Popover.changeList(cx), `<span class="btn p off" id="run-btn" data-editing="1" title="실행 ▶ · 확정한 버전에서 — 편집 중인 버전은 실행하지 않습니다">실행 ▶</span>`));
}
/* the confirm window: 취소 / 현재 버전 수정 / 새 버전 생성 (p.22 · 43). '현재 버전 수정' is locked with the reason for a deployed
   version or one being deployed (S-3 · S-6); the whole window is locked while the edit has problems (#17). */
function confirmModal(p) {
  const e = p.edit, rel = e && relOf(p, e.release);
  if (!UI.confirm || !e || !rel) return "";
  const c = UI.confirmState || (UI.confirmState = { memo: "", busy: false, err: null, wheel: e.wheel_default ?? null });   // D-444 #10: the default record
  const v = rel.version || {}, problems = confirmReasons(e), locked = !!v.lock_reason;
  const bad = !e.ok || problems.length;
  const d = (editDiffLoad(p) || {}).data;   // the change lines and the edited revision (the same read as the change view)
  const next = e.next_name || "새 버전", rev = d ? esc((d.revision || "").replace(/^sha256:/, "").slice(0, 8)) : "";
  const ir = d && d.ir_diff && d.ir_diff.graphs ? Object.values(d.ir_diff.graphs)[0] || {} : {};
  const structural = ["added_nodes", "removed_nodes", "edges_added", "edges_removed"].some(k => (ir[k] || []).length);
  // EV-075 R5 (v2 15쪽): the node by its role words, its id and the whole line on mouse-over
  const what = d ? `${d.lines.map(l => hv(`${l.node ? roleWords(rel, l.node) + " " : ""}${l.summary}`, `${l.node ? l.node + " " : ""}${l.summary}`)).join(" · ") || "—"}${structural ? " · 구조 변경 있음" : ""}` : "변경 줄을 읽는 중…";
  // the version's latest evaluation and verdict at its revision (p.43: they stay as the earlier revision's record)
  const judged = p.runs.filter(r => r.rel === rel.id && (r.verdicts || []).length).pop(), lastV = judged && judged.verdicts[judged.verdicts.length - 1];
  const past = judged ? `지난 평가 ${esc(judged.id)} · 판정(${esc(lastV.v)})은 '이전 revision 기록'으로 남음` : "지난 평가 · 판정이 있으면 '이전 revision 기록'으로 남음";
  const ok = (good, label, desc, right, id) => `<div class="cf-item" id="${id}"><span class="cf-mark${good === true ? " ok" : good === false ? " bad" : ""}">${good === true ? "✓" : good === false ? "✕" : "i"}</span><div><b>${label}</b><div class="small muted">${desc}</div></div><span class="cf-right">${right}</span></div>`;
  const why = bad ? `<ul id="cf-reasons">${(problems.length ? problems : ["검사를 통과하지 않았습니다"]).map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  const items = ok(true, `변경 ${e.lines}줄`, what, "확인", "cf-lines") +
    ok(!bad, "검사", bad ? `문제 ${problems.length || 1}개 — 아래 이유를 고치면 확정할 수 있습니다` : hv("문제 없음", "0~2단계 + 흐름 검사 통과 · 문제 0"), bad ? "문제 있음" : "통과", "cf-check") +
    ok(!bad, "코드로 못 바꾸는 항목", bad ? `${problems.length || 1}개 — 있으면 확정 잠김` : hv("없음", "없음(있으면 확정 잠김 — 예: 종류 미정 node · 검사 문제)"), `${bad ? problems.length || 1 : 0}개`, "cf-blocked") + why +
    (locked
      ? ok(null, "저장 방법", hv(`${v.lock_reason === "운영(배포) 버전" || /운영/.test(v.lock_reason || "") ? "운영 버전이라 " : ""}새 버전(${next})으로만 저장`, `현재 버전 수정 — ${rel.name}는 ${v.lock_reason}이라 선택 불가 · 새 버전 생성 — ${next}${rev ? `(revision ${rev})` : ""}이 생기고 이때 ${next} 탭이 열림`), "", "cf-howto")
      : ok(null, "현재 버전 수정", `${esc(rel.name)}의 revision이 바뀜 · ${past} · ${esc(rel.name)}은 다시 평가 중 (승인됨이었다면 승인 풀림)`, `→ ${esc(rel.name)}`, "cf-howto") +
        ok(null, "새 버전 생성", `${esc(rel.name)}은 ${esc(v.badge || "그대로")} 그대로 남고 ${esc(next)}(평가 중)가 새로 생김${rev ? ` · revision ${rev}` : ""} — ${esc(rel.name)}의 평가 · 판정 기록 그대로`, `→ ${esc(next)}`, "cf-howto-new"));
  const head = locked ? ""
    : `${esc(rel.name)}은 배포 전 버전(${esc(v.badge || "평가 중")}) — "현재 버전 수정"과 "새 버전 생성" 둘 다 누를 수 있습니다`;
  const notes = "";   // EV-075 (v2 16쪽): how-to notes left out — the buttons and the reasons list say it
  return `<div class="scrim" id="confirm-window"><div class="modal" style="max-width:min(600px, calc(100vw - 32px))"><div class="mh"><h3>${esc(rel.name)} 편집 확정</h3></div><div class="mb">` +
    `<div class="small" id="cf-head">${head}</div><div class="cf-items">${items}</div>` +
    `<div class="row" style="margin-top:8px"><label class="small muted" for="cf-memo">메모</label><input type="text" id="cf-memo" value="${esc(c.memo)}" style="flex:1;min-width:0"></div>` +
    // D-444 #10 (E3): the name and the choice only — no sentence
    ((e.wheels || []).length ? `<div class="row" style="margin-top:8px"><label class="small muted" for="cf-wheel">개선 기록에 묶기</label><select id="cf-wheel">` +
      e.wheels.map(w => `<option value="${w.seq}"${c.wheel === w.seq ? " selected" : ""}>${esc(w.name)}</option>`).join("") +
      `<option value=""${c.wheel == null ? " selected" : ""}>묶지 않음</option></select></div>` : "") +
    (c.err ? `<div class="rg err small" id="cf-error">${esc(c.err)}</div>` : "") +
    `<div class="row" style="margin-top:10px;justify-content:flex-end"><span class="btn" data-act="cf-cancel" id="cf-cancel">취소</span>` +
    `<span class="btn${locked || bad || c.busy ? " off" : " p"}" data-act="cf-update" id="cf-update" title="${esc(v.lock_reason || "")}">현재 버전 수정${locked ? " 🔒" : ""}</span>` +
    `<span class="btn p${bad || c.busy ? " off" : ""}" data-act="cf-new" id="cf-new">새 버전 생성</span></div>${notes}</div></div></div>`;
}
/* 배포 승인 (manual p.28, CHG-113): the form on an approved version; after it, the same window shows the four steps read from the
   two sessions' status files (S-4: the sessions do steps 2-4, the screen shows them) */
/* EV-075 STEPS (v2 24 · 36쪽 · 수정본 F-04): the deploy / rollback steps in plain words — the name, and the detail as its result;
   the deploy side's own words on mouse-over. Notes that only name the machinery (schema · commit · drs) are left out of the row and
   kept on the step name's mouse-over. */
const STEP_NAME = { "요청 파일": "승인", "Pack 받아 넣기 · 태그": "새 버전 저장", "배포단 등록": "서버 등록", "운영 전환": "운영 시작", "되돌리기 요청": "되돌리기 승인", "배포단 등록 확인": "서버 확인" };
function stepDetail(text, state) {
  const t = String(text || ""), done = state === "done";
  const rules = [[/^태그 (v\d+) · 커밋 [0-9a-f]+$/, m => `태그 ${m[1]}`], [/^배포단 등록 \S+$/, () => "등록됨"],
    [/^배포단이 태그 (v\d+)로 요청 처리 시작 — run_\S+$/, m => `${m[1]}로 운영 시작`], [/^결정 deploy_rolled_back/, () => done ? "기록됨" : "결정 기록"],
    [/^VPS 확인 스크립트 rollback/, () => done ? "확인 끝 · 코드 · 데이터 대조" : "코드 · 데이터 대조"],
    [/^배포단이 .*처리 시작 · 옛 서비스 내림$/, () => done ? "되돌린 버전으로 운영" : "되돌린 버전으로 운영 전환"]];
  for (const [re, f] of rules) { const m = re.exec(t); if (m) return hv(f(m), t); }
  if (/^approved-change@\d · schema 검사$|^\S+-pack · dev 커밋 \+ 태그|^\S+@\S+ · revision [0-9a-f]+ · drs( [0-9a-f]+)? 대조$|^배포단이 Pack 태그\(tagged|^결정을 저장했습니다$|^요청 파일을 만들었습니다$/.test(t)) return "";
  return esc(t);
}
const stepRowHtml = (x, i, note, d = stepDetail(x.detail || note, x.state)) => `<div class="dep-row" data-step="${i + 1}" data-state="${x.state}"><span class="dep-mark">${{ done: "✓", active: "…", failed: "✕", wait: i + 1 }[x.state]}</span><div><b title="${esc(x.name)}${d === "" && (x.detail || note) ? " · " + esc(x.detail || note) : ""}">${esc(STEP_NAME[x.name] || x.name)}</b><div class="small muted">${d}</div></div><span class="dep-say">${{ done: "완료", active: "진행 중", failed: "실패", wait: "대기" }[x.state]}</span></div>`;
function deployModal(p) {
  const rel = relOf(p, UI.deploy && UI.deploy.rel), v = rel && rel.version;
  if (!rel || !v) return "";
  const st = UI.deploy, ro = v.rollout, op = p.releases.find(r => r.version && r.version.state === "운영");
  // EV-039 CHG-197 (D-198, approved-change@2): the Pack's apply tool takes the next free v<n> — the tag is the Pack status file's (a
  // developer change: the one it already has), never one made from the version name; before it is tagged the steps say so
  const tag = (ro && ro.pack && ro.pack.tag) || (v.deploy_target && v.deploy_target.pack_change && v.deploy_target.pack_change.tag) || null;
  const tg = v.deploy_target, drs = tg && tg.deployment_id === st.id && tg.data_release_id ? ` ${String(tg.data_release_id).replace(/^drs:/, "").slice(0, 8)}` : "";
  const stepNote = [tg && tg.pack_change ? `개발자 변경 — 요청 파일은 Pack 쪽${tg.pack_change.id ? `(${tg.pack_change.id})` : ""}` : `approved-change@2 · schema 검사`, `${rel.pack.id}-pack · dev 커밋 + ${tag ? `태그 ${tag}` : "태그(Pack이 다음 빈 v<n>을 정함)"}`, `${(ro && ro.deployment_id) || st.id} · revision ${revShort(v.revision)} · drs${drs} 대조`, tag ? `배포단이 태그 ${tag}로 요청 처리 시작` : "배포단이 Pack 태그(tagged 뒤 정해짐)로 요청 처리 시작"];
  const noBase = !ro && tg && tg.deployment_id === st.id && !tg.data_release_id;
  // EV-051 CHG-W2-25: a vector DB this version changed — the store, its version and embedding mode, and the ledger bundle it makes
  const ix = !ro && ((tg && (tg.index_changes || tg.index_note)) ? tg : (v.await && (v.await.index_changes || v.await.index_note)) ? v.await : null);
  const ixLine = !ix ? "" : ix.index_for !== st.id ? `<div class="note small" id="dep-index-change">벡터 DB 바뀜 확인 — 배포 id가 기본과 달라 승인할 때 다시 셈</div>`   // values are the default id's
    : ix.index_note ? `<div class="rg err small" id="dep-index-change">${esc(ix.index_note)}</div>`
    : `<div class="note small" id="dep-index-change">${ix.index_changes.map(c => `벡터 DB ${esc(c.store)} 바뀜(${esc(c.release_id)}, 임베딩 ${esc(c.embedding_mode || "모름")})`).join(" · ")} → ` +
      `장부 자료 묶음 ${esc(String(ix.index_drs).replace(/^drs:/, "").slice(0, 8))} · 배포단은 운영 임베딩과 같을 때만 전환</div>`;
  const stepList = (ro ? ro.steps : ["요청 파일", "Pack 받아 넣기 · 태그", "배포단 등록", "운영 전환"].map(name => ({ name, state: "wait", detail: "" })))
    .map((x, i) => stepRowHtml(x, i, stepNote[i])).join("");
  const body = ro
    ? `<div class="small">${hv(`${rel.name} · ${ro.decided_by} · ${ro.at}`, `${rel.name} · ${ro.deployment_id} · ${ro.decided_by} · ${ro.at} · 이유: ${ro.reason}`)}</div><div class="dep-list" id="dep-steps">${stepList}</div>` +
      (ro.failed ? `<div class="rg err small" id="dep-failed">배포 실패 — ${esc(ro.fail_reason || ro.reason || "")} · '현재 버전 수정'은 잠긴 채입니다(S-6)</div>` : "") +
      (ro.ledger_note ? `<div class="note small" id="dep-ledger-note">${esc(ro.ledger_note)}</div>` : "") +
      (ro.source === "developer_change" ? `<div class="small muted" id="dep-request">${hv("요청 파일 없음 — Pack 쪽 개발자 변경", "단계는 두 세션의 상태 파일을 읽어 보입니다")}</div>` : "") +
      `<div class="row" style="margin-top:10px;justify-content:flex-end"><span class="btn" data-act="dep-cancel" id="dep-close">닫기</span></div>`
    : `<div class="small" id="dep-head">${hv(`${rel.name}이 운영이 되고${op ? `, ${op.name}는 이전 버전으로 남습니다` : "니다"}`, `승인한 바로 그 ${rel.name}(revision ${revShort(v.revision)})이 운영 Version이 됩니다${op ? ` · ${op.name}는 이전 Version으로 남습니다` : ""}`)}</div><div class="small muted">${hv(`배포 중엔 ${rel.name}을 고칠 수 없습니다`, `누르는 순간부터 ${rel.name}의 '현재 버전 수정'이 잠깁니다(배포 진행 중, S-6)`)}</div>` +
      ((pc => pc ? `<div class="${pc.none ? "rg err small" : "small"}" id="dep-pack-change">${pc.none ? esc(pc.none) : `Pack 변경 id <b>${esc(pc.id)}</b>(개발자 변경 · 태그 ${esc(pc.tag || "")})`}</div>` : "")(tg && tg.pack_change)) +   // EV-037 CHG-191
      `<div class="dep-list">${stepList}</div>` +
      `<div class="field"><label for="dep-id" title="배포 id">올릴 서버</label><input id="dep-id" value="${esc(st.id)}"></div>` +
      byField("dep-by", st.by, st.err, "실명 · 연락처 없음") +
      `<div class="field"><label for="dep-reason">이유</label><input id="dep-reason" value="${esc(st.reason)}"></div>` +
      ixLine +
      (noBase ? `<div class="rg err small" id="dep-no-target">${esc(tg.note || "배포 대상의 운영 배포 줄이 없어 배포 기록 줄의 자료 묶음을 정할 수 없습니다 — 같은 배포 대상의 운영 배포가 먼저 있어야 합니다")}</div>` : "") +
      (notBy(st.err).length ? `<div class="rg err small" id="dep-error">${notBy(st.err).map(esc).join("<br>")}</div>` : "") +
      `<div class="row" style="margin-top:10px;justify-content:flex-end"><span class="btn" data-act="dep-cancel" id="dep-cancel">취소</span><span class="btn p${st.busy || noBase ? " off" : ""}" data-act="dep-send" id="dep-send">배포 승인</span></div>`;
  const aw = !ro && v.await;   // EV-035 CHG-186 (supplement A10 · 13/26): hypothesis · change · result, then the four steps as they are
  const sum = aw ? `<div class="small muted" id="dep-auto-sub">${hv("배포 승인 한 번", "사람이 하는 단 한 번 — 아래를 보고 승인합니다. 4단계는 설명서 28쪽 그대로")}</div>` +   // packrt 137 ①: a label, the sentence on mouse-over
    `<div class="vt" id="dep-auto-sum" style="margin:8px 0"><div class="small" data-sum="1"><b>1 가설</b> ${aw.hypotheses.map(esc).join(" · ")}</div>` +
    `<div class="small" data-sum="2"><b>2 변경</b> ${((v.from && v.from.changes) || []).map(esc).join(" · ")} (${((v.from && v.from.changes) || []).length}줄)</div>` +
    `<div class="small" data-sum="3"><b>3 결과</b> ${aw.reasons.filter(x => !(aw.hidden_none && /숨긴 case/.test(x))).map(esc).join(" · ")}${aw.hidden_none ? ` · <span class="err-text">숨긴 case 검증 없음</span>` : ""}</div></div>` +
    `<div class="small muted" id="dep-auto-note">${hv(`${VERDICT_WORD.adopt} 판정도 함께 남음`, `배포 승인을 누르면 같은 별명 · 이유로 ${rel.name}의 ${VERDICT_WORD.adopt} 판정(평가 ${aw.label || ""})도 함께 남습니다 — 둘 중 하나가 거부되면 아무것도 남지 않습니다`)}</div>` : "";
  const title = ro ? `${esc(rel.name)} 배포 단계` : aw ? `${esc(rel.name)}(✦ 자동)를 운영으로 배포할까요?` : `${esc(rel.name)}을 운영으로 배포 승인할까요?`;
  return `<div class="scrim" id="deploy-window"><div class="modal" style="max-width:min(560px, calc(100vw - 32px))"><div class="mh"><h3>${title}</h3></div><div class="mb">${sum}${body}</div></div></div>`;
}
/* EV-SUI-01 CHG-S02 (D-140): the rollback window — the deploy approval window's frame (#deploy-window), three steps and the one
   line the operator runs on the VPS. The decision is LLMOps' only write; the steps follow the deploy side's status file. */
function rollbackModal(p) {
  const rel = relOf(p, UI.rollback && UI.rollback.rel), v = rel && rel.version;
  if (!rel || !v) return "";
  const st = UI.rollback, tg = v.rollback_target;
  // the rollback this window follows: the one just sent (kept by id, also after it switched and left the 운영 version's view), or
  // the 운영 version's own one in progress / failed; none (a new form) after '다시 되돌리기'
  const mine = st.id ? p.releases.map(r => r.version && (r.version.rollback || r.version.rolled_back)).find(b => b && b.rollback_id === st.id) : null;
  const sent = !mine && st.id && st.sent && st.sent.rollback_id === st.id ? st.sent : null;   // D-652 12-g: until the state read brings it
  const rb = mine || sent || (v.rollback && !(v.rollback.failed && st.again) ? v.rollback : null);
  const tname = rb ? rb.target_name : tg ? tg.name : "";
  // EV-039 CHG-198: the target's tag is its deployment row's tag, not one made from its name (names and Pack tags are not 1:1)
  const ttag = (((relOf(p, rb ? rb.target : tg && tg.release) || {}).version || {}).deployment || {}).tag;
  const note = [`결정 deploy_rolled_back · 되돌리기 id`, `VPS 확인 스크립트 rollback · ${esc(tname)} revision · drs 대조`, `배포단이 ${ttag ? `태그 ${esc(ttag)}` : "대상 버전의 태그"}로 처리 시작 · 옛 서비스 내림`];
  const list = (rb ? rb.steps : ["되돌리기 요청", "배포단 등록 확인", "운영 전환"].map(name => ({ name, state: "wait", detail: "" })))
    .map((x, i) => stepRowHtml(x, i, note[i])).join("");
  const body = rb
    ? `<div class="small">${hv(`${rb.decided_by} · ${rb.at}`, `${rb.rollback_id} · ${rb.deployment_id} · ${rb.decided_by} · ${rb.at} · 이유: ${rb.reason_text}`)}</div><div class="dep-list" id="dep-steps">${list}</div>` +
      (rb.failed ? `<div class="rg err small" id="rb-failed">되돌리기 실패 — ${esc(rb.reason || "")} · ${esc(rel.name)}은 그대로 운영입니다</div>` : "") +
      `<div class="field">${fold(`rb-command-${rb.rollback_id}`, "서버 명령 보기(복사)", `<textarea id="rb-command" readonly rows="4" style="width:100%;font-family:monospace;font-size:11px" title="단계는 배포단 상태 파일(${esc(rb.rollback_id)}.json)을 읽어 보입니다">${esc(rb.command || "")}</textarea>`)}</div>` +
      `<div class="row" style="margin-top:10px;justify-content:flex-end">${rb.failed && tg ? `<span class="btn" data-act="rb-again" id="rb-again">다시 되돌리기</span>` : ""}<span class="btn" data-act="rb-cancel" id="rb-close">닫기</span></div>`
    : `<div class="small" id="rb-head">이미 등록된 ${esc(tname)}(revision ${revShort(tg ? tg.revision : "")})이 다시 운영 Version이 됩니다 · ${esc(rel.name)}은 이전 Version으로 남습니다</div>` +
      `<div class="dep-list">${list}</div>` +
      byField("rb-by", st.by, st.err, "실명 · 연락처 없음") +
      `<div class="field"><label for="rb-reason">이유</label><input id="rb-reason" value="${esc(st.reason)}"></div>` +
      (notBy(st.err).length ? `<div class="rg err small" id="rb-error">${notBy(st.err).map(esc).join("<br>")}</div>` : "") +
      `<div class="row" style="margin-top:10px;justify-content:flex-end"><span class="btn" data-act="rb-cancel" id="rb-cancel">취소</span><span class="btn p${st.busy ? " off" : ""}" data-act="rb-send" id="rb-send">되돌리기 승인</span></div>`;
  return `<div class="scrim" id="deploy-window" data-kind="rollback"><div class="modal" style="max-width:min(560px, calc(100vw - 32px))"><div class="mh"><h3>${rb ? `${esc(rel.name)} → ${esc(tname)} 되돌리기 단계` : `${esc(tname)}으로 되돌릴까요?`}</h3></div><div class="mb">${body}</div></div></div>`;
}
function rollbackSend(p) {
  const st = UI.rollback;
  if (!st || st.busy) return;
  st.busy = true; st.err = null; render();
  api("POST", `/api/projects/${p.id}/releases/${st.rel}/rollback`, { decided_by: st.by, reason: st.reason })
    // D-652 12-g: the steps from the approval's own answer at once (step 1 done) — the state read can be slow; the status file's steps
    // replace them once it is read
    .then(out => {
      const tg = ((relOf(p, st.rel) || {}).version || {}).rollback_target || {};
      st.busy = false; st.again = false; st.id = out && out.rollback_id;
      st.sent = out && out.rollback_id ? { rollback_id: out.rollback_id, deployment_id: out.deployment_id, command: out.command, decided_by: st.by, reason_text: st.reason,
        at: "방금", target: tg.release, target_name: tg.name, steps: [{ name: "되돌리기 요청", state: "done", detail: "결정을 저장했습니다" }, { name: "배포단 등록 확인", state: "active", detail: "" }, { name: "운영 전환", state: "wait", detail: "" }] } : null;   // deploying.rollback_progress(None) 꼴 그대로
      keepBy(st.by); render(); return refresh(); })
    .catch(e => { st.busy = false; st.err = e.detail && e.detail.reasons ? e.detail.reasons : [e.message]; render(); });
}
function deploySend(p) {
  const st = UI.deploy;
  if (!st || st.busy) return;
  st.busy = true; st.err = null; render();
  api("POST", `/api/projects/${p.id}/releases/${st.rel}/deploy-approval`, { deployment_id: st.id, decided_by: st.by, reason: st.reason })
    // D-652 12-e: the approval may have moved the change onto the running version (D-620) — the window follows the version that went
    // out and shows its steps, so the measured version's form is gone (no second approval)
    .then(r => { st.busy = false; if (r && r.release) st.rel = r.release; keepBy(st.by); render(); return refresh(); })   // D-652 12-g: drawn at once
    .catch(e => { st.busy = false; st.err = e.detail && e.detail.reasons ? e.detail.reasons : [e.message]; render(); });
}
function confirmSend(p, mode) {
  const c = UI.confirmState;
  if (!c || c.busy) return;
  c.busy = true; c.err = null; render();
  api("POST", `/api/projects/${p.id}/edit/confirm`, { mode, memo: c.memo, ...(c.wheel != null ? { wheel: c.wheel } : {}) })
    .then(r => { UI.confirm = false; UI.confirmState = null; UI.ediff = null; return refresh().then(() => { UI.itab = "exec"; go(`#/p/${p.id}/r/${r.release.id}`); }); })
    .catch(e => { c.busy = false; c.err = e.message; render(); });
}

function nodeSel(rel) {
  if (rel.pack) return "";   // EV-022 PC-1 (manual p.3 ①, p.6): an Agent Pack node opens the popover next to the node (web/popover.js), not a panel here
  const g = rel.graph, n = g.nodes.find(x => x.name === UI.node) || g.nodes[Math.min(2, g.nodes.length - 1)];
  if (!n) return "";
  const where = n.dynamic ? `동적 구성 · 코드 양방향 반영 불가` : esc(n.loc);
  const kinds = g.edges.filter(e => e.source === n.name || e.target === n.name).map(e => e.kind === "cond" ? "조건부" : e.kind === "loop" ? "loop" : e.kind === "par" ? "병렬" : "").filter(Boolean);
  const packInfo = n.kind ? `<br><span id="node-kind">${esc(n.kind)}</span>${n.fn_loc ? ` · 함수 <span id="node-fn">${esc(n.fn_loc)}</span> · 소스 ${esc(n.source_sha256.slice(0, 8))}` : ""} · params <span class="muted" id="node-params">${esc(Object.entries(n.params || {}).map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ").slice(0, 240))}</span>` : "";
  return `<div class="small" id="node-info"><b>선택 node: ${esc(n.name)}</b> <span class="muted" id="node-loc">${where}</span>${kinds.length ? ` <span class="muted">· ${[...new Set(kinds)].join(" · ")}</span>` : ""}${packInfo}<br>입력 필드: ${esc(g.input_field)}</div>`;
}
function revShort(x) { return esc((x || "").replace(/^sha256:/, "").slice(0, 8)); }
function execInsp(p, rel) {
  const ins = inputsOf(p, rel);
  if (!UI.execInput || !ins.find(i => i.id === UI.execInput)) UI.execInput = ins.length ? ins[ins.length - 1].id : null;
  const L = projLlm(p.scoring);   // the Project's provider decides (EV-005 CHG-28)
  const keyWarn = p.scoring.llm && !hasKey(L.id) ? `<div class="banner err small" id="llm-key-warning"><b>${esc(L.name)} API 키를 찾을 수 없음</b> — 이대로 실행하면 LLM 점수는 '채점 안 됨(키 없음)'으로 남습니다.<span class="sp"></span><span class="btn" data-act="go" data-to="#/settings" id="to-settings">설정 열기</span></div>` : "";
  const box = ins.length ? `<h3>실행 입력</h3><div data-region="R-05" id="exec-inputs">` + ins.map(i => `<div class="lrow${i.id === UI.execInput ? " on" : ""}" data-act="pick-input" data-id="${i.id}"><input type="radio"${i.id === UI.execInput ? " checked" : ""}><span class="small">${i.kind} · <b>${suiteWords(rel, i.name) !== i.name ? hv(suiteWords(rel, i.name), i.name) : esc(i.name)}</b>${i.tag ? ` · ${hv(String(i.tag).replace(/^S-\d+ · (\d\d-\d\d) \d\d:\d\d 등록/, "$1 등록"), i.tag)}` : ""} · ${i.n}건</span></div>`).join("") + `</div><span class="link small" data-act="go" data-to="#/p/${p.id}/inputs">+ 실행 입력 만들기</span>`
    : `<div class="rg err small" data-region="R-05" id="no-input"><b>실행에 필요한 입력값이 없습니다</b><div class="row" style="margin-top:6px"><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=gen" id="ni-gen">입력값 생성</span><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=file" id="ni-file">파일 올리기</span><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=direct" id="ni-direct">직접 쓰기</span><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=data" id="ni-data">검증데이터 올리기</span></div></div>`;
  const v = rel.version, from = v && v.from ? relOf(p, v.from.rid) : null;
  const chosen = ins.find(i => i.id === UI.execInput);
  const sameRun = from && chosen ? p.runs.filter(r => r.rel === from.id && r.input.id === chosen.id && r.status !== "실행 중").pop() : null;
  const same = sameRun ? `<div class="small muted" id="exec-same-input">${hv(`${from.name}과 같은 입력 · 바로 비교`, `${from.name} ${sameRun.id}과 같은 입력 — 비교에 바로 쓰입니다`)}</div>` : "";
  const vbox = v && v.from ? `<div class="vt" id="exec-version" style="margin-top:8px"><b>${esc(rel.name)}</b><div class="small">${hv(`${v.from.at ? `${v.from.at} 확정 · ` : ""}${v.from.release}에서`, `${v.from.at ? `확정 ${v.from.at} · ` : ""}출발 ${v.from.release} ${revShort(v.from.revision)} · revision ${revShort(v.revision)}`)}</div>` +
    (v.from.changes && v.from.changes.length ? `<div class="small" id="exec-version-changes">바뀐 곳: ${v.from.changes.map(x => hv(changeWords(rel, x), x)).join(" · ")}${v.from.auto ? ` · 출처 = 자동 · 가설 ${esc(v.from.auto.label)}` : ""}</div>` : "") +
    `</div>` : "";
  return keyWarn + box + same + vbox + (UI.runNotice ? `<div class="err-under" id="run-notice">${esc(UI.runNotice)}</div>` : "") + '<hr style="border:0;border-top:1px solid #eee">' + nodeSel(rel);   // EV-075: the '⋯' on the tab says it
}
function nextVersionName(p) { const n = p.releases.map(r => /^V(\d+)$/.exec(r.name)).filter(Boolean).map(m => +m[1]); return n.length ? `V${Math.max(...n) + 1}` : "V1"; }   // S-19, same rule as the server
function val(m, d = 2) { return m.v !== undefined ? (typeof m.v === "number" ? m.v.toFixed(d) : m.v) : null; }
/* EV-022 S-15 (manual p.40): what the removed feature left — its evaluation Runs and the change decision about them — stays as
   an earlier-flow record, folded under the Run tab of the release it started from. No verdict, comparison or opening here. */
function legacyBlock(p, rel) {
  const lg = p.legacy || { runs: [], decisions: [] }, runs = lg.runs.filter(r => r.rel === rel.id);
  if (!runs.length) return "";
  const ds = lg.decisions;
  return `<details class="small" id="legacy-records" style="margin-top:10px"><summary class="muted">흐름 이전 기록(시연 근거 아님) · Run ${runs.length}${ds.length ? ` · 결정 ${ds.length}` : ""}</summary>` +
    `<ul id="legacy-runs">${runs.map(r => `<li data-legacy-run="${esc(r.id)}">${esc(r.label)} · ${esc(r.status)} · ${esc(r.at)} · 실행 입력 ${esc(r.input.name)}</li>`).join("")}</ul>` +
    (ds.length ? `<ul id="legacy-decisions">${ds.map(d => `<li data-legacy-decision="${d.decision_id}">${esc(d.decision)} · ${esc(d.decided_by)} · 이유: ${esc(d.reason)}</li>`).join("")}</ul>` : "") + `</details>`;
}
/* The evaluation Run inspector (P07). The 운영 Run screen of manual p.30 — operational Runs, node input · output and '평가 case로 올리기' — is web/opsrun.js (EV-025) */
function runInsp(p, rel, run) { return runInspCore(p, rel, run) + legacyBlock(p, rel); }
/* EV-SUI-01 CHG-S06: the fake answers' limits of a case (llmops/fake_rule.py) — the rule's reason names as they are */
const FAKE_LIMIT_NOTE = "프롬프트 효과는 실제 provider에서만 봅니다";
function fakeLimitsText(fl) {
  return (fl || []).map(x => (x.reasons || []).join(" · ") + (x.node ? `(${x.node})` : "")).join("; ");
}
function fakeReasons(fl) {
  return [...new Set((fl || []).flatMap(x => x.reasons || []))].join(" · ");
}
function runInspCore(p, rel, run) {
  if (!run) return `<div class="empty small" id="no-run"><b>Run이 없습니다</b><div class="muted">실행 탭에서 실행 입력을 고르고 실행하세요.</div></div>`;
  const live = run.status === "실행 중";
  const ok = run.cases.filter(c => c.state === "완료" || c.state === STOPPED).length, fail = run.cases.filter(c => ["실패", "timeout"].includes(c.state)).length, fin = run.cases.filter(c => !["대기", "실행 중"].includes(c.state)).length;
  let head = `<div class="small" id="run-head"><b>${run.id} ${run.status}</b> case ${Math.min(fin + (live ? 1 : 0), run.cases.length)}/${run.cases.length} · 성공 ${ok} 실패 ${fail} · case 제한 <span id="run-limit">${run.limit}s</span></div>` +
    // EV-075 (v2 17쪽): the code number and the mode show; the rest of the run's identity on mouse-over (AC-09-C2 keeps the mode in view)
    `<div class="small muted" id="run-ident">${hv(`코드 ${run.code}`, `코드 ${run.code} · 그래프 ${run.graphId} · 실행 입력 ${run.input.name} (${run.input.kind}${(si => si ? ` · ${si.tag}` : "")(run.input.kind === SUITE && suiteInfo(p, run.input.id))}) · ${run.mode} · ${run.at}`)} · <span id="run-mode" title="${esc(run.mode)}">${run.mode === "가짜 provider" ? "가짜 모델" : "실제 모델"}</span></div>`;
  if (live) head += `<progress value="${fin}" max="${run.cases.length}" style="width:100%"></progress><span class="btn d" data-act="run-cancel" id="run-cancel">Run 취소</span>`;
  else {
    const m = { ...(run.summary || NO_SUMMARY), rec: recOf(p, run) };   // answer 019-A grade1 P2: the same Run shows the same recommendation here as in 판정 (S-22)
    const s22 = m.rec.v && m.rec !== (run.summary || NO_SUMMARY).rec;
    head += `<div class="row small" style="margin-top:6px" id="run-kpi"><span class="kpi" style="padding:6px"><b style="font-size:14px">${ok} / ${fail}</b>성공 / 실패</span><span class="kpi" style="padding:6px"><b style="font-size:14px" id="kpi-agg">${val(m.agg) ?? (m.agg.none === SUITE_NOTE ? "값 없음" : "—")}</b>집계</span><span class="kpi" style="padding:6px"><b style="font-size:14px" id="kpi-rec">${m.rec.v ? recWord(m.rec.v) : "없음"}</b>권고</span></div>` +
      `<div class="small muted" id="rec-rule">${s22 ? `적용 규칙: ${ruleWords(m.rec.rule)}` : m.rec.v ? `적용 규칙: ${ruleWords(m.rec.rule)} · 입력값: 집계 ${val(m.agg) ?? "—"} · 기준 ${p.rule.pass.toFixed(2)} · 오류 ${m.fails} · timeout ${m.tos}` : m.rule && m.rule.none === "검증데이터 없음" ? `규칙 점수: 검증데이터 없음 (0점 아님) · ${esc(m.rec.none)} → 판정 시 이유 필수` : hv("권고 없음 · 판정 때 이유 필요", `${m.rec.none}${m.agg.none ? " · " + m.agg.none : ""} → 판정 시 이유 필수`)}</div>`;
    if (run.cases.every(c => c.state === "실패" && c.sim && /API 키/.test(c.sim.error?.summary || ""))) head += `<div class="rg err small">API 키를 찾을 수 없음</div>`;
    if (m.scorecard) head += scorecardView(m.scorecard, relOf(p, run.rel));
  }
  const ci = Math.min(UI.caseIdx, run.cases.length - 1);
  const cases = `<h3 style="margin-top:10px">case</h3><div id="case-list">` + run.cases.map((c, i) => {
    const sc = c.llm && typeof c.llm === "object" ? c.llm.score.toFixed(2) : "";
    // D-432 덧 7 (R3): one folded line — the input's words, ▸ opens it as it is below the row (the row itself still picks the case)
    const key = `case-${run.id}-${i}`, many = typeof c.input !== "string" || c.input.includes("\n"), open = many && !!(UI.folds && UI.folds[key]);
    const words = typeof c.input === "string" ? c.input.split("\n")[0] : caseWords(rel, c.input);
    return `<div class="lrow${i === ci ? " on" : ""}" data-act="case" data-i="${i}" data-case-state="${c.state}"><span class="small" style="min-width:0;overflow-wrap:anywhere"${c.inputSha ? ` title="${esc(`입력 지문 ${c.inputSha.slice(0, 19)}…`)}"` : ""}>${many ? `<span class="fold-h" data-act="fold" data-k="${esc(key)}">${open ? "▾" : "▸"}</span> ` : ""}${i + 1} · ${esc(words)}</span><span class="sp"></span>${sc ? `<span class="small">${sc}</span>` : ""}<span class="badge ${c.state === "완료" || c.state === STOPPED ? "ok" : c.state === "실행 중" ? "run" : ""}">${c.state}</span>${c.fake_limits ? `<span class="badge hold" style="margin-left:4px" data-fake-limits="${esc(fakeReasons(c.fake_limits))}" title="${esc(`${fakeReasons(c.fake_limits)} — ${FAKE_LIMIT_NOTE}`)}">가짜 한계</span>` : ""}</div>` +
      (open ? `<div class="fold-b" data-fold="${esc(key)}" style="margin:0 0 6px 18px">${fold.pre(c.input)}</div>` : "");
  }).join("") + "</div>";
  const c = run.cases[ci];
  let detail = "";
  if (c && c.sim && !live) {
    const d = c.diag || { type: "—", rule: "—", evidence: "—" };
    const suiteRun = run.input.kind === SUITE;
    const ruleCell = suiteRun ? SUITE_NOTE : c.rule === "검증데이터 없음" ? "검증데이터 없음" : c.rule ? `정답 일치 ${c.rule.match ? "예" : "아니오"} · 금지 문구 ${c.rule.forbidden ? "포함" : "없음"}` : p.scoring.rule ? "—" : "규칙 채점 꺼짐";
    const llmCell = suiteRun ? SUITE_NOTE : p.scoring.skip ? "채점 생략" : !p.scoring.llm ? "LLM 채점 꺼짐" : typeof c.llm === "string" ? c.llm : c.llm ? `${c.llm.score.toFixed(2)} · ${esc(c.llm.why)} (${run.modeKey === "fake" ? "가짜 judge" : esc(c.llm.provider)})` : "—";
    detail = `<h3 style="margin-top:10px">case ${ci + 1} 결과</h3><div class="kv" id="case-detail"><span class="muted">상태</span><span>${c.state}${c.sim.error ? ` · 오류 node ${c.sim.error.node} · ${esc(c.sim.error.summary)}` : ""}${c.fake_limits ? `<span id="case-fake-limits" title="${esc(FAKE_LIMIT_NOTE)}"> · 가짜 응답 한계: ${esc(fakeLimitsText(c.fake_limits))}</span>` : ""}</span><span class="muted">최종 출력</span><span id="case-final">${esc(c.sim.final || "—")}</span>` +
      `<span class="muted">규칙 점수</span><span id="case-rule">${p.scoring.skip && !suiteRun ? "채점 생략" : ruleCell}</span><span class="muted">LLM 점수</span><span id="case-llm">${llmCell}</span><span class="muted">진단</span><span id="case-diag">${d.type} · 규칙: ${esc(d.rule)} · 근거: ${esc(d.evidence)}</span></div>`;
  }
  let io = "";
  const src = c ? (live && run.cur.ci === ci ? c.events : c.sim ? c.sim.visits : []) : [];
  if (c && UI.node) {
    const vs = src.filter(v => v.node === UI.node);
    if (vs.length) {
      const it = Math.min(UI.iter, vs.length), v = vs[it - 1];
      io = `<h3 style="margin-top:10px" id="io-title">${UI.node} · 회차 ${it}/${vs.length}</h3>` + (vs.length > 1 ? `<div class="row small">${vs.map((_, k) => `<span class="chip${k + 1 === it ? " on" : ""}" data-act="iter" data-k="${k + 1}">회차 ${k + 1}</span>`).join("")}</div>` : "") +
        `<div class="small" id="node-io"${v.kind ? ' style="overflow-wrap:anywhere"' : ""} data-io='${esc(JSON.stringify({ i: v.input, o: v.output }))}'>상태 ${v.status === "done" ? "완료" : v.status}${v.status !== "done" && c.sim && c.sim.error && c.sim.error.node === UI.node ? ` · 오류 ${esc(c.sim.error.summary)}` : ""}<span id="node-cost"> · 시간 ${v.ms != null ? (v.ms / 1000).toFixed(2) + "s" : "—"} · token ${v.tokens != null ? v.tokens : "—"}</span><br>입력 ${esc(JSON.stringify(v.input))}<br>→ 출력 ${v.output ? esc(JSON.stringify(v.output)) : "(없음)"}${packRecord(v, run.manifest)}</div>`;
    } else io = `<div class="small muted" id="node-io-none">이 case에서 ${UI.node} node를 지나지 않았습니다</div>`;
  }
  return head + cases + detail + io + (!live ? `<div style="margin-top:10px"><span class="btn p" data-act="itab" data-k="verdict" id="to-verdict">판정 탭 ›</span></div>` : "");
}
/* EV-041 CHG-203: a predict@1 node's values in one line — the same rule as llmops/node_detail.predict_view (the popover and the
   operational Run screen get that line from the server; the Run tab reads the stored visit here). Only a 0.11.0 visit (it always has
   probabilities) gets a line — a 0.10.x visit has none, as before */
function predictText(v) {
  const pr = Object.values(v.output && typeof v.output === "object" ? v.output : {}).find(x => x && typeof x === "object" && "type" in x && "probability" in x) || {};
  const get = k => v[k] ?? pr[k], pct = x => `${(Number(x) * 100).toFixed(1)}%`, parts = [], probs0 = get("probabilities");
  if (!probs0 || typeof probs0 !== "object" || !Object.keys(probs0).length) return "";
  if (get("model_release") != null) parts.push(`모델 release ${get("model_release")}`);
  if (pr.type != null && pr.probability != null) parts.push(`${pr.type} ${pct(pr.probability)}`);
  if (get("alarm") != null) parts.push(`알람 ${get("alarm") ? "예" : "아니오"}${get("threshold") != null ? `(문턱 ${Number(get("threshold")).toFixed(2)})` : ""}`);
  parts.push("확률 " + Object.entries(probs0).sort((a, b) => Number(b[1]) - Number(a[1]) || (a[0] < b[0] ? -1 : 1)).map(([k, x]) => `${k} ${pct(x)}`).join(" · "));
  return parts.join(" · ");
}
/* EV-050 CHG-W2-04: the vector DB version a retrieve visit used, when the Run manifest records its recipe (same rule as
   node_detail.index_view) */
function indexText(v, manifest) {
  const r = v.retrievals || {}, rel = r.index_release, idx = (manifest && manifest.evidence_index_releases) || {};
  const e = rel && (idx[r.store] ? [idx[r.store]] : Object.values(idx)).find(x => x && x.release_id === rel && x.index_recipe && typeof x.index_recipe === "object" && !Array.isArray(x.index_recipe));
  if (!e) return "";
  const size = e.index_recipe.chunk_size;
  return `${rel} · 청크 ${size == null ? "섹션 그대로" : size} · 오버랩 ${e.index_recipe.overlap || 0}`;
}
/* D-543 ⑥ ① · D-546 K6: the relations a node's output used, in one line — the same rule as llmops/node_detail.relations_of (graph
   {used, layer} marks · an exception_guide's relation_id · a decision's relation); "" when none */
function relationText(v) {
  const marks = [];
  const walk = (x, d) => {
    if (d > 3 || !x || typeof x !== "object") return;
    if (Array.isArray(x)) { x.forEach(y => walk(y, d + 1)); return; }
    const g = x.graph, eg = x.exception_guide;
    if (g && typeof g === "object" && !Array.isArray(g) && g.layer && g.used) marks.push(g);
    if (eg && typeof eg === "object" && typeof eg.relation_id === "string" && eg.relation_id) marks.push({ used: eg.relation_id, layer: null });
    if ("kind" in x && typeof x.relation === "string" && x.relation && !(g && typeof g === "object" && !Array.isArray(g))) marks.push({ used: x.relation, layer: null });
    Object.entries(x).forEach(([k, y]) => { if (k !== "graph" && k !== "exception_guide") walk(y, d + 1); });
  };
  walk(v.output, 0);
  if (!marks.length) return "";
  const used = [...new Set(marks.flatMap(m => typeof m.used === "string" ? [m.used] : m.used))].sort();
  const layer = [...new Set(marks.filter(m => m.layer).map(m => String(m.layer)))].sort().join(" · ");
  const named = used.filter(u => u !== "base");
  return (layer ? `그래프 ${layer} · ` : "그래프 · ") + (named.length ? `쓰인 관계 ${named.join(", ")}` : "기본 관계(이력 개수)");
}
/* Agent Pack node records (EV-002 CHG-10): kind, search results, checks, review */
function packRecord(v, manifest) {
  if (!v.kind) return "";
  const r = v.retrievals, chk = [...(v.checks || []), ...(v.expect_checks || []).map(x => ({ rule: "expect." + x.check, passed: x.passed, detail: x.detail }))];
  return `<div id="node-pack" style="margin-top:6px"><span class="muted">종류</span> ${esc(v.kind)}` +
    (r ? `<br><span class="muted">리트리버 결과</span> <span id="node-retrievals">${r.results.map(x => `${esc(x.id)} ${Number(x.score).toFixed(2)} ${esc(x.source_type)}`).join(" · ") || "없음"} · 넓혀 찾기: ${r.fallback_used ? "예" : "아니오"}${r.index_release ? ` · 벡터 DB ${esc(r.index_release)}` : ""}</span>` : "") +
    (chk.length ? `<br><span class="muted">검사</span> <span id="node-checks">${chk.map(x => `${esc(x.rule)} ${x.passed === true ? "통과" : x.passed === false ? "실패" : "—"}`).join(" · ")}</span>` : "") +
    (v.review ? `<br><span class="muted">검토</span> <span id="node-review">${esc(reviewText(v.review))}</span>` : "") +
    (v.route ? `<br><span class="muted">분기</span> ${esc(v.route.value)} → ${esc(v.route.target)}` : "") +
    (String(v.kind).startsWith("predict") && predictText(v) ? `<br><span class="muted">예측</span> <span id="node-predict">${esc(predictText(v))}</span>` : "") +
    (String(v.kind).startsWith("retrieve") && indexText(v, manifest) ? `<br><span class="muted">벡터 DB</span> <span id="node-index">${esc(indexText(v, manifest))}</span>` : "") +
    (relationText(v) ? `<br><span class="muted">관계</span> <span id="node-relations">${esc(relationText(v))}</span>` : "") + `</div>`;   // D-546 K6
}
/* scorecard of an evaluation suite Run (EV-002 CHG-12): columns kept apart, never one score */
function pct(x) { return x && x.v !== undefined ? `${Math.round(x.v * 100)}%${x.n !== undefined ? ` (${x.ok ?? Math.round(x.v * x.n)}/${x.n})` : ""}` : esc((x && x.none) || "—"); }
// EV-075 (수정본 F-01 · F-02 · R1 · R4 · R5): each cell is its result — what was not reached is grey '도달 안 함' (not a failure), node ids
// on mouse-over; the cell's whole text too
const plainText = h => String(h).replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
function scorecardView(s, rel) {
  const mechAll = Object.entries(s.mechanical_checks).map(([n, rules]) => rules.none ? `${esc(n)} ${esc(rules.none)}` : Object.entries(rules).map(([r, x]) => `${esc(n)}.${esc(r)} ${pct(x)}`).join(" · ")).join(" · ") || "검사 없음";
  const unreached = Object.values(s.mechanical_checks).filter(r => r.none && /도달 안 함/.test(r.none)).length;
  const reached = Object.entries(s.mechanical_checks).filter(([, r]) => !r.none).map(([n, rules]) => Object.entries(rules).map(([r, x]) => `${esc(roleWords(rel, n))} ${pct(x)}`).join(" · ")).filter(Boolean);
  const mech = `<span class="hv" title="${esc(plainText(mechAll))}">${reached.join(" · ")}${reached.length && unreached ? " · " : ""}${unreached ? `<span class="unreached">검사 ${unreached}개 도달 안 함</span>` : ""}${!reached.length && !unreached ? esc(plainText(mechAll)) : ""}</span>`;
  const brAll = Object.entries(s.branch_ratio).map(([n, b]) => `${esc(n)}: ${b.none ? esc(b.none) : Object.entries(b).map(([t, x]) => `${esc(t)} ${Math.round(x.v * 100)}%`).join(" · ")}`).join(" / ") || "분기 없음";
  const brParts = Object.entries(s.branch_ratio).map(([n, b]) => b.none ? (/도달 안 함/.test(b.none) ? `<span class="unreached">${esc(roleWords(rel, n))} 도달 안 함</span>` : `${esc(roleWords(rel, n))} ${esc(b.none)}`)
    : Object.entries(b).map(([t, x]) => `${esc(roleWords(rel, t))} ${Math.round(x.v * 100)}%`).join(" · "));
  const br = brParts.length ? `<span class="hv" title="${esc(plainText(brAll))}">${brParts.join(" · ")}</span>` : "분기 없음";
  const fa = s.fixture_agreement, mj = s.model_judge;
  const faAll = `최종 ${pct(fa.final)} · node ${pct(fa.nodes)}${fa.final_not_evaluated_stopped ? ` · 최종 기대값 평가 안 됨(검토 전 멈춤) ${fa.final_not_evaluated_stopped} case` : ""}`;
  return `<div class="kv small" id="scorecard" style="margin-top:8px"><span class="muted">기계 검사</span><span id="sc-mech">${mech}</span>` +
    `<span class="muted">기대값 일치</span><span id="sc-fixture">${fa.final.v === undefined && fa.nodes.v === undefined && !fa.final_not_evaluated_stopped ? hv("—", plainText(faAll)) : faAll}</span>` +
    `<span class="muted">분기 비율</span><span id="sc-branch">${br}</span>` +
    `<span class="muted">모델 검토자</span><span id="sc-judge">${mj.v !== undefined ? mj.v.toFixed(2) + (mj.provider === "가짜 provider" ? " (가짜 judge)" : mj.model ? ` · ${esc(mj.model)}${mj.served_model && mj.served_model !== mj.model ? ` (응답 ${esc(mj.served_model)})` : ""}` : "") + (mj.why && mj.why.length ? ` · 이유: ${esc(mj.why[0])}` : "") + (mj.setting ? ` · ${esc(mj.setting)}` : "") : esc(mj.none)}</span>` +
    `<span class="muted">사람 검토 품질</span><span id="sc-human">${hv("—", s.post_human_review_quality.none || "")}</span>` +
    `<span class="muted">속도 · 실패</span><span id="sc-ops">${hv(`${s.latency.v !== undefined ? s.latency.v.toFixed(1) + "s" : "—"} · 실패 ${plainText(pct(s.failure_rate))}`, `latency · token · 실패율: ${s.latency.v !== undefined ? s.latency.v.toFixed(1) + "s" : s.latency.none} · ${s.tokens.v !== undefined ? s.tokens.v.toFixed(1) + "k" : s.tokens.none} · ${plainText(pct(s.failure_rate))}`)}</span>` +
    (rs => `<span class="muted">검토 출처</span><span id="sc-review">${Object.values(s.review_sources).every(x => !x) ? hv("—", plainText(rs)) : rs}</span>`)(`fixture ${s.review_sources.fixture} · 자동 처리 ${s.review_sources.auto}${esc(autoActionsText(s.review_auto_actions))} · 모델 ${s.review_sources.model} · 사람 ${s.review_sources["사람"]}${s.review_sources["기타"] ? ` · 기타 ${s.review_sources["기타"]}` : ""}`) +
    (s.model_metrics ? (t => `<span class="muted">모델 지표</span><span id="sc-model">${/정확도 기대 case 없음/.test(plainText(t)) && !/정확도 \d/.test(plainText(t)) ? hv("—", plainText(t)) : t}</span>`)(modelMetricsText(s.model_metrics)) : "") +
    (s.retrieval_metrics ? `<span class="muted">리트리버 지표</span><span id="sc-retrieval">${retrievalMetricsText(s.retrieval_metrics, rel)}</span>` : "") + `</div>` +
    (s.nodes ? `<div style="overflow-x:auto;max-width:100%"><table class="small" id="sc-nodes" style="margin-top:6px;white-space:nowrap"><tr><th>node</th><th>도달</th><th>기계 검사</th><th>기대값</th><th>judge</th></tr>${s.nodes.map(n => `<tr data-sc-node="${esc(n.node)}"><td>${esc(n.node)}</td><td>${n.reached}/${n.cases}</td><td>${n.checks === null ? `<span class="unreached">도달 안 함</span>` : Object.keys(n.checks).length ? Object.entries(n.checks).map(([r, x]) => `${esc(r)} ${pct(x)}`).join(" · ") : "—"}</td><td>${n.expected && n.expected.v !== undefined ? pct(n.expected) : hv("—", (n.expected && n.expected.none) || "")}</td><td>${n.judge.v !== undefined ? `${n.judge.v.toFixed(2)}${n.judge.why && n.judge.why[0] ? ` · ${esc(n.judge.why[0])}` : ""}` : hv("—", n.judge.none || "")}</td></tr>`).join("")}</table></div>` : "") +
    (s.model_metrics ? confusionTables(s.model_metrics) : "") +
    (s.failure_reasons.length ? `<div class="small muted" id="sc-reasons">실패 이유 ${s.failure_reasons.slice(0, 5).map(r => `case ${r.case} ${esc(r.node)} ${esc(r.rule || r.path || "")}: 기대 ${esc(JSON.stringify(r.expected))} / 실제 ${esc(JSON.stringify(r.actual)).slice(0, 80)}`).join(" · ")}${s.failure_reasons.length > 5 ? ` 외 ${s.failure_reasons.length - 5}건` : ""}</div>` : "");
}
/* EV-042 CHG-206 (RX-L2): a suite Run's model metrics — 'node · 정확도 a/n · 재현율 <class> a/n … · 정밀도 … · 정상 오경보(알람) a/n'
   (classes without a case are left out of the line, kept in the table), then a confusion table per node (rows expected, columns predicted) */
function mmFrac(x) { return x && x.none === undefined && x.n !== undefined ? `${x.ok}/${x.n}` : esc((x && x.none) || "—"); }
function modelMetricsText(mm) {
  const per = (o) => Object.entries(o || {}).filter(([, x]) => x && x.none === undefined).map(([k, x]) => `${esc(k)} ${mmFrac(x)}`).join(" · ");
  return Object.entries(mm).map(([n, m]) => [`${esc(n)} · 정확도 ${mmFrac(m.accuracy)}`, per(m.recall) && `재현율 ${per(m.recall)}`, per(m.precision) && `정밀도 ${per(m.precision)}`,
    `정상 오경보(알람) ${mmFrac(m.normal_false_alarm)}`, m.no_label ? `기대 없음 ${m.no_label}` : "", m.not_reached ? `도달 안 함 ${m.not_reached}` : ""].filter(Boolean).join(" · ")).join(" / ");
}
/* EV-043 CHG-210: '<node> · recall@10 a/n · hit@10 a/n · 사람 확인 a/n · 같은 20장 반자동 a/n' (+ 리트리버 없음 k · 위치 없음 k) */
function retrievalMetricsText(rm, rel) {   // EV-075 (v2 17쪽): '<store> 정답 a/n · 1개 이상 a/n', the metric names on mouse-over
  return Object.entries(rm).map(([n, m]) => { const whole = [`${n} · recall@10 ${plainText(mmFrac(m["recall@10"]))}`, `hit@10 ${plainText(mmFrac(m["hit@10"]))}`,
      m["recall@10_human"] && m["recall@10_human"].none === undefined ? `사람 확인 ${plainText(mmFrac(m["recall@10_human"]))} · 같은 표본 반자동 ${plainText(mmFrac(m["recall@10_human_auto"]))}` : "",
      m.no_retrieval ? `리트리버 없음 ${m.no_retrieval}` : "", m.no_locator ? `위치 없음 ${m.no_locator}` : ""].filter(Boolean).join(" · ");
    const who = roleWords(rel, n).replace(/ 리트리버$/, "");
    return hv(`${who} 정답 ${plainText(mmFrac(m["recall@10"]))} · 1개 이상 ${plainText(mmFrac(m["hit@10"]))}`, whole); }).join(" / ");
}
function confusionTables(mm) {
  return Object.entries(mm).filter(([, m]) => m.classes && m.classes.length).map(([n, m]) => `<div style="overflow-x:auto;max-width:100%"><table class="small" id="sc-confusion" data-mm-node="${esc(n)}" style="margin-top:6px;white-space:nowrap">` +
    `<tr><th>${esc(n)} 기대 \\ 예측</th>${m.classes.map(c => `<th>${esc(c)}</th>`).join("")}</tr>` +
    Object.entries(m.matrix || {}).map(([w, row]) => `<tr data-mm-expected="${esc(w)}"><td>${esc(w)}</td>${m.classes.map(c => `<td>${row[c] || 0}</td>`).join("")}</tr>`).join("") + `</table></div>`).join("");
}
/* the recommendation a verdict is compared with: the stored one, or S-22 (user decision 10-14, manual p.26) — on a same-condition
   evaluation of a confirmed version whose paths moved against the Run it pairs with, hold (the server applies the same rule) */
function recOf(p, run) {
  const m = run.summary || NO_SUMMARY, rel = relOf(p, run.rel), gate = rel && rel.version && rel.version.judge, pair = gate && gate.pairs && gate.pairs[run.uid];
  return pair && pair.path_changed >= 1 ? { v: "hold", rule: `경로 ${pair.path_changed}개 변경 · 사람이 비교를 보고 판단` } : m.rec;
}
/* answer 019-A E-S-03 G1 (manual p.27): an approved or rejected version shows its result only — a change goes through edit → confirm */
function verdictResult(p, rel, ver) {
  const runs = p.runs.filter(r => r.rel === rel.id && (r.verdicts || []).length);
  const last = runs.map(r => ({ r, h: r.verdicts[r.verdicts.length - 1] })).filter(x => x.h.v === (ver.state === "승인됨" ? "adopt" : "reject")).sort((a, b) => String(a.h.at).localeCompare(String(b.h.at))).pop();
  const ap = ver.approval, h = last ? last.h : {};
  const next = ver.state === "승인됨"
    ? (ver.rollout ? `배포 진행 중 — 위 띠의 단계를 봅니다` : `위 띠의 '배포 승인 ▶'을 누르면 이 ${esc(rel.name)}이 그대로 운영으로 갑니다`)
    : `고치려면 ${esc(rel.name)}에서 새 편집 → 확정(현재 버전 수정 · 새 버전 생성, 43쪽)`;
  const hist = last ? [...last.r.verdicts].reverse().map((x, i) => `<div class="small${i ? " muted" : ""}" data-verdict-row="${i}">${esc(x.v)}${x.by ? " · " + esc(x.by) : ""} · ${esc(x.at)}${x.reason ? " · 이유: " + esc(x.reason) : ""}</div>`).join("") : `<div class="small muted">—</div>`;
  return `<div class="vt" id="v-result" data-state="${esc(ver.state)}"><b>${esc(rel.name)} · ${esc(ver.state)}</b> (${esc(h.v || "")}${h.by ? " · " + esc(h.by) : ""} · ${esc(h.at || "")})${h.reason ? `<div class="small">이유: ${esc(h.reason)}</div>` : ""}</div>` +
    (ap ? `<div class="vt" id="v-approval"><b>승인에 묶인 것</b><div class="small">revision ${revShort(ap.revision)}${ap.data_release ? ` · 자료 묶음 ${esc(String(ap.data_release).slice(0, 12))}…` : ""}</div><div class="small">평가 ${esc(ap.label || "")}${ap.compare ? ` · 비교 ${esc(ap.compare.name)} ${esc(ap.compare.label)}` : ""} · scorecard</div></div>` : "") +
    `<h3 style="margin-top:8px">다음</h3><div class="small" id="v-next">${next}</div><h3 style="margin-top:8px">판정 이력</h3><div id="v-history">${hist}</div>`;
}
function verdictInsp(p, run, relOpen) {
  if (!run || run.status === "실행 중") {   // EV-064 CHG-CF-3 (나): a version not evaluated yet still gets the reason and the button
    const g = relOpen && relOpen.version && relOpen.version.judge, off = g && g.needed && !g.ok ? verdictSameOffer(p, relOpen) : null;
    return `<div class="small muted" id="no-verdict-run">${run ? "실행이 끝나면 판정할 수 있습니다" : "판정할 Run이 없습니다"}</div>` +
      (g && g.needed && !g.ok ? `<div class="small" style="color:#b45309;margin-top:4px" id="v-gate">${esc(g.reason)}</div>` +
        (off ? `<div class="row" style="margin-top:4px" id="v-gate-actions"><span class="btn${off.busy ? " off" : ""}" data-act="v-same-run" id="v-same-run">${off.busy ? "같은 조건으로 실행 중…" : "같은 조건으로 평가 돌리기"}</span></div>` : "") : "");
  }
  const hist = p.verdicts[run.id] || [], cur = hist[hist.length - 1];
  const rel = relOf(p, run.rel), ver = rel && rel.version, gate = ver && ver.judge, pair = gate && gate.pairs && gate.pairs[run.uid];
  if (ver && ["승인됨", "거절"].includes(ver.state)) return verdictResult(p, rel, ver);
  const m = { ...(run.summary || NO_SUMMARY), rec: recOf(p, run) };
  const gateWhy = gate && gate.needed && !(gate.pairs || {})[run.uid]
    ? (gate.ok ? `이 Run은 같은 조건 평가가 아닙니다 — 같은 조건 평가 ${Object.keys(gate.pairs).map(u => esc((p.runs.find(x => x.uid === u) || {}).id || u)).join(", ")}에서 ${VERDICT_WORD.adopt} · ${VERDICT_WORD.reject}을 남깁니다` : esc(gate.reason)) : "";
  const gated = !!gateWhy;
  const gateOpen = gated && gate.ok ? p.runs.filter(r => r.rel === run.rel && gate.pairs[r.uid]).pop() : null;   // EV-064 CHG-CF-4: one button to the latest same-condition evaluation
  const sameOffer = gate && gate.needed && !gate.ok ? verdictSameOffer(p, rel) : null;   // EV-064 CHG-CF-3 (나)
  if (UI.verdictFor !== (run.uid || run.id)) { UI.verdict = null; UI.verdictFor = run.uid || run.id; }   // EV-076: a pick does not carry over to another Run
  const sel = gated && ["adopt", "reject"].includes(UI.verdict) ? null : UI.verdict || null;   // EV-076 (D-431 덧): no default pick — the recommendation is only marked
  const ap = ver && ver.approval;
  const target = ver ? `<div class="vt" id="v-target"><b>판정 대상</b> · ${esc(rel.name)}(${esc(ver.badge)}${ver.from ? " · 고정" : ""}) · revision ${revShort(ver.revision)} · 평가 ${esc(run.id)}${pair ? ` · 비교 ${esc(pair.name)} ${esc(pair.label)}(같은 조건)` : gate && gate.needed ? " · 같은 조건 비교 없음" : ""}</div>` +
    (ap ? `<div class="vt" id="v-approval"><b>승인에 묶인 것</b> · revision ${revShort(ap.revision)}${ap.data_release ? ` · 자료 묶음 ${esc(String(ap.data_release).slice(0, 12))}` : ""} · 평가 ${esc(ap.label || "")}${ap.compare ? ` · 비교 ${esc(ap.compare.name)} ${esc(ap.compare.label)}` : ""} · scorecard</div>` : "") : "";
  const revTag = run.manifest && run.manifest.pack_revision ? `<span class="small muted hv" id="v-revision" title="revision ${esc(run.manifest.pack_revision)}">코드 ${esc(run.manifest.pack_revision.slice(7, 15))}</span>` : "";
  const aw = ver && ver.await;
  const awBox = aw ? `<div class="vt" id="v-auto-rec" style="margin-bottom:8px"><b>${esc(aw.label || "")} 권고 · ${VERDICT_WORD[ver.auto_rec] || VERDICT_WORD.adopt}</b> <span class="muted">(자동 · ${recBy(aw)})</span>` +
    `<div class="small" style="margin-top:4px"><b>1 권고 이유</b>${aw.scoring ? ` <span class="muted" id="v-auto-scoring">${esc(aw.scoring)}</span>` : ""}<br>${aw.reasons.filter(x => !(aw.hidden_none && /숨긴 case/.test(x))).map(esc).join(" · ")}${aw.hidden_none ? `<br><span class="err-text" id="v-auto-hidden-none">⚠ 숨긴 case 검증 없음</span>` : ""}</div>` +
    `<div class="small" style="margin-top:4px"><b>2 승인에 묶인 것</b><br>revision ${revShort(ver.revision)} · 개선 ${esc(aw.wheel)} · 가설 ${esc((ver.from && ver.from.auto && ver.from.auto.label) || "")}<br>평가 ${esc(aw.label || "")} · 비교 ${esc((aw.compare || {}).name || "")} ${esc((aw.compare || {}).label || "")} · scorecard</div>` +
    (aw.sealed ? `<div class="small" style="margin-top:4px" id="v-sealed"><b>봉인 결과</b> · ${esc(aw.sealed)}</div>`   // D-477 덧 13: the sealed test, once, on this wheel version
      : `<div class="row small" style="margin-top:4px"><span class="btn${UI.sealedBusy ? " off" : ""}" data-act="v-sealed-run" id="v-sealed-run">봉인 시험</span><span class="muted" id="v-sealed-by">결정자 별명 예 owner-01 · 괄호 · 공백 안 됨</span>${UI.sealedMsg ? `<span id="v-sealed-msg">${esc(UI.sealedMsg)}</span>` : ""}</div>`) +
    `<div class="small" style="margin-top:4px"><b>사람이 할 일</b> — 위 띠 <b>배포 승인 ▶</b> 한 번</div></div>` : "";
  const recLine = awBox + target + `<div class="row"><b>${run.id}</b>${revTag}${m.rec.v ? `<span class="badge hold" id="v-rec" data-v="${esc(m.rec.v)}">권고 ${VERDICT_WORD[m.rec.v] || esc(m.rec.v)}</span>` : `<span class="badge" id="v-rec">권고 없음</span>`}</div><div class="small muted">${m.rec.v ? `규칙: ${esc(m.rec.rule)}` : hv("권고 없음 · 이유 필요", `${m.rec.none} → 이유 필수`)}</div>`;
  const byRefused = typeof UI.verdictErr === "string" && BY_ERR.test(UI.verdictErr);   // D-457 B: under the alias field, not the reason
  const form = `<div data-region="R-05" id="verdict-form" style="margin-top:8px"><div class="row">` + ["adopt", "hold", "reject"].map(v => `<span class="btn${sel === v ? " p" : ""}${gated && v !== "hold" ? " off" : ""}" data-act="vsel" data-v="${v}" id="v-${v}" title="${v}${gated && v !== "hold" ? ` — ${gateWhy}` : ""}">${VERDICT_WORD[v]}${m.rec.v === v ? " (권고)" : ""}</span>`).join("") + `</div>` +
    (gated ? `<div class="small" style="color:#b45309;margin-top:4px" id="v-gate">${gateWhy}</div>` : "") +
    (gated && (gateOpen || sameOffer) ? `<div class="row" style="margin-top:4px" id="v-gate-actions">${gateOpen ? `<span class="btn" data-act="v-gate-open" data-run="${esc(gateOpen.id)}" id="v-gate-open">${esc(gateOpen.id)}로 판정</span>` : ""}` +
      `${sameOffer ? `<span class="btn${sameOffer.busy ? " off" : ""}" data-act="v-same-run" id="v-same-run">${sameOffer.busy ? "같은 조건으로 실행 중…" : "같은 조건으로 평가 돌리기"}</span>` : ""}</div>` : "") +
    `<div class="field" style="margin-top:8px"><label title="권고와 다르면 필수 · 확정이면 선택">이유</label><textarea rows="2" id="v-reason"${UI.verdictErr && !byRefused ? ' style="border-color:#dc2626"' : ""}>${esc(UI.vReason || "")}</textarea></div>` +
    (UI.verdictErr && !byRefused ? `<div class="small" style="color:#dc2626" id="v-error">${typeof UI.verdictErr === "string" ? esc(UI.verdictErr) : m.rec.v ? "권고와 다른 판정은 이유를 적어야 저장됩니다." : "권고가 없는 Run은 이유를 적어야 저장됩니다."}</div>` : "") +
    `<div style="margin-top:6px">${byField("v-by", UI.vBy ?? lastBy(p), byRefused ? [UI.verdictErr] : [], "실명 · 연락처 없음, 예 owner-01")}</div>` +
    `<div class="row small"><span>제외 범위 <input id="v-excl"></span><span>다음 가설 <input id="v-hyp"></span></div></div>` +
    `<div class="row" style="margin-top:8px"><span class="sp"></span><span class="btn" data-act="v-cancel" id="v-cancel">취소</span><span class="btn p${sel ? "" : " off"}" data-act="v-save" id="v-save">판정 저장</span></div></div>`;
  const histHtml = `<div style="margin-top:10px" id="v-history"><h3>현재 판정 · 이력</h3>` + (cur ? [...hist].reverse().map((h, i) => `<div class="small${i ? " muted" : ""}" data-verdict-row="${i}">${i ? "이전: " : ""}${hv(`${VERDICT_WORD[h.v] || h.v}${h.by ? " · " + h.by : ""} · ${String(h.at).replace(/:\d\d$/, "")}`, `${h.v}${h.by ? " · " + h.by : ""} · ${h.confirm ? "권고 확정" : "뒤집기"} · ${h.at}${h.reason ? " · 이유: " + h.reason : ""}${h.excl ? " · 제외 범위: " + h.excl : ""}${h.hyp ? " · 다음 가설: " + h.hyp : ""}${run.manifest && run.manifest.pack_revision ? " · revision " + run.manifest.pack_revision.slice(7, 15) : ""}`)}</div>`).join("") : `<div class="small muted">아직 판정 없음</div>`) + "</div>";
  return recLine + form + histHtml;
}

/* ------------------------------------------------------------------ P04 release registration */
function pageRegister(p) {
  const r = UI.reg || (UI.reg = { name: "", how: "local", path: "", builtin: "", result: null, busy: false });
  const res = r.result;
  const errList = res && res.errors && res.errors.length ? `<ul id="reg-errors">${res.errors.map(e => `<li>${e.line ? `pack.yaml ${e.line}줄: ` : ""}${esc(e.message)}</li>`).join("")}</ul>` : "";
  const isPack = res && !res.error && res.sourceKind === "agent_pack";
  const center = r.busy ? `<div class="small muted" id="reg-busy">코드를 읽는 중…</div>` : !res ? `<div class="small muted">읽으면 그래프가 여기에 보입니다</div>` : res.error ? `<div class="rg err small" id="reg-error"><b>등록 안 됨</b> — ${errList ? "호환 안 됨 — " : esc(res.error.replace(/^등록 안 됨 — /, ""))}${errList}</div>` :
    `<div class="small muted" id="reg-preview">${isPack ? `<b id="reg-pack">Agent Pack 감지 · ${esc(res.pack.id)} ${esc(res.pack.version)}</b> · ` : ""}${graphCounts(res.graph)}${res.envExcluded && res.envExcluded.length ? " · .env를 제외함" : ""}</div>` +
    (isPack && res.pack.warnings.length ? `<div class="note" id="reg-warnings">${res.pack.warnings.map(esc).join(" · ")}</div>` : "") +
    (res.graph.mismatch.length ? `<div class="note" id="reg-mismatch">선언 그래프와 실행 그래프가 다릅니다 — ${res.graph.mismatch.map(esc).join(" · ")}</div>` : "") + `<div id="reg-graph">${graphSVG(branchNamed(res.graph, res.branchLabels))}</div>` + graphLegend(res.graph);   // answer 021-01 2-2: the same branch names as the workbench; answer 021-06: its own sideways scroll
  const how = [["local", "로컬 폴더 경로"], ["upload", "업로드"], ["builtin", "내장 예시"]].map(([k, l]) => `<span class="chip${r.how === k ? " on" : ""}" data-act="reg-how" data-how="${k}">${l}</span>`).join(" ");
  // execution config of an Agent Pack: found in the folder's execution/ (EV-003 CHG-18); set once per read folder
  if (isPack && r.cfgFolder !== res.pack.work_folder) {
    const cs = res.pack.configs || []; r.cfgFolder = res.pack.work_folder;
    if (!r.config) { r.cfgPick = cs.length === 1 ? cs[0] : ""; r.config = r.cfgPick ? `${r.cfgFolder}/${r.cfgPick}` : ""; }
    else r.cfgPick = cs.find(c => `${r.cfgFolder}/${c}` === r.config) || "";
  }
  const cfgs = isPack ? res.pack.configs || [] : [];
  const pickErr = k => r.pickErr && r.pickErr.k === k ? `<div class="rg err small" id="reg-pick-${k}-error" style="margin-top:6px">${esc(r.pickErr.message)}</div>` : "";
  const cfgHelp = `<div class="small muted" id="reg-config-help" style="margin:2px 0 4px">실행 설정 파일(.yaml): 이 Pack을 실행할 Python과 벡터 DB·모델·표의 release 번호를 적은 파일입니다</div>`;
  const cfgArea = !isPack ? "" : cfgs.length ? `<div class="field" style="margin-top:8px"><label>실행 설정</label>${cfgHelp}<select id="reg-config-pick" data-act="reg-config-pick" style="width:100%">${cfgs.length > 1 ? `<option value=""${r.cfgPick ? "" : " selected"}>실행 설정을 고르세요</option>` : ""}${cfgs.map(c => `<option value="${esc(c)}"${c === r.cfgPick ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>${cfgs.length === 1 ? `<div class="small muted" id="reg-config-found" style="margin-top:4px">폴더 안에서 찾음</div>` : ""}</div>`
    : `<div class="field" style="margin-top:8px"><label>실행 설정</label>${cfgHelp}</div><div class="note" id="reg-config-none" style="margin-top:4px">Pack 폴더의 execution/에 실행 설정 파일이 없습니다 — 다른 파일을 고르세요</div><div style="margin-top:6px"><span class="btn${r.picking ? " off" : ""}" data-act="reg-pick-file" id="reg-pick-file">${r.picking === "file" ? "파일 고르는 중…" : "파일 고르기"}</span></div>${pickErr("file")}<input id="reg-config" style="margin-top:6px;width:100%;box-sizing:border-box" value="${esc(r.config || "")}" placeholder="또는 경로 입력 /Users/me/config/local-fake.yaml">`;
  const packCfg = isPack ? `${cfgArea}<div class="small muted" id="reg-pack-note" style="margin-top:6px">사본을 만들어 실행합니다 · 작업 폴더는 바꾸지 않음 · .env·.venv·evals 제외</div>` : "";
  const src = r.how === "local" ? `<div style="margin-top:8px"><span class="btn${r.picking ? " off" : ""}" data-act="reg-pick" id="reg-pick">${r.picking === "folder" ? "폴더 고르는 중…" : "폴더 불러오기"}</span></div>${pickErr("folder")}<div class="row" style="margin-top:6px"><input id="reg-path" style="flex:1;min-width:0" value="${esc(r.pathDraft !== undefined ? r.pathDraft : r.path)}" placeholder="또는 경로 입력 /Users/me/agents/…"><span class="btn" data-act="reg-read" id="reg-read">읽기</span></div><div class="small muted" style="margin-top:4px">원본 폴더는 바꾸지 않음</div>${packCfg}`
    : r.how === "upload" ? `<div style="margin-top:8px"><input type="file" id="reg-files" multiple><span class="btn" data-act="reg-read" id="reg-read">읽기</span></div><div class="small muted" style="margin-top:4px">업로드 시 .env 제외 · 복사본을 보관</div>`
    : `<div style="margin-top:8px" id="reg-builtins">` + (S.builtin || []).map(b => `<label class="lrow"><input type="radio" name="reg-builtin" value="${esc(b.id)}"${r.builtin === b.id ? " checked" : ""} data-act="reg-builtin" data-name="${esc(b.name)}"> ${esc(b.name)} <span class="small muted">· ${esc(b.example)}</span></label>`).join("") + `</div>`;
  const insp = `<h3>새 release</h3><div class="field"><label>이름</label><input id="reg-name" value="${esc(r.name)}" placeholder="release 이름"></div><div class="row">${how}</div>${src}` +
    `<div class="small muted" style="margin-top:6px">${hv("graph.py · agent.py · pack.yaml 폴더", "진입점: graph.py 또는 agent.py의 build_graph() (선택: INPUT_FIELD), 또는 Agent Pack의 pack.yaml(로컬 경로만)")}</div>` +
    `<div class="row" style="margin-top:12px"><span class="sp"></span><span class="btn" data-act="reg-cancel" id="reg-cancel">취소</span><span class="btn p${res && !res.error && (!isPack || r.config) ? "" : " off"}" data-act="reg-save" id="reg-save">등록</span></div>`;
  return shell(p, "", crumbP(p, "<b>새 release</b>"), workbench(p, "new", center, [["new", "새 release"]], "new", insp));
}
function regSource(r) {
  return r.how === "local" ? { how: "local", path: r.path, config: r.config || "" } : r.how === "builtin" ? { how: "builtin", builtin: r.builtin } : { how: "upload", files: r.files || {} };
}
function regRead() {
  const r = UI.reg; r.busy = true; r.result = null; render();
  api("POST", `/api/projects/${curProject().id}/releases/read`, regSource(r))
    .then(res => { r.busy = false; r.result = res; render(); })
    .catch(e => { r.busy = false; r.result = { error: e.message, errors: e.detail && e.detail.errors }; render(); });
}
function readUploads(input) {
  const fs = [...input.files];
  return Promise.all(fs.map(f => new Promise(ok => { const fr = new FileReader(); fr.onload = () => ok([f.webkitRelativePath || f.name, String(fr.result).split(",")[1] || ""]); fr.readAsDataURL(f); })))
    .then(pairs => Object.fromEntries(pairs));
}

/* ------------------------------------------------------------------ P09 compare */
const METRICS = ["정답 일치", "LLM 점수", "집계 점수", "성공률", "오류 수", "timeout 수", "latency", "token"];
function metricOf(m, k) {
  const map = { "정답 일치": m.rule, "LLM 점수": m.llm, "집계 점수": m.agg, "성공률": m.success, "오류 수": { v: m.fails }, "timeout 수": { v: m.tos }, "latency": m.latency, "token": m.tokens };
  return map[k];
}
function fmtMetric(k, x) { if (x.v === undefined) return null; return k === "성공률" ? `${Math.round(x.v * 100)}%` : k === "latency" ? `${x.v.toFixed(1)}s` : k === "token" ? `${x.v.toFixed(1)}k` : ["오류 수", "timeout 수"].includes(k) ? String(x.v) : x.v.toFixed(2); }
/* The comparison table comes from the server (compare_rows over stored Runs, P-10); only the row selection and the
   chart's metric are screen state. */
function loadCompare(p) {
  UI.cmpStale = false;
  const qs = new URLSearchParams();
  if (UI.cmpInput) { qs.set("input", UI.cmpInput); for (const [k, v] of Object.entries(UI.cmpRun)) { const [rel, inp] = k.split("|"); if (inp === UI.cmpInput) qs.append("pick", `${rel}:${v}`); } }
  if (UI.cmpAuto) qs.set("auto", UI.cmpAuto);
  UI.cmpErr = null;
  api("GET", `/api/projects/${p.id}/compare?${qs}`).then(d => { UI.cmp = { pid: p.id, ...d }; UI.cmpInput = d.input; render(); })
    .catch(e => { UI.cmpErr = { pid: p.id, auto: UI.cmpAuto || null, msg: e.message }; if (UI.cmp && UI.cmp.pid === p.id) UI.cmp = null; render(); });   // D-551 덧 1 ③
}
function pageCompare(p, auto) {
  if (!UI.cmpSel) UI.cmpSel = {};
  if ((auto || null) !== (UI.cmpAuto || null)) { UI.cmpAuto = auto || null; UI.cmpInput = null; UI.cmpStale = true; }   // EV-035: opening it (or leaving it) reads the table again
  if (UI.cmpErr && UI.cmpErr.pid === p.id && UI.cmpErr.auto === (UI.cmpAuto || null) && !UI.cmpStale)   // D-551 덧 1 ③: one error line, never '불러오는 중' for ever
    return shell(p, "", crumbP(p, "<b>비교</b>"), workbench(p, "compare", `<div class="small" id="cmp-err" style="color:#b42318">비교를 불러오지 못함 — ${esc(UI.cmpErr.msg || "")}</div>`, [["cmp", "비교"]], "cmp", ""));
  if (!UI.cmp || UI.cmp.pid !== p.id || UI.cmpStale) { loadCompare(p); if (!UI.cmp || UI.cmp.pid !== p.id) return shell(p, "", crumbP(p, "<b>비교</b>"), workbench(p, "compare", `<div class="small muted">불러오는 중…</div>`, [["cmp", "비교"]], "cmp", "")); }
  const d = UI.cmp, ins = d.inputs;
  const selKey = id => `${d.input}|${id}`;
  // EV-064 CHG-CF-1: rows a person did not change follow the server's pair on every read — checked when under the anchor's condition
  for (const r of d.rows) if (!UI.cmpTouched[selKey(r.rel)] || !r.run) UI.cmpSel[selKey(r.rel)] = !!r.run && (d.anchor ? !!r.same : true);
  const au = UI.cmpAuto && d.auto && !d.auto.waiting ? d.auto : null;
  if (au) for (const r of d.rows) UI.cmpSel[selKey(r.rel)] = !!(r.run && au.rows[r.rel]);
  const stCol = d.rows.some(r => r.state);   // EV-022 p.24: what is added to P09 is for tables with Agent Pack versions only; LangGraph-only tables stay as they were (S-17, PRES-114, answer 019-A E-S-03 Q5)
  // H1 (manual p.24): an Agent Pack table also shows the scorecard cells that differ between the two chosen Runs as columns
  const selPre = d.rows.filter(r => r.run && UI.cmpSel[selKey(r.rel)]);
  const detPre = stCol ? cmpDetails(p, selPre) : null, dd = detPre && detPre.data;
  const scAll = dd ? Object.entries(dd.scorecard).filter(([, x]) => x.changed) : [];
  // answer 019-A merge 2 §2 (manual p.24): the table keeps three changed cells — the check, then the branches (근거 검사 통과 · → 초안 ·
  // → escalation) — so 성공률 · latency stay on one screen; every changed cell is in the bars and in 세부 사항
  const scRank = ([k]) => k.startsWith("기계 검사") ? 0 : k.startsWith("분기 ") ? 1 : 2;
  const ownCells = r => ((d.scorecard || []).find(x => x.rel === r.rel && x.run_id === r.run) || {}).cells || {};   // EV-071 (D-443 ②)
  // EV-076 (D-445 덧): the retriever recall columns are always shown (each chosen row its own value), then the changed cells as before
  const rkKeys = stCol ? [...new Set(selPre.flatMap(r => Object.keys(ownCells(r)).filter(k => /recall@10$/.test(k))))] : [];
  const scCols = [...rkKeys.map(k => [k, null]), ...scAll.map((x, i) => [x, i]).sort((a, b) => scRank(a[0]) - scRank(b[0]) || a[1] - b[1]).slice(0, 3).sort((a, b) => a[1] - b[1]).map(([x]) => x).filter(([k]) => !rkKeys.includes(k))];
  // answer 019-A grade1 P1: Agent Pack suite Runs have no 정답 일치 · LLM 점수 · 집계 점수 (always '값 없음' — the scorecard is the score),
  // so a Pack table on an evaluation suite leaves those three out (they stay on the Run tab); LangGraph tables are as they were (S-17)
  const packSuite = stCol && (ins.find(i => i.id === d.input) || {}).kind === SUITE;
  const cols = stCol ? ["성공률", "latency", ...METRICS.filter(k => k !== "성공률" && k !== "latency" && !(packSuite && ["정답 일치", "LLM 점수", "집계 점수"].includes(k)))] : METRICS;   // p.24: 성공률 · latency next to the changed cells; every earlier column stays
  const scCell = r => scCols.map(([k, x]) => { if (x === null) { const on = r.run && UI.cmpSel[selKey(r.rel)], v = on ? ownCells(r)[k] : null, ref = selPre[0] ? ownCells(selPre[0])[k] : null, chg = on && v != null && ref != null && v !== ref;
      return `<td data-sc-col="${esc(k)}"${chg ? ' class="chg" data-changed="sc"' : ""}>${esc(v ?? "—")}</td>`; }   // EV-076: coloured only where it differs from the first chosen row
    const v = r.run === dd.base.run ? x.base : r.run === dd.other.run ? x.other : r.run && UI.cmpSel[selKey(r.rel)] ? ownCells(r)[k] : null;
    return `<td data-sc-col="${esc(k)}"${r.run === dd.other.run ? ' class="chg" data-changed="sc"' : ""}>${esc(v ?? "—")}</td>`; }).join("");
  const table = !d.any_run ? `<div class="empty" id="cmp-empty"><b>아직 비교할 Run이 없습니다</b><div class="small muted">이 실행 입력으로 돈 Run이 없습니다. release 탭에서 이 입력으로 실행하세요.</div></div>` :
    `<div style="overflow-x:auto;max-width:100%" id="cmp-table-wrap"><table id="cmp-table"><tr><th></th><th>release</th>${stCol ? "<th>상태</th>" : ""}<th>비교 Run</th>${scCols.map(([k]) => `<th data-sc-col="${esc(k)}">${mword(p, k)}</th>`).join("")}${cols.map(k => `<th>${mword(p, k)}</th>`).join("")}</tr>` + d.rows.map(r => {
      const ref = stCol && d.rows.find(x => x.run && x.metrics && UI.cmpSel[selKey(x.rel)]), other = ref && ref !== r && r.metrics && UI.cmpSel[selKey(r.rel)];
      const cells = cols.map(k => { if (!r.metrics) return `<td data-metric="${k}">—</td>`; const x = r.metrics[k], f = fmtMetric(k, x), chg = other && !["latency", "token"].includes(k) && f !== fmtMetric(k, ref.metrics[k]); return `<td data-metric="${k}"${chg ? ' class="chg" data-changed="1"' : ""}>${f ?? `<span class="val-none" title="값 없음">—</span>`}</td>`; }).join("");
      return `<tr data-row="${esc(r.name)}"${other ? ' class="cmp-other"' : ""}><td><input type="checkbox" data-act="cmp-sel" data-rel="${r.rel}"${r.run ? "" : " disabled"}${r.run && UI.cmpSel[selKey(r.rel)] ? " checked" : ""} style="accent-color:${r.color}" data-color="${r.color}"></td><td><span style="color:${r.color}">●</span> ${esc(r.name)}</td>${stCol ? `<td data-state-cell>${vbadge(r.state) || "—"}</td>` : ""}` +
        `<td>${r.runs.length ? `<select data-act="cmp-run" data-rel="${r.rel}">${r.run ? "" : `<option value="" selected>고르기</option>`}${r.runs.map(x => `<option value="${x.id}"${x.id === r.run ? " selected" : ""}>${x.label} · ${x.status}</option>`).join("")}</select>` : `<span title="이 입력으로 돈 Run 없음">—</span>`}</td>${scCell(r)}${cells}</tr>`;   // the changed cells first (p.24 columns in sight), the earlier columns after them
    }).join("") + "</table></div>";
  const sel = d.rows.filter(r => r.run && UI.cmpSel[selKey(r.rel)]);
  let chart = "", changedShown = false;
  if (d.any_run) {
    // EV-076: recall first when there is one; a picked recall metric this table does not have falls back to the default rule
    const pickedRk = UI.metricSet && String(UI.metric || "").startsWith("__sc:") ? UI.metric.slice(5) : null, lost = !!pickedRk && !rkKeys.includes(pickedRk);
    const asDefault = !UI.metricSet || lost, rkMetric = pickedRk && !lost ? pickedRk : asDefault ? rkKeys[0] || null : null;
    const plain = METRICS.includes(UI.metric) ? UI.metric : METRICS.find(k => /^LLM/.test(k)) || METRICS[0];   // the page's first default (critic E3-A2 N3)
    const showChanged = !!(scAll.length && (UI.metric === "__changed" || (asDefault && !rkMetric)));   // H1: '바뀐 지표 n개' first for an Agent Pack table
    changedShown = showChanged;
    chart = `<div style="margin-top:12px" id="cmp-chart"><div class="row"><b>그래프</b><select id="cmp-metric" data-act="cmp-metric">${scAll.length ? `<option value="__changed"${showChanged ? " selected" : ""}>달라진 것 ${scAll.filter(([, x]) => cellNum(x.base) !== null && cellNum(x.other) !== null).length}개</option>` : ""}${rkKeys.map(k => `<option value="__sc:${esc(k)}"${rkMetric === k ? " selected" : ""}>${esc(metricWords(p, k))}</option>`).join("")}${METRICS.map(k => `<option value="${esc(k)}"${!showChanged && !rkMetric && k === plain ? " selected" : ""}>${esc(metricWords(p, k))}</option>`).join("")}</select><span class="small muted" style="display:none">${showChanged ? `${esc((selPre[0] || {}).name || "")} 파랑 · ${esc((selPre[1] || {}).name || "")} 주황 · 표에서 고른 release` : "한 번에 지표 하나 · 표에서 고른 release"}</span></div>`;
    if (!sel.length) chart += `<div class="small muted" id="cmp-nosel" style="margin-top:8px">표에서 비교할 release를 고르세요</div>`;
    const vals = showChanged || rkMetric ? [] : sel.map(r => ({ r, x: r.metrics[plain] || { none: "—" } }));
    if (rkMetric && !showChanged) { const rv = sel.map(r => ({ r, raw: ownCells(r)[rkMetric] })), mx = Math.max(1e-9, ...rv.map(o => cellNum(o.raw) ?? 0));
      chart += rv.map(({ r, raw }) => `<div class="barrow" data-bar="${esc(r.name)}"><span class="nm">${esc(r.name)}</span>` + (cellNum(raw) === null ? `<span class="val-none" data-none="1">—</span>` :
        `<div style="height:16px;width:${Math.max(2, Math.round(cellNum(raw) / mx * 380))}px;background:${r.color};border-radius:3px" data-color="${r.color}"></div><b data-val="${esc(raw)}">${esc(raw)}</b>`) + "</div>").join(""); }
    if (showChanged) chart += cmpChangedBars(detPre, selPre, ownCells, p);
    const max = Math.max(1e-9, ...vals.filter(o => o.x.v !== undefined).map(o => o.x.v));
    chart += vals.map(({ r, x }) => `<div class="barrow" data-bar="${esc(r.name)}"><span class="nm">${esc(r.name)}</span>` + (x.v === undefined ? `<span class="val-none" data-none="1">값 없음 — ${esc(x.none)}</span>` :
      `<div style="height:16px;width:${Math.max(2, Math.round(x.v / max * 380))}px;background:${r.color};border-radius:3px" data-color="${r.color}"></div><b data-val="${fmtMetric(plain, x)}">${fmtMetric(plain, x)}</b>`) + "</div>").join("") + "</div>";
  }
  // EV-075 SUITE: an evaluation suite by its role words and count (an <option> cannot hover — the full line shows under the select)
  const cmpRel = (p.releases || []).find(r => r.graph && r.graph.nodes.some(n => n.kind)) || null;
  const cmpLabel = i => (si => `${suiteWords(cmpRel, i.name) !== i.name ? esc(suiteWords(cmpRel, i.name)) : `${i.kind} · ${esc(i.name)}`}${si ? ` · ${si.tag.replace(/^S-\d+ · (\d\d-\d\d) \d\d:\d\d 등록.*$/, "$1 등록")}` : ""} · ${i.n}건${si && si.group.length > 1 ? ` · 같은 내용 ${si.group.slice(1).join("·")} 함께` : ""}`)(i.kind === SUITE && suiteInfo(p, i.id));
  const cmpChosen = ins.find(i => i.id === d.input) || ins[0];   // EV-013 CHG-74: the closed select can cut the label; its whole text is written below it
  const insp = `<h3>비교 기준</h3><select id="cmp-input" data-act="cmp-input">${ins.map(i => `<option value="${i.id}"${i.id === d.input ? " selected" : ""}>${cmpLabel(i)}</option>`).join("")}</select>${cmpChosen ? `<div class="small sel-full" id="cmp-input-full">${cmpLabel(cmpChosen)}</div>` : ""}` +
    ((si => si && si.group.length > 1 ? `<div class="small muted cmp-same-note">같은 내용으로 등록된 평가 suite(${si.group.join("·")})의 Run을 함께 비교합니다</div>` : "")(suiteInfo(p, d.input))) +
    `<div style="margin-top:12px"><span class="btn p" data-act="go" data-to="#/p/${p.id}/monitor" id="to-monitor">고른 release 모니터 비교 ›</span></div>`;
  const det = stCol ? cmpDetails(p, sel) : null;
  const inLabel = (i => i ? `${esc(i.name)} ${i.n} case` : "")(ins.find(i => i.id === d.input));
  const offer = UI.cmpOffer = sameRunOffer(p, d, sel);   // EV-064 CHG-CF-3 (가)
  const nCase = (ins.find(i => i.id === d.input) || {}).n;   // EV-075 (v2 25쪽): the condition as its result, the whole line on mouse-over
  const condAll = `수치 비교 · 같은 실행 입력(${plainText(inLabel)})${det && det.data ? det.data.condition.same ? " · 같은 조건 확인됨(suite · 모델 · 벡터 DB · 표 · runtime · 자료 묶음)" + plainText(notesText(det.data.condition)) : ` · 조건 다름 — ${differsText(det.data.condition)}` : ""}`;
  const cond = `<div class="cmp-cond" id="cmp-cond">${det && det.data && !det.data.condition.same ? `${hv(`같은 시험 ${nCase ?? ""}건`, condAll)} · <b>조건 다름 — ${esc(differsText(det.data.condition))}</b>` : hv(`같은 시험 ${nCase ?? ""}건${det && det.data ? " · 조건 같음 ✓" : ""}`, condAll)}` +
    (offer && offer.targets.length ? ` <span class="btn${offer.busy ? " off" : ""}" data-act="cmp-same-run" id="cmp-same-run">${offer.busy ? "같은 조건으로 실행 중…" : "같은 조건으로 평가 돌리기"}</span>` : "") +
    (offer && offer.line ? `<div class="small" id="cmp-no-pair">${esc(offer.line)}</div>` : "") + `</div>`;
  if (UI.cmpAuto && d.auto) return shell(p, "", crumbP(p, "<b>비교</b>"), workbench(p, "compare", autoCompare(p, d, cond, det, sel), [["cmp", "비교"]], "cmp", autoCompareInsp(p, d)));
  return shell(p, "", crumbP(p, "<b>비교</b>"), workbench(p, "compare", (d.any_run && stCol ? cond : "") + table + chart + (changedShown ? "" : cmpChangedBars(det, sel, ownCells, p)) + cmpDetailsBlock(det) + scorecardCompare(d.scorecard, p) + revDiff(p), [["cmp", "비교"]], "cmp", insp));
}
/* EV-035 CHG-184 (supplement A8 · 11/26): P09 as the wheel opened it — the two versions on the wheel's work suite with the added cells
   (고칠 세트 · 지킬 세트 · 흔들림 · 숨긴 case · 토큰 · latency · LLM 호출), the criteria line and, on the right, the grading agent's
   recommendation (kept on the wheel's row — not a verdict). The details below are the usual ones for the two Runs. */
// EV-075 (ORCH 더함): who made the wheel's recommendation — the accuracy wheel's grading agent, the efficiency wheel's criteria table
const recBy = aw => aw && aw.kind === "efficiency" ? "기준 표" : "채점 에이전트";
function autoCompare(p, d, cond, det, sel) {
  const au = d.auto;
  if (au.waiting) return `<div class="small muted" id="cmp-auto-waiting">${hv(`${au.wheel} 비교 없음 — ⑥ 뒤에 생김`, `개선 ${au.wheel}의 비교는 아직 없습니다 — ⑥에서 채점 에이전트가 권고를 남기면 여기에 보입니다`)}</div>`;
  const k = x => x == null ? "—" : x >= 1000 ? (x / 1000).toFixed(1) + "k" : Math.round(x);
  const rows = d.rows.filter(r => au.rows[r.rel]).map(r => { const a = au.rows[r.rel], isNew = r.rel === au.new.id;
    return `<tr data-auto-row="${esc(r.name)}"${isNew ? ' class="cmp-other"' : ""}><td><input type="checkbox" checked disabled style="accent-color:${r.color}"></td>` +
      `<td><span style="color:${r.color}">●</span> ${esc(r.name)} ${isNew ? "✦ 자동" : esc((r.state || ""))} · ${esc(r.label || "")}</td>` +
      `<td data-c="fix">${esc(a.fix)}${isNew && a.fixed_up ? " ✓" : ""}</td><td data-c="keep">${esc(a.keep)}</td><td data-c="flaky">${esc(a.flaky)}</td>` +
      `<td data-c="hidden"${isNew && /없음/.test(a.hidden) ? ' class="err-text"' : ""}>${esc(a.hidden)}</td><td data-c="tokens">${k(a.tokens)}</td>` +
      `<td data-c="latency">${a.latency == null ? "—" : a.latency.toFixed(1) + "s"}</td><td data-c="calls">${a.calls == null ? "—" : (Math.round(a.calls * 10) / 10)}</td></tr>`; }).join("");
  const table = `<div style="overflow-x:auto;max-width:100%"><table id="cmp-auto-table"><tr><th></th><th>release</th><th>고칠 세트</th><th>지킬 세트</th><th>흔들림</th><th>숨긴 case</th><th>토큰</th><th>latency</th><th>LLM 호출</th></tr>${rows}</table></div>`;
  const mark = c => c.ok === true ? "✓" : c.ok === false ? "✗" : "—";
  const crit = `<div class="vt" id="cmp-auto-criteria" style="margin-top:10px"><b>판정 기준 — ${au.kind === "efficiency" ? "효율 개선" : "성능 개선(목표 정확도)"}</b>${au.scoring ? ` <span class="small" style="color:#6b7280" id="cmp-auto-scoring">${esc(au.scoring)}</span>` : ""}<div class="small">${au.criteria.map(c => `<span data-crit="${esc(c.name)}" data-ok="${c.ok}"${c.hover ? ` title="${esc(c.hover)}"` : ""}>${esc(c.name)}${c.text && !c.name.includes(c.text.split(" ")[0]) ? " " + esc(c.text) : ""} ${mark(c)}</span>`).join(" · ")}</div>` +
    `<div class="small muted">${esc(au.new.name)} 칸은 ⑤ 표의 실행을 다시 씀 · ${esc(au.base.name)} ${esc(au.base.label || "")}은 같은 조건 확인 · ${esc(au.new.name)}가 떨어지면 표의 다음 줄이 올라옴</div></div>`;
  const fake = au.real ? "" : `<div class="note" id="cmp-auto-fake" style="margin-top:8px">실제 품질 · 비용은 Gate B 뒤 — 지금 가짜 provider로는 잴 수 없음</div>`;
  const head = cond.replace("</div>", ` · 개선 ${esc(au.wheel)}이 자동으로 엶</div>`);
  return head + table + crit + fake + cmpDetailsBlock(det);
}
function autoCompareInsp(p, d) {
  const au = d.auto;
  if (au.waiting) return "";
  return `<h3>비교할 Version</h3><div class="small" id="cmp-auto-versions">☑ ${esc(au.base.name)} · ${esc(au.base.label || "")}<br>☑ ${esc(au.new.name)} · ✦ 자동 · ${esc(au.new.label || "")}</div>` +
    `<h3 style="margin-top:10px">채점</h3><div class="small">${au.kind === "efficiency" ? "기준 표(비용 · 호출 · 지연 · 지킬 망가짐)" : "채점 에이전트 — 버전이 어떻게 만들어졌는지 모른 채 결과만 · 기준 표 + scorecard, 추가 호출 없음"}</div>` +
    `<h3 style="margin-top:10px">판정 권고</h3><div class="vt" id="cmp-auto-rec" data-decision="${esc(au.decision)}"><b>${VERDICT_WORD[au.decision] ? hv(VERDICT_WORD[au.decision], au.decision) : esc(au.decision)} (자동)</b>` +
    (au.hidden_none ? `<div class="small err-text" id="cmp-auto-hidden-none">⚠ 숨긴 case 검증 없음</div>` : "") +
    (au.failed.length ? `<div class="small">떨어진 기준: ${au.failed.map(esc).join(" · ")}</div>` : "") +
    `<div class="small muted">개선 기록에 저장됨 — 판정이 아님(사람 판정은 그대로)</div></div>` +
    `<div style="margin-top:8px"><span class="btn p" data-act="cmp-auto-verdict" data-rel="${esc(au.new.id)}" id="cmp-auto-verdict">${esc(au.new.name)} 판정 보기 ›</span></div>`;
}
/* EV-064 CHG-CF-3 (가) (D-406 ②): '같은 조건으로 평가 돌리기' on P09 — only for a difference a new Run can make equal (runtime). Two or more
   checked rows whose only difference from the anchor is runtime: the rows on an older runtime. The anchor's row alone: its start
   version, else the 운영 version — when it has no Run on this input or differs only in runtime; any other difference is one head
   line instead. A target started by the button on this screen (UI.sameTried) that still differs is not offered again. */
function verCmp(a, b) { const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number); for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0); return 0; }
function sameRunOffer(p, d, sel) {
  if (!d.anchor || UI.cmpAuto) return null;
  const rt = r => { const run = p.runs.find(x => x.uid === r.run); return (((run && run.manifest) || {}).runtime || {}).version || ""; };
  let targets = [], line = null;
  if (sel.length >= 2) {
    const diff = new Set(sel.flatMap(r => r.differs || []));
    if (!diff.size || [...diff].some(k => k !== "runtime")) return null;
    const top = sel.map(rt).sort(verCmp).pop();
    targets = sel.filter(r => rt(r) !== top).map(r => r.rel);
  } else if (sel.length === 1 && sel[0].anchor) {
    const a = relOf(p, sel[0].rel), from = a && a.pack && a.pack.from && a.pack.from.rid;
    const op = (p.releases.find(r => r.version && r.version.state === "운영") || {}).id;
    const cand = d.rows.find(r => r.rel === (from && relOf(p, from) ? from : op && op !== sel[0].rel ? op : null));
    if (!cand || UI.cmpTouched[`${d.input}|${cand.rel}`]) return null;   // nothing to run, or a person unchecked it
    if (!cand.run || (cand.differs.length && cand.differs.every(k => k === "runtime"))) targets = [cand.rel];
    else if (cand.differs.length) line = `같은 조건 짝 없음 — ${cand.name} ${cand.differs.join(" · ")} 다름`;
    else return null;
  } else return null;
  const tried = targets.map(rid => UI.sameTried[`${p.id}|${d.input}|${rid}`]).filter(Boolean);
  const busy = tried.some(u => u === "…" || (r => r && ["대기", "실행 중"].includes(r.status))(p.runs.find(x => x.uid === u)));
  if (targets.length && tried.length === targets.length && !busy) return line ? { targets: [], line } : null;
  return { targets, line, busy };
}
/* EV-064 CHG-CF-3 (나) (D-406 ②): on a confirmed version without a same-condition evaluation, '같은 조건으로 평가 돌리기' runs this
   version and its pair version (the start version, else the 운영 version) on one suite (this version's last evaluation suite, else
   the pair's) — offered when a side has no evaluation Run or the two last evaluations differ only in runtime · suite (read with the
   comparison details); a version difference keeps the reason only. Once started from this screen and still unpaired: not again. */
function evalRuns(p, rid) { return p.runs.filter(r => r.rel === rid && r.manifest && r.manifest.evaluation && !["대기", "실행 중", "취소됨"].includes(r.status)); }
function verdictSameOffer(p, rel) {
  const from = rel.pack && rel.pack.from && rel.pack.from.rid, op = (p.releases.find(r => r.version && r.version.state === "운영") || {}).id;
  const pair = relOf(p, from) ? from : op && op !== rel.id ? op : null;
  if (!pair) return null;
  const mine = evalRuns(p, rel.id).pop(), theirs = evalRuns(p, pair).pop(), src = mine || theirs;
  const inp = src && inputsOf(p, rel).find(i => i.id === src.input.id);
  if (!inp) return null;
  const k = `${p.id}|v|${rel.id}`, tried = UI.sameTried[k];
  const busy = !!tried && (tried === "…" || tried.some(u => (r => r && ["대기", "실행 중"].includes(r.status))(p.runs.find(x => x.uid === u))));
  if (tried && !busy) return null;
  if (mine && theirs) {
    const key = `${theirs.uid}|${mine.uid}`;
    if (!UI.vSameDet || UI.vSameDet.key !== key) {
      UI.vSameDet = { key, differs: null };
      api("GET", `/api/projects/${p.id}/compare/details?base=${encodeURIComponent(theirs.uid)}&other=${encodeURIComponent(mine.uid)}`)
        .then(d => { if (UI.vSameDet && UI.vSameDet.key === key) { UI.vSameDet.differs = (d.condition || {}).differs || []; render(); } }).catch(() => {});
    }
    const dif = UI.vSameDet.differs;
    if (!dif || !dif.length || dif.some(x => !["runtime", "평가 suite"].includes(x))) return null;
  }
  return { rel: rel.id, pair, input: inp.id, busy };
}
/* EV-022 CHG-110 (manual p.24): the details of the first two chosen rows — their Runs' case paths (parallel siblings in one
   order, 019d), final outputs, the scorecard as named cells and the changed lines — read from the server, nothing stored */
function cmpDetails(p, sel) {
  if (sel.length < 2) return null;
  const key = `${sel[0].run}|${sel[1].run}`;
  if (!UI.cmpDet || UI.cmpDet.key !== key) {
    UI.cmpDet = { key, data: null, err: null, names: [sel[0], sel[1]] };
    api("GET", `/api/projects/${p.id}/compare/details?base=${encodeURIComponent(sel[0].run)}&other=${encodeURIComponent(sel[1].run)}`)
      .then(d => { if (UI.cmpDet && UI.cmpDet.key === key) { UI.cmpDet.data = d; render(); } }).catch(e => { if (UI.cmpDet && UI.cmpDet.key === key) { UI.cmpDet.err = e.message; render(); } });
  }
  UI.cmpDet.names = [sel[0], sel[1]];
  return UI.cmpDet;
}
function cellNum(x) { if (typeof x === "number") return x; const m = /^(\d+(?:\.\d+)?)\/(\d+)$/.exec(String(x ?? "")); return m ? (Number(m[2]) ? Number(m[1]) / Number(m[2]) : 0) : null; }
function cmpChangedBars(det, sel, ownCells, p) {
  if (!det || !det.data) return "";
  const rows = Object.entries(det.data.scorecard).filter(([, x]) => x.changed && cellNum(x.base) !== null && cellNum(x.other) !== null);
  if (!rows.length) return "";
  const [a, b] = det.names;
  const undrawn = Object.entries(det.data.scorecard).filter(([, x]) => x.changed && (cellNum(x.base) === null || cellNum(x.other) === null));   // a cell with no value on one side stays in the table only
  return `<div style="margin-top:12px" id="cmp-changed"><b title="${esc(a.name)} ↔ ${esc(b.name)} · scorecard에서 값이 다른 칸">달라진 것 ${rows.length}개</b>` + rows.map(([k, x]) => {
    // EV-071 (D-443 ②): a bar for each further chosen row too (its own cell, when it is a number)
    const more = (sel || []).filter(r => r.run !== a.run && r.run !== b.run && ownCells).map(r => [r, ownCells(r)[k]]).filter(([, raw]) => cellNum(raw) !== null);
    const va = cellNum(x.base), vb = cellNum(x.other), max = Math.max(1e-9, va, vb, ...more.map(([, raw]) => cellNum(raw)));
    const bar = (v, raw, r) => `<div class="barrow"><span class="nm small">${esc(r.name)}</span><div style="height:12px;width:${Math.max(2, Math.round(v / max * 300))}px;background:${r.color};border-radius:3px"></div><span class="small">${esc(raw)}</span></div>`;
    return `<div class="cmp-bar" data-changed-metric="${esc(k)}"><div class="small">${mword(p, k)}</div>${bar(va, x.base, a)}${bar(vb, x.other, b)}${more.map(([r, raw]) => bar(cellNum(raw), raw, r)).join("")}</div>`;
  }).join("") + (undrawn.length ? `<div class="small muted" id="cmp-undrawn">값이 한쪽에만 있어 막대로 못 그린 칸 ${undrawn.length}개 — ${undrawn.map(([k, x]) => `${esc(k)}(${esc(a.name)} ${esc(x.base ?? "—")} · ${esc(b.name)} ${esc(x.other ?? "—")})`).join(", ")} · 위 표에 있습니다</div>` : "") + "</div>";
}
/* the part of a case path where the two Runs differ: the node before the first difference, then this Run's own nodes
   (red) — the whole path is in the title (manual p.24 'case별 경로 차이') */
function cmpPath(path, other, marked) {
  let i = 0;
  while (i < path.length && i < other.length && path[i] === other[i]) i++;
  if (i === path.length && i === other.length) return `<span class="muted" title="${esc(path.join(" → "))}">경로 같음</span>`;
  const tail = path.slice(i).filter(n => marked.includes(n));
  const shown = [i ? esc(path[i - 1]) : "START"].concat((tail.length ? tail : path.slice(i, i + 1)).map(n => `<span class="cmp-only">${esc(n)}</span>`));
  return `<span title="${esc(path.join(" → "))}">${i > 1 ? "… → " : ""}${shown.join(" → ")}${tail.length || i < path.length ? "" : " (끝)"}</span>`;
}
function cmpDetailsBlock(det) {
  if (!det) return "";
  if (det.err) return `<div class="rg err small" id="cmp-details-error">${esc(det.err)}</div>`;
  const d = det.data;
  if (!d) return `<div class="small muted" id="cmp-details" data-loading>세부 사항을 읽는 중…</div>`;
  const s = d.summary, files = d.lines.map(f => `${esc(f.path)} ${f.lines}행`).join(" · ") || "없음";
  const nfl = d.cases.filter(r => r.keep).length;   // EV-SUI-01 CHG-S06: cases with fake limits
  const head = `<div class="row" data-act="cmp-det" id="cmp-det-toggle" style="cursor:pointer"><b title="case별 경로 차이 · scorecard 전체 칸 · 바뀐 줄">${UI.cmpDetOpen ? "▾" : "▸"} 자세히</b><span class="small muted" style="display:none">case별 경로 차이(${s.total} case 중 경로 ${s.path_changed} · 최종 출력 ${s.final_differs} 다름${nfl ? ` · 가짜 한계 ${nfl} case` : ""}) · scorecard 전체 칸 · 바뀐 줄(${files})${UI.cmpDetOpen ? "" : " — 눌러서 펼침"}</span></div>`;
  if (!UI.cmpDetOpen) return `<div id="cmp-details">${head}</div>`;
  const cases = d.cases.filter(r => r.changed || r.keep).map(r => `<tr data-case="${esc(r.case_id || r.index + 1)}"><td>${esc(r.case_id || `case ${r.index + 1}`)}</td><td>${cmpPath(r.base.path, r.other.path, r.path_diff.only_base)}</td><td>${cmpPath(r.other.path, r.base.path, r.path_diff.only_other)}</td><td>${r.final_differs ? "다름" : "같음"}</td></tr>` +
    (r.keep ? `<tr data-fake-limits-for="${esc(r.case_id || r.index + 1)}"><td></td><td colspan="3" class="small muted" title="${esc(FAKE_LIMIT_NOTE)}">가짜 한계: base ${esc(fakeLimitsText(r.fake_limits.base) || "없음")} · other ${esc(fakeLimitsText(r.fake_limits.other) || "없음")}</td></tr>` : "")).join("");
  const sc = Object.entries(d.scorecard).map(([k, x]) => `<tr${x.changed ? ' class="chg"' : ""}><td>${esc(k)}</td><td>${esc(x.base ?? "—")}</td><td>${esc(x.other ?? "—")}</td></tr>`).join("");
  const ctx = (x, n) => `<span class="nd-ctx">${n}    ${esc(x)}</span>`;   // same shape as the change view (p.21)
  const hunks = d.lines.map(f => `<div data-hunks="${esc(f.path)}">` + f.hunks.map(h => `<div class="nd-hunk"><span class="muted">${esc(f.path)} @@ ${h.ctx_start || h.old_start},${h.ctx_count || h.old.length} @@${h.id ? ` — id: ${esc(h.id)}` : ""}</span>` +
    `${(h.before || []).map((x, i) => ctx(x, h.ctx_start + i)).join("")}${h.old.map((x, i) => `<span class="nd-del">${h.old_start + i}  - ${esc(x)}</span>`).join("")}${h.new.map((x, i) => `<span class="nd-add">${h.new_start + i}  + ${esc(x)}</span>`).join("")}${(h.after || []).map((x, i) => ctx(x, h.old_start + h.old.length + i)).join("")}</div>`).join("") + "</div>").join("");
  return `<div id="cmp-details" data-open="1">${head}<div class="small" id="cmp-line" style="margin-top:6px">${esc(d.line)} · 최종 출력 다름 ${s.final_differs}개</div>` +
    `<div style="overflow-x:auto"><table id="cmp-cases"><tr><th>case</th><th>${esc(d.base.release)} ${esc(d.base.label)} 경로</th><th>${esc(d.other.release)} ${esc(d.other.label)} 경로</th><th>최종 출력</th></tr>${cases || `<tr><td colspan="4" class="muted">바뀐 case 없음</td></tr>`}</table></div>` +
    `<div style="overflow-x:auto;margin-top:8px"><table id="cmp-sc-cells"><tr><th>scorecard 칸</th><th>${esc(d.base.release)}</th><th>${esc(d.other.release)}</th></tr>${sc}</table></div>` +
    `<div id="cmp-lines" style="margin-top:8px"><b>바뀐 줄</b>${hunks || ` <span class="muted">없음 — 같은 revision</span>`}</div></div>`;
}
/* revision diff of two Agent Pack releases of the same Pack id (EV-002 CHG-13): read only */
function revDiff(p) {
  const packs = p.releases.filter(r => r.pack), ids = new Set(packs.map(r => r.pack.id));
  if (![...ids].some(id => packs.filter(r => r.pack.id === id).length > 1)) return "";
  const sel = UI.diffSel = UI.diffSel && packs.some(r => r.id === UI.diffSel.a) && packs.some(r => r.id === UI.diffSel.b) ? UI.diffSel : (() => {
    const a = packs.find(r => packs.filter(x => x.pack.id === r.pack.id).length > 1);
    return { a: a.id, b: packs.find(r => r.id !== a.id && r.pack.id === a.pack.id).id };
  })();
  const opt = k => packs.map(r => `<option value="${r.id}"${r.id === sel[k] ? " selected" : ""}>${esc(r.name)} · ${esc(r.pack.id)} · ${esc(r.pack.revision.slice(7, 15))}</option>`).join("");
  const d = UI.diff && UI.diff.pid === p.id ? UI.diff : null;
  let out = "";
  if (d && d.error) out = `<div class="small" style="color:#c4320a;margin-top:6px" id="diff-error">${esc(d.error)}</div>`;
  else if (d && d.res) {
    const r = d.res, head = `<div class="small muted" style="margin-top:6px">${esc(r.a.name)} ${esc(r.a.revision.slice(7, 15))} ↔ ${esc(r.b.name)} ${esc(r.b.revision.slice(7, 15))}</div>`;
    const val = c => c.before !== undefined && c.after !== undefined ? `${esc(c.before)} → ${esc(c.after)}` : esc(c.after ?? c.before ?? "");
    out = `<div id="diff-result">${head}` + (r.same ? `<div class="small" id="diff-same" style="margin-top:4px">차이 없음 — 같은 revision</div>` :
      `<table class="small" style="margin-top:4px;white-space:nowrap;min-width:max-content" id="diff-changes">${r.changes.map(c => `<tr data-diff-path="${esc(c.path)}"><td>${esc(c.path)}</td><td>${esc(c.change)}</td><td>${val(c)}</td></tr>`).join("")}</table>` +
      `<div class="small" style="margin-top:4px" id="diff-files">바뀐 파일: ${r.files.length ? r.files.map(f => `${esc(f.path)}${f.change === "변경" ? "" : `(${esc(f.change)})`}`).join(", ") : "없음"}</div>` +
      (r.graph ? `<div class="small" style="margin-top:4px" id="diff-graph">그래프 버전 ${esc(r.graph.before)} → ${esc(r.graph.after)}${r.graph.relations.map(x => ` · <span data-diff-relation="${esc(x.id)}">${esc(x.id)} ${esc(x.before)} → ${esc(x.after)}</span>`).join("")}</div>` : "")) + "</div>";   // D-543 ⑥ ③
  }
  return `<div style="margin-top:16px;overflow-x:auto" id="rev-diff"><b title="revision 차이 · 같은 Pack id의 두 release · 읽기만 합니다">코드 차이</b>` +
    `<div class="row" style="margin-top:6px"><select id="diff-a" data-act="diff-pick" data-k="a">${opt("a")}</select><span>↔</span><select id="diff-b" data-act="diff-pick" data-k="b">${opt("b")}</select><span class="btn" data-act="diff-open" id="diff-open">revision 차이 열기</span></div>${out}</div>`;
}
/* scorecard comparison for an evaluation suite input (EV-002 CHG-12): rows whose conditions differ are shown, not compared */
/* EV-042 CHG-207 (#21): '모델 release(모델 파일 다름 | 임계값 다름 0.50 → 0.60 | …)' — the differing items with the server's detail */
/* EV-050 CHG-W2-03: what differs inside a same condition — " · 벡터 DB <store> — 청크 · 오버랩 다름(…)" */
function notesText(c) { return Object.entries((c && c.notes) || {}).map(([k, x]) => ` · ${esc(k)} — ${esc(x)}`).join(""); }
function differsText(c) { return (c.differs || []).map(k => c.differs_detail && c.differs_detail[k] ? `${k}(${c.differs_detail[k]})` : k).join(", "); }
function scorecardCompare(rows, p) {   // p: the retriever columns read by their role (mword)
  if (!rows) return "";
  if (!rows.length) return `<div class="small muted" style="margin-top:12px" id="sc-cmp-empty">이 평가 suite로 끝난 Run이 없습니다</div>`;
  const cols = new Set();
  for (const r of rows) if (r.scorecard) for (const [n, rules] of Object.entries(r.scorecard.mechanical_checks)) if (!rules.none) for (const k of Object.keys(rules)) cols.add(`${n}.${k}`);
  const br = new Set();
  for (const r of rows) if (r.scorecard) for (const [n, b] of Object.entries(r.scorecard.branch_ratio)) for (const k of Object.keys(b)) if (k !== "none") br.add(`${n}→${k}`);
  const mechCols = [...cols].sort(), brCols = [...br].sort();
  // EV-042 CHG-206: model metric columns on same-condition rows only (Q-1 (가)) — 정확도 · 재현율 <class> (with a value on the reference row) · 정상 오경보
  const ref0 = rows.find(r => r.reference && r.scorecard && r.scorecard.model_metrics);
  const mmCols = ref0 ? Object.entries(ref0.scorecard.model_metrics).flatMap(([n, m]) => [[n, "acc"], ...Object.entries(m.recall || {}).filter(([, x]) => x.none === undefined).map(([k]) => [n, "rec", k]), [n, "fa"]]) : [];
  const ref1 = rows.find(r => r.reference && r.scorecard && r.scorecard.retrieval_metrics);   // EV-043: retriever columns from the reference row
  const rmCols = ref1 ? Object.keys(ref1.scorecard.retrieval_metrics).flatMap(n => [[n, "recall@10"], [n, "hit@10"], [n, "recall@10_human"]]) : [];
  const rmName = ([n, k]) => `리트리버 ${n} ${k === "recall@10_human" ? "사람 확인" : k}`;   // the column key (shown by metricWords)
  const rmCell = (s, [n, k]) => { const m = (s.retrieval_metrics || {})[n]; return m ? mmFrac(m[k]) : "—"; };
  const mmName = ([n, kind, k]) => `모델 ${n} ${kind === "acc" ? "정확도" : kind === "fa" ? "정상 오경보" : `재현율 ${k}`}`;
  const mmCell = (s, [n, kind, k]) => { const m = (s.model_metrics || {})[n]; if (!m) return "—"; return mmFrac(kind === "acc" ? m.accuracy : kind === "fa" ? m.normal_false_alarm : (m.recall || {})[k]); };
  const head = `<tr><th>release</th><th>revision</th><th>조건</th>${mechCols.map(c => `<th>${esc(c)}</th>`).join("")}${brCols.map(c => `<th>${esc(c)}</th>`).join("")}<th>최종 기대값</th><th>node 기대값</th><th>모델 검토자</th><th title="latency">속도</th><th>실패율</th>${mmCols.map(c => `<th data-mm-col>${esc(mmName(c))}</th>`).join("")}${rmCols.map(c => `<th data-rm-col>${mword(p, rmName(c))}</th>`).join("")}</tr>`;
  const cellText = (r, html) => html.replace(/<[^>]+>/g, "").trim();
  const cellsOf = s => {   // EV-064: the reference row may come after other rows (the anchor's row), so its cells are read first
    const m = c => { const [n, k] = [c.slice(0, c.indexOf(".")), c.slice(c.indexOf(".") + 1)]; const r0 = s.mechanical_checks[n]; if (r0 && r0.none) return /도달 안 함/.test(r0.none) ? `<span class="unreached" title="${esc(r0.none)}">도달 안 함</span>` : dash(esc(r0.none)); const x = (r0 || {})[k]; return x ? pct(x) : "—"; };
    const b = c => { const [n, k] = c.split("→"); const r0 = s.branch_ratio[n]; if (!r0) return "—"; if (r0.none) return esc(r0.none); const x = r0[k]; return x ? `${Math.round(x.v * 100)}%` : "0%"; };
    return { m, b, cells: [...mechCols.map(c => [c, m(c)]), ...brCols.map(c => [c, b(c)]), ["최종 기대값", dash(pct(s.fixture_agreement.final))], ["node 기대값", dash(pct(s.fixture_agreement.nodes))],
      ["모델 검토자", s.model_judge.v !== undefined ? s.model_judge.v.toFixed(2) : dash(esc(s.model_judge.none))], ["latency", s.latency.v !== undefined ? s.latency.v.toFixed(1) + "s" : "—"], ["실패율", pct(s.failure_rate)],
      ...mmCols.map(c => [mmName(c), dash(mmCell(s, c))]), ...rmCols.map(c => [rmName(c), dash(rmCell(s, c))])] };
  };
  const refRow = rows.find(r => r.reference && r.scorecard && r.same);
  const refCells = refRow ? Object.fromEntries(cellsOf(refRow.scorecard).cells.map(([k, v]) => [k, cellText(refRow, v)])) : null;
  const body = rows.map(r => {
    const s = r.scorecard, cond = r.reference ? "기준" : r.same ? (r.warning ? `같음 · <span class="muted">${esc(r.warning)}</span>` : "같음") + notesText(r) : `<b>조건 다름 — ${esc(differsText(r))}</b>`;
    if (!s || !r.same) return `<tr data-sc-row="${esc(r.name)}"><td><span style="color:${r.color}">●</span> ${esc(r.name)}</td><td>${esc((r.revision || "").slice(7, 15))}</td><td data-sc-cond>${cond}</td><td colspan="${mechCols.length + brCols.length + 5 + mmCols.length + rmCols.length}" class="muted">${r.same ? "scorecard 없음" : "비교 안 함"}</td></tr>`;
    const { m, b, cells } = cellsOf(s);
    const chg = k => !r.reference && refCells && k !== "latency" && k in refCells && refCells[k] !== cellText(r, cells.find(c => c[0] === k)[1]) ? ' class="chg" data-changed="' + esc(k) + '"' : "";   // EV-022 p.24: a value that differs from the reference row
    return `<tr data-sc-row="${esc(r.name)}"><td><span style="color:${r.color}">●</span> ${esc(r.name)}</td><td>${esc((r.revision || "").slice(7, 15))}</td><td data-sc-cond>${cond}</td>${mechCols.map(c => `<td${chg(c)}>${m(c)}</td>`).join("")}${brCols.map(c => `<td${chg(c)}>${b(c)}</td>`).join("")}` +
      `<td${chg("최종 기대값")}>${dash(pct(s.fixture_agreement.final))}</td><td data-node-expect${chg("node 기대값")}>${dash(pct(s.fixture_agreement.nodes))}${r.no_expect && r.no_expect.length ? `<div class="small" style="color:#b45309" data-no-expect="${esc(r.no_expect.join(","))}">기대값 없음 · ${r.no_expect.map(esc).join(", ")}</div>` : ""}${r.expect_removed && r.expect_removed.length ? `<div class="small" style="color:#b45309" data-expect-removed="${esc(r.expect_removed.join(","))}">기대값 node 없음(지움) · ${r.expect_removed.map(esc).join(", ")}</div>` : ""}</td><td${chg("모델 검토자")}>${s.model_judge.v !== undefined ? s.model_judge.v.toFixed(2) : dash(esc(s.model_judge.none))}</td><td>${s.latency.v !== undefined ? s.latency.v.toFixed(1) + "s" : "—"}</td><td${chg("실패율")}>${pct(s.failure_rate)}</td>${mmCols.map(c => `<td data-mm-cell${!s.model_metrics ? "" : chg(mmName(c))}>${dash(mmCell(s, c))}</td>`).join("")}${rmCols.map(c => `<td data-rm-cell${!s.retrieval_metrics ? "" : chg(rmName(c))}>${dash(rmCell(s, c))}</td>`).join("")}</tr>`;   // EV-042: a Run without model metrics (older) is not a change — the same rule as compare.scorecard_diff
  }).join("");
  return `<div style="margin-top:16px;overflow-x:auto" id="sc-compare"><b title="scorecard 비교 — 칸별로 보고 하나의 점수로 합치지 않습니다 · 같은 평가 suite·모델·벡터 DB·표·runtime일 때만 비교">항목별 점수</b><table style="margin-top:6px;white-space:nowrap;min-width:max-content">${head}${body}</table></div>`;
}

/* ------------------------------------------------------------------ P13 monitors */
function monInputs(p) {   // suites only when every chosen release is a release of that suite's Pack (EV-002 CHG-11)
  const rels = p.releases.filter(r => !UI.monSel || UI.monSel[r.id]);
  return inputsOf(p).filter(i => i.kind !== SUITE || (rels.length && rels.every(r => r.pack && r.pack.id === (p.suites.find(s => s.id === i.id) || {}).pack_id)));
}
function pageMonitor(p) {
  if (!UI.monSel) UI.monSel = Object.fromEntries(p.releases.map(r => [r.id, true]));
  const ins = monInputs(p);
  const bundle = p.bundles[p.bundles.length - 1];
  if (!UI.monInput || !ins.find(i => i.id === UI.monInput)) UI.monInput = bundle && bundle.input && ins.find(i => i.id === bundle.input.id) ? bundle.input.id : ins.length ? ins[0].id : null;   // the last bundle's input first
  const runs = bundle ? bundle.runs.map(uid => p.runs.find(r => r.uid === uid)).filter(Boolean) : [];
  const enl = UI.monEnlarged && runs.find(r => r.id === UI.monEnlarged) ? UI.monEnlarged : null;
  const caseN = runs.length ? Math.max(...runs.map(r => r.cases.length)) : 0;
  const mon = r => {
    const rel = relOf(p, r.rel) || { color: "#9ca3af", name: r.relName };
    const live = r.status === "실행 중";
    const ci = live ? Math.min(r.cur.ci, r.cases.length - 1) : Math.min(UI.monCase, r.cases.length - 1);
    const c = r.cases[ci], hl = live ? highlightFor(r, ci, r.cur.vi) : (c && c.sim ? highlightFor(r, ci) : {});
    const dv = monDiverge(ci);
    if (!live && dv) hl.diverge = new Set(r.uid === dv.base ? dv.onlyBase : r.uid === dv.other ? dv.onlyOther : []);
    const fin = r.cases.filter(x => !["대기", "실행 중"].includes(x.state)).length;
    const failed = r.cases.filter(x => x.state === "실패" || x.state === "timeout");
    let detail = "";
    if (!live && c) {
      if (!c.sim || c.state === "실행 안 됨") detail = `<div class="out">case ${ci + 1}: 실행 안 됨</div>`;
      else detail = (c.state === STOPPED ? `<span class="badge" data-mon-stopped>${STOPPED}</span>` : "") + monFinal(r, c) + `<div class="small muted" data-mon-detail style="overflow-wrap:anywhere">${c.sim.tools.length ? `<span data-tools>tool 호출: ${c.sim.tools.map(t => `${esc(t.name)}(${esc(JSON.stringify(t.args ?? {}))}) → ${t.result == null ? "결과 없음" : esc(typeof t.result === "string" ? t.result : JSON.stringify(t.result))}`).join(", ")}</span> · ` : ""}경로 ${hv("▸ 지나간 노드", c.sim.visits.map(v => v.node).join("→"))} · 정확도: ${r.input.kind === SUITE ? hv("값 없음", SUITE_NOTE) : r.input.id === "data" ? (c.rule && typeof c.rule === "object" ? (c.rule.match ? "정답 일치" : "불일치") : "—") : "검증데이터 없음"} · ${(c.sim.ms / 1000).toFixed(1)}s · ${c.sim.tokens == null ? "token —" : `${(c.sim.tokens / 1000).toFixed(1)}k token`}</div>`;
    } else if (live && c) detail = `<div class="out" data-live>출력(실시간): ${esc((c.events[c.events.length - 1] && JSON.stringify(c.events[c.events.length - 1].output)) || "…")}▌</div>`;
    const failNote = failed.length && !live ? `<div class="small" style="color:#c4320a" data-partial>일부 실패 · ${failed.map(x => `case ${x.i + 1} 오류 node ${x.sim.error.node}`).join(", ")}</div>` : "";
    return `<div class="mon" data-monitor="${esc(rel.name)}" data-run="${r.id}" data-status="${r.status}" style="border-top:3px solid ${rel.color};min-height:280px"><div class="mh"><b style="color:${rel.color}">${esc(rel.name)}</b><span class="small muted">${r.id} · ${r.status}${live ? ` case ${fin + 1}/${r.cases.length}` : ""}</span><span class="sp"></span>` +
      `<span class="link small" data-act="mon-enlarge" data-run="${r.id}">${enl === r.id ? "원래 배열" : "크게 보기"}</span>${live ? `<span class="btn d" style="padding:1px 6px" data-act="mon-cancel" data-run="${r.id}">이 버전 취소</span>` : ""}</div>` +
      `<div class="mb">${graphSVG(branchNamed(r.graph, rel.branchLabels), hl, enl === r.id ? 1.3 : 0.75)}${detail}${failNote}</div></div>`;   // p.25: branch names as on the workbench
  };
  monDetails(p, runs);
  let center;
  if (!runs.length) center = `<div class="small muted" id="mon-empty">release와 실행 입력을 고르고 '함께 실행'을 누르세요</div>`;
  else if (enl) center = `<div class="small muted" style="margin-bottom:6px">누른 모니터가 위쪽 절반 전체, 나머지는 아래에 AC-35 배열</div><div id="monarea"><div id="mon-top">${mon(runs.find(r => r.id === enl))}</div><div class="monarea" id="mon-rest" style="margin-top:10px">${runs.filter(r => r.id !== enl).map(mon).join("")}</div></div>`;
  else center = `<div class="small muted" style="margin-bottom:6px" id="mon-note">${runs.length}개 버전 · ${runs.filter(r => r.status !== "실행 중").length}개 끝남</div><div class="monarea" id="monarea">${runs.map(mon).join("")}</div>`;
  const chk = p.releases.map(r => `<div class="lrow"><input type="checkbox" data-act="mon-rel" data-rel="${r.id}"${UI.monSel[r.id] ? " checked" : ""} style="accent-color:${r.color}"><span class="small">${esc(r.name)}</span></div>`).join("");
  const monLabel = i => `${esc(i.name)}${i.tag ? ` · ${i.tag}` : ""} · ${i.n} case`;   // EV-013 CHG-74: also written in full under the select
  const insp = `<h3>release</h3>${chk}<h3 style="margin-top:8px">실행 입력</h3>` + (ins.length ? `<select id="mon-input" data-act="mon-input">${ins.map(i => `<option value="${i.id}"${i.id === UI.monInput ? " selected" : ""}>${monLabel(i)}</option>`).join("")}</select><div class="small sel-full" id="mon-input-full">${monLabel(ins.find(i => i.id === UI.monInput) || ins[0])}</div>` :
    `<div class="rg err small" id="mon-no-input"><b>실행에 필요한 입력값이 없습니다</b><div class="row" style="margin-top:6px"><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=gen">입력값 생성</span><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=file">파일 올리기</span><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=direct">직접 쓰기</span><span class="btn" data-act="go" data-to="#/p/${p.id}/inputs?seg=data">검증데이터 올리기</span></div></div>`) +
    `<h3 style="margin-top:8px">case</h3><select id="mon-case" data-act="mon-case">${Array.from({ length: caseN }, (_, i) => `<option value="${i}"${i === UI.monCase ? " selected" : ""}>${i + 1}</option>`).join("")}</select>` +
    (UI.monErr ? `<div class="err-under" id="mon-err">release를 2개 이상 고르세요</div>` : "") + (UI.monNotice ? `<div class="err-under" id="mon-notice">${esc(UI.monNotice)}</div>` : "") +
    `<div class="row" style="margin-top:10px"><span class="btn p${ins.length ? "" : " off"}" data-act="mon-start" id="mon-start">함께 실행</span><span class="btn d" data-act="mon-cancel-all" id="mon-cancel-all">모두 취소</span></div>`;
  return shell(p, "", crumbP(p, "비교 › <b>모니터</b>"), workbench(p, "compare", center + monReasons(p, runs), [["mon", "모니터 설정"]], "mon", insp));
}
/* the first two finished Runs of the bundle, compared by the server (same details as P09: parallel siblings in one order) */
function monDetails(p, runs) {
  const [a, b] = runs;
  const pack = r => { const rel = relOf(p, r.rel); return !!(rel && rel.pack); };   // S-17: Agent Pack versions only
  if (!a || !b || a.status === "실행 중" || b.status === "실행 중" || !pack(a) || !pack(b)) { UI.monDet = null; return; }
  const key = `${a.uid}|${b.uid}`;
  if (UI.monDet && UI.monDet.key === key) return;
  UI.monDet = { key, base: a.uid, other: b.uid, data: null };
  api("GET", `/api/projects/${p.id}/compare/details?base=${encodeURIComponent(a.uid)}&other=${encodeURIComponent(b.uid)}`).then(d => { if (UI.monDet && UI.monDet.key === key) { UI.monDet.data = d; render(); } }).catch(() => {});
}
/* H2 (manual p.25): for two Agent Pack Runs the long report is folded to its first line so the reasons table sits under the graphs */
function monFinal(run, c) {
  const full = c.sim.final || (c.sim.error ? "실패 · 오류 node " + c.sim.error.node : "—");
  if (!UI.monDet || UI.monOpen === run.uid) return `<div class="out" data-final>${esc(full)}${UI.monDet ? ` <span class="link small" data-act="mon-out" data-run="${run.uid}">접기</span>` : ""}</div>`;
  const first = String(full).split("\n").find(x => x.trim()) || full;
  return `<div class="out" data-final data-folded="1">${esc(first)} <span class="link small" data-act="mon-out" data-run="${run.uid}" id="mon-out-${run.uid}">▸ 보고서 원문</span></div>`;
}
function monDraftKind(run, ci, node) {
  const v = ((run.cases[ci] || {}).visits || []).find(x => x.node === node), out = v && v.output;
  if (!out || typeof out !== "object") return node || "—";
  const inner = Object.values(out).find(x => x && typeof x === "object" && x.kind) || out;
  return inner.kind ? `${inner.kind}(${node})` : node;
}
function monDiverge(ci) {
  const m = UI.monDet, row = m && m.data && m.data.cases[ci];
  if (!row) return null;
  const bp = row.base.path, op = row.other.path;
  let i = 0;
  while (i < bp.length && i < op.length && bp[i] === op[i]) i++;
  if (i === bp.length && i === op.length) return null;
  return { base: m.base, other: m.other, onlyBase: row.path_diff.only_base.length ? row.path_diff.only_base : bp.slice(i, i + 1), onlyOther: row.path_diff.only_other.length ? row.path_diff.only_other : op.slice(i, i + 1),
           at: bp[i - 1] || "START", next: [bp[i] || "END", op[i] || "END"], row };
}
function monChecks(run, ci) {
  const c = run.cases[ci], out = {};
  for (const v of (c && c.visits) || []) {
    const chk = [...(v.checks || []), ...(v.expect_checks || []).map(x => ({ rule: "expect." + x.check, passed: x.passed, detail: x.detail }))].filter(x => x.passed !== null && x.passed !== undefined);
    if (chk.length) out[v.node] = chk.every(x => x.passed) ? "통과" : "불통과(" + chk.filter(x => !x.passed).map(x => `${x.rule}${x.detail ? ": " + (typeof x.detail === "string" ? x.detail : JSON.stringify(x.detail)) : ""}`).join(" · ") + ")";
  }
  return out;
}
function monReasons(p, runs) {
  const m = UI.monDet;
  if (!m || !m.data) return "";
  const ci = Math.min(UI.monCase, m.data.cases.length - 1), row = m.data.cases[ci], dv = monDiverge(ci);
  const a = runs.find(r => r.uid === m.base), b = runs.find(r => r.uid === m.other);
  if (!row || !a || !b) return "";
  const ca = monChecks(a, ci), cb = monChecks(b, ci);
  const checks = [...new Set([...Object.keys(ca), ...Object.keys(cb)])].filter(n => ca[n] !== cb[n]);
  const rows = (dv ? [`<tr class="chg" data-reason="next"><td>${hv(`${dv.at} 뒤 간 곳`, `${dv.at} 다음`)}</td><td>${esc(dv.next[0])}</td><td>${esc(dv.next[1])}</td></tr>`,
    `<tr data-reason="draft-kind"><td>보고서 초안 종류</td><td>${esc(monDraftKind(a, ci, dv.next[0]))}</td><td class="chg">${esc(monDraftKind(b, ci, dv.next[1]))}</td></tr>`] : []).concat(
    checks.map(n => `<tr data-reason="check" data-node="${esc(n)}"><td title="${esc(n)} 검사 결과">${esc(n)} 검사</td><td>${checkWords(ca[n] || "실행 안 됨")}</td><td class="chg">${checkWords(cb[n] || "실행 안 됨")}</td></tr>`),
    [`<tr data-reason="final"><td>최종 출력</td><td colspan="2">${row.final_differs ? "다름" : "같음"}</td></tr>`]);
  return `<div style="margin-top:10px;overflow-x:auto" id="mon-reasons"><div class="small muted">case ${esc(row.case_id || ci + 1)} · 빨간 노드 = 갈린 곳${dv ? "" : " · 경로 같음"}</div>` +
    `<table><tr><th>case ${esc(row.case_id || ci + 1)}</th><th>${esc(a.relName)} ${esc(a.id)}</th><th>${esc(b.relName)} ${esc(b.id)}</th></tr>${rows.join("")}</table></div>`;
}
function layoutMonitors() {
  // AC-35/36/37: column count by monitor count, reduced while a monitor would be narrower than 360px; min height 280px; no horizontal scroll
  const fit = (el, n) => {
    if (!el) return;
    const W = el.clientWidth, desired = n <= 3 ? 1 : n === 4 ? 2 : 3;
    const cols = Math.max(1, Math.min(desired, Math.floor((W + 10) / (360 + 10))));
    el.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
    el.dataset.cols = cols;
  };
  const area = document.getElementById("monarea"), rest = document.getElementById("mon-rest");
  if (rest) {
    fit(rest, rest.children.length);
    const top = document.getElementById("mon-top");
    const h = Math.max(280, Math.round((window.innerHeight - 150) / 2));
    top.firstElementChild.style.minHeight = h + "px";
  } else if (area) fit(area, area.children.length);
  const note = document.getElementById("mon-note");
  if (note && area) { const base = note.dataset.base || (note.dataset.base = note.textContent); note.textContent = window.innerWidth <= 1100 ? `좁은 화면: ${area.dataset.cols}열 · 아래로 스크롤 · 설정 칸은 아래로 · ${base}` : base; }
}

/* ------------------------------------------------------------------ P12 inputs */
function pageInputs(p) {
  const ins = inputsOf(p);
  if (!UI.setId || !ins.find(i => i.id === UI.setId)) UI.setId = ins.length ? ins[ins.length - 1].id : null;
  const cur = ins.find(i => i.id === UI.setId);
  const inRel = (p.releases || []).find(r => r.graph && r.graph.nodes.some(n => n.kind)) || null;   // EV-075 (v2 22쪽): suite by role words
  const table = `<table id="sets-table"><tr><th>종류</th><th>이름</th><th>case</th><th>출처</th></tr>` + (ins.length ? ins.map(i => `<tr data-act="pick-set" data-id="${i.id}" data-set="${esc(i.name)}"${i.id === UI.setId ? ' style="background:#eff6ff"' : ""}><td>${i.kind}</td><td><b>${suiteWords(inRel, i.name) !== i.name ? hv(suiteWords(inRel, i.name), i.name) : esc(i.name)}</b>${i.tag ? `<br><span class="small muted">${hv(String(i.tag).replace(/^S-\d+ · (\d\d-\d\d) \d\d:\d\d 등록/, "$1 등록"), i.tag)}</span>` : ""}</td><td data-n>${i.n}</td><td>${/^Pack \S+ · evals\//.test(i.src || "") ? hv("Pack 평가 세트", i.src) : esc(i.src)}</td></tr>`).join("") : `<tr><td colspan="4" class="muted">실행 입력이 없습니다</td></tr>`) + "</table>";
  const cases = cur ? `<h3 style="margin-top:12px" title="${esc(cur.name)}${cur.tag ? ` · ${esc(cur.tag)}` : ""}">${esc(suiteWords(inRel, cur.name))}</h3><table><tr><th>#</th><th>입력</th></tr>${casesOfInput(p, cur).map((c, i) => `<tr><td>${i + 1}</td><td>${typeof c.input === "string" ? esc(c.input) : fold(`in-${cur.id}-${i}`, inputSummary(c.input), fold.pre(c.input))}</td></tr>`).join("")}</table>` : "";
  const seg = `<div class="seg">${[["gen", "임의 생성"], ["file", "파일"], ["direct", "직접"], ["data", "검증데이터"], ["suite", "평가 suite"]].map(([k, l]) => `<span class="${UI.seg === k ? "on" : ""}" data-act="seg" data-k="${k}">${l}</span>`).join("")}</div>`;
  let form = "";
  if (UI.seg === "gen") {
    const rel = relOf(p, UI.genRel) || relOf(p, UI.lastRel) || p.releases[0];
    if (!p.releases.length) form = `<div class="small muted" style="margin-top:8px">release를 먼저 등록하세요</div>`;
    else {
      UI.genRel = rel.id;
      const L = projLlm(p.scoring), keyOk = hasKey(L.id);   // the Project's provider key, checked before generating (AC-32-C3, EV-005)
      form = (!keyOk ? `<div class="banner err small" style="margin-top:8px" id="gen-nokey"><b>${esc(L.name)} API 키를 찾을 수 없음</b> — 생성할 수 없습니다. 파일 올리기·직접 쓰기는 됩니다.<span class="sp"></span><span class="btn" data-act="go" data-to="#/settings">설정 열기</span></div>` : "") +
        `<div class="small" style="margin-top:8px">맞출 release: <select id="gen-rel" data-act="gen-rel">${p.releases.map(r => `<option value="${r.id}"${r.id === rel.id ? " selected" : ""}>${esc(r.name)}${r.id === UI.lastRel ? " (지금 탭)" : ""}</option>`).join("")}</select><br>개수 <input id="gen-count" value="${UI.genCount || 5}" style="width:44px"> (1~20)<br>설명(선택) <input id="gen-desc" style="width:100%"></div><div class="small muted" id="gen-keyline">${esc(L.name)} 키 · ${esc(S.settings.mode_label)} · 결과는 자동으로 세트로 남음 · 입력 필드: <span id="gen-field">${esc(rel.graph.input_field)}</span></div>` +
        (rel.pack ? `<div class="note" style="margin-top:6px" id="gen-pack" title="Agent Pack release는 입력값 생성을 아직 지원하지 않습니다 — 평가 suite·파일·직접 쓰기를 쓰세요">이 버전은 입력값 자동 생성 안 됨</div>` : "") +
        `<div class="row" style="margin-top:8px"><span class="sp"></span><span class="btn p${keyOk && !UI.genBusy && !rel.pack ? "" : " off"}" data-act="gen" id="gen-btn">${UI.genBusy ? "생성 중…" : "생성"}</span></div>`;
      if (UI.gen) {
        const g = UI.gen, okN = g.rows.filter(r => r.ok).length;
        form += `<div data-region="R-06" style="margin-top:8px" id="gen-result"><table><tr><th>#</th><th>생성된 입력</th><th>결과</th></tr>${g.rows.map((r, i) => `<tr><td>${i + 1}</td><td>${esc(r.ok ? JSON.stringify(r.value) : r.raw)}</td><td${r.ok ? "" : ' class="bad"'}>${r.ok ? "남김" : esc(r.reason)}</td></tr>`).join("")}</table>` +
          (okN ? `<div class="note" style="margin-top:6px" id="gen-note">${g.rows.length}개 중 ${okN}개를 만들었고 ${g.rows.length - okN}개는 뺐습니다 · ${okN}개짜리 입력값 세트 '${esc(g.setName)}'로 남았습니다</div>` :
            `<div class="rg err small" style="margin-top:6px" id="gen-allfail"><b>하나도 만들지 못했습니다</b> — ${esc(g.rows[0].reason.replace(/^만들지 못했습니다 \((.*)\)$/, "$1"))} · 입력값 세트는 바뀌지 않았습니다 <span class="btn" data-act="gen-retry" id="gen-retry">다시 생성</span></div>`) + "</div>";
      }
    }
  }
  if (UI.seg === "file") form = `<div style="margin-top:8px" class="small">입력만 담은 JSON/CSV <input type="file" id="set-file" accept=".json,.csv,.txt"></div>` + (UI.fileErr ? `<div class="rg err small" id="set-file-error" style="margin-top:6px"><b>올릴 수 없음</b> — ${esc(UI.fileErr)}</div>` : "");
  if (UI.seg === "direct") form = `<div class="field" style="margin-top:8px"><label>입력값 (한 줄에 하나)</label><textarea rows="5" id="direct-text">${esc(UI.direct)}</textarea></div><div class="row"><span class="sp"></span><span class="btn" data-act="direct-cancel" id="direct-cancel">취소</span><span class="btn p" data-act="direct-save" id="direct-save">저장</span></div>`;
  if (UI.seg === "data") form = p.data ? `<div class="small" style="margin-top:8px">검증데이터 ${esc(p.data.name)} · ${p.data.cases.length} case가 있습니다</div>` : `<div style="margin-top:8px" class="small">검증데이터(input·expected 필수, forbidden 선택) <input type="file" id="data-file" accept=".json,.csv,.txt"></div>` + (UI.dataErr ? `<div class="rg err small" id="data-file-error" style="margin-top:6px"><b>올릴 수 없음</b> — ${esc(UI.dataErr)}</div>` : "");
  if (UI.seg === "suite") {   // EV-002 CHG-11: evals/<suite>/ of a Pack release's work folder
    const packs = p.releases.filter(r => r.pack);
    if (!packs.length) form = `<div class="small muted" style="margin-top:8px" id="suite-no-pack">평가 suite는 Agent Pack release가 있어야 등록할 수 있습니다</div>`;
    else {
      const rel = packs.find(r => r.id === UI.suiteRel) || packs[0];
      if (UI.suiteRel !== rel.id || !UI.suiteAvail) { UI.suiteRel = rel.id; UI.suiteAvail = { rel: rel.id, list: null };
        api("GET", `/api/projects/${p.id}/releases/${rel.id}/suites-available`).then(d => { if (UI.suiteAvail && UI.suiteAvail.rel === rel.id) { UI.suiteAvail.list = d.suites; render(); } }).catch(e => { UI.suiteErr = e.message; render(); }); }
      const list = UI.suiteAvail.list;
      form = `<div class="small" style="margin-top:8px">Pack release <select id="suite-rel" data-act="suite-rel">${packs.map(r => `<option value="${r.id}"${r.id === rel.id ? " selected" : ""}>${esc(r.name)} · ${esc(r.pack.id)}</option>`).join("")}</select></div>` +
        `<div class="small" style="margin-top:6px">evals/ <select id="suite-name">${(list || []).map(n => `<option value="${esc(n)}"${n === UI.suiteName ? " selected" : ""}>${esc(n)}</option>`).join("")}</select>${list && !list.length ? ` <span class="muted" id="suite-none">작업 폴더 evals/에 suite.yaml이 있는 폴더가 없습니다</span>` : ""}</div>` +
        `<div class="small muted">작업 폴더는 읽기만 하고 사본을 보관합니다 · 등록 뒤 바뀌어도 이 suite는 그대로</div>` +
        (UI.suiteErr ? `<div class="rg err small" id="suite-error">${esc(UI.suiteErr)}</div>` : "") +
        (UI.suiteDone ? `<div class="note small" id="suite-done">등록됨 · ${esc(UI.suiteDone.name)}${(si => si ? ` · S-${si.seq}` : "")(suiteInfo(p, UI.suiteDone.id))} · suite_revision ${esc(UI.suiteDone.suite_revision.slice(7, 15))} · dataset_hash ${esc(UI.suiteDone.dataset_hash.slice(7, 15))} · ${UI.suiteDone.cases.length} case · review_policy ${esc(UI.suiteDone.review_policy.default)}</div>` : "") +
        `<div class="row" style="margin-top:8px"><span class="sp"></span><span class="btn p${list && list.length && !UI.suiteBusy ? "" : " off"}" data-act="suite-add" id="suite-add">등록</span></div>`;
    }
  }
  const insp = `<h3>입력값 만들기</h3>${seg}${form}${promoSection(p)}`;
  return shell(p, "", crumbP(p, "<b>실행 입력</b>"), workbench(p, "inputs", table + cases, [["make", "만들기"]], "make", insp));
}
/* EV-025 OPS-3 (manual p.30 ③, CHG-126): 올린 case — operational Runs put up on the 운영 Run screen — added to a registered suite S-k
   as a new suite S-n (S-k stays as it is). The usual same-condition rule applies: run the base version on S-n too before comparing. */
function promoSection(p) {
  const ups = p.promotions || [];
  if (!ups.length) return "";
  const packOf = x => (relOf(p, x.release_id) || { pack: {} }).pack.id, bases = (p.suites || []);
  const base = bases.find(s => s.id === UI.promoBase) || bases[0];
  if (base) UI.promoBase = base.id;
  const fit = base ? ups.filter(x => packOf(x) === base.pack_id) : [];
  UI.promoPick = UI.promoPick || {};
  const rows = ups.map(x => { const ok = fit.includes(x), on = ok && UI.promoPick[x.promotion_id];
    return `<div data-promotion="${x.promotion_id}"><label><input type="checkbox" data-act="promo-pick" data-id="${x.promotion_id}"${on ? " checked" : ""}${ok ? "" : " disabled"}> ${esc(x.source_run_id.slice(0, 15))}… · ${esc((x.observed || {}).input || "")} · ${esc((x.observed || {}).result || "")} · ${esc(x.promoted_by)}</label>${ok ? "" : ` <span class="muted">Pack이 달라 이 suite에 더할 수 없음</span>`}</div>`; }).join("");
  const n = fit.filter(x => UI.promoPick[x.promotion_id]).length, d = UI.promoDone, si = d && suiteInfo(p, d.id);
  return `<div class="vt small" id="promo-box" style="margin-top:12px"><b id="promo-count">올린 case ${ups.length}개</b> <span class="muted">운영 Run 화면에서 올린 입력 · 기대값 없음</span>${rows}` +
    (base ? `<div style="margin-top:6px">기준 suite <select id="promo-base">${bases.map(s => `<option value="${s.id}"${s.id === base.id ? " selected" : ""}>${esc((suiteInfo(p, s.id) || {}).tag || s.name)} · ${esc(s.name)}</option>`).join("")}</select></div>` +
      `<div class="row" style="margin-top:6px"><span class="sp"></span><span class="btn p${n && !UI.promoBusy ? "" : " off"}" data-act="promo-suite-add" id="promo-suite-add">올린 case를 더해 새 suite로 등록</span></div>`
      : `<div class="muted" id="promo-no-base">기준이 될 평가 suite를 먼저 등록하세요</div>`) +
    (UI.promoErr ? `<div class="rg err small" id="promo-error">${esc(UI.promoErr)}</div>` : "") +
    (d ? `<div class="note small" id="promo-done">등록됨 · ${esc(si ? si.tag : d.name)} · ${d.cases.length} case · 비교하려면 기준 버전도 이 suite로 실행하세요</div>` : "") + `</div>`;
}

/* ------------------------------------------------------------------ P11 briefs */
const BRIEF_FIELDS = [["purpose", "목적", "무엇을 위해 바꾸는가"], ["change", "변경점", "현재 대비 바꿀 점"], ["io", "입출력 계약", "입력·출력 형식"], ["eval", "평가 기준", "무엇으로 판단하나"], ["forbid", "금지 행동", "하면 안 되는 것"], ["fixture", "필요한 예시", "실행에 필요한 예시"], ["trace", "남길 기록 형식", "남길 기록"]];   // EV-075 (v2 27쪽)
function pageBriefs(p, bid) {
  const b = bid ? p.briefs.find(x => x.id === bid) : null;
  const d = UI.briefDraft || (UI.briefDraft = b ? { ...b.fields, title: b.title, id: b.id } : { title: "", id: null });
  const center = `<div class="row"><input id="brief-title" style="font-size:18px;border:0;padding:0;flex:1;font-weight:700" placeholder="요청서 제목" value="${esc(d.title)}"><span class="sp"></span><span class="btn" data-act="brief-cancel" id="brief-cancel">취소</span><span class="btn p" data-act="brief-save" id="brief-save">요청서 저장</span></div><div class="note" id="brief-note">${hv("기록용 — 실행되지 않음", "요청서는 저장·열람용 기록입니다. 제품은 요청서로 release를 만들거나 코드를 만들거나 실행하지 않습니다.")}</div>` +
    BRIEF_FIELDS.map(([k, l, h]) => `<div class="sect"><div><h3>${l}</h3><div class="small muted">${h}</div></div><textarea class="box" rows="2" id="brief-${k}" style="width:100%">${esc(d[k] || "")}</textarea></div>`).join("");
  const insp = `<h3>요청서</h3><div id="brief-list">${p.briefs.map(x => `<div class="lrow${b && x.id === b.id ? " on" : ""}" data-act="go" data-to="#/p/${p.id}/briefs/${x.id}"><span class="small"><b>${esc(x.title)}</b> · ${x.at}</span></div>`).join("") || '<div class="small muted">아직 없음</div>'}</div><span class="link small" data-act="brief-new" id="brief-new">+ 새 요청서</span>`;
  return shell(p, "", crumbP(p, "<b>요청서</b>"), workbench(p, "briefs", center, [["brief", "요청서"]], "brief", insp));
}

/* ------------------------------------------------------------------ P10 settings */
function pageSettings() {
  const st = S.settings || { providers: [], mode_label: "" };
  const nav = `<section class="rg flat" data-region="R-01"><div class="toc"><a class="on">LLMOps 키</a><a>LLM 호출 모드</a><a>키 가리기</a><a>Agent 키 안내</a></div></section>`;
  // one card per provider with its own status and its own registration (EV-005 CHG-26)
  const card = x => `<div class="keycard" id="key-card-${x.id}" data-provider="${x.id}"><div class="row">${PROVIDER_ICON[x.id] || ""}<b>${esc(x.title)}</b></div>` +
    `<div><span class="badge${x.status === "none" ? "" : " ok"}" id="key-status-${x.id}">${esc(x.status_label)}</span></div>` +
    `<div class="small muted">${hv("환경 변수", x.env.join(" · 또는 "))}</div>` +
    `<div class="row" style="margin-top:auto"><span class="btn" data-act="key-open" data-provider="${x.id}" id="key-open-${x.id}">${x.status === "temp" ? "바꾸기" : `${esc(x.name)} API 키 등록`}</span>` +
    (x.status === "temp" ? `<span class="btn" data-act="key-clear" data-provider="${x.id}" id="key-clear-${x.id}">지우기</span>` : "") + `</div></div>`;
  const cont = `<section class="rg flat" data-region="R-02"><h1>LLMOps 키</h1><div class="small muted" style="margin:4px 0 12px">${hv("채점 · 입력값 만들기에 쓰는 키 (값은 안 보임)", "LLM 채점·입력값 생성·평가 suite의 모델 검토에 씁니다. Project마다 provider를 고릅니다. 값은 화면에 보이지 않습니다.")}</div>` +
    `<div class="keycards" id="key-cards">${(st.providers || []).map(card).join("")}</div>` +
    `<div class="sect"><div><h3>LLM 호출 모드</h3></div><div class="small" id="mode-status">${hv(`${st.mode_label === "가짜 provider" ? "가짜 모델" : "실제 모델"} (서버 설정)`, `${st.mode_label} (표시만 · 제품 밖 설정으로만 바뀜)`)}</div></div>` +
    `<div class="sect"><div><h3>키 가리기</h3></div><div class="small muted" id="mask-note">${hv("코드에 직접 적은 키는 가려지지 않음", "가리기는 LLMOps 키와 흔한 키 형식에만 적용되며, 업로드한 코드 파일 안에 직접 적힌 키는 복사본에 남을 수 있습니다.")}</div></div>` +
    `<div class="sect" style="border:0"><div><h3>Agent 키 안내</h3></div><div class="small muted">Agent는 자기 키를 씀 · LLMOps 키는 안 넘김</div></div></section>`;
  return shell(null, "settings", "<b>설정</b>", `<div class="split" style="grid-template-columns:200px 1fr">${nav}<div style="max-width:820px">${cont}</div></div>`);
}

/* ------------------------------------------------------------------ router + render */
function route() {
  const [path, qs] = location.hash.replace(/^#/, "").split("?");
  const q = new URLSearchParams(qs || "");
  const s = path.split("/").filter(Boolean);
  return { s, q };
}
let lastRouteKey = "", lastHash = "";
function render() {
  const { s, q } = route();
  const app = document.getElementById("app"), ov = document.getElementById("overlay");
  let html = "", over = "";
  const key = s.join("/");
  if (key !== lastRouteKey) {
    UI.runNotice = null; UI.verdictErr = false; UI.node = null; UI.cmpStale = true; UI.briefDraft = null;
    if (s[0] === "p" && s.length === 2) { UI.itab = "project"; UI.runId = null; }
    if (s[0] === "p" && (s[2] === "r" || (!s[2] && s.length === 2))) reread(s[1], s[3]);
    lastRouteKey = key;
  }
  if (!s.length || s[0] === "new") {
    html = pageProjects();
    if (s[0] === "new") { UI.newp = UI.newp || { name: "", desc: "", open: "data", score: "both", pass: "0.80", below: "hold", data: null, dataErr: null, prov: "openai", llmName: (provOf("openai") || {}).default_model || "gpt-4.1-mini" }; over = newProjectModal(); }
  } else if (s[0] === "settings") { html = pageSettings(); if (UI.keyModal) over = keyModal(); }
  else if (s[0] === "p") {
    const p = proj(s[1]);
    if (!p) { go("#/"); return; }
    const sub = s[2];
    if (!sub) html = pageProject(p, null);
    else if (sub === "r" && s[4] === "edit" && s[5] === "diff") { UI.lastRel = s[3]; html = pageEditDiff(p, s[3]); }   // EV-022 manual p.21
    else if (sub === "r") { UI.lastRel = s[3]; UI.opsRun = s[4] === "run" && OpsRun.isOps(s[5]) ? s[5] : null; if (s[4] === "run" && s[5] && !UI.opsRun) { UI.runId = s[5]; } html = pageProject(p, s[3]); }
    else if (sub === "new-release") html = pageRegister(p);
    else if (sub === "auto") html = pageAuto(p, s[3]);   // EV-030 CHG-156 · EV-035 CHG-189 (#/p/{pid}/auto/A-n)
    else if (!["inputs", "compare", "monitor", "briefs"].includes(sub)) html = pendingPage(p, sub);
    else if (sub === "compare") html = pageCompare(p, s[3]);   // EV-035 CHG-184: #/p/{pid}/compare/A-n = the comparison the wheel opened
    else if (sub === "monitor") html = pageMonitor(p);
    else if (sub === "inputs") { if (q.get("seg") && location.hash !== lastHash) { UI.seg = q.get("seg"); UI.gen = null; } html = pageInputs(p); }
    else if (sub === "briefs") html = pageBriefs(p, s[3]);
    // the numbers come from the server's delete summary (the same count the delete removes)
    if (UI.modal && UI.modal.type === "proj-del" && UI.modal.c) { const c = UI.modal.c; over = delModal(`「${esc(p.name)}」를 삭제할까요?`, `${hv(`release ${c.release} · Run ${c.run} · 판정 ${c.verdict} 함께 지워짐`, `release ${c.release} · Run ${c.run} · 판정 ${c.verdict} · 업로드 복사본 ${c.upload_copy}${c.pack_copy ? ` · Pack 사본 ${c.pack_copy}` : ""}${c.eval_suite ? ` · 평가 suite ${c.eval_suite}` : ""}이 함께 지워집니다. 로컬 원본 코드는 그대로입니다.`)} <b style="color:#dc2626">되돌릴 수 없습니다.</b>`, "proj-del-ok"); }   // EV-075 (v2 9쪽)
    if (UI.modal && UI.modal.type === "rel-del" && UI.modal.c) { const r = relOf(p, UI.modal.rel), c = UI.modal.c; over = delModal(`release 「${esc(r.name)}」를 삭제할까요?`, `Run ${c.run} · 판정 ${c.verdict}${c.upload_copy ? ` · 업로드 복사본 ${c.upload_copy}` : ""}${c.pack_copy ? ` · Pack 사본 ${c.pack_copy}` : ""}이 함께 지워집니다. <b style="color:#dc2626">되돌릴 수 없습니다.</b> 로컬 원본 코드는 그대로입니다.`, "rel-del-ok"); }
  }
    if (s[0] === "p" && UI.confirm) { const cp = proj(s[1]); if (cp) over = confirmModal(cp) || over; }   // EV-022 manual p.22
    if (s[0] === "p" && UI.deploy) { const cp = proj(s[1]); if (cp) over = deployModal(cp) || over; }   // EV-022 manual p.28
    if (s[0] === "p" && UI.rollback) { const cp = proj(s[1]); if (cp) over = rollbackModal(cp) || over; }   // EV-SUI-01 D-140
    if (s[0] === "p" && s[2] === "r" && UI.opsRun && UI.opsPromote) { const cp = proj(s[1]), rel = cp && relOf(cp, s[3]); if (rel) over = OpsRun.promoteWindow(edCtx(cp, rel), rel) || over; }   // EV-025 OPS-3 p.30 ③
  patch(app, html); patch(ov, over);
  lastHash = location.hash;
  layoutMonitors();
}
/* opening a release re-reads local-path code so the run flow follows the current code (AC-08) */
function reread(pid, rid) {
  const p = proj(pid); if (!p) return;
  const rel = relOf(p, rid) || p.releases[0];
  if (!rel || rel.how !== "로컬 경로") return;
  api("POST", `/api/projects/${pid}/releases/${rel.id}/reread`).then(nr => {
    if (nr.sourceKind === "agent_pack") UI.workChanged[nr.id] = nr.workChanged ? (nr.workSameAs || true) : false;   // kept on screen across /api/state refreshes (EV-002 CHG-02)
    const i = p.releases.findIndex(x => x.id === nr.id); if (i >= 0) { p.releases[i] = { ...p.releases[i], ...nr }; render(); } }).catch(() => {});   // EV-022: keep the version view
}
/* screens whose slice is not built yet (plan v17 slice order) */
/* EV-030 AC-4 (CHG-156, supplement A5 · 08/26): the '✦ 자동 개선' tab — wheels by themselves (web/autocycle.js) */
function pageAuto(p, name) {
  if (!(p.auto && p.auto.on)) return pendingPage(p, "자동 개선");
  const cx = { p, ui: UI, api, esc, render, go, graph: (g, hl, rel) => graphSVG(rel ? branchNamed(g, rel.branchLabels) : g, hl) };   // D-459: names as on the workbench
  if (name) {   // EV-035 CHG-189: one wheel — its bar, the graph (분홍 신호 · 보라 책임), the responsible node and the hypotheses
    AutoCycle.loadDetail(cx, name);
    return shell(p, "", crumbP(p, `<span data-act="go" data-to="#/p/${p.id}/auto">자동 개선</span> › <b>${esc(name)}</b>`),
      workbench(p, "auto", AutoCycle.centerDetail(cx), [["wheel", "개선"]], "wheel", AutoCycle.inspDetail(cx)));
  }
  AutoCycle.load(cx);
  return shell(p, "", crumbP(p, "<b>자동 개선</b>"), workbench(p, "auto", AutoCycle.center(cx), [["wheel", "개선"]], "wheel", AutoCycle.insp(cx)));
}
function pendingPage(p, what) {
  const body = `<section class="rg band" id="pending-screen"><b>이 화면은 이후 구현 단계에서 연결됩니다</b> <span class="small muted">(${esc(what)})</span></section>`;
  return p ? shell(p, "", crumbP(p), body) : shell(null, what === "설정" ? "settings" : "", `<b>${esc(what)}</b>`, body);
}
/* in-place DOM patch: keeps elements (and clicks, focus, typed values) stable while live runs re-render every tick */
function patch(root, html) {
  const next = document.createElement("div");
  next.innerHTML = html;
  morphChildren(root, next);
  if (typeof markMore === "function") markMore();   // ORCH 231 ①: the morph drops data-more · --fade-at the drawing does not carry — read again
}
function morphChildren(a, b) {
  const ac = [...a.childNodes], bc = [...b.childNodes];
  bc.forEach((y, i) => {
    const x = ac[i];
    if (!x) { a.appendChild(y); return; }
    if (x.nodeType !== y.nodeType || x.nodeName !== y.nodeName || (x.nodeType === 1 && x.id !== y.id)) { a.replaceChild(y, x); return; }
    if (x.nodeType !== 1) { if (x.nodeValue !== y.nodeValue) x.nodeValue = y.nodeValue; return; }
    morphEl(x, y);
  });
  for (let i = bc.length; i < ac.length; i++) a.removeChild(ac[i]);
}
function morphEl(a, b) {
  const tag = a.tagName;
  const oldValAttr = a.getAttribute("value"), oldText = tag === "TEXTAREA" ? a.textContent : null;
  for (const { name } of [...a.attributes]) if (!b.hasAttribute(name)) a.removeAttribute(name);
  for (const { name, value } of [...b.attributes]) if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  if (tag === "INPUT") {
    if (a.type === "checkbox" || a.type === "radio") a.checked = b.hasAttribute("checked");
    else if (a.type !== "file" && oldValAttr !== b.getAttribute("value")) a.value = b.getAttribute("value") || "";
    return;
  }
  if (tag === "TEXTAREA") { if (oldText !== b.textContent) { a.textContent = b.textContent; a.value = b.textContent; } return; }
  morphChildren(a, b);
  if (tag === "SELECT") { const i = [...b.options].findIndex(o => o.hasAttribute("selected")); a.selectedIndex = i < 0 ? 0 : i; }
}
function keyModal() {   // registration window for one provider's temporary key (EV-005 CHG-26)
  const x = provOf(UI.keyModal.provider) || { id: UI.keyModal.provider, name: UI.keyModal.provider };
  return `<div class="scrim"><div class="modal" style="width:460px" data-region="R-03" id="key-window" data-provider="${x.id}"><div class="mh">${PROVIDER_ICON[x.id] || ""}<b id="key-window-title">${esc(x.name)} API 키 등록</b></div>` +
    `<div class="mb"><div class="small muted">임시 저장 · 다시 시작하면 사라짐</div><input type="password" id="key-input" placeholder="${esc(x.name)} API 키" autocomplete="off">` +
    (UI.keyModal.empty ? `<div class="small" id="key-input-error" style="color:#dc2626">키를 입력하세요</div>` : "") + `</div>` +
    `<div class="mf"><span class="btn" data-act="key-cancel" id="key-cancel">취소</span><span class="btn p" data-act="key-save" id="key-save">등록</span></div></div></div>`;
}
function delModal(q, detail, act) {
  return `<div class="scrim"><div class="modal" style="width:520px" data-region="R-06" id="del-modal"><div class="mh"><b>삭제 확인</b></div><div class="mb"><div>${q}</div><div class="small" id="del-detail">${detail}</div></div><div class="mf"><span class="btn" data-act="modal-cancel" id="del-cancel">취소</span><span class="btn d" data-act="${act}" id="del-ok">삭제</span></div></div></div>`;
}

/* ------------------------------------------------------------------ events */
function curProject() { const { s } = route(); return s[0] === "p" ? proj(s[1]) : null; }
document.addEventListener("click", ev => {
  const t = ev.target.closest("[data-act]");
  if (!t) return;
  const a = t.dataset.act, p = curProject();
  const { s } = route();
  if (p && s[2] === "r") { const rel = relOf(p, s[3]) || p.releases[0]; if (rel && (Popover.onClick(a, t, edCtx(p, rel)) || Canvas.onClick(a, t, edCtx(p, rel)))) return; }
  if (a === "go") {
    ev.stopPropagation();
    const to = t.dataset.to || "";
    // D-499 덧 13 (4차 신규자 시험): pressing a tab to the auto cycle reads its list again — the list was kept by address, so pressing the
    // tab it already showed (or coming back to it) showed a new wheel only after reloading the page
    if (/^#\/p\/[^/]+\/auto(\/|$)/.test(to)) { const st = AutoCycle.state(UI); st.hash = null; st.detailKey = null; }
    if (/^#\/p\/[^/]+\/compare(\/|$)/.test(to) && UI.cmpErr) { UI.cmpErr = null; UI.cmpStale = true; }   // D-551 덧 1 ③: pressed again, it asks again
    if (to === location.hash) { render(); return; }
    go(to); return;
  }
  if (a === "ed-confirm-open") { if (!t.classList.contains("off")) { UI.confirm = true; UI.confirmState = null; render(); } return; }   // EV-022 p.21 → p.22
  if (a === "cf-cancel") { UI.confirm = false; UI.confirmState = null; render(); return; }
  if ((a === "cf-update" || a === "cf-new") && p) { if (!t.classList.contains("off")) confirmSend(p, a === "cf-new" ? "new" : "update"); return; }
  if (a === "menu") { UI.menu = !UI.menu; render(); return; }
  if (a === "example") { UI.menu = false; api("POST", `/api/examples/${encodeURIComponent(t.dataset.name)}`).then(np => refresh().then(() => go(`#/p/${np.id}`))).catch(e => alert(e.message)); return; }
  if (a === "chip") { newpCapture(); UI.newp.open = UI.newp.open === t.dataset.k ? null : t.dataset.k; render(); return; }
  if (a === "np-score") { newpCapture(); UI.newp.score = t.value; render(); return; }
  if (a === "np-prov") { newpCapture(); UI.newp.prov = t.value; UI.newp.llmName = (provOf(t.value) || {}).default_model || ""; render(); return; }
  if (a === "np-cancel") { UI.newp = null; go("#/"); return; }
  if (a === "np-skip" || a === "np-create") {
    const n = UI.newp; newpCapture(); n.name = n.name.trim();
    if (!n.name) { alert("Project 이름을 적으세요"); return; }
    if (a === "np-create" && n.dataErr) return;
    const sc = { skip: n.score === "skip", rule: n.score === "rule" || n.score === "both", llm: n.score === "llm" || n.score === "both" };
    if (sc.llm) { sc.llm_provider = n.prov; sc.llm_model = n.llmName.trim(); }   // always sent with LLM scoring (EV-005 CHG-27)
    const data = a === "np-skip" || !n.data ? null : { name: n.data.name, text: n.data.text };
    api("POST", "/api/projects", { name: n.name, desc: n.desc, scoring: sc, rule: { pass: Number(n.pass), below: n.below }, data })
      .then(np => { UI.newp = null; return refresh().then(() => go(`#/p/${np.id}`)); })
      .catch(e => { n.dataErr = e.message; render(); });
    return;
  }
  const PORTED = new Set(["kg-diff", "mon-out", "cmp-auto-verdict", "dep-open", "dep-cancel", "dep-send", "rb-open", "rb-cancel", "rb-send", "rb-again", "op-runs", "ops-more", "ops-proc", "ops-tab", "ops-iter", "ops-promote", "ops-promote-cancel", "ops-promote-save", "promo-pick", "promo-suite-add", "rev-view", "rev-edit", "kg-open", "kg-keep", "kg-replace", "kg-merge", "kg-draft-drop", "kg-merge-cancel", "kg-merge-ok", "ed-packdiff", "vb-diff", "cmp-det", "ed-confirm-open", "cf-cancel", "cf-update", "cf-new", "itab", "seg", "node", "pick-input", "reg-start", "reg-how", "reg-builtin", "reg-read", "reg-cancel", "reg-save", "run", "run-cancel", "case", "iter", "open-run", "vsel", "v-cancel", "v-save", "v-gate-open", "cmp-same-run", "v-same-run", "v-sealed-run", "gen", "gen-retry", "direct-cancel", "direct-save", "pick-set", "mon-enlarge", "mon-cancel", "mon-cancel-all", "mon-start", "proj-del", "rel-del", "modal-cancel", "proj-del-ok", "rel-del-ok", "brief-new", "brief-cancel", "brief-save", "suite-add", "diff-open", "reg-pick", "reg-pick-file", "ops-label-save", "auto-pick", "auto-panel-close", "auto-eff-open", "auto-eff-go", "auto-ff-open", "auto-ff-confirm", "auto-set-open", "auto-set-save", "auto-more-soon", "auto-version-open", "auto-stop-open", "auto-stop-go", "auto-q-open", "auto-q-go", "fold"]);  // connected so far
  if (p && !PORTED.has(a)) return;
  if (!p) {
    if (a === "key-open") { UI.keyModal = { provider: t.dataset.provider, empty: false }; render(); const el = document.getElementById("key-input"); if (el) el.focus(); }
    if (a === "key-cancel") { UI.keyModal = null; render(); }
    if (a === "key-save" && UI.keyModal) {
      const el = document.getElementById("key-input"), v = el.value.trim(); el.value = "";   // the value goes only to the server's memory
      if (!v) { UI.keyModal.empty = true; render(); return; }
      api("POST", `/api/settings/keys/${UI.keyModal.provider}`, { value: v }).then(st => { S.settings = st; UI.keyModal = null; render(); }).catch(e => alert(e.message));
    }
    if (a === "key-clear") api("DELETE", `/api/settings/keys/${t.dataset.provider}`).then(st => { S.settings = st; render(); });
    return;
  }
  if (a === "itab") { UI.itab = t.dataset.k; UI.verdictErr = false; if (s[2] === "r" && s[4] === "edit") { go(`#/p/${p.id}/r/${s[3]}`); return; } render(); return; }   // the change view's tabs go back to the graph
  if (a === "node") { UI.node = t.dataset.node; UI.iter = 1; render(); return; }
  if (a === "case") { UI.caseIdx = Number(t.dataset.i); UI.iter = 1; render(); return; }
  if (a === "iter") { UI.iter = Number(t.dataset.k); render(); return; }
  if (a === "pick-input") { UI.execInput = t.dataset.id; UI.runNotice = null; render(); return; }
  if (a === "cmp-auto-verdict") { UI.itab = "verdict"; go(`#/p/${p.id}/r/${t.dataset.rel}`); return; }   // EV-035 CHG-184 → the new version's 판정 tab
  if (a === "open-run") { const r = runOf(p, t.dataset.run); if (r) { UI.runId = r.id; UI.itab = "run"; UI.caseIdx = 0; go(`#/p/${p.id}/r/${r.rel}/run/${r.id}`); } return; }
  if (a === "run") {
    const rel = relOf(p, s[3]) || p.releases[0];
    const ins = inputsOf(p);
    if (!ins.length) { UI.itab = "exec"; UI.runNotice = "실행하지 않았습니다 — 실행 입력을 먼저 준비하세요"; render(); return; }
    if (UI.itab !== "exec") { UI.itab = "exec"; render(); return; }
    const input = ins.find(i => i.id === UI.execInput) || ins[ins.length - 1];
    startRun(p, rel, input).then(run => { UI.runId = run.id; UI.itab = "run"; UI.caseIdx = 0; UI.node = null; go(`#/p/${p.id}/r/${rel.id}/run/${run.id}`); render(); })
      .catch(e => { UI.runNotice = `실행하지 않았습니다 — ${e.message}`; render(); });
    return;
  }
  if (a === "run-cancel") { const rel = relOf(p, s[3]) || p.releases[0], r = rel ? shownRun(p, rel) : p.runs[p.runs.length - 1]; if (r) stopRun(r); return; }   // EV-064 CHG-CF-6
  if (a === "vsel") { if (t.classList.contains("off")) return; UI.verdict = t.dataset.v; UI.vReason = document.getElementById("v-reason").value; render(); return; }
  if (a === "cmp-same-run") {   // EV-064 CHG-CF-3 (가): the offered rows on the same input; the screen stays where it is
    const d = UI.cmp, off = UI.cmpOffer, inp = d && inputsOf(p).find(i => i.id === d.input);
    if (t.classList.contains("off") || !off || !inp) return;
    for (const rid of off.targets) {
      const rel = relOf(p, rid), k = `${p.id}|${d.input}|${rid}`;
      if (!rel) continue;
      UI.sameTried[k] = "…";
      startRun(p, rel, inp).then(run => { UI.sameTried[k] = run.uid; render(); }).catch(e => { delete UI.sameTried[k]; render(); alert(e.message); });
    }
    render(); return;
  }
  if (a === "v-sealed-run") {   // D-477 덧 13: the sealed test once on this wheel version, under the 판정 tab's alias
    const relNow = relOf(p, s[3]) || p.releases[0], by = (document.getElementById("v-by") || {}).value || "";
    if (t.classList.contains("off") || !relNow) return;
    UI.sealedBusy = true; UI.sealedMsg = "";
    api("POST", `/api/projects/${p.id}/sealed-check`, { release_id: relNow.id, by }).then(r => { UI.sealedMsg = r.line; return refresh(); })
      .catch(e => { UI.sealedMsg = e.message; }).finally(() => { UI.sealedBusy = false; render(); });
    render(); return;
  }
  if (a === "v-same-run") {   // EV-064 CHG-CF-3 (나): this version and its pair on one suite; the 판정 tab stays
    const relNow = relOf(p, s[3]) || p.releases[0], off = relNow && verdictSameOffer(p, relNow), inp = off && inputsOf(p, relNow).find(i => i.id === off.input);
    if (t.classList.contains("off") || !off || !inp) return;
    const k = `${p.id}|v|${off.rel}`;
    UI.sameTried[k] = "…";
    Promise.all([off.rel, off.pair].map(rid => startRun(p, relOf(p, rid), inp))).then(runs => { UI.sameTried[k] = runs.map(r => r.uid); render(); })
      .catch(e => { delete UI.sameTried[k]; render(); alert(e.message); });
    render(); return;
  }
  if (a === "v-gate-open") { const r = runOf(p, t.dataset.run); if (r) { UI.runId = r.id; UI.itab = "verdict"; UI.verdict = null; UI.verdictErr = false; go(`#/p/${p.id}/r/${r.rel}/run/${r.id}`); render(); } return; }   // EV-064 CHG-CF-4
  if (a === "v-cancel") { UI.verdict = null; UI.vReason = ""; UI.verdictErr = false; for (const id of ["v-reason", "v-excl", "v-hyp"]) document.getElementById(id).value = ""; render(); return; }
  if (a === "vb-diff") { UI.diffSel = { a: t.dataset.a, b: t.dataset.b }; UI.diff = null; go(`#/p/${p.id}/compare`);   // EV-022 p.23: the confirmed version's changes
    api("GET", `/api/projects/${p.id}/pack-diff?a=${encodeURIComponent(t.dataset.a)}&b=${encodeURIComponent(t.dataset.b)}`).then(res => { UI.diff = { pid: p.id, res }; render(); setTimeout(() => { const el = document.getElementById("rev-diff"); if (el) el.scrollIntoView({ block: "start" }); }, 0); })
      .catch(e => { UI.diff = { pid: p.id, error: e.message }; render(); });
    return; }
  if (a === "ed-packdiff") { UI.edPackDiff = !UI.edPackDiff; render(); return; }
  if (a === "dep-open") { const rel = relOf(p, t.dataset.rel), vv = rel.version || {}; UI.deploy = { rel: rel.id, id: (vv.deploy_target && vv.deploy_target.deployment_id) || (vv.await && vv.await.deployment_id) || `${rel.pack.id}@${rel.name.toLowerCase()}-pg-local`, by: lastBy(p), reason: "", busy: false, err: null }; render(); return; }   // EV-071: the server's default (the 운영 deployment)
  if (a === "dep-cancel") { UI.deploy = null; render(); return; }
  if (a === "rb-open") {   // EV-SUI-01: '되돌리기 단계' follows the one in progress; the button after a failure opens a new form at once
    const rel = p && relOf(p, t.dataset.rel), rb = rel && rel.version && rel.version.rollback;
    UI.rollback = { rel: t.dataset.rel, by: lastBy(p), reason: "", busy: false, err: null, again: t.id === "vb-rollback" && !!(rb && rb.failed), id: t.id === "vb-rollback-steps" && rb ? rb.rollback_id : null };
    render(); return; }
  if (a === "rb-cancel") { UI.rollback = null; render(); return; }
  if (a === "rb-again") { if (UI.rollback) { UI.rollback.again = true; UI.rollback.err = null; } render(); return; }
  if (a === "rb-send" && p) { if (!t.classList.contains("off")) rollbackSend(p); return; }
  if (a === "dep-send" && p) { if (!t.classList.contains("off")) deploySend(p); return; }
  if (a === "op-runs") {   // EV-025: the 운영 Run screen (p.30); D-499 덧 13: every press reads the list again (new 운영 Runs without reloading)
    UI.itab = "run"; if (UI.ops) UI.ops.key = null;
    const to = `#/p/${p.id}/r/${s[3] || t.closest("[data-rel]")?.dataset.rel || UI.lastRel}/run/ops`;
    if (to === location.hash) render(); else go(to);
    return;
  }
  if (a === "ops-tab") { UI.opsTab = t.dataset.k; render(); return; }   // EV-025 OPS-2
  if (a === "ops-iter") { UI.iter = Number(t.dataset.k); render(); return; }
  // EV-025 OPS-3 (manual p.30 ③): 평가 case로 올리기 — the confirm window, then the append-only 올린 case
  if (a === "promo-pick") { UI.promoPick = UI.promoPick || {}; UI.promoPick[t.dataset.id] = t.checked; render(); return; }
  if (a === "promo-suite-add" && p && !t.classList.contains("off")) {
    const sel = document.getElementById("promo-base"), ids = Object.keys(UI.promoPick || {}).filter(k => UI.promoPick[k]).map(Number);
    UI.promoBusy = true; UI.promoErr = null; UI.promoDone = null; render();
    api("POST", `/api/projects/${p.id}/eval-suites/promoted`, { suite: sel ? sel.value : UI.promoBase, promotions: ids })
      .then(d => { UI.promoBusy = false; UI.promoDone = d; UI.promoPick = {}; refresh(); }).catch(e => { UI.promoBusy = false; UI.promoErr = e.message; render(); });
    return;
  }
  if (a === "ops-promote" && p) { if (!t.classList.contains("off")) { UI.opsPromote = { by: "", reason: "" }; UI.opsNotice = null; render(); } return; }
  if (a === "ops-promote-cancel") { UI.opsPromote = null; render(); return; }
  if (a === "ops-promote-save" && p && UI.opsPromote && !UI.opsPromote.busy) { const rel = relOf(p, s[3]); if (rel) OpsRun.promote(edCtx(p, rel), rel, document.getElementById("ops-promote-by").value.trim(), document.getElementById("ops-promote-reason").value.trim()); return; }
  if (a === "auto-pick") { AutoCycle.state(UI).sel = t.dataset.k; render(); return; }   // EV-030 CHG-156: the wheel whose log and facts show
  if (a.startsWith("auto-") && p && AutoCycle.act(a, t, { p, ui: UI, api, render })) return;   // EV-069 CHG-EF-7: start · settings · fixed flow
  if (a === "ops-label-save" && p) { const rel = relOf(p, s[3]), v = id => document.getElementById(id).value; if (rel) OpsRun.saveLabel(edCtx(p, rel), rel, v("ops-label-pick"), v("ops-label-reason"), v("ops-label-by")); return; }   // EV-030 CHG-154
  if (a === "ops-proc" && p) { const st = UI.ops || {}; st.procOpen = !st.procOpen; render(); return; }   // EV-077: '절차 기록 n건' opens · closes
  if (a === "ops-more" && p) { const rel = relOf(p, s[3]); if (rel) OpsRun.more(edCtx(p, rel), rel); return; }
  if (a === "fold") { UI.folds = { ...(UI.folds || {}), [t.dataset.k]: !(UI.folds || {})[t.dataset.k] }; render(); return; }   // EV-075 R3
  if (a === "rev-view") { UI.revOpen = UI.revOpen === t.dataset.rev ? null : t.dataset.rev; render(); return; }
  if (a === "kg-open") { UI.kgOpen = !UI.kgOpen; render(); return; }
  if (a === "kg-diff") { kgDiffOpen(t.dataset.kgTag); return; }   // D-581
  if (a === "kg-keep" || a === "kg-replace") { kgChoose(t.dataset.kgReport, a.slice(3)); return; }   // D-560 겹침 (31쪽)
  if (a === "kg-merge") {
    const [pid, rid] = (UI.kg && UI.kg.key || "|").split("|"), report = t.dataset.kgReport;
    api("GET", `/api/projects/${pid}/releases/${rid}/kg/overlap-draft?report_id=${encodeURIComponent(report)}`)
      .then(d => { UI.kgDraft = { report, fake: d.fake, checks: d.checks, actions: d.actions }; UI.kgErr = null; render(); }).catch(e => { UI.kgErr = e.message; render(); });
    return;
  }
  if (a === "kg-draft-drop" && UI.kgDraft) { UI.kgDraft[t.dataset.side].splice(Number(t.dataset.i), 1); render(); return; }
  if (a === "kg-merge-cancel") { UI.kgDraft = null; render(); return; }
  if (a === "kg-merge-ok" && UI.kgDraft) { kgChoose(UI.kgDraft.report, "merge", { checks: UI.kgDraft.checks, actions: UI.kgDraft.actions }); return; }
  if (a === "rev-edit" && p) { api("POST", `/api/projects/${p.id}/releases/${t.dataset.rel}/revisions/${encodeURIComponent(t.dataset.rev)}/edit`).then(() => refresh()).then(() => go(`#/p/${p.id}/r/${t.dataset.rel}`)).catch(e => { UI.revErr = e.message; render(); }); return; }
  if (a === "mon-out") { UI.monOpen = UI.monOpen === t.dataset.run ? null : t.dataset.run; render(); return; }
  if (a === "cmp-det") { UI.cmpDetOpen = !UI.cmpDetOpen; render(); return; }
  if (a === "diff-open") {
    const s = UI.diffSel;
    api("GET", `/api/projects/${p.id}/pack-diff?a=${encodeURIComponent(s.a)}&b=${encodeURIComponent(s.b)}`).then(res => { UI.diff = { pid: p.id, res }; render(); })
      .catch(e => { UI.diff = { pid: p.id, error: e.message }; render(); });
    return;
  }
  if (a === "v-save") {
    const vrel = relOf(p, s[3]) || p.releases[0], run = vrel && judgeRun(p, vrel);   // EV-064 CHG-CF-4: the Run the 판정 tab shows is the one saved
    if (!run) return;
    if (!UI.verdict || t.classList.contains("off")) return;   // EV-076: nothing is saved before a pick
    const rec = recOf(p, run), v = UI.verdict, reason = document.getElementById("v-reason").value;
    const confirm = !!rec.v && v === rec.v, by = (document.getElementById("v-by") || {}).value || "";
    if (!confirm && !reason.trim()) { UI.verdictErr = true; UI.vReason = reason; render(); return; }
    api("POST", `/api/runs/${run.uid}/verdicts`, { v, reason, by, excl: document.getElementById("v-excl").value, hyp: document.getElementById("v-hyp").value })
      .then(r => { putRun(proj(p.id) || p, r); markWrite(); keepBy(by); UI.verdict = null; UI.vReason = ""; UI.vBy = null; UI.verdictErr = false; for (const id of ["v-reason", "v-excl", "v-hyp"]) { const el = document.getElementById(id); if (el) el.value = ""; } render(); refresh(); })   // EV-022: the version state follows the verdict
      .catch(e => { UI.verdictErr = e.status === 503 || (e.status === 422 && e.message && !/이유를 적어야/.test(e.message)) ? e.message : true; UI.vReason = reason; render(); });   // EV-058 CHG-243
    return;
  }
  if (a === "proj-del") { api("GET", `/api/projects/${p.id}/delete-summary`).then(c => { UI.modal = { type: "proj-del", c }; render(); }); return; }
  if (a === "rel-del") { ev.stopPropagation(); const rid = t.dataset.rel; api("GET", `/api/projects/${p.id}/releases/${rid}/delete-summary`).then(c => { UI.modal = { type: "rel-del", rel: rid, c }; render(); }); return; }
  if (a === "modal-cancel") { UI.modal = null; render(); return; }
  if (a === "proj-del-ok") { UI.modal = null; api("DELETE", `/api/projects/${p.id}`).then(() => refresh().then(() => go("#/"))).catch(e => alert(e.message)); return; }
  if (a === "rel-del-ok") { const rid = UI.modal.rel; UI.modal = null; api("DELETE", `/api/projects/${p.id}/releases/${rid}`).then(() => refresh().then(() => go(`#/p/${p.id}`))).catch(e => alert(e.message)); return; }
  if (a === "reg-start") { UI.reg = { name: "", how: t.dataset.how, path: "", builtin: "", result: null, busy: false }; go(`#/p/${p.id}/new-release`); return; }
  if (a === "reg-how") { UI.reg.name = document.getElementById("reg-name").value; UI.reg.how = t.dataset.how; UI.reg.result = null; render(); return; }
  if (a === "reg-builtin") { UI.reg.builtin = t.value; UI.reg.name = document.getElementById("reg-name").value || t.dataset.name; regRead(); return; }
  if (a === "reg-read") {
    const r = UI.reg; r.name = document.getElementById("reg-name").value;
    if (r.how === "local") {
      const np = document.getElementById("reg-path").value.trim(), c = document.getElementById("reg-config");
      if (c) r.config = c.value.trim();
      if (np !== r.path) { r.config = ""; r.cfgFolder = null; }   // another folder: its own execution config (EV-003 CHG-18)
      r.path = np; r.pathDraft = undefined; r.pickErr = null; regRead();
    }
    if (r.how === "upload") readUploads(document.getElementById("reg-files")).then(files => { r.files = files; regRead(); });
    return;
  }
  if (a === "reg-pick" || a === "reg-pick-file") {   // macOS chooser through the local server (EV-003 CHG-17, CHG-18)
    const r = UI.reg, k = a === "reg-pick" ? "folder" : "file"; if (r.picking) return;
    // r.path stays the folder that was last read: a typed path is only a draft until it is read (EV-003 CHG-17/18)
    r.name = document.getElementById("reg-name").value; r.pathDraft = document.getElementById("reg-path").value.trim();
    const c = document.getElementById("reg-config"); if (c) r.config = c.value.trim();
    r.picking = k; r.pickErr = null; render();
    api("POST", `/api/pick-${k}`, {})
      .then(x => {
        r.picking = null;
        if (x.cancelled) { render(); return; }
        if (k === "file") { r.config = x.path; render(); return; }
        if (x.path !== r.path) { r.config = ""; r.cfgFolder = null; }   // compared with the last read folder
        r.path = x.path; r.pathDraft = undefined; regRead();
      })
      .catch(e => { r.picking = null; r.pickErr = { k, message: e.message }; render(); });
    return;
  }
  if (a === "reg-cancel") { UI.reg = null; go(`#/p/${p.id}`); return; }
  if (a === "reg-save") {
    const r = UI.reg; if (!r.result || r.result.error || r.busy) return;
    const cfgEl = document.getElementById("reg-config"); if (cfgEl) r.config = cfgEl.value.trim();
    if (r.result.sourceKind === "agent_pack" && !r.config) return;   // an execution config is required (CHG-04, EV-003 CHG-18)
    r.name = document.getElementById("reg-name").value.trim() || (S.builtin.find(b => b.id === r.builtin) || {}).name || "새 release";
    r.busy = true; render();
    api("POST", `/api/projects/${p.id}/releases`, { name: r.name, ...regSource(r) })
      .then(rel => { UI.reg = null; return refresh().then(() => go(`#/p/${p.id}/r/${rel.id}`)); })
      .catch(e => { r.busy = false; r.result = { error: e.message, errors: e.detail && e.detail.errors }; render(); });
    return;
  }
  if (a === "seg") { UI.seg = t.dataset.k; UI.gen = null; UI.fileErr = null; UI.dataErr = null; render(); return; }
  if (a === "gen" || a === "gen-retry") {
    if (!hasKey(projLlm(p.scoring).id) || UI.genBusy) return;   // no key for the Project's provider: nothing is generated
    const rel = relOf(p, a === "gen-retry" ? UI.gen.rel : document.getElementById("gen-rel").value);
    const count = a === "gen-retry" ? UI.gen.count : Math.max(1, Math.min(20, Number(document.getElementById("gen-count").value) || 5));
    const desc = a === "gen-retry" ? UI.gen.desc : document.getElementById("gen-desc").value;
    UI.genCount = count; UI.genBusy = true; render();
    api("POST", `/api/projects/${p.id}/input-sets/generate`, { release: rel.id, count, desc })
      .then(r => { UI.genBusy = false; UI.gen = { rows: r.rows, rel: rel.id, count, desc, setName: r.set ? r.set.name : "" }; if (r.set) { p.sets.push(r.set); UI.setId = r.set.id; } render(); })
      .catch(e => { UI.genBusy = false; UI.gen = { rows: [{ ok: false, raw: "", reason: e.message }], rel: rel.id, count, desc, setName: "" }; render(); });
    return;
  }
  if (a === "direct-cancel") { UI.direct = ""; document.getElementById("direct-text").value = ""; render(); return; }
  if (a === "direct-save") {
    const text = document.getElementById("direct-text").value;
    if (!text.split(/\n/).some(x => x.trim())) return;
    api("POST", `/api/projects/${p.id}/input-sets`, { how: "direct", text })
      .then(set => { p.sets.push(set); UI.setId = set.id; UI.direct = ""; document.getElementById("direct-text").value = ""; render(); }).catch(e => alert(e.message));
    return;
  }
  if (a === "pick-set") { UI.setId = t.dataset.id; render(); return; }
  if (a === "suite-add") {
    const name = (document.getElementById("suite-name") || {}).value;
    if (!name || UI.suiteBusy) return;   // EV-012 CHG-69: one request at a time (the handler checks, not only the button look)
    UI.suiteBusy = true; UI.suiteName = name; render();   // the folder choice stays shown while the request runs
    api("POST", `/api/projects/${p.id}/eval-suites`, { release: UI.suiteRel, suite: name })
      .then(s => { UI.suiteErr = null; UI.suiteDone = s; p.suites = [...(p.suites || []), s]; UI.setId = s.id; }).catch(e => { UI.suiteErr = e.message; UI.suiteDone = null; })
      .finally(() => { UI.suiteBusy = false; render(); });
    return;
  }
  if (a === "brief-new") { UI.briefDraft = { title: "", id: null }; go(`#/p/${p.id}/briefs`); render(); return; }
  if (a === "brief-cancel") { UI.briefDraft = null; render(); return; }
  if (a === "brief-save") {
    const title = document.getElementById("brief-title").value.trim() || "제목 없는 요청서";
    const fields = Object.fromEntries(BRIEF_FIELDS.map(([k]) => [k, document.getElementById("brief-" + k).value]));
    const id = UI.briefDraft && UI.briefDraft.id;
    api(id ? "PUT" : "POST", `/api/projects/${p.id}/briefs${id ? "/" + id : ""}`, { title, fields })
      .then(b => { const i = p.briefs.findIndex(x => x.id === b.id); if (i >= 0) p.briefs[i] = b; else p.briefs.push(b); UI.briefDraft = null; go(`#/p/${p.id}/briefs/${b.id}`); render(); }).catch(e => alert(e.message));
    return;
  }
  if (a === "mon-enlarge") { UI.monEnlarged = UI.monEnlarged === t.dataset.run ? null : t.dataset.run; render(); return; }
  if (a === "mon-cancel") { const r = runOf(p, t.dataset.run); if (r) stopRun(r); return; }
  if (a === "mon-cancel-all") { const b = p.bundles[p.bundles.length - 1]; if (b) api("POST", `/api/bundles/${b.id}/cancel`); return; }
  if (a === "mon-start") {
    const ins = monInputs(p); if (!ins.length) return;   // no run input: the guidance is shown and nothing starts (AC-31-C3)
    const rels = p.releases.filter(r => UI.monSel[r.id]);
    if (rels.length < 2) { UI.monErr = true; render(); return; }
    UI.monErr = false;
    const input = ins.find(i => i.id === UI.monInput) || ins[0];
    api("POST", `/api/projects/${p.id}/bundles`, { releases: rels.map(r => r.id), input: { kind: runKind(input), id: input.id } })
      .then(b => { const cp = proj(p.id) || p; for (const r of b.runs) followRun(cp, putRun(cp, r)); cp.bundles.push({ id: b.id, input: b.input, runs: b.run_ids, at: b.at }); markWrite(); UI.monEnlarged = null; UI.monCase = 0; render(); })
      .catch(e => { UI.monNotice = e.message; render(); });
    return;
  }
});
document.addEventListener("input", ev => {   // typed execution config path turns 등록 on/off without re-rendering (EV-003 CHG-18)
  const t = ev.target, r = UI.reg;
  { const p = curProject(), { s } = route(); if (p && s[2] === "r") { const rel = relOf(p, s[3]) || p.releases[0]; if (rel && Popover.onInput(t, edCtx(p, rel))) return; } }
  if (t.id === "cf-memo" && UI.confirmState) { UI.confirmState.memo = t.value; return; }   // EV-022 confirm window memo
  if (t.id === "cf-wheel" && UI.confirmState) { UI.confirmState.wheel = t.value ? +t.value : null; return; }   // D-444 #10: the record to tie to
  if (UI.deploy && ["dep-id", "dep-by", "dep-reason"].includes(t.id)) { UI.deploy[t.id.slice(4)] = t.value; return; }   // EV-022 deploy approval window
  if (UI.rollback && ["rb-by", "rb-reason"].includes(t.id)) { UI.rollback[t.id.slice(3)] = t.value; return; }   // EV-SUI-01 rollback window
  if (t.id === "v-by") { UI.vBy = t.value; return; }
  if (t.id !== "reg-config" || !r || !r.result || r.result.error) return;
  r.config = t.value.trim();
  const s = document.getElementById("reg-save"); if (s) s.classList.toggle("off", !r.config);
});
document.addEventListener("change", ev => {
  const t = ev.target, p = curProject();
  if (t.id === "np-file") readFile(t, (name, text) => { newpCapture(); api("POST", "/api/parse/validation-data", { name, text })
    .then(r => { UI.newp.dataErr = null; UI.newp.data = { name, text, cases: r.cases }; render(); })
    .catch(e => { UI.newp.dataErr = e.message; UI.newp.data = null; render(); }); });
  if (!p) return;
  if (t.id === "set-file") readFile(t, (name, text) => { t.value = ""; api("POST", `/api/projects/${p.id}/input-sets`, { how: "file", name, text })
    .then(set => { UI.fileErr = null; p.sets.push(set); UI.setId = set.id; render(); }).catch(e => { UI.fileErr = e.message; render(); }); });
  if (t.id === "data-file") readFile(t, (name, text) => { api("POST", `/api/projects/${p.id}/validation-data`, { name, text })
    .then(() => { UI.dataErr = null; return refresh(); }).catch(e => { UI.dataErr = e.message; render(); }); });
  if (t.dataset.act === "cmp-input") { UI.cmpInput = t.value; UI.cmpStale = true; render(); }
  if (t.dataset.act === "cmp-sel") { UI.cmpSel[`${UI.cmpInput}|${t.dataset.rel}`] = t.checked; UI.cmpTouched[`${UI.cmpInput}|${t.dataset.rel}`] = true; render(); }   // EV-064: a person's check stays
  if (t.dataset.act === "cmp-run") { if (t.value) { UI.cmpRun[t.dataset.rel + "|" + UI.cmpInput] = t.value; UI.cmpSel[`${UI.cmpInput}|${t.dataset.rel}`] = true; UI.cmpTouched[`${UI.cmpInput}|${t.dataset.rel}`] = true; UI.cmpStale = true; render(); } }   // EV-064: a pick counts as a person's choice
  if (t.dataset.act === "cmp-metric") { UI.metric = t.value; UI.metricSet = true; render(); }
  if (t.dataset.act === "reg-config-pick") { const r = UI.reg; r.name = document.getElementById("reg-name").value; r.pathDraft = document.getElementById("reg-path").value.trim(); r.cfgPick = t.value; r.config = t.value ? `${r.cfgFolder}/${t.value}` : ""; render(); }
  if (t.dataset.act === "diff-pick") { UI.diffSel[t.dataset.k] = t.value; UI.diff = null; render(); }
  if (t.dataset.act === "mon-rel") { UI.monSel[t.dataset.rel] = t.checked; render(); }
  if (t.dataset.act === "mon-input") { UI.monInput = t.value; render(); }
  if (t.dataset.act === "mon-case") { UI.monCase = Number(t.value); render(); }
  if (t.dataset.act === "gen-rel") { UI.genRel = t.value; render(); }
  if (t.dataset.act === "suite-rel") { UI.suiteRel = t.value; UI.suiteAvail = null; UI.suiteErr = null; render(); }
});
function newpCapture() {
  const n = UI.newp, g = id => document.getElementById(id);
  if (g("np-name")) n.name = g("np-name").value;
  if (g("np-desc")) n.desc = g("np-desc").value;
  if (g("np-pass")) { n.pass = g("np-pass").value; n.below = g("np-below").value; }
  if (g("np-llm-name")) n.llmName = g("np-llm-name").value;
}
function readFile(input, cb) { const f = input.files[0]; if (!f) return; const fr = new FileReader(); fr.onload = () => cb(f.name, String(fr.result)); fr.readAsText(f); }
document.addEventListener("pointerdown", ev => { const p = curProject(), { s } = route(); if (p && s[2] === "r") { const rel = relOf(p, s[3]) || p.releases[0]; if (rel) Canvas.onPointer(ev, edCtx(p, rel)); } });   // EV-022 canvas drag (web/canvas.js)
window.addEventListener("hashchange", render);
window.addEventListener("resize", layoutMonitors);
/* D-432 덧 8: the whole text of a word (hv) or of any '자세히' title shows in one black bubble the page draws — within 0.15 s of the
   mouse or the keyboard focus — instead of the browser's own title box (about a second late, and missing in some in-app browsers).
   The element under the pointer lends its title to the bubble while it is there (kept in data-tip, so the two boxes never show
   together) and gets it back when the pointer leaves; a re-render (morph · the 3 s re-read) keeps the bubble on the element now under
   the pointer or the focus. Near an edge the bubble flips above and stays inside the window. */
const Tip = (() => {
  let box = null, el = null, text = "", timer = 0, x = -1, y = -1, how = "mouse";
  const lend = e => { if (e.hasAttribute("title")) { text = e.getAttribute("title"); e.removeAttribute("title"); } if (text && e.dataset.tip !== text) e.dataset.tip = text; };
  const giveBack = () => { if (el && el.isConnected) { if (!el.hasAttribute("title") && text) el.setAttribute("title", text); delete el.dataset.tip; } };
  const titled = n => n && n.closest ? n.closest("[title]") : null;
  function place() {
    if (!el || !text) { if (box) box.hidden = true; return; }
    if (box.textContent !== text) box.textContent = text;
    box.hidden = false;
    const r = el.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight;
    const top = r.bottom + 6 + h > innerHeight - 6 && r.top - h - 6 >= 6 ? r.top - h - 6 : r.bottom + 6;
    box.style.left = `${Math.max(6, Math.min(r.left, innerWidth - w - 6))}px`;
    box.style.top = `${Math.max(6, Math.min(top, innerHeight - h - 6))}px`;
  }
  function follow(recs) {   // a re-render: the lent title came back (morph) or the element was replaced
    if (!el) return;
    if (recs.some(r => r.target === el && r.type === "attributes")) lend(el);
    if (!el.isConnected) {
      const n = titled(how === "focus" ? document.activeElement : document.elementFromPoint(x, y));
      el = n; text = "";
      if (n) lend(n);
    }
    place();
  }
  function setup() {
    box = document.createElement("div");
    box.id = "tip"; box.setAttribute("role", "tooltip"); box.hidden = true;
    document.body.appendChild(box);
    new MutationObserver(follow).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["title", "data-tip"] });
  }
  function open(e, why) {
    if (!box) setup();
    close(); el = e; how = why; lend(e);
    timer = setTimeout(place, why === "focus" ? 0 : 100);
  }
  function close() { clearTimeout(timer); giveBack(); el = null; text = ""; if (box) box.hidden = true; }
  document.addEventListener("mouseover", ev => {
    x = ev.clientX; y = ev.clientY;
    if (el && el.contains(ev.target)) return;
    const t = titled(ev.target);
    if (t) open(t, "mouse"); else if (el && how === "mouse") close();
  });
  document.addEventListener("mouseout", ev => { if (!ev.relatedTarget && el && how === "mouse") close(); });   // the pointer left the window
  document.addEventListener("focusin", ev => { const t = titled(ev.target); if (t) open(t, "focus"); });
  document.addEventListener("focusout", () => { if (el && how === "focus") close(); });
  document.addEventListener("scroll", () => { if (el) place(); }, true);
  return { close };
})();
if (!location.hash) location.hash = "#/";
api("GET", "/api/mode").then(m => {   // EV-075 COMMON: the band says the mode in plain words; the address notice when the mouse is over it
  const band = document.querySelector(".mode");
  document.getElementById("mode-label").textContent = m.mode === "fake" ? "가짜 모델로 실행 중" : "실제 모델 사용 중";
  band.dataset.mode = m.mode; band.title = `LLM 호출 모드: ${m.label} — 호출 주소를 따르지 않는 코드는 실제 모델을 부를 수 있습니다`;
});
refresh();
