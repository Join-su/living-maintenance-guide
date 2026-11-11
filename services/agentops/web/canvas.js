/* EV-022 갈래 B — 캔버스 구조 편집(설명서 11~20쪽): 편집 중 node의 네 방향 점 · 선 위 ＋, 끌어 만들기 · 잇기, 갈 조건,
   지우기 미리보기 · 막힘, ⌗ 노드 정리, node 위치 저장. 이 파일만 갈래 B가 고칩니다(지시 019a). app.js는 아래 자리만
   부르고, 서버는 계획 v5 '화면과 서버 사이 약속'(execution/api-contract.md v1)의 API만 씁니다.
   - 편집이 열린 Agent Pack release에서만 점 · ＋ · 끌기가 나타납니다(모드 · 도구 막대 없음, 승인됨 · 운영 버전은 편집 전엔 보기만 #16).
   - 검사는 서버(packrt)가 합니다. 화면은 끄는 동안 맞는 곳만 초록으로 보이고(#6 · #8), 놓을 때 서버 결과를 그대로 보입니다.
   - node 위치는 LLMOps에 저장하고(Pack ui 칸을 쓰지 않음), 배치는 ⌗ 노드 정리를 누를 때만 바뀝니다. */
"use strict";
const Canvas = (() => {
  const ST = { layouts: {}, loading: {}, edit: null, editKey: "", graph: null, undo: {}, toast: null, drag: null, ctx: null, styled: false, labels: {} };
  // state 키의 쉬운 이름(io · context 응답의 labels, api-contract v1.6 — '초안 답', '근거 목록' …). 없으면 키 그대로
  const L = key => (ST.labels[key] && ST.labels[key].label) || key;
  const Ls = keys => (keys || []).map(L).join(" · ");
  const keepLabels = r => { if (r && r.labels && typeof r.labels === "object") Object.assign(ST.labels, r.labels); return r; };
  const NODE_W = 90, NODE_H = 34;
  // 추가할 수 있는 표준 node(옵션 표 9절 4항, packrt ADDABLE). 쉬운 이름을 크게, 실제 종류는 작게(12쪽)
  const KINDS = [
    { kind: "llm@1", name: "모델로 쓰기", desc: "값을 받아 답 · 글을 내보냄 — 예: 답 다듬기", base: "polish" },
    { kind: "retrieve@1", name: "리트리버", desc: "알람 해석을 받아 근거를 찾음", base: "find_more" },
    { kind: "validate@2", name: "검사", desc: "값을 검사해 결과를 냄", base: "check_more" },
    { kind: "router@1", name: "갈래", desc: "값에 따라 길을 나눔", base: "route_more" },
    { kind: "render@1", name: "결과 쓰기", desc: "템플릿으로 결과 쓰기", base: "render_more" },
  ];
  const BLOCKED = [["human_review@1", "사람 검토"], ["custom_code@3", "Pack 함수"], ["predict@1", "모델 예측"]];   // #1
  const esc = x => String(x ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const kindOf = n => String((n && n.kind) || "").split("@")[0];

  /* ---------------------------------------------------------------- state */
  const isPack = ctx => !!(ctx && ctx.p && ctx.rel && ctx.rel.pack && ctx.rel.graph);
  const editing = ctx => isPack(ctx) && !!(ctx.p.edit && ctx.p.edit.release === ctx.rel.id);
  const layoutKey = ctx => editing(ctx) ? `edit:${ctx.p.id}` : `rel:${ctx.rel.id}`;
  const layoutUrl = ctx => editing(ctx) ? `/api/projects/${ctx.p.id}/edit/layout` : `/api/projects/${ctx.p.id}/releases/${ctx.rel.id}/layout`;
  const gname = ctx => (ctx.rel.graph && ctx.rel.graph.graph_name) || (ctx.rel.pack && ctx.rel.pack.entrypoint);
  const rerender = ctx => { if (ctx && ctx.render) ctx.render(); };
  function reloadState(ctx) {   // /api/state의 Project edit 칸(탭 배지 · 줄 수)을 다시 읽음 — edCtx의 refresh(api-contract v1.2)
    ST.editKey = "";
    const f = (ctx && ctx.refresh) || (typeof refresh === "function" ? refresh : null);
    if (f) return Promise.resolve(f()).catch(() => rerender(ctx));
    rerender(ctx);
    return Promise.resolve();
  }
  function ensureEdit(ctx) {
    if (isPack(ctx) && !ctx.p.edit) {   // the edit ended (확정 · 버리기): the next edit reads its own layout, no leftover 정리 취소 (review E-S-07 N2)
      const k = `edit:${ctx.p.id}`; delete ST.layouts[k]; delete ST.undo[k]; if (ST.toast && ST.toast.key === k) ST.toast = null;
    }
    if (!editing(ctx)) { ST.edit = null; ST.editKey = ""; return; }
    const key = `${ctx.p.id}:${JSON.stringify(ctx.p.edit)}`;   // the whole summary: a rename keeps the line count but changes revision · pending
    if (ST.editKey === key) return;
    ST.editKey = key;
    ctx.api("GET", `/api/projects/${ctx.p.id}/edit`).then(r => { ST.edit = r.edit || null; rerender(ctx); }).catch(() => {});
  }
  function ensureLayout(ctx) {
    const key = layoutKey(ctx);
    if (key in ST.layouts || ST.loading[key]) return;
    ST.loading[key] = true;
    ctx.api("GET", layoutUrl(ctx)).then(r => { ST.layouts[key] = r && r.saved ? r.positions || {} : null; })
      .catch(() => { ST.layouts[key] = null; }).finally(() => { delete ST.loading[key]; rerender(ctx); });
  }
  function sized(graph, nodes) {
    let minX = Infinity, minY = Infinity, maxX = 0, maxY = 0;
    for (const n of nodes) { minX = Math.min(minX, n.x); minY = Math.min(minY, n.y); maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y); }
    const dx = minX < 20 ? 20 - minX : 0, dy = minY < 12 ? 12 - minY : 0;
    const out = dx || dy ? nodes.map(n => ({ ...n, x: n.x + dx, y: n.y + dy })) : nodes;
    // 저장된 배치의 크기로 그림(정리하면 좁고 높아짐 — 서버 좌표의 넓은 폭을 남기지 않음, 20쪽)
    return { ...graph, nodes: out, width: Math.max(320, maxX + dx + NODE_W + 40), height: Math.max(160, maxY + dy + NODE_H + 40) };
  }

  /* ---------------------------------------------------------------- hooks: layout · decorate */
  function layout(graph, ctx) {
    if (!isPack(ctx)) return graph;
    ensureEdit(ctx); ensureLayout(ctx);
    if (editing(ctx) && !Object.keys(ST.labels).length && !ST.labelsAsked && graph.nodes && graph.nodes[0]) {   // 쉬운 이름 한 번 읽기
      ST.labelsAsked = true;
      ctx.api("GET", `/api/projects/${ctx.p.id}/edit/nodes/${encodeURIComponent(graph.nodes[0].name)}/io`).then(keepLabels).catch(() => {});
    }
    // 편집 중이면 편집본 그래프(새 node · 연결 포함) — 서버 EDIT의 graph 칸(api-contract 보완 요청)
    const original = !!(ctx.ui && ctx.ui.edView === "original");   // S-9 원본 보기: 출발 버전의 원래 그래프(보기만)
    const base = editing(ctx) && !original && ST.edit && ST.edit.graph && ST.edit.graph.nodes ? ST.edit.graph : graph;
    // 저장된 배치가 없으면 ⌗ 노드 정리와 같은 배치로 보임(저장하지 않음 — 5 · 11쪽의 접힌 그래프, 리드 몫 4). 저장된 배치에 없는 새 node는
    // 정리 배치 자리로
    // 기본 배치는 출발 버전 그래프로 한 번 정한 정리 배치 — 편집으로 node가 늘어도 있던 node는 움직이지 않음(19쪽 ①), 새 node는 놓은 자리
    // D-459 (user 10-29, Q-1): every graph is drawn in the one tidy layout (app.js tidied · web/tidy.js) from the first look — a layout
    // saved before (no '__v2' mark) is not used for drawing; '⌗ 노드 정리' lays it out again and saves the mark
    // the branch names first (packrt 053: the names' lines set a gap's height — every screen lays out the graph as it is shown)
    const named = typeof branchNamed === "function" && ctx.rel ? branchNamed(base, ctx.rel.branchLabels) : base;
    const g = typeof tidied === "function" ? tidied(named) : (() => { const auto = tidyPositions(graph); return sized(base, base.nodes.map(n => auto[n.name] ? { ...n, x: auto[n.name][0], y: auto[n.name][1] } : n)); })();
    if (ST.ghost && editing(ctx)) { ST.graph = { ...g, height: Math.max(g.height || 0, ST.ghost.y + NODE_H + 24) }; return ST.graph; }   // 놓은 자리가 그래프 아래면 그만큼 늘림(14쪽)
    ST.graph = editing(ctx) ? { ...g, pending: (ST.edit && ST.edit.check && ST.edit.check.pending) || [] } : g;
    return ST.graph;
  }
  function style() {
    if (ST.styled || typeof document === "undefined") return;
    ST.styled = true;
    const s = document.createElement("style");
    s.id = "cv-style";
    s.textContent = `.cv-bar{display:flex;justify-content:flex-end;align-items:center;gap:8px;margin:0 0 4px;flex-wrap:wrap;min-width:0;position:sticky;left:0}
.cv-toast{font-size:12px;background:#ecfdf3;border:1px solid #abefc6;color:#067647;border-radius:8px;padding:3px 8px}
svg.cv-edit .cv-dot{opacity:0;cursor:crosshair;pointer-events:none}svg.cv-edit .graph-node:hover .cv-dot,svg.cv-edit .graph-node.sel .cv-dot{opacity:1;pointer-events:auto}
svg.cv-edit .cv-edge .cv-plus{opacity:0;cursor:pointer}svg.cv-edit .cv-edge:hover .cv-plus{opacity:1}
.graph-node.cv-no{opacity:.45}
.graph-node.cv-next [data-outline]{stroke:#98a2b3!important;stroke-dasharray:4 3}
#cv-dialog{position:fixed;z-index:30;width:320px;max-width:calc(100vw - 32px);max-height:80vh;overflow:auto;background:#fff;border:1px solid #d0d5dd;border-radius:12px;box-shadow:0 8px 24px rgba(16,24,40,.18);padding:12px;font-size:12.5px}
#cv-dialog h4{margin:0 0 8px;font-size:14px;display:flex;gap:6px;align-items:baseline}#cv-dialog .x{cursor:pointer;color:#667085;margin-left:auto}
#cv-dialog input[type=search]{width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #d0d5dd;border-radius:8px;margin-bottom:8px}
.cv-io{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}.cv-io div{background:#f9fafb;border-radius:8px;padding:6px;min-width:0}
.cv-kind{border:1px solid #e4e7ec;border-radius:8px;padding:7px 8px;margin-top:6px;cursor:pointer}.cv-kind b{font-size:13px}.cv-kind code{font-size:11px;color:#667085;margin-left:4px}
.cv-kind.rec{border-color:#7a5af8;background:#f4f3ff}.cv-kind.off{cursor:not-allowed;color:#98a2b3;background:#f9fafb}
.cv-sec{font-size:11.5px;color:#667085;margin-top:8px}.cv-err{color:#b42318;margin-top:6px}.cv-ok-t{color:#067647;margin-top:6px}
.cv-row{display:flex;gap:6px;justify-content:flex-end;margin-top:10px;flex-wrap:wrap}.cv-btn{border:1px solid #d0d5dd;border-radius:8px;padding:5px 10px;cursor:pointer;background:#fff}
.cv-btn.p{background:#7a5af8;color:#fff;border-color:#7a5af8}.cv-btn.d{background:#d92d20;color:#fff;border-color:#d92d20}.cv-btn.off{opacity:.45;cursor:not-allowed}
.cv-case{display:flex;gap:6px;align-items:center;margin-top:4px}
.graph-node.cv-chg [data-outline]{stroke:#dc6803!important;stroke-width:2px!important}.graph-node.cv-new [data-outline]{stroke:#dc6803!important;stroke-dasharray:5 4!important;fill:#fffaeb!important}
.graph-node.cv-red [data-outline]{stroke:#d92d20!important;stroke-width:2.5px!important}.graph-node.cv-fade{opacity:.4}.graph-node.cv-del [data-outline]{stroke:#f97066!important;stroke-dasharray:5 4!important}.graph-node.cv-del{opacity:.6}
.graph-node.cv-tgt [data-outline]{stroke:#12b76a!important;stroke-width:2px!important}
.graph-node.cv-ok [data-outline]{stroke:#12b76a!important;stroke-width:3px!important;stroke-dasharray:none!important}
.cv-hint{font-size:11.5px;color:#667085;margin-top:2px;position:sticky;left:0}.cv-foot{display:flex;justify-content:center;margin:4px 0;flex-wrap:wrap;gap:6px;position:sticky;left:0}
.cv-note{position:sticky;left:0;font-size:12px;background:#fff;border:1px solid #abefc6;border-radius:10px;padding:6px 10px;margin:4px 0;max-width:100%;box-sizing:border-box}.cv-note b{color:#067647}`;
    document.head.appendChild(s);
  }
  // "다음 연결 필요": 서버가 END로 못 가거나 START에서 못 닿는다고 알린 node(check.pending, api-contract v1.2 — 14쪽 #5,
  // 반영은 되고 확정 때만 막힘). pending이 없던 서버 응답이면 검사 문구에서 읽음
  function needsNext() {
    const chk = (ST.edit && ST.edit.check) || {}, out = new Set(Array.isArray(chk.pending) ? chk.pending : []);
    for (const e of chk.errors || []) { const m = /node '([a-z][a-z0-9_]*)'(?:에서 END로 가는 경로가 없습니다|는 START에서 닿지 않습니다)/.exec(e.message || ""); if (m) out.add(m[1]); }
    return out;
  }
  // 편집에서 값을 고친 node(주황 · '바뀜')와 이 편집에서 만든 node(주황 점선 = 새 node(반영 전)) — 설명서 범례(11쪽)
  function editMarks(ctx) {
    const changed = new Set(), made = new Set(), base = new Set(((ctx.rel.graph && ctx.rel.graph.nodes) || []).map(n => n.name));
    for (const n of (ST.graph ? ST.graph.nodes : [])) if (!base.has(n.name)) made.add(n.name);
    for (const l of (ST.edit && ST.edit.lines) || []) if (l.node && l.kind === "value" && !made.has(l.node)) changed.add(l.node);
    return { changed, made };
  }
  // 확정한 버전(평가 중 · 승인됨 · 거절 · 운영)의 그래프: 출발 버전에서 바뀐 · 더한 node에 '바뀜'(23 · 27 · 29쪽, 리드 몫 10) — 확정 기록의 nodes.
  // 운영이 되어도 기준은 출발 버전이라 남습니다(29쪽 그림, 답장 019-A-es09-review 3절 1)
  function confirmedMarks(svg, ctx) {
    const v = ctx.rel.version, nodes = (v && v.from && v.from.nodes) || [];
    if (editing(ctx) || !nodes.length || v.state === "이전" || !ST.graph) return svg;
    const byName = Object.fromEntries(ST.graph.nodes.map(n => [n.name, n])), set = new Set(nodes);
    style();
    return svg.replace(/(<g class="graph-node)([^"]*"[^>]*data-node="([^"]+)"[^>]*>)([\s\S]*?)(<\/g>)/g, (m, a, rest, name, body, close) => {
      const n = byName[name.replace(/&amp;/g, "&")];
      return n && set.has(n.name) ? a + " cv-chg" + rest + body + badge(n, "바뀜", "#dc6803") + close : m;
    });
  }
  function dotsFor(n) {
    const { x, y } = n, dia = kindOf(n) === "router";
    const pts = dia ? [[x + 45, y - 5], [x + 105, y + 17], [x + 45, y + 39], [x - 15, y + 17]] : [[x + 45, y], [x + NODE_W, y + 17], [x + 45, y + NODE_H], [x, y + 17]];
    return pts.map(([cx, cy], i) => `<circle class="cv-dot" data-cv-dot="${esc(n.name)}" data-side="${i}" cx="${cx}" cy="${cy}" r="5" fill="#fff" stroke="#7a5af8" stroke-width="2"/>`).join("");
  }
  const badge = (n, text, color) => `<g class="cv-badge"><rect x="${n.x + NODE_W - 34}" y="${n.y - 9}" width="${Math.max(30, text.length * 10 + 10)}" height="14" rx="4" fill="${color}"/>` +
    `<text x="${n.x + NODE_W - 29}" y="${n.y + 1}" font-size="9.5" font-weight="700" fill="#fff">${esc(text)}</text></g>`;
  function decorate(svg, ctx) {
    ST.ctx = ctx;
    if (!isPack(ctx)) return svg;
    style();
    const bar = `<div class="cv-bar"><span class="btn" data-act="cv-tidy" id="cv-tidy">⌗ 노드 정리</span></div>`;
    // 정리 결과(20쪽): 그래프 아래 '정리했습니다 … ↶ 정리 취소'와 정리 규칙 한 줄
    const tidied = ST.toast && ST.toast.key === layoutKey(ctx);
    const foot = tidied ? `<div class="cv-foot"><span class="cv-toast" id="cv-toast"><b>정리했습니다</b> 배치만 바뀜 · revision · 변경 목록 그대로 <span class="link" data-act="cv-untidy" id="cv-untidy">↶ 정리 취소</span></span></div>` +
      `<div class="cv-hint" id="cv-tidy-rule">정리 규칙: 왼쪽 → 오른쪽 · 길면 아래로 접어 반대로 · 갈래 · 나란한 node는 위아래로 · 선 겹침 최소</div>` : "";
    if (!editing(ctx) || !ST.graph || (ctx.ui && ctx.ui.edView === "original")) return bar + confirmedMarks(svg, ctx) + foot;   // S-9: 원본 보기는 점 · ＋ 없이
    const g = ST.graph, byName = Object.fromEntries(g.nodes.map(n => [n.name, n])), next = needsNext(), { changed, made } = editMarks(ctx);
    const mk = ST.mark || {}, red = new Set(mk.red || []), fade = new Set(mk.fade || []), del = new Set(mk.del || []), tgt = new Set(mk.tgt || []), badges = mk.badges || {};
    let out = svg.replace("<svg ", `<svg class="cv-edit" `);
    out = out.replace(/(<g class="graph-node[^"]*"[^>]*data-node="([^"]+)"[^>]*>)([\s\S]*?)(<\/g>)/g, (m, open, name, body, close) => {
      const n = byName[name.replace(/&amp;/g, "&")];
      if (!n) return m;
      const cls = [next.has(n.name) && "cv-next", changed.has(n.name) && "cv-chg", made.has(n.name) && "cv-new", red.has(n.name) && "cv-red",
        fade.has(n.name) && "cv-fade", del.has(n.name) && "cv-del", tgt.has(n.name) && "cv-tgt"].filter(Boolean).join(" ");
      const o = cls ? open.replace('class="graph-node', `class="graph-node ${cls}`) : open;
      let extra = next.has(n.name) ? `<text x="${n.x}" y="${n.y + NODE_H + 13}" font-size="10" fill="#667085">다음 연결 필요</text>` : "";
      if (badges[n.name]) extra += badge(n, badges[n.name], "#d92d20");
      else if (changed.has(n.name)) extra += badge(n, "바뀜", "#dc6803");
      if (kindOf(n) === "custom_code") extra += `<text class="cv-lock" x="${n.x + NODE_W - 12}" y="${n.y + NODE_H - 4}" font-size="10">🔒</text>`;   // #13
      return o + body + extra + (n.blank ? "" : dotsFor(n)) + close;   // EV-073: a blank node gets its connections when it gets a kind
    });
    // 지우기 미리보기의 빨간 점선(17쪽) · 고른 연결(#14)
    const redEdges = new Set((mk.edges || []).map(([s, t]) => `${s}>${t}`));
    out = out.replace(/(<(?:line|path) data-edge="([^"]+)"[^>]*?)(\/>)/g, (m, head, id, tail) => {
      const key = id.replace(/&amp;/g, "&").replace(/&gt;/g, ">");
      if (redEdges.has(key)) return head.replace(/stroke="[^"]*"/, 'stroke="#d92d20"').replace(/stroke-dasharray="[^"]*"/, "") + ' stroke-dasharray="5 4"' + tail;
      if (ST.selEdge === key) return head.replace(/stroke="[^"]*"/, 'stroke="#7a5af8"').replace(/stroke-width="[^"]*"/, 'stroke-width="3"') + tail;
      return m;
    });
    // 선 고르기 칸 · ＋는 그려진 선(꺾인 선, E-S-09 L1)을 따라감 — 양 끝 14px은 node 점 자리라 비움, ＋는 가장 긴 토막 가운데
    const drawn = {};
    const tidyRoutes = (ST.graph && ST.graph.tidy && ST.graph.tidy.routes) || null;   // D-467: a curve is followed by the layout's own points
    for (const m of svg.matchAll(/<path data-edge="([^"]+)"[^>]*? d="M([^"]+)"/g)) { const id = m[1].replace(/&amp;/g, "&").replace(/&gt;/g, ">");
      drawn[id] = tidyRoutes && tidyRoutes[id] ? tidyRoutes[id].pts : /C/.test(m[2]) ? null : m[2].split(" L").map(p => p.split(",").map(Number)); }
    const hits = g.edges.filter(e => e.kind !== "loop" && byName[e.source] && byName[e.target]).map(e => {
      const a = byName[e.source], b = byName[e.target], pts = (drawn[`${e.source}>${e.target}`] || [[a.x + NODE_W, a.y + 17], [b.x, b.y + 17]]).map(p => [...p]);
      const cut = (p, q) => { const d = Math.hypot(q[0] - p[0], q[1] - p[1]); if (d > 60) { p[0] += (q[0] - p[0]) * 14 / d; p[1] += (q[1] - p[1]) * 14 / d; } };   // a short line keeps its whole length to be chosen
      const n = pts.length; cut(pts[0], pts[1]); cut(pts[n - 1], pts[n - 2]);
      let mx = 0, my = 0, best = -1;
      for (let i = 1; i < n; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); if (d > best) { best = d; mx = (pts[i][0] + pts[i - 1][0]) / 2; my = (pts[i][1] + pts[i - 1][1]) / 2; } }
      if (tidyRoutes && n > 4) {   // D-467: on a curve read in many small pieces, ＋ at half the line's length
        const len = pts.slice(1).map((q, i) => Math.hypot(q[0] - pts[i][0], q[1] - pts[i][1])), half = len.reduce((a, b) => a + b, 0) / 2;
        let run = 0; for (let i = 0; i < len.length; i++) { if (run + len[i] >= half) { const t = (half - run) / (len[i] || 1); mx = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t; my = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t; break; } run += len[i]; }
      }
      return `<g class="cv-edge"><polyline data-act="cv-edge" data-a="${esc(e.source)}" data-b="${esc(e.target)}" data-edge-hit="${esc(e.source)}>${esc(e.target)}" points="${pts.map(p => p.join(",")).join(" ")}" fill="none" stroke="transparent" stroke-width="14" style="cursor:pointer"/>` +
        `<g class="cv-plus" data-act="cv-plus" data-a="${esc(e.source)}" data-b="${esc(e.target)}" data-edge-plus="${esc(e.source)}>${esc(e.target)}"><circle cx="${mx}" cy="${my}" r="8" fill="#fff" stroke="#7a5af8" stroke-width="2"/>` +
        `<text x="${mx}" y="${my + 4}" text-anchor="middle" font-size="12" font-weight="700" fill="#7a5af8">+</text></g></g>`;
    }).join("");
    // 빈 곳에 놓은 자리(14 · 16쪽 '여기에 놓음' · '새 node')
    const gh = ST.ghost && byName[ST.ghost.from] ? (() => {
      const a = byName[ST.ghost.from], x = ST.ghost.x - NODE_W / 2, y = ST.ghost.y - NODE_H / 2;
      return `<g id="cv-ghost"><line x1="${a.x + 45}" y1="${a.y + NODE_H}" x2="${x + 45}" y2="${y}" stroke="#7a5af8" stroke-width="2" stroke-dasharray="5 4"/>` +
        `<rect x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}" rx="6" fill="#faf9ff" stroke="#7a5af8" stroke-width="1.5" stroke-dasharray="5 4"/>` +
        `<text x="${x + 45}" y="${y + 21}" text-anchor="middle" font-size="10.5" fill="#6941c6">${esc(ST.ghost.label)}</text></g>`;
    })() : "";
    // 선 고르기 칸 · ＋는 node 아래에(node 클릭 · 마우스가 먼저), 놓은 자리 상자는 맨 위에
    const firstNode = out.indexOf('<g class="graph-node');
    out = firstNode >= 0 ? out.slice(0, firstNode) + `<g id="cv-overlay">${hits}</g>` + out.slice(firstNode) : out.replace(/<\/svg>\s*$/, `<g id="cv-overlay">${hits}</g></svg>`);
    out = out.replace(/<\/svg>\s*$/, `${gh}</svg>`);
    const note = ST.note && ST.note.key === layoutKey(ctx) ? `<div class="cv-note" id="cv-note">${ST.note.html}</div>` : "";
    return bar + out + note + foot;
  }
  // 11쪽 안내 줄은 범례 아래(리드 몫 1, 합치기 3): app.js가 범례 뒤에 부름
  function editHint(ctx) {
    return editing(ctx) && !(ctx.ui && ctx.ui.edView === "original") ? `<div class="cv-hint" id="cv-edit-hint">${hv("편집 중 · 실행 없음", "편집 중 — 실행 없음(검사 · 미리보기만) · node에 마우스 → 네 방향 점(끌어 만들기 · 잇기) · 선에 마우스 → ＋(사이에 끼우기) · 지우기 = 고르고 Delete")}</div>` : "";
  }

  /* ---------------------------------------------------------------- graph helpers */
  const nodeOf = name => ST.graph && ST.graph.nodes.find(n => n.name === name);
  const edgesOf = () => (ST.graph && ST.graph.edges) || [];
  function reaches(from, to) {   // to에서 from으로 돌아가는 길이 있나(loop 연결 제외)
    const seen = new Set([from]), stack = [from];
    while (stack.length) { const x = stack.pop(); if (x === to) return true; for (const e of edgesOf()) if (e.source === x && e.kind !== "loop" && !seen.has(e.target)) { seen.add(e.target); stack.push(e.target); } }
    return false;
  }
  // 끄는 중 화면 판정(31쪽 #6 · #8 · 받는 값 형식, 33쪽 문구) — 서버 검사(packrt)는 놓을 때. 돌려주는 값: "" 또는 이유
  const START_END = "START로 들어가거나 END에서 나가는 연결은 쓸 수 없습니다";
  const BACK_TITLE = "✕ 되돌아가는 연결";
  function edgeCheck(from, to) {
    if (to === "START" || from === "END") return START_END;
    if (from === to) return "자기 자신으로는 연결할 수 없습니다";
    const a = nodeOf(from), b = nodeOf(to);
    if (!a || !b) return "그 자리에는 놓을 수 없습니다";
    if (edgesOf().some(e => e.source === from && e.target === to)) return "이미 있는 연결입니다";
    if (reaches(to, from)) return "조건 없이 돌면 끝나지 않습니다. 다시 돌리려면 router 갈래로, 빠져나갈 길과 함께";
    const give = new Set([...(a.writes || [])]);
    if (kindOf(a) !== "router" && (b.reads || []).length && !(b.reads || []).some(k => give.has(k)))
      return `${b.name}는 ${Ls(b.reads)}만 받음`;
    return "";
  }
  const checkTitle = why => /끝나지 않습니다/.test(why) ? BACK_TITLE : `✕ ${why === START_END || /자기 자신|이미 있는/.test(why) ? "놓을 수 없는 자리" : "받는 값이 맞지 않음"}`;
  function uniqueId(base) {
    const have = new Set((ST.graph ? ST.graph.nodes : []).map(n => n.name));
    let id = base.replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "") || "node", i = 2;
    while (have.has(id)) id = `${base}_${i++}`;
    return id;
  }
  // 끼운 node 자리: 두 node 사이에서 다른 node와 겹치지 않는 가장 가까운 곳(위 · 아래로 한 줄씩)
  function freeSpot(x, y) {
    const busy = (ST.graph ? ST.graph.nodes : []).map(n => [n.x, n.y]);
    const free = (cx, cy) => busy.every(([bx, by]) => Math.abs(bx - cx) > NODE_W + 8 || Math.abs(by - cy) > NODE_H + 10);
    for (const dy of [0, -50, 50, -100, 100, -150, 150]) if (free(x, y + dy)) return [x, Math.max(12, y + dy)];
    return [x, y + 200];
  }
  const template = kind => ST.graph && ST.graph.nodes.find(n => n.kind === kind);
  // 고를 수 있는 종류와 회색 이유(#1 · #3). 맥락 묶음(context)의 addable_kinds가 있으면 그대로 따름
  function kindChoices(from, to, ctxInfo) {
    const add = ctxInfo && Array.isArray(ctxInfo.addable_kinds) ? Object.fromEntries(ctxInfo.addable_kinds.map(k => [k.kind, k])) : null;
    const a = nodeOf(from), b = to ? nodeOf(to) : null;
    return KINDS.map(k => {
      let reason = "";
      if (add && add[k.kind] && add[k.kind].ok === false) reason = add[k.kind].reason || "이 자리에는 넣을 수 없습니다";
      else if (!template(k.kind)) reason = "Pack에 이 종류의 본보기가 없음 — Pack 쪽 자리 요청";
      else if (k.kind.startsWith("validate")) {
        const used = ST.graph.nodes.filter(n => kindOf(n) === "validate").map(n => n.name);
        reason = `담을 자리 없음 — 검사 결과 자리를 ${used.join(" · ")}가 이미 씀`;
      }
      return { ...k, reason, rec: false };
    }).sort((x, y) => (x.reason ? 1 : 0) - (y.reason ? 1 : 0))
      .map(k => {
        const shared = a && b ? (a.writes || []).filter(x => (b.reads || []).includes(x)) : [];
        const want = shared.includes("evidence") ? "retrieve@1" : (b && kindOf(b) === "validate") || shared.length ? "llm@1" : !b ? "retrieve@1" : "llm@1";
        return { ...k, rec: !k.reason && k.kind === want };
      }).sort((x, y) => (y.rec ? 1 : 0) - (x.rec ? 1 : 0));
  }
  // 새 node: 같은 종류의 node를 본보기로 params를 가져오고, 입력은 앞 node가 내보내는 값으로 고정(14쪽)
  function makeNode(kind, from, to, id) {
    const t = template(kind), a = nodeOf(from), b = to ? nodeOf(to) : null;
    const tp = JSON.parse(JSON.stringify((t && t.params) || {}));
    const shared = a && b ? (a.writes || []).filter(x => (b.reads || []).includes(x)) : [];
    const node = { id, kind, reads: [], writes: [] };
    if (kind === "llm@1") {
      node.reads = [...new Set([...(a ? a.reads || [] : []), ...(a ? a.writes || [] : [])])];
      const w = shared[0] || (a && (a.writes || [])[0]) || (t.writes || [])[0];
      node.writes = w ? [w] : [];
      const writer = ST.graph.nodes.find(n => kindOf(n) === "llm" && (n.writes || []).includes(w));
      node.params = { provider: tp.provider, model: tp.model, prompt: (writer && writer.params.prompt) || tp.prompt, output: (writer && writer.params.output) || tp.output };
      if (tp.temperature !== undefined) node.params.temperature = tp.temperature;
    } else if (kind === "retrieve@1") { node.reads = t.reads || []; node.writes = t.writes || []; node.params = tp; }
    else if (kind === "render@1") { node.reads = t.reads || []; node.writes = t.writes || []; node.params = tp; }
    else if (kind === "router@1") {
      node.reads = (a && a.writes && a.writes.length ? [a.writes[0]] : t.reads) || [];
      node.params = { by: node.reads[0] ? `state.${node.reads[0]}.passed` : tp.by, cases: { "true": to || "END" }, default: to || "END" };
    } else { node.reads = t.reads || []; node.writes = t.writes || []; node.params = { rules: [] }; }
    node.expect = { text: `${(KINDS.find(k => k.kind === kind) || {}).name || kind} — 화면에서 만듦(설명은 고치기에서)` };
    return node;
  }
  // router 연결은 params에만 적힘(#7): 고른 갈 조건(cases 키 또는 default)의 대상을 바꾸는 set op
  // default가 같은 곳으로 가던 조건이면 default도 함께 옮김(화면에 따로 보이지 않는 default가 옛 대상으로 선을 남기지 않게)
  function routeOps(ctx, router, key, target) {
    const r = nodeOf(router), p = (r && r.params) || {};
    if (key === "default") return [{ op: "set", graph: gname(ctx), node: router, param: "default", value: target }];
    const old = (p.cases || {})[key], ops = [{ op: "set", graph: gname(ctx), node: router, param: "cases", value: { ...(p.cases || {}), [key]: target } }];
    if (p.default && p.default === old) ops.push({ op: "set", graph: gname(ctx), node: router, param: "default", value: target });
    return ops;
  }
  function routeKeys(router) {
    const r = nodeOf(router), p = (r && r.params) || {};
    const cases = Object.entries(p.cases || {}).map(([k, t]) => ({ key: k, target: t }));
    // default가 어느 조건과 같은 곳으로 가면 따로 보이지 않음(16쪽은 예 · 아니오 두 줄)
    return [...cases, ...(p.default && !cases.some(c => c.target === p.default) ? [{ key: "default", target: p.default }] : [])];
  }

  /* ---------------------------------------------------------------- server calls */
  async function sendOps(ctx, ops, place, extra) {
    try {
      const r = await ctx.api("POST", `/api/projects/${ctx.p.id}/releases/${ctx.rel.id}/edit/ops`, { ops, source: "person", ...(extra || {}) });
      ST.edit = r.edit || ST.edit;
      if (place) await savePositions(ctx, place);
      close(); await reloadState(ctx);
      return true;
    } catch (e) {
      const d = e.detail || {}, all = d.errors || [];
      // packrt의 '동시에 실행될 수 있는 …' 문구는 다른 문제를 따라 여럿 나옴 — 주 문구를 먼저, 그 문구는 수만(33쪽 #9 '문제 1')
      const conc = x => /^동시에 실행될 수 있는/.test(x.message || ""), main = all.filter(x => !conc(x)), side = all.filter(conc), errs = main.length ? main : all;
      const msg = d.kind === "edit-elsewhere" ? `편집 중인 것이 있습니다(${d.release}에서) · 확정하거나 버린 뒤 고칠 수 있습니다`
        : (errs.map(x => x.message).join(" / ") + (main.length && side.length ? ` / 함께 생기는 동시 실행 문구 ${side.length}건` : "")) || e.message || "반영할 수 없습니다";
      // 서버 검사 문구가 가리키는 node를 그래프에서 빨강으로(33쪽 #9 '검토 대상 없음', 31쪽 '반영 잠김')
      const red = [], badges = {};
      for (const x of errs) for (const m of String(x.message || "").matchAll(/'([a-z][a-z0-9_]*)'/g)) if (nodeOf(m[1])) {
        red.push(m[1]); if (/검토 대상/.test(x.message)) badges[m[1]] = "검토 대상 없음";
      }
      // 32쪽 #4: 연결을 바꿔 이 편집에서 만든 node가 고른 값이 그 자리에서 아직 없음(packrt 흐름 검사 F1) — 칸 빨강 · 그 값을 만드는 node 강조 ·
      // 반영 잠김(서버가 거절해 저장 안 됨) · 다시 고르기 / 연결 되돌리기
      const made = editMarks(ctx).made, f1 = all.map(x => /node '([a-z][a-z0-9_]*)'에 닿는 경로 중 reads '([a-z0-9_]+)'를 앞에서 쓰지 않는 경로가 있습니다/.exec(x.message || "")).find(m => m && made.has(m[1]));
      if (f1) {
        const [, n, key] = f1, makers = (ST.graph ? ST.graph.nodes : []).filter(x => (x.writes || []).includes(key)).map(x => x.name);
        ST.mark = { red: [n], tgt: makers, badges: { [n]: "고른 값 없음" } }; rerender(ctx);
        const r = ST.lastPoint || { x: 200, y: 200 };
        open(`<h4>고른 값 없음 <span class="muted">${esc(n)}</span> <span class="x" data-cv="close">✕</span></h4><div class="cv-err" id="cv-picked-gone"><b>고른 값이 이 자리에서는 아직 없음</b></div>` +
          `<div>${esc(n)}가 고른 '${esc(L(key))}'는 ${makers.map(esc).join(" · ") || "앞 node"}이(가) 만듭니다. 바꾼 연결로 오면 이 값이 아직 비어 있습니다</div><div class="small muted">${esc(f1[0])}</div>` +
          `<div class="cv-row"><span class="cv-btn p" data-cv="repick" data-node="${esc(n)}" id="cv-repick">다시 고르기</span><span class="cv-btn" data-cv="close" id="cv-undo-link">연결 되돌리기</span></div>`, r.x, r.y);
        ST.pending = { type: "repick", node: n };
        return false;
      }
      if (red.length) {
        ST.mark = { ...(ST.mark || {}), red, badges: { ...((ST.mark || {}).badges || {}), ...badges } }; rerender(ctx);
        // the reason window opens on the other side of the screen from the red node, so the node and its badge stay in sight (33쪽 #9)
        const el = document.querySelector(`.graph-node[data-node="${CSS.escape(red[0])}"]`), r = el && el.getBoundingClientRect();
        if (r) ST.lastPoint = { x: r.left + r.width / 2 < window.innerWidth / 2 ? window.innerWidth - 360 : 230, y: 120 };
      }
      showError(msg);
      return false;
    }
  }
  async function savePositions(ctx, extra) {
    const pos = {};
    for (const n of (ST.graph ? ST.graph.nodes : [])) pos[n.name] = [n.x, n.y];
    Object.assign(pos, extra || {});
    const key = layoutKey(ctx);
    try { await ctx.api("PUT", layoutUrl(ctx), { positions: pos }); ST.layouts[key] = pos; } catch (e) { /* 위치 저장 실패는 배치만 서버 좌표로 남음 */ }
  }
  async function preview(ctx, ops) {
    return ctx.api("POST", `/api/projects/${ctx.p.id}/releases/${ctx.rel.id}/edit/preview`, { ops });
  }
  // a removal checked by preview (when delete-impact cannot read an edit with a node waiting for its connection): the nodes it newly
  // cuts off — END/START messages of nodes that were not waiting before (a waiting node's own messages do not stop a save, p.14)
  async function cutBy(ctx, ops) {
    const pv = await preview(ctx, ops), was = new Set((((ST.edit || {}).check) || {}).pending || []), noEnd = [], unreach = [], errs = [];
    for (const z of pv.all_errors || pv.errors || []) {
      const m = String(z.message || ""), e = /node '([a-z0-9_]+)'에서 END로 가는 경로가 없습니다/.exec(m), s = /node '([a-z0-9_]+)'는 START에서 닿지 않습니다/.exec(m);
      if ((e && was.has(e[1])) || (s && was.has(s[1])) || /^동시에 실행될 수 있는/.test(m)) continue;
      if (e) noEnd.push(e[1]); if (s) unreach.push(s[1]); errs.push(m);
    }
    return { ok: pv.ok && !noEnd.length && !unreach.length && !errs.length, errs, noEnd, unreach };
  }

  /* ---------------------------------------------------------------- dialog */
  let dlg = null;
  function open(html, x, y) {
    if (!dlg) { dlg = document.createElement("div"); dlg.id = "cv-dialog"; document.body.appendChild(dlg); }
    dlg.innerHTML = html; dlg.hidden = false;
    const w = Math.min(320, window.innerWidth - 32);
    dlg.style.left = `${Math.max(16, Math.min(x, window.innerWidth - w - 16))}px`;
    dlg.style.top = `${Math.max(16, Math.min(y, window.innerHeight - (dlg.offsetHeight || 120) - 16))}px`;   // the whole window stays on the screen
    const s = dlg.querySelector("input[type=search]"); if (s) s.focus();
  }
  function fit() {   // the window grows (a chosen condition adds '옮기기 / 앞에 끼우기'): keep it on the screen
    if (!dlg || dlg.hidden) return;
    const top = parseFloat(dlg.style.top) || 16, h = dlg.offsetHeight;
    if (top + h > window.innerHeight - 16) dlg.style.top = `${Math.max(16, window.innerHeight - h - 16)}px`;
  }
  function close() {
    const was = !!(ST.mark || ST.ghost);
    if (dlg) { dlg.hidden = true; dlg.innerHTML = ""; }
    ST.pending = null; ST.mark = null; ST.ghost = null;
    if (was) rerender(ST.ctx);
  }
  function showError(msg) {   // 이유는 창 맨 위(제목 아래)에 — 긴 목록 아래로 숨지 않게
    if (!dlg || dlg.hidden) return alertBox(msg);
    let el = dlg.querySelector(".cv-err");
    if (!el) { el = Object.assign(document.createElement("div"), { className: "cv-err", id: "cv-err" }); const h = dlg.querySelector("h4"); h ? h.after(el) : dlg.prepend(el); }
    const parts = String(msg).split(" / ");   // packrt 문구는 그대로, 너무 길면 앞 3개와 나머지 수
    el.textContent = parts.length > 3 ? `${parts.slice(0, 3).join(" / ")} … 외 ${parts.length - 3}건` : msg;
    dlg.scrollTop = 0;
  }
  function alertBox(msg, title) { const r = ST.lastPoint || { x: 200, y: 200 }; open(`<h4>${esc(title || "반영할 수 없음")} <span class="x" data-cv="close">✕</span></h4><div class="cv-err" id="cv-err">${esc(msg)}</div><div class="cv-row"><span class="cv-btn" data-cv="close">닫기</span></div>`, r.x, r.y); }
  const ioBox = (inp, outp) => `<div class="cv-io"><div><span class="muted">입력 ←</span><br>${inp}</div><div><span class="muted">출력 →</span><br>${outp}</div></div>`;
  function kindList(choices) {
    const rec = choices.filter(k => k.rec), rest = choices.filter(k => !k.rec);
    const item = k => `<div class="cv-kind${k.rec ? " rec" : ""}${k.reason ? " off" : ""}" ${k.reason ? "" : `data-cv="kind" data-kind="${k.kind}"`} data-kind-row="${k.kind}" data-search="${esc(k.name + " " + k.desc + " " + k.kind)}"><b>${esc(k.name)}</b><code>${k.kind}</code><div class="small">${esc(k.reason || k.desc)}</div></div>`;
    const blocked = BLOCKED.map(([k, n]) => `<div class="cv-kind off" data-kind-row="${k}" data-search="${esc(n + " " + k)}"><b>${esc(n)}</b><code>${k}</code><div class="small">'${k}' node는 추가할 수 없습니다 — LLMOps에서는 추가 안 됨, Pack 저장소에서</div></div>`).join("");
    // 32쪽 #3: 담을 자리가 Pack에 없으면 '담을 빈 자리가 없습니다 — …' · Pack 쪽 자리 요청 남기기(기존 요청서 P11에 한 장, 답장 019-A E-S-03 Q2) / 취소
    const slot = choices.find(k => k.reason && /^담을 자리 없음/.test(k.reason));
    const ask = slot ? `<div class="cv-sec" id="cv-slot-ask"><span class="cv-err">담을 빈 자리가 없습니다 — 같이 쓰면 앞 결과를 덮어씁니다</span></div>` +
      `<div class="cv-row" style="justify-content:flex-start"><span class="cv-btn p" data-cv="slot-request" data-kind="${slot.kind}" id="cv-slot-request">Pack 쪽 자리 요청 남기기</span><span class="cv-btn" data-cv="close">취소</span></div>` : "";
    // EV-073 (D-443, 설명서 35쪽): 종류 없이 역할 글만 — 고를 수 있는 종류 다음 한 줄(추천이 첫 줄인 것은 그대로, PRES-AI-1)
    const blank = `<div class="cv-kind cv-blank" data-cv="kind" data-kind="blank" data-kind-row="blank" data-search="빈 node 역할"><b>빈 node</b><code>역할만</code></div>`;
    return (rec.length ? `<div class="cv-sec">추천</div>${rec.map(item).join("")}` : "") + `<div class="cv-sec">다른 종류</div>${rest.filter(k => !k.reason).map(item).join("")}${blank}${rest.filter(k => k.reason).map(item).join("")}${ask}${blocked}`;
  }
  async function contextOf(ctx, node) {
    try { return keepLabels(await ctx.api("GET", `/api/projects/${ctx.p.id}/edit/nodes/${encodeURIComponent(node)}/context`)); } catch (e) { return null; }
  }

  // 선 위 ＋ — 사이에 끼우기(12쪽)
  async function openInsert(ctx, a, b, x, y) {
    const A = nodeOf(a), B = nodeOf(b);
    if (!A || !B) return;
    const info = await contextOf(ctx, a);
    const shared = (A.writes || []).filter(k => (B.reads || []).includes(k));
    const cond = kindOf(A) === "router" ? routeKeys(a).filter(r => r.target === b).map(r => r.key) : [];
    ST.pending = { type: "insert", a, b, cond, x, y };
    open(`<h4>사이에 끼우기 <span class="x" data-cv="close">✕</span></h4><input type="search" id="cv-search" placeholder="무엇을 넣을까요? (예: 다듬기, 리트리버)">` +
      ioBox(`${esc(Ls(A.writes) || "—")} <span class="muted">(${esc(a)}가 내보냄)</span>`, `${esc(b)}가 받음(${esc(Ls(shared.length ? shared : B.reads))})`) +
      kindList(kindChoices(a, b, info)), x, y);
  }
  // 점을 빈 곳에 놓음 — 그 자리 목록(14쪽), router에서 끌면 갈 조건 먼저(16쪽)
  async function openCreate(ctx, from, gx, gy, x, y) {
    const A = nodeOf(from);
    if (!A) return;
    const info = await contextOf(ctx, from);
    const router = kindOf(A) === "router";
    ST.pending = { type: "create", from, gx, gy, x, y, key: null, mode: "before" };
    ST.ghost = { from, x: gx, y: gy, label: router ? "새 node" : "여기에 놓음" }; rerender(ctx);   // 14 · 16쪽 놓은 자리
    const head = router ? condBox(from, null) : ioBox(`${esc(Ls(A.writes) || "—")} <span class="muted">(${esc(from)}가 내보냄)</span>`, "아직 없음 — 다음 연결 때 정해짐");
    open(`<h4>${router ? "갈 조건" : "새 node"} <span class="x" data-cv="close">✕</span></h4>${router ? "" : `<input type="search" id="cv-search" placeholder="무엇을 할 node인가요?">`}${head}` +
      `${router ? `<div class="cv-sec">새 node 종류</div>` : ""}` + kindList(kindChoices(from, null, info)), x, y);
  }
  // router 조건 이름: cases "true" = 예, "false" = 아니오, default는 cases가 "true" 하나뿐이면 아니오(16 · 33쪽)
  function keyLabel(router, key) {
    const r = nodeOf(router), cases = Object.keys((r && r.params && r.params.cases) || {});
    if (key === "true") return "예";
    if (key === "false") return "아니오";
    if (key === "default") return cases.length === 1 && cases[0] === "true" ? "아니오" : "그 밖(default)";
    return key;
  }
  function byLabel(by) {   // state.evidence_check.passed → evidence_check 결과 › 통과 여부
    const m = /^state\.([a-z0-9_]+)\.?(.*)$/.exec(by || "");
    if (!m) return esc(by);
    return `${esc(L(m[1]) === m[1] ? m[1] + " 결과" : L(m[1]))}${m[2] ? ` › ${m[2] === "passed" ? "통과 여부" : esc(m[2])}` : ""}`;
  }
  function condBox(router, target) {
    const r = nodeOf(router), by = (r.params && r.params.by) || "";
    const keys = routeKeys(router);
    const rows = keys.map(k => `<label class="cv-case"><input type="radio" name="cv-key" data-cv="key" value="${esc(k.key)}"> <b>${esc(keyLabel(router, k.key))}</b> → ${esc(k.target)} · <span class="muted">사용 중</span></label>`).join("");
    return ioBox(`${esc(Ls(r.reads) || router)} <span class="muted">(${esc((r.reads || []).join(" · ") || router)})</span>`, target ? `조건에 맞으면 → ${esc(target)}` : "조건에 맞으면 → 새 node") +
      `<div class="cv-sec">기준 값 · 정해짐</div><div id="cv-by">${byLabel(by)} <code>${esc(by.replace(/^state\./, ""))}</code></div>` +
      `<div class="cv-sec">조건</div>${rows}<div id="cv-mode-box"></div><div class="small muted">조건을 고르지 않으면 연결이 생기지 않음</div>`;
  }
  // 고른 조건이 이미 다른 node로 가면 '옮기기 / 앞에 끼우기'(16 · 33쪽 #7)
  function modeBox(router, key, target) {
    const old = (routeKeys(router).find(k => k.key === key) || {}).target;
    if (!old || old === target) return "";
    const lab = keyLabel(router, key);
    return `<div class="small" style="margin-top:6px">"${esc(lab)}"는 사용 중 — 옮기기 / 앞에 끼우기</div>` +
      `<label class="cv-case"><input type="radio" name="cv-mode" data-cv="mode" value="before" checked> ${esc(old)} 앞에 끼우기</label>` +
      `<label class="cv-case"><input type="radio" name="cv-mode" data-cv="mode" value="move"> 옮기기</label>`;
  }
  // router 점을 다른 node에 놓음 — 갈 조건 고르기(16쪽, #7): 고른 조건의 대상을 이 node로 옮김
  function openRouteTo(ctx, router, target, x, y) {
    ST.pending = { type: "route", from: router, to: target, key: null, mode: "before" };
    ST.mark = { tgt: [target] }; rerender(ctx);
    open(`<h4>갈 조건 <span class="x" data-cv="close">✕</span></h4>${condBox(router, target)}<div class="cv-row"><span class="cv-btn" data-cv="close">취소</span><span class="cv-btn p off" data-cv="route-ok" id="cv-route-ok">잇기</span></div>`, x, y);
  }
  // 지우기 전에 영향부터(17 · 18쪽, #11~14)
  async function openDelete(ctx, name, x, y) {
    const n = nodeOf(name);
    if (!n) return;
    if (kindOf(n) === "custom_code") {
      open(`<h4>지울 수 없음 <span class="muted">${esc(name)}</span> <span class="x" data-cv="close">✕</span></h4><div class="cv-err">함수 node '${esc(name)}'는 삭제할 수 없습니다(연결 위치만 바꿀 수 있습니다)</div><div class="small">코드 변경은 Pack 저장소에서</div><div class="cv-row"><span class="cv-btn" data-cv="close">취소</span></div>`, x, y);
      return;
    }
    const plain = edgesOf().filter(e => !e.conditional && e.kind !== "loop" && (e.source === name || e.target === name));
    const ops = [...plain.map(e => ({ op: "remove_edge", graph: gname(ctx), edge: [e.source, e.target] })), { op: "remove_node", graph: gname(ctx), node: name }];
    const routedFrom = edgesOf().filter(e => e.conditional && e.target === name).map(e => e.source);
    let after;
    try { after = await ctx.api("POST", `/api/projects/${ctx.p.id}/edit/delete-impact`, { node: name }); }
    catch (e) { after = null; }
    const edgeText = e => Array.isArray(e) ? e.join(" → ") : e && e.join ? `${e.join.join(" · ")} → ${e.to}` : e && e.from ? `${e.from} → ${e.to}` : JSON.stringify(e);
    let ok, errs, remove = plain.map(e => `${e.source} → ${e.target}`), readd = [], sendList = ops;
    if (after && after.after) {   // api-contract v1.1: 지우려면 응답의 ops를 그대로 보냄(join의 한 출처를 지우면 그 node만 뺀 연결을 다시 넣음, 17쪽)
      ok = after.after.ok; errs = (after.after.errors || []).map(x => x.message);
      if (after.remove_edges) remove = after.remove_edges.map(edgeText);
      readd = (after.add_edges || []).map(edgeText);
      if (Array.isArray(after.ops) && after.ops.length) sendList = after.ops;
    } else { try { ({ ok, errs } = await cutBy(ctx, ops)); } catch (e) { ok = false; errs = [e.message]; } }
    ST.pending = { type: "delete", name, ops: sendList, router: routedFrom[0] || null };
    // 그래프 표시는 창이 열린 동안 계속(close가 지움): 함께 지울 연결 빨간 점선 · 지울 node 흐림(17쪽), 막히면 node 빨강 · 갈래에 '갈 곳 없어짐'(18쪽)
    const pairs = after && after.remove_edges ? after.remove_edges.filter(Array.isArray) : plain.map(e => [e.source, e.target]);
    ST.mark = ok ? { edges: pairs, del: [name] } : { edges: [], red: [name], badges: routedFrom.length ? { [routedFrom[0]]: "갈 곳 없어짐" } : {} };
    rerender(ctx);
    // 이 편집에서 만든 node를 지우면 그 node를 만들고 이은 변경 줄이 함께 사라짐(17쪽 '8 · 9 · 10줄이 사라짐(만들고 지운 것)')
    const gone = ((ST.edit && ST.edit.lines) || []).filter(l => (l.ops || (l.op ? [l.op] : [])).some(o =>
      (o.op === "add_node" && o.node && o.node.id === name) || (o.op === "add_edge" && JSON.stringify(o.edge || "").includes(`"${name}"`)))).map(l => l.n);
    if (ok) {
      open(`<h4>${esc(name)} 지우기 <span class="x" data-cv="close">✕</span></h4><div class="cv-sec">함께 지움</div><div id="cv-del-edges">연결 ${remove.length}${remove.length ? " — " + esc(remove.join(" · ")) : ""}</div>` +
        (readd.length ? `<div class="cv-sec">다시 이음</div><div id="cv-del-readd">${esc(readd.join(" · "))}</div>` : "") +
        `<div class="cv-sec">지운 뒤</div><div class="cv-ok-t">검사 통과 · 끊기는 node 없음</div>` +
        (gone.length ? `<div class="cv-sec">변경 목록</div><div id="cv-del-lines">${gone.join(" · ")}줄이 사라짐(만들고 지운 것)</div>` : "") +
        `<div class="cv-row"><span class="cv-btn" data-cv="close">취소</span><span class="cv-btn d" data-cv="delete-ok" id="cv-delete-ok">지우기</span></div>`, x, y);
    } else {
      const why = routedFrom.length ? `${esc(routedFrom[0])}의 갈 조건이 이 node로 감 — 지우면 갈 곳이 없음` : "흐름이 끊깁니다";
      open(`<h4>지울 수 없음 <span class="muted">${esc(name)}</span> <span class="x" data-cv="close">✕</span></h4><div class="cv-sec">이유</div><div>${why}</div><div class="cv-sec">지우면</div>` +
        `<div class="cv-err" id="cv-del-block">${(errs || []).map(esc).join("<br>")}</div>${routedFrom.length ? `<div class="small">갈 조건을 다른 node로 옮기면 지울 수 있음</div>` : ""}` +
        `<div class="cv-row"><span class="cv-btn" data-cv="close">취소</span>${routedFrom.length ? `<span class="cv-btn p" data-cv="fix-route" id="cv-fix-route">갈 조건 고치기 ›</span>` : ""}<span class="cv-btn off">지우기</span></div>`, x, y);
    }
  }

  // 연결을 지울 때(34쪽 #14): 끊기는 node 수 · 강조 · 삭제 잠김. router 갈래 연결은 갈 조건에서 고침(#7)
  async function openEdgeDelete(ctx, a, b, x, y) {
    const e = edgesOf().find(z => z.source === a && z.target === b);
    if (!e) return;
    if (e.conditional) {
      ST.pending = { type: "delete", name: null, ops: [], router: a };
      open(`<h4>${esc(a)} → ${esc(b)} <span class="x" data-cv="close">✕</span></h4><div>router '${esc(a)}'의 나가는 연결은 params에만 적습니다 — 갈 조건에서 고칩니다</div>` +
        `<div class="cv-row"><span class="cv-btn" data-cv="close">취소</span><span class="cv-btn p" data-cv="fix-route" id="cv-fix-route">갈 조건 고치기 ›</span></div>`, x, y);
      return;
    }
    const ops = [{ op: "remove_edge", graph: gname(ctx), edge: [a, b] }];
    let res = null;
    try { res = await ctx.api("POST", `/api/projects/${ctx.p.id}/edit/delete-impact`, { edge: [a, b] }); } catch (err) { res = null; }
    let ok, errs, noEnd = [], unreach = [], sendList = ops;
    if (res && res.after) {
      ok = res.after.ok; errs = (res.after.errors || []).map(z => z.message);
      noEnd = res.after.no_end || []; unreach = res.after.unreachable || [];
      if (Array.isArray(res.ops) && res.ops.length) sendList = res.ops;
    } else {
      try { ({ ok, errs, noEnd, unreach } = await cutBy(ctx, ops)); } catch (err) { ok = false; errs = [err.message]; }
    }
    ST.pending = { type: "delete", name: null, ops: sendList, router: null };
    ST.mark = ok ? { edges: [[a, b]] } : { edges: [[a, b]], red: noEnd, fade: unreach.filter(n => !noEnd.includes(n)) };
    rerender(ctx);
    if (ok) {
      open(`<h4>연결 지우기 <span class="x" data-cv="close">✕</span></h4><div id="cv-del-edges">${esc(a)} → ${esc(b)}</div><div class="cv-sec">지운 뒤</div><div class="cv-ok-t">검사 통과 · 끊기는 node 없음</div>` +
        `<div class="cv-row"><span class="cv-btn" data-cv="close">취소</span><span class="cv-btn d" data-cv="delete-ok" id="cv-delete-ok">지우기</span></div>`, x, y);
    } else {
      open(`<h4>지울 수 없습니다 <span class="x" data-cv="close">✕</span></h4><div id="cv-del-cut">${esc(a)} → ${esc(b)}를 지우면 END로 못 가는 node ${noEnd.length} · START에서 닿지 않는 node ${unreach.length}</div>` +
        `<div class="cv-err" id="cv-del-block">${(errs || []).slice(0, 4).map(esc).join("<br>")}</div><div class="small">다른 연결로 먼저 이어 주세요</div>` +
        `<div class="cv-row"><span class="cv-btn" data-cv="close">취소</span><span class="cv-btn off">지우기</span></div>`, x, y);
    }
  }
  // 놓아서 이은 뒤 한 줄로 무엇이 더해졌나(15쪽 '입력에 더함', #10 합치기 규칙)
  async function noteAdded(ctx, from, to) {
    let io = null;
    try { io = keepLabels(await ctx.api("GET", `/api/projects/${ctx.p.id}/edit/nodes/${encodeURIComponent(to)}/io`)); } catch (e) { io = null; }
    const A = nodeOf(from), give = new Set((A && A.writes) || []);
    const rows = ((io && io.reads) || []).filter(r => give.has(r.key) || (r.from || []).includes(from));
    const parts = rows.map(r => {
      const others = (r.from || []).filter(x => x !== from);
      return `${esc(L(r.key))} ← <b>${esc(from)}</b>(새로)${others.length ? " · " + others.map(esc).join(" · ") + "(그대로)" : ""}` +
        (others.length ? `<div class="small muted">${others.length + 1}곳이 같은 ${esc(L(r.key))}을 내보내도 합치기 규칙이 있어 괜찮음</div>` : "");
    });
    ST.note = { key: layoutKey(ctx), html: `<b>입력에 더함</b> <span class="small muted">${esc(to)} — 기존 입력은 그대로</span><div>${parts.join("<br>") || `${esc(from)} → ${esc(to)} 연결`}</div>` };
    rerender(ctx);
  }

  /* ---------------------------------------------------------------- dialog actions */
  async function pickKind(ctx, kind) {
    const pd = ST.pending;
    if (!pd) return;
    const g = gname(ctx), base = (KINDS.find(k => k.kind === kind) || {}).base || "node";
    if (kind === "blank") {   // EV-073 CHG-AI-1 · 3: a blank node remembers where it hangs; its connections are made when it gets a kind
      const id = uniqueId("blank");
      if (pd.type === "insert") {
        const A = nodeOf(pd.a), B = nodeOf(pd.b);
        const op = { op: "add_blank", graph: g, node: { id, expect: { text: "" } }, from: [pd.a], to: [pd.b], split: !pd.cond.length, route: pd.cond.length ? { router: pd.a, key: pd.cond[0] } : null };
        return sendOps(ctx, [op], { [id]: freeSpot(Math.round((A.x + B.x) / 2), Math.round((A.y + B.y) / 2)) });
      }
      if (pd.type === "create") {
        const A = nodeOf(pd.from), router = kindOf(A) === "router";
        if (router && !pd.key) return showError("조건을 고르지 않으면 연결이 생기지 않습니다");
        const old = router ? (routeKeys(pd.from).find(r => r.key === pd.key) || {}).target : null;
        const to = router && pd.mode === "before" && old && old !== "END" ? old : null;
        const op = { op: "add_blank", graph: g, node: { id, expect: { text: "" } }, from: [pd.from], to: to ? [to] : [], split: false, route: router ? { router: pd.from, key: pd.key } : null };
        return sendOps(ctx, [op], { [id]: [Math.round(pd.gx - NODE_W / 2), Math.round(pd.gy - NODE_H / 2)] });
      }
      return;
    }
    if (pd.type === "insert") {
      const A = nodeOf(pd.a), B = nodeOf(pd.b);
      const shared = (A.writes || []).filter(k => (B.reads || []).includes(k));
      const id = uniqueId(kind === "llm@1" && shared[0] ? `${base}_${shared[0]}` : base);
      const node = makeNode(kind, pd.a, pd.b, id);
      let ops = [{ op: "add_node", graph: g, node, after: pd.a }];
      if (pd.cond.length) ops = ops.concat(...pd.cond.map(k => routeOps(ctx, pd.a, k, id)));   // router 연결: 그 조건의 대상을 새 node로
      else ops.push({ op: "remove_edge", graph: g, edge: [pd.a, pd.b] }, { op: "add_edge", graph: g, edge: [pd.a, id] });
      ops.push({ op: "add_edge", graph: g, edge: [id, pd.b] });
      const place = { [id]: freeSpot(Math.round((A.x + B.x) / 2), Math.round((A.y + B.y) / 2)) };
      return sendOps(ctx, ops, place);
    }
    if (pd.type === "create") {
      const A = nodeOf(pd.from), router = kindOf(A) === "router";
      if (router && !pd.key) return showError("조건을 고르지 않으면 연결이 생기지 않습니다");
      const old = router ? (routeKeys(pd.from).find(r => r.key === pd.key) || {}).target : null;
      const to = router && pd.mode === "before" && old && old !== "END" ? old : null;
      const id = uniqueId(base);
      const node = makeNode(kind, pd.from, to, id);
      const ops = [{ op: "add_node", graph: g, node, after: pd.from }];
      if (router) ops.push(...routeOps(ctx, pd.from, pd.key, id)); else ops.push({ op: "add_edge", graph: g, edge: [pd.from, id] });
      if (to) ops.push({ op: "add_edge", graph: g, edge: [id, to] });
      return sendOps(ctx, ops, { [id]: [Math.round(pd.gx - NODE_W / 2), Math.round(pd.gy - NODE_H / 2)] });
    }
  }
  if (typeof document !== "undefined") {
    document.addEventListener("click", ev => {
      const t = ev.target.closest("[data-cv]");
      if (!t || !dlg || !dlg.contains(t)) return;
      const ctx = ST.ctx, a = t.dataset.cv, pd = ST.pending;
      if (a === "close") return close();
      if (!ctx || !pd) return;
      if (a === "kind") return pickKind(ctx, t.dataset.kind);
      if (a === "key") {
        pd.key = t.value; pd.mode = "before";
        const ok = dlg.querySelector("#cv-route-ok"); if (ok) ok.classList.remove("off");
        const mb = dlg.querySelector("#cv-mode-box"); if (mb) mb.innerHTML = modeBox(pd.from, pd.key, pd.type === "route" ? pd.to : null);
        fit();
        return;
      }
      if (a === "mode") { pd.mode = t.value; return; }
      if (a === "route-ok" && pd.type === "route") {
        if (!pd.key) return showError("조건을 고르지 않으면 연결이 생기지 않습니다");
        const old = (routeKeys(pd.from).find(k => k.key === pd.key) || {}).target;
        const ops = routeOps(ctx, pd.from, pd.key, pd.to);
        if (pd.mode === "before" && old && old !== pd.to && old !== "END" && !edgesOf().some(e => e.source === pd.to && e.target === old))
          ops.push({ op: "add_edge", graph: gname(ctx), edge: [pd.to, old] });   // 그 대상 앞에 끼우기
        return sendOps(ctx, ops);
      }
      if (a === "delete-ok" && pd.type === "delete") { const gone = pd.name; return sendOps(ctx, pd.ops).then(ok => { if (ok && gone && ctx.ui.node === gone) { ctx.ui.node = null; rerender(ctx); } }); }   // the deleted node's popover closes
      if (a === "slot-request") {   // 32쪽 #3: 요청서(P11)에 한 장 — LLMOps 안 기록만, task10-pack에는 쓰지 않음
        const where = pd.type === "insert" ? `${pd.a} → ${pd.b} 사이` : `${pd.from} 다음`;
        const fields = { purpose: `새 검사 node(${t.dataset.kind})의 결과를 담을 state 자리`, change: `Pack에 검사 결과 state 자리(와 schema)를 더해 주세요 — ${where}에 새 검사 node를 넣으려 함`,
          io: "검사 결과 자리를 evidence_check · answer_check가 이미 씀 — 같이 쓰면 앞 결과를 덮어씀", eval: "", forbid: "기존 검사 결과 자리를 덮어쓰지 않음", fixture: "", trace: "설명서 32쪽 #3 · 캔버스 편집에서 남김" };
        ctx.api("POST", `/api/projects/${ctx.p.id}/briefs`, { title: "Pack 자리 요청 — 새 검사 결과 자리", fields })
          .then(() => { const el = dlg.querySelector("#cv-slot-ask"); if (el) el.innerHTML = `<span class="cv-ok-t" id="cv-slot-saved">요청서에 남겼습니다 — 위 '요청서'에서 봅니다(Pack 저장소에는 쓰지 않음)</span>`; const b = dlg.querySelector("#cv-slot-request"); if (b) b.classList.add("off"); return reloadState(ctx); })
          .catch(e => showError(`요청서에 남기지 못함 — ${e.message}`));
        return;
      }
      if (a === "repick" && pd.type === "repick") { const n = pd.node; close(); if (typeof Popover !== "undefined") Popover.show(n); ctx.ui.node = n; ctx.ui.popTab = "edit"; ctx.render(); return; }   // 32쪽 #4 다시 고르기: 그 node 팝오버 고치기
      if (a === "fix-route" && pd.type === "delete" && pd.router) { const n = pd.router; close(); if (typeof Popover !== "undefined") Popover.show(n); ctx.ui.node = n; ctx.ui.popTab = "edit"; ctx.render(); }   // route_evidence 팝오버로(18쪽)
    });
    document.addEventListener("input", ev => {
      if (!dlg || ev.target.id !== "cv-search") return;
      const q = ev.target.value.trim().toLowerCase();
      for (const row of dlg.querySelectorAll("[data-kind-row]")) row.hidden = !!q && !row.dataset.search.toLowerCase().includes(q);
    });
    document.addEventListener("keydown", ev => {
      if (ev.key === "Escape" && dlg && !dlg.hidden) { close(); return; }
      if (ev.key !== "Delete" && ev.key !== "Backspace") return;
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      if (/INPUT|TEXTAREA|SELECT/.test(tag) || (dlg && !dlg.hidden)) return;
      const ctx = ST.ctx;
      if (editing(ctx) && ST.selEdge) {   // 고른 연결 지우기(34쪽 #14)
        const [a, b] = ST.selEdge.split(">"), el = document.querySelector(`[data-edge-hit="${CSS.escape(ST.selEdge)}"]`), rr = el ? el.getBoundingClientRect() : { right: 200, top: 200 };
        ev.preventDefault(); ST.lastPoint = { x: rr.right + 12, y: rr.top };
        return openEdgeDelete(ctx, a, b, rr.right + 12, rr.top);
      }
      if (!editing(ctx) || !ctx.ui.node || !nodeOf(ctx.ui.node)) return;
      ev.preventDefault();
      const el = document.querySelector(`.graph-node[data-node="${CSS.escape(ctx.ui.node)}"]`), r = el ? el.getBoundingClientRect() : { right: 200, top: 200 };
      ST.lastPoint = { x: r.right + 12, y: r.top };
      openDelete(ctx, ctx.ui.node, r.right + 12, r.top);
    });
  }

  /* ---------------------------------------------------------------- drag (점 끌기) */
  function svgPoint(svg, ev) {
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const m = svg.getScreenCTM(); return m ? pt.matrixTransform(m.inverse()) : { x: 0, y: 0 };
  }
  function nodeAt(ev) {   // 선 고르기 칸(투명 굵은 선)이 node 위를 지나도 그 아래 node를 찾음
    for (const el of document.elementsFromPoint(ev.clientX, ev.clientY)) {
      const g = el.closest && el.closest(".graph-node");
      if (g) return g.dataset.node;
      if (el.id === "cv-dialog" || (el.closest && el.closest("#cv-dialog"))) return null;
    }
    return null;
  }
  function onPointer(ev, ctx) {
    const dot = ev.target && ev.target.closest && ev.target.closest("[data-cv-dot]");
    if (!dot || !editing(ctx)) return false;
    ev.preventDefault(); ev.stopPropagation();
    const svg = dot.ownerSVGElement, from = dot.dataset.cvDot, p0 = svgPoint(svg, ev);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    for (const [k, v] of Object.entries({ x1: dot.getAttribute("cx"), y1: dot.getAttribute("cy"), x2: p0.x, y2: p0.y, stroke: "#7a5af8", "stroke-width": 2, "stroke-dasharray": "5 4", "pointer-events": "none", id: "cv-drag-line" })) line.setAttribute(k, v);
    svg.appendChild(line);
    ST.drag = { from, svg, line, ctx, hover: null };
    ST.note = null; ST.selEdge = null;
    // 놓을 수 있는 node만 초록 테두리(15쪽) — 나머지는 끌어 올리면 흐림(#8)
    for (const g of svg.querySelectorAll(".graph-node")) if (g.dataset.node !== from && !edgeCheck(from, g.dataset.node)) g.classList.add("cv-ok");
    const move = e => {
      const d = ST.drag; if (!d) return;
      const p = svgPoint(d.svg, e); d.line.setAttribute("x2", p.x); d.line.setAttribute("y2", p.y);
      const over = nodeAt(e);
      if (over !== d.hover) {
        for (const g of d.svg.querySelectorAll(".graph-node.cv-no")) g.classList.remove("cv-no");
        d.hover = over;
        const bad = over && over !== d.from && edgeCheck(d.from, over);
        if (bad) { const el = d.svg.querySelector(`.graph-node[data-node="${CSS.escape(over)}"]`); if (el) el.classList.add("cv-no"); }
        d.line.setAttribute("stroke", bad ? "#d92d20" : "#7a5af8");   // 되돌아가는 연결 등은 끄는 중 선 빨강(33쪽 #6)
      }
    };
    const up = e => {
      document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up);
      const d = ST.drag; ST.drag = null; if (!d) return;
      d.line.remove();
      for (const g of d.svg.querySelectorAll(".graph-node.cv-ok,.graph-node.cv-no")) g.classList.remove("cv-ok", "cv-no");
      ST.lastPoint = { x: e.clientX + 12, y: e.clientY };
      const target = nodeAt(e), p = svgPoint(d.svg, e);
      if (target === d.from) {   // 33쪽 #8: 자기 자신 — 끌어 놓았으면 이유(점을 눌렀다 떼기만 했으면 조용히)
        if (Math.hypot(p.x - Number(d.line.getAttribute("x1")), p.y - Number(d.line.getAttribute("y1"))) >= 20) alertBox(edgeCheck(d.from, d.from), checkTitle(edgeCheck(d.from, d.from)));
        return;
      }
      if (target) {
        const why = edgeCheck(d.from, target);
        if (why) return alertBox(why, checkTitle(why));   // 놓아도 안 생김 · 이유(#6 · #8)
        if (kindOf(nodeOf(d.from)) === "router") return openRouteTo(d.ctx, d.from, target, e.clientX + 12, e.clientY);
        const from = d.from;   // 놓을 때 서버 검사(15쪽), 이으면 '입력에 더함' 한 줄
        // 이미 join으로 받는 node에 잇기(15쪽 find_safety → evidence_check): join에 출처를 더함(리드 몫 7 — 서버 그래프 node의 join 칸)
        const tn = nodeOf(target), J = tn && Array.isArray(tn.join) ? tn.join : null;
        const ops = J && !J.includes(from)
          ? [{ op: "remove_edge", graph: gname(d.ctx), edge: { join: J, to: target } }, { op: "add_edge", graph: gname(d.ctx), edge: { join: [...J, from], to: target } }]
          : [{ op: "add_edge", graph: gname(d.ctx), edge: [from, target] }];
        return sendOps(d.ctx, ops).then(ok => ok && noteAdded(d.ctx, from, target));
      }
      if (Math.hypot(p.x - Number(d.line.getAttribute("x1")), p.y - Number(d.line.getAttribute("y1"))) < 20) return;
      openCreate(d.ctx, d.from, p.x, p.y, e.clientX + 12, e.clientY);
    };
    document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
    return true;
  }

  /* ---------------------------------------------------------------- ⌗ 노드 정리(19 · 20쪽) */
  // 왼쪽 → 오른쪽 층, 길면 아래로 접어 반대로, 갈래 · 나란한 node는 위아래로, 층 안 순서는 앞 node 평균 자리로(선 겹침 줄임)
  function tidyPositions(g) {
    const names = g.nodes.map(n => n.name), idx = Object.fromEntries(names.map((n, i) => [n, i]));
    const out = {}, inc = {};
    for (const n of names) { out[n] = []; inc[n] = []; }
    const state = {}, back = new Set();
    const visit = v => { state[v] = 1; for (const e of g.edges) if (e.source === v && out[e.target]) { if (state[e.target] === 1 || e.kind === "loop") back.add(`${v}>${e.target}`); else if (!state[e.target]) visit(e.target); } state[v] = 2; };
    for (const n of names) if (!state[n] && !g.edges.some(e => e.target === n && e.source !== n)) visit(n);
    for (const n of names) if (!state[n]) visit(n);
    for (const e of g.edges) if (out[e.source] && out[e.target] && !back.has(`${e.source}>${e.target}`)) { out[e.source].push(e.target); inc[e.target].push(e.source); }
    const layer = {}, order = [];
    const indeg = Object.fromEntries(names.map(n => [n, inc[n].length])), queue = names.filter(n => !indeg[n]);
    while (queue.length) { const v = queue.shift(); order.push(v); for (const w of out[v]) if (--indeg[w] === 0) queue.push(w); }
    for (const n of names) if (!order.includes(n)) order.push(n);
    for (const v of order) layer[v] = Math.max(0, ...inc[v].map(u => (layer[u] ?? 0) + 1));
    const layers = [];
    for (const v of order) (layers[layer[v]] = layers[layer[v]] || []).push(v);
    const rank = {};
    layers.forEach((L, li) => {
      if (li) L.sort((a, b) => avg(a) - avg(b) || idx[a] - idx[b]);
      L.forEach((v, i) => { rank[v] = i; });
    });
    function avg(v) { const ps = inc[v].filter(u => rank[u] !== undefined); return ps.length ? ps.reduce((s, u) => s + rank[u], 0) / ps.length : rank[v] ?? 0; }
    // 5열이 1400 폭 작업대 칸에 들어가게(설명서 20쪽처럼 START 다음 5칸에서 접음). 칸 사이 46px는 꺾인 선 · 갈래 이름(좁으면 낱말마다 줄 바꿈) 자리(E-S-09 L1 · L3, app.js packRoutes)
    const COLS = 5, DX = 136, DY = 64, GAP = 40, pos = {};
    // E-S-09 L2(설명서 11 · 20쪽): END로 끝나는 node(record_no_issue · report)는 따로 한 줄 — 첫 줄은 위, 다음 줄부터는 아래 — 이라
    // 오른쪽 바로 옆에 END가 들어감. 그 밖의 node는 지금처럼 층 안 가운데
    const leaf = new Set(names.filter(n => !out[n].length && inc[n].length));
    let top = 20;
    for (let b = 0; b * COLS < layers.length; b++) {
      const band = layers.slice(b * COLS, b * COLS + COLS), mid = band.map(L => L.filter(v => !leaf.has(v))), tall = Math.max(0, ...mid.map(L => L.length));   // a band of END nodes only: their row is the band
      const leaves = band.some(L => L.some(v => leaf.has(v))), above = leaves && b === 0;
      const t0 = top + (above ? DY : 0);
      band.forEach((L, c) => {
        const col = b % 2 ? COLS - 1 - c : c;   // 접어 반대로
        const M = mid[c], y0 = t0 + ((tall - M.length) * DY) / 2;
        M.forEach((v, i) => { pos[v] = [30 + col * DX, Math.round(y0 + i * DY)]; });
        L.filter(v => leaf.has(v)).forEach((v, i) => { pos[v] = [30 + col * DX, above ? top - i * DY : t0 + (tall + i) * DY]; });
      });
      top = t0 + Math.max(1, tall + (leaves && !above ? 1 : 0)) * DY + GAP;
    }
    return pos;
  }
  async function tidy(ctx) {
    if (!isPack(ctx) || !ST.graph) return;
    const key = layoutKey(ctx), before = Object.fromEntries(ST.graph.nodes.map(n => [n.name, [n.x, n.y]]));
    const fresh = typeof tidied === "function" ? tidied({ ...ST.graph, tidy: null }) : null;   // D-459: the tidy layout again, saved with its mark
    const pos = fresh ? { __v2: [2, 0], ...Object.fromEntries(fresh.nodes.map(n => [n.name, [n.x, n.y]])) } : tidyPositions(ST.graph);
    try { await ctx.api("PUT", layoutUrl(ctx), { positions: pos }); } catch (e) { return alertBox(`배치를 저장하지 못했습니다 — ${e.message}`); }
    ST.undo[key] = before; ST.layouts[key] = pos; ST.toast = { key };
    rerender(ctx);
  }
  async function untidy(ctx) {
    const key = layoutKey(ctx), before = ST.undo[key];
    if (!before) return;
    try { await ctx.api("PUT", layoutUrl(ctx), { positions: before }); } catch (e) { return alertBox(`배치를 되돌리지 못했습니다 — ${e.message}`); }
    ST.layouts[key] = before; delete ST.undo[key]; ST.toast = null;
    rerender(ctx);
  }

  function onClick(a, t, ctx) {
    if (!isPack(ctx)) return false;
    if (a === "cv-tidy") { tidy(ctx); return true; }
    if (a === "cv-untidy") { untidy(ctx); return true; }
    if (a === "node" && ST.selEdge) { ST.selEdge = null; return false; }   // node를 고르면 연결 고르기는 풀림(클릭은 app.js가 처리)
    if (a === "cv-edge" && editing(ctx)) {   // 연결 고르기 — 고른 채 Delete(34쪽 #14)
      const key = `${t.dataset.a}>${t.dataset.b}`;
      ST.selEdge = ST.selEdge === key ? null : key;
      rerender(ctx);
      return true;
    }
    if (a === "cv-plus" && editing(ctx)) {
      const r = t.getBoundingClientRect();
      ST.lastPoint = { x: r.right + 12, y: r.top };
      openInsert(ctx, t.dataset.a, t.dataset.b, r.right + 12, r.top);
      return true;
    }
    return false;
  }
  // 이 편집에서 만든 node의 이름을 바꾼 뒤(팝오버 '이름 (id)', 14 · 32쪽): 놓은 자리를 새 이름으로 옮겨 저장
  function renamed(ctx, a, b) {
    const key = layoutKey(ctx), L = ST.layouts[key];
    if (!L || !L[a]) return;
    L[b] = L[a]; delete L[a];
    ctx.api("PUT", layoutUrl(ctx), { positions: L }).catch(() => {});
  }
  // 바깥(팝오버 보기 탭의 "지우기" 등)에서 부를 수 있는 지우기 미리보기
  function requestDelete(ctx, name) { const el = document.querySelector(`.graph-node[data-node="${CSS.escape(name)}"]`), r = el ? el.getBoundingClientRect() : { right: 200, top: 200 }; return openDelete(ctx, name, r.right + 12, r.top); }

  // EV-073 CHG-AI-3: a blank node gets a kind from its popover — the same new node as the kind list makes there, with its role text,
  // and the connections it remembered (the blank line goes: fills)
  function fillBlank(ctx, b, kind) {
    const g = gname(ctx), from = b.from[0], to = b.to[0] || null, id = b.id;
    if (!template(kind)) return showError("Pack에 이 종류의 본보기가 없음");
    const node = makeNode(kind, from, to, id);
    if (b.expect && b.expect.text) node.expect = { text: b.expect.text };
    const ops = [{ op: "add_node", graph: g, node, ...(from ? { after: from } : {}) }];
    if (b.route) ops.push(...routeOps(ctx, b.route.router, b.route.key, id));
    else for (const f of b.from) { if (b.split) for (const t of b.to) ops.push({ op: "remove_edge", graph: g, edge: [f, t] }); ops.push({ op: "add_edge", graph: g, edge: [f, id] }); }
    for (const t of b.to) ops.push({ op: "add_edge", graph: g, edge: [id, t] });
    return sendOps(ctx, ops, null, { fills: id });
  }
  return { layout, decorate, editHint, onPointer, onClick, requestDelete, renamed, fillBlank, KINDS, _tidyPositions: tidyPositions, _state: ST };
})();
