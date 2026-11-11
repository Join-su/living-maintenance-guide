/* EV-022 갈래 C — node 옆 팝오버(설명서 6 · 7 · 10 · 13쪽: 보기 · 고치기 · 실행 값 · ✦ AI — EV-073 35 · 36쪽), 변경 목록 카드
   (9 · 10 · 20쪽), 편집 띠(9쪽, 편집 버리기 S-8 · 원본 보기 S-9), 오류 즉시 표시(31~34쪽). 이 파일만 갈래 C가 고칩니다
   (지시 019a). app.js는 아래 자리만 부르고, 서버는 execution/api-contract.md v1의 API와 기존 node 상세 API만 씁니다.
   Agent Pack release에서만 동작하고, LangGraph 코드 release는 지금 그대로입니다(S-17). */
"use strict";
const Popover = (() => {
  const PV = {
    node: null, tab: "view", pos: null, ctx: null,                    // 열린 팝오버와 자리
    det: null, ioRes: null, prompts: null,                            // 서버에서 읽은 것 {key, data, err}
    form: {}, files: {}, removed: [], added: [], addKind: "evidence", addText: "", pick: null,   // 고치기 값
    start: false, preview: null, previewOps: null, busy: false, msg: null, err: null, ai: false,
    run: null, caseIdx: 0, iter: 1,                                   // 실행 값 탭
    edit: null, sel: [], dep: null, discard: false, bandErr: null,    // 편집본 · 변경 목록
  };
  const DONE = ["완료", "일부 실패", "취소됨"];
  const FIXED_VIEW_ONLY = ["승인됨", "이전"];                        // 34쪽 #16: 보기만 + '이 버전에서 새 편집'
  const RULES = ["evidence", "evidence_ids", "unsafe_assertion", "schema", "required", "range", "unit"];
  const LABEL = {
    min_results: "최소", relax: "다시 찾을 때 풀 조건",
    min_items: "최소 근거 수", min_score: "최소 매뉴얼 점수", require_source_types: "꼭 있어야 할 출처", top_k: "가져올 개수", search: "찾는 방식",
    fallback: "부족할 때", score_threshold: "최소 점수", mmr: "고루 뽑기", filter: "조건", query: "질의 문장", where: "조건", select: "가져올 칸",
    limit: "개수", provider: "모델 제공처", model: "모델", prompt: "프롬프트 파일", temperature: "temperature", max_tokens: "최대 토큰",
    template: "템플릿 파일", on_fail: "실패하면 갈 곳", rerank: "재정렬", pool: "재정렬 개수", method: "찾는 방식", fuse: "섞기", k: "RRF k",
    index: "벡터 DB 나누기", chunk_size: "청크 크기", overlap: "오버랩", cases: "갈 조건", default: "그 밖이면", routes: "검토 결과별 갈 곳", timeout: "기다리는 시간",
  };
  const SEARCH = [["similarity", "비슷한 것 순"], ["mmr", "겹치지 않게 고루"]];
  const VAL = { manual: "매뉴얼", history: "정비 이력", failure_type: "고장 유형" };              // 7 · 10쪽 칩의 쉬운 이름(실제 값은 작게)
  const KIND = { validate: "검사", retrieve: "리트리버", llm: "모델로 쓰기", router: "갈래", render: "결과 쓰기", human_review: "사람 검토", custom_code: "Pack 함수", predict: "모델 예측" };
  // 7 · 10쪽에 먼저 보이는 칸(옵션 표의 나머지 칸은 '다른 칸'으로 접음 — 지울 수는 없음)
  const PRIMARY = { retrieve: ["top_k", "search", "method", "fuse", "index", "fallback", "rerank"], validate: [], llm: ["prompt", "provider", "model"], router: ["cases", "default"], render: ["template"], human_review: ["routes", "timeout"] };
  const kindWord = k => KIND[String(k || "").split("@")[0]] || "";
  // 기본 규칙 값의 칸(packrt 기본 규칙) — 지금 값에 없는 칸도 고칠 수 있게 보임(7쪽: min_score를 새로 넣음)
  const RULE_KEYS = { evidence: { min_items: "int", min_score: "score", require_source_types: "list" } };
  const esc0 = x => String(x ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const short = x => String(x || "").replace(/^sha256:/, "").slice(0, 8);
  const lbl = k => LABEL[k] ? `<span title="${esc0(k)}">${LABEL[k]}</span>` : esc0(k);   // EV-075 (v2 12쪽): the option key on mouse-over

  /* ---------------------------------------------------------------- 상태 읽기 */
  const isPack = c => !!(c && c.rel && c.rel.pack);
  const ver = rel => rel.version || null;                                // api-contract: Pack release의 version 칸(없으면 아직 서버 전)
  const vstate = rel => (ver(rel) || {}).state || null;
  const editHere = c => !!(c.p.edit && c.p.edit.release === c.rel.id);
  const editElse = c => !!(c.p.edit && c.p.edit.release !== c.rel.id);
  const relName = (c, rid) => ((c.p.releases.find(r => r.id === rid) || {}).name) || rid;
  const reload = c => (c.refresh ? c.refresh() : c.render());           // /api/state 다시 읽기(리드가 ctx.refresh를 줄 때)
  const base = c => `/api/projects/${c.p.id}`;

  function load(slot, key, url, c, onErr) {
    if (PV[slot] && PV[slot].key === key) return PV[slot];
    PV[slot] = { key, data: null, err: null };
    c.api("GET", url).then(d => { if (PV[slot] && PV[slot].key === key) { PV[slot].data = d; c.render(); } })
      .catch(e => { if (PV[slot] && PV[slot].key === key) { PV[slot].err = onErr ? onErr(e) : e.message; PV[slot].status = e.status; c.render(); } });
    return PV[slot];
  }
  function editData(c) {   // EDIT(api-contract) — 이 release에 열린 편집이 있을 때만
    if (!editHere(c)) { PV.edit = null; return null; }
    const key = `${c.p.id}|${JSON.stringify(c.p.edit)}`;
    if (!PV.edit || PV.edit.key !== key) {
      PV.edit = { key, data: null, err: null };
      c.api("GET", `${base(c)}/edit`).then(d => { if (PV.edit && PV.edit.key === key) { PV.edit.data = d.edit; c.render(); } })
        .catch(e => { if (PV.edit && PV.edit.key === key) { PV.edit.err = e.message; c.render(); } });
    }
    return PV.edit.data;
  }

  /* ---------------------------------------------------------------- 팝오버 */
  function detail(c) {
    const runs = c.p.runs.filter(r => r.rel === c.rel.id && DONE.includes(r.status));
    const run = PV.tab === "run" ? (runs.find(r => r.uid === PV.run) || runs[runs.length - 1] || null) : null;
    const ci = run ? Math.min(PV.caseIdx, run.cases.length - 1) : 0;
    // 9 · 13쪽: 편집 중에는 편집본 값(edit=1 — api-contract v1.9), Run 값은 그 Run의 사본에서
    const q = run ? `?run=${encodeURIComponent(run.uid)}&case=${ci}&iter=${PV.iter}` : editHere(c) ? "?edit=1" : "";
    const key = [c.rel.id, PV.node, run ? run.uid : "", ci, PV.iter, JSON.stringify(c.p.edit || null)].join("|");
    return { st: load("det", key, `${base(c)}/releases/${c.rel.id}/nodes/${encodeURIComponent(PV.node)}${q}`, c), runs, run, ci };
  }
  function io(c) {
    const key = [c.p.id, c.rel.id, PV.node, JSON.stringify(c.p.edit || null)].join("|");
    return load("ioRes", key, `${base(c)}/edit/nodes/${encodeURIComponent(PV.node)}/io${editHere(c) ? "" : `?release=${encodeURIComponent(c.rel.id)}`}`, c);
  }
  function ioStrip(c, d) {   // 13쪽: 탭 위 입력 · 출력 칸(출처 node)
    const r = io(c), x = r.data;
    const reads = x ? x.reads.map(i => ({ key: i.key, nodes: i.from, missing: i.certain === false })) : (d ? d.reads.map(i => ({ key: i.name, nodes: null })) : []);
    const writes = x ? x.writes.map(o => ({ key: o.key, nodes: o.to })) : (d ? d.writes.map(o => ({ key: o.name, nodes: null })) : []);
    const name = k => { const l = ((x && x.labels) || {})[k]; return l && l.label && l.label !== k ? `<span title="${esc0(l.desc || k)}">${esc0(l.label)}</span> <span class="pv-k">${esc0(k)}</span>` : esc0(k); };
    const row = (it, arrow) => `<div class="pv-io-row${it.missing ? " pv-io-miss" : ""}" data-io-key="${esc0(it.key)}"${it.missing ? ' data-missing="1"' : ""}>${name(it.key)}${it.nodes && it.nodes.length ? ` ${arrow} <b>${it.nodes.map(esc0).join(" · ")}</b>` : it.nodes && arrow === "→" ? ` ${arrow} <b>아직 없음</b>` : ""}</div>`;
    const miss = reads.filter(i => i.missing);   // 32쪽 #4: 고른 값이 이 자리에서는 아직 없음
    const missBox = miss.length ? `<div class="pv-lock" id="pv-io-missing"><div class="pv-errline">고른 값이 이 자리에서는 아직 없음</div>${miss.map(i => `<div class="small">${esc0(i.key)}${i.nodes && i.nodes.length ? `은(는) ${i.nodes.map(esc0).join(" · ")}이(가) 만듭니다. 앞 연결이 바뀌어서` : "이"} 이 자리에서는 아직 비어 있습니다</div>`).join("")}<div class="muted small">다시 고르거나 연결을 되돌리세요 — 반영이 잠깁니다</div></div>` : "";
    const next = x && x.writes.length && x.writes.every(o => !o.to || !o.to.length);   // 31쪽 #5: 뒤 연결 없음 — 경고 대신 회색 표시
    return `<div class="pv-io" id="pv-io"><div><div class="pv-io-h">입력 ←</div>${reads.map(i => row(i, "←")).join("") || '<div class="muted">없음</div>'}</div>` +
      `<div><div class="pv-io-h">출력 →</div>${writes.map(o => row(o, "→")).join("") || '<div class="muted">없음(분기만)</div>'}</div></div>` +
      (next ? `<div class="pv-grey" id="pv-next-needed">다음 연결 필요 — 확정 때만 막습니다</div>` : "") + missBox;
  }
  function viewTab(c, d) {
    const rules = ((d.editable || []).concat(d.view_only || [])).filter(e => /^rules\[\d+\]\./.test(e.param))
      .map(e => { const m = /^rules\[(\d+)\]\.(\w+)$/.exec(e.param); return `<li data-param="${esc0(e.param)}"><span class="pv-k">규칙 ${Number(m[1]) + 1}</span> ${esc0(m[2] === "custom" ? e.value : `${m[2]}: ${ruleText(e.value)}`)}${e.reason ? ' <span class="pv-k">(Pack 함수 · 보기만)</span>' : ""}</li>`; }).join("");
    const runs = c.p.runs.filter(r => r.rel === c.rel.id && DONE.includes(r.status)), last = runs[runs.length - 1];
    const ex = d.expect.text || "", first = ex.split(/[,，.。—]/)[0].trim();   // EV-075 (v2 11쪽): the first clause, the whole on mouse-over
    return `<div class="pv-sec"><div class="pv-h">하는 일</div><div id="pv-expect">${!ex ? "적힌 설명 없음" : first && first.length < ex.length ? hv(first.length > 40 ? first.slice(0, 39) + "…" : first, ex) : esc0(ex)}</div></div>` +
      (rules ? `<div class="pv-sec"><div class="pv-h">규칙</div><ul id="pv-rules">${rules}</ul></div>` : "") +
      `<div class="pv-sec"><div class="pv-h">지난 실행</div><div id="pv-last-run">${last ? hv(`지난 평가: ${passLine(last, d).replace(/^(\d+) case 중 (\d+) (\S+)$/, "$1건 중 $2건 $3")}`, `${last.id} · ${passLine(last, d)}`) : "끝난 Run이 없습니다"}</div></div>` +
      `<div class="row small pv-actions">${last ? `<span class="btn" data-act="pv-tab" data-k="run" id="pv-to-run">실행 값 자세히</span>` : ""}${editHere(c) && !d.structure.function_node ? `<span class="btn" data-act="pv-delete" id="pv-delete">지우기</span>` : ""}${d.structure.function_node ? "" : `<span class="btn p" data-act="pv-tab" data-k="edit" id="pv-to-edit">고치기 ›</span>`}</div>`;
  }
  function ruleText(v) {   // 6쪽: "evidence: min_items 2, 출처 manual"
    if (!v || typeof v !== "object" || Array.isArray(v)) return typeof v === "string" ? v : JSON.stringify(v);
    return Object.entries(v).map(([k, x]) => k === "require_source_types" ? `출처 ${[].concat(x).join(" + ")}` : `${k} ${Array.isArray(x) ? x.join(" + ") : typeof x === "object" ? JSON.stringify(x) : x}`).join(", ");
  }
  function passLine(run, d) {   // 6쪽 "R-1 · 10 case 중 10 통과"
    const out = (d.writes[0] || {}).name;
    let n = 0, ok = 0;
    for (const cs of run.cases) {
      const vs = (cs.visits || []).filter(v => v.node === PV.node);
      if (!vs.length) continue;
      n++;
      const v = vs[vs.length - 1];
      if (d.kind_name === "validate") { const o = (v.output || {})[out]; if (o && o.passed === true) ok++; }
      else if (v.status === "done") ok++;
    }
    return n ? `${n} case 중 ${ok} ${d.kind_name === "validate" ? "통과" : "완료"}` : `이 node를 지난 case 없음(${run.cases.length} case)`;
  }
  function runTab(c, d, runs, run, ci) {
    if (!run) return `<div class="muted" id="pv-run-none">이 release의 끝난 Run이 없습니다 — 확정한 버전에서 실행하면 여기에 입력 · 출력이 보입니다</div>`;
    const pick = `<div class="row small"><select data-pv="run" id="pv-run">${runs.map(r => `<option value="${r.uid}"${r.uid === run.uid ? " selected" : ""}>${esc0(r.id)} · ${esc0(r.status)}</option>`).join("")}</select>` +
      `<select data-pv="case" id="pv-case">${run.cases.map((x, i) => `<option value="${i}"${i === ci ? " selected" : ""}>case ${i + 1}</option>`).join("")}</select></div>`;
    const rv = d.run_value;
    if (!rv || !rv.visited) return pick + `<div class="muted" id="pv-run-value" data-status="not-visited">이 case에서 ${esc0(PV.node)} node를 지나지 않았습니다</div>`;
    const iters = rv.iters > 1 ? `<div class="row small">${Array.from({ length: rv.iters }, (_, k) => `<span class="chip${k + 1 === rv.iter ? " on" : ""}" data-act="pv-iter" data-k="${k + 1}">회차 ${k + 1}</span>`).join("")}</div>` : "";
    return pick + iters + `<div class="kv" id="pv-run-value" data-status="${esc0(rv.status)}"><span class="muted">상태</span><span>${rv.status === "done" ? "완료" : esc0(rv.status)}${rv.ms == null ? "" : ` · ${(rv.ms / 1000).toFixed(2)}s`}</span>` +
      `<span class="muted">입력</span><span id="pv-run-in">${foldValue("in", rv.input)}</span><span class="muted">출력</span><span id="pv-run-out">${foldValue("out", rv.output)}</span>` +
      (rv.node_error ? `<span class="muted">오류</span><span>${esc0(JSON.stringify(rv.node_error))}</span>` : "") +
      (rv.predict ? `<span class="muted">예측</span><span id="pv-run-predict">${esc0(rv.predict.text)}</span>` : "") +   // EV-041 CHG-203: under 출력
      (rv.index ? `<span class="muted">벡터 DB</span><span id="pv-run-index">${esc0(rv.index.replace(/^벡터 DB /, ""))}</span>` : "") +   // EV-050 CHG-W2-04
      (rv.relations ? `<span class="muted">관계</span><span id="pv-run-relations">${esc0(rv.relations.text)}</span>` : "") + `</div>`;   // D-543 ⑥ ①
  }

  /* ---- 고치기: 옵션 표가 허용한 칸만(node 상세 API의 editable · form) */
  const cur = (d, prm) => { const e = (d.editable || []).find(x => x.param === prm); return e && e.value !== undefined && e.value !== "unset" ? e.value : undefined; };
  const val = (d, prm) => (prm in PV.form ? PV.form[prm] : cur(d, prm));
  const poolOk = (d, x) => Math.min(50, Math.max(5, Number(val(d, "top_k")) || 0, Math.round(x)));   // EV-072: 5 ~ 50 · 아래 끝 top_k
  function stepper(id, v, attrs) {
    const hint = (typeof v === "number" && !Number.isInteger(v) && v >= 0 && v <= 1) || /data-score/.test(attrs) ? ` <span class="muted">0 ~ 1 · 0.1씩</span>` : "";
    return `<span class="pv-step"><span class="btn" data-act="pv-step" data-d="-1" ${attrs}>−</span><input type="text" inputmode="decimal" data-pv-num ${attrs} id="${id}" value="${esc0(v ?? "")}"><span class="btn" data-act="pv-step" data-d="1" ${attrs}>＋</span></span>${hint}`;
  }
  function chips(list, on, attrs) { return list.map(([v, l0]) => { const l = l0 || VAL[v]; return `<span class="chip${on.includes(v) ? " on" : ""}" data-act="pv-chip" data-v="${esc0(v)}" ${attrs}>${on.includes(v) ? "☑ " : ""}${l ? `<span title="${esc0(v)}">${esc0(l)}</span>` : esc0(v)}</span>`; }).join(""); }
  function ruleField(d, i, k) {   // 7쪽: 규칙 값의 칸마다 − ＋ · 고르기
    const prm = `rules[${i}].${k}`, v = val(d, prm), at = `data-rule="${i}" data-rk="${esc0(k)}"`;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const known = RULE_KEYS[k] || {};
      return [...new Set([...Object.keys(known), ...Object.keys(v)])].map(sk => {
        const sv = v[sk], a2 = `${at} data-sk="${esc0(sk)}"`, kind = known[sk];
        let ctl;
        if (typeof sv === "number" || (sv === undefined && (kind === "int" || kind === "score"))) ctl = stepper(`pv-r${i}-${sk}`, sv, a2 + (kind === "score" ? ' data-score="1"' : ""));
        else if ((sv === undefined && kind === "list") || (Array.isArray(sv) && sv.every(x => typeof x === "string"))) {
          const listed = ((d.form || {}).enums || {})[sk] || [], on = sv || [], opts = [...new Set([...on, ...listed])].map(x => [x]);
          // 7쪽 · 사용자 결정(10-13 '프롬프트 외 모든 칸은 고르기만'): schema 목록이 있으면 칩으로만 고름 — 글로 더하는 칸은 목록이 없을 때만
          ctl = chips(opts, on, a2) + (sk === "require_source_types" && listed.length ? ` <span class="muted small">목록 = 근거 출처 종류(schema)</span>` : "") + (listed.length ? "" : `<input type="text" class="pv-add" data-pv-addval ${a2} placeholder="목록에 없는 값 추가(쉼표로 끝내기)" value="">`);
        } else if (typeof sv === "boolean") ctl = `<span class="chip${sv ? " on" : ""}" data-act="pv-bool" ${a2}>${sv ? "☑ 예" : "☐ 아니오"}</span>`;
        else ctl = `<textarea rows="2" data-pv-json ${a2}>${esc0(JSON.stringify(sv))}</textarea>`;
        return `<li data-param="${esc0(prm)}.${esc0(sk)}"><div class="pv-lb">규칙 ${i + 1} · ${lbl(sk)}</div>${ctl}</li>`;
      }).join("") + (PV.more ? `<li><span class="link small" data-act="pv-rule-del" data-i="${i}">규칙 ${i + 1} 지우기</span></li>` : "");
    }
    return `<li data-param="${esc0(prm)}"><div class="pv-lb">규칙 ${i + 1} · ${esc0(k)}</div><textarea rows="2" data-pv-json data-rule="${i}" data-rk="${esc0(k)}">${esc0(JSON.stringify(v))}</textarea></li>`;
  }
  function field(c, d, prm) {
    const f = d.form || {}, v = val(d, prm), at = `data-prm="${esc0(prm)}"`;
    let ctl;
    if (prm === "provider") ctl = chips(Object.keys(f.providers || {}).map(x => [x]), [v], at);
    else if (prm === "model") ctl = `<select data-pv="param" ${at} id="pv-f-model">${((f.providers || {})[val(d, "provider")] || []).map(x => `<option${x === v ? " selected" : ""}>${esc0(x)}</option>`).join("")}</select>`;
    else if (prm === "search") ctl = chips(SEARCH, [v], at);
    else if (prm === "method") ctl = chips(((f.choices || {}).method || []).map(x => [x]), [v], at);   // D-492 덧 28: retrieve@2 찾는 방식
    else if (prm === "fuse") {   // 섞기 — RRF k만(찾는 방식이 hybrid일 때), 나머지 칸은 그대로
      if (val(d, "method") !== "hybrid") return "";
      ctl = `<div class="pv-sub"><span class="pv-k">${LABEL.k}</span> ${stepper("pv-f-fuse-k", (v || {}).k ?? 60, `${at} data-sk="k"`)}</div>`;
    }
    else if (prm === "index") {   // 벡터 DB 나누기 — 청크 크기 100씩 · 오버랩 50씩(청크 크기가 있을 때만)
      const o = v && typeof v === "object" ? v : {};
      ctl = `<div class="pv-sub"><span class="pv-k">${LABEL.chunk_size}</span> ${stepper("pv-f-index-chunk", o.chunk_size ?? "", `${at} data-sk="chunk_size" data-by="100"`)}${o.chunk_size == null ? ` <span class="muted small" id="pv-index-whole">섹션 그대로</span>` : ""}</div>` +
        (o.chunk_size == null ? "" : `<div class="pv-sub"><span class="pv-k">${LABEL.overlap}</span> ${stepper("pv-f-index-overlap", o.overlap ?? 0, `${at} data-sk="overlap" data-by="50"`)} <span class="muted small" id="pv-overlap-range">0 ~ ${Math.max(0, o.chunk_size - 1)}</span></div>`);
    }
    else if (/^option\./.test(prm)) {   // D-492 덧 28: a Pack-declared option (split) — its own choices, set_option
      const it = (d.editable || []).find(x => x.param === prm) || {};
      ctl = chips((it.choices || []).map(x => [x]), [v], at);
    }
    else if (prm === "on_fail" || prm === "default") ctl = `<select data-pv="param" ${at} id="pv-f-${prm}"><option value=""${v ? "" : " selected"}>—</option>${(f.nodes || []).map(x => `<option${x === v ? " selected" : ""}>${esc0(x)}</option>`).join("")}</select>`;
    else if (prm === "prompt" || prm === "template") return fileField(c, d, prm, v);
    else if (prm === "rerank" && v && typeof v === "object") {   // EV-072 (D-446 ③ · 덧 2): 재정렬 개수 — 걸음 1 · 숫자 직접 · 5 ~ 50 · 아래 끝 top_k / 가중치는 보기만
      const tk = Number(val(d, "top_k")) || 0, w = v.weights || {};
      ctl = `<div class="pv-sub"><span class="pv-k">${LABEL.pool}</span> ${stepper("pv-f-rerank-pool", v.pool ?? tk, `${at} data-sk="pool" data-pool`)} <span class="muted small" id="pv-pool-range">${Math.max(5, tk)} ~ 50${v.pool == null ? " · 기본 = 가져올 개수" : ""}</span></div>` +
        `<div class="pv-sub" id="pv-rerank-weights"><span class="pv-k">가중치</span> 리트리버 ${esc0(w.retrieval ?? "—")} · 센서 ${esc0(w.sensor ?? "—")}</div>`;
    }
    else if (typeof v === "number" || ["top_k", "temperature", "max_tokens", "score_threshold", "limit"].includes(prm)) {
      const lim = prm === "top_k" ? (f.limits || {}).top_k : null;   // D-492 덧 28 (013-66): the Pack's output promise is the upper end
      ctl = stepper(`pv-f-${prm}`, v, at) + (lim ? ` <span class="muted small" id="pv-topk-range">1 ~ ${esc0(lim.max)} · ${esc0(lim.from)}</span>` : "");
    }
    else if (v && typeof v === "object" && !Array.isArray(v) && Object.values(v).every(x => typeof x === "number" || (Array.isArray(x) && x.every(y => typeof y === "string")))) {
      ctl = Object.keys(v).map(sk => { const a2 = `${at} data-sk="${esc0(sk)}"`, sv = v[sk];   // 10쪽 fallback: 칸마다 − ＋ · 칩
        return `<div class="pv-sub"><span class="pv-k">${LABEL[sk] || esc0(sk)}</span> ${typeof sv === "number" ? stepper(`pv-f-${prm}-${sk}`, sv, a2) + (sk === "min_results" ? " 건" : "") : chips([...new Set([...sv, ...(((f.enums || {})[sk]) || [])])].map(x => [x]), sv, a2)}</div>`; }).join("");
    }
    else if (v !== undefined && typeof v === "object") ctl = `<textarea rows="2" data-pv-json ${at}>${esc0(JSON.stringify(v))}</textarea>`;
    else ctl = `<input type="text" data-pv="param" ${at} value="${esc0(v ?? "")}">`;
    const item = (d.editable || []).find(x => x.param === prm) || {};
    const head = /^option\./.test(prm) ? `<span title="${esc0(prm.slice(7))}">${esc0(item.label || prm.slice(7))}</span>` : lbl(prm);
    const badge = item.rebuild ? ` <span class="pv-badge" data-rebuild="1">벡터 DB 다시 만듦</span>` : "";   // D-492 덧 28
    return `<li data-param="${esc0(prm)}"><div class="pv-lb">${head}${badge}</div>${ctl}</li>`;
  }
  function fileField(c, d, prm, v) {   // 13쪽: 프롬프트 파일 고르기 + 내용 + 미리 넣어 둔 묶음
    const f = d.form || {}, list = (f.files || {})[prm + "s"] || [], txt = v in PV.files ? PV.files[v] : ((f.file_text || {})[v] ?? "");
    const big = (f.too_large || []).includes(v) || !(v in (f.file_text || {}));
    let lib = "";
    if (prm === "prompt") {
      const r = load("prompts", c.p.id, `${base(c)}/prompt-library`, c), items = (r.data && r.data.items) || [];
      const note = r.err ? `<div class="pv-errline" id="pv-lib-error">프롬프트 묶음을 읽지 못함 — ${esc0(r.err)}</div>`   // a 422 is not '없음' (review E-S-06 N3)
        : (r.data && r.data.note) || (r.data && !items.length) ? `<div class="pv-grey" id="pv-lib-none">${esc0((r.data && r.data.note) || "묶음 없음 — task10-pack 준비 중")}</div>` : "";
      lib = `<div class="pv-lb">미리 넣어 둔 것에서 고름</div><div id="pv-lib">${items.map(it => `<span class="chip${PV.pick && PV.pick.path === it.path ? " on" : ""}" data-act="pv-lib" data-path="${esc0(it.path)}" title="reads ${esc0((it.reads || []).join(", "))} → writes ${esc0((it.writes || []).join(", "))} · ${esc0(it.output || "")}">${esc0(it.name)}</span>`).join("")}</div>${note}`;
    }
    const picked = PV.pick ? `<div class="small" id="pv-lib-picked">새 파일로 넣음: <b>${esc0(libPath(PV.pick))}</b></div><pre class="pv-pre">${esc0(PV.pick.text || "")}</pre>` : "";
    return `<li data-param="${esc0(prm)}"><div class="pv-lb">${lbl(prm)} <span class="muted">실행 때 모델에게 보내는 지시</span></div>` +
      ((sel) => `<select data-pv="param" data-prm="${esc0(prm)}" id="pv-f-${prm}">${[...new Set([sel, v, ...list].filter(Boolean))].map(x => `<option${x === sel ? " selected" : ""}>${esc0(x)}</option>`).join("")}</select>`)(PV.pick ? libPath(PV.pick) : v) +   // 13쪽: 고른 묶음 파일이 프롬프트 파일 칸에
      (PV.pick ? picked : big ? `<div class="muted" id="pv-file-big">파일이 커서 여기서 고칠 수 없습니다</div>` : `<textarea rows="5" data-pv-file="${esc0(v)}" id="pv-file">${esc0(txt)}</textarea>` +
        `<div class="small" id="pv-insert"><span class="pv-k">값 넣기</span> ${(d.reads || []).map(x => `<span class="chip" data-act="pv-insert" data-file="${esc0(v)}" data-key="${esc0(x.name)}">{${esc0(x.name)}}</span>`).join("")}</div>` +
        `<div class="muted small">다른 node가 같은 파일을 쓰면 함께 바뀝니다 — 이 node만 바꾸려면 묶음에서 새 파일로 고르세요</div>`) +
      lib + `</li>`;
  }
  const libPath = it => `prompts/lib/${String(it.path || it.name).split("/").pop()}`;
  function opsOf(c, d) {
    const graph = d.graph, node = PV.node, ops = [], f = d.form || {};
    for (const prm of Object.keys(PV.form)) {
      if (prm === "reads") continue;   // a new node's reads: below
      let v = PV.form[prm];
      if (typeof v === "string" && /^rules\[/.test(prm) === false && ["top_k", "temperature", "max_tokens", "score_threshold", "limit"].includes(prm)) v = Number(v);
      const m = /^rules\[(\d+)\]\.(\w+)$/.exec(prm);
      if (m && PV.removed.includes(Number(m[1]))) continue;
      if (same(v, cur(d, prm))) continue;
      if (/^option\./.test(prm)) {   // D-492 덧 28: a declared option — one value in its graph (packrt set_option)
        const it = (d.editable || []).find(x => x.param === prm) || {};
        ops.push({ op: "set_option", graph: it.graph || graph, node, option: prm.slice(7), value: v });
        continue;
      }
      if (v === "" || v === undefined) ops.push({ op: "unset", graph, node, param: prm });
      else ops.push({ op: "set", graph, node, param: prm, value: v });
    }
    for (const [path, text] of Object.entries(PV.files)) if (text !== (f.file_text || {})[path] && !PV.pick) ops.push({ op: "write_file", path, text });
    if (PV.pick) {   // api-contract E-S-11: 새 파일 + prompt 설정을 한 묶음으로
      const path = libPath(PV.pick), exists = (((f.files || {}).prompts) || []).includes(path);
      if (!exists) ops.push({ op: "create_file", path, text: PV.pick.text || "" });
      ops.push({ op: "set", graph, node, param: "prompt", value: path });
    }
    if (PV.form.reads && !same(PV.form.reads, (d.reads || []).map(x => x.name))) ops.unshift({ op: "set", graph, node, param: "reads", value: PV.form.reads });   // 13쪽 ③(새 node만)
    [...PV.removed].sort((a, b) => b - a).forEach(i => ops.push({ op: "remove_rule", graph, node, index: i }));
    PV.added.forEach(a => ops.push({ op: "add_rule", graph, node, rule: { [a.kind]: a.value } }));
    if (PV.idErr) throw new Error(`이름 (id): ${PV.idErr.short}`);   // 32쪽 #2: 반영 잠김
    if (PV.idText && PV.idText !== node) ops.push({ op: "set", graph, node, param: "id", value: PV.idText });   // 이름은 마지막에(앞 줄은 지금 이름으로)
    return ops;
  }
  function previewBox() {
    if (PV.busy) return `<div class="muted" id="pv-busy">검사하는 중…</div>`;
    if (PV.err) return `<div class="rg err small" id="pv-error">${esc0(PV.err)}</div>`;
    const r = PV.preview;
    if (!r) return PV.msg ? `<div class="small" id="pv-msg">${esc0(PV.msg)}</div>` : "";
    const files = (r.changed_files || []).map(fl => fl.hunks.map(h => `<div class="pv-hunk" data-file="${esc0(fl.path)}" data-line="${h.old_start}"><span class="muted">${esc0(fl.path)} ${h.old_start}행</span>${h.old.map(l => `<div class="pv-del">- ${esc0(l)}</div>`).join("")}${h.new.map(l => `<div class="pv-addl">+ ${esc0(l)}</div>`).join("")}</div>`).join("")).join("");
    const where = (r.changed_files || []).map(fl => `${fl.path} ${fl.hunks.map(h => `${h.old_start}행`).join(" · ")} ${fl.hunks.reduce((s, h) => s + Math.max(h.old.length, h.new.length), 0)}줄`).join(" · ");
    const inEdit = PV.ctx && editHere(PV.ctx), lines = inEdit ? (PV.ctx.p.edit.lines || 0) + 1 : 0;
    const errs = (r.errors || []).map(e => `<li data-stage="${esc0(e.stage)}" data-line="${e.line ?? ""}">${e.line ? `pack.yaml:${e.line} · ` : ""}${esc0(e.message)}</li>`).join("");
    const waits = (r.pending || []).length ? `<b>반영할 수 있음</b> · 다음 연결 필요 — ${esc0(r.pending.join(" · "))} (확정 때만 막힘)` : "";   // 14쪽: 기다리는 node가 있어도 반영은 됨
    return `<div class="rg${r.ok ? "" : " err"} small" id="pv-preview" data-ok="${r.ok}"><div id="pv-preview-head">${r.ok ? (waits || (inEdit ? `<b>검사 통과</b> · 흐름 검사 · 편집본 revision 다시 계산(변경 ${lines}개 기준) · ${short(r.revision)}` : `<b>검사 통과</b> · 0~2단계 + 흐름 검사 · revision ${short(r.revision)}`)) + (where ? ` · 바뀐 곳 ${esc0(where)}` : "") : "<b>반영할 수 없음</b> — 검사에서 막혔습니다"}</div>` +
      (r.ok && (r.index_rebuild || []).length ? `<div id="pv-rebuild">벡터 DB 다시 만들기 필요 — ${esc0(r.index_rebuild.join(" · "))} · 확정 때 만듦</div>` : "") +   // D-492 덧 28
      (errs ? `<ul id="pv-preview-errors">${errs}</ul>` : "") + files + `</div>`;
  }
  const isNewNode = c => editHere(c) && !((c.rel.graph && c.rel.graph.nodes) || []).some(n => n.name === PV.node);
  // 13쪽 ③ 읽을 값(새 node만 — 받을 수 있는 값만 칩으로) · ④ 같은 값을 쓰는 곳이 여럿이면 물어봄
  function newNodeParts(c, d) {
    if (!isNewNode(c)) return "";
    const x = (PV.ioRes && PV.ioRes.data) || null, lab = k => { const l = ((x && x.labels) || {})[k]; return l && l.label && l.label !== k ? `${esc0(l.label)} <span class="pv-k">${esc0(k)}</span>` : esc0(k); };
    const curR = PV.form.reads || (d.reads || []).map(r => r.name);
    const chipsR = ((x && x.readable) || curR).map(k => `<span class="chip${curR.includes(k) ? " on" : ""}" data-act="pv-read" data-k="${esc0(k)}">${curR.includes(k) ? "☑ " : ""}${lab(k)}</span>`).join("");
    const asks = ((x && x.reads) || []).map(r => ({ r, others: (r.writers || []).filter(w => !(r.from || []).includes(w)) })).filter(a => a.others.length && !(PV.joinSkip || {})[a.r.key])
      .map(({ r, others }) => `<div class="pv-confirm" data-join-ask="${esc0(r.key)}"><b>"${lab(r.key)}"이 ${others.length + (r.from || []).length}곳에서 옴</b><div class="small">${others.map(esc0).join(" · ")} 길도 이을까요?</div>` +
        `<div class="row small"><span class="btn p" data-act="pv-join-both" data-k="${esc0(r.key)}" data-from="${esc0(others.join(","))}" id="pv-join-both">둘 다 연결</span><span class="btn" data-act="pv-join-skip" data-k="${esc0(r.key)}">${esc0((r.from || []).join(" · ") || "지금")} 길만</span></div></div>`).join("");
    // 32쪽 #2: 이름은 제안 · 바꿀 수 있음(14쪽) — 칸을 벗어날 때 검사, 겹치거나 규칙에 안 맞으면 칸 빨강 · 이유 · 반영 잠김
    const idv = PV.idText ?? PV.node, idRow = `<li data-param="id" id="pv-id-row"><div class="pv-lb">이름 (id)</div><input type="text" id="pv-id" value="${esc0(idv)}"${PV.idErr ? ' style="border-color:#d92d20;background:#fef3f2"' : ""}>` +
      (PV.idErr ? `<div class="pv-errline" id="pv-id-err">✕ ${esc0(PV.idErr.short)}</div><div class="muted small">${esc0(PV.idErr.why)} — 제안 이름으로 되돌리기 / 다른 이름</div>` : "") + `</li>`;
    return idRow + `<li data-param="reads" id="pv-reads"><div class="pv-lb">읽을 값 <span class="pv-k">받을 수 있는 값만</span></div>${chipsR}</li>` + (asks ? `<li id="pv-join-asks">${asks}</li>` : "");
  }
  function editTab(c, d) {
    if (d.structure.function_node) {   // 34쪽 #13
      return `<div class="pv-lock" id="pv-fn-lock">🔒 <b>${esc0(PV.node)}</b> ${esc0(d.kind)} · Pack 함수<div class="row small"><span class="chip off">고치기</span><span class="chip off">삭제</span><span class="chip on" title="편집 중 그래프에서 연결을 끌어 바꿉니다" id="pv-fn-move">연결 위치 바꾸기</span></div>` +
        `<div class="pv-errline">함수 node '${esc0(PV.node)}'는 삭제할 수 없습니다(연결 위치만 바꿀 수 있습니다)</div><div class="muted">코드 변경은 Pack 저장소에서 합니다</div></div>`;
    }
    const st = vstate(c.rel), name = c.rel.name;
    if (editElse(c)) return `<div class="rg err small" id="pv-edit-elsewhere">편집 중인 것이 있습니다(${esc0(relName(c, c.p.edit.release))}에서) · 확정하거나 버린 뒤 ${esc0(name)}을 고칠 수 있습니다</div>`;
    if (!editHere(c) && FIXED_VIEW_ONLY.includes(st) && !PV.start) {   // 34쪽 #16
      return `<div class="pv-lock" id="pv-fixed"><b>${esc0(name)} · ${esc0(st)} — 고칠 수 없습니다</b><div class="muted">점 · ＋가 나타나지 않고 팝오버는 보기만</div>` +
        `<div class="row small"><span class="btn p" data-act="pv-start" id="pv-start">이 버전에서 새 편집</span></div><div class="muted small">배포 버전에서 시작한 편집은 확정 때 반드시 새 버전</div></div>`;
    }
    const note = editHere(c) ? hv("편집 중에 반영", "옵션 표가 허용한 칸만 · 모두 고르기 · 숫자는 − ＋ · 편집 중에 반영됩니다")   // EV-075 (v2 12쪽)
      : st === "운영" ? hv("운영 버전은 그대로 · 반영하면 편집 시작", `옵션 표가 허용한 칸만 · 모두 고르기 · 숫자는 − ＋ · 운영 ${name}는 바뀌지 않습니다 — 반영하면 편집이 시작됩니다`)
      : hv(`${name}는 그대로 · 반영하면 편집 시작`, `옵션 표가 허용한 칸만 · 모두 고르기 · 숫자는 − ＋ · ${name} 자체는 그대로 — 반영하면 이 버전에서 편집이 시작됩니다`);
    const ed = d.editable || [];
    const ruleItems = ed.filter(e => /^rules\[\d+\]\./.test(e.param) && !PV.removed.includes(Number(/\[(\d+)\]/.exec(e.param)[1])))
      .map(e => { const m = /^rules\[(\d+)\]\.(\w+)$/.exec(e.param); return ruleField(d, Number(m[1]), m[2]); }).join("");
    const viewRules = (d.view_only || []).filter(e => /^rules\[\d+\]\./.test(e.param)).map(e => `<li data-param="${esc0(e.param)}"><div class="pv-lb">규칙 ${Number(/\[(\d+)\]/.exec(e.param)[1]) + 1} · ${esc0(e.param.split(".").pop() === "custom" ? e.value : `${e.param.split(".").pop()}: ${e.value}`)}</div><div class="pv-grey">Pack 함수 규칙 — 보기만</div></li>`).join("");
    const addRow = ed.some(e => e.param === "rules.add_remove") ? `<li data-param="rules.add_remove"><div class="pv-lb">기본 규칙 추가</div><select data-pv="addkind" id="pv-addkind">${RULES.map(k => `<option${k === PV.addKind ? " selected" : ""}>${k}</option>`).join("")}</select>` +
      `<input type="text" data-pv="addtext" id="pv-addtext" value="${esc0(PV.addText)}" placeholder='값(JSON), 예: {"min_items": 1}'><span class="btn" data-act="pv-rule-add">추가</span>` +
      PV.added.map((a, j) => `<div class="small">새 규칙 ${esc0(a.kind)} ${esc0(JSON.stringify(a.value))} <span class="link" data-act="pv-rule-unadd" data-i="${j}">빼기</span></div>`).join("") + `</li>` : "";
    const first = PRIMARY[d.kind_name] || null, isFirst = prm => !first || first.includes(prm) || /^option\./.test(prm);
    const others = ed.filter(e => !/^rules/.test(e.param) && isFirst(e.param)).map(e => field(c, d, e.param)).join("");
    const rest = ed.filter(e => !/^rules/.test(e.param) && !isFirst(e.param));
    const moreN = rest.length + (addRow ? 1 : 0);
    const more = moreN ? `<li class="pv-more-row"><span class="link small" data-act="pv-more" id="pv-more">${PV.more ? "옵션 표의 다른 칸 접기" : `옵션 표의 다른 칸 ${moreN}개 ›`}</span></li>` +
      (PV.more ? addRow + rest.map(e => field(c, d, e.param)).join("") : "") : "";
    const ok = PV.preview && PV.preview.ok && PV.previewOps === JSON.stringify(safeOps(c, d));
    return `<div class="muted small">${note}</div><ul class="pv-form" id="pv-form">${newNodeParts(c, d)}${ruleItems}${viewRules}${others}${more}</ul>` + previewBox() +
      `<div class="row small pv-actions"><span class="btn" data-act="pv-reset" id="pv-reset">되돌리기</span>` +
      (ok ? `<span class="btn p${PV.busy || PV.idErr ? " off" : ""}" data-act="pv-apply" id="pv-apply">편집에 반영</span>` : `<span class="btn p${PV.busy || PV.idErr ? " off" : ""}" data-act="pv-preview" id="pv-preview-btn">미리보기</span>`) + `</div>`;
  }
  // EV-075 R3 (v2 13쪽): a run value folds — the summary line names what is in it (state names in plain words), the value opens on a click
  function foldValue(side, v) {
    const labels = ((PV.ioRes || {}).data || {}).labels || {}, name = k => (labels[k] && labels[k].label) || k;
    const sum = v && typeof v === "object" && !Array.isArray(v) ? Object.keys(v).map(name).join(" · ") || "비어 있음" : Array.isArray(v) ? `목록 ${v.length}개` : String(v ?? "—");
    return typeof v === "object" && v !== null ? fold(`pv-${side}-${PV.node}`, sum, fold.pre(v)) : esc0(sum);
  }
  function safeOps(c, d) { try { return opsOf(c, d); } catch (e) { return null; } }
  /* ---------------------------------------------------------------- EV-073 ✦ AI 탭 · 빈 node(설명서 35 · 36쪽, D-440 · D-443 · D-447) */
  const blankOf = c => { const e = editData(c); return (((e && e.lines) || []).find(l => l.kind === "blank" && l.node === PV.node) || {}).blank || null; };
  const shortVal = v => { const x = typeof v === "string" ? v : JSON.stringify(v); return x.length > 40 ? x.slice(0, 39) + "…" : x; };
  function aiState(b) {
    if (!PV.ai || PV.ai.node !== PV.node) PV.ai = { node: PV.node, ask: b ? b.expect.text : "", busy: false, data: null, err: null, out: null };
    return PV.ai;
  }
  function aiCard(c, A) {
    const d = A.data, p = d && d.proposal, n = p && p.node;
    if (!d) return "";
    if (!p) return `<div class="pv-grey" id="pv-ai-none">제안 없음${(d.dropped || []).map(x => `<div class="small">${esc0(x)}</div>`).join("")}</div>`;
    const vals = p.ops.filter(o => o.op === "set").map(o => `${lbl(o.param)} ${esc0(shortVal(o.value))}`);
    const params = vals.length ? vals : Object.entries(n.params || {}).map(([k, v]) => `${lbl(k)} ${esc0(shortVal(v))}`);
    const ck = d.check || {}, errs = (ck.errors || []).filter(x => x.stage !== "blank");
    const okay = !!d.check && !errs.length;
    const outs = (d.out_choices || []).map(o => `<span class="chip${A.out === o ? " on" : ""}" data-act="pv-ai-out" data-k="${esc0(o)}" data-ai-out="${esc0(o)}">→ ${esc0(o)}</span>`).join(" ");
    return `<div class="pv-ai-card" id="pv-ai-card" data-ok="${okay}"><div><span class="pv-ai-tag">AI 제안</span> <b>${esc0(n.id)}</b> <span class="muted">${esc0(kindWord(n.kind))} · ${esc0(n.kind)}</span>` +
      `${p.ops.some(o => o.op === "remove_node") ? ' <span class="chip">바꿔 끼움</span>' : ""}</div>` +
      `<div class="small" id="pv-ai-io">받음 ${esc0((n.reads || []).join(" · ") || "—")} → 넘김 ${esc0((n.writes || []).join(" · ") || "—")}</div>` +
      `<div class="small" id="pv-ai-params">${params.join(" · ")}</div>` +
      (d.why ? `<div class="small" id="pv-ai-why">이유 · ${esc0(d.why)}</div>` : "") +
      (outs ? `<div class="small" id="pv-ai-outs">출력 연결 ${outs}</div>` : "") +
      `<div class="small ${okay ? "pv-ai-ok" : "pv-errline"}" id="pv-ai-check">${okay ? "검사 통과" : `검사 문제 ${errs.length}`}</div>` + errs.map(x => `<div class="pv-errline small">${esc0(x.message)}</div>`).join("") +
      ((ck.pending || []).length ? `<div class="small muted" id="pv-ai-pending">다음 연결 필요 · ${esc0(ck.pending.join(" · "))}</div>` : "") +
      (d.fixed_flow || []).map(x => `<div class="pv-errline small" data-ai-fixed>고정 흐름 닿음 · ${esc0(x.what || "")}</div>`).join("") +
      (d.dropped || []).map(x => `<div class="small muted">${esc0(x)}</div>`).join("") +
      `<div class="small muted" id="pv-ai-meta">${esc0(d.model)} · ${d.mode === "fake" ? "가짜" : `$${Number(d.usd || 0).toFixed(4)}`}</div>` +
      `<div class="row small pv-actions"><span class="btn${okay && !A.busy ? "" : " off"}" data-act="pv-ai-open" id="pv-ai-open">고치기로 열기</span><span class="btn p${okay && !A.busy ? "" : " off"}" data-act="pv-ai-apply" id="pv-ai-apply">편집에 반영</span></div></div>`;
  }
  function aiTab(c) {
    if (editElse(c)) return `<div class="pv-grey" id="pv-ai-note">편집 중인 것이 있습니다(${esc0(relName(c, c.p.edit.release))}에서)</div>`;
    if (!editHere(c) && FIXED_VIEW_ONLY.includes(vstate(c.rel))) return `<div class="pv-grey" id="pv-ai-note">보기만</div>`;
    const b = blankOf(c), A = aiState(b);
    return `<ul class="pv-form"><li><textarea id="pv-ai-ask" data-pv="ai-ask" rows="3" placeholder="이렇게 바꿔줘">${esc0(A.ask)}</textarea></li></ul>` +
      `<div class="row small pv-actions"><span class="btn p${A.busy ? " off" : ""}" data-act="pv-ai-go" id="pv-ai-go">${A.busy ? "제안 받는 중…" : A.data ? "다시 제안" : "제안 받기"}</span></div>` +
      (A.err ? `<div class="rg err small" id="pv-ai-error">${esc0(A.err)}</div>` : "") + aiCard(c, A);
  }
  function blankHtml(c, b, pos) {
    const tab = ["role", "edit", "ai"].includes(PV.tab) ? PV.tab : "role";
    const tabs = [["role", "역할"], ["edit", "고치기"], ["ai", "✦ AI"]].map(([k, l]) => `<span class="pv-tab${k === "ai" ? " pv-ai" : ""}${tab === k ? " on" : ""}" data-act="pv-tab" data-k="${k}" id="pv-tab-${k}">${l}</span>`).join("");
    const kinds = ((typeof Canvas !== "undefined" && Canvas.KINDS) || []).map(k => `<div class="cv-kind" data-act="pv-blank-kind" data-k="${k.kind}" data-kind-row="${k.kind}"><b>${esc0(k.name)}</b><code>${k.kind}</code></div>`).join("");
    const body = tab === "ai" ? aiTab(c) : tab === "edit" ? `<div id="pv-blank-kinds">${kinds}</div>` + (PV.err ? `<div class="rg err small">${esc0(PV.err)}</div>` : "")
      : `<ul class="pv-form"><li><textarea id="pv-role" data-pv="role" rows="3" placeholder="역할">${esc0(PV.role ?? b.expect.text)}</textarea></li></ul>` +
        `<div class="row small pv-actions"><span class="btn" data-act="pv-delete" id="pv-delete">지우기</span><span class="btn p" data-act="pv-role-save" id="pv-role-save">저장</span></div>` + (PV.err ? `<div class="rg err small">${esc0(PV.err)}</div>` : "");
    requestAnimationFrame(place);
    return `<div class="pv-pop" id="node-pop" data-node="${esc0(PV.node)}" data-tab="${tab}" data-blank="1" style="${pos}">` +
      `<div class="pv-head"><b>${esc0(PV.node)}</b> <span class="pv-io-miss">종류 미정</span><span class="sp"></span><span class="pv-x" data-act="pv-close" id="pv-close">✕</span></div>` +
      `<div class="pv-io"><div><div class="pv-io-h">입력</div><div class="pv-io-row">${esc0(b.from.join(" · ") || "—")}</div></div><div><div class="pv-io-h">출력</div><div class="pv-io-row">${esc0(b.to.join(" · ") || "—")}</div></div></div>` +
      `<div class="pv-tabs">${tabs}</div><div class="pv-body" id="pv-body">${body}</div></div>`;
  }
  function html(c) {
    PV.ctx = c;
    if (PV.relId !== (c.rel && c.rel.id)) { PV.relId = c.rel && c.rel.id; PV.start = false; PV.pos = null; }   // another version tab: its own lock (#16) and place (review E-S-06 N5)
    if (!isPack(c) || !PV.node || !c.ui || c.ui.node !== PV.node) return "";
    const eg = editHere(c) && PV.edit && PV.edit.data && PV.edit.data.graph;   // v1.2: 편집본 그래프(새 node 포함)
    const node = ((eg || c.rel.graph).nodes || []).find(n => n.name === PV.node) || (c.rel.graph.nodes || []).find(n => n.name === PV.node);
    if (!node) return "";
    if (c.ui.popTab) { PV.tab = c.ui.popTab; c.ui.popTab = null; PV.pos = null; }   // v1.2: 18쪽 '갈 조건 고치기'가 고치기 탭으로 엶
    const blank = node.blank && blankOf(c);
    if (blank) return blankHtml(c, blank, PV.pos ? `left:${PV.pos[0]}px;top:${PV.pos[1]}px` : "left:-9999px;top:0");
    const { st, runs, run, ci } = detail(c), d = st.data;
    const pos = PV.pos ? `left:${PV.pos[0]}px;top:${PV.pos[1]}px` : "left:-9999px;top:0";
    const tabs = [["view", "보기"], ["edit", "고치기"], ["run", "실행 값"]].map(([k, l]) => `<span class="pv-tab${PV.tab === k ? " on" : ""}" data-act="pv-tab" data-k="${k}" id="pv-tab-${k}">${l}</span>`).join("") +
      `<span class="pv-tab pv-ai${PV.tab === "ai" ? " on" : ""}" data-act="pv-tab" data-k="ai" id="pv-tab-ai">✦ AI</span>`;
    let body;
    if (st.err) body = `<div class="rg err small" id="pv-detail-error"><b>node 상세를 읽지 못함</b> — ${esc0(st.err)}</div>`;
    else if (!d) body = `<div class="muted" data-loading>node 상세를 읽는 중…</div>`;
    else body = PV.tab === "edit" ? editTab(c, d) : PV.tab === "run" ? runTab(c, d, runs, run, ci) : PV.tab === "ai" ? aiTab(c) : viewTab(c, d);
    requestAnimationFrame(place);
    return `<div class="pv-pop" id="node-pop" data-node="${esc0(PV.node)}" data-tab="${PV.tab}" style="${pos}">` +
      `<div class="pv-head"><b>${esc0(PV.node)}</b> <span class="muted">${hv(kindWord(node.kind) || node.kind || "", `${kindWord(node.kind) ? `${kindWord(node.kind)} · ` : ""}${node.kind || ""}${d ? ` · ${d.loc}` : ""}`)}${eg && !(c.rel.graph.nodes || []).some(n => n.name === PV.node) ? " · 새 node" : ""}</span><span class="sp"></span><span class="pv-x" data-act="pv-close" id="pv-close">✕</span></div>` +
      (d ? ioStrip(c, d) : "") + `<div class="pv-tabs">${tabs}</div><div class="pv-body" id="pv-body">${body}</div></div>`;
  }
  function place() {
    const c = PV.ctx, pop = document.getElementById("node-pop");
    if (!c || !pop || !PV.node) return;
    const el = [...document.querySelectorAll("[data-node]")].find(x => x !== pop && x.getAttribute("data-node") === PV.node && x.closest("svg"));
    if (!el) return;
    const r = el.getBoundingClientRect(), w = pop.offsetWidth || 380, h = pop.offsetHeight || 420;
    let left = r.right + 12;
    if (left + w > window.innerWidth - 8) left = Math.max(8, r.left - w - 12);
    const top = Math.max(8, Math.min(r.top - 24, window.innerHeight - h - 8));
    const pos = [Math.round(left), Math.round(top)];
    if (!PV.pos || PV.pos[0] !== pos[0] || PV.pos[1] !== pos[1]) { PV.pos = pos; pop.style.left = pos[0] + "px"; pop.style.top = pos[1] + "px"; c.render(); }
  }

  /* ---------------------------------------------------------------- 편집 띠(9쪽) */
  function editBand(c) {
    PV.ctx = c;
    if (!isPack(c) || !editHere(c)) return "";
    const e = editData(c), n = (c.p.edit || {}).lines ?? (e ? e.lines.length : 0);
    const chk = e ? e.check : { ok: (c.p.edit || {}).ok, errors: [] };
    const errs = (chk && chk.errors && chk.errors.length ? chk.errors : ((c.p.edit || {}).problems || []).map(m => ({ message: m }))) || [];
    const pending = (chk && chk.pending) || [];
    const confirmOff = !chk || !chk.ok;
    const view = c.ui.edView === "original";
    const problems = errs.length ? `<div class="pv-problems" id="pv-problems"><b>문제 ${errs.length}</b>${errs.map(x => `<div class="pv-errline" data-path="${esc0(x.path || "")}">${x.line ? `pack.yaml:${x.line} · ` : ""}${esc0(x.message)}</div>`).join("")}</div>` : "";
    const discard = PV.discard ? `<div class="pv-confirm" id="pv-discard-box"><b>편집을 버릴까요?</b> 변경 ${n}개가 모두 사라집니다. ${esc0(c.rel.name)}는 그대로입니다.` +
      `<div class="row small"><span class="btn" data-act="pv-discard-no">취소</span><span class="btn p" data-act="pv-discard-ok" id="pv-discard-ok">버리기</span></div></div>` : "";
    return `<div class="pv-band" id="edit-band" data-lines="${n}" data-ok="${!confirmOff}"><b>${esc0(c.rel.name)} · 편집 중</b> <span class="muted">${hv(`변경 ${n}개 · ${confirmOff ? "검사 문제 있음" : "검사 통과"}`, `${vstate(c.rel) === "운영" ? `운영 ${c.rel.name}는 그대로` : `${c.rel.name}는 그대로`}(편집본 따로 저장) · 변경 ${n}개 · ${confirmOff ? "검사 문제 있음" : "검사 통과"} · 새 버전은 확정 때`, "edit-band-sum")}</span>` +   // EV-075 EDIT
      `<span class="btn" data-act="pv-view" id="pv-view-toggle">${view ? "편집본 보기" : "원본 보기"}</span>` +
      `<span class="btn" data-act="go" data-to="#/p/${c.p.id}/r/${c.rel.id}/edit/diff" id="pv-diff">변경 내용(diff)</span>` +
      `<span class="btn" data-act="pv-discard" id="pv-discard">편집 버리기</span>` +
      `<span class="btn pv-soon-btn" data-act="pv-soon" id="pv-auto-rec">✦ 자동 추천 <span class="pv-soon">준비 중</span></span>` +   // EV-073 CHG-AI-10 (D-441): 자리만
      `<span class="btn p${confirmOff ? " off" : ""}" data-act="pv-confirm" id="pv-confirm"${confirmOff ? ` title="검사 문제나 코드로 못 바꾸는 항목이 있으면 확정이 잠깁니다"` : ""}>확정…</span></div>` +
      (view ? `<div class="pv-grey" id="pv-original-note">원본 보기 — ${esc0(c.rel.name)}의 원래 그래프입니다(보기만)</div>` : "") +
      (confirmOff && (errs.length || pending.length) ? `<div class="pv-grey" id="pv-confirm-why">확정 잠김 — 아래 문제를 고치면 열립니다</div>` : "") + problems +
      (pending.length ? `<div class="pv-grey" id="pv-pending">다음 연결 필요 — ${pending.map(esc0).join(" · ")} (반영은 됐고 확정만 막힘)</div>` : "") + discard +
      (PV.soon ? `<div class="pv-grey" id="pv-soon-note">✦ 자동 추천 · 준비 중</div>` : "") +
      (PV.bandErr ? `<div class="rg err small" id="pv-band-error">${esc0(PV.bandErr)}</div>` : "");
  }

  /* ---------------------------------------------------------------- 변경 목록(9 · 10 · 20쪽) */
  function changeList(c) {
    PV.ctx = c;
    if (!isPack(c) || !editHere(c)) return "";
    const e = editData(c);
    if (!e) return `<div class="muted" id="change-list" data-loading>변경 목록을 읽는 중…</div>`;
    const groups = {};
    e.lines.forEach(l => { groups[l.group] = (groups[l.group] || 0) + 1; });
    const kindOf = name => { const g = (e.graph || c.rel.graph || {}).nodes || []; return ((g.find(n => n.name === name) || (c.rel.graph.nodes || []).find(n => n.name === name)) || {}).kind || ""; };
    const title = l => l.kind === "node" ? `node ${l.node || ""}` : l.kind === "edge" ? (l.summary || "연결") : l.kind === "file" ? `파일 ${l.node || ""}` : (l.node || "");
    const sub = l => l.kind === "value" ? `값 · ${l.summary || ""}` : l.kind === "edge" ? "" : (l.summary || "");
    const structural = e.lines.filter(l => l.kind === "node" || l.kind === "edge");
    let rows;
    if (PV.expand) {   // 20쪽: 줄마다 고르기 · ＋/−/✎ · 번호 · 묶음은 왼쪽 보라 선
      rows = e.lines.map(l => `<div class="pv-line${groups[l.group] > 1 ? " pv-grp" : ""}" data-line="${l.n}" data-group="${l.group}" data-kind="${esc0(l.kind)}">` +
        `<span class="chip${PV.sel.includes(l.n) ? " on" : ""}" data-act="pv-sel" data-n="${l.n}">${PV.sel.includes(l.n) ? "☑" : "☐"}</span>` +
        `<span class="pv-sign pv-s${l.sign === "＋" ? "add" : l.sign === "−" ? "del" : "val"}">${esc0(l.sign)}</span><div class="pv-ltxt"><b>${esc0(title(l))}</b>${sub(l) ? `<div class="small">${esc0(sub(l))}</div>` : ""}${l.source === "ai" ? ` <span class="pv-ai-tag">AI 제안</span>` : l.source && l.source !== "person" ? ` <span class="muted">(${esc0(l.source)})</span>` : ""}</div>` +
        `<span class="pv-n">${l.n}</span>${PV.expand ? "" : `<span class="btn small" data-act="pv-revert" data-n="${l.n}">되돌리기</span>`}</div>`).join("") +
        (structural.length ? `<div class="small"><span class="link" data-act="pv-expand" id="pv-collapse">접기</span></div>` : "");
    } else {   // 9 · 10 · 17쪽: 값 수정 카드 + 구조 편집은 한 줄로 접음
      rows = e.lines.filter(l => !structural.includes(l)).map(l => `<div class="pv-card" data-line="${l.n}" data-group="${l.group}" data-kind="${esc0(l.kind)}"><span class="pv-n">${l.n}</span>` +
        `<span class="pv-chipnode"><b>${esc0(l.node || "")}</b><span class="small muted">${(w => kindOf(l.node) ? hv(w, `${kindOf(l.node)} · ${w}`) : esc0(w))(l.kind === "file" ? "파일 수정" : l.kind === "blank" ? "빈 node" : "값 수정")}</span></span>${l.source === "ai" ? '<span class="pv-ai-tag">AI 제안</span>' : ""}<span class="sp"></span>` +
        `<span class="btn small" data-act="pv-revert" data-n="${l.n}">되돌리기</span><div class="pv-csum small">${esc0(l.summary || "")}</div></div>`).join("") +
        (structural.length ? `<div class="small" id="pv-structure"><span class="link" data-act="pv-expand" id="pv-expand">＋ 구조 편집 ${structural.length}줄 — 펼쳐 봄</span></div>` : "");   // 9쪽: 값 수정만이면 카드만
    }
    const dep = PV.dep ? `<div class="pv-confirm" id="pv-dep-box"><div class="pv-errline">${esc0(PV.dep.message || `${PV.dep.asked.join(" · ")}번을 되돌리면 ${PV.dep.lines.filter(x => !PV.dep.asked.includes(x)).join(" · ")}번이 고친 node가 없어집니다`)}</div>` +
      `<div class="row small"><span class="btn p" data-act="pv-revert-together" id="pv-revert-together">${[...new Set([...PV.dep.asked, ...PV.dep.lines])].sort((a, b) => a - b).join(" · ")}번 함께 되돌리기</span><span class="btn" data-act="pv-dep-cancel">취소</span></div></div>` : "";
    const selBox = PV.sel.length ? `<div class="pv-confirm" id="pv-sel-box">고른 ${PV.sel.length}줄 되돌리기 — ${e.lines.length - PV.sel.length}줄(${e.lines.filter(l => !PV.sel.includes(l.n)).map(l => esc0(l.node || l.summary || "")).slice(0, 3).join(" · ")})만 남음 · 지운 연결은 되살아남 · 배치는 지금 그대로` +
      `<div class="row small"><span class="btn p" data-act="pv-revert-sel" id="pv-revert-sel">고른 줄 되돌리기</span><span class="btn" data-act="pv-sel-clear">취소</span></div></div>` : "";
    return `<div id="change-list" data-lines="${e.lines.length}"><h3>변경 목록${PV.expand ? ` · ${e.lines.length}` : ""}</h3>${rows || '<div class="muted">아직 없음</div>'}${dep}${selBox}` +

      `<h3>이 편집</h3><div class="small" id="pv-edit-sum">${hv(`출발 ${c.rel.name} · ${e.check && e.check.ok ? "검사 통과" : "검사 문제 있음"}`, `출발 ${c.rel.name} ${short(e.base_revision)} · 지금 revision ${short(e.revision)} · ${e.check && e.check.ok ? "검사 통과" : "검사 문제 있음"}`)}</div>` +

      (PV.bandErr ? `<div class="rg err small">${esc0(PV.bandErr)}</div>` : "") + `</div>`;
  }

  /* ---------------------------------------------------------------- 동작 */
  function resetForm() { Object.assign(PV, { form: {}, files: {}, removed: [], added: [], addText: "", pick: null, preview: null, previewOps: null, err: null, msg: null, idText: null, idErr: null }); }
  function open(name) { if (PV.node !== name) { resetForm(); Object.assign(PV, { node: name, tab: "view", pos: null, start: false, det: null, ioRes: null, run: null, caseIdx: 0, iter: 1, role: null }); } }
  function revert(c, lines, together) {
    PV.bandErr = null;
    c.api("DELETE", `${base(c)}/edit/ops?lines=${lines.join(",")}${together ? "&together=1" : ""}`)
      .then(r => { PV.dep = null; PV.sel = []; if (PV.edit) PV.edit.data = r.edit; reload(c); })
      .catch(e => {
        if (e.status === 409 && e.detail && e.detail.kind === "depends") { PV.dep = { asked: lines, lines: e.detail.lines || [], message: e.detail.message }; }
        else PV.bandErr = `되돌리지 못함 — ${e.message}`;
        c.render();
      });
  }
  const ordered = (o, k) => { const ks = Object.keys(RULE_KEYS[k] || {}); return Object.fromEntries(Object.entries(o).sort(([x], [y]) => (ks.includes(x) ? ks.indexOf(x) : ks.length) - (ks.includes(y) ? ks.indexOf(y) : ks.length))); };   // 8쪽 줄 순서
  function onClick(a, t, c) {
    if (!isPack(c)) return false;
    if (a === "node") { open(t.dataset.node); return false; }   // app.js가 UI.node를 정하고 다시 그림
    if (!a.startsWith("pv-")) return false;
    const d = PV.det && PV.det.data;
    const num = x => { const v = Number(x); return Number.isFinite(v) ? v : 0; };
    switch (a) {
      case "pv-close": PV.node = null; c.ui.node = null; break;
      case "pv-tab": PV.tab = t.dataset.k; PV.pos = null; break;
      case "pv-iter": PV.iter = Number(t.dataset.k); break;
      case "pv-start": PV.start = true; break;
      case "pv-step": {
        const step = (cur0) => { const v = num(cur0), f = t.dataset.score || !Number.isInteger(v) || (v > 0 && v < 1) ? 0.1 : 1; let x = Math.round((v + f * Number(t.dataset.d)) * 100) / 100; if (t.dataset.score) x = Math.min(1, Math.max(0, x)); return x; };
        if (t.dataset.rule !== undefined) { const prm = `rules[${t.dataset.rule}].${t.dataset.rk}`, o = { ...(val(d, prm) || {}) }; o[t.dataset.sk] = step(o[t.dataset.sk]); PV.form[prm] = ordered(o, t.dataset.rk); }
        else if (t.dataset.pool !== undefined) { const o = { ...(val(d, t.dataset.prm) || {}) }; o.pool = poolOk(d, num(o.pool ?? val(d, "top_k")) + Number(t.dataset.d)); PV.form[t.dataset.prm] = o; }   // EV-072: 걸음 1
        else if (t.dataset.sk !== undefined) {
          const v0 = val(d, t.dataset.prm), o = { ...(v0 && typeof v0 === "object" ? v0 : {}) }, by = Number(t.dataset.by);   // D-492 덧 28: 청크 크기 100씩 · 오버랩 50씩 · 'unset'(없음) = 빈 칸
          o[t.dataset.sk] = by ? Math.max(0, num(o[t.dataset.sk] ?? 0) + by * Number(t.dataset.d)) : Math.max(0, step(o[t.dataset.sk]));
          if (t.dataset.sk === "chunk_size" && !o.chunk_size) { delete o.chunk_size; delete o.overlap; }   // 0 = 섹션 그대로
          if (t.dataset.sk === "overlap" && o.chunk_size != null) o.overlap = Math.min(o.overlap, o.chunk_size - 1);
          PV.form[t.dataset.prm] = o;
        }
        else if (t.dataset.prm === "top_k") { const lim = ((d.form || {}).limits || {}).top_k; PV.form.top_k = Math.max(1, Math.min(lim ? lim.max : Infinity, step(val(d, "top_k")))); }   // 013-66
        else PV.form[t.dataset.prm] = step(val(d, t.dataset.prm));
        break;
      }
      case "pv-chip": {
        if (t.dataset.rule !== undefined) { const prm = `rules[${t.dataset.rule}].${t.dataset.rk}`, o = { ...(val(d, prm) || {}) }, l = [...(o[t.dataset.sk] || [])], v = t.dataset.v; o[t.dataset.sk] = l.includes(v) ? l.filter(x => x !== v) : [...l, v]; PV.form[prm] = ordered(o, t.dataset.rk); }
        else if (t.dataset.sk !== undefined) { const o = { ...(val(d, t.dataset.prm) || {}) }, l = [...(o[t.dataset.sk] || [])], v = t.dataset.v; o[t.dataset.sk] = l.includes(v) ? l.filter(x => x !== v) : [...l, v]; PV.form[t.dataset.prm] = o; }
        else PV.form[t.dataset.prm] = t.dataset.v;
        break;
      }
      case "pv-bool": { const prm = `rules[${t.dataset.rule}].${t.dataset.rk}`, o = { ...(val(d, prm) || {}) }; o[t.dataset.sk] = !o[t.dataset.sk]; PV.form[prm] = ordered(o, t.dataset.rk); break; }
      case "pv-rule-del": { const i = Number(t.dataset.i); PV.removed = PV.removed.includes(i) ? PV.removed.filter(x => x !== i) : [...PV.removed, i]; break; }
      case "pv-rule-add": { try { PV.added.push({ kind: PV.addKind, value: JSON.parse(PV.addText || "null") }); PV.addText = ""; PV.err = null; } catch (e) { PV.err = `규칙 값이 JSON 형식이 아닙니다 — ${e.message}`; } break; }
      case "pv-rule-unadd": PV.added.splice(Number(t.dataset.i), 1); break;
      case "pv-lib": { const items = ((PV.prompts && PV.prompts.data) || {}).items || [], it = items.find(x => x.path === t.dataset.path); PV.pick = PV.pick && it && PV.pick.path === it.path ? null : it || null;
        if (PV.pick && isNewNode(c) && Array.isArray(PV.pick.reads) && PV.pick.reads.length) PV.form.reads = [...PV.pick.reads];   // 13쪽: 고른 프롬프트가 읽는 값
        break; }
      case "pv-read": { const k = t.dataset.k, curR = PV.form.reads || ((d && d.reads) || []).map(x => x.name); PV.form.reads = curR.includes(k) ? curR.filter(x => x !== k) : [...curR, k]; break; }
      case "pv-delete": {
        const n = PV.node, b = blankOf(c);
        if (b) {   // EV-073: a blank node has no connections in the Pack yet — its line goes
          c.api("POST", `${base(c)}/releases/${c.rel.id}/edit/ops`, { ops: [{ op: "remove_node", graph: b.graph, node: b.id }], source: "person" })
            .then(r => { PV.node = null; c.ui.node = null; PV.ai = null; if (PV.edit) PV.edit.data = r.edit; reload(c); }).catch(e => { PV.err = `지우지 못함 — ${e.message}`; c.render(); });
          return true;
        }
        if (typeof Canvas !== "undefined" && Canvas.requestDelete) Canvas.requestDelete(c, n); return true;
      }   // 11쪽 ③ · 17쪽: 지우기 전 영향 미리보기
      case "pv-join-skip": PV.joinSkip = { ...(PV.joinSkip || {}), [t.dataset.k]: true }; break;
      case "pv-join-both": {   // 13쪽 ④ '둘 다 연결': 그 값을 쓰는 다른 node에서 이 node로 잇기
        const others = String(t.dataset.from || "").split(",").filter(Boolean);
        c.api("POST", `${base(c)}/releases/${c.rel.id}/edit/ops`, { ops: others.map(o => ({ op: "add_edge", graph: d.graph, edge: [o, PV.node] })), source: "person" })
          .then(r => { if (PV.edit) PV.edit.data = r.edit; PV.ioRes = null; PV.det = null; reload(c); })
          .catch(e => { PV.err = `잇지 못함 — ${e.message}`; c.render(); });
        return true;
      }
      case "pv-insert": { const f = (d && d.form) || {}, k = t.dataset.file, txt = k in PV.files ? PV.files[k] : ((f.file_text || {})[k] ?? ""); PV.files[k] = `${txt}${txt && !/\s$/.test(txt) ? " " : ""}{${t.dataset.key}}`; break; }
      case "pv-reset": resetForm(); break;
      case "pv-preview": {
        if (!d || PV.busy) return true;
        let ops;
        try { ops = opsOf(c, d); } catch (e) { PV.err = e.message; break; }
        if (!ops.length) { PV.err = null; PV.preview = null; PV.msg = "고친 값이 없습니다"; break; }
        Object.assign(PV, { busy: true, err: null, msg: null, preview: null });
        const sent = JSON.stringify(ops);
        c.api("POST", `${base(c)}/releases/${c.rel.id}/edit/preview`, { ops })
          .then(r => { PV.busy = false; PV.preview = r; PV.previewOps = sent; c.render(); })
          .catch(e => { PV.busy = false; PV.err = e.status === 409 && e.detail && e.detail.kind === "edit-elsewhere" ? `편집 중인 것이 있습니다(${e.detail.release}에서) · 확정하거나 버린 뒤 ${c.rel.name}을 고칠 수 있습니다` : `미리보기를 하지 못함 — ${e.message}`; c.render(); });
        break;
      }
      case "pv-apply": {
        if (!d || PV.busy || !PV.preview || !PV.preview.ok) return true;
        const ops = JSON.parse(PV.previewOps);
        PV.busy = true;
        c.api("POST", `${base(c)}/releases/${c.rel.id}/edit/ops`, { ops, source: "person" })
          .then(r => { const rn = ops.find(o => o.param === "id" && o.op === "set"); if (rn && typeof Canvas !== "undefined" && Canvas.renamed) Canvas.renamed(c, rn.node, rn.value);
            PV.busy = false; resetForm(); PV.det = null; PV.ioRes = null; if (PV.edit) PV.edit.data = r.edit; PV.node = null; c.ui.node = null; c.ui.itab = "change"; reload(c); })   // 9쪽: 반영하면 오른쪽 '변경' 탭
          .catch(e => {
            PV.busy = false;
            const dt = e.detail || {};
            PV.err = dt.kind === "edit-elsewhere" ? `편집 중인 것이 있습니다(${dt.release}에서) · 확정하거나 버린 뒤 ${c.rel.name}을 고칠 수 있습니다`
              : dt.kind === "check" ? `반영할 수 없음 — ${(dt.errors || []).map(x => x.message).join(" / ")}` : `반영하지 못함 — ${e.message}`;
            c.render();
          });
        break;
      }
      case "pv-view": c.ui.edView = c.ui.edView === "original" ? "edit" : "original"; break;
      case "pv-discard": PV.discard = true; break;
      case "pv-discard-no": PV.discard = false; break;
      case "pv-discard-ok":
        c.api("DELETE", `${base(c)}/edit`).then(() => { PV.discard = false; PV.edit = null; PV.sel = []; c.ui.edView = "edit"; c.ui.ediff = null; reload(c); })   // D-551 덧 1 ②: the change view lets the discarded edit go
          .catch(e => { PV.bandErr = `버리지 못함 — ${e.message}`; c.render(); });
        return true;
      case "pv-confirm": { const e = PV.edit && PV.edit.data; if (e && e.check && !e.check.ok) return true; c.ui.confirm = true; break; }   // 리드의 확정 창(22 · 43쪽)
      case "pv-sel": { const n = Number(t.dataset.n); PV.sel = PV.sel.includes(n) ? PV.sel.filter(x => x !== n) : [...PV.sel, n]; break; }
      case "pv-sel-clear": PV.sel = []; break;
      case "pv-more": PV.more = !PV.more; break;
      case "pv-expand": PV.expand = !PV.expand; if (!PV.expand) PV.sel = []; break;
      case "pv-revert": revert(c, [Number(t.dataset.n)], false); return true;
      case "pv-revert-sel": revert(c, [...PV.sel].sort((x, y) => x - y), false); return true;
      case "pv-revert-together": revert(c, PV.dep.asked, true); return true;
      case "pv-dep-cancel": PV.dep = null; break;
      case "pv-soon": PV.soon = !PV.soon; break;   // EV-073 CHG-AI-10: 요청 0
      case "pv-role-save": {
        const b = blankOf(c); if (!b) return true;
        c.api("POST", `${base(c)}/releases/${c.rel.id}/edit/ops`, { ops: [{ op: "set", graph: b.graph, node: b.id, param: "expect", value: { text: PV.role ?? b.expect.text } }], source: "person" })
          .then(r => { PV.role = null; PV.err = null; PV.ai = null; if (PV.edit) PV.edit.data = r.edit; reload(c); }).catch(e => { PV.err = `저장하지 못함 — ${e.message}`; c.render(); });
        return true;
      }
      case "pv-blank-kind": {
        const b = blankOf(c); if (!b || typeof Canvas === "undefined" || !Canvas.fillBlank) return true;
        PV.edit = null; Promise.resolve(Canvas.fillBlank(c, b, t.dataset.k)).then(() => { PV.det = null; PV.ioRes = null; PV.tab = "edit"; c.render(); });
        return true;
      }
      case "pv-ai-out": { const A = PV.ai; if (A) A.out = A.out === t.dataset.k ? null : t.dataset.k; break; }
      case "pv-ai-go": {
        const b = blankOf(c), A = aiState(b);
        if (A.busy) return true;
        Object.assign(A, { busy: true, err: null, data: null, out: null });
        c.api("POST", `${base(c)}/edit/nodes/${encodeURIComponent(PV.node)}/suggest`, { ask: A.ask, release: c.rel.id })
          .then(r => { A.busy = false; A.data = r; c.render(); })
          .catch(e => { const dt = e.detail || {}; A.busy = false;
            A.err = dt.kind === "provider" ? `provider · ${dt.code} · ${dt.where}${dt.status ? ` · HTTP ${dt.status}` : ""}` : dt.message || (typeof dt === "string" ? dt : e.message); c.render(); });
        break;
      }
      case "pv-ai-open": case "pv-ai-apply": {
        const A = PV.ai, p = A && A.data && A.data.proposal;
        if (!p || A.busy) return true;
        const g = (p.ops[0] || {}).graph, ops = p.ops.concat(A.out ? [{ op: "add_edge", graph: g, edge: [p.node.id, A.out] }] : []);
        A.busy = true;
        c.api("POST", `${base(c)}/releases/${c.rel.id}/edit/ops`, { ops, fills: p.fills, source: "ai", ai: A.data.ai })
          .then(r => { const id = p.node.id, open2 = a === "pv-ai-open"; PV.ai = null; resetForm(); PV.det = null; PV.ioRes = null; if (PV.edit) PV.edit.data = r.edit;
            if (open2) { PV.node = id; c.ui.node = id; PV.tab = "edit"; PV.pos = null; } else { PV.node = null; c.ui.node = null; c.ui.itab = "change"; } reload(c); })
          .catch(e => { const dt = e.detail || {}; A.busy = false; A.err = dt.kind === "check" ? `반영할 수 없음 — ${(dt.errors || []).map(x => x.message).join(" / ")}` : `반영하지 못함 — ${e.message}`; c.render(); });
        return true;
      }
      default: return false;
    }
    c.render();
    return true;
  }
  function onInput(t, c) {
    if (!isPack(c) || !t.closest || !(t.closest("#node-pop") || t.closest("#change-list") || t.closest("#edit-band"))) return false;
    const d = PV.det && PV.det.data;
    if (t.dataset.pv === "run") { PV.run = t.value; PV.caseIdx = 0; PV.iter = 1; c.render(); return true; }
    if (t.dataset.pv === "case") { PV.caseIdx = Number(t.value); PV.iter = 1; c.render(); return true; }
    if (t.dataset.pv === "addkind") { PV.addKind = t.value; return true; }
    if (t.dataset.pv === "addtext") { PV.addText = t.value; return true; }
    if (t.id === "pv-id") { PV.idText = t.value.trim(); return true; }   // 검사는 칸을 벗어날 때(change)
    if (t.dataset.pv === "role") { PV.role = t.value; return true; }        // EV-073 빈 node 역할 글
    if (t.dataset.pv === "ai-ask") { if (PV.ai) PV.ai.ask = t.value; return true; }
    if (t.dataset.pvFile !== undefined) { PV.files[t.dataset.pvFile] = t.value; return true; }
    if (t.dataset.pvAddval !== undefined) {   // 목록 칸에 새 값(쉼표나 Enter 대신 입력이 끝나면 change에서 — 여기서는 쉼표로 끝낸 값만)
      if (!t.value.endsWith(",")) return true;
      const v = t.value.slice(0, -1).trim(), prm = `rules[${t.dataset.rule}].${t.dataset.rk}`, o = { ...(val(d, prm) || {}) };
      if (v) { o[t.dataset.sk] = [...new Set([...(o[t.dataset.sk] || []), v])]; PV.form[prm] = ordered(o, t.dataset.rk); }
      t.value = ""; c.render(); return true;
    }
    if (t.dataset.rule !== undefined) {
      const prm = `rules[${t.dataset.rule}].${t.dataset.rk}`;
      if (t.dataset.sk !== undefined) {
        const o = { ...(val(d, prm) || {}) };
        if (t.dataset.pvNum !== undefined) { const v = Number(t.value); if (t.value.trim() === "") delete o[t.dataset.sk]; else if (Number.isFinite(v)) o[t.dataset.sk] = v; }
        else { try { o[t.dataset.sk] = JSON.parse(t.value); } catch (e) { return true; } }
        PV.form[prm] = ordered(o, t.dataset.rk);
      } else { try { PV.form[prm] = JSON.parse(t.value); } catch (e) { return true; } }
      return true;
    }
    if (t.dataset.prm && t.dataset.pool !== undefined && t.dataset.pvNum !== undefined) {   // EV-072: 재정렬 개수를 숫자로 — 칸을 벗어날 때 범위로 맞춤
      const o = { ...(val(d, t.dataset.prm) || {}) }, v = Number(t.value);
      if (t.value.trim() !== "" && Number.isInteger(v)) { o.pool = v; PV.form[t.dataset.prm] = o; }
      return true;
    }
    if (t.dataset.prm && t.dataset.sk !== undefined && t.dataset.pvNum !== undefined) {
      const o = { ...(val(d, t.dataset.prm) || {}) }, v = Number(t.value);
      if (t.value.trim() !== "" && Number.isFinite(v)) { o[t.dataset.sk] = v; PV.form[t.dataset.prm] = o; }
      return true;
    }
    if (t.dataset.prm) {
      const prm = t.dataset.prm;
      if (t.dataset.pvNum !== undefined) { const v = Number(t.value); PV.form[prm] = t.value.trim() === "" ? "" : Number.isFinite(v) ? v : t.value; }
      else if (t.dataset.pvJson !== undefined) { try { PV.form[prm] = JSON.parse(t.value); } catch (e) { return true; } }
      else PV.form[prm] = t.value;
      if (t.tagName === "SELECT") { if (prm === "prompt" || prm === "template") PV.pick = null; c.render(); }
      return true;
    }
    return false;
  }

  /* ---------------------------------------------------------------- 오류 판정 도우미(31~32쪽 #1 · #2 · #3 — 갈래 B도 씀) */
  const ADDABLE = ["llm@1", "retrieve@1", "validate@1", "validate@2", "router@1", "render@1"];
  const check = {
    kind(kind) { return ADDABLE.includes(kind) ? null : `'${kind}' node는 추가할 수 없습니다(추가할 수 있는 종류: ${ADDABLE.join(", ")})`; },      // #1
    name(id, ids) {                                                                                                                        // #2
      if (ids.includes(id)) return `node id '${id}'가 두 번 있습니다`;
      if (!/^[a-z][a-z0-9_]*$/.test(id) || ["START", "END"].includes(id)) return `node id로 '${id}'는 쓸 수 없습니다 — 영문 소문자 · 숫자 · _ 만`;
      return null;
    },
    slot(free) { return free && free.length ? null : "담을 빈 자리가 없습니다 — 같이 쓰면 앞 결과를 덮어씁니다"; },                          // #3
  };

  /* ---------------------------------------------------------------- 모양(한 번만 넣음 — index.html을 고치지 않음) */
  // 32쪽 #2: 이름 칸을 벗어날 때 검사(겹침 · 형식) — 문제면 칸 빨강 · 이유 한 줄 · 반영 잠김, 고치면 풀림
  // EV-072: 재정렬 개수를 숫자로 넣고 칸을 벗어나면 5 ~ 50 · 아래 끝 top_k로 맞춤
  if (typeof document !== "undefined") document.addEventListener("change", ev => {
    if (ev.target.id !== "pv-f-rerank-pool" || !PV.ctx || !PV.det || !PV.det.data) return;
    const d = PV.det.data, o = { ...(val(d, "rerank") || {}) }, v = Number(ev.target.value);
    o.pool = poolOk(d, Number.isFinite(v) ? v : (o.pool ?? Number(val(d, "top_k")))); PV.form.rerank = o; PV.preview = null; PV.previewOps = null; PV.ctx.render();
  });
  if (typeof document !== "undefined") document.addEventListener("change", ev => {
    if (ev.target.id !== "pv-id" || !PV.ctx) return;
    const id = ev.target.value.trim(), g = typeof Canvas !== "undefined" && Canvas._state && Canvas._state.graph;
    const ids = ((g && g.nodes) || []).map(n => n.name).filter(n => n !== PV.node), why = id === PV.node ? null : check.name(id, ids);
    PV.idText = id; PV.idErr = why ? { short: ids.includes(id) ? "이미 있는 이름입니다" : "영문 소문자 · 숫자 · _ 만", why } : null;
    PV.preview = null; PV.previewOps = null; PV.ctx.render();
  });
  if (typeof document !== "undefined" && !document.getElementById("pv-style")) {
    const s = document.createElement("style");
    s.id = "pv-style";
    s.textContent = `.pv-pop{position:fixed;z-index:15;width:min(380px,calc(100vw - 16px));max-height:calc(100vh - 16px);overflow:auto;background:#fff;border:1px solid #d0d5dd;border-radius:12px;box-shadow:0 8px 24px rgba(16,24,40,.16);padding:10px 12px;font-size:12.5px}
.pv-head{display:flex;align-items:center;gap:6px}.pv-head .sp{flex:1}.pv-x{cursor:pointer;color:#667085;padding:0 4px}
.pv-io{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:6px 0;background:#f8fafc;border:1px solid #eaecf0;border-radius:8px;padding:6px 8px}.pv-io-h{font-weight:700;font-size:11.5px;color:#475467}.pv-io-row{font-size:11.5px;overflow-wrap:anywhere}
.pv-tabs{display:flex;gap:12px;border-bottom:1px solid #eaecf0;margin:4px 0 8px}.pv-tab{cursor:pointer;padding:4px 0;color:#475467}.pv-tab.on{color:#3056d3;border-bottom:2px solid #3056d3;font-weight:700}
.pv-ai{color:#6941c6}.pv-soon{font-size:10px;background:#f2f4f7;color:#667085;border-radius:6px;padding:0 4px}.pv-soon-btn{color:#98a2b3}
.pv-ai-tag{display:inline-block;font-size:10.5px;font-weight:700;color:#6941c6;background:#f4f3ff;border-radius:999px;padding:0 6px}.pv-ai-card{border:1px solid #bdb4fe;border-radius:8px;padding:8px;margin-top:8px;display:grid;gap:3px}.pv-ai-ok{color:#067647;font-weight:600}
#pv-blank-kinds .cv-kind{border:1px solid #e4e7ec;border-radius:8px;padding:6px 8px;margin-top:6px;cursor:pointer}#pv-blank-kinds .cv-kind code{font-size:11px;color:#667085;margin-left:4px}
.pv-sec{margin-bottom:8px}.pv-h{font-weight:700;color:#344054;margin-bottom:2px}.pv-foot{text-align:right}
.pv-form{list-style:none;padding:0;margin:6px 0;display:grid;gap:8px}.pv-badge{display:inline-block;margin-left:6px;padding:0 6px;border-radius:8px;background:#fff4e5;color:#b54708;font-size:11px;font-weight:500}.pv-form li{min-width:0}.pv-lb{font-weight:600;margin-bottom:2px}.pv-k{color:#98a2b3;font-weight:400}
.pv-form textarea,.pv-form input[type=text],.pv-form select{width:100%;box-sizing:border-box;font-size:12px}
.pv-step{display:inline-flex;align-items:center;gap:4px}.pv-step input{width:64px!important;text-align:center}.pv-add{margin-top:4px}
.pv-grey{color:#667085;background:#f2f4f7;border-radius:8px;padding:6px 8px;margin:4px 0;font-size:12px}
.pv-lock{border:1px solid #fda29b;background:#fffbfa;border-radius:8px;padding:8px}.pv-errline{color:#b42318;font-weight:600;margin:4px 0;overflow-wrap:anywhere}
.pv-pop .chip.off,.pv-pop .btn.off,.pv-band .btn.off{opacity:.45;pointer-events:none}.pv-actions{justify-content:flex-end;gap:6px;margin-top:8px}
.pv-hunk{font-family:ui-monospace,monospace;font-size:11px;margin-top:4px;overflow-wrap:anywhere}.pv-del{color:#b42318}.pv-addl{color:#067647}.pv-pre{white-space:pre-wrap;font-size:11px;max-height:120px;overflow:auto;background:#f8fafc;padding:6px;border-radius:6px}
.pv-band{display:flex;flex-wrap:wrap;align-items:center;gap:6px;background:#fffaeb;border:1px solid #fedf89;border-radius:10px;padding:6px 10px;margin-bottom:8px;font-size:12.5px}.pv-band .sp{flex:1}
.pv-problems{border:1px solid #fda29b;background:#fef3f2;border-radius:8px;padding:6px 10px;margin-bottom:8px}
.pv-confirm{border:1px solid #d0d5dd;background:#fff;border-radius:8px;padding:8px;margin:6px 0;box-shadow:0 4px 12px rgba(16,24,40,.08)}
.pv-line{display:flex;align-items:flex-start;gap:6px;border:1px solid #eaecf0;border-radius:8px;padding:6px;margin:4px 0;min-width:0}.pv-line.pv-grp{border-left:3px solid #7a5af8}
.pv-sign{font-weight:700;width:14px}.pv-sadd{color:#067647}.pv-sdel{color:#b42318}.pv-sval{color:#b54708}
.pv-card{display:flex;flex-wrap:wrap;align-items:center;gap:6px;border:1px solid #eaecf0;border-radius:8px;padding:6px 8px;margin:4px 0}.pv-card .sp{flex:1}.pv-chipnode{display:inline-flex;flex-direction:column;border:1px solid #fedf89;background:#fffaeb;border-radius:6px;padding:2px 6px}.pv-csum{flex-basis:100%}
.pv-sub{margin:2px 0}.pv-io-miss{color:#b42318;font-weight:600}.pv-ltxt{flex:1;min-width:0;overflow-wrap:anywhere}.pv-n{color:#98a2b3;font-size:11px}.pv-note{background:#fffaeb;border-radius:8px;padding:6px 8px;margin-top:6px}`;
    document.head.appendChild(s);
    window.addEventListener("scroll", () => { if (PV.node) requestAnimationFrame(place); }, true);
    window.addEventListener("resize", () => { if (PV.node) requestAnimationFrame(place); });
  }

  return { html, changeList, editBand, onClick, onInput, check, show: open, _state: PV };   // show: the canvas opens a node's popover (18쪽 갈 조건 고치기, 32쪽 #4 다시 고르기)
})();
