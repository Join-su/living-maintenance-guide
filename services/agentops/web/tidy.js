/* D-459 (user 10-29): '⌗ 노드 정리' is a layout whose lines read — one layout for every screen that draws a graph.
   D-467 (user 10-29, line shape C): the flow runs left to right — layers are columns (START first, an END right after each node that
   leads nowhere, on its row), a branch reads as one row; every line leaves its source on the right side (a diamond: the corner toward
   its branch — top, bottom or right) with a small dot, runs flat to the end of its column, then curves into the next column's node on
   its left side; a line over more than one column keeps a slot in every column it passes (flat through it), so it never runs through a
   box; the order inside a column comes from barycentre sweeps with neighbour swaps (fewest crossings kept); the branch name sits near
   the end where its line enters. Pure function (no DOM): the browser and the tests read the same numbers.
   Tidy.layout(graph, {nameWidth, isDecision}) → {nodes: [{...n, x, y}], start: [x, y], startTargets, ends: {node: [x, y]},
   routes: {"a>b" | "START>a" | "a>END": {d, pts, port, label, back}}, width, height, crossings} — node box 90 × 34, a decision 120 × 44
   around x − 15 · y − 5 (app.js nodeBox), START · END pills 52 × 24; d is the path to draw, pts the line read every few pixels. */
