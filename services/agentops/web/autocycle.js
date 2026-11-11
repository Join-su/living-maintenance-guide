/* EV-030 AC-4 (CHG-156 · 157, supplement A5 · 08/26): the '✦ 자동 개선' screen (#/p/{pid}/auto) — the Project's improvement wheels,
   newest first, each with its 8-cell bar (초록 끝 · 주황 지금 · 파랑 사람 차례 · 빨강 멈춤 · 회색 대기), the chosen wheel's progress log
   (one line each time it moves on) and, on the right, its facts. Wheels start by themselves after a diagnosis; nothing here is
   clicked to make one. The eight cell names come from the server (/api/projects/{pid}/auto); ⑤'s name is shown on this screen
   only (Q-10), so no screen file carries the word (v24 screen-wording test). */
const AutoCycle = (() => {
  const state = ui => ui.auto || (ui.auto = { pid: null, hash: null, data: null, sel: null });
  function baseWords(w, p) {   // D-624 4번: '운영' only while the base still runs, else the version running now ('기준 V18 운영' after V19 ran)
    const name = w.base.name || "?";
    const op = ((p && p.releases) || []).find(r => r.version && r.version.state === "운영");
    if (!op) return `기준 ${name}`;
    return op.id === w.base.release ? `기준 ${name} 운영` : `기준 ${name} · 지금 운영 ${op.name}`;
  }
  function load(ctx) {
    const { p, ui, api, render } = ctx, st = state(ui);
    if (st.pid === p.id && st.hash === location.hash) return;
    if (st.pid !== p.id) Object.assign(st, { panel: null, flow: null, settings: null, err: null });   // EV-069 (slice review 2 B2): another Project's windows never carry over
    Object.assign(st, { pid: p.id, hash: location.hash, data: st.pid === p.id ? st.data : null });   // read again on every visit
    api("GET", `/api/projects/${p.id}/auto`).then(d => { if (st.pid === p.id) { st.data = d; render(); } })
      .catch(e => { st.data = { state: "error", err: e.message, wheels: [] }; render(); });
  }
  const hm = s => { const d = new Date(s * 1000), z = n => String(n).padStart(2, "0"); return `${z(d.getHours())}:${z(d.getMinutes())}`; };
  const KIND = { performance: "성능 개선", efficiency: "효율 개선" };
  const pick = (st, d) => d.wheels.find(w => w.name === st.sel) || d.wheels[0];
  // D-470 덧 21 (보고 070 ②): a recommendation reads the verdict tab's words (EV-075 · v2 18쪽), the stored value on mouse-over
  const WORD = { adopt: "채택", hold: "보류", reject: "버림" };
  const rw = v => WORD[v] ? hv(WORD[v], v) : esc(v);
  // EV-075 (D-432 덧 6 · v2 13 · 14 쪽 · R1 · R2): the server's wheel words read short here — the whole text on mouse-over (hv)
  const SAY = [
    [/^③ dataset 실제 호출 상한 없음/, "③에서 멈춤: 비용 상한 없음"], [/^③ dataset 가짜 모드 · 공유 기록/, "③에서 멈춤: 가짜 모드 · 공유 기록"], [/^③ dataset — 작업 suite를 만듦$/, "시험 데이터 만듦"], [/^③ 자동 올리기 꺼짐 — /, "자동 올리기 꺼짐"],
    [/^끝 · 리허설 전 닫음/, "끝 · 시험용"], [/^끝 · 기준이 바뀜/, "끝 · 기준 버전 바뀜"],
    [/^사람 라벨 0건 — 기다리지 않고 진행$/, "사람 라벨 없음 · 계속"], [/^실제 호출 상한 없음 — 바퀴는 여기서 멈춥니다/, "비용 상한 없음 — 멈춤"],
    // EV-075 (packrt 136 · 13): an efficiency wheel's lines without suite names or node kinds
    [/^효율 개선 — (.+) · ③ dataset$/, m => `효율 개선 — ${m[1]}`], [/^작업 suite \S+\(지킬 = 전부\) · 확인 suite \S+$/, "작업 시험 · 확인 시험 정함"],
    [/^흔들림 기준선 — 운영 버전으로 작업 suite (\d+)번 실행/, m => `흔들림 기준선 — 운영 버전으로 ${m[1]}번 실행`],
    [/^④ 가설 — 구조 가설\(규칙\)$/, "④ 가설 — 구조 가설"], [/^구조 가설 (\d+)개\(규칙\) · 버림 (\d+)$/, m => `구조 가설 ${m[1]}개 · 버림 ${m[2]}`],
    [/^⑤ \S+ 줄 — 구조 가설의 변경 줄 · 고정 흐름 검사$/, "⑤ 변경 줄 — 고정 흐름 검사"],
    [/^반복 (\d+) · 묶음 처음 — \S+ 줄 (\d+)개\((.*?)\)/, m => `반복 ${m[1]} — 줄 ${m[2]}개(${m[3]})`],
    [/^\S+ 표 1등 — 줄 (\d+) · 같은 작업 suite로 (\d+)번 더/, m => `1등 줄 ${m[1]} — ${m[2]}번 더 실행`],
    [/^⑤ 1등 \S+ 줄 → 새 버전 (\S+) ✦ 자동 · 가설 (H-\d+) · .*$/, m => `⑤ 새 버전 ${m[1]} ✦ 자동 · 가설 ${m[2]}`],
    [/^⑥ 비교 — 확인 suite\(test\)를 운영 · 새 버전에서 한 번씩 · 기준 표로 판정 권고\(judge 없음\)$/, "⑥ 비교 — 확인 시험을 두 버전에서 · 기준 표로 권고"],
    [/^⑥ 판정 권고 (adopt|hold|reject) \(자동(?: · (?:기준 표|채점 에이전트))?\) — /, m => `⑥ 권고 ${WORD[m[1]]} — `],
    [/^⑦ 사람 차례 — .* 할 일: 배포 승인 ▶ 한 번$/, "⑦ 사람 차례 — 배포 승인 ▶ 한 번"],
    [/^끝 · 알림 — 구조 가설 없음\(바꿀 수 있는 llm · 표준 node 없음\)$/, "끝 · 바꿀 노드 없음"],
    // D-462 A: the structure agent's lines (screen words say 가설 — EV-030 Q-10)
    [/^반복 (\d+) · 구조 \S+ (\d+)개\(출처 에이전트\) · 시험 (\d+) · 대기 (\d+) · 버림 (\d+)$/, m => `구조 가설 ${m[2]}개(에이전트) · 시험 ${m[3]} · 버림 ${m[5]}`],
    [/^반복 (\d+) — 구조 \S+ 새로 받음$/, m => `반복 ${m[1]} — 구조 가설 새로`], [/^대기 — 시험 6개 넘음/, "대기 · 시험 안 함"],
    [/^끝 · 사람 승인 필요 — /, "끝 · 사람 승인 필요"], [/^끝 · 에이전트 한 번 시험 — /, "끝 · 에이전트 한 번 시험"], [/^구조 \S+ 에이전트 (호출 실패|비용 상한).* — 이번 반복은 규칙 틀 3개로$/, m => `에이전트 ${m[1]} — 규칙 틀로`],
    [/^끝 · 알림 — 품질 검토 확신도 0\.7 미만 · 줄 (\d+) · 사람 확인 필요$/, m => `끝 · 사람 확인 필요 — 줄 ${m[1]} 품질 검토`],   // D-462 B
  ];
  const short = s => { const t = String(s ?? ""), hit = SAY.find(([re]) => re.test(t));
    return (hit ? (typeof hit[1] === "function" ? t.replace(hit[0], (...m) => hit[1](m)) : hit[1]) : t.replace(/dataset/g, "시험 데이터")).replace(/ · 실제 모드 — 측정 Run은 한 번에 하나\(동시 수 같음\)/g, "").replace(/ · 확인 suite 나빠짐 /g, " · 확인 시험 나빠짐 ").replace(/ · 권고 reject로 남김/g, " · 권고 버림으로 남김").replace(/한 번에 한 바퀴/g, "한 번에 개선 하나").replace(/바퀴/g, "개선").replace(/손잡이가/g, "옵션이").replace(/손잡이를/g, "옵션을").replace(/손잡이는/g, "옵션은").replace(/손잡이/g, "옵션"); };
  // D-492 덧 18 ①: the screen says '자동 개선' — a stored line (log · status · result) reads it on mouse-over too, the record unchanged
  const noWheel = t => String(t ?? "").replace(/한 번에 한 바퀴/g, "한 번에 개선 하나").replace(/이 바퀴/g, "이 자동 개선").replace(/바퀴/g, "개선").replace(/손잡이가/g, "옵션이").replace(/손잡이를/g, "옵션을").replace(/손잡이는/g, "옵션은").replace(/손잡이/g, "옵션");   // D-525 덧 30: a stored '손잡이' too
  const say = s => { const t = String(s ?? ""), w = short(t), whole = noWheel(t); return w === whole ? esc(w) : hv(w, whole); };
  const started = w => w.kind === "efficiency" ? "· 효율 개선으로 시작" : /사람 체크|사람 ✗ 라벨/.test(w.started_by) ? "· 사람 체크로 시작"
    : /진단 신호/.test(w.started_by) ? "· 자동 점검으로 시작" : `· ${w.started_by}`;
  const cell = (w, c, i, name) => `<span class="wcell" data-cell="${i + 1}" data-c="${c}"${c === "idle" && /^끝/.test(w.status || "") ? ' title="도달 안 함"' : ""}>${esc(short(name))}</span>`;   // R4 · C-5: grey, not a failure
  const spendLine = s => hv(`비용 $${s.wheel.toFixed(2)} · 상한 ${s.cap == null ? "없음" : "$" + s.cap.toFixed(2)}`,
    `실제 호출 ${s.calls} · 이 자동 개선 $${s.wheel.toFixed(2)} · 이 서버 합계 $${s.server.toFixed(2)} / 상한 ${s.cap == null ? "없음" : "$" + s.cap.toFixed(2)}`);
  function center(ctx) {
    const { ui, esc } = ctx, st = state(ui), d = st.data;
    if (!d) return `<div class="small muted" id="auto-loading">읽는 중…</div>`;
    if (d.state === "error") return `<div class="rg err small" id="auto-error">${esc(d.err)}</div>`;
    if (d.state === "off") return `<div class="small muted" id="auto-off">자동 진단 꺼짐 — 저장 표 없음(migration 승인 대기)</div>`;
    const head = `<div class="row"><span class="seg"><span class="on" id="auto-seg-list">개선 목록</span></span>${effHead(ctx, d)}</div>` + panel(ctx, d);
    if (!d.wheels.length) return head + `<div class="vt small muted" id="auto-empty">아직 개선이 없습니다 — 사람 체크 기록이 오면 저절로 시작합니다</div>`;
    const sel = pick(st, d);
    const rows = d.wheels.map(w => `<div class="vt wrow${w === sel ? " on" : ""}" data-wheel="${esc(w.name)}" data-act="auto-pick" data-k="${esc(w.name)}" style="cursor:pointer">` +
      `<div class="row"><b>${esc(w.name)}</b><span class="small">${hv(started(w), `· ${KIND[w.kind] || w.kind} · ${w.started_by} · ${baseWords(w, ctx.p)}`)}</span>` +
      `<span class="sp"></span><span class="small" data-wheel-status>${say(w.status)}</span>` +
      (w.far ? `<span class="small" data-wheel-far>· ${esc(w.far)}</span>` : "") +   // D-659 ③: an earlier iteration went further
      `<span class="small" data-act="go" data-to="#/p/${esc(ctx.p.id)}/auto/${esc(w.name)}" data-wheel-open style="margin-left:8px;color:var(--accent,#3056d3);cursor:pointer">열기 ›</span></div>` +
      `<div class="wbar">${w.cells.map((c, i) => cell(w, c, i, d.cells[i])).join("")}</div></div>`).join("");
    const log = `<div class="vt" id="auto-log" style="margin-top:10px"><div class="row"><b>${esc(sel.name)} 진행 기록</b></div>` +
      `<table class="small"><tr><th>시각</th><th>칸</th><th>무엇을 했나</th></tr>${sel.log.map(l => `<tr data-log-cell="${l.cell}"><td>${hm(l.at)}</td><td>${esc(short(d.cells[l.cell - 1]))}</td><td>${say(l.text)}</td></tr>`).join("")}</table></div>`;
    return head + `<div id="auto-wheels" style="display:grid;gap:8px;margin-top:8px">${rows}</div>` + log;
  }
  function insp(ctx) {
    const { ui, esc } = ctx, st = state(ui), d = st.data;
    if (!d || !d.wheels || !d.wheels.length) return `<div class="small muted" id="auto-wheel-none">개선이 없습니다</div>`;
    const w = pick(st, d);
    return `<h3>${esc(w.name)}</h3><div class="small" id="auto-wheel-info"><div data-w-k="start">시작: ${esc(String(w.started_by || "").replace(/^자동\(사람 체크\) · (\d+)건$/, "사람 체크 $1건"))}</div>` +
      `<div data-w-k="scope">${hv(`${w.scope} · ${w.goal}`, `범위 ${w.scope} · 목표 ${w.goal}`)}</div>` +
      `<div data-w-k="base">${hv(baseWords(w, ctx.p), `${baseWords(w, ctx.p)} · revision ${(w.base.revision || "").slice(7, 15)}`)}</div></div>` +
      (w.sets ? `<h3 style="margin-top:10px">③ 세트</h3><div class="small" id="auto-wheel-sets">고칠 ${w.sets.fix} · 지킬 ${w.sets.keep} · 흔들림 ${w.sets.flaky} · 숨김 ${w.sets.hidden}${w.doc ? ` · ${esc(w.doc)}` : ""}</div>` : "") +
      (w.spend ? `<h3 style="margin-top:10px">비용</h3><div class="small" id="auto-wheel-spend">${spendLine(w.spend)}</div>` : "") +
      (w.kind === "efficiency" ? `<h3 style="margin-top:10px">판정</h3><div class="small" id="auto-wheel-agents">${hv("기준 표", "구조 가설(규칙) · 기준 표 판정 · judge 없음")}</div>` : "") +
      `<h3 style="margin-top:10px">사람이 할 일</h3><div class="small" id="auto-wheel-human">${/^끝/.test(w.status || "") ? "할 일 —" : hv("할 일: ⑦에서 승인", "⑦ 배포 승인 한 번 — 차례가 오면 막대의 ⑦이 파랑")}</div>`;   // an ended wheel has nothing left to do
  }
  // ---------------------------------------------------------------- EV-035 CHG-189: one wheel (#/p/{pid}/auto/A-n), read again every 3 s
  const POLL_MS = 3000;
  function loadDetail(ctx, name) {
    const { p, ui, api, render } = ctx, st = state(ui);
    const key = `${p.id}/${name}`;
    const read = () => api("GET", `/api/projects/${p.id}/auto/${encodeURIComponent(name)}`).then(d => { if (st.detailKey === key) { st.detail = d; render(); } })
      .catch(e => { if (st.detailKey === key) { st.detail = { error: e.message }; render(); } });
    if (st.detailKey !== key) {
      Object.assign(st, { detailKey: key, detail: null });
      clearInterval(st.timer);
      st.timer = setInterval(() => { if (location.hash.endsWith(`/auto/${name}`)) read(); else clearInterval(st.timer); }, POLL_MS);
      read();
    }
  }
  const SRC = { document: "시작 가설 문서", agent: "가설 에이전트", rule: "규칙", harness: "시험 장치" };
  const SRC_EFF = { agent: "에이전트", rule: "규칙", harness: "시험 장치" };   // D-462 A: who made an efficiency wheel's structure hypothesis
  // a float with more than two decimals reads to two (EV-043 A3-N4: 1 − 0.7 = 0.30000000000000004 → 0.3; the stored value stays)
  const CHANGE = { narrow: "좁힘", widen: "넓힘", off: "끔" };   // D-543 ⑥ ②: a relation hypothesis {id, change, when}
  const relVal = v => `${v.id} ${CHANGE[v.change] || v.change}${v.when && (v.when.sensors || []).length ? " · " + v.when.sensors.map(c => c.join(" ")).join(" · ") : ""}${v.when && v.when.equipment_id ? ` · 설비 ${v.when.equipment_id}` : ""}`;   // D-546 덧 29
  const val = v => v == null ? "—" : typeof v === "object" ? (v.line ? `+ "${v.line}"` : v.change && v.id ? relVal(v) : JSON.stringify(v)) : typeof v === "number" && Math.round(v * 100) / 100 !== v ? String(Math.round(v * 100) / 100) : String(v);
  function centerDetail(ctx) {
    const { ui, esc } = ctx, d = state(ui).detail;
    if (!d) return `<div class="small muted" id="auto-loading">읽는 중…</div>`;
    if (d.error) return `<div class="rg err small" id="auto-error">${esc(d.error)}</div>`;
    const names = d.cells_names || [];
    const head = `<div class="row" id="auto-detail-head"><span data-act="go" data-to="#/p/${esc(ctx.p.id)}/auto" style="cursor:pointer">‹ 개선 목록</span>` +
      `<b style="margin-left:8px">${hv(`개선 ${d.name} · ${d.kind === "efficiency" ? "효율" : d.goal} · 기준 ${d.base.name || "?"}`, `자동 개선 ${d.name} · ${KIND[d.kind] || "성능 개선"} · 범위 ${d.scope} · 목표 ${d.goal} · 기준 ${d.base.name || "?"}(운영)`)}</b>` +
      `<span class="sp"></span><span class="small" data-wheel-status>${say(d.status)}</span>` +
      ((d.status || "").includes("고정 흐름 다시 확인") && d.base && d.base.release ? `<span class="link small" data-act="auto-ff-open" data-rel="${esc(d.base.release)}" data-name="${esc(d.base.name || "")}" id="auto-ff-hold" style="margin-left:8px">고정 흐름 창 ›</span>` : "") +   // D-485: the base version's window, from the ⑤ hold
      (d.ended_at == null && !d.stop ? `<span class="btn" data-act="auto-stop-open" id="auto-stop" style="margin-left:8px">이 자동 개선 멈춤</span>` : "") +   // D-476 덧 20
      (d.quality_wait ? `<span class="btn p" data-act="auto-q-open" data-k="pass" id="auto-q-pass" style="margin-left:8px">사람 확인 — 줄 ${esc(d.quality_wait.row)} 통과</span>` +   // D-652 다섯째
        `<span class="btn" data-act="auto-q-open" data-k="drop" id="auto-q-drop" style="margin-left:4px">버림</span>` : "") + `</div>` +
      stopPanel(ctx, d) + qualityPanel(ctx, d) +
      (state(ui).panel === "flow" ? flowPanel(ctx, `<span class="btn" data-act="auto-panel-close" id="auto-panel-cancel">취소</span>`,
        state(ui).err ? `<div class="rg err small" id="auto-panel-err">${esc(state(ui).err)}</div>` : "") : "") +
      `<div class="wbar">${d.cells.map((c, i) => cell(d, c, i, names[i] || "")).join("")}</div>`;
    const r = d.responsible, rel = (ctx.p.releases || []).find(x => x.id === d.release.id);
    const graph = rel && rel.graph ? `<div id="auto-graph" style="overflow-x:auto;margin-top:8px">${ctx.graph(rel.graph, { suspect: r && r.signal, responsible: r && r.decided ? r.node : null, changed: d.changed || [] }, rel)}</div>` +
      `<div class="small muted" id="auto-graph-legend">분홍 = 문제가 보인 노드 · 보라 = 고칠 노드${(d.changed || []).length ? ` · 주황 = ${hv("1등 줄이 바꾼 노드", `${d.changed.join(" · ")} — 고칠 노드와 같으면 보라 테두리`)}` : ""}</div>` : "";
    const ok = x => x === true ? "✓ 맞음" : x === false ? "✗ 틀림" : "— judge 없음";
    const ROLE = { llm: "모델로 쓰기", retrieve: "리트리버", validate: "검사", router: "갈래", render: "결과 쓰기", custom_code: "Pack 함수", predict: "모델 예측", human_review: "사람 검토" };
    const role = id => { const n = ((rel && rel.graph && rel.graph.nodes) || []).find(x => x.name === id); return n ? hv(`${ROLE[String(n.kind || "").split("@")[0]] || "노드"} 노드`, id) : esc(id); };   // a node the release does not list keeps its id
    // D-549 덧 3 (감안 2 · 4): the diagnosis as 대상 · 근거 (automain.diagnosis_of) — a node today, a relation later in the same line
    const dg = d.diagnosis, BASIS = { retriever: "시작 문서 가설", check: "사람 체크가 가리킴", deviation: "실행 기록에서 처음 어긋남", probe: "노드 하나씩 바꿔 봄" };
    const dgEv = e => [(e && e.text) || BASIS[e && e.basis] || "앞 노드로 거슬러 봄", e && (e.cases || []).length ? `case ${e.cases.length}` : ""].filter(Boolean).join(" · ");
    const diagLine = dg ? `<div class="small" id="auto-diag">대상 ${esc({ node: "노드", relation: "관계" }[dg.target_kind] || dg.target_kind)} ${esc(dg.target || "—")} · 근거 ${esc(dgEv(dg.evidence))}</div>` : "";
    const resp = r ? `<div class="vt" id="auto-resp" style="margin-top:10px"><div class="row"><b>④ 고칠 노드 찾기</b><span class="small muted">${r.basis === "retriever" ? hv("시작 문서 가설", "시작 문서 가설의 리트리버 node(리트리버 개선)") : r.basis === "check" ? "사람 체크가 가리킨 노드" : r.basis === "deviation" || r.basis === "probe" ? hv("실행 기록을 노드 순서대로 봄", "Pack 선언 기준(라벨 파일 diagnose)으로 처음 어긋난 노드 · 비용 0") : hv("앞 노드로 거슬러 봄", "reads를 거슬러 expect에 대어 봄")}${r.case ? ` · ${hv("사람 체크 case 사슬", r.case)}` : ""} · 고칠 case ${r.cases}개</span></div>` +
      (r.rows.length ? `<table class="small"><tr><th>node</th><th>입력</th><th>출력</th></tr>${r.rows.map(x => `<tr data-resp-node="${esc(x.node)}"><td>${esc(x.node)}${x.step === 1 ? " (첫 걸음)" : ""}${x.signal ? " (신호)" : ""}</td><td data-c="input">${x.step === 1 ? "—" : x.input_ok === true ? "✓" : x.input_ok === false ? "✗ 앞 node 출력 틀림" : "—"}</td><td>${x.step === 1 ? "—" : x.signal ? "✗ 검사 실패" : x.why ? `${x.ok === false ? "✗" : x.ok ? "✓" : "—"} ${esc(x.why)}` : ok(x.ok)}</td></tr>`).join("")}</table>` : "") +   // 143: a node a check or a start document names has no chain to show
      `<div class="small" id="auto-resp-line">${r.basis === "retriever" ? `→ 고칠 노드 = <b>${role(r.node)}</b> — 시작 문서 가설` : r.basis === "deviation" ? `→ 고칠 노드 = <b>${role(r.node)}</b> — 처음 어긋남: ${esc(r.evidence || "")}` : r.basis === "probe" ? `→ 실행 기록으로 판정 안 남 — 노드 하나씩 바꿔 봄: ${(r.probe || []).map(role).join(" → ")}` : r.basis === "check" ? `→ 고칠 노드 = <b>${role(r.node)}</b> — 사람 체크가 가리킴` : r.decided ? `→ 출력이 틀린 첫 노드 = <b>${role(r.node)}</b>` : `고칠 노드 판단 불가(가짜 judge) — 문제가 보인 노드 ${role(r.node)}로 둠`}</div>${diagLine}</div>` : "";
    const hy = d.hypotheses || [];
    // D-549 덧 3 (감안 4): 대상 · 바꿈 · 근거 — the server's cells (app.hyp_cells); an option keeps its range on mouse-over
    const cellsTd = h => { const c = h.cells || { target: { word: "노드", id: h.node }, change: `옵션 ${h.knob} → ${val(h.value)}`, evidence: SRC[h.source] || h.source || "—" };
      const opt = h.option && c.change.startsWith("옵션 ") ? `옵션 ${hv(h.option.label, `옵션 ${h.knob} · ${h.option.range} · 지금 ${val(h.option.now)}`)} → ${esc(val(h.value))}` : esc(c.change);
      return `<td data-c="target">${esc(c.target.word)} ${esc(c.target.id)}</td><td data-c="change">${opt}</td><td data-c="evidence">${esc(c.evidence)}</td>`; };
    const rows = hy.map(h => `<tr data-hyp="${h.id}" data-batch="${esc(h.batch)}"><td>${h.iteration}</td><td>${h.discarded ? "—" : h.batch === "first" ? "처음" : `대기 ${h.batch_no - 1}`}</td>` +
      cellsTd(h) + `<td data-c="predicts">${[esc((h.predicts || []).join(" · ")), (h.checked || []).length ? hv(`사람 체크 ${h.checked.length}`, `싼 시험에 더한 사람 체크 case — ${h.checked.join(" · ")}`) : ""].filter(Boolean).join(" + ")}</td>` +
      `<td data-c="cheap">${esc(h.cheap || "—")}</td><td>${say(h.discarded || h.result || "")}</td></tr>`).join("");
    const its = [...new Set(hy.map(h => h.iteration))].length, M = (d.settings || {}).rebuilds || 3;
    const byAgent = hy.some(h => h.source === "agent");
    const note = d.kind === "efficiency" ? esc(byAgent ? "구조 가설(에이전트) · 규칙 검사 · 시험 최대 6" : "구조 가설(규칙) · 고정 흐름 검사") : hv("가설 = 노드 하나의 옵션 하나", `node 하나 · 옵션 하나 · 값 · 한 번에 최대 ${(d.settings || {}).max_hypotheses || 10}개, 넘으면 대기 · 검사를 느슨하게 하는 가설은 버림`);
    const effRowsH = hy.map(h => `<tr data-hyp="${h.id}"><td>${h.iteration}</td><td>${esc(h.knob.replace("구조:", ""))} ${esc(h.node)}${h.value == null ? "" : ` · ${esc(val(h.value))}`}</td>` +
      `<td data-c="src">${esc(SRC_EFF[h.source] || h.source)}</td><td data-c="ff">${esc(h.discarded ? "—" : h.fixed_flow || "—")}</td><td>${say(h.discarded || h.result || "")}</td></tr>`).join("");
    const hyps = `<div class="vt" id="auto-hyps" style="margin-top:10px"><div class="row"><b>④ 가설</b><span class="small muted">${note}</span></div>` +
      (!hy.length ? `<div class="small muted" id="auto-hyps-empty">아직 가설이 없습니다</div>`
        : d.kind === "efficiency" ? `<table class="small"><tr><th>반복</th><th>가설(구조)</th><th>출처</th><th>고정 흐름</th><th>결과</th></tr>${effRowsH}</table>`
        : `<table class="small"><tr><th>반복</th><th>묶음</th><th>대상</th><th>바꿈</th><th>근거</th><th>바뀔 case</th><th>싼 시험</th><th>결과</th></tr>${rows}</table>`) +
      `<div class="small" id="auto-hyps-sum">반복 ${d.iteration || its || 1} / ${M}${d.outcome ? ` — ${say(d.outcome)}` : ""}</div></div>`;
    const rws = d.rows || [], L = d.labels || {};
    const win = rws.filter(x => x.release && x.outcome === "winner").pop() || rws.find(x => x.release);   // after a recommended reject the next row's version
    const lastRec = (rws.filter(x => x.recommendation).pop() || {}).recommendation;   // EV-043 CHG-211: ⑦ shows the confirm suite (test) once, after the choice
    const confirm = lastRec && lastRec.decision === "adopt" && lastRec.confirm ? `<div class="small" id="auto-confirm-line">⑦ ${esc(lastRec.confirm.text)}</div>` : "";
    const money = v => v == null ? "—" : "$" + (v < 0.01 ? v.toFixed(5) : v.toFixed(3));
    const effRows = d.kind === "efficiency";   // EV-069 CHG-EF-7 (P-4): an efficiency row's columns
    const title = esc(String(L.title || "").replace(/^\d+ /, ""));   // EV-075: the server's table title without its leading number (v2 '④ 가설')
    // D-462 B: the quality review's findings under the row's result (case · question · field · why), its source beside it
    const qual = x => { const q = x.quality, fs = (q && q.findings) || [];
      if (!fs.length) return q && q.verdict === "pass" && !q.skipped && !q.failed   // D-659 ①: a review with no finding says it passed
        ? `${say(x.result)} · <span data-c="quality-pass">품질 검토 통과 · 지적 0</span> <span class="muted">· ${esc(q.source)}</span>` : say(x.result);
      const lines = fs.map(f => `${f.case} · ${f.question} · ${f.field} — ${f.why}${f.sure ? "" : " · 확신도 0.7 미만"}`).join("\n");
      return `<span data-c="quality" data-source="${esc(q.source)}">${hv(short(x.result), `${x.result}\n${lines}`)} <span class="muted">· ${esc(q.source)}</span></span>`; };
    const gone = `<span class="muted" data-c="not-reached" title="버린 줄 — Run을 돌리지 않음(실패 아님)">도달 안 함</span>`;   // R4 · C-5: grey, not a failure
    const table = rws.length && effRows ? `<div class="vt" id="auto-rows" style="margin-top:10px"><div class="row"><b>${title}</b></div>` +
      `<table class="small"><tr><th>${esc(L.row)}</th><th>가설</th><th>지킬 세트</th><th>비용(case당)</th><th>LLM 호출(case당)</th><th>지연</th><th>결과</th></tr>${rws.map(x => { const e = x.efficiency || {};
        const b = d.eff_base || {}, n2 = v => v == null ? "—" : String(Math.round(v * 100) / 100), s1 = v => v == null ? "—" : v.toFixed(1) + "s";
        const pair = (bv, rv, f) => `${f(bv)} → ${f(rv)}`;
        const who = `${esc(x.hypotheses.join(" + "))} · ${x.source ? `<span data-c="source">${esc(x.source)}</span>` : esc(x.node)}`;   // D-470 ⓒ: a merged row reads '합침'
        if (x.outcome === "discarded") return `<tr data-row="${x.id}" data-outcome="discarded"><td>${x.id}</td><td>${who}</td><td>${gone}</td>` +
          `<td data-c="cost" class="muted">—</td><td data-c="calls-llm" class="muted">—</td><td data-c="latency" class="muted">—</td><td>${say(x.result)}</td></tr>`;
        return `<tr data-row="${x.id}" data-outcome="${esc(x.outcome || "")}"><td>${x.id}</td><td>${who}</td><td>${esc(x.keep)}</td>` +
          `<td data-c="cost">${pair(b.cost, e.cost, money)}${e.cost_estimated ? " 추정" : ""}</td><td data-c="calls-llm">${pair(b.calls_llm, e.calls_llm, n2)}</td>` +
          `<td data-c="latency">${pair(b.latency, e.latency, s1)}</td><td>${qual(x)}</td></tr>`; }).join("")}</table>` +
      (d.compare ? `<div class="small" id="auto-compare-line">${lastRec ? `⑥ 권고 ${rw(lastRec.decision)} — ${esc((lastRec.reasons || []).join(" · "))} · ` : ""}<span data-act="go" data-to="${esc(d.compare)}" style="color:var(--accent,#3056d3);cursor:pointer" id="auto-compare-open">${esc(L.compare)}</span></div>` : "") +
      (win ? `<div class="small" id="auto-winner">${esc(L.winner.replace("{name}", win.release.name))} — <span data-act="auto-version-open" data-to="#/p/${esc(ctx.p.id)}/r/${esc(win.release.id)}" data-rel="${esc(win.release.id)}" style="color:var(--accent,#3056d3);cursor:pointer" id="auto-version-open">${esc(win.release.name)} 보기 ›</span></div>` : "") + `</div>`
      : rws.length ? `<div class="vt" id="auto-rows" style="margin-top:10px"><div class="row"><b>${title}</b><span class="small muted">${hv("표의 줄끼리 비교", L.note || "")}</span></div>` +
      `<table class="small"><tr><th>${esc(L.row)}</th><th>가설</th><th>싼 시험</th><th>고칠 세트</th><th>지킬 세트</th><th>토큰</th><th>지연</th><th>LLM 호출</th><th>참고(엄격 셈)</th><th>결과</th></tr>${rws.map(x => `<tr data-row="${x.id}" data-outcome="${esc(x.outcome || "")}"><td>${x.id}</td>` +
      `<td>${esc(x.hypotheses.join(" + "))} · ${esc(x.node)}</td><td>${esc(x.cheap)}</td><td>${esc(x.fix)}</td><td>${esc(x.keep)}</td><td>${x.tokens == null ? "—" : (x.tokens / 1000).toFixed(1) + "k"}</td><td data-c="latency">${x.latency == null ? "—" : x.latency.toFixed(1) + "s"}</td><td data-c="calls">${x.calls == null ? "—" : x.calls}</td><td data-c="ref" class="muted">${esc(x.ref || "—")}</td><td>${say(x.result)}</td></tr>`).join("")}</table>` +
      (d.compare ? `<div class="small" id="auto-compare-line">${(rws.find(x => x.recommendation) || {}).recommendation ? `⑥ 권고 ${rw(rws.filter(x => x.recommendation).pop().recommendation.decision)} (자동) — ` : ""}<span data-act="go" data-to="${esc(d.compare)}" style="color:var(--accent,#3056d3);cursor:pointer" id="auto-compare-open">${esc(L.compare)}</span></div>` : "") +
      confirm + (win ? `<div class="small" id="auto-winner">${esc(L.winner.replace("{name}", win.release.name))} — <span data-act="auto-version-open" data-to="#/p/${esc(ctx.p.id)}/r/${esc(win.release.id)}" data-rel="${esc(win.release.id)}" style="color:var(--accent,#3056d3);cursor:pointer" id="auto-version-open">${esc(win.release.name)} 보기 ›</span></div>` : "") + `</div>` : "";
    return head + graph + resp + hyps + table;
  }
  // D-476 덧 20: '이 바퀴 멈춤' — the alias and a reason line (≤ 200); the server ends the wheel before its next step ('멈추는 중' → '끝 · 사람이 멈춤')
  function stopPanel(ctx, d) {
    const { esc } = ctx, st = state(ctx.ui);
    if (st.panel !== "stop" || d.ended_at != null || d.stop) return "";
    const err = st.err ? `<div class="rg err small" id="auto-panel-err">${esc(st.err)}</div>` : "";
    return `<div class="vt" id="auto-stop-panel" style="margin-top:8px"><div class="row"><b>이 자동 개선 멈춤 — ${esc(d.name)}</b></div><table class="small">` +
      `<tr><td>별명</td><td><input id="auto-stop-by" placeholder="owner-01"></td></tr>` +
      `<tr><td>까닭</td><td><input id="auto-stop-why" maxlength="200"></td></tr></table>${err}` +
      `<div class="row"><span class="sp"></span><span class="btn" data-act="auto-panel-close" id="auto-stop-cancel">취소</span><span class="btn p" data-act="auto-stop-go" id="auto-stop-go">멈춤</span></div></div>`;
  }
  // D-652 다섯째: the quality review held a row for a person (확신도 0.7 미만) — pass (the wheel goes on to the version) or drop (the next row),
  // with the alias and a reason (≤ 200) kept on the row and in the log
  function qualityPanel(ctx, d) {
    const { esc } = ctx, st = state(ctx.ui);
    if (st.panel !== "quality" || !d.quality_wait) return "";
    const pass = st.qk !== "drop", err = st.err ? `<div class="rg err small" id="auto-panel-err">${esc(st.err)}</div>` : "";
    return `<div class="vt" id="auto-q-panel" style="margin-top:8px"><div class="row"><b>사람 확인 — 줄 ${esc(d.quality_wait.row)} ${pass ? "통과" : "버림"}</b></div>` +
      `<div class="small muted">${pass ? "이 줄로 이어 감 — 새 버전 · ⑥ 비교 · ⑦ 사람 승인은 그대로" : "이 줄을 표에서 빼고 다음 줄로"}</div><table class="small">` +
      `<tr><td>별명</td><td><input id="auto-q-by" placeholder="owner-01"></td></tr>` +
      `<tr><td>까닭</td><td><input id="auto-q-why" maxlength="200"></td></tr></table>${err}` +
      `<div class="row"><span class="sp"></span><span class="btn" data-act="auto-panel-close" id="auto-q-cancel">취소</span><span class="btn p" data-act="auto-q-go" id="auto-q-go">${pass ? "통과" : "버림"}</span></div></div>`;
  }
  function inspDetail(ctx) {
    const { ui, esc } = ctx, d = state(ui).detail;
    if (!d || d.error) return "";
    const s = d.settings || {};
    return `<h3>${esc(d.name)} · ${say(d.status)}</h3>` +
      (d.sets ? `<h3 style="margin-top:10px">③ 세트</h3><div class="small" id="auto-wheel-sets">고칠 ${d.sets.fix} · 지킬 ${d.sets.keep} · 흔들림 ${d.sets.flaky} · 숨김 ${d.sets.hidden}${d.doc ? ` · ${esc(d.doc)}` : ""}</div>` : "") +
      ((rec => rec ? `<h3 style="margin-top:10px">⑥ 판정 권고</h3><div class="small" id="auto-wheel-rec">${rw(rec.decision)} (자동 · ${d.kind === "efficiency" ? "기준 표" : "채점 에이전트"}) — ${esc((rec.reasons || []).join(" · "))}</div>` + (rec.scoring ? `<div class="small muted" id="auto-wheel-scoring">${esc(rec.scoring)}</div>` : "") : "")(((d.rows || []).filter(x => x.recommendation).pop() || {}).recommendation)) +
      (d.spend ? `<h3 style="margin-top:10px">비용</h3><div class="small" id="auto-wheel-spend">${spendLine(d.spend)}</div>` : "") +
      `<h3 style="margin-top:10px">반복 규칙</h3><div class="small" id="auto-rules">한 번에 가설 최대 ${s.max_hypotheses || 10}개 · 나머지 대기<br>모두 떨어지면 대기 ${s.max_hypotheses || 10}개씩 → 대기도 떨어지면 새로 세움(반복 +1)<br>반복 기본 ${s.rebuilds || 3} · 그 뒤에도 실패면 알림</div>`;
  }
  // ---------------------------------------------------------------- EV-069 CHG-EF-7 (P-1 · P-2 · P-3): start · settings · fixed flow
  const GOALS = [["cost", "비용(USD)"], ["calls", "LLM 호출"], ["latency", "지연"]];
  const DAYS = [["mon", "월"], ["tue", "화"], ["wed", "수"], ["thu", "목"], ["fri", "금"], ["sat", "토"], ["sun", "일"]];
  function effHead(ctx, d) {
    const { esc } = ctx, e = d.efficiency || {};
    const why = !e.operational ? `<span class="chip" id="auto-eff-why">운영 버전 없음</span>`
      : e.open ? `<span class="chip" data-act="auto-pick" data-k="${esc(e.open)}" id="auto-eff-why" style="cursor:pointer">열린 개선 ${esc(e.open)} ›</span>`
      : e.fixed_flow !== "확인함" ? `<span class="chip" data-act="auto-ff-open" id="auto-eff-why" style="cursor:pointer">고정 흐름 ${esc(e.fixed_flow || "확인 필요")} ›</span>` : "";
    const can = e.operational && !e.open && e.fixed_flow === "확인함";
    return `<span class="sp"></span>${why}` + (e.operational ? `<span class="link small" data-act="auto-ff-open" id="auto-ff-link" style="margin-left:8px">고정 흐름</span>` : "") +
      `<span class="link small" data-act="auto-set-open" id="auto-set-link" style="margin-left:8px">설정</span>` +
      `<span class="small muted" data-act="auto-more-soon" id="auto-more-soon" style="margin-left:8px;cursor:pointer">세부 설정 <span class="pv-soon">준비 중</span></span>` +   // EV-073 CHG-AI-10 (D-444 #4): 자리만 · 요청 0
      `<span class="btn p${can ? "" : " off"}" data-act="auto-eff-open" id="auto-eff-start" style="margin-left:8px">효율 개선 시작</span>`;
  }
  function flowPanel(ctx, close, err) {   // D-485: the fixed-flow window of the version it was opened for (the list page · a wheel's page)
    const { esc } = ctx, st = state(ctx.ui);
    const f = st.flow;
    if (!f) return `<div class="vt small muted" id="auto-ff-panel" style="margin-top:8px"><div class="row">${st.err ? "" : "읽는 중…"}<span class="sp"></span>${close}</div>${err}</div>`;
    const show = v => v == null ? "" : Array.isArray(v) ? (v.length ? v.map(show).join(" · ") : "없음")
      : typeof v === "object" ? Object.entries(v).map(([k, x]) => x && typeof x === "object" && !Array.isArray(x) ? `${k}: ${show(x)}` : show(x)).join(" · ")
      : typeof v === "string" && v.startsWith("sha256:") ? v.slice(7, 15) : String(v);
    const rows = (f.items || []).map(i => `<tr data-ff="${esc(i.id)}"><td>${esc(i.what)}</td><td data-c="ff-value">${esc(show(i.value))}</td><td class="muted">${esc(i.basis || "")}</td></tr>`).join("");
    const foot = f.state === "확인함" ? `<div class="small" id="auto-ff-done">확인함 ${f.confirmed_at ? hm(f.confirmed_at) : ""} ${esc(f.confirmed_by || "")}</div>`
      : f.state === "읽지 못함" ? `<div class="rg err small" id="auto-ff-why">읽지 못함 — ${esc(f.why || "")}</div>`
      : `<div class="row"><input id="auto-ff-by" placeholder="owner-01"><span class="btn p" data-act="auto-ff-confirm" id="auto-ff-confirm">확인</span></div>`;
    return `<div class="vt" id="auto-ff-panel" style="margin-top:8px"><div class="row"><b>고정 흐름 — ${esc(st.flowName || "")} · ${esc((f.revision || "").slice(7, 15))}</b><span class="sp"></span>${close}</div>` +
      `<table class="small"><tr><th>항목</th><th>이 Pack에서 읽은 값</th><th>근거</th></tr>${rows}</table>${foot}${err}</div>`;
  }
  function panel(ctx, d) {
    const { esc } = ctx, st = state(ctx.ui), e = d.efficiency || {};
    if (st.soon && !st.panel) return `<div class="pv-grey" id="auto-more-note" style="margin-top:8px">세부 설정 · 준비 중</div>`;
    const err = st.err ? `<div class="rg err small" id="auto-panel-err">${esc(st.err)}</div>` : "";
    // EV-075 (054 D-2): a name registered more than once reads with its order among that name and the day; id · time on mouse-over
    const all = ctx.p.suites || [], two = d2 => String(d2).padStart(2, "0"), day = t => { const x = new Date(t * 1000); return `${two(x.getMonth() + 1)}-${two(x.getDate())}`; };
    const at = t => `${day(t)} ${two(new Date(t * 1000).getHours())}:${two(new Date(t * 1000).getMinutes())}`;
    const word = s => { const same = all.filter(x => x.name === s.name).sort((x, y) => (x.created_at || 0) - (y.created_at || 0));
      return same.length < 2 ? esc(s.name) : `${esc(s.name)} · v${same.indexOf(s) + 1} · ${day(s.created_at)}`; };
    const opts = (sel, blank) => (blank ? `<option value="">—</option>` : "") + all.map(s => `<option value="${esc(s.id)}"${all.some(x => x !== s && x.name === s.name) ? ` title="${esc(s.id)} · ${at(s.created_at)} 등록"` : ""}${s.id === sel ? " selected" : ""}>${word(s)}</option>`).join("");
    const close = `<span class="btn" data-act="auto-panel-close" id="auto-panel-cancel">취소</span>`;
    if (st.panel === "start" && e.operational) return `<div class="vt" id="auto-eff-panel" style="margin-top:8px"><div class="row"><b>효율 개선 시작</b></div><table class="small">` +
      `<tr><td>대상</td><td id="auto-eff-target">${esc(e.operational.name)} · 운영 · ${esc((e.operational.revision || "").slice(7, 15))}</td></tr>` +
      `<tr><td>목표</td><td>${GOALS.map(([k, n]) => `<label><input type="checkbox" id="auto-eff-g-${k}" checked> ${n}</label>`).join(" ")}</td></tr>` +
      `<tr><td>작업 suite</td><td><select id="auto-eff-work">${opts(e.work_suite)}</select></td></tr>` +
      `<tr><td>확인 suite</td><td><select id="auto-eff-conf">${opts(null, true)}</select></td></tr>` +
      `<tr><td>별명</td><td><input id="auto-eff-by" placeholder="owner-01"></td></tr></table>${err}` +
      `<div class="row"><span class="sp"></span>${close}<span class="btn p" data-act="auto-eff-go" id="auto-eff-go">시작</span></div></div>`;
    if (st.panel === "flow") {
      return flowPanel(ctx, close, err);
    }
    if (st.panel === "settings") {
      const g = st.settings;
      if (!g) return `<div class="vt small muted" id="auto-set-panel" style="margin-top:8px"><div class="row">${st.err ? "" : "읽는 중…"}<span class="sp"></span>${close}</div>${err}</div>`;
      const sc = g.schedule || {};
      return `<div class="vt" id="auto-set-panel" style="margin-top:8px"><div class="row"><b>설정 — 트리거</b></div><table class="small">` +
        `<tr><th>종류</th><th>시작</th><th>범위</th><th>목표</th></tr><tr><td>성능 개선</td><td>사람 체크</td><td>옵션만</td><td>정확도</td></tr>` +
        `<tr><td>효율 개선</td><td><select id="auto-set-day">${DAYS.map(([k, n]) => `<option value="${k}"${(sc.day || "mon") === k ? " selected" : ""}>매주 ${n}</option>`).join("")}</select> ` +
        `<input id="auto-set-time" value="${esc(sc.time || "02:00")}" size="5"> <label><input type="checkbox" id="auto-set-off"${g.schedule ? "" : " checked"}> 끔</label></td><td>${esc(g.scope)}</td><td>비용 · LLM 호출 · 지연</td></tr></table>` +
        `<table class="small"><tr><td>작업 suite</td><td><select id="auto-set-work">${opts(g.work_suite, true)}</select></td></tr>` +
        `<tr><td>확인 suite</td><td><select id="auto-set-conf">${opts(g.confirm_suite, true)}</select></td></tr>` +
        `<tr><td>별명</td><td><input id="auto-set-by" placeholder="owner-01"></td></tr></table>${err}` +
        `<div class="row"><span class="sp"></span>${close}<span class="btn p" data-act="auto-set-save" id="auto-set-save">저장</span></div></div>`;
    }
    return "";
  }
  function act(a, t, ctx) {
    const { p, api, render } = ctx, st = state(ctx.ui), e = (st.data || {}).efficiency || {};
    const v = id => ((document.getElementById(id) || {}).value || "").trim();
    const again = () => { st.hash = null; load(ctx); };
    const fail = err => { st.err = err.message; render(); };
    if (a === "auto-panel-close") { st.panel = null; st.err = null; render(); return true; }
    if (a === "auto-version-open") {   // 054 D-1: the wheel's new version may be newer than the page's state — read the releases again first
      const to = t.dataset.to;
      if ((p.releases || []).some(r => r.id === t.dataset.rel)) go(to); else refresh().then(() => go(to), () => go(to));
      return true;
    }
    if (a === "auto-stop-open") { st.panel = "stop"; st.err = null; render(); return true; }   // D-476 덧 20
    if (a === "auto-stop-go" && st.detail && st.detail.name) {
      const name = st.detail.name;
      api("POST", `/api/projects/${p.id}/auto/${encodeURIComponent(name)}/stop`, { by: v("auto-stop-by"), reason: v("auto-stop-why") })
        .then(() => { st.panel = null; st.err = null; if (st.detail && st.detail.name === name) st.detail = { ...st.detail, stop: { by: v("auto-stop-by") }, status: "멈추는 중" }; render(); }).catch(fail);
      return true;
    }
    if (a === "auto-q-open") { st.panel = "quality"; st.qk = t.dataset.k; st.err = null; render(); return true; }   // D-652 다섯째
    if (a === "auto-q-go" && st.detail && st.detail.name && !st.qBusy) {
      const name = st.detail.name;
      st.qBusy = true;
      api("POST", `/api/projects/${p.id}/auto/${encodeURIComponent(name)}/quality`, { decision: st.qk === "drop" ? "drop" : "pass", by: v("auto-q-by"), reason: v("auto-q-why") })
        .then(() => { st.qBusy = false; st.panel = null; st.err = null; if (st.detail && st.detail.name === name) st.detail = { ...st.detail, quality_wait: undefined, status: "이어 감" }; render(); })
        .catch(err => { st.qBusy = false; fail(err); });
      return true;
    }
    if (a === "auto-more-soon") { st.soon = !st.soon; render(); return true; }   // EV-073 CHG-AI-10: 요청 0
    if (a === "auto-eff-open") { if (!t.classList.contains("off")) { st.panel = "start"; st.err = null; render(); } return true; }
    if (a === "auto-eff-go") {
      const goals = GOALS.map(g => g[0]).filter(k => (document.getElementById(`auto-eff-g-${k}`) || {}).checked);
      api("POST", `/api/projects/${p.id}/auto/efficiency`, { by: v("auto-eff-by"), goals, work_suite: v("auto-eff-work") || null, confirm_suite: v("auto-eff-conf") || null })
        .then(r => { st.panel = null; st.err = null; st.sel = r.wheel; again(); }).catch(fail);
      return true;
    }
    if (a === "auto-ff-open" && (t.dataset.rel || e.operational)) {   // D-485: any Pack version (its band · the wheel's ⑤ hold), else the 운영 one
      const rid = t.dataset.rel || e.operational.id, name = t.dataset.name || (e.operational || {}).name || "";
      if (st.pid !== p.id) Object.assign(st, { pid: p.id, hash: null, data: null, settings: null });   // opened from a version band of this Project
      Object.assign(st, { panel: "flow", flow: null, err: null, flowName: name });
      if (t.dataset.to && location.hash !== t.dataset.to) location.hash = t.dataset.to;
      render();
      api("GET", `/api/projects/${p.id}/releases/${rid}/fixed-flow`).then(f => { if (st.pid === p.id) { st.flow = { ...f, release: rid, project: p.id }; render(); } }).catch(fail);
      return true;
    }
    if (a === "auto-ff-confirm" && st.flow && st.flow.release && st.flow.project === p.id) {   // the version the window shows (slice review 2 B2)
      api("POST", `/api/projects/${p.id}/releases/${st.flow.release}/fixed-flow/confirm`, { by: v("auto-ff-by") }).then(f => { st.flow = { ...f, release: st.flow.release, project: p.id }; st.err = null; again(); }).catch(fail);
      return true;
    }
    if (a === "auto-set-open") {
      Object.assign(st, { panel: "settings", settings: null, err: null }); render();
      api("GET", `/api/projects/${p.id}/auto/settings`).then(g => { st.settings = g.efficiency; render(); }).catch(fail);
      return true;
    }
    if (a === "auto-set-save") {
      const off = (document.getElementById("auto-set-off") || {}).checked;
      api("PUT", `/api/projects/${p.id}/auto/settings`, { by: v("auto-set-by"), efficiency: { schedule: off ? null : { day: v("auto-set-day"), time: v("auto-set-time") },
        work_suite: v("auto-set-work") || null, confirm_suite: v("auto-set-conf") || null } })
        .then(g => { st.settings = g.efficiency; st.panel = null; st.err = null; render(); }).catch(fail);
      return true;
    }
    return false;
  }
  return { load, center, insp, state, loadDetail, centerDetail, inspDetail, act, baseWords };
})();
