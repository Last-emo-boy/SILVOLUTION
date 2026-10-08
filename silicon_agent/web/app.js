// silicon-agent worktable. Plain ES module, no build step.
import { wire } from "/static/form.js";
// Codex-like: one calm transcript (a turn per iteration), a composer, details behind toggles.

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
const STAGES = [["behavioral", "behavioral"], ["post_synth_func", "post-synth"], ["post_synth_time", "post-synth timing"], ["post_impl_func", "post-impl"], ["post_impl_time", "post-impl timing"]];

// ---------------------------------------------------------------- state
// /work?thread=<id> scopes the worktable to one conversation: only its runs are listed, and every
// run created here (new, fork, re-run) is bound to it. Without ?thread it is the global view.
const THREAD = new URLSearchParams(location.search).get("thread");
let jobs = [], selected = null, detail = null, seen = 0, pollTimer = null, specs = [];
let view = "empty";
const state = { get jobs() { return jobs; }, get detail() { return detail; }, get selected() { return selected; }, cmpA: null, cmpB: null, thread: THREAD };
let ext = null;
const openTurns = new Set(); // iteration numbers whose details are expanded

// ---------------------------------------------------------------- boot
(async function boot() {
  try { $("#who").textContent = (await api("/me")).subject; } catch { $("#who").textContent = "unauthenticated"; }
  specs = await api("/specs");
  try { BFACTS = (await api("/schema")).backend_facts; BSTAGES = Object.fromEntries(Object.entries(BFACTS).map(([k, v]) => [k, v.sim_stages])); } catch {}
  ext = wire({ $, api, post, esc, el, fmt, specs, state, select, refreshJobs, show });
  await ext.initForm();
  ext.initActions();
  $("#new-run").onclick = () => show("new");
  $("#open-memory").onclick = () => show("memory");
  $("#send").onclick = sendSteer;
  $("#steer").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendSteer(); });
  $("#steer").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"; });
  $("#btn-stop").onclick = async () => { if (selected) { await post(`/jobs/${selected}/stop`); $("#btn-stop").disabled = true; } };
  $("#t-insp").onclick = () => toggleInspector();
  $("#close-insp").onclick = () => toggleInspector(false);
  $("#t-rail").onclick = () => { const sh = $(".shell"); if (window.innerWidth <= 800) sh.classList.toggle("rail-shown"); else sh.classList.toggle("rail-hidden"); };
  document.querySelectorAll(".tabs button[data-tab]").forEach((b) => { b.onclick = () => switchTab(b.dataset.tab); });
  refreshMemCount();
  if (THREAD) {
    try {
      const th = await api(`/threads/${THREAD}`);
      const head = $(".rail-head"); head.querySelector(".brand").innerHTML = `<a class="back" href="/#${esc(THREAD)}" title="Back to the conversation">‹</a> <span class="logo">▣</span> <span class="tt" title="${esc(th.title)}">${esc(th.title || THREAD)}</span>`;
      document.title = `${th.title || THREAD} · worktable`;
    } catch { $(".rail-head .brand").insertAdjacentHTML("beforeend", ` <span class="muted">(thread not found)</span>`); }
  }
  await refreshJobs();
  setInterval(refreshJobs, 5000);
  const hash = location.hash.slice(1);
  if (hash === "memory") show("memory"); else if (hash === "new") show("new"); else if (hash.startsWith("compare/")) { const [, a, b] = hash.split("/"); ext.openCompare(a, b); }
  else if (hash && (!THREAD || jobs.some((j) => j.id === hash))) select(hash);
  else if (THREAD && jobs.length) select(jobs[0].id);
  else show("empty");
})();

function show(v) {
  view = v;
  $("#empty").hidden = v !== "empty"; $("#new-form").hidden = v !== "new"; $("#run").hidden = v !== "run"; $("#memory").hidden = v !== "memory"; $("#compare").hidden = v !== "compare";
  if (v !== "run") { selected = null; clearTimeout(pollTimer); document.querySelectorAll("#runs li").forEach((li) => li.classList.remove("sel")); }
  if (v === "memory") { location.hash = "memory"; renderMemoryView(); }
  if (v === "new") location.hash = "new";
}
function toggleInspector(force) {
  const on = force ?? !$("#split").classList.contains("with-insp");
  $("#split").classList.toggle("with-insp", on);
  $("#t-insp").textContent = on ? "Details ◂" : "Details ▸";
  localStorage.setItem("sa.insp", on ? "1" : "0");
}
async function refreshMemCount() {
  try { const m = await api("/memory"); $("#mem-count").textContent = m.reduce((a, g) => a + g.count, 0); } catch {}
}

