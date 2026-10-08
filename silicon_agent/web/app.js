// silicon-agent worktable. Plain ES module, no build step.
// Layout: runs rail | transcript (one turn per iteration) + composer | inspector tabs.

const $ = (s) => document.querySelector(s);
const api = async (path, opts) => {
  const r = await fetch(`/api${path}`, { cache: "no-store", ...opts });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 300)}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
};
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const fmt = (n, d = 1) => (n == null ? "–" : Number(n).toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };

// ---------------------------------------------------------------- state
let jobs = [], selected = null, detail = null, seen = 0, pollTimer = null, specs = [], memCache = null;
let view = "empty"; // empty | new | run | memory

// ---------------------------------------------------------------- boot
(async function boot() {
  try { $("#who").textContent = (await api("/me")).subject; } catch { $("#who").textContent = "unauthenticated"; }
  const h = await api("/health");
  $("#host").textContent = `${h.backends.join(" + ")} · ${h.designs.join(", ")}`;
  specs = await api("/specs");
  const tpl = $("#tpl");
  for (const s of specs) tpl.appendChild(el(`<option value="${esc(s.name)}">${esc(s.design)} · ${esc(s.backend)} · ${esc(s.task)}</option>`));
  tpl.onchange = () => { $("#spec").value = JSON.stringify(specs.find((s) => s.name === tpl.value).spec, null, 2); };
  tpl.onchange();
  $("#new-run").onclick = () => show("new");
  $("#open-memory").onclick = () => show("memory");
  $("#submit").onclick = submit;
  $("#send").onclick = sendSteer;
  $("#steer").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendSteer(); });
  $("#steer").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"; });
  $("#btn-stop").onclick = async () => { if (selected) { await post(`/jobs/${selected}/stop`); $("#btn-stop").disabled = true; } };
  document.querySelectorAll(".tabs button").forEach((b) => { b.onclick = () => switchTab(b.dataset.tab); });
  $("#t-insp").onclick = () => document.querySelector(".main").classList.toggle("show-inspector");
  $("#t-rail").onclick = () => document.querySelector(".shell").classList.toggle("show-rail");
  refreshMemCount();
  await refreshJobs();
  setInterval(refreshJobs, 5000);
  const hash = location.hash.slice(1);
  if (hash === "memory") show("memory"); else if (hash === "new") show("new"); else if (hash) select(hash);
})();

function show(v) {
  view = v;
  $("#empty").hidden = v !== "empty"; $("#new-form").hidden = v !== "new"; $("#run").hidden = v !== "run"; $("#memory").hidden = v !== "memory";
  if (v !== "run") { selected = null; clearTimeout(pollTimer); document.querySelectorAll("#runs li").forEach((li) => li.classList.remove("sel")); }
  if (v === "memory") { location.hash = "memory"; renderMemoryView(); }
  if (v === "new") location.hash = "new";
}

async function refreshMemCount() {
  try { memCache = await api("/memory"); const n = memCache.reduce((a, g) => a + g.count, 0); $("#mem-count").textContent = `memory: ${n} lessons`; } catch {}
}

