/* EV-025 운영 Run 화면 (manual p.30, CHG-122 · 124): the operational Runs of the 운영 version, read from the deploy side's
   trace through the server (/api/projects/{pid}/releases/{rid}/ops-runs). Only on this screen — reached with '운영 Run 보기' on
   the 운영 version's band, URL #/p/{pid}/r/{rid}/run/run_… (or run/ops when there is none yet). Picking an evaluation Run
   in the table goes to its usual Run page (P07, unchanged); the node popover stays as it is everywhere else (PRES-124 · 125).
   Nothing is stored: the screen keeps only what it read (UI.ops). */
const OpsRun = (() => {
  const isOps = id => id === "ops" || /^run_[0-9A-HJKMNP-TV-Z]{26}$/.test(id || "");
  const state = ui => ui.ops || (ui.ops = { key: null, list: null, more: [], next: null, loading: false, detail: {}, failed: {}, diag: {} });
  const POLL_MS = 3000;   // EV-030 CHG-152: while the automatic diagnosis runs, the list is read again so the band and cells move on by themselves
  const autoOf = st => (st.list && st.list.auto) || null;
  const autoOn = st => { const a = autoOf(st); return !!a && a.state !== "off" && a.state !== "elsewhere"; };   // EV-034 D-168: elsewhere = off

  function load(ctx, rel) {
    const { p, ui, api, render } = ctx, st = state(ui);
    if (st.key !== rel.id + "|" + rel.pack.revision) {
      clearTimeout(st.poll);
      Object.assign(st, { key: rel.id + "|" + rel.pack.revision, list: null, more: [], next: null, loading: true, detail: {}, failed: {}, diag: {}, poll: null, procOpen: false });
      api("GET", `/api/projects/${p.id}/releases/${rel.id}/ops-runs`).then(d => { if (st.key.startsWith(rel.id + "|")) { st.list = d; st.next = d.next; st.loading = false; render(); } })
        .catch(e => { st.list = { connected: false, reason_text: e.message, runs: [] }; st.loading = false; render(); });
    }
    const id = ui.opsRun;
    if (id && id !== "ops" && !st.detail[id] && !st.failed[id] && !st.pending) {
      st.pending = id;
      api("GET", `/api/projects/${p.id}/releases/${rel.id}/ops-runs/${id}`).then(d => { st.detail[id] = d; st.pending = null; render(); })
        .catch(e => { st.failed[id] = e.message; st.pending = null; render(); });
    }
    // EV-030 CHG-152: the chosen Run's diagnosis (right '진단 결과' box), and the list read again every few seconds while it runs
    if (id && id !== "ops" && autoOn(st) && !st.diag[id] && st.diagPending !== id) {
      const key = st.key;
      st.diagPending = id;
      api("GET", `/api/projects/${p.id}/releases/${rel.id}/ops-runs/${id}/diagnosis`).then(d => { if (st.key === key) { st.diag[id] = d; st.diagPending = null; render(); } })
        .catch(() => { st.diagPending = null; });
    }
    if (autoOf(st) && autoOf(st).state === "running" && !st.poll) {
      const key = st.key;
      st.poll = setTimeout(() => api("GET", `/api/projects/${p.id}/releases/${rel.id}/ops-runs`).then(d => {
        if (st.key !== key) return;
        st.list = d; if (!st.more.length) st.next = d.next; st.diag = {}; st.poll = null; render();
      }).catch(() => { st.poll = null; }), POLL_MS);
    }
    // run/ops with operational Runs: open the newest one (the table's first row, p.30 ①)
    if (id === "ops" && st.list && st.list.runs && st.list.runs.length) ctx.go(`#/p/${p.id}/r/${rel.id}/run/${st.list.runs[0].run_id}`);
  }
  function more(ctx, rel) {
    const { p, ui, api, render } = ctx, st = state(ui);
    if (!st.next) return;
    api("GET", `/api/projects/${p.id}/releases/${rel.id}/ops-runs?before=${encodeURIComponent(st.next)}`).then(d => { st.more.push(...d.runs); st.next = d.next; render(); }).catch(() => {});
  }
  const ms = v => v === null || v === undefined ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`;
  const local = iso => { if (!iso) return ""; const d = new Date(iso), z = n => String(n).padStart(2, "0"); return `${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`; };
  const short = id => id.length > 15 ? id.slice(0, 15) + "…" : id;

  /* ---- EV-025 OPS-3 (manual p.30 ③, CHG-125): '평가 case로 올리기' above the table — the chosen operational Run only (an
     evaluation Run opens P07 and has no button here); off with its reason on run/ops or when the Run is already put up. */
  const pastOf = rel => !!(rel && rel.version && rel.version.state === "이전");   // D-652 6-a: an earlier version's screen is for viewing only
  function promoteButton(ctx, runs, rel) {
    if (pastOf(rel)) return `<span class="small muted" id="ops-past">이전 버전 — 보기만</span>`;
    const { ui } = ctx, cur = runs.find(r => r.run_id === ui.opsRun);
    const why = ui.opsRun === "ops" || !cur ? "고른 운영 Run이 없습니다" : cur.promoted ? "이미 올린 Run입니다" : !cur.input ? "원문 없음 — 올릴 입력이 없습니다" : "";
    return `<span class="btn${why ? " off" : ""}" data-act="ops-promote" id="ops-promote"${why ? ` title="${why}"` : ""}>평가 case로 올리기</span>`;   // EV-075: why it is grey on mouse-over
  }
  /* the confirm window: the Run, its input (copied as the deploy side stored it), what was observed, no expected value (Q-2 A) */
  function promoteWindow(ctx, rel) {
    const { ui, esc } = ctx, st = state(ui), w = ui.opsPromote, d = st.detail[ui.opsRun];
    if (!w || !d) return "";
    return `<div class="scrim" id="ops-promote-window"><div class="modal" style="max-width:min(560px, calc(100vw - 32px))"><div class="mh"><h3>평가 case로 올리기</h3></div><div class="mb small">` +
      `<div data-op-k="run">${hv(`${runNo(st, d.run_id)} · ${rel.name}`, `운영 Run ${d.run_id} · ${rel.name} · 배포 ${d.deployment_id || ""}`)}</div>` +
      `<div data-op-k="input">${hv(`입력: ${d.input_summary || d.input || "—"} · 원문 그대로 복사`, `입력 ${d.input || ""} · 배포단이 가린 원문 그대로 복사(${(d.input_ref || "").slice(0, 15)}…)`)}</div>` +   // F-03 · F-06
      `<div data-op-k="observed">결과: ${esc(d.result || "")}</div>` +
      `<div data-op-k="expected" title="기대값 없음 — 올린 case는 기대값 없이 평가 suite에 더해집니다">기대값 없이 올라감</div>` +
      `<div class="field" style="margin-top:8px"><label>올린 사람 별명</label><input id="ops-promote-by" value="${esc(w.by || "")}"></div>` +
      `<div class="field"><label>이유</label><input id="ops-promote-reason" value="${esc(w.reason || "")}"></div>` +
      (w.err ? `<div class="rg err small" id="ops-promote-error">${esc(w.err)}</div>` : "") +
      `<div class="small muted">${hv("지울 수 없음 · 추가만", "올린 case는 지울 수 없고(추가만), 실행 입력의 평가 suite 등록에서 기준 suite에 더해 새 suite로 등록합니다")}</div>` +
      `<div class="row" style="margin-top:10px"><span class="sp"></span><span class="btn" data-act="ops-promote-cancel" id="ops-promote-cancel">취소</span>` +
      `<span class="btn p${w.busy ? " off" : ""}" data-act="ops-promote-save" id="ops-promote-save">${w.busy ? "올리는 중…" : "올리기"}</span></div></div></div></div>`;
  }
  function promote(ctx, rel, by, reason) {
    const { p, ui, api, render, refresh } = ctx, st = state(ui), w = ui.opsPromote;
    w.by = by; w.reason = reason; w.busy = true; w.err = null; render();
    api("POST", `/api/projects/${p.id}/releases/${rel.id}/ops-runs/${ui.opsRun}/promote`, { by, reason })
      .then(() => { ui.opsPromote = null; st.key = null; ui.opsNotice = "올렸습니다 — 실행 입력의 평가 suite 등록에서 새 suite로 등록할 수 있습니다"; refresh(); })
      .catch(e => { w.busy = false; w.err = e.message; render(); });
  }

  /* the evaluation Runs of this version in the same table (p.30: 'R-2 · LLMOps 평가 · task10_holdout 12 · 5 경로 변경 · 1.0s') */
  function evalRows(ctx, rel, auto) {
    const { p, esc } = ctx, gate = rel.version && rel.version.judge;
    return p.runs.filter(r => r.rel === rel.id && r.status !== "실행 중").slice().reverse().map(r => {
      const pair = gate && gate.pairs && gate.pairs[r.uid], s = r.summary || {};
      const ok = r.cases.filter(c => c.state === "완료").length;
      const res = pair && pair.path_changed !== undefined ? `바뀐 길 ${pair.path_changed}` : `${ok}/${r.cases.length} 성공`;
      const lat = s.latency && s.latency.v !== undefined ? `${s.latency.v.toFixed(1)}s` : "—";
      return `<tr data-ops-row="${esc(r.id)}" data-where="evaluation" data-act="open-run" data-run="${esc(r.id)}" style="cursor:pointer"><td>${esc(r.id)}</td><td title="LLMOps 평가">${r.auto ? `<span class="hv" data-run-auto title="${esc(`LLMOps 평가 · ${noWheel(r.auto.text)}`)}">평가</span>` : "평가"}</td><td>${(t => t === r.input.name && !SET_WORD(t) ? esc(`${t} ${r.cases.length}`) : hv(`${(SET_WORD(r.input.name) || t.split(" · ").pop())} ${r.cases.length}건`, `${r.input.name} ${r.cases.length}`))(suiteWords(rel, r.input.name))}</td><td>${esc(res)}</td><td>${lat}</td>${auto ? `<td class="muted" data-auto-cell title="평가 Run">—</td>` : ""}</tr>`;
    }).join("");
  }
  // EV-075 (packrt 136 · v2 19쪽): an evaluation suite named …_work / …_confirm (a wheel's work and confirm suites) reads '작업 시험' · '확인 시험'
  const SET_WORD = n => /^[a-z0-9]+_\w+_work$/.test(n) ? "작업 시험" : /^[a-z0-9]+_\w+_confirm$/.test(n) ? "확인 시험" : "";
  // EV-075 RUNLIST (v2 19쪽): the cell's result in plain words — the diagnosis's own words on mouse-over
  const cellWords = t => t === "비율 다름" ? "이상: 판단 비율" : /^통과 · judge/.test(t) ? "정상" : String(t).replace(/^사람 체크 · /, "사람: ");
  // EV-075 F-06: the alarm the Run handled (the Pack label file's input_summary, made by the server) — the event and the input
  // fingerprint on mouse-over; without a summary the cell keeps the input as before ('—' when there is nothing)
  const alarmCell = r => { const tip = [r.input ? `이벤트 ${r.input}` : "", r.input_ref ? `입력 지문 ${String(r.input_ref).slice(0, 19)}…` : "", r.input_missing || ""].filter(Boolean).join(" · ");
    return r.input_summary ? `<td class="hv" title="${esc0(tip)}" data-alarm>${esc0(r.input_summary)}</td>` : `<td${tip ? ` title="${esc0(tip)}"` : ""}>${r.input ? esc0(r.input) : `<span class="muted">—</span>`}</td>`; };
  const noWheel = t => String(t ?? "").replace(/한 번에 한 바퀴/g, "한 번에 개선 하나").replace(/바퀴/g, "개선");   // D-492 덧 18 ①: a stored line on screen
  const esc0 = x => String(x ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // an operational Run by its place in this list ('운영 #n'), its id on mouse-over
  const runNo = (st, id) => { const L = st.list ? [...st.list.runs, ...st.more] : [], i = L.findIndex(r => r.run_id === id); return i >= 0 ? `운영 #${i + 1}` : short(id); };
  /* EV-030 CHG-152 (supplement A1 · A2): the 진단(자동) cell — the change contract's cell value table, failures in pink */
  function autoCell(esc, dg) {
    const c = dg || { text: "대기", signal: null }, cls = c.signal === "fail" || c.signal === "human" && (c.text.startsWith("사람 ✗") || c.text.startsWith("사람 체크")) ? "auto-fail" : c.signal === "error" ? "auto-err" : c.signal ? "" : "muted";
    return `<td data-auto-cell data-signal="${esc(c.signal || "")}">${c.text ? `<span class="${cls}" title="${esc(c.text + (c.from ? " · " + c.from : ""))}">${esc(cellWords(c.text))}</span>` : ""}</td>`;
  }
  /* the band above the table: running k / n → done, stopped when the logDB cannot be read; off = one grey line only */
  function band(ctx) {
    const { ui, esc } = ctx, a = autoOf(state(ui));
    if (!a || a.state === "none") return "";
    if (a.state === "off" || a.state === "elsewhere") return `<div class="small muted" id="auto-off" data-state="${esc(a.state)}">${esc(a.text)}</div>`;
    const m = /^(✦ 자동 진단 (?:중|끝|멈춤))(.*)$/.exec(a.text || "") || [null, "", a.text || ""];
    const open = a.wheel ? `<span class="sp"></span><span class="btn p" data-act="go" data-to="#/p/${ctx.p.id}/auto" id="auto-open">자동 개선에서 보기 ›</span>` : "";   // AC-4
    const all = `${m[1]}${m[2]}`, d = /(\d+)건[^·]*·[^·]*·?.*?사람 체크 (\d+)건 → (?:개선|바퀴|진행 중인 바퀴) (A-\d+)( 기록에 더함)?/.exec(all), k = /(\d+)건/.exec(all);   // EV-075: the result (D-457 B: the server's '바퀴' line too)
    const bad = Number(a.fails) || 0;   // ORCH (리드 223): '이상' = the failure signals the server counted, not the Runs it diagnosed
    const words = d ? `${bad ? `이상 ${bad}건 · ` : ""}사람 체크 ${d[2]}건 → 개선 ${d[3]} ${d[4] ? "기록에 더함" : "시작"}` : /실패 신호 없음/.test(all) && !/사람 체크/.test(all) ? `점검 ${k ? k[1] : ""}건 · 이상 없음` : all.replace(/^✦ 자동 진단 끝/, "").trim() || all;
    return `<div class="auto-band row" id="auto-band" data-state="${esc(a.state)}"><span>${hv(words, noWheel(all))}</span>${open}</div>`;
  }
  function table(ctx, rel) {
    const { p, ui, esc } = ctx, st = state(ui), L = st.list, auto = autoOn(st);
    const runs = L ? [...L.runs, ...st.more] : [];
    const rowOf = (r, proc) => `<tr data-ops-row="${esc(r.run_id)}" data-where="deployment"${proc ? ` data-proc="1"` : ""} data-act="go" data-to="#/p/${p.id}/r/${rel.id}/run/${esc(r.run_id)}" class="${r.run_id === ui.opsRun ? "on" : ""}" style="cursor:pointer">` +
      `<td class="hv" title="${esc(r.run_id)}">${esc(runNo(st, r.run_id))}${r.promoted ? ` <span class="badge run" data-promoted="1">올림</span>` : ""}</td><td title="배포단(운영)">운영</td>${alarmCell(r)}` +
      `<td>${esc(r.result)}</td><td>${ms(r.ms)}</td>${auto ? autoCell(esc, r.diagnosis) : ""}</tr>`;
    // EV-077 (D-454): the review targets by default; the fixed-procedure records fold into one line (names only) — a person's
    // check or label on one keeps it among the targets (D-412), and the Run open now stays visible
    const shown = r => r.target !== false || r.kept || (r.diagnosis && r.diagnosis.signal === "human") || r.run_id === ui.opsRun;
    const procs = runs.filter(r => !shown(r)), cols = auto ? 6 : 5;
    const rows = runs.filter(shown).map(r => rowOf(r)).join("") + (procs.length ? `<tr id="ops-proc-fold" data-act="ops-proc" style="cursor:pointer"><td colspan="${cols}" class="muted">` +
      `${st.procOpen ? "▾" : "▸"} 절차 기록 ${procs.length}건</td></tr>` + (st.procOpen ? procs.map(r => rowOf(r, true)).join("") : "") : "");
    const note = !L ? `<div class="small muted" id="ops-loading">운영 기록을 읽는 중…</div>`
      : !L.connected ? `<div class="rg err small" id="ops-unreadable">운영 기록을 읽을 수 없습니다 — ${esc(L.reason_text || L.reason || "")} · 평가 Run은 그대로 보입니다</div>`
      : !L.runs.length ? `<div class="small muted" id="ops-none">이 버전의 운영 기록 없음${L.deployment_ids && L.deployment_ids.length ? ` · 배포 ${L.deployment_ids.map(esc).join(" · ")}` : " · 이 버전의 배포 기록 줄이 없습니다"}</div>` : "";
    const moreBtn = st.next ? `<span class="link small" data-act="ops-more" id="ops-more">운영 Run 더 보기</span>` : "";
    // the table's columns can take more later (supplement A1 진단(자동)) — the header row is one list
    const head = ["실행", "구분", runs.some(r => r.input_summary) ? "알람" : "입력", "결과", "시간", ...(auto ? ["확인"] : [])].map(h => `<th>${h}</th>`).join("");   // EV-075 RUNLIST · F-06
    return band(ctx) + `<div class="vt" id="ops-history"><div class="row"><b title="평가 Run과 운영 Run(배포단 logDB)을 Version 기준으로">${esc(rel.name)} 실행 기록</b><span class="sp"></span><span id="ops-actions">${promoteButton(ctx, runs, rel)}</span></div>` +
      (ui.opsNotice ? `<div class="note small" id="ops-promote-done">${esc(ui.opsNotice)}</div>` : "") +
      `<div style="overflow-x:auto"><table class="small" id="ops-table" style="white-space:nowrap"><tr>${head}</tr>${rows}${evalRows(ctx, rel, auto)}</table></div>${note}${moreBtn}</div>`;
  }
  /* graph colours of the chosen operational Run (the same marks an evaluation Run's path uses) */
  /* EV-030 CHG-153: the node the judge suspects (pink) — only a judge that counts as a signal (test file or real), never the fake one */
  /* EV-065 (CHG-264): a checked Run's pink node and judge table come from its check row's judge (check_judge) — the judge looks there only */
  const judgeOf = dg => (dg && dg.check_judge) || dg;
  const suspectOf = (st, id) => { const dg = st.diag[id], j = judgeOf(dg); return dg && dg.state === "done" && ["harness", "real"].includes(j.judge_kind) ? j.suspect_node || null : null; };
  function highlight(ctx) {
    const st = state(ctx.ui), d = st.detail[ctx.ui.opsRun];
    if (!d) return {};
    const look = { done: "done", fail: "fail", live: "live", skipped: "idle" }, s = {}, counts = {};
    for (const v of d.visits) { s[v.node] = look[v.status] || "idle"; counts[v.node] = (counts[v.node] || 0) + 1; }
    return { state: s, counts, passed: new Set(d.passed), suspect: suspectOf(st, ctx.ui.opsRun) };
  }
  function runNote(ctx, rel) {
    const { ui, esc } = ctx, st = state(ui), d = st.detail[ui.opsRun];
    if (ui.opsRun === "ops") return "";
    if (st.failed[ui.opsRun]) return `<div class="rg err small" id="ops-run-error">${esc(st.failed[ui.opsRun])}</div>`;
    if (!d) return `<div class="small muted" id="ops-run-loading">운영 Run을 읽는 중…</div>`;
    return `<div class="small" id="ops-flow-head"><b title="${esc(d.run_id)} · 운영 · ${esc(rel.name)}">${esc(runNo(st, d.run_id))}의 흐름</b> <span class="muted">· 노드를 누르면 입력 · 출력</span></div>`;
  }
  /* the right Run tab on this screen: the chosen operational Run (p.30 '선택 Run'); the chosen node's part comes with OPS-2 */
  function insp(ctx, rel) {
    const { ui, esc } = ctx, st = state(ui), d = st.detail[ui.opsRun];
    if (!d) return `<div class="small muted" id="ops-insp-empty">${ui.opsRun === "ops" ? "운영 Run이 없습니다" : "운영 Run을 읽는 중…"}</div>`;
    return `<h3>선택 Run</h3><div class="small" id="ops-run-box"><div data-ops-k="id">${hv(runNo(st, d.run_id), `${d.run_id} · 운영`)}</div>` +
      `<div data-ops-k="version">${hv(rel.name, `Version ${rel.name} · revision ${(d.pack_revision || "").slice(7, 15)} · 배포 ${d.deployment_id || ""}`)}</div>` +
      `<div data-ops-k="time">${esc(local(d.started_at))} · ${ms(d.ms)}</div>` +
      `<div data-ops-k="result">결과 ${esc(d.result)}${d.review ? ` · 검토 ${esc(d.review.action || "")}${d.review.actor_kind === "auto" ? "(자동) · 사람 검토 아님" : d.review.actor_kind ? `(${esc(d.review.actor_kind)})` : ""}` : ""}${d.input ? ` · 입력 ${esc(d.input)}` : ""}</div></div>` + autoBox(ctx) + nodeInsp(ctx, rel);
  }
  /* EV-030 CHG-152 (supplement A1 ② · A2 right): while the batch runs, '자동 진단' (it runs by itself, reads the logDB off the
     operating path, progress, the three signals); once the chosen Run is diagnosed, '진단 결과' (judge · rule · ratio · people · rubric) */
  const KIND_WORD = { harness: "judge 시험 장치", real: "judge 실제", fake: "judge(가짜) — 신호로 쓰지 않음", skipped: "judge 꺼짐 — 실제 judge는 Gate B 승인 뒤" };
  function judgeLine(a, dg) {
    const kind = dg.judge_kind, nodes = ((dg.judge || {}).nodes || []).filter(n => n.ok !== null && n.ok !== undefined);
    if (kind === "skipped") return "judge 꺼짐 — 실제 judge는 Gate B 승인 뒤";
    if (kind === "fake") return `judge(가짜) node ${nodes.length}개 — 표시만, 신호로 쓰지 않음`;
    if (a && a.state === "done" && a.judge_kind === kind) return `judge ${a.judge_fails} / ${a.n} 실패${a.suspect && a.judge_fails ? ` · ${a.suspect}` : ""}`;
    const bad = nodes.filter(n => n.ok === false).map(n => n.node);
    return `judge ${bad.length} / ${nodes.length} node 실패${bad.length ? ` · ${bad.join(" · ")}` : ""}`;
  }
  // EV-075 (수정본 F-03): the rule's result in words — 한 건 and the ratio (a small sample = 판단 보류)
  function ruleWords(first, r) {
    const ratio = !r || !r.compared ? (/표본/.test((r && r.why) || "") ? "표본 적음(판단 보류)" : "비교 없음") : r.differ ? "판단 비율이 평가 때와 다름" : "비율 같음";
    return `규칙 ${first ? `✗ ${first}` : "통과"} · ${ratio}`;
  }
  function ratioLine(r) {
    if (!r || !r.compared) return `비율: ${(r && r.why) || "비교할 평가 Run 없음"}`;
    const it = r.items.find(x => x.differ) || r.items.find(x => /escalation/.test(x.what)) || r.items[0];
    return `비율 ${r.differ ? "다름" : "이상 없음"}` + (it ? ` (운영 ${it.what} ${it.ops} ${it.differ ? "≠" : "="} 평가 ${r.eval_run || ""} ${it.eval})` : "");
  }
  /* EV-065 CHG-263a: the '사람 체크' paragraph — the person's checks of this Run (kind · source · time · its diagnosis · the
     improvement that took it); without any, the line stays as it was */
  /* D-633 (app 001-zzz114): the exception recommendation the person saw in the check and what they did with it — one line */
  function offerWords(o) {
    if (!o) return "";
    const what = `예외 추천 ${o.relation_id || "(관계 id 없음)"}`, sec = o.picked_section ? ` ${o.picked_section}` : "";
    const did = o.adopted === true ? "정비자가 고름" : o.adopted === false ? (o.how === "switched" ? `안 고름 — 점검 바꾸기로${sec}` :
      o.how === "followed" ? `안 고름 — 추천 점검대로 감${sec ? `(${sec.trim()})` : ""}` : "안 고름") : "보임 · 고름 여부 기록 없음";
    return [what, did, o.run_id ? `Run ${o.run_id}` : "", o.record_key ? `기록 ${o.record_key}` : ""].filter(Boolean).join(" · ");
  }
  function checksLine(dg, esc) {
    const cs = dg.checks || [], ow = offerWords(dg.offer), offer = ow ? `<div data-auto-offer>${esc(ow)}</div>` : "";
    if (!cs.length) return `<div data-auto-k="people" title="사람 피드백: 현장 결과 자리 없음">사람 체크: 없음${offer}</div>`;
    const hm = s => { const d = new Date(s * 1000); return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
    return `<div data-auto-k="people"><b>사람 체크</b>` + cs.map(c => `<div data-auto-check>${esc(c.kind)}${c.node ? ` · node ${esc(c.node)}` : ""} · ${esc(c.source)} · ${hm(c.at)}` +
      ` · ${c.judge ? `진단 ${esc(c.judge)}` : "진단 못 함 — 다음에 다시"}${c.wheel ? ` · 개선 ${esc(c.wheel)}` : ""}</div>`).join("") + offer + `</div>`;
  }
  function autoBox(ctx) {
    const { ui, esc } = ctx, st = state(ui), a = autoOf(st), dg = st.diag[ui.opsRun];
    if (!a || a.state === "off" || a.state === "elsewhere" || ui.opsRun === "ops") return "";
    if (a.state === "running") return `<h3 style="margin-top:10px">자동 진단</h3><div class="small" id="auto-box"><div>새 운영 Run이 들어오면 저절로 돕니다</div>` +
      `<div>운영 경로 밖에서 logDB를 읽음</div><div data-auto-k="progress">진행 ${a.k} / ${a.n}</div>` +
      `<div style="margin-top:6px"><b>세 신호</b></div><div>· 사람 피드백(현장 결과)</div><div>· 규칙(한 건 · 비율)</div><div>· LLM judge(표본)</div></div>`;
    if (dg && dg.procedure) return `<h3 style="margin-top:10px">진단 결과</h3><div class="small muted" id="auto-result" data-procedure="1">절차 기록</div>`;   // EV-077
    if (!dg || dg.state === "none" || dg.state === "waiting" || dg.state === "running") return "";
    if (dg.state === "error") return `<h3 style="margin-top:10px">진단 결과</h3><div class="small" id="auto-result"><div class="auto-err" data-auto-k="error">${esc(dg.cell.text)}</div></div>`;
    const rule = dg.rule || {}, first = rule.first === "run" ? "Run" : rule.first;
    return `<h3 style="margin-top:10px">진단 결과</h3><div class="small" id="auto-result">` +
      `<div data-auto-k="cell">${(t => cellWords(t) === t ? esc(t) : hv(cellWords(t), t))(dg.cell.text || "진단할 node 없음")}</div>` +   // EV-075 (packrt 136): the list cell's words here too
      (dg.signal === "none" || dg.judge_kind === "skipped" ? "" : `<div data-auto-k="judge">${esc(judgeLine(a, dg))}</div>`) +
      `<div data-auto-k="rule">${hv(ruleWords(first, dg.ratio), `규칙: 한 건 ${first ? `✗ ${first}` : "통과"} · ${ratioLine(dg.ratio)} · rubric ${dg.rubric || ""} · ${KIND_WORD[dg.judge_kind] || "judge 없음"}`)}</div>` +
      checksLine(dg, esc) + `</div>`;
  }
  /* ---- EV-025 OPS-2 (manual p.30 ②, CHG-123 · 124): the chosen node of the chosen operational Run — the box beside the graph,
     the tabs under it (입력 · 출력 / 피드백 · 검토 / Metadata, more can come: supplement A3 · A4) and 선택 node in the Run tab.
     Values are the node's raw input and output from the trace's payload files (reads keys in, writes keys out). */
  function loadNode(ctx, rel) {
    const { p, ui, api, render } = ctx, st = state(ui), id = ui.opsRun, node = ui.node;
    if (!id || id === "ops" || !node) return null;
    const key = `${id}|${node}|${ui.iter || 1}`;
    st.nodes = st.nodes || {};
    if (!st.nodes[key]) {
      st.nodes[key] = { loading: true };
      api("GET", `/api/projects/${p.id}/releases/${rel.id}/nodes/${encodeURIComponent(node)}?ops_run=${id}&iter=${ui.iter || 1}`)
        .then(d => { st.nodes[key] = { d }; render(); }).catch(e => { st.nodes[key] = { err: e.message }; render(); });
    }
    return st.nodes[key];
  }
  const short_ = r => r ? `${r.slice(0, 15)}…` : "—";
  const hm = iso => { if (!iso) return ""; const d = new Date(iso), z = (n, k = 2) => String(n).padStart(k, "0"); return `${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}.${z(d.getMilliseconds(), 3)}`; };
  const failedRule = ov => (ov.checks || []).some(c => c && c.passed === false);
  const statusWord = ov => ov.status === "fail" ? "오류" : ov.status === "skipped" ? "건너뜀" : ov.status === "live" ? "실행 중" : failedRule(ov) ? "실패" : "완료";
  /* where the next step went: 'route_evidence → escalation_draft(부족)' — a router's chosen target with its branch name */
  function nextLine(ctx, rel, ov) {
    const { ui, esc } = ctx, st = state(ui), d = st.detail[ui.opsRun], bl = rel.branchLabels || {};
    return (ov.next || []).map(n => {
      const v = d && d.visits.find(x => x.node === n), r = v && v.route;
      if (!r || !r.target) return esc(n);
      const name = (bl[n] || {})[String(r.value)] || String(r.value);
      return `${esc(n)} → <b>${esc(r.target)}</b>(${esc(name)})`;
    }).join(" · ") || "—";
  }
  function nodeBox(ctx, rel) {
    const { ui, esc } = ctx, n = loadNode(ctx, rel), sus = suspectOf(state(ui), ui.opsRun);
    if (!n) return "";
    if (n.loading) return `<div class="vt small muted" id="ops-node-box">선택 node · ${esc(ui.node)} — 읽는 중…</div>`;
    if (n.err) return `<div class="rg err small" id="ops-node-box">${esc(n.err)}</div>`;
    const ov = n.d.ops_value || {};
    if (!ov.visited) return `<div class="vt small" id="ops-node-box"><b>선택 node · ${esc(ui.node)}</b><div class="muted">이 Run에서 지나지 않은 node입니다</div></div>`;
    const rules = (ov.checks || []).map(c => `${esc(c.rule)} ${c.passed ? "✓" : "✗"}`).join(" · ");
    const word = statusWord(ov);
    return `<div class="vt small" id="ops-node-box"><b>선택 node · ${esc(ui.node)}</b>` +
      `<div data-ops-n="kind">${(k => hv(({ retrieve: "리트리버", llm: "모델로 쓰기", validate: "검사", router: "갈래", render: "결과 쓰기", human_review: "사람 검토", custom_code: "Pack 함수", predict: "모델 예측" })[String(k).split("@")[0]] || k, k))((ov.meta || {}).kind || n.d.kind || "")} · <b style="color:${word === "완료" ? "inherit" : "#dc2626"}">${word}</b> · ${ms(ov.ms)}</div>` +   // EV-075 (v2 20쪽)
      `<div data-ops-n="next">다음: ${nextLine(ctx, rel, ov)}</div>` + (rules ? `<div data-ops-n="rules">규칙 ${rules}</div>` : "") +
      (!sus ? "" : ui.node === sus ? `<div data-ops-n="diag">진단 <span class="auto-fail">judge 실패</span> 이 node가 의심 node</div><div class="muted" data-ops-n="legend">분홍 테두리 = judge가 의심한 node</div>`
        : failedRule(ov) ? `<div data-ops-n="diag">진단 규칙대로 실패 ✓ — 앞 node <b>${esc(sus)}</b>를 의심(분홍)</div><div class="muted" data-ops-n="legend">분홍 테두리 = judge가 의심한 node</div>` : "") +
      (ov.iters > 1 ? `<div class="row" style="gap:4px;margin-top:4px">${Array.from({ length: ov.iters }, (_, i) => `<span class="chip${ov.iter === i + 1 ? " on" : ""}" data-act="ops-iter" data-k="${i + 1}">${i + 1}회차</span>`).join("")}</div>` : "") + `</div>`;
  }
  /* one field tree (p.30 'Input ⌄ · ⌄ alarm_resolution · display_name: …'): objects and lists fold, values stay on one line */
  function tree(esc, v, depth = 0) {
    const leaf = x => x === null ? "null" : typeof x === "object" ? (Array.isArray(x) ? `[${x.length}]` : "{…}") : typeof x === "string" ? `"${x.length > 160 ? x.slice(0, 160) + "…" : x}"` : String(x);
    if (v === null || typeof v !== "object") return `<span>${esc(leaf(v))}</span>`;
    const entries = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v);
    return entries.map(([k, x]) => x !== null && typeof x === "object" && (Array.isArray(x) ? x.length : Object.keys(x).length)
      ? `<details${depth < 1 ? " open" : ""} data-ops-key="${esc(k)}"><summary>${esc(k)}${Array.isArray(x) ? ` (${x.length})` : ""}</summary><div style="padding-left:14px">${tree(esc, x, depth + 1)}</div></details>`
      : `<div data-ops-key="${esc(k)}" style="overflow-wrap:anywhere"><span class="muted">${esc(k)}:</span> ${esc(leaf(x))}</div>`).join("");   // PRES-130: a long value wraps inside its field
  }
  function side(esc, title, val, why, ref) {
    const body = val === null || val === undefined ? `<div class="rg err small" data-ops-missing>원문 없음(${esc(why || "")}) · ${esc(short_(ref))}</div>`
      : Object.keys(val).length ? tree(esc, val) : `<div class="small muted">값 없음</div>`;
    return `<div class="vt small" style="flex:1;min-width:0;overflow-x:auto" data-ops-side="${title}"><details open><summary><b>${title}</b></summary>${body}</details></div>`;
  }
  function tabs(ctx, rel) {
    const { ui, esc } = ctx, n = loadNode(ctx, rel);
    if (!n || !n.d || !(n.d.ops_value || {}).visited) return "";
    const ov = n.d.ops_value, tab = ui.opsTab || "io";
    const head = [["io", "입력 · 출력"], ["review", "사람 체크"], ["meta", "Metadata"]]   // a list: judge results (supplement A3 · A4) can join later
      .map(([k, l]) => `<span class="tab${tab === k ? " on" : ""}" data-act="ops-tab" data-k="${k}" id="ops-tab-${k}">${l}</span>`).join("");
    let body = "";
    if (tab === "io") body = (ov.predict ? `<div class="small" id="ops-predict" data-ops-predict style="margin-bottom:6px;overflow-wrap:anywhere"><b>예측</b> ${esc(ov.predict.text)} <span class="muted">(trace 기록)</span></div>` : "") +   // EV-041 CHG-204
      (ov.relations ? `<div class="small" id="ops-relations" style="margin-bottom:6px;overflow-wrap:anywhere"><b>관계</b> ${esc(ov.relations.text)}</div>` : "") +   // D-543 ⑥ ①
      `<div class="row" style="gap:10px;align-items:flex-start">${side(esc, "Input", ov.input, ov.input_missing, (ov.meta || {}).input_ref)}${side(esc, "Output", ov.output, ov.output_missing, (ov.meta || {}).output_ref)}</div>`;
    if (tab === "review") body = judgeBlock(ctx, n) + labelBlock(ctx, rel) + `<div class="vt small" id="ops-review">` +
      ((ov.reviews || []).length ? ov.reviews.map(r => `<div data-ops-review>${esc(r.event === "review_recorded" ? "검토 기록" : "검토 요청")} · ${esc(r.action || "—")}${r.actor_kind ? `(${esc(r.actor_kind === "auto" ? "자동" : r.actor_kind)})` : ""}${r.reason ? ` · ${esc(r.reason)}` : ""} · ${esc(hm(r.at))}</div>`).join("") : `<div class="muted">이 node의 검토 기록 없음</div>`) +
      ((ov.checks || []).length ? `<div style="margin-top:6px"><b>검사</b>${ov.checks.map(c => `<div data-ops-check>${esc(c.rule)} ${c.passed ? "✓" : "✗"} · ${esc(c.detail || "")}</div>`).join("")}</div>` : "") +
      ((ov.expect_checks || []).length ? `<div style="margin-top:6px"><b>기대 검사</b>${ov.expect_checks.map(c => `<div>${esc(c.check || c.rule || "")} ${c.passed ? "✓" : "✗"}</div>`).join("")}</div>` : "") + `</div>`;
    if (tab === "meta") { const m = ov.meta || {}; body = `<div class="vt small" id="ops-meta">` +
      [["종류", m.kind], ["provider · model", m.provider ? `${m.provider} · ${m.model || ""}` : "—"], ["tokens in · out", m.tokens_in !== null && m.tokens_in !== undefined ? `${m.tokens_in} · ${m.tokens_out}` : "—"],
       ["시각", `${hm(m.started_at)} → ${hm(m.ended_at)} · ${ms(m.ms)}`], ["Pack 함수 hash", m.custom_source_hash || "—"], ["input", m.input_ref || "—"], ["output", m.output_ref || "—"],
       ["기록에 없음", (ov.not_recorded || []).join(" · ") || "—"]].map(([k, v]) => `<div data-ops-meta="${esc(k)}"><span class="muted">${esc(k)}</span> ${esc(v)}</div>`).join("") + `</div>`; }
    return `<div id="ops-node-tabs" style="margin-top:8px"><div class="tabs row" style="gap:6px">${head}</div>${body}</div>`;
  }
  /* EV-030 CHG-154 (supplement A4 ①): the judge's result for the chosen node — rubric items, ✓/✗ and the (masked) reason */
  const JUDGE_WORD = { harness: "judge 시험 장치", real: "judge 실제", fake: "judge(가짜) — 신호 아님", skipped: "judge 꺼짐" };
  function judgeBlock(ctx, n) {
    const { ui, esc } = ctx, dg0 = state(ui).diag[ui.opsRun];
    if (!dg0 || dg0.state !== "done") return "";
    const dg = judgeOf(dg0), jn = ((dg.judge || {}).nodes || []).find(x => x.node === ui.node), kind = ((n.d.ops_value.meta || {}).kind || n.d.kind || "").split("@")[0];
    const head = `<b>① LLM judge</b>`;
    if (!jn) return `<div class="vt small" id="auto-judge">${head} <span class="muted" data-judge-none>judge 대상 아님(${esc(dg.judge_kind === "skipped" ? ((dg.judge || {}).why || "실제 judge는 Gate B 승인 뒤") : kind)})</span></div>`;
    if (jn.ok === null || jn.ok === undefined) return `<div class="vt small" id="auto-judge">${head} <span class="muted" data-judge-none>${esc(jn.why || "judge 대상 아님")}</span></div>`;
    return `<div class="vt small" id="auto-judge"><div>${head} <span class="${jn.ok ? "" : "auto-fail"}" data-judge-word>${jn.ok ? "통과" : "실패"}</span> <span class="muted">rubric ${esc(dg.rubric || "")} · ${esc(kind)} · ${esc(JUDGE_WORD[dg.judge_kind] || "")}</span></div>` +
      `<table class="small" style="margin-top:4px"><tr><th>rubric 항목</th><th>결과</th><th>이유</th></tr>${(jn.items || []).map(i => `<tr data-judge-item><td>${esc(i.name)}</td><td>${i.ok ? "✓" : "✗"}</td><td style="white-space:normal">${esc(i.why || "")}</td></tr>`).join("")}</table></div>`;
  }
  /* (A4 ②) a person's label: optional, the loop does not wait for it; once left it comes before the judge (D-92). Append only. */
  function labelBlock(ctx, rel) {
    const { ui, esc } = ctx, dg = state(ui).diag[ui.opsRun], f = ui.opsLabel || {};
    if (!dg || !dg.labels) return "";
    const lb = dg.label;
    const cur = lb ? `<div data-label-cur>${esc(dg.cell.text)} · 이유 ${esc(lb.reason)} · ${esc(lb.by_alias)} · ${esc(local(new Date(lb.created_at * 1000).toISOString()))}</div>`
      : `<div class="muted" data-label-cur>비어 있음 — 고리는 기다리지 않음 · 남기면 judge보다 우선</div>`;
    if (pastOf(rel)) return `<div class="vt small" id="auto-label" style="margin-top:6px"><div><b>② 사람 라벨</b> <span class="muted">· 이전 버전 — 보기만</span></div>${lb ? cur : `<div class="muted" data-label-cur>비어 있음</div>`}</div>`;
    return `<div class="vt small" id="auto-label" style="margin-top:6px"><div><b>② 사람 라벨</b> <span class="muted">· 선택 · 목록 ${esc(dg.label_list_version)}</span></div>${cur}` +
      `<div class="row" style="margin-top:6px"><select id="ops-label-pick">${dg.labels.map(l => `<option value="${esc(l)}"${f.label === l ? " selected" : ""}>${esc((dg.label_names || {})[l] || l)}</option>`).join("")}</select>` +
      `<input id="ops-label-reason" placeholder="이유" value="${esc(f.reason || "")}" style="flex:1;min-width:120px"><input id="ops-label-by" placeholder="남긴 사람 별명" value="${esc(f.by || "")}" style="width:110px">` +
      `<span class="btn p${f.busy ? " off" : ""}" data-act="ops-label-save" id="ops-label-save">${f.busy ? "남기는 중…" : "라벨 남기기"}</span></div>` +
      (f.err ? `<div class="auto-err" id="ops-label-error">${esc(f.err)}</div>` : "") + `</div>`;
  }
  function saveLabel(ctx, rel, label, reason, by) {
    const { p, ui, api, render } = ctx, st = state(ui), id = ui.opsRun;
    ui.opsLabel = { label, reason, by, busy: true };
    render();
    api("POST", `/api/projects/${p.id}/releases/${rel.id}/ops-runs/${id}/labels`, { label, reason, by, node: ui.node || null })
      .then(d => { st.diag[id] = d; ui.opsLabel = null; for (const r of [...(st.list ? st.list.runs : []), ...st.more]) if (r.run_id === id) r.diagnosis = d.cell; render(); })
      .catch(e => { ui.opsLabel = { label, reason, by, err: e.message }; render(); });
  }
  /* 선택 node in the Run tab (p.30 right): time, the two raw-value refs and where they come from */
  function nodeInsp(ctx, rel) {
    const { ui, esc } = ctx, st = state(ui), n = loadNode(ctx, rel), d = st.detail[ui.opsRun];
    if (!n || !n.d || !(n.d.ops_value || {}).visited) return "";
    const m = n.d.ops_value.meta || {};
    return `<h3 style="margin-top:10px">선택 node</h3><div class="small" id="ops-node-insp"><div>${esc(ui.node)} · ${esc(m.kind || "")}</div><div>${esc(hm(m.started_at))} → ${esc(hm(m.ended_at).slice(-4))}</div>` +
      `<div>input ${esc(short_(m.input_ref))}</div><div>output ${esc(short_(m.output_ref))}</div><div class="muted">trace: logDB node_event · 원문 = ${esc((d && d.payload_source) || "")}</div></div>`;
  }
  return { isOps, load, more, band, table, highlight, runNote, insp, nodeBox, tabs, nodeInsp, promoteWindow, promote, saveLabel, offerWords };
})();