// ---------------------------------------------------------------- rail
async function refreshJobs() {
  jobs = await api(`/jobs?thread=${encodeURIComponent(THREAD || "")}`);  // "" = runs that belong to no conversation
  const ul = $("#runs"); ul.innerHTML = "";
  for (const j of jobs) {
    const b = j.best?.metrics;
    const status = j.status === "done" && b ? `${fmt(b.fmax_mhz, 0)} MHz · ${b.dsp} DSP · ${j.iterations} iter` : j.status === "running" ? `running · iter ${j.iterations + 1}` : `${j.status} · ${j.iterations} iter`;
    const li = el(`<li class="${j.id === selected ? "sel" : ""}" data-id="${esc(j.id)}" title="${esc(j.task || j.id)}"><span class="dot ${j.status}"></span><div><div class="t">${esc(j.task || j.id)}</div><div class="m">${esc(j.backend)} · ${status}</div></div></li>`);
    li.onclick = () => select(j.id);
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- run view
async function select(id) {
  if (THREAD && !jobs.some((j) => j.id === id)) { await refreshJobs(); if (!jobs.some((j) => j.id === id)) { show("empty"); location.hash = ""; return; } }
  selected = id; seen = 0; detail = null; openTurns.clear();
  location.hash = id;
  clearTimeout(pollTimer);
  document.querySelectorAll("#runs li").forEach((li) => li.classList.toggle("sel", li.dataset.id === id));
  $("#empty").hidden = true; $("#new-form").hidden = true; $("#memory").hidden = true; $("#compare").hidden = true; $("#run").hidden = false; view = "run";
  $("#transcript").innerHTML = ""; $("#rtl").textContent = "";
  toggleInspector(localStorage.getItem("sa.insp") === "1");
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
  const rows = new Map(), notes = [];
  let pendingMem = null, pendingSteer = [];
  const key = (e) => `${e.iteration}.${e.slot || 0}`;
  const get = (e) => { const k = key(e); if (!rows.has(k)) rows.set(k, { n: e.iteration, slot: e.slot || 0, pending: true, user: [], stages: {} }); return rows.get(k); };
  for (const e of detail.events) {
    switch (e.kind) {
      case "user": pendingSteer.push(e); break;
      case "note": if (e.iteration) get({ iteration: e.iteration }).note = e.message; else notes.push(e); break;
      case "steer": get({ iteration: e.iteration }).user.push(...pendingSteer.splice(0)); break;
      case "memory": pendingMem = e; break;
      case "rescreen": get({ iteration: e.iteration }).rescreen = e; break;
      case "plan": {
        const prev = rows.get(`${e.iteration - 1}.0`); if (prev) prev.next = e;
        if (!e.stop) { const r = get({ iteration: e.iteration }); r.plan = e; if (pendingMem) { r.memory = pendingMem; pendingMem = null; } }
        break;
      }
      case "evaluating": { const r = get(e); r.ir = e.ir; if (pendingMem && !e.slot) { r.memory = pendingMem; pendingMem = null; } break; }
      case "stage": { const r = get(e); r.stages[e.stage] = e; break; }
      case "result": Object.assign(get(e), e, { pending: false }); break;
      case "lesson": get({ iteration: e.iteration }).lesson = e; break;
    }
  }
  const out = [...rows.values()].sort((a, b) => a.n - b.n || a.slot - b.slot);
  if (pendingSteer.length && out.length) out.at(-1).userAfter = pendingSteer;
  if (notes.length && out.length) out[0].notes = notes;
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
  const liveRow = rows.find((r) => r.pending && !r.slot) || rows.find((r) => r.pending);

  $("#j-task").textContent = spec.task || selected;
  const pill = $("#j-pill"); pill.className = `pill ${detail.status}`;
  const nRounds = new Set(done.map((r) => r.n)).size;
  pill.textContent = { queued: "queued", running: liveRow ? `iteration ${liveRow.n}${rows.filter((r) => r.pending).length > 1 ? " ×" + rows.filter((r) => r.pending).length : ""}` : "running", done: `PASS · ${nRounds} iter${done.length > nRounds ? ` · ${done.length} designs` : ""}`, failed: `FAIL · ${nRounds} iter`, error: "error", interrupted: "interrupted" }[detail.status] || detail.status;
  $("#j-best").textContent = best ? `best: ${fmt(best.metrics.fmax_mhz, 0)} MHz · ${best.metrics.lut} LUT · ${best.metrics.dsp} DSP · ${best.throughput} ${unit}` : `target: ≥${c.fmax_mhz} MHz${c.max_dsp != null ? ` · ≤${c.max_dsp} DSP` : ""}${c.max_lut != null ? ` · ≤${c.max_lut} LUT` : ""}`;
  $("#btn-stop").hidden = !live; $("#btn-stop").disabled = false;
  let pl = $(".parent-link");
  if (detail.parent?.id) { if (!pl) { pl = el(`<div class="parent-link"></div>`); $("#transcript").before(pl); } const inScope = jobs.some((j) => j.id === detail.parent.id); pl.innerHTML = `forked from ${inScope ? `<a href="#${esc(detail.parent.id)}">` : "<span>"}${esc(jobs.find((j) => j.id === detail.parent.id)?.task || detail.parent.id)}${inScope ? "</a>" : "</span>"}${detail.parent.iteration ? ` · iteration ${detail.parent.iteration}` : ""}`; if (inScope) pl.querySelector("a").onclick = (e) => { e.preventDefault(); select(detail.parent.id); }; }
  else if (pl) pl.remove();
  renderTranscript(rows, unit, spec, c);
  $("#composer").classList.toggle("disabled", !live);
  $("#chint-r").textContent = `${spec.design || "matmul"} · ${spec.backend || "ecp5"} · ${selected}`;
  renderSugs(done.at(-1));
  renderDesign(liveRow?.ir ? liveRow : done.at(-1), done, spec);
  renderTrajectory(done, c, unit);
  renderSim(liveRow || done.at(-1), spec);
  if (best && !$("#rtl").textContent) api(`/jobs/${selected}/rtl`).then((t) => { $("#rtl").textContent = t; }).catch(() => {});
  renderMemoryTab(rows);
}

// ---------------------------------------------------------------- transcript
function irChips(ir, prevIr, locked) {
  if (Array.isArray(ir.stages)) {
    const ls = Array.isArray(locked?.stages) ? locked.stages : [];
    return ir.stages.map((st, i) => `<div class="chips"><span class="chip"><span class="k">s${i}</span></span>` + Object.entries(st).filter(([k]) => k !== "coefs" && !(ls[i] && k in ls[i])).map(([k, v]) => {
      const changed = prevIr?.stages?.[i] && JSON.stringify(prevIr.stages[i][k]) !== JSON.stringify(v);
      return `<span class="chip ${changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
    }).join("") + `</div>`).join("") + `<div class="chips">` + Object.entries(ir).filter(([k]) => k !== "stages").map(([k, v]) => `<span class="chip ${prevIr && JSON.stringify(prevIr[k]) !== JSON.stringify(v) ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`).join("") + `</div>`;
  }
  return `<div class="chips">` + Object.entries(ir).filter(([k]) => !(k in (locked || {})) && k !== "coefs").map(([k, v]) => {
    const changed = prevIr && JSON.stringify(prevIr[k]) !== JSON.stringify(v);
    return `<span class="chip ${changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
  }).join("") + `</div>`;
}
const line = (lbl, html, cls = "") => `<div class="ln"><span class="lbl ${cls}">${lbl}</span><div class="txt">${html}</div></div>`;
let BSTAGES = {}, BFACTS = {};
const stagesOf = (backend) => BSTAGES[backend] || ["behavioral", "post_synth_func"];
const stageChips = (r, backend) => {
  const keys = stagesOf(backend);
  if (keys.length < 2) return "";
  const st = r.pending ? r.stages : (r.metrics?.sim_stages || {});
  const behav = r.pending ? (r.ir ? "active" : "todo") : (r.metrics?.sim_passed ? "done" : "fail");
  const cls = (k) => { const s = st[k]; if (!s) return r.pending ? "todo" : "todo"; return s.passed === true ? "done" : s.passed === false ? "fail" : "todo"; };
  const parts = STAGES.filter(([k]) => keys.includes(k)).map(([k, l]) => `<span class="${k === "behavioral" ? behav : cls(k)}"><i></i>${l}</span>`);
  return `<div class="stages">${parts.join("")}</div>`;
};

function turnSignature(r) {
  // anything that changes what the turn shows; the open/closed state is deliberately not part of it
  return JSON.stringify([r.pending, r.slot, r.note, r.notes?.length, r.user.length, r.userAfter?.length, r.plan?.action, r.plan?.candidates?.length, r.plan?.rescreened, r.ir, r.violations, r.metrics?.fmax_mhz, r.metrics?.sim_stages && Object.keys(r.metrics.sim_stages).length, Object.keys(r.stages).map((k) => k + r.stages[k].passed), r.lesson?.lesson?.id, r.memory?.lessons?.length, r.next?.stop, r.diagnosis]);
}

function buildTurn(r, prevIr, unit, spec) {
  const li = el(`<li class="turn ${r.pending ? "live" : ""} ${r.slot ? "sib" : ""}" data-n="${r.n}.${r.slot}"><div class="num">${r.slot ? `${r.n}.${r.slot}` : r.n}</div><div class="body"></div></li>`);
  const b = li.querySelector(".body");
  for (const nt of r.notes || []) b.appendChild(el(`<div class="action"><span class="why">${esc(nt.message)}</span></div>`));
  for (const u of r.user) b.appendChild(el(`<div class="usermsg"><span class="u">you</span>${esc(u.message)}</div>`));
  const a = r.plan?.action ?? r.action;
  if (r.slot) b.appendChild(el(`<div class="action"><span class="why">sibling candidate (measured in parallel) — </span>${esc(r.action || "")}</div>`));
  else if (r.n === 1) b.appendChild(el(`<div class="action">Start from the spec's initial design.</div>`));
  else if (a) b.appendChild(el(`<div class="action">${r.plan?.fallback ? `<span class="why">fallback candidate — </span>` : ""}${esc(a)}</div>`));
  if (r.note) b.appendChild(el(`<div class="action"><span class="why">${esc(r.note)}</span></div>`));
  if (r.ir) b.appendChild(el(irChips(r.ir, prevIr, spec.locked)));
  if (r.pending) {
    b.appendChild(el(`<div class="head"><span class="verdict live">${r.ir ? "simulating · synthesizing · place & route" : "planning"}</span></div>`));
    if (r.ir) b.appendChild(el(stageChips(r, spec.backend)));
  } else {
    const m = r.metrics, ok = r.violations.length === 0;
    const seeds = m.fmax_seeds && m.fmax_seeds.length > 1 ? ` <span class="muted" title="${m.fmax_seeds.length} P&R seeds">${fmt(Math.min(...m.fmax_seeds), 0)}–${fmt(Math.max(...m.fmax_seeds), 0)}</span>` : "";
    b.appendChild(el(`<div class="head"><span class="verdict ${ok ? "ok" : "bad"}">${ok ? "FEASIBLE" : "VIOLATES"}</span><span class="nums"><b>${fmt(m.fmax_mhz)} MHz</b>${seeds} · ${m.lut} LUT · ${m.dsp} DSP${m.bram ? ` · ${m.bram} BRAM` : ""}${m.power_mw != null ? ` · ${m.power_mw} mW` : ""} · ${r.throughput} ${unit} · ${r.eda_seconds}s</span>${ok ? "" : `<span class="viol">${esc(r.violations.join(" · "))}</span>`}</div>`));
  }
  // details: grows in place under this turn; the list around it does not move
  const d = el(`<details class="more" ${openTurns.has(r.n) ? "open" : ""}><summary>details</summary><div class="inner"></div></details>`);
  d.addEventListener("toggle", () => { if (d.open) openTurns.add(r.n); else openTurns.delete(r.n); li.classList.toggle("open", d.open); });
  li.classList.toggle("open", d.open);
  const inner = d.querySelector(".inner"), parts = [];
  const diag = r.plan?.diagnosis ?? r.diagnosis;
  if (diag && r.n > 1) { inner.appendChild(el(line("hypothesis", esc(diag)))); parts.push("hypothesis"); }
  if (r.memory?.lessons?.length) { inner.appendChild(el(line("memory", r.memory.lessons.map((l) => `<div class="memrow"><span class="mid">${esc(l.id)} ${l.score >= 0 ? "+" : ""}${fmt(l.score)}</span>${esc(l.text)}</div>`).join(""), "accent"))); parts.push("memory"); }
  if (r.plan?.candidates?.length) {
    const picked = JSON.stringify(r.plan.next_ir);
    inner.appendChild(el(line("candidates", r.plan.candidates.map((cd) => {
      const isPick = JSON.stringify(cd.ir) === picked, e = cd.estimate;
      return `<div class="cand ${isPick ? "picked" : ""}"><span class="rk">${cd.rank ? "#" + cd.rank : "✕"}</span><div>${esc(cd.why)}<br>${irChips(cd.ir, prevIr, spec.locked)}${cd.rejected ? `<div class="rej">${esc(cd.rejected)}</div>` : e ? `<div class="est ${cd.est_violations?.length ? "bad" : ""}">est ~${fmt(e.fmax_mhz, 0)} MHz · ~${e.lut} LUT · ~${e.dsp} DSP${cd.est_violations?.length ? " · " + esc(cd.est_violations.join(", ")) : " · looks feasible"}${isPick ? " · chosen" : ""}</div>` : ""}</div></div>`;
    }).join("") + (r.plan.rescreened ? `<div class="est">re-planned after screening rejected the first set</div>` : ""))));
    parts.push(`${r.plan.candidates.length} candidates`);
  }
  if (!r.pending) {
    const m = r.metrics;
    if (m.critical_path?.length > 1) { inner.appendChild(el(line("critical", `<div class="cp">${fmt(m.critical_path[0].total_ns, 2)} ns: ` + m.critical_path.slice(1).map((s) => `<span>${esc(s.kind)} <i>${esc(s.rtl)}</i> ${fmt(s.logic_ns, 2)}+${fmt(s.route_ns, 2)}</span>`).join("") + `</div>`))); parts.push("critical path"); }
    inner.appendChild(el(line("simulation", stageChips(r, spec.backend))));
    if (r.lesson) {
      const L = r.lesson.lesson;
      const node = el(line("lesson", `<div class="lesson"><div class="ltxt">${esc(L.text)}</div><div class="vote"><span class="${L.outcome}">${L.outcome}</span><button data-v="up" title="confirm">👍</button><button data-v="down" title="contradict">👎</button></div></div>`, "warn"));
      node.querySelectorAll("button").forEach((btn) => { btn.onclick = async () => { const res = await post(`/memory/${spec.design || "matmul"}/${spec.backend || "ecp5"}/vote`, { id: L.id, vote: btn.dataset.v }); btn.parentElement.querySelector("span").textContent = `${L.outcome} · ${res.score >= 0 ? "+" : ""}${fmt(res.score)}`; refreshMemCount(); }; });
      inner.appendChild(node); parts.push("lesson");
    }
  }
  if (inner.childElementCount) { d.querySelector("summary").textContent = `details · ${parts.join(" · ")}`; b.appendChild(d); }
  if (r.next?.stop) b.appendChild(el(`<div class="action"><span class="why">planner stopped — </span>${esc(r.next.action)}</div>`));
  if (r.userAfter) for (const u of r.userAfter) b.appendChild(el(`<div class="usermsg"><span class="u">you</span>${esc(u.message)} <span class="muted">(queued for the next plan)</span></div>`));
  li.dataset.sig = turnSignature(r);
  return li;
}

function renderTranscript(rows, unit, spec, c) {
  const ol = $("#transcript");
  const atBottom = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 60;
  const existing = new Map([...ol.querySelectorAll("li.turn[data-n]")].map((li) => [li.dataset.n, li]));
  let prevIr = null, cursor = null;
  for (const r of rows) {
    const key = `${r.n}.${r.slot}`, cur = existing.get(key);
    if (cur && cur.dataset.sig === turnSignature(r)) { cursor = cur; }
    else {
      const li = buildTurn(r, prevIr, unit, spec);
      if (cur) ol.replaceChild(li, cur); else if (cursor) cursor.after(li); else ol.prepend(li);
      cursor = li;
    }
    existing.delete(key);
    if (!r.pending) prevIr = r.ir;
  }
  for (const stale of existing.values()) stale.remove();
  let err = ol.querySelector("li.turn.error");
  if (detail.error && !err) ol.appendChild(el(`<li class="turn error"><div class="num">!</div><div class="body"><pre class="code">${esc(detail.error)}</pre></div></li>`));
  if (!detail.error && err) err.remove();
  if (atBottom) ol.scrollTop = ol.scrollHeight;
}

function renderSugs(last) {
  const s = $("#sugs"); s.innerHTML = "";
  if (!last) return;
  const ir = last.ir || {}, v = last.violations || [], sugs = [];
  if (v.some((x) => x.startsWith("fmax"))) sugs.push("focus on timing: which single knob removes the stage on the critical path?");
  if (v.some((x) => x.startsWith("dsp"))) sugs.push("reduce DSP count first, even at lower throughput");
  if (v.some((x) => /simulation failed/.test(x))) sugs.push("a netlist sim failed: is this a real functional bug or a timing-sim artifact? explain");
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

// ---------------------------------------------------------------- inspector
function switchTab(name) {
  document.querySelectorAll(".tabs button[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) => { p.hidden = p.id !== `pane-${name}`; });
}

function renderDesign(row, done, spec) {
  const p = $("#pane-design"); p.innerHTML = "";
  if (!row?.ir) { p.appendChild(el(`<div class="muted">no design yet</div>`)); return; }
  const design = spec.design || "matmul", locked = spec.locked || {}, xc = spec.backend === "xc7s50", dspKind = (BFACTS[spec.backend] || {}).dsp_kind || "DSP";
  const ir = { ...locked, ...row.ir };
  const prev = done.filter((d) => d.n < row.n).at(-1)?.ir;
  const sec = el(`<div class="sec"><h3>Architecture <span class="hint">iteration ${row.n}${row.pending ? " (evaluating)" : ""}</span></h3></div>`);
  const arch = el(`<div class="arch"></div>`);
  if (design === "matmul") {
    const R = Math.min(ir.pe_rows, 12), C = Math.min(ir.pe_cols, 16);
    for (let i = 0; i < R; i++) { const r = el(`<div class="r"></div>`); for (let j = 0; j < C; j++) r.appendChild(el(`<div class="pe">×+</div>`)); arch.appendChild(r); }
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.pe_rows}×${ir.pe_cols} output-stationary systolic${ir.pe_rows > 12 || ir.pe_cols > 16 ? " (clipped)" : ""} · A → rows, B ↓ columns</span><span>PE: ${dspKind} (${ir.dsp_regs}) + ${ir.acc_in_dsp && xc ? "DSP post-adder" : "fabric carry-chain"} accumulator, ${ir.acc_width} b${ir.pipe_mul ? ` + ${ir.pipe_mul} fabric pipe` : ""}</span><span>${ir.reg_in ? "registered inputs · " : ""}${row.throughput ?? ir.pe_rows * ir.pe_cols} MAC/cycle · latency ${row.latency_cycles ?? "?"} cycles</span></div>`));
  } else if (design === "fir_chain") {
    for (const [i, st] of (ir.stages || []).entries()) {
      const U = st.symmetric ? Math.ceil(st.taps / 2) : st.taps, M = Math.ceil(U / st.fold);
      const r = el(`<div class="r"></div>`); r.appendChild(el(`<div class="tap on" style="width:30px;text-align:center;font:9px var(--mono)">s${i}</div>`));
      for (let m = 0; m < Math.min(M, 16); m++) r.appendChild(el(`<div class="pe mac">×+${st.fold > 1 ? `/${st.fold}` : ""}</div>`)); arch.appendChild(r);
    }
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.stages.length}-stage FIR cascade: ${ir.stages.map((st) => `${st.taps} taps${st.symmetric ? " sym" : ""} fold ${st.fold}`).join(" → ")}</span><span>FIFO ${ir.fifo_depth} deep between stages · ${ir.round_mode} reduction · ${row.throughput ?? "?"} samples/cycle · latency ${row.latency_cycles ?? "?"} cycles</span></div>`));
  } else if (design === "conv2d") {
    const K = ir.k, PR = ir.par_rows;
    const lines = el(`<div class="r"></div>`); for (let l = 0; l < K - 1; l++) lines.appendChild(el(`<div class="tap on" style="width:${Math.min(ir.width, 640) / 8}px" title="line buffer ${l}"></div>`)); arch.appendChild(lines);
    for (let r = 0; r < K; r++) { const row = el(`<div class="r"></div>`); for (let c = 0; c < K; c++) row.appendChild(el(`<div class="pe ${r < PR ? "" : "mac"}" title="window[${r}][${c}]">×</div>`)); arch.appendChild(row); }
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${K}×${K} window on a ${ir.width}-px line · ${K - 1} line buffer${K > 2 ? "s" : ""} in ${ir.line_mem === "bram" ? "block RAM" : "LUT shift registers"}</span><span>${K * PR} multiplier${K * PR > 1 ? "s" : ""} (${PR} of ${K} rows per cycle) · adder tree with ${ir.tree_regs} register level${ir.tree_regs === 1 ? "" : "s"} · ${ir.acc_width} b</span><span>${row.throughput ?? PR / K} px/cycle · latency ${row.latency_cycles ?? "?"} cycles</span></div>`));
  } else {
    const U = ir.symmetric ? Math.ceil(ir.taps / 2) : ir.taps, M = Math.ceil(U / ir.fold);
    const taps = el(`<div class="r"></div>`); for (let k = 0; k < Math.min(ir.taps, 32); k++) taps.appendChild(el(`<div class="tap ${ir.symmetric && k >= ir.taps / 2 ? "" : "on"}"></div>`)); arch.appendChild(taps);
    const macs = el(`<div class="r"></div>`); for (let m = 0; m < Math.min(M, 16); m++) macs.appendChild(el(`<div class="pe mac">×+${ir.fold > 1 ? `/${ir.fold}` : ""}</div>`)); arch.appendChild(macs);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.taps} taps${ir.symmetric ? ", symmetric (" + U + " unique)" : ""} · ${ir.fold === 1 ? "transposed form, 1 sample/cycle" : `folded ×${ir.fold}, direct form, 1/${ir.fold} sample/cycle`}</span><span>${M} multiplier${M > 1 ? "s" : ""} (${ir.dsp_regs}) · ${ir.acc_in_dsp && xc ? "DSP post-adder" : "fabric"} accumulate, ${ir.acc_width} b · latency ${row.latency_cycles ?? "?"} cycles</span></div>`));
  }
  p.appendChild(sec);
  const ks = el(`<div class="sec"><h3>IR <span class="hint">click a knob to draft a steer</span></h3><div class="knobs"></div></div>`);
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

function renderSim(row, spec) {
  const p = $("#pane-sim"); p.innerHTML = "";
  if (!row) { p.appendChild(el(`<div class="muted">no iteration yet</div>`)); return; }
  const keys = stagesOf(spec.backend);
  const st = row.pending ? row.stages : (row.metrics?.sim_stages || {});
  const period = row.metrics ? (1000 / spec.constraints.fmax_mhz) : null;
  const sec = el(`<div class="sec"><h3>Simulation ladder <span class="hint">iteration ${row.n}${period ? ` · target period ${fmt(period, 3)} ns` : ""}</span></h3><table class="simtable"></table></div>`);
  const t = sec.querySelector("table");
  const rowHtml = (name, purpose, state, log) => `<tr><td class="st ${state === "pass" ? "ok" : state === "fail" ? "bad" : "na"}">${state}</td><td><b>${name}</b><br><span class="muted">${purpose}</span>${log ? `<div class="log">${esc(log)}</div>` : ""}</td></tr>`;
  const behav = row.pending ? (row.ir ? "running" : "—") : row.metrics?.sim_passed ? "pass" : "fail";
  t.insertAdjacentHTML("beforeend", rowHtml("1 · Behavioral", "RTL logic vs. Python golden model (cocotb + iverilog)", behav, !row.pending && !row.metrics?.sim_passed ? row.metrics?.sim_log?.slice(-400) : ""));
  const purposes = { post_synth_func: "synthesized netlist, unisim models, no delays", post_synth_time: "synthesized netlist with estimated SDF delays at target period", post_impl_func: "placed-and-routed netlist, no delays", post_impl_time: "placed-and-routed netlist with real SDF delays at target period — the signoff" };
  const names = { post_synth_func: "2 · Post-synthesis functional", post_synth_time: "3 · Post-synthesis timing", post_impl_func: "4 · Post-implementation functional", post_impl_time: "5 · Post-implementation timing" };
  for (const k of Object.keys(names)) {
    const s = st[k];
    const state = !keys.includes(k) ? "n/a" : !s ? (row.pending ? "pending" : "—") : s.passed === true ? "pass" : s.passed === false ? "fail" : "skipped";
    t.insertAdjacentHTML("beforeend", rowHtml(names[k], purposes[k], state, s?.passed === false ? s.log : ""));
  }
  if (!keys.includes("post_impl_time")) sec.appendChild(el(`<div class="muted small-text">Timing and post-implementation stages need an SDF-annotated netlist, which the open-source P&R tools do not produce; they run on the Vivado backend.</div>`));
  p.appendChild(sec);
}

// ---------------------------------------------------------------- trajectory
const NS = "http://www.w3.org/2000/svg";
const svgEl = (t, a = {}) => { const e = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); return e; };
const tip = $("#tip");
const showTip = (ev, text) => { tip.hidden = false; tip.textContent = text; tip.style.left = `${ev.clientX + 14}px`; tip.style.top = `${ev.clientY + 14}px`; };
const hideTip = () => { tip.hidden = true; };

function chart(container, rows, key, target, label, color, opts = {}) {
  container.innerHTML = "";
  const W = container.clientWidth || 360, H = container.clientHeight || 170, L = 40, Rm = 12, T = 12, B = 22;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` }); container.appendChild(svg);
  if (!rows.length) return;
  const vals = rows.map((r) => (opts.get ? opts.get(r) : r.metrics[key]));
  const max = Math.max(target || 0, ...vals) * 1.12 || 1;
  const X = (i) => L + ((i + 0.5) / rows.length) * (W - L - Rm), Y = (v) => H - B - (v / max) * (H - B - T);
  const g = svgEl("g", { class: "grid" });
  for (let i = 0; i <= 3; i++) { const v = (max * i) / 3, y = Y(v); g.appendChild(svgEl("line", { x1: L, x2: W - Rm, y1: y, y2: y })); const t = svgEl("text", { x: L - 5, y: y + 3, "text-anchor": "end" }); t.textContent = max > 10 ? Math.round(v) : v.toFixed(2); svg.appendChild(t); }
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
  renderPareto(p, done, c, unit);
}

// every measured point of this design kind on this device across all runs, with the Pareto front;
// this run's points are highlighted so you can see where it sits against everything known
async function renderPareto(p, done, c, unit) {
  const spec = detail.spec, design = spec.design || "matmul", backend = spec.backend || "ecp5";
  let data; try { data = await api(`/pareto?design=${design}&backend=${backend}`); } catch { return; }
  if (!data.points.length) return;
  const sec = el(`<div class="sec"><h3>Pareto front <span class="hint">${data.n_points} measured designs on ${esc(backend)} · ${data.front.length} non-dominated</span></h3><div class="chart"></div><div class="plegend"><span><i style="background:var(--accent)"></i>this run</span><span><i style="background:var(--good)"></i>front</span><span><i style="background:var(--surface-3)"></i>other runs</span></div></div>`);
  p.appendChild(sec);
  requestAnimationFrame(() => {
    const box = sec.querySelector(".chart"), W = box.clientWidth || 340, H = 170, pad = { l: 40, r: 10, t: 8, b: 24 };
    const svg = svgEl("svg", { width: W, height: H });
    const xs = data.points.map((q) => q.dsp), ys = data.points.map((q) => q.fmax_mhz);
    const x0 = 0, x1 = Math.max(...xs, c.max_dsp || 0) * 1.1 + 1, y0 = 0, y1 = Math.max(...ys, c.fmax_mhz) * 1.1;
    const X = (v) => pad.l + ((v - x0) / (x1 - x0)) * (W - pad.l - pad.r), Y = (v) => H - pad.b - ((v - y0) / (y1 - y0)) * (H - pad.t - pad.b);
    svg.appendChild(svgEl("line", { x1: pad.l, y1: Y(c.fmax_mhz), x2: W - pad.r, y2: Y(c.fmax_mhz), stroke: "var(--border-2)", "stroke-dasharray": "3 3" }));
    if (c.max_dsp != null) svg.appendChild(svgEl("line", { x1: X(c.max_dsp), y1: pad.t, x2: X(c.max_dsp), y2: H - pad.b, stroke: "var(--border-2)", "stroke-dasharray": "3 3" }));
    const mine = new Set(done.map((r) => JSON.stringify(r.ir)));
    const frontSet = new Set(data.front.map((q) => q.job + ":" + q.iteration));
    // the 2-D projection (Fmax vs DSP) of the 4-D front: keep only points that are not beaten on both axes,
    // so the drawn line is the attainable Fmax for each DSP budget
    const fr2 = data.front.filter((q) => !data.front.some((o) => o !== q && o.dsp <= q.dsp && o.fmax_mhz >= q.fmax_mhz && (o.dsp < q.dsp || o.fmax_mhz > q.fmax_mhz)));
    const fr = fr2.sort((a, b) => a.dsp - b.dsp || b.fmax_mhz - a.fmax_mhz);
    if (fr.length > 1) svg.appendChild(svgEl("polyline", { points: fr.map((q) => `${X(q.dsp)},${Y(q.fmax_mhz)}`).join(" "), fill: "none", stroke: "var(--good)", "stroke-width": 1, opacity: 0.6 }));
    for (const q of data.points) {
      const isMine = q.job === selected, onFront = frontSet.has(q.job + ":" + q.iteration);
      const dot = svgEl("circle", { cx: X(q.dsp), cy: Y(q.fmax_mhz), r: isMine ? 4.5 : 3, fill: isMine ? "var(--accent)" : onFront ? "var(--good)" : "var(--surface-3)", stroke: isMine ? "var(--bg)" : "none" });
      dot.addEventListener("mousemove", (ev) => showTip(ev, `${q.task.slice(0, 40)} · iter ${q.iteration}\n${fmt(q.fmax_mhz, 0)} MHz · ${q.lut} LUT · ${q.dsp} DSP · ${q.throughput} ${unit}\n${Object.entries(q.ir).map(([k, v]) => k + "=" + JSON.stringify(v)).join(" ")}`));
      dot.addEventListener("mouseleave", hideTip);
      dot.style.cursor = "pointer"; dot.onclick = () => { if (q.job !== selected && jobs.some((j) => j.id === q.job)) select(q.job); };
      svg.appendChild(dot);
    }
    for (const [v, lbl] of [[y1 * 0.9, `${fmt(y1 * 0.9, 0)} MHz`], [c.fmax_mhz, `target`]]) svg.appendChild(Object.assign(svgEl("text", { x: 2, y: Y(v) + 3, fill: "var(--muted)", "font-size": 9, "font-family": "var(--mono)" }), { textContent: lbl }));
    svg.appendChild(Object.assign(svgEl("text", { x: W - pad.r, y: H - 6, fill: "var(--muted)", "font-size": 9, "text-anchor": "end", "font-family": "var(--mono)" }), { textContent: "DSP →" }));
    box.appendChild(svg);
  });
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
  const mem = await api("/memory");
  list.innerHTML = "";
  if (!mem.length) { list.appendChild(el(`<div class="muted">empty — lessons appear after the first run with two or more iterations</div>`)); return; }
  for (const g of mem) {
    const grp = el(`<div class="memgroup"><h3>${esc(g.design)} · ${esc(g.backend)} <span class="muted">${g.count} lessons</span></h3></div>`);
    for (const l of g.lessons) grp.appendChild(lessonCard(l, g.design, g.backend, true));
    list.appendChild(grp);
  }
}