(function (root) {
  const NW = 90, NH = 34, DW = 120, DH = 44, PW = 52, PH = 24, GAPY = 22, TOP = 16, LEFT = 16, DUMMY_H = 6, GAPX = 48;

  // the box a slot takes in the layout's own terms: x · y = top-left of the outline
  const slotW = v => v.dummy ? 0 : v.pill ? PW : v.diamond ? DW : NW;
  const slotH = v => v.dummy ? DUMMY_H : v.pill ? PH : v.diamond ? DH : NH;
  function box(n) {   // n.x · n.y as app.js gives them (a diamond's x · y = outline left + 15 · top + 5)
    if (n.pill) return [n.x, n.y, n.x + PW, n.y + PH];
    return n.diamond ? [n.x - 15, n.y - 5, n.x + 105, n.y + 39] : [n.x, n.y, n.x + NW, n.y + NH];
  }

  function terms(g) {
    const names = new Set(g.nodes.map(n => n.name)), into = new Set(g.edges.filter(e => e.kind !== "loop").map(e => e.target)), from = new Set(g.edges.map(e => e.source));
    const start = (g.start && g.start.length ? g.start : g.nodes.map(n => n.name).filter(n => !into.has(n))).filter(n => names.has(n));
    const waiting = new Set(g.pending || []);
    return { start, end: g.nodes.map(n => n.name).filter(n => !from.has(n) && !waiting.has(n)) };
  }

  // a cubic piece read every few pixels (for names, the crossing count and the tests' own reading)
  const cubic = (p0, c1, c2, p3, n = 16) => Array.from({ length: n + 1 }, (_, i) => { const t = i / n, u = 1 - t;
    return [u * u * u * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * p3[0], u * u * u * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * p3[1]]; });
  const r1 = v => Math.round(v * 10) / 10;

  function layout(g, opt = {}) {
    const nameWidth = opt.nameWidth || (s => [...String(s)].reduce((w, c) => w + (c.charCodeAt(0) > 255 ? 10 : 5.6), 0));
    const decision = opt.isDecision || (n => /^router/.test(String(n.kind || "")));
    const t = terms(g);
    // ---------------------------------------------------------------- slots and lines (START · END included)
    const V = {}, order = [];
    const add = (id, n) => { V[id] = { id, ...n }; order.push(id); };
    add("__start", { pill: true, term: "START" });
    for (const n of g.nodes) add(n.name, { node: n, diamond: !!decision(n) });
    for (const n of t.end) add(`__end:${n}`, { pill: true, term: "END", of: n });
    const E = [];
    for (const s of t.start) E.push({ source: "__start", target: s, id: `START>${s}` });
    for (const e of g.edges) if (e.kind !== "loop" && V[e.source] && V[e.target]) E.push({ source: e.source, target: e.target, id: `${e.source}>${e.target}`, label: e.label });
    for (const n of t.end) E.push({ source: n, target: `__end:${n}`, id: `${n}>END` });
    // lines that go back (a cycle without the loop kind) are drawn under the graph, not laid out
    const outs = Object.fromEntries(order.map(id => [id, E.filter(x => x.source === id)])), state = {}, back = new Set();
    const visit = v => { state[v] = 1; for (const x of outs[v]) { if (state[x.target] === 1) back.add(x.id); else if (!state[x.target]) visit(x.target); } state[v] = 2; };
    visit("__start");
    for (const id of order) if (!state[id]) visit(id);
    const fwd = E.filter(x => !back.has(x.id));
    // longest-path columns; an END sits right after its node
    const ins = Object.fromEntries(order.map(id => [id, fwd.filter(x => x.target === id)])), layer = {};
    const indeg = Object.fromEntries(order.map(id => [id, ins[id].length])), queue = order.filter(id => !indeg[id]), topo = [];
    while (queue.length) { const v = queue.shift(); topo.push(v); for (const x of fwd.filter(x => x.source === v)) if (--indeg[x.target] === 0) queue.push(x.target); }
    for (const id of order) if (!topo.includes(id)) topo.push(id);
    for (const v of topo) layer[v] = Math.max(0, ...ins[v].map(x => (layer[x.source] ?? 0) + 1));
    for (const v of topo) if (V[v].term === "END") layer[v] = layer[V[v].of] + 1;
    const nL = Math.max(...order.map(v => layer[v])) + 1;
    // ---------------------------------------------------------------- a slot in every column a long line passes
    const L = Array.from({ length: nL }, () => []), chains = {};
    for (const id of order) L[layer[id]].push(id);
    for (const x of fwd) {
      const a = layer[x.source], b = layer[x.target], ch = [x.source];
      for (let k = a + 1; k < b; k++) { const d = `__d:${x.id}:${k}`; V[d] = { id: d, dummy: true }; layer[d] = k; L[k].push(d); ch.push(d); }
      ch.push(x.target); chains[x.id] = ch;
    }
    const up = {}, down = {};
    for (const id of Object.keys(V)) { up[id] = []; down[id] = []; }
    for (const ch of Object.values(chains)) for (let i = 1; i < ch.length; i++) { down[ch[i - 1]].push(ch[i]); up[ch[i]].push(ch[i - 1]); }
    // ---------------------------------------------------------------- order inside columns: barycentre sweeps · neighbour swaps
    const pos = {};
    const renumber = k => L[k].forEach((v, i) => { pos[v] = i; });
    L.forEach((l, k) => renumber(k));
    const crossBetween = k => {
      let c = 0; const es = [];
      for (const v of L[k]) for (const w of down[v]) es.push([pos[v], pos[w]]);
      for (let i = 0; i < es.length; i++) for (let j = i + 1; j < es.length; j++) if ((es[i][0] - es[j][0]) * (es[i][1] - es[j][1]) < 0) c++;
      return c;
    };
    const total = () => { let c = 0; for (let k = 0; k + 1 < nL; k++) c += crossBetween(k); return c; };
    const bary = (v, nb) => nb[v].length ? nb[v].reduce((s, w) => s + pos[w], 0) / nb[v].length : pos[v];
    let best = L.map(l => [...l]), bestC = total();
    for (let it = 0; it < 30; it++) {
      if (it % 2 === 0) for (let k = 1; k < nL; k++) { L[k].sort((a, b) => bary(a, up) - bary(b, up) || pos[a] - pos[b]); renumber(k); }
      else for (let k = nL - 2; k >= 0; k--) { L[k].sort((a, b) => bary(a, down) - bary(b, down) || pos[a] - pos[b]); renumber(k); }
      for (let k = 0; k < nL; k++) for (let i = 0; i + 1 < L[k].length; i++) {
        const near = () => (k ? crossBetween(k - 1) : 0) + (k + 1 < nL ? crossBetween(k) : 0), before = near();
        [L[k][i], L[k][i + 1]] = [L[k][i + 1], L[k][i]]; renumber(k);
        if (near() >= before) { [L[k][i], L[k][i + 1]] = [L[k][i + 1], L[k][i]]; renumber(k); }
      }
      const c = total();
      if (c < bestC) { bestC = c; best = L.map(l => [...l]); }
    }
    for (let k = 0; k < nL; k++) { L[k] = best[k]; renumber(k); }
    // ---------------------------------------------------------------- y inside a column: stacked, then pulled to the neighbours
    const Y = {};
    L.forEach(l => { let y = TOP; for (const v of l) { Y[v] = y; y += slotH(V[v]) + GAPY; } });
    const cy = v => Y[v] + slotH(V[v]) / 2;
    const gapY = (a, b) => V[a].dummy && V[b].dummy ? 8 : V[a].dummy || V[b].dummy ? 14 : GAPY;   // a passing line needs less room than a node
    for (let it = 0; it < 40; it++) {
      const ks = it % 2 ? [...Array(nL).keys()].reverse() : [...Array(nL).keys()];
      for (const k of ks) {
        const l = L[k], want = l.map(v => {
          if (V[v].term === "END") return cy(V[v].of) - slotH(V[v]) / 2;   // END on its node's row
          const nb = [...up[v], ...down[v]]; return nb.length ? nb.reduce((s, w) => s + cy(w), 0) / nb.length - slotH(V[v]) / 2 : Y[v];
        });
        const y = [...want];
        for (let i = 1; i < y.length; i++) y[i] = Math.max(y[i], y[i - 1] + slotH(V[l[i - 1]]) + gapY(l[i - 1], l[i]));
        for (let i = y.length - 2; i >= 0; i--) y[i] = Math.min(y[i], y[i + 1] - slotH(V[l[i]]) - gapY(l[i], l[i + 1]));
        for (let i = 0; i < y.length; i++) { y[i] = Math.max(y[i], TOP); if (i) y[i] = Math.max(y[i], y[i - 1] + slotH(V[l[i - 1]]) + gapY(l[i - 1], l[i])); }
        l.forEach((v, i) => { Y[v] = y[i]; });
      }
    }
    // an END on its node's row at the end (the sweeps can move the node after its END): its column is laid again with the ENDs fixed
    // there and the rest pushed up or down around them; kept as it was when two fixed ENDs would overlap
    for (let k = 0; k < nL; k++) {
      const l = L[k], fixed = l.map(v => V[v].term === "END");
      if (!fixed.some(Boolean)) continue;
      const y = l.map((v, i) => fixed[i] ? cy(V[v].of) - slotH(V[v]) / 2 : Y[v]);
      for (let i = 1; i < y.length; i++) if (!fixed[i]) y[i] = Math.max(y[i], y[i - 1] + slotH(V[l[i - 1]]) + gapY(l[i - 1], l[i]));
      for (let i = y.length - 2; i >= 0; i--) if (!fixed[i]) y[i] = Math.min(y[i], y[i + 1] - slotH(V[l[i]]) - gapY(l[i], l[i + 1]));
      if (y.every((v, i) => !i || v >= y[i - 1] + slotH(V[l[i - 1]]) + gapY(l[i - 1], l[i]) - 0.5)) l.forEach((v, i) => { Y[v] = y[i]; });
    }
    const minY = Math.min(...Object.keys(V).map(v => Y[v]));
    for (const v of Object.keys(V)) Y[v] = Math.round(Y[v] - minY + TOP);
    // ---------------------------------------------------------------- x: columns as wide as their widest box, gaps as wide as the names in them
    const labels = {};
    for (const x of fwd) if (x.label) {
      const opts = [[x.label], ...(x.label.includes(" · ") ? [x.label.split(" · ")] : [])];
      labels[x.id] = opts.map(ls => ({ ls, w: Math.max(...ls.map(nameWidth)), h: ls.length * 11 }));
    }
    const colW = L.map(l => Math.max(0, ...l.map(v => slotW(V[v]))));
    const gapW = L.map((l, k) => Math.max(GAPX, ...fwd.filter(x => x.label && layer[x.target] === k + 1).map(x => Math.min(180, labels[x.id][0].w + 30))));   // the one-line name fits (ORCH 231 ②)
    const colX = []; let x0 = LEFT;
    for (let k = 0; k < nL; k++) { colX[k] = x0; x0 += colW[k] + gapW[k]; }
    const X = Object.fromEntries(Object.keys(V).map(v => [v, colX[layer[v]]]));
    // ---------------------------------------------------------------- ports: out on the right side (a diamond: its corner) · in on the left side
    const node = v => ({ ...V[v], x: X[v] + (V[v].diamond ? 15 : 0), y: Y[v] + (V[v].diamond ? 5 : 0) });
    const right = v => X[v] + slotW(V[v]), colEnd = v => colX[layer[v]] + colW[layer[v]];
    const nextOf = id => chains[id][1], prevOf = id => chains[id][chains[id].length - 2];
    const portOut = {}, portIn = {};
    for (const v of Object.keys(V)) {
      if (V[v].dummy) continue;
      const out = fwd.filter(x => x.source === v).sort((p, q) => cy(nextOf(p.id)) - cy(nextOf(q.id)) || (p.id < q.id ? -1 : 1));
      if (V[v].diamond) {
        // a corner toward the branch: top · bottom when the column has nothing between the corner and the line's next row, else the right corner
        const clear = (y1, y2) => !L[layer[v]].some(w => w !== v && !V[w].dummy && Y[w] < Math.max(y1, y2) && Y[w] + slotH(V[w]) > Math.min(y1, y2));
        for (const p of out) {
          const ty = cy(nextOf(p.id)), c = cy(v);
          portOut[p.id] = ty < c - 8 && clear(Y[v], ty) ? { x: X[v] + DW / 2, y: Y[v], dir: "up" }
            : ty > c + 8 && clear(Y[v] + DH, ty) ? { x: X[v] + DW / 2, y: Y[v] + DH, dir: "down" } : { x: right(v), y: c, dir: "right" };
        }
      } else {
        const h = slotH(V[v]) - (V[v].pill ? 8 : 12);
        out.forEach((p, i) => { portOut[p.id] = { x: right(v), y: cy(v) + (out.length === 1 ? 0 : -h / 2 + (h * i) / (out.length - 1)), dir: "right" }; });
      }
      const inn = fwd.filter(x => x.target === v).sort((p, q) => cy(prevOf(p.id)) - cy(prevOf(q.id)) || (p.id < q.id ? -1 : 1));
      const hi = slotH(V[v]) - (V[v].pill ? 8 : 12);
      inn.forEach((p, i) => {   // a diamond takes its lines in at its left corner (they meet only there)
        portIn[p.id] = V[v].diamond ? { x: X[v], y: cy(v) } : { x: X[v], y: cy(v) + (inn.length === 1 ? 0 : -hi / 2 + (hi * i) / (inn.length - 1)) };
      });
    }
    // ---------------------------------------------------------------- the lines: flat to the column's end, a curve across the gap, flat through a passing column
    const routes = {};
    const hop = (a, b, vertical) => {   // a cubic from a to b, leaving flat (or straight up · down from a diamond's corner) and arriving flat
      const dx = b[0] - a[0];
      const c1 = vertical ? [a[0], b[1]] : [a[0] + dx / 2, a[1]], c2 = vertical ? [a[0] + dx / 2, b[1]] : [b[0] - dx / 2, b[1]];
      return { d: `C${r1(c1[0])},${r1(c1[1])} ${r1(c2[0])},${r1(c2[1])} ${r1(b[0])},${r1(b[1])}`, pts: cubic(a, c1, c2, b) };
    };
    for (const [id, ch] of Object.entries(chains)) {
      const o = portOut[id], p0 = [o.x, o.y], d = [`M${r1(p0[0])},${r1(p0[1])}`], pts = [p0];
      let cur = p0;
      const flat = x => { if (x > cur[0] + 0.5) { cur = [x, cur[1]]; d.push(`L${r1(cur[0])},${r1(cur[1])}`); pts.push(cur); } };
      let vertical = o.dir !== "right";
      if (!vertical) flat(colEnd(ch[0]));
      for (let i = 1; i < ch.length; i++) {
        const v = ch[i], to = V[v].dummy ? [X[v], cy(v)] : [portIn[id].x, portIn[id].y];
        const h = hop(cur, to, vertical); d.push(h.d); pts.push(...h.pts.slice(1)); cur = to; vertical = false;
        if (V[v].dummy) flat(colEnd(v) + 0);
      }
      routes[id] = { d: d.join(" "), pts: pts.map(([a, b]) => [r1(a), r1(b)]), port: p0.map(r1), label: null };
    }
    // lines that go back: down under the graph on their own lane, then up into the target from below
    const bottom = Math.max(...Object.keys(V).map(v => Y[v] + slotH(V[v]))) + 18;
    E.filter(x => back.has(x.id)).forEach((x, i) => {
      const a = x.source, b = x.target, ly = bottom + i * 10, s = [right(a), cy(a)], e = [X[b] + slotW(V[b]) / 2, Y[b] + slotH(V[b])];
      const d = `M${s[0]},${s[1]} C${s[0] + 24},${s[1]} ${s[0] + 24},${ly} ${s[0]},${ly} L${e[0]},${ly} C${e[0] - 12},${ly} ${e[0]},${ly} ${e[0]},${e[1]}`;
      routes[x.id] = { d, pts: [s, [s[0] + 18, ly], [e[0], ly], e], port: s, label: null, back: true };
    });
    // ---------------------------------------------------------------- branch names: near the end where the line enters
    const boxes = Object.keys(V).filter(v => !V[v].dummy).map(v => box(node(v)));
    const taken = [];
    const over = (r, b) => r[0] < b[2] && r[2] > b[0] && r[1] < b[3] && r[3] > b[1];
    const lineIn = (r, rt) => rt.pts.some(([x, y], i) => i && (() => { const [px, py] = rt.pts[i - 1], n = Math.max(2, Math.ceil(Math.hypot(x - px, y - py) / 1.5));
      for (let j = 0; j <= n; j++) { const qx = px + (x - px) * j / n, qy = py + (y - py) * j / n; if (qx > r[0] - 1 && qx < r[2] + 1 && qy > r[1] - 1 && qy < r[3] + 1) return true; } return false; })());
    // ORCH 231 ②: a name sits on its own line — no other line nearer to the name's middle than its own
    const gap = (rt, c) => Math.min(...rt.pts.map(([x, y]) => Math.hypot(x - c[0], y - c[1])));
    const ownNearest = (r, rt) => { const c = [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2], d = gap(rt, c); return !Object.values(routes).some(rr => rr !== rt && gap(rr, c) < d + 4); };
    // lines into one node: the top one's name goes above it first, the bottom one's below it first (outside the bundle)
    const side = id => { const t = fwd.find(x => x.id === id).target, ins = fwd.filter(x => x.target === t).map(x => [x.id, portIn[x.id] ? portIn[x.id].y : 0]).sort((a, b) => a[1] - b[1]);
      return ins.length < 2 ? 0 : ins[0][0] === id ? -1 : ins[ins.length - 1][0] === id ? 1 : 0; };
    for (const x of fwd.filter(x => x.label)) {
      const rt = routes[x.id], [px, py] = rt.pts[rt.pts.length - 1];
      let placed = null;
      const fits = rect => !boxes.some(b => over(rect, b)) && !taken.some(b => over(rect, b)) && !Object.values(routes).some(rr => rr !== rt && lineIn(rect, rr)) && !lineIn(rect, rt) && ownNearest(rect, rt);
      for (const o of [...labels[x.id]]) {   // the one-line name first, every way, before the folded one (names whole — P04 branch lines)
        const spots = [[0, 0], [0, -1], [0, 1], [-10, 0], [-10, -1], [0, 2], [0, -2], [-20, 1], ...[-30, -45, -60].flatMap(d => [[d, 0], [d, -1], [d, 1], [d, -2], [d, 2]])];
        const outer = side(x.id) > 0 ? [...spots.filter(([, l]) => l < 0), ...spots.filter(([, l]) => l >= 0)] : spots;   // the bottom line: below first
        for (const [dx, lift] of outer) {   // above the line · below it · further left · higher
          const bot = lift >= 0 ? py - 3 - lift * 12 : py + 4 + o.h - (lift + 1) * 12, top = bot - o.h, r = px - 6 + dx, l = r - o.w, rect = [l - 1, top, r + 1, bot + 2];
          if (fits(rect)) { placed = { x: r, y: top + 9, anchor: "end", lines: o.ls, ok: true, box: rect }; break; }
        }
        if (placed) break;
        // ORCH 231 ②: else along the line's last curve, back from its end — centred on the line, just above or below it, where the
        // lines into the same node have parted
        for (const [qx, qy] of rt.pts.slice(-17).reverse().slice(2)) {
          for (const sd of side(x.id) > 0 ? [1, -1] : [-1, 1]) {
            const under = rt.pts.filter(([x]) => x >= qx - o.w / 2 - 3 && x <= qx + o.w / 2 + 3).map(p => p[1]);   // the line's own rise under the name's width
            const top = sd < 0 ? Math.min(qy, ...under) - 4 - o.h : Math.max(qy, ...under) + 4, rect = [qx - o.w / 2 - 1, top, qx + o.w / 2 + 1, top + o.h + 2];
            if (rect[2] > px + 2 || rect[0] < px - 140 || rect[3] < py - 40 || rect[1] > py + 40) continue;
            if (fits(rect)) { placed = { x: qx, y: top + 9, anchor: "middle", lines: o.ls, ok: true, box: rect }; break; }
          }
          if (placed) break;
        }
        if (placed) break;
      }
      if (!placed) { const o = labels[x.id][labels[x.id].length - 1], r = px - 6, rect = [r - o.w - 1, py - 3 - o.h, r + 1, py - 1]; placed = { x: r, y: rect[1] + 9, anchor: "end", lines: o.ls, ok: false, box: rect }; }
      taken.push(placed.box); rt.label = placed;
    }
    // ---------------------------------------------------------------- out
    const nodes = g.nodes.map(n => { const p = node(n.name); return { ...n, x: Math.round(p.x), y: Math.round(p.y) }; });
    const all = [...boxes, ...taken, ...Object.values(routes).flatMap(r => r.pts.map(([a, b]) => [a, b, a, b]))];
    const width = Math.round(Math.max(...all.map(b => b[2])) + LEFT), height = Math.round(Math.max(...all.map(b => b[3])) + 16);
    const ends = Object.fromEntries(t.end.map(n => [n, [Math.round(X[`__end:${n}`]), Math.round(Y[`__end:${n}`])]]));
    return { nodes, start: [Math.round(X.__start), Math.round(Y.__start)], startTargets: t.start, ends, routes, width, height, crossings: count(routes), flow: "lr" };
  }

  // crossings: two lines' pieces crossing strictly inside both (lines that share a node are not counted — they meet at it)
  function count(routes) {
    const list = Object.entries(routes).map(([id, r]) => ({ ends: id.split(">"), pts: r.pts }));
    const orient = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    const cross = ([p, q], [r, s]) => { const d1 = orient(r, s, p), d2 = orient(r, s, q), d3 = orient(p, q, r), d4 = orient(p, q, s);
      return ((d1 > 0.01 && d2 < -0.01) || (d1 < -0.01 && d2 > 0.01)) && ((d3 > 0.01 && d4 < -0.01) || (d3 < -0.01 && d4 > 0.01)); };
    const segs = p => p.slice(1).map((q, i) => [p[i], q]);
    let n = 0;
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      if (list[i].ends.some(x => list[j].ends.includes(x))) continue;
      if (segs(list[i].pts).some(a => segs(list[j].pts).some(b => cross(a, b)))) n++;
    }
    return n;
  }

  root.Tidy = { layout, box, count };
})(typeof window !== "undefined" ? window : globalThis);