// ---------------------------------------------------------------- new run
async function submit() {
  const msg = $("#submit-msg"); msg.className = "msg"; msg.textContent = "submitting…";
  let spec;
  try { spec = JSON.parse($("#spec").value); } catch (e) { msg.className = "msg err"; msg.textContent = `spec is not valid JSON: ${e.message}`; return; }
  if ($("#nomem").checked) spec.no_memory = true;
  try {
    const r = await post("/jobs", { spec, max_iter: Number($("#maxiter").value) });
    msg.textContent = `queued ${r.id}`;
    await refreshJobs();
    select(r.id);
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
}

// ---------------------------------------------------------------- rail
async function refreshJobs() {
  jobs = await api("/jobs");
  const ul = $("#runs"); ul.innerHTML = "";
  for (const j of jobs) {
    const b = j.best?.metrics;
    const status = j.status === "done" && b ? `PASS · ${fmt(b.fmax_mhz, 0)} MHz · ${j.iterations} iter` : j.status === "running" ? `running · iter ${j.iterations + 1}` : `${j.status} · ${j.iterations} iter`;
    const li = el(`<li class="${j.id === selected ? "sel" : ""}" data-id="${esc(j.id)}"><span class="dot ${j.status}"></span><div><div class="t">${esc(j.task || j.id)}</div><div class="m">${esc(j.backend)} · ${status}</div></div></li>`);
    li.onclick = () => select(j.id);
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- run view
async function select(id) {
  selected = id; seen = 0; detail = null;
  location.hash = id;
  clearTimeout(pollTimer);
  document.querySelectorAll("#runs li").forEach((li) => li.classList.toggle("sel", li.dataset.id === id));
  $("#empty").hidden = true; $("#new-form").hidden = true; $("#memory").hidden = true; $("#run").hidden = false; view = "run";
  $("#transcript").innerHTML = ""; $("#rtl").textContent = "";
  await poll();
}

async function poll() {
  if (!selected) return;
  const d = await api(`/jobs/${selected}?after=${seen}`);
  if (!detail) detail = { ...d, events: [] };
  Object.assign(detail, { status: d.status, summary: d.summary, error: d.error });
  detail.events.push(...d.events);
  seen = d.n_events;
  render();
  if (["queued", "running"].includes(d.status)) pollTimer = setTimeout(poll, 2500);
}

// fold the event stream into turns: one per iteration
function turns() {
  const rows = new Map();
  let pendingMem = null, pendingSteer = [];
  const get = (n) => { if (!rows.has(n)) rows.set(n, { n, pending: true, user: [] }); return rows.get(n); };
  for (const e of detail.events) {
    if (e.kind === "user") { pendingSteer.push(e); continue; }
    if (e.kind === "steer") { get(e.iteration).user.push(...pendingSteer.splice(0)); continue; }
    if (e.kind === "memory") { pendingMem = e; continue; }
    if (e.kind === "plan") {
      const prev = rows.get(e.iteration - 1); if (prev) prev.next = e;
      if (!e.stop) { const r = get(e.iteration); r.plan = e; r.memory = pendingMem; pendingMem = null; }
      continue;
    }
    if (e.kind === "evaluating") { const r = get(e.iteration); r.ir = e.ir; if (pendingMem) { r.memory = pendingMem; pendingMem = null; } continue; }
    if (e.kind === "result") { Object.assign(get(e.iteration), e, { pending: false }); continue; }
    if (e.kind === "lesson") { get(e.iteration).lesson = e; continue; }
  }
  const out = [...rows.values()].sort((a, b) => a.n - b.n);
  if (pendingSteer.length && out.length) out.at(-1).userAfter = pendingSteer;
  return out;
}

function bestOf(rows, c) {
  const feas = rows.filter((r) => r.violations && r.violations.length === 0);
  if (!feas.length) return null;
  const key = (r) => (c.objective || []).map((o) => ({ maximize_throughput: -r.throughput, maximize_fmax: -r.metrics.fmax_mhz, minimize_lut: r.metrics.lut, minimize_dsp: r.metrics.dsp, minimize_latency: r.latency_cycles }[o] ?? 0));
  return feas.sort((a, b) => { const ka = key(a), kb = key(b); for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i]; return 0; })[0];
}

function render() {
  const spec = detail.spec, c = spec.constraints, rows = turns(), done = rows.filter((r) => !r.pending);
  const best = detail.summary?.best || bestOf(done, c);
  const unit = detail.summary?.throughput_unit || (spec.design === "fir" ? "samples/cycle" : "MAC/cycle");
  const live = ["queued", "running"].includes(detail.status);
  const liveRow = rows.find((r) => r.pending);

  $("#j-task").textContent = spec.task || selected;
  const pill = $("#j-pill"); pill.className = `pill ${detail.status}`;
  pill.textContent = { queued: "queued", running: liveRow ? `running · iteration ${liveRow.n} · ${liveRow.ir ? "sim / synth / P&R" : "planning"}` : "running", done: `PASS · ${done.length} iterations`, failed: `FAIL · ${done.length} iterations`, error: "ERROR", interrupted: "interrupted" }[detail.status] || detail.status;
  $("#btn-stop").hidden = !live; $("#btn-stop").disabled = false;
  renderConstrip(best || done.at(-1), c, unit, !!best);
  renderTranscript(rows, unit, spec);
  $("#composer").classList.toggle("disabled", !live);
  $("#chint-r").textContent = `${spec.design || "matmul"} · ${spec.backend || "ecp5"} · ${selected}${detail.subject ? " · by " + detail.subject : ""}`;
  renderSugs(done.at(-1));
  renderDesign(liveRow?.ir ? liveRow : done.at(-1), done, spec);
  renderTrajectory(done, c, unit);
  if (best && !$("#rtl").textContent) api(`/jobs/${selected}/rtl`).then((t) => { $("#rtl").textContent = t; }).catch(() => {});
  renderMemoryTab(rows);
}

function renderConstrip(row, c, unit, feasible) {
  const s = $("#constrip"); s.innerHTML = "";
  if (!row) return;
  const m = row.metrics;
  const items = [
    ["fmax", `${fmt(m.fmax_mhz)} / ≥${c.fmax_mhz} MHz`, m.fmax_mhz >= c.fmax_mhz],
    ["dsp", c.max_dsp != null ? `${m.dsp} / ≤${c.max_dsp}` : `${m.dsp}`, c.max_dsp == null || m.dsp <= c.max_dsp],
    ["lut", c.max_lut != null ? `${m.lut} / ≤${c.max_lut}` : `${m.lut}`, c.max_lut == null || m.lut <= c.max_lut],
    ["sim", m.sim_passed ? "pass" : "FAIL", m.sim_passed],
    ["throughput", `${row.throughput} ${unit}`, null],
    ["latency", `${row.latency_cycles} cycles`, null],
  ];
  for (const [k, v, ok] of items) s.appendChild(el(`<span class="${ok === null ? "" : ok ? "ok" : "bad"}"><i></i><em>${k}</em><b>${esc(v)}</b></span>`));
  s.appendChild(el(`<span><em>${feasible ? "best feasible" : "latest attempt"}</em></span>`));
}

// ---------------------------------------------------------------- transcript
function irChips(ir, prevIr, locked) {
  return `<div class="chips">` + Object.entries(ir).filter(([k]) => !(k in (locked || {})) && k !== "coefs").map(([k, v]) => {
    const changed = prevIr && JSON.stringify(prevIr[k]) !== JSON.stringify(v);
    return `<span class="chip ${changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
  }).join("") + `</div>`;
}
const line = (lbl, html, cls = "") => `<div class="ln"><span class="lbl ${cls}">${lbl}</span><div class="txt">${html}</div></div>`;

function renderTranscript(rows, unit, spec) {
  const ol = $("#transcript");
  const atBottom = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 40;
  ol.innerHTML = "";
  let prevIr = null;
  for (const r of rows) {
    const li = el(`<li class="turn ${r.pending ? "live" : ""}"><div class="num">${r.n}</div><div class="body"></div></li>`);
    const b = li.querySelector(".body");
    for (const u of r.user) b.appendChild(el(line("you", `<div class="usermsg">${esc(u.message)}</div>`, "user")));
    if (r.memory?.lessons?.length) b.appendChild(el(line("memory", r.memory.lessons.map((l) => `<div class="memrow"><span class="mid">${esc(l.id)} ${l.score >= 0 ? "+" : ""}${fmt(l.score)}</span>${esc(l.text)}</div>`).join(""), "accent")));
    if (r.n === 1) b.appendChild(el(line("start", "Initial design from the spec.")));
    else {
      const d = r.plan?.diagnosis ?? r.diagnosis, a = r.plan?.action ?? r.action;
      if (d) b.appendChild(el(line("hypothesis", esc(d))));
      if (a) b.appendChild(el(line("action", esc(a))));
    }
    if (r.ir) b.appendChild(el(line("ir", irChips(r.ir, prevIr, spec.locked))));
    if (r.pending) {
      const k = r.ir ? 1 : 0;
      b.appendChild(el(line("running", `<div class="progress">${["plan", "sim", "synth", "place & route", "timing"].map((s, i) => `<span class="${i < k ? "done" : i === k ? "active" : "todo"}"><i></i>${s}</span>`).join("")}</div>`, "accent")));
    } else {
      const m = r.metrics, ok = r.violations.length === 0;
      const cp = m.critical_path?.length > 1 ? `<div class="cp">critical ${fmt(m.critical_path[0].total_ns, 2)} ns: ` + m.critical_path.slice(1).map((s) => `<span>${esc(s.kind)} <i>${esc(s.rtl)}</i> ${fmt(s.logic_ns, 2)}+${fmt(s.route_ns, 2)}</span>`).join("") + `</div>` : "";
      b.appendChild(el(line("result", `<div class="res"><div class="row"><span class="${ok ? "ok" : "bad"}">${ok ? "FEASIBLE" : "VIOLATES"}</span><span>fmax ${fmt(m.fmax_mhz)} MHz · LUT ${m.lut} · FF ${m.ff} · DSP ${m.dsp} · ${r.throughput} ${unit} · ${r.eda_seconds}s</span>${ok ? "" : `<span class="bad">${esc(r.violations.join("; "))}</span>`}</div>${cp}</div>`)));
      if (r.lesson) {
        const L = r.lesson.lesson;
        const node = el(line("lesson", `<div class="lesson"><div class="ltxt">${esc(L.text)}</div><div class="vote"><span class="${L.outcome}">${L.outcome}</span><button data-v="up" title="confirm">👍</button><button data-v="down" title="contradict">👎</button></div></div>`, "warn"));
        node.querySelectorAll("button").forEach((btn) => { btn.onclick = async () => { const res = await post(`/memory/${spec.design || "matmul"}/${spec.backend || "ecp5"}/vote`, { id: L.id, vote: btn.dataset.v }); btn.parentElement.querySelector("span").textContent = `${L.outcome} · ${res.score >= 0 ? "+" : ""}${fmt(res.score)}`; refreshMemCount(); }; });
        b.appendChild(node);
      }
    }
    if (r.next?.stop) b.appendChild(el(line("planner", `${esc(r.next.diagnosis)}<br><b>${esc(r.next.action)}</b> · stop`)));
    if (r.userAfter) for (const u of r.userAfter) b.appendChild(el(line("you", `<div class="usermsg">${esc(u.message)} <span class="muted">(queued for the next plan)</span></div>`, "user")));
    ol.appendChild(li);
    if (!r.pending) prevIr = r.ir;
  }
  if (detail.error) ol.appendChild(el(`<li class="turn"><div class="num">!</div><div class="body">${line("error", `<pre class="code">${esc(detail.error)}</pre>`)}</div></li>`));
  if (atBottom) ol.scrollTop = ol.scrollHeight;
}

function renderSugs(last) {
  const s = $("#sugs"); s.innerHTML = "";
  if (!last) return;
  const ir = last.ir || {}, v = last.violations || [], sugs = [];
  if (v.some((x) => x.startsWith("fmax"))) sugs.push("focus on timing: which single knob removes the stage on the critical path?");
  if (v.some((x) => x.startsWith("dsp"))) sugs.push("reduce DSP count first, even at lower throughput");
  if (!v.length) sugs.push("explore one more shape before stopping");
  if ("acc_in_dsp" in ir) sugs.push("don't change acc_in_dsp");
  if ("fold" in ir) sugs.push("try the transposed form (fold=1)");
  sugs.push("explain the last critical path in one sentence");
  for (const t of sugs.slice(0, 4)) { const b = el(`<button>${esc(t)}</button>`); b.onclick = () => { $("#steer").value = t; $("#steer").focus(); }; s.appendChild(b); }
}

async function sendSteer() {
  const ta = $("#steer"), msg = ta.value.trim();
  if (!msg || !selected) return;
  ta.disabled = true;
  try { await post(`/jobs/${selected}/steer`, { message: msg }); ta.value = ""; ta.style.height = "auto"; await poll(); }
  catch (e) { alert(e.message); }
  finally { ta.disabled = false; ta.focus(); }
}

// ---------------------------------------------------------------- inspector: design
function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) => { p.hidden = p.id !== `pane-${name}`; });
}

function renderDesign(row, done, spec) {
  const p = $("#pane-design"); p.innerHTML = "";
  if (!row?.ir) { p.appendChild(el(`<div class="muted">no design yet</div>`)); return; }
  const design = spec.design || "matmul", locked = spec.locked || {}, xc = spec.backend === "xc7s50";
  const ir = { ...locked, ...row.ir };  // planner-visible fields plus the task-locked ones
  const prev = done.filter((d) => d.n < row.n).at(-1)?.ir;
  const sec = el(`<div class="sec"><h3>Architecture <span class="hint">iteration ${row.n}${row.pending ? " (evaluating)" : ""}</span></h3></div>`);
  const arch = el(`<div class="arch"></div>`);
  if (design === "matmul") {
    const R = Math.min(ir.pe_rows, 12), C = Math.min(ir.pe_cols, 16);
    for (let i = 0; i < R; i++) { const r = el(`<div class="r"></div>`); for (let j = 0; j < C; j++) r.appendChild(el(`<div class="pe">×+</div>`)); arch.appendChild(r); }
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.pe_rows}×${ir.pe_cols} output-stationary systolic${ir.pe_rows > 12 || ir.pe_cols > 16 ? " (clipped)" : ""} · A → rows, B ↓ columns</span><span>PE: ${xc ? "DSP48E1" : "MULT18X18D"} (${ir.dsp_regs}) + ${ir.acc_in_dsp && xc ? "DSP post-adder" : "fabric carry-chain"} accumulator, ${ir.acc_width} b${ir.pipe_mul ? ` + ${ir.pipe_mul} fabric pipe` : ""}</span><span>${ir.reg_in ? "registered inputs · " : ""}${row.throughput ?? ir.pe_rows * ir.pe_cols} MAC/cycle · latency ${row.latency_cycles ?? "?"} cycles</span></div>`));
  } else {
    const U = ir.symmetric ? Math.ceil(ir.taps / 2) : ir.taps, M = Math.ceil(U / ir.fold);
    const taps = el(`<div class="r"></div>`); for (let k = 0; k < Math.min(ir.taps, 32); k++) taps.appendChild(el(`<div class="tap ${ir.symmetric && k >= ir.taps / 2 ? "" : "on"}"></div>`)); arch.appendChild(taps);
    const macs = el(`<div class="r"></div>`); for (let m = 0; m < Math.min(M, 16); m++) macs.appendChild(el(`<div class="pe mac">×+${ir.fold > 1 ? `/${ir.fold}` : ""}</div>`)); arch.appendChild(macs);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.taps} taps${ir.symmetric ? ", symmetric (" + U + " unique)" : ""} · ${ir.fold === 1 ? "transposed form, 1 sample/cycle" : `folded ×${ir.fold}, direct form, 1/${ir.fold} sample/cycle`}</span><span>${M} multiplier${M > 1 ? "s" : ""} (${ir.dsp_regs}) · ${ir.acc_in_dsp && xc ? "DSP post-adder" : "fabric"} accumulate, ${ir.acc_width} b · latency ${row.latency_cycles ?? "?"} cycles</span></div>`));
  }
  p.appendChild(sec);
  const ks = el(`<div class="sec"><h3>IR <span class="hint">click a knob to suggest a change</span></h3><div class="knobs"></div></div>`);
  const kl = ks.querySelector(".knobs");
  for (const [k, v] of Object.entries(ir)) {
    if (k === "coefs") continue;
    const lockd = k in locked, changed = !lockd && prev && JSON.stringify(prev[k]) !== JSON.stringify(v);
    const kn = el(`<div class="knob ${lockd ? "locked" : changed ? "changed" : ""}"><span>${esc(k)}</span><span class="v">${esc(JSON.stringify(v))}</span></div>`);
    if (!lockd) kn.onclick = () => { $("#steer").value = `next iteration: change ${k} (currently ${JSON.stringify(v)}) and keep everything else`; $("#steer").focus(); };
    kl.appendChild(kn);
  }
  p.appendChild(ks);
  const src = row.pending ? done.at(-1) : row, cp = src?.metrics?.critical_path;
  if (cp?.length > 1) {
    const total = cp[0].total_ns || 1;
    const ps = el(`<div class="sec"><h3>Critical path <span class="hint">iteration ${src.n} · ${fmt(total, 2)} ns</span></h3><div class="pbars"></div><div class="plegend"><span><i style="background:var(--accent)"></i>logic</span><span><i style="background:var(--lut)"></i>route</span></div></div>`);
    const bars = ps.querySelector(".pbars");
    for (const s of cp.slice(1)) {
      const name = `${s.kind} ${s.rtl.split(".").slice(-2).join(".").slice(-26)}`;
      bars.appendChild(el(`<div class="pbar" title="${esc(s.rtl)}"><span>${esc(name)}</span><div class="track">${s.logic_ns ? `<div class="l" style="width:${(s.logic_ns / total) * 100}%"></div>` : ""}${s.route_ns ? `<div class="rt" style="width:${(s.route_ns / total) * 100}%"></div>` : ""}</div><span class="v">${fmt(s.logic_ns + s.route_ns, 2)} ns</span></div>`));
    }
    p.appendChild(ps);
  }
}

// ---------------------------------------------------------------- inspector: trajectory
const NS = "http://www.w3.org/2000/svg";
const svgEl = (t, a = {}) => { const e = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); return e; };
const tip = $("#tip");
const showTip = (ev, text) => { tip.hidden = false; tip.textContent = text; tip.style.left = `${ev.clientX + 14}px`; tip.style.top = `${ev.clientY + 14}px`; };
const hideTip = () => { tip.hidden = true; };

function chart(container, rows, key, target, label, color, opts = {}) {
  container.innerHTML = "";
  const W = container.clientWidth || 360, H = container.clientHeight || 180, L = 40, Rm = 12, T = 12, B = 22;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` }); container.appendChild(svg);
  if (!rows.length) return;
  const vals = rows.map((r) => (opts.get ? opts.get(r) : r.metrics[key]));
  const max = Math.max(target || 0, ...vals) * 1.12 || 1;
  const X = (i) => L + ((i + 0.5) / rows.length) * (W - L - Rm), Y = (v) => H - B - (v / max) * (H - B - T);
  const g = svgEl("g", { class: "grid" });
  for (let i = 0; i <= 3; i++) { const v = (max * i) / 3, y = Y(v); g.appendChild(svgEl("line", { x1: L, x2: W - Rm, y1: y, y2: y })); const t = svgEl("text", { x: L - 5, y: y + 3, "text-anchor": "end" }); t.textContent = Number.isInteger(max) || max > 10 ? Math.round(v) : v.toFixed(2); svg.appendChild(t); }
  svg.appendChild(g);
  if (target != null) { svg.appendChild(svgEl("line", { class: "target", x1: L, x2: W - Rm, y1: Y(target), y2: Y(target) })); const t = svgEl("text", { x: W - Rm, y: Y(target) - 3, "text-anchor": "end" }); t.textContent = `${label} ${opts.limitKind || "≥"} ${target}`; svg.appendChild(t); }
  if (opts.line) svg.appendChild(svgEl("path", { d: rows.map((r, i) => `${i ? "L" : "M"}${X(i)},${Y(vals[i])}`).join(" "), fill: "none", stroke: color, "stroke-width": 2 }));
  rows.forEach((r, i) => {
    const ok = r.violations.length === 0, v = vals[i];
    if (opts.line) svg.appendChild(svgEl("circle", { cx: X(i), cy: Y(v), r: 4.5, fill: ok ? "var(--good)" : "var(--surface)", stroke: ok ? "var(--good)" : "var(--bad)", "stroke-width": 2 }));
    else { const slot = (W - L - Rm) / rows.length, bw = Math.min(22, slot - 3); svg.appendChild(svgEl("rect", { x: X(i) - bw / 2, y: Y(v), width: bw, height: Math.max(H - B - Y(v), 0), rx: 2, fill: target != null && v > target ? "var(--bad)" : color })); }
    const t = svgEl("text", { x: X(i), y: H - 6, "text-anchor": "middle" }); t.textContent = r.n; svg.appendChild(t);
    const hit = svgEl("rect", { class: "hit", x: X(i) - (W - L - Rm) / rows.length / 2, y: T, width: (W - L - Rm) / rows.length, height: H - T - B });
    hit.onmousemove = (ev) => showTip(ev, `iteration ${r.n}\n${label} ${typeof v === "number" ? fmt(v, key === "fmax_mhz" ? 1 : key ? 0 : 3) : v}\n${ok ? "feasible" : r.violations.join("\n")}`);
    hit.onmouseleave = hideTip; svg.appendChild(hit);
  });
}

