// silicon-agent console: plain ES module, no build step, no dependencies.
// Talks to /api/*; sluice (or a bearer token for the CLI) handles auth upstream.

const $ = (s) => document.querySelector(s);
const api = async (path, opts) => {
  const r = await fetch(`/api${path}`, { cache: "no-store", ...opts });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
};
const fmt = (n, d = 1) => (n == null ? "–" : Number(n).toFixed(d));
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// ---------------------------------------------------------------- state
let jobs = [];
let selected = null;        // job id
let detail = null;          // {status, events, spec, summary}
let seen = 0;               // events consumed for the selected job
let pollTimer = null;

// ---------------------------------------------------------------- boot
(async function boot() {
  try { const me = await api("/me"); $("#who").textContent = me.subject; } catch { $("#who").textContent = "unauthenticated"; }
  const specs = await api("/specs");
  const tpl = $("#tpl");
  for (const s of specs) {
    const o = document.createElement("option");
    o.value = s.name; o.textContent = `${s.design} · ${s.backend} · ${s.task}`; o.dataset.spec = JSON.stringify(s.spec, null, 2);
    tpl.appendChild(o);
  }
  tpl.onchange = () => { $("#spec").value = tpl.selectedOptions[0].dataset.spec; };
  tpl.onchange();
  $("#submit").onclick = submit;
  await refreshJobs();
  setInterval(refreshJobs, 5000);
  const hash = location.hash.slice(1);
  if (hash) select(hash);
})();