function renderTrajectory(done, c, unit) {
  const p = $("#pane-trajectory"); p.innerHTML = "";
  if (!done.length) { p.appendChild(el(`<div class="muted">no results yet</div>`)); return; }
  for (const [title, key, target, label, color, opts] of [
    ["Fmax", "fmax_mhz", c.fmax_mhz, "MHz", "var(--accent)", { line: true }],
    ["LUT", "lut", c.max_lut, "LUT", "var(--lut)", { limitKind: "≤" }],
    ["DSP", "dsp", c.max_dsp, "DSP", "var(--dsp)", { limitKind: "≤" }],
    [`Throughput (${unit})`, null, null, unit, "var(--text-2)", { get: (r) => r.throughput }],
  ]) {
    const sec = el(`<div class="sec"><h3>${title}</h3><div class="chart"></div></div>`);
    p.appendChild(sec);
    requestAnimationFrame(() => chart(sec.querySelector(".chart"), done, key, target, label, color, opts));
  }
}

// ---------------------------------------------------------------- memory
function lessonCard(l, design, backend, withSit) {
  const node = el(`<div class="lesson"><div><div class="ltxt">${esc(l.text)}</div>${withSit ? `<div class="sit">${esc((l.situation?.violations || []).join(","))} · critical: ${esc(l.situation?.critical || "")} · ${esc((l.situation?.structure || []).join(", "))} · from ${esc(l.run_id || "")}#${l.iteration ?? ""} · used ${l.retrieved ?? 0}×</div>` : ""}</div><div class="vote"><span class="sc">${l.score >= 0 ? "+" : ""}${fmt(l.score)}</span><button data-v="up">👍</button><button data-v="down">👎</button></div></div>`);
  node.querySelectorAll("button").forEach((btn) => { btn.onclick = async () => { const r = await post(`/memory/${design}/${backend}/vote`, { id: l.id, vote: btn.dataset.v }); node.querySelector(".sc").textContent = `${r.score >= 0 ? "+" : ""}${fmt(r.score)}`; refreshMemCount(); }; });
  return node;
}

function renderMemoryTab(rows) {
  const p = $("#pane-memory"); p.innerHTML = "";
  const spec = detail.spec, design = spec.design || "matmul", backend = spec.backend || "ecp5";
  const recalled = rows.flatMap((r) => r.memory?.lessons || []);
  const learned = rows.filter((r) => r.lesson).map((r) => r.lesson.lesson);
  if (!recalled.length && !learned.length) { p.appendChild(el(`<div class="muted">${spec.no_memory ? "memory disabled for this run (A/B)" : "nothing recalled or learned yet"}</div>`)); return; }
  if (learned.length) { const s = el(`<div class="sec"><h3>Learned in this run</h3></div>`); for (const l of learned) s.appendChild(lessonCard({ ...l, score: l.outcome === "helped" ? 1 : l.outcome === "hurt" ? -1.5 : 0 }, design, backend)); p.appendChild(s); }
  if (recalled.length) { const s = el(`<div class="sec"><h3>Recalled</h3></div>`); const ids = new Set(); for (const l of recalled) { if (ids.has(l.id)) continue; ids.add(l.id); s.appendChild(lessonCard(l, design, backend)); } p.appendChild(s); }
}

async function renderMemoryView() {
  const list = $("#memory-list"); list.innerHTML = `<div class="muted">loading…</div>`;
  memCache = await api("/memory");
  list.innerHTML = "";
  if (!memCache.length) { list.appendChild(el(`<div class="muted">empty — lessons appear after the first run with two or more iterations</div>`)); return; }
  for (const g of memCache) {
    const grp = el(`<div class="memgroup"><h3>${esc(g.design)} · ${esc(g.backend)} <span class="muted">${g.count} lessons</span></h3></div>`);
    for (const l of g.lessons) grp.appendChild(lessonCard(l, g.design, g.backend, true));
    list.appendChild(grp);
  }
}