async function submit() {
  const msg = $("#submit-msg"); msg.className = "msg"; msg.textContent = "submitting…";
  let spec;
  try { spec = JSON.parse($("#spec").value); } catch (e) { msg.className = "msg err"; msg.textContent = `spec is not valid JSON: ${e.message}`; return; }
  try {
    const r = await api("/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ spec, max_iter: Number($("#maxiter").value) }) });
    msg.textContent = `queued ${r.id}`;
    await refreshJobs();
    select(r.id);
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
}

// ---------------------------------------------------------------- job list
async function refreshJobs() {
  jobs = await api("/jobs");
  const ul = $("#jobs");
  ul.innerHTML = "";
  for (const j of jobs) {
    const li = document.createElement("li");
    li.className = j.id === selected ? "sel" : "";
    const best = j.best?.metrics;
    li.innerHTML = `<span class="dot ${j.status}"></span>
      <div><div class="t">${esc(j.task || j.id)}</div><div class="m">${esc(j.design)} · ${esc(j.backend)} · ${j.iterations} iter · ${esc(j.id)}</div></div>
      <div class="r">${best ? `${fmt(best.fmax_mhz, 0)} MHz<br>${best.dsp} DSP` : esc(j.status)}</div>`;
    li.onclick = () => select(j.id);
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- detail
async function select(id) {
  selected = id; seen = 0; detail = null;
  location.hash = id;
  clearTimeout(pollTimer);
  document.querySelectorAll("#jobs li").forEach((li) => li.classList.toggle("sel", li.querySelector(".m")?.textContent.includes(id)));
  $("#empty").hidden = true; $("#job").hidden = false;
  $("#log").innerHTML = ""; $("#best-rtl").textContent = "loading…";
  await poll();
}

async function poll() {
  if (!selected) return;
  const d = await api(`/jobs/${selected}?after=${seen}`);
  if (!detail) detail = { ...d, events: [] };
  detail.status = d.status; detail.summary = d.summary; detail.error = d.error;
  detail.events.push(...d.events);
  seen = d.n_events;
  render();
  if (["queued", "running"].includes(d.status)) pollTimer = setTimeout(poll, 2500);
}

function iterations() {
  // fold the event stream into one row per iteration: {n, ir, metrics, violations, diagnosis, action, plan}
  const rows = new Map();
  let pendingPlan = null;
  for (const e of detail.events) {
    if (e.kind === "evaluating") rows.set(e.iteration, { n: e.iteration, ir: e.ir, plan: pendingPlan, pending: true });
    if (e.kind === "result") { const r = rows.get(e.iteration) || { n: e.iteration, ir: e.ir }; Object.assign(r, e, { pending: false }); rows.set(e.iteration, r); }
    if (e.kind === "plan") { pendingPlan = e; const r = rows.get(e.iteration - 1); if (r) r.next = e; }
  }
  return [...rows.values()].sort((a, b) => a.n - b.n);
}

function render() {
  const spec = detail.spec, c = spec.constraints, rows = iterations();
  const done = rows.filter((r) => !r.pending);
  const best = detail.summary?.best || bestOf(done, c);
  const unit = detail.summary?.throughput_unit || (spec.design === "fir" ? "samples/cycle" : "MAC/cycle");

  $("#j-task").textContent = spec.task || selected;
  $("#j-meta").textContent = `${spec.design || "matmul"} · ${spec.backend || "ecp5"} · ${selected}${detail.subject ? " · by " + detail.subject : ""}`;
  const v = $("#j-verdict"); v.className = `verdict ${detail.status}`;
  v.textContent = { queued: "QUEUED", running: `RUNNING · iteration ${rows.length}`, done: `PASS · ${done.length} iterations`, failed: `FAIL · no feasible design`, error: "ERROR", interrupted: "INTERRUPTED" }[detail.status] || detail.status;

  renderTiles(best || done.at(-1), c, unit, !!best);
  renderFmax(done, c.fmax_mhz);
  renderBars($("#c-lut"), done, "lut", c.max_lut, "LUT", "var(--series-2)");
  renderBars($("#c-dsp"), done, "dsp", c.max_dsp, "DSP", "var(--series-3)");
  renderPareto(done, unit);
  renderLog(rows, unit);
  if (best) {
    $("#best-ir").textContent = JSON.stringify(Object.fromEntries(Object.entries(best.ir).filter(([k]) => k !== "coefs")), null, 2);
    if ($("#best-rtl").textContent === "loading…") api(`/jobs/${selected}/rtl`).then((t) => { $("#best-rtl").textContent = t; }).catch(() => {});
  } else {
    $("#best-ir").textContent = detail.error ? detail.error : "no feasible design yet";
  }
}

function bestOf(rows, c) {
  const feas = rows.filter((r) => r.violations && r.violations.length === 0);
  if (!feas.length) return null;
  const key = (r) => (c.objective || []).map((o) => ({ maximize_throughput: -r.throughput, maximize_fmax: -r.metrics.fmax_mhz, minimize_lut: r.metrics.lut, minimize_dsp: r.metrics.dsp, minimize_latency: r.latency_cycles }[o] ?? 0));
  return feas.sort((a, b) => { const ka = key(a), kb = key(b); for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i]; return 0; })[0];
}

// ---------------------------------------------------------------- tiles
function renderTiles(row, c, unit, feasible) {
  const el = $("#tiles"); el.innerHTML = "";
  if (!row) return;
  const m = row.metrics;
  const tiles = [
    ["Fmax", `${fmt(m.fmax_mhz)} MHz`, `≥ ${c.fmax_mhz} MHz`, m.fmax_mhz >= c.fmax_mhz],
    ["DSP", m.dsp, c.max_dsp != null ? `≤ ${c.max_dsp}` : "unconstrained", c.max_dsp == null || m.dsp <= c.max_dsp],
    ["LUT", m.lut, c.max_lut != null ? `≤ ${c.max_lut}` : "unconstrained", c.max_lut == null || m.lut <= c.max_lut],
    ["FF", m.ff, c.max_ff != null ? `≤ ${c.max_ff}` : "", c.max_ff == null || m.ff <= c.max_ff],
    ["Throughput", row.throughput, unit, null],
    ["Latency", row.latency_cycles, "cycles", null],
    ["Simulation", m.sim_passed ? "pass" : "FAIL", "vs golden model", m.sim_passed],
  ];
  for (const [k, v, cc, ok] of tiles) {
    const d = document.createElement("div");
    d.className = `tile ${ok === null ? "na" : ok ? "ok" : "bad"}`;
    d.innerHTML = `<div class="k">${k}</div><div class="v">${esc(v)}</div><div class="c">${esc(cc)}</div>`;
    el.appendChild(d);
  }
}

// ---------------------------------------------------------------- charts (hand-rolled SVG)
const NS = "http://www.w3.org/2000/svg";
const svgEl = (t, a = {}) => { const e = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); return e; };
const tip = $("#tip");
const showTip = (ev, text) => { tip.hidden = false; tip.textContent = text; tip.style.left = `${ev.clientX + 14}px`; tip.style.top = `${ev.clientY + 14}px`; };
const hideTip = () => { tip.hidden = true; };

function frame(container, { left = 44, right = 16, top = 14, bottom = 24 } = {}) {
  container.innerHTML = "";
  const W = container.clientWidth || 400, H = container.clientHeight || 200;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  container.appendChild(svg);
  return { svg, W, H, x0: left, x1: W - right, y0: top, y1: H - bottom };
}

function yAxis(f, min, max, ticks = 4, suffix = "") {
  const g = svgEl("g", { class: "axis" }), gr = svgEl("g", { class: "grid" });
  for (let i = 0; i <= ticks; i++) {
    const v = min + ((max - min) * i) / ticks, y = f.y1 - ((v - min) / (max - min || 1)) * (f.y1 - f.y0);
    gr.appendChild(svgEl("line", { x1: f.x0, x2: f.x1, y1: y, y2: y }));
    const t = svgEl("text", { x: f.x0 - 6, y: y + 4, "text-anchor": "end" }); t.textContent = Math.round(v) + suffix; g.appendChild(t);
  }
  f.svg.appendChild(gr); f.svg.appendChild(g);
}

function renderFmax(rows, target) {
  const f = frame($("#c-fmax"));
  if (!rows.length) return;
  const vals = rows.map((r) => r.metrics.fmax_mhz);
  const max = Math.max(target * 1.15, ...vals) * 1.05, min = 0;
  yAxis(f, min, max, 4);
  const X = (i) => f.x0 + ((i + 0.5) / rows.length) * (f.x1 - f.x0);
  const Y = (v) => f.y1 - ((v - min) / (max - min)) * (f.y1 - f.y0);
  f.svg.appendChild(svgEl("line", { class: "target", x1: f.x0, x2: f.x1, y1: Y(target), y2: Y(target) }));
  const tl = svgEl("text", { class: "lbl", x: f.x1, y: Y(target) - 4, "text-anchor": "end" }); tl.textContent = `target ${target}`; f.svg.appendChild(tl);
  const path = svgEl("path", { d: rows.map((r, i) => `${i ? "L" : "M"}${X(i)},${Y(r.metrics.fmax_mhz)}`).join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": 2 });
  f.svg.appendChild(path);
  rows.forEach((r, i) => {
    const ok = r.violations.length === 0;
    f.svg.appendChild(svgEl("circle", { cx: X(i), cy: Y(r.metrics.fmax_mhz), r: 5, fill: ok ? "var(--good)" : "var(--surface-1)", stroke: ok ? "var(--good)" : "var(--critical)", "stroke-width": 2 }));
    const t = svgEl("text", { class: "axis", x: X(i), y: f.y1 + 16, "text-anchor": "middle" }); t.textContent = r.n; t.style.fill = "var(--text-muted)"; t.style.font = "11px var(--mono)"; f.svg.appendChild(t);
    const hit = svgEl("rect", { class: "hit", x: X(i) - (f.x1 - f.x0) / rows.length / 2, y: f.y0, width: (f.x1 - f.x0) / rows.length, height: f.y1 - f.y0 });
    hit.onmousemove = (ev) => showTip(ev, `iteration ${r.n}\nfmax ${fmt(r.metrics.fmax_mhz)} MHz\n${ok ? "feasible" : r.violations.join("\n")}`);
    hit.onmouseleave = hideTip;
    f.svg.appendChild(hit);
  });
}

function renderBars(container, rows, key, limit, label, color) {
  const f = frame(container);
  if (!rows.length) return;
  const vals = rows.map((r) => r.metrics[key]);
  const max = Math.max(limit || 0, ...vals) * 1.1 || 1;
  yAxis(f, 0, max, 3);
  const slot = (f.x1 - f.x0) / rows.length, bw = Math.min(28, slot - 2);
  const Y = (v) => f.y1 - (v / max) * (f.y1 - f.y0);
  if (limit != null) {
    f.svg.appendChild(svgEl("line", { class: "target", x1: f.x0, x2: f.x1, y1: Y(limit), y2: Y(limit) }));
    const tl = svgEl("text", { class: "lbl", x: f.x1, y: Y(limit) - 4, "text-anchor": "end" }); tl.textContent = `${label} ≤ ${limit}`; f.svg.appendChild(tl);
  }
  rows.forEach((r, i) => {
    const v = r.metrics[key], x = f.x0 + slot * i + (slot - bw) / 2, over = limit != null && v > limit;
    const h = Math.max(f.y1 - Y(v), 0);
    const bar = svgEl("rect", { x, y: Y(v), width: bw, height: h, rx: 3, fill: over ? "var(--critical)" : color });
    bar.onmousemove = (ev) => showTip(ev, `iteration ${r.n}\n${label} ${v}${limit != null ? ` (limit ${limit})` : ""}`);
    bar.onmouseleave = hideTip;
    f.svg.appendChild(bar);
    const t = svgEl("text", { x: x + bw / 2, y: f.y1 + 16, "text-anchor": "middle" }); t.textContent = r.n; t.style.fill = "var(--text-muted)"; t.style.font = "11px var(--mono)"; f.svg.appendChild(t);
  });
  const lb = svgEl("text", { class: "lbl", x: f.x0, y: f.y0 + 2 }); lb.textContent = label; f.svg.appendChild(lb);
}

function renderPareto(rows, unit) {
  const f = frame($("#c-pareto"), { left: 52, bottom: 30 });
  if (!rows.length) return;
  const xs = rows.map((r) => r.metrics.lut), ys = rows.map((r) => r.throughput);
  const xmax = Math.max(...xs) * 1.15 || 1, ymax = Math.max(...ys) * 1.2 || 1;
  yAxis(f, 0, ymax, 4);
  const X = (v) => f.x0 + (v / xmax) * (f.x1 - f.x0), Y = (v) => f.y1 - (v / ymax) * (f.y1 - f.y0);
  const ax = svgEl("g", { class: "axis" });
  for (let i = 0; i <= 4; i++) { const v = (xmax * i) / 4; const t = svgEl("text", { x: X(v), y: f.y1 + 16, "text-anchor": "middle" }); t.textContent = Math.round(v); ax.appendChild(t); }
  f.svg.appendChild(ax);
  const xl = svgEl("text", { class: "lbl", x: f.x1, y: f.y1 + 28, "text-anchor": "end" }); xl.textContent = "LUT →"; f.svg.appendChild(xl);
  const yl = svgEl("text", { class: "lbl", x: f.x0, y: f.y0 - 0 }); yl.textContent = `${unit} ↑`; f.svg.appendChild(yl);
  // feasible frontier: sort feasible points by LUT, keep those with strictly increasing throughput
  const feas = rows.filter((r) => r.violations.length === 0).sort((a, b) => a.metrics.lut - b.metrics.lut);
  const front = []; let bestT = -Infinity;
  for (const r of feas) if (r.throughput > bestT) { front.push(r); bestT = r.throughput; }
  if (front.length > 1) f.svg.appendChild(svgEl("path", { d: front.map((r, i) => `${i ? "L" : "M"}${X(r.metrics.lut)},${Y(r.throughput)}`).join(" "), fill: "none", stroke: "var(--good)", "stroke-width": 1.5, "stroke-dasharray": "3 3" }));
  // coincident points (same LUT and throughput) are fanned out along a small arc so every iteration stays visible
  const buckets = new Map();
  rows.forEach((r) => { const k = `${r.metrics.lut}|${r.throughput}`; buckets.set(k, [...(buckets.get(k) || []), r]); });
  const offset = (r) => { const b = buckets.get(`${r.metrics.lut}|${r.throughput}`); if (b.length === 1) return [0, 0]; const i = b.indexOf(r), a = (i / b.length) * 2 * Math.PI; return [Math.cos(a) * 14, Math.sin(a) * 14]; };
  rows.forEach((r) => {
    const ok = r.violations.length === 0, [dx, dy] = offset(r), cx = X(r.metrics.lut) + dx, cy = Y(r.throughput) + dy;
    if (dx || dy) f.svg.appendChild(svgEl("line", { x1: X(r.metrics.lut), y1: Y(r.throughput), x2: cx, y2: cy, stroke: "var(--border)" }));
    const dot = svgEl("circle", { cx, cy, r: 9, fill: ok ? "var(--series-1)" : "var(--surface-1)", stroke: ok ? "var(--series-1)" : "var(--critical)", "stroke-width": 2 });
    dot.onmousemove = (ev) => showTip(ev, `iteration ${r.n}\n${r.throughput} ${unit}, ${r.metrics.lut} LUT, ${r.metrics.dsp} DSP, ${fmt(r.metrics.fmax_mhz)} MHz\n${ok ? "feasible" : r.violations.join("\n")}`);
    dot.onmouseleave = hideTip;
    f.svg.appendChild(dot);
    const t = svgEl("text", { x: cx, y: cy + 4, "text-anchor": "middle" }); t.textContent = r.n; t.style.fill = ok ? "#fff" : "var(--text-primary)"; t.style.font = "600 10px var(--mono)"; f.svg.appendChild(t);
  });
}

// ---------------------------------------------------------------- log
function renderLog(rows, unit) {
  const ol = $("#log"); ol.innerHTML = "";
  for (const r of rows) {
    const li = document.createElement("li");
    const locked = detail.spec.locked || {};
    const ir = Object.entries(r.ir).filter(([k]) => !(k in locked) && k !== "coefs").map(([k, v]) => `${k}=${JSON.stringify(v)}`).join("  ");
    let res;
    if (r.pending) res = `<div class="res pending">simulating / synthesizing / place &amp; route…</div>`;
    else {
      const m = r.metrics, ok = r.violations.length === 0;
      res = `<div class="res"><span class="${ok ? "ok" : "bad"}">${ok ? "FEASIBLE" : "VIOLATES"}</span> · fmax ${fmt(m.fmax_mhz)} MHz · LUT ${m.lut} · FF ${m.ff} · DSP ${m.dsp} · ${r.throughput} ${unit} · sim ${m.sim_passed ? "pass" : "FAIL"} · ${r.eda_seconds}s${ok ? "" : `<br>${esc(r.violations.join("; "))}`}</div>`;
      if (m.critical_path?.length > 1) res += `<div class="cp">critical path ${fmt(m.critical_path[0].total_ns, 2)} ns: ` + m.critical_path.slice(1).map((s) => `<span>${esc(s.kind)} <i>${esc(s.rtl)}</i> ${fmt(s.logic_ns, 2)}+${fmt(s.route_ns, 2)}</span>`).join("") + `</div>`;
    }
    // the plan that produced this iteration: live runs carry it as a `plan` event, reconstructed
    // runs carry the same text on the result itself (diagnosis/action fields)
    const diag = r.plan?.diagnosis ?? r.diagnosis, act = r.plan?.action ?? r.action;
    const plan = r.n === 1 ? `<div class="diag"><b>Start</b> initial design from the spec</div>`
      : (diag || act) ? `<div class="diag"><b>Hypothesis</b> ${esc(diag || "")}<br><b>Action</b> ${esc(act || "")}</div>` : "";
    const stop = r.next?.stop ? `<div class="diag"><b>Planner</b> ${esc(r.next.diagnosis)}<br><b>Decision</b> ${esc(r.next.action)} <b>· stop</b></div>`
      : (r.next && !r.next.stop && rows.indexOf(r) === rows.length - 1) ? `<div class="diag pending">planner proposed iteration ${r.next.iteration}: ${esc(r.next.action)}</div>` : "";
    li.innerHTML = `<div class="n">${r.n}</div><div>${plan}<div class="ir">${esc(ir)}</div>${res}${stop}</div>`;
    ol.appendChild(li);
  }
}
