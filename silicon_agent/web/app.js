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
const PROJECT = new URLSearchParams(location.search).get("project");   // a project's runs across its conversations
let jobs = [], selected = null, detail = null, seen = 0, pollTimer = null, specs = [];
const STATUS_ZH = { queued: "排队中", running: "运行中", done: "完成", failed: "未找到可行设计", error: "出错", interrupted: "已停止", deleted: "已删除" };
const PROP_ZH = { gating: "门禁中", passed: "已通过", failed: "未通过", interrupted: "被打断" };
let view = "empty";
const state = { get jobs() { return jobs; }, get detail() { return detail; }, get selected() { return selected; }, cmpA: null, cmpB: null, thread: THREAD };
let ext = null;
const openTurns = new Set(); // iteration numbers whose details are expanded

// ---------------------------------------------------------------- boot
(async function boot() {
  try { $("#who").textContent = (await api("/me")).subject; } catch { $("#who").textContent = "unauthenticated"; }
  try { AGH_WEB = !!(await api("/health")).agh_web; } catch {}
  const llmBanner = async () => { try { const st = (await api("/health")).llm; let b = $("#llm-banner"); if (!st || st.ok !== false) { if (b) b.remove(); return; } if (!b) { b = el(`<div id="llm-banner" class="banner"></div>`); $("#main").prepend(b); } b.textContent = `模型账户不可用（DeepSeek 返回 ${st.code || "?"}）：规划器暂停，已启动的运行照常跑完；不需要规划器的扫描和自带 RTL 检查仍可用${st.balance ? ` · 余额 ${st.balance}` : ""}`; } catch {} };
  llmBanner(); setInterval(llmBanner, 30000);
  specs = await api("/specs");
  try { BFACTS = (await api("/schema")).backend_facts; BSTAGES = Object.fromEntries(Object.entries(BFACTS).map(([k, v]) => [k, v.sim_stages])); } catch {}
  ext = wire({ $, api, post, esc, el, fmt, specs, state, select, refreshJobs, show });
  await ext.initForm();
  ext.initActions();
  $("#new-run").onclick = () => show("new");
  $("#open-memory").onclick = () => show("memory");
  const navOn = () => document.querySelectorAll(".navlist .nav").forEach((b) => b.classList.toggle("on", b.id === `open-${view}`));
  document.querySelectorAll(".navlist .nav").forEach((b) => b.addEventListener("click", () => setTimeout(navOn, 0)));
  $("#runs").addEventListener("click", () => setTimeout(navOn, 0));
  $("#open-bench").onclick = () => show("bench");
  $("#open-proposals").onclick = () => show("proposals");
  $("#open-catalog").onclick = () => show("catalog");
  refreshPropCount();
  $("#send").onclick = sendSteer;
  $("#steer").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendSteer(); });
  $("#steer").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"; });
  $("#btn-stop").onclick = async () => { if (selected) { await post(`/jobs/${selected}/stop`); $("#btn-stop").disabled = true; } };
  $("#t-insp").onclick = () => toggleInspector();
  $("#close-insp").onclick = () => toggleInspector(false);
  $("#t-rail").onclick = () => { const sh = $(".shell"); if (window.innerWidth <= 800) sh.classList.toggle("rail-shown"); else sh.classList.toggle("rail-hidden"); };
  document.querySelectorAll(".tabs button[data-tab]").forEach((b) => { b.onclick = () => switchTab(b.dataset.tab); });
  refreshMemCount();
  if (PROJECT && !THREAD) {
    try {
      const p = await api(`/projects/${PROJECT}`);
      $(".rail-head .brand").innerHTML = `<a class="back" href="/#project/${esc(PROJECT)}" title="返回专案">‹</a> <span class="logo">▣</span> <span class="tt" title="${esc(p.name)}">${esc(p.name)}</span>`;
      document.title = `${p.name} · 工作台`;
    } catch { $(".rail-head .brand").insertAdjacentHTML("beforeend", ` <span class="muted">（找不到该专案）</span>`); }
  }
  if (THREAD) {
    try {
      const th = await api(`/threads/${THREAD}`); threadData = th;
      const head = $(".rail-head"); head.querySelector(".brand").innerHTML = `<a class="back" href="/#${esc(THREAD)}" title="返回对话">‹</a> <span class="logo">▣</span> <span class="tt" title="${esc(th.title)}">${esc(th.title || THREAD)}</span>`;
      document.title = `${th.title || THREAD} · 工作台`;
    } catch { $(".rail-head .brand").insertAdjacentHTML("beforeend", ` <span class="muted">（找不到该对话）</span>`); }
  }
  await refreshJobs();
  setInterval(refreshJobs, 5000);
  // ?insp=1 opens the inspector on load (deep links, screenshots)
  if (new URLSearchParams(location.search).get("insp") === "1") toggleInspector(true);
  const tabQ = new URLSearchParams(location.search).get("tab");   // ?tab=design|trajectory|sim|rtl|memory|agent
  if (tabQ) switchTab(tabQ);
  const hash = location.hash.slice(1);
  if (hash === "memory") show("memory"); else if (hash === "bench") show("bench"); else if (hash === "proposals" || hash.startsWith("proposals/")) show("proposals"); else if (hash === "catalog") show("catalog"); else if (hash === "new") show("new"); else if (hash.startsWith("compare/")) { const [, a, b] = hash.split("/"); ext.openCompare(a, b); }
  else if (hash && (!THREAD || jobs.some((j) => j.id === hash))) select(hash);
  else if ((THREAD || PROJECT) && jobs.length) select(jobs[0].id);
  else show("empty");
})();

function show(v) {
  view = v;
  $("#empty").hidden = v !== "empty"; $("#new-form").hidden = v !== "new"; $("#run").hidden = v !== "run"; $("#memory").hidden = v !== "memory"; $("#compare").hidden = v !== "compare"; $("#bench").hidden = v !== "bench"; $("#proposals").hidden = v !== "proposals"; $("#catalog").hidden = v !== "catalog";
  if (v !== "run") { selected = null; clearTimeout(pollTimer); document.querySelectorAll("#runs li").forEach((li) => li.classList.remove("sel")); }
  if (v === "memory") { location.hash = "memory"; renderMemoryView(); }
  if (v === "bench") { location.hash = "bench"; renderBenchView(); }
  if (v === "catalog") { location.hash = "catalog"; renderCatalogView(); }
  if (v === "proposals") { if (!location.hash.startsWith("#proposals")) location.hash = "proposals"; renderProposalsView(); }
  if (v === "new") location.hash = "new";
}
function toggleInspector(force) {
  const on = force ?? !$("#split").classList.contains("with-insp");
  $("#split").classList.toggle("with-insp", on);
  $("#t-insp").textContent = on ? "详情 ◂" : "详情 ▸";
  localStorage.setItem("sa.insp", on ? "1" : "0");
}
async function refreshPropCount() {
  try { const p = await api("/proposals"); $("#prop-count").textContent = p.length || ""; } catch {}
}
async function refreshMemCount() {
  try { const m = await api("/memory"); $("#mem-count").textContent = m.reduce((a, g) => a + g.count, 0); } catch {}
}

// ---------------------------------------------------------------- rail
async function refreshJobs() {
  jobs = await api(PROJECT && !THREAD ? `/jobs?project=${encodeURIComponent(PROJECT)}` : `/jobs?thread=${encodeURIComponent(THREAD || "")}`);  // thread "" = runs that belong to no conversation
  const ul = $("#runs"); ul.innerHTML = "";
  for (const j of jobs) {
    const b = j.best?.metrics;
    const status = j.status === "done" && b ? `${fmt(b.fmax_mhz, 0)} MHz · ${b.dsp} DSP · ${j.iterations} 轮` : j.status === "running" ? `运行中 · 第 ${j.iterations + 1} 轮` : `${STATUS_ZH[j.status] || j.status} · ${j.iterations} 轮`;
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
  pill.textContent = { queued: "排队中", running: liveRow ? `第 ${liveRow.n} 轮${rows.filter((r) => r.pending).length > 1 ? " ×" + rows.filter((r) => r.pending).length : ""}` : "运行中", done: `可行 · ${nRounds} 轮${done.length > nRounds ? ` · ${done.length} 个设计` : ""}`, failed: `未找到可行设计 · ${nRounds} 轮`, error: "出错", interrupted: "已停止" }[detail.status] || detail.status;
  $("#j-best").textContent = best ? `最佳：${fmt(best.metrics.fmax_mhz, 0)} MHz · ${best.metrics.lut} LUT · ${best.metrics.dsp} DSP · ${best.throughput} ${unit}` : `目标：≥${c.fmax_mhz} MHz${c.max_dsp != null ? ` · ≤${c.max_dsp} DSP` : ""}${c.max_lut != null ? ` · ≤${c.max_lut} LUT` : ""}`;
  $("#btn-stop").hidden = !live; $("#btn-stop").disabled = false;
  let pl = $(".parent-link");
  if (detail.parent?.id) { if (!pl) { pl = el(`<div class="parent-link"></div>`); $("#transcript").before(pl); } const inScope = jobs.some((j) => j.id === detail.parent.id); pl.innerHTML = `分叉自 ${inScope ? `<a href="#${esc(detail.parent.id)}">` : "<span>"}${esc(jobs.find((j) => j.id === detail.parent.id)?.task || detail.parent.id)}${inScope ? "</a>" : "</span>"}${detail.parent.iteration ? ` · 第 ${detail.parent.iteration} 轮` : ""}`; if (inScope) pl.querySelector("a").onclick = (e) => { e.preventDefault(); select(detail.parent.id); }; }
  else if (pl) pl.remove();
  renderTranscript(rows, unit, spec, c);
  $("#composer").classList.toggle("disabled", !live);
  $("#chint-r").textContent = `${spec.design || "matmul"} · ${spec.backend || "ecp5"} · ${selected}`;
  renderSugs(done.at(-1));
  // the best design may be a sibling candidate (slot > 0): match by iteration and the knobs both records carry
  const bi = detail.summary?.best?.ir, bestRow = bi ? done.find((r) => r.n === detail.summary.best.iteration && Object.keys(r.ir || {}).every((k) => k === "coefs" || JSON.stringify(r.ir[k]) === JSON.stringify(bi[k]))) : null;
  renderDesign(liveRow?.ir ? liveRow : (bestRow || done.at(-1)), done, spec);
  renderTrajectory(done, c, unit);
  renderSim(liveRow || bestRow || done.at(-1), spec);
  if (best && !$("#rtl").textContent) api(`/jobs/${selected}/rtl`).then((t) => { $("#rtl").textContent = t; }).catch(() => {});
  renderMemoryTab(rows);
  renderAgentTab();
}

// the Agnes Harness side of a run: the chat turns that started it and the agent's tool trail (steps) with arguments
let threadData = null, AGH_WEB = false;
function renderAgentTab() {
  const tab = $("#tab-agent"), p = $("#pane-agent");
  const msgs = (threadData?.messages || []).filter((m) => m.source === "agh" && (m.job === selected || (m.kind === "text" && m.tools?.some((t) => JSON.stringify(t.args || {}).includes(selected)))));
  tab.hidden = !msgs.length;
  if (!msgs.length) { p.innerHTML = ""; return; }
  const sig = JSON.stringify(msgs.map((m) => [m.t, m.steps?.length]));
  if (p.dataset.sig === sig) return;
  p.dataset.sig = sig; p.innerHTML = "";
  const sid = threadData.agh_session || "";
  p.appendChild(el(`<div class="sec"><h3>智能体 <span class="hint">Agnes Harness 会话 · ${esc(sid)}</span>${AGH_WEB && sid ? ` <a class="aghlink" href="/agh/?session=${encodeURIComponent(sid)}" target="_blank" title="在 AGH 工作台打开这个会话：完整记录、工具调用、审批">⚙ 在 AGH 打开 ↗</a>` : ""}</h3></div>`));
  for (const m of msgs) {
    const sec = el(`<div class="sec agent-turn"></div>`);
    if (m.kind === "text") {
      const steps = m.steps || [];
      // the trailing text step is the reply itself (shown below), not a working note
      const lastTool = steps.map((s) => s.kind).lastIndexOf("tool");
      const list = el(`<div class="agent-steps-list"></div>`);
      let k = 0;
      for (const [i, st] of steps.entries()) {
        if (st.kind === "text") { if (st.text.trim() && i < lastTool) list.appendChild(el(`<div class="note">${esc(st.text.trim())}</div>`)); continue; }
        const t = (m.tools || [])[k++] || {};
        const args = t.args ? JSON.stringify(t.args) : "";
        list.appendChild(el(`<div class="call ${st.status === "failed" ? "x" : ""}"><span class="n">${k}</span><code>${esc(st.name || "?")}</code>${args ? `<span class="args" title="${esc(args)}">${esc(args.length > 160 ? args.slice(0, 160) + "…" : args)}</span>` : ""}${st.status === "failed" ? `<span class="bad"> 失败</span>` : ""}</div>`));
      }
      sec.appendChild(list);
      sec.appendChild(el(`<div class="agent-reply">${esc(m.text || "")}</div>`));
      if (m.usage) {
        const u = m.usage, tok = (u.input || 0) + (u.cache_read || 0) + (u.output || 0);
        const cost = u.usd_micros == null ? "—" : `$${(u.usd_micros / 1e6).toFixed(u.usd_micros < 10000 ? 4 : 2)}`;
        const t = u.ms == null ? "—" : u.ms < 60000 ? `${(u.ms / 1000).toFixed(1)} s` : `${Math.floor(u.ms / 60000)} min ${Math.round((u.ms % 60000) / 1000)} s`;
        sec.appendChild(el(`<div class="hint mono" title="这一轮的 Agnes Harness 账本">${u.calls ?? "?"} 次模型调用 · 输入 ${u.input || 0} + 缓存命中 ${u.cache_read || 0} + 输出 ${u.output || 0} = ${tok} tokens · ${cost} · ${t}${u.model ? ` · ${esc(u.model)}` : ""}${m.resumed ? " · 服务器重启后续接" : ""}</div>`));
      }
    } else {
      sec.appendChild(el(`<div class="muted small-text">${esc(m.text || "由智能体启动的运行")}</div>`));
    }
    p.appendChild(sec);
  }
}

// ---------------------------------------------------------------- transcript
function irChips(ir, prevIr, locked) {
  if (Array.isArray(ir.nodes)) {
    const ln = Array.isArray(locked?.nodes) ? locked.nodes : [];
    return ir.nodes.map((nd, i) => `<div class="chips"><span class="chip"><span class="k">n${i}</span> ${esc(nd.kind)}</span>` + Object.entries(nd.ir || {}).filter(([k]) => k !== "coefs" && !(ln[i]?.ir && k in ln[i].ir)).map(([k, v]) => {
      const changed = prevIr?.nodes?.[i] && JSON.stringify(prevIr.nodes[i].ir?.[k]) !== JSON.stringify(v);
      return `<span class="chip ${changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
    }).join("") + `</div>`).join("") + `<div class="chips">` + Object.entries(ir).filter(([k]) => k !== "nodes").map(([k, v]) => `<span class="chip ${prevIr && JSON.stringify(prevIr[k]) !== JSON.stringify(v) ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`).join("") + `</div>`;
  }
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
  for (const u of r.user) b.appendChild(el(`<div class="usermsg"><span class="u">你</span>${esc(u.message)}</div>`));
  const a = r.plan?.action ?? r.action;
  if (r.slot) b.appendChild(el(`<div class="action"><span class="why">并行候选（同轮实测）— </span>${esc(loopText(r.action || ""))}</div>`));
  else if (r.n === 1) b.appendChild(el(`<div class="action">从规格的起点设计开始。</div>`));
  else if (a) b.appendChild(el(`<div class="action">${r.plan?.fallback ? `<span class="why">兜底候选 — </span>` : ""}${esc(loopText(a))}</div>`));
  if (r.note) b.appendChild(el(`<div class="action"><span class="why">${esc(loopText(r.note))}</span></div>`));
  if (r.ir) b.appendChild(el(irChips(r.ir, prevIr, spec.locked)));
  if (r.pending) {
    b.appendChild(el(`<div class="head"><span class="verdict live">${r.ir ? "仿真 · 综合 · 布局布线" : "规划中"}</span></div>`));
    if (r.ir) b.appendChild(el(stageChips(r, spec.backend)));
  } else {
    const m = r.metrics, ok = r.violations.length === 0;
    const seeds = m.fmax_seeds && m.fmax_seeds.length > 1 ? ` <span class="muted" title="${m.fmax_seeds.length} 粒布局布线种子">${fmt(Math.min(...m.fmax_seeds), 0)}–${fmt(Math.max(...m.fmax_seeds), 0)}</span>` : "";
    b.appendChild(el(`<div class="head"><span class="verdict ${ok ? "ok" : "bad"}">${ok ? "可行" : "违反约束"}</span><span class="nums"><b>${fmt(m.fmax_mhz)} MHz</b>${seeds} · ${m.area_um2 != null ? `${m.lut} cells · ${fmt(m.area_um2 / 1000, 1)}k µm²` : `${m.lut} LUT · ${m.dsp} DSP`}${m.bram ? ` · ${m.bram} BRAM` : ""}${m.power_mw != null ? ` · ${m.power_mw} mW` : ""} · ${r.throughput} ${unit} · ${r.eda_seconds}s</span>${ok ? "" : `<span class="viol">${esc(r.violations.join(" · "))}</span>`}</div>`));
  }
  // details: grows in place under this turn; the list around it does not move
  const d = el(`<details class="more" ${openTurns.has(r.n) ? "open" : ""}><summary>详情</summary><div class="inner"></div></details>`);
  d.addEventListener("toggle", () => { if (d.open) openTurns.add(r.n); else openTurns.delete(r.n); li.classList.toggle("open", d.open); });
  li.classList.toggle("open", d.open);
  const inner = d.querySelector(".inner"), parts = [];
  const diag = r.plan?.diagnosis ?? r.diagnosis;
  if (diag && r.n > 1) { inner.appendChild(el(line("诊断", esc(diag)))); parts.push("诊断"); }
  if (r.memory?.lessons?.length) { inner.appendChild(el(line("记忆", r.memory.lessons.map((l) => `<div class="memrow"><span class="mid">${esc(l.id)} ${l.score >= 0 ? "+" : ""}${fmt(l.score)}</span>${esc(l.text)}</div>`).join(""), "accent"))); parts.push("记忆"); }
  if (r.plan?.candidates?.length) {
    const picked = JSON.stringify(r.plan.next_ir);
    inner.appendChild(el(line("候选", r.plan.candidates.map((cd) => {
      const isPick = JSON.stringify(cd.ir) === picked, e = cd.estimate;
      return `<div class="cand ${isPick ? "picked" : ""}"><span class="rk">${cd.rank ? "#" + cd.rank : "✕"}</span><div>${esc(cd.why)}<br>${irChips(cd.ir, prevIr, spec.locked)}${cd.rejected ? `<div class="rej">${esc(cd.rejected)}</div>` : e ? `<div class="est ${cd.est_violations?.length ? "bad" : ""}">est ~${fmt(e.fmax_mhz, 0)} MHz · ~${e.lut} LUT · ~${e.dsp} DSP${cd.est_violations?.length ? " · " + esc(cd.est_violations.join(", ")) : " · looks feasible"}${isPick ? " · chosen" : ""}</div>` : ""}</div></div>`;
    }).join("") + (r.plan.rescreened ? `<div class="est">筛选否决了第一组候选后重新规划</div>` : ""))));
    parts.push(`${r.plan.candidates.length} 个候选`);
  }
  if (!r.pending) {
    const m = r.metrics;
    if (m.critical_path?.length > 1) { inner.appendChild(el(line("关键路径", `<div class="cp">${fmt(m.critical_path[0].total_ns, 2)} ns: ` + m.critical_path.slice(1).map((s) => `<span>${esc(s.kind)} <i>${esc(s.rtl)}</i> ${fmt(s.logic_ns, 2)}+${fmt(s.route_ns, 2)}</span>`).join("") + `</div>`))); parts.push("关键路径"); }
    inner.appendChild(el(line("仿真", stageChips(r, spec.backend))));
    if (r.lesson) {
      const L = r.lesson.lesson;
      const node = el(line("教训", `<div class="lesson"><div class="ltxt">${esc(L.text)}</div><div class="vote"><span class="${L.outcome}">${({ helped: "有效", hurt: "有害", neutral: "无明显影响" })[L.outcome] || L.outcome}</span><button data-v="up" title="确认">👍</button><button data-v="down" title="反驳">👎</button></div></div>`, "warn"));
      node.querySelectorAll("button").forEach((btn) => { btn.onclick = async () => { const res = await post(`/memory/${spec.design || "matmul"}/${spec.backend || "ecp5"}/vote`, { id: L.id, vote: btn.dataset.v }); btn.parentElement.querySelector("span").textContent = `${L.outcome} · ${res.score >= 0 ? "+" : ""}${fmt(res.score)}`; refreshMemCount(); }; });
      inner.appendChild(node); parts.push("教训");
    }
  }
  if (inner.childElementCount) { d.querySelector("summary").textContent = `详情 · ${parts.join(" · ")}`; b.appendChild(d); }
  if (r.next?.stop) b.appendChild(el(`<div class="action"><span class="why">规划器停止 — </span>${esc(loopText(r.next.action))}</div>`));
  if (r.userAfter) for (const u of r.userAfter) b.appendChild(el(`<div class="usermsg"><span class="u">you</span>${esc(u.message)} <span class="muted">（将在下一轮规划前生效）</span></div>`));
  li.dataset.sig = turnSignature(r);
  return li;
}

const LOOP_ZH = { "stopped by user": "用户已停止", "stopping with best-so-far": "规划器不可用，以目前最佳设计结束", "no legal candidate": "没有合法候选", "measure every point": "逐点实测", "REJECTED (all candidates invalid or duplicates) -- propose different IRs": "全部候选无效或重复，要求重新提出", "stop": "停止" };
const loopText = (x) => typeof x === "string" ? (LOOP_ZH[x] || x.replace(/^planner unavailable: /, "规划器不可用：").replace(/^design-space sweep over (.*): (\d+) points$/, "设计空间扫描 $1：$2 个点").replace(/^sweep point (.*) rejected: /, "扫描点 $1 被拒：").replace(/^sweep point (.*) invalid: /, "扫描点 $1 无效：").replace(/^sibling candidate: /, "").replace(/^sibling candidate #(\d+) won this round; continuing from it$/, "并行候选 #$1 赢得本轮，从它继续")) : x;
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
  if (detail.error && !err) ol.appendChild(el(`<li class="本轮出错"><div class="num">!</div><div class="body"><pre class="code">${esc(detail.error)}</pre></div></li>`));
  if (!detail.error && err) err.remove();
  if (atBottom) ol.scrollTop = ol.scrollHeight;
}

function renderSugs(last) {
  const s = $("#sugs"); s.innerHTML = "";
  if (!last) return;
  const ir = last.ir || {}, v = last.violations || [], sugs = [];
  if (v.some((x) => x.startsWith("fmax"))) sugs.push("聚焦时序：哪一个旋钮能去掉关键路径上的那一级？");
  if (v.some((x) => x.startsWith("dsp"))) sugs.push("先把 DSP 压下来，吞吐可以降");
  if (v.some((x) => /simulation failed/.test(x))) sugs.push("a netlist sim failed: is this a real functional bug or a timing-sim artifact? explain");
  if (!v.length) sugs.push("停止前再探索一种结构");
  if ("acc_in_dsp" in ir) sugs.push("不要改 acc_in_dsp");
  if ("fold" in ir) sugs.push("try the transposed form (fold=1)");
  sugs.push("用一句话解释上一轮的关键路径");
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
  if (!row?.ir) { p.appendChild(el(`<div class="muted">还没有设计</div>`)); return; }
  const design = spec.design || "matmul", locked = spec.locked || {}, xc = spec.backend === "xc7s50", dspKind = (BFACTS[spec.backend] || {}).dsp_kind || "DSP";
  const ir = { ...locked, ...row.ir };
  const prev = done.filter((d) => d.n < row.n).at(-1)?.ir;
  const sec = el(`<div class="sec"><h3>架构 <span class="hint">第 ${row.n} 轮${row.pending ? "（评估中）" : ""}</span></h3></div>`);
  const arch = el(`<div class="arch"></div>`);
  if (design === "matmul") {
    const R = Math.min(ir.pe_rows, 12), C = Math.min(ir.pe_cols, 16);
    for (let i = 0; i < R; i++) { const r = el(`<div class="r"></div>`); for (let j = 0; j < C; j++) r.appendChild(el(`<div class="pe">×+</div>`)); arch.appendChild(r); }
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.pe_rows}×${ir.pe_cols} 输出驻留 systolic 阵列${ir.pe_rows > 12 || ir.pe_cols > 16 ? "（示意已截断）" : ""} · A → 行，B ↓ 列</span><span>PE：${dspKind}（${ir.dsp_regs}）+ ${ir.acc_in_dsp && xc ? "DSP 内部后加器" : "逻辑进位链"}累加，${ir.acc_width} 位${ir.pipe_mul ? ` + ${ir.pipe_mul} 级逻辑流水` : ""}</span><span>${ir.reg_in ? "输入打拍 · " : ""}${row.throughput ?? ir.pe_rows * ir.pe_cols} MAC/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "graph") {
    sec.appendChild(el(`<div class="archlegend"><span>${(ir.nodes || []).map((nd) => nd.kind).join(" → ")}</span><span>级间 FIFO 深 ${ir.fifo_depth} · 位宽适配 ${ir.adapt} · ${row.throughput ?? "?"} 样本/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "fir_chain") {
    for (const [i, st] of (ir.stages || []).entries()) {
      const U = st.symmetric ? Math.ceil(st.taps / 2) : st.taps, M = Math.ceil(U / st.fold);
      const r = el(`<div class="r"></div>`); r.appendChild(el(`<div class="tap on" style="width:30px;text-align:center;font:9px var(--mono)">s${i}</div>`));
      for (let m = 0; m < Math.min(M, 16); m++) r.appendChild(el(`<div class="pe mac">×+${st.fold > 1 ? `/${st.fold}` : ""}</div>`)); arch.appendChild(r);
    }
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.stages.length} 级 FIR 级联：${ir.stages.map((st) => `${st.taps} 阶${st.symmetric ? "（对称）" : ""} 折叠 ×${st.fold}`).join(" → ")}</span><span>级间 FIFO 深 ${ir.fifo_depth} · 位宽缩减 ${ir.round_mode} · ${row.throughput ?? "?"} 样本/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "conv2d") {
    const K = ir.k, PR = ir.par_rows;
    const lines = el(`<div class="r"></div>`); for (let l = 0; l < K - 1; l++) lines.appendChild(el(`<div class="tap on" style="width:${Math.min(ir.width, 640) / 8}px" title="行缓存 ${l}"></div>`)); arch.appendChild(lines);
    for (let r = 0; r < K; r++) { const row = el(`<div class="r"></div>`); for (let c = 0; c < K; c++) row.appendChild(el(`<div class="pe ${r < PR ? "" : "mac"}" title="窗口[${r}][${c}]">×</div>`)); arch.appendChild(row); }
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${K}×${K} 窗口，行宽 ${ir.width} 像素 · ${K - 1} 条行缓存放在${ir.line_mem === "bram" ? "块 RAM" : "LUT 移位寄存器"}</span><span>${K * PR} 个乘法器（每周期算 ${K} 行中的 ${PR} 行）· 加法树 ${ir.tree_regs} 级寄存器 · 累加 ${ir.acc_width} 位</span><span>${row.throughput ?? PR / K} 像素/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "cic") {
    const N = ir.stages || 1, R = ir.decim || 2, B = (ir.data_width || 16) + Math.ceil(N * Math.log2(R));
    const r1 = el(`<div class="r"></div>`); for (let i = 0; i < N; i++) r1.appendChild(el(`<div class="pe mac" title="积分器 ${i}">∫${ir.pipe_integ && i ? "|" : ""}</div>`)); r1.appendChild(el(`<div class="pe dec" title="抽取 ÷${R}">↓${R}</div>`)); for (let i = 0; i < N; i++) r1.appendChild(el(`<div class="pe mac" title="梳状器 ${i}">Δ${ir.pipe_comb && i ? "|" : ""}</div>`)); arch.appendChild(r1);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>Hogenauer CIC：${N} 级积分器 → ↓${R} → ${N} 级梳状器（M=1）· 寄存器位宽 ${B} 位 · 无乘法器</span><span>${ir.pipe_integ ? "积分器级间打拍" : "积分器组合级联"} · ${ir.pipe_comb ? "梳状器流水（valid 移位链）" : "梳状器组合级联"}${ir.reg_in ? " · 输入打拍" : ""} · ${row.throughput ?? 1} 输入样本/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "decim") {
    const M = ir.decim || 2, W = M * (ir.fold || 1), K = Math.ceil((ir.taps || 1) / W);
    const taps = el(`<div class="r"></div>`); for (let k = 0; k < Math.min(ir.taps, 32); k++) taps.appendChild(el(`<div class="tap on"></div>`)); arch.appendChild(taps);
    const macs = el(`<div class="r"></div>`); macs.appendChild(el(`<div class="pe dec">↓${M}</div>`)); for (let m = 0; m < Math.min(K, 16); m++) macs.appendChild(el(`<div class="pe mac">×+</div>`)); arch.appendChild(macs);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.taps} 阶抽取 FIR，↓${M}：快照后在 ${W} 个周期里用 ${K} 个乘法器扫完（${ir.dsp_regs}）${ir.sum_tree ? " · 归约打拍" : ""}</span><span>累加 ${ir.acc_width} 位${ir.pipe_mul ? ` · ${ir.pipe_mul} 级逻辑流水` : ""}${ir.reg_in ? " · 输入打拍" : ""} · ${row.throughput ?? "?"} 输入样本/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "nco") {
    const r1 = el(`<div class="r"></div>`); r1.appendChild(el(`<div class="pe mac" title="相位累加器">Σφ</div>`)); r1.appendChild(el(`<div class="pe ${ir.table_mem === "bram" ? "mem" : "mac"}" title="四分之一波正弦表">sin</div>`)); for (let i = 0; i < (ir.pipe_lut || 0); i++) r1.appendChild(el(`<div class="tap on" title="查表流水"></div>`)); r1.appendChild(el(`<div class="pe mac">×I</div>`)); r1.appendChild(el(`<div class="pe mac">×Q</div>`)); for (let i = 0; i < (ir.pipe_mix || 0); i++) r1.appendChild(el(`<div class="tap on" title="混频流水"></div>`)); arch.appendChild(r1);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.phase_width} 位相位累加器 → ${ir.lut_addr_width} 位地址，四分之一波正弦表（${1 << ((ir.lut_addr_width || 8) - 2)} × ${ir.sin_width} 位，放在${ir.table_mem === "bram" ? "块 RAM" : "逻辑"}）${ir.dither ? " · LFSR 相位抖动" : ""}</span><span>复数混频：2 个乘法器（${ir.dsp_regs}）· 查表后 ${ir.pipe_lut} 级 + 混频后 ${ir.pipe_mix} 级流水 · 输出 ${ir.out_width} 位 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "iir") {
    const r1 = el(`<div class="r"></div>`); for (let i = 0; i < (ir.sections || 1); i++) { r1.appendChild(el(`<div class="pe mac" style="width:44px" title="双二阶 ${i}">b/a ${i}</div>`)); } arch.appendChild(r1);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.sections} 节 Butterworth 级联（TDF-II 双二阶），截止 ${ir.cutoff} fs · 系数 ${ir.coef_width} 位 / 小数 ${ir.coef_frac} 位 · 状态 ${ir.state_width} 位，饱和</span><span>每节 5 个乘法器（${ir.dsp_regs}）；递归 a 路保持组合逻辑${ir.pipe_mul ? " · 前馈路打拍" : ""} · 1 样本/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else if (design === "cordic") {
    const r1 = el(`<div class="r"></div>`); for (let i = 0; i < Math.min(ir.iterations || 12, 24); i++) r1.appendChild(el(`<div class="tap on" title="旋转 ${i}"></div>`)); arch.appendChild(r1);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.iterations} 级 CORDIC ${ir.mode === "vector" ? "矢量" : "旋转"}模式，每级流水 ${ir.unroll} 次迭代${ir.fold > 1 ? `，折叠 ×${ir.fold}` : ""} · ${ir.width} 位 + ${ir.guard_bits} 位保护${ir.scale_comp ? " · 增益补偿" : ""}${ir.reg_out ? " · 输出打拍" : ""}</span><span>${row.throughput ?? "?"} 样本/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期 · 无乘法器</span></div>`));
  } else if (design === "fft") {
    const n = ir.n || 64, st = Math.round(Math.log2(n));
    const r1 = el(`<div class="r"></div>`); for (let i = 0; i < Math.min(st, 12); i++) r1.appendChild(el(`<div class="pe mac" title="第 ${i} 级">bf</div>`)); arch.appendChild(r1);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${n} 点流水 FFT，${st} 级蝶形 · 旋转因子 ${ir.tw_width} 位放在 ${ir.tw_mem} · 延迟线放在 ${ir.delay_mem} · 位增长 ${ir.growth}</span><span>${ir.cmul === "three" ? "3 乘法器复数乘" : "4 乘法器复数乘"}（${ir.dsp_regs}）· 乘法后 ${ir.mul_regs} 级 + 蝶形后 ${ir.bf_regs} 级寄存器${ir.bit_reverse ? " · 位反序输出" : ""} · ${row.throughput ?? "?"} 样本/周期 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  } else {
    const U = ir.symmetric ? Math.ceil(ir.taps / 2) : ir.taps, M = Math.ceil(U / ir.fold);
    const taps = el(`<div class="r"></div>`); for (let k = 0; k < Math.min(ir.taps, 32); k++) taps.appendChild(el(`<div class="tap ${ir.symmetric && k >= ir.taps / 2 ? "" : "on"}"></div>`)); arch.appendChild(taps);
    const macs = el(`<div class="r"></div>`); for (let m = 0; m < Math.min(M, 16); m++) macs.appendChild(el(`<div class="pe mac">×+${ir.fold > 1 ? `/${ir.fold}` : ""}</div>`)); arch.appendChild(macs);
    sec.appendChild(arch);
    sec.appendChild(el(`<div class="archlegend"><span>${ir.taps} 阶${ir.symmetric ? "，对称（" + U + " 个独立系数）" : ""} · ${ir.fold === 1 ? "转置型，1 样本/周期" : `折叠 ×${ir.fold}，直接型，1/${ir.fold} 样本/周期`}</span><span>${M} 个乘法器（${ir.dsp_regs}）· ${ir.acc_in_dsp && xc ? "DSP 内部后加器" : "逻辑"}累加，${ir.acc_width} 位 · 延迟 ${row.latency_cycles ?? "?"} 周期</span></div>`));
  }
  p.appendChild(sec);
  const ks = el(`<div class="sec"><h3>IR 旋钮 <span class="hint">点一个旋钮起草一条指示</span></h3><div class="knobs"></div></div>`);
  const kl = ks.querySelector(".knobs");
  for (const [k, v] of Object.entries(ir)) {
    if (k === "coefs") continue;
    const lockd = k in locked, changed = !lockd && prev && JSON.stringify(prev[k]) !== JSON.stringify(v);
    const kn = el(`<div class="knob ${lockd ? "locked" : changed ? "changed" : ""}"><span>${esc(k)}</span><span class="v">${esc(JSON.stringify(v))}</span></div>`);
    if (!lockd) kn.onclick = () => { $("#steer").value = `下一轮：改 ${k}（当前 ${JSON.stringify(v)}），其他保持不变`; $("#steer").focus(); };
    kl.appendChild(kn);
  }
  p.appendChild(ks);
  const src = row.pending ? done.at(-1) : row, cp = src?.metrics?.critical_path, m = src?.metrics;
  if (m && m.fmax_mhz > 0) p.appendChild(renderTimingResources(src, spec));
  if (cp?.length > 1) {
    const total = cp[0].total_ns || 1, tp = spec.constraints?.fmax_mhz ? 1000 / spec.constraints.fmax_mhz : 0;
    const span = Math.max(total, tp) || 1;
    const ps = el(`<div class="sec"><h3>关键路径 <span class="hint">第 ${src.n} 轮 · ${fmt(total, 2)} ns${tp ? ` · 目标周期 ${fmt(tp, 2)} ns` : ""}</span></h3><div class="pbars"></div><div class="plegend"><span><i style="background:var(--accent)"></i>逻辑</span><span><i style="background:var(--lut)"></i>布线</span>${tp ? `<span><i style="background:var(--bad);width:2px"></i>目标周期</span>` : ""}</div></div>`);
    const bars = ps.querySelector(".pbars");
    let acc = 0;
    for (const s of cp.slice(1)) {
      const name = `${s.kind} ${s.rtl.split(".").slice(-2).join(".").slice(-26)}`;
      const seg = (s.logic_ns || 0) + (s.route_ns || 0);
      bars.appendChild(el(`<div class="pbar" title="${esc(s.rtl)} · ${s.cells || 1} 个单元 · 累计 ${fmt(acc + seg, 2)} ns"><span>${esc(name)}</span><div class="track" style="width:${(100 * total / span).toFixed(1)}%"><div class="off" style="width:${(acc / total) * 100}%"></div>${s.logic_ns ? `<div class="l" style="width:${(s.logic_ns / total) * 100}%"></div>` : ""}${s.route_ns ? `<div class="rt" style="width:${(s.route_ns / total) * 100}%"></div>` : ""}</div><span class="v">${fmt(seg, 2)} ns</span></div>`));
      acc += seg;
    }
    if (tp) { const tk = el(`<div class="pbar tick"><span></span><div class="track"><i style="left:${(100 * tp / span).toFixed(2)}%"></i></div><span class="v"></span></div>`); bars.appendChild(tk); }
    p.appendChild(ps);
  }
}

// the sign-off numbers an engineer reads first: clock closure, how noisy the P&R is, how full the device is, where the
// time on the critical path goes
function renderTimingResources(src, spec) {
  const m = src.metrics, f = BFACTS[spec.backend] || {}, asic = m.area_um2 != null;
  const target = spec.constraints?.fmax_mhz, tp = target ? 1000 / target : null, ap = m.fmax_mhz ? 1000 / m.fmax_mhz : null;
  const slack = tp != null && ap != null ? tp - ap : null, margin = target ? (m.fmax_mhz / target - 1) * 100 : null;
  const seeds = m.fmax_seeds || [], lo = seeds.length ? Math.min(...seeds) : null, hi = seeds.length ? Math.max(...seeds) : null;
  const spread = seeds.length > 1 ? (hi - lo) / hi * 100 : null;
  const cp = m.critical_path || [], segs = cp.slice(1), logic = segs.reduce((a, s) => a + (s.logic_ns || 0), 0), route = segs.reduce((a, s) => a + (s.route_ns || 0), 0), cells = segs.reduce((a, s) => a + (s.cells || 1), 0);
  const cls = slack == null ? "" : slack >= 0 ? "ok" : "bad";
  const sec = el(`<div class="sec"><h3>时序与资源 <span class="hint">第 ${src.n} 轮 · ${esc(f.name || spec.backend)} · ${esc(f.toolchain || "")}</span></h3></div>`);
  const grid = el(`<div class="tgrid"></div>`);
  const cell = (label, value, sub, c = "") => grid.appendChild(el(`<div class="tcell ${c}"><span class="l">${label}</span><b>${value}</b>${sub ? `<span class="s">${sub}</span>` : ""}</div>`));
  cell("Fmax", `${fmt(m.fmax_mhz)} MHz`, target ? `目标 ${target} MHz` : "");
  if (tp != null) cell("周期", `${fmt(ap, 2)} ns`, `目标 ${fmt(tp, 2)} ns`);
  if (slack != null) cell("裕量 slack", `${slack >= 0 ? "+" : ""}${fmt(slack, 2)} ns`, `${margin >= 0 ? "+" : ""}${fmt(margin, 1)}% 余量`, cls);
  if (seeds.length > 1) cell("布局布线种子", `${seeds.length} 粒 · 离散 ${fmt(spread, 1)}%`, `${fmt(lo, 1)} – ${fmt(hi, 1)} MHz · 取中位数`, spread > 6 ? "warn" : "");
  if (segs.length) cell("关键路径", `${fmt(cp[0].total_ns, 2)} ns`, `逻辑 ${fmt(100 * logic / (logic + route || 1), 0)}% · 布线 ${fmt(100 * route / (logic + route || 1), 0)}% · ${cells} 个单元`);
  sec.appendChild(grid);
  // device utilization: used / capacity, as bars (an ASIC has no capacity; cells and area are absolute)
  const util = el(`<div class="util"></div>`);
  const bar = (label, used, cap, kind) => {
    const pct = cap ? Math.min(100, 100 * used / cap) : 0;
    util.appendChild(el(`<div class="ubar"><span class="l">${label}${kind ? ` <i>${esc(kind)}</i>` : ""}</span><div class="track"><div class="${pct > 85 ? "hot" : ""}" style="width:${pct.toFixed(1)}%"></div></div><span class="v">${used}${cap ? ` / ${cap} · ${fmt(pct, 1)}%` : ""}</span></div>`));
  };
  if (asic) {
    util.appendChild(el(`<div class="ubar"><span class="l">标准单元</span><div class="track none"></div><span class="v">${m.lut}</span></div>`));
    util.appendChild(el(`<div class="ubar"><span class="l">面积</span><div class="track none"></div><span class="v">${fmt(m.area_um2 / 1000, 1)}k µm²</span></div>`));
    if (m.power_mw != null) util.appendChild(el(`<div class="ubar"><span class="l">功耗</span><div class="track none"></div><span class="v">${m.power_mw} mW</span></div>`));
  } else {
    bar("LUT", m.lut, f.lut, f.lut_kind); bar("FF", m.ff, f.lut ? f.lut * (spec.backend === "ice40" ? 1 : 1) : 0, "");
    bar("DSP", m.dsp, f.dsp, f.dsp_kind); bar("BRAM", m.bram || 0, f.bram, "");
  }
  sec.appendChild(util);
  return sec;
}

function renderSim(row, spec) {
  const p = $("#pane-sim"); p.innerHTML = "";
  if (!row) { p.appendChild(el(`<div class="muted">还没有轮次</div>`)); return; }
  const keys = stagesOf(spec.backend);
  const st = row.pending ? row.stages : (row.metrics?.sim_stages || {});
  const period = row.metrics ? (1000 / spec.constraints.fmax_mhz) : null;
  const sec = el(`<div class="sec"><h3>仿真阶梯 <span class="hint">第 ${row.n} 轮${period ? ` · 目标周期 ${fmt(period, 3)} ns` : ""}</span></h3><table class="simtable"></table></div>`);
  const t = sec.querySelector("table");
  const rowHtml = (name, purpose, state, log) => `<tr><td class="st ${state === "通过" ? "ok" : state === "失败" ? "bad" : "na"}">${state}</td><td><b>${name}</b><br><span class="muted">${purpose}</span>${log ? `<div class="log">${esc(log)}</div>` : ""}</td></tr>`;
  const behav = row.pending ? (row.ir ? "运行中" : "—") : row.metrics?.sim_passed ? "通过" : "失败";
  t.insertAdjacentHTML("beforeend", rowHtml("1 · 行为级仿真", "RTL 逻辑 vs Python 黄金模型（cocotb + iverilog）", behav, !row.pending && !row.metrics?.sim_passed ? row.metrics?.sim_log?.slice(-400) : ""));
  const purposes = { post_synth_func: "综合后网表，unisim 模型，无延迟", post_synth_time: "综合后网表 + 估算 SDF 延迟，目标周期", post_impl_func: "布线后网表，无延迟", post_impl_time: "布线后网表 + 真实 SDF 延迟，目标周期 — 签核" };
  const names = { post_synth_func: "2 · 综合后功能", post_synth_time: "3 · 综合后时序", post_impl_func: "4 · 实现后功能", post_impl_time: "5 · 实现后时序" };
  for (const k of Object.keys(names)) {
    const s = st[k];
    const state = !keys.includes(k) ? "不适用" : !s ? (row.pending ? "等待" : "—") : s.passed === true ? "通过" : s.passed === false ? "失败" : "跳过";
    t.insertAdjacentHTML("beforeend", rowHtml(names[k], purposes[k], state, s?.passed === false ? s.log : ""));
  }
  const covs = row.metrics?.sim_coverage && Object.values(row.metrics.sim_coverage)[0];
  if (covs) sec.appendChild(el(`<div class="sec"><h3>覆盖 <span class="hint">行为级测试台的场景计数</span></h3><div class="chips">${Object.entries(covs).map(([k, v]) => `<span class="chip ${v ? "" : "lock"}"><span class="k">${esc(k)}</span> ${v}</span>`).join("")}</div></div>`));
  if (!keys.includes("post_impl_time")) sec.appendChild(el(`<div class="muted small-text">时序和实现后阶段需要带 SDF 延迟标注的网表，开源布局布线工具不产生；这些阶段在 Vivado 后端上跑。</div>`));
  p.appendChild(sec);
  if (detail?.summary?.best && !row.pending) p.appendChild(renderSignoff());
}

// formal equivalence / properties and the mutation score of the best design: cached results shown, buttons run them
let signoffCache = {};
function renderSignoff() {
  const sec = el(`<div class="sec"><h3>验证签核 <span class="hint">最佳设计 · SymbiYosys + 变异测试</span></h3><div class="signoff"></div></div>`);
  const box = sec.querySelector(".signoff");
  const jid = selected, t = signoffCache[jid];
  const line = (label, body, cls, btn) => box.appendChild(el(`<div class="srow"><span class="l">${label}</span><span class="b ${cls}">${body}</span>${btn || ""}</div>`));
  if (!t) { api(`/jobs/${jid}/trust`).then((r) => { if (!r.error) { signoffCache[jid] = r; if (selected === jid) render(); } }).catch(() => {}); box.appendChild(el(`<div class="muted small-text">加载中…</div>`)); return sec; }
  const f = t.formal, m = t.mutation;
  const fbtn = `<button class="small ghost" data-act="formal">${t.running?.formal ? "验证中…" : f ? "重新验证" : "跑形式验证"}</button>`;
  if (f) line("形式验证", `等价 ${f.equivalence === true ? "<span class='ok'>通过</span>" : f.equivalence === false ? "<span class='bad'>失败</span>" : "不适用"} · 流协议属性 ${f.properties ? "<span class='ok'>通过</span>" : "<span class='bad'>失败</span>"} · 深度 ${f.depth}`, "", fbtn);
  else line("形式验证", t.running?.formal ? "服务器上运行中…" : "未跑 — 实测 RTL 与当前生成器输出的有界等价，加流协议属性", "muted", fbtn);
  const mbtn = `<button class="small ghost" data-act="mutation">${t.running?.mutation ? "注入中…" : m ? "重跑" : "跑变异测试"}</button>`;
  if (m && m.score != null) line("变异分数", `<b class="${m.score >= 0.8 ? "ok" : m.score >= 0.5 ? "warn" : "bad"}">${m.killed}/${m.total} 被抓住 · ${Math.round(m.score * 100)}%</b>${m.survived?.length ? ` · 漏网：<span class="mono">${esc(m.survived.join(", "))}</span>` : ""}`, "", mbtn);
  else if (m && m.baseline === false) line("变异分数", "<span class='bad'>测试台在未变异的 RTL 上就失败</span>", "", mbtn);
  else line("变异分数", t.running?.mutation ? "服务器上运行中…" : "未跑 — 往 RTL 注入 24 处错误，看行为级测试台能抓住多少", "muted", mbtn);
  const seeds = t.seeds || [];
  if (seeds.length > 1) line("布局布线种子", `${seeds.map((x) => fmt(x, 1)).join(" / ")} MHz · 离散 ${fmt(100 * (Math.max(...seeds) - Math.min(...seeds)) / Math.max(...seeds), 1)}%`, "");
  box.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true; b.textContent = b.dataset.act === "formal" ? "验证中…" : "注入中…";
      try {
        let r;
        for (let i = 0; i < 120; i++) { r = await api(b.dataset.act === "formal" ? `/jobs/${jid}/formal?depth=20` : `/jobs/${jid}/mutation`); if (r.status !== "running") break; await new Promise((ok) => setTimeout(ok, 5000)); }
      } catch {}
      delete signoffCache[jid]; render();
    };
  });
  return sec;
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
    hit.onmousemove = (ev) => showTip(ev, `第 ${r.n} 轮\n${label} ${typeof v === "number" ? fmt(v, key === "fmax_mhz" ? 1 : key ? 0 : 3) : v}\n${ok ? "可行" : r.violations.join("\n")}`);
    hit.onmouseleave = hideTip; svg.appendChild(hit);
  });
}

function renderTrajectory(done, c, unit) {
  const p = $("#pane-trajectory"); p.innerHTML = "";
  if (!done.length) { p.appendChild(el(`<div class="muted">还没有结果</div>`)); return; }
  for (const [title, key, target, label, color, opts] of [
    ["Fmax", "fmax_mhz", c.fmax_mhz, "MHz", "var(--accent)", { line: true }],
    ["LUT", "lut", c.max_lut, "LUT", "var(--lut)", { limitKind: "≤" }],
    ["DSP", "dsp", c.max_dsp, "DSP", "var(--dsp)", { limitKind: "≤" }],
    [`吞吐 (${unit})`, null, null, unit, "var(--text-2)", { get: (r) => r.throughput }],
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
  const sec = el(`<div class="sec"><h3>Pareto 前沿 <span class="hint">${esc(backend)} 上实测过 ${data.n_points} 个设计 · ${data.front.length} 个非支配</span></h3><div class="chart"></div><div class="plegend"><span><i style="background:var(--accent)"></i>本次运行</span><span><i style="background:var(--good)"></i>前沿</span><span><i style="background:var(--surface-3)"></i>其他运行</span></div></div>`);
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
      dot.addEventListener("mousemove", (ev) => showTip(ev, `${q.task.slice(0, 40)} · 第 ${q.iteration} 轮\n${fmt(q.fmax_mhz, 0)} MHz · ${q.lut} LUT · ${q.dsp} DSP · ${q.throughput} ${unit}\n${Object.entries(q.ir).map(([k, v]) => k + "=" + JSON.stringify(v)).join(" ")}`));
      dot.addEventListener("mouseleave", hideTip);
      dot.style.cursor = "pointer"; dot.onclick = () => { if (q.job !== selected && jobs.some((j) => j.id === q.job)) select(q.job); };
      svg.appendChild(dot);
    }
    for (const [v, lbl] of [[y1 * 0.9, `${fmt(y1 * 0.9, 0)} MHz`], [c.fmax_mhz, `目标`]]) svg.appendChild(Object.assign(svgEl("text", { x: 2, y: Y(v) + 3, fill: "var(--muted)", "font-size": 9, "font-family": "var(--mono)" }), { textContent: lbl }));
    svg.appendChild(Object.assign(svgEl("text", { x: W - pad.r, y: H - 6, fill: "var(--muted)", "font-size": 9, "text-anchor": "end", "font-family": "var(--mono)" }), { textContent: "DSP →" }));
    box.appendChild(svg);
  });
}

// ---------------------------------------------------------------- memory
function lessonCard(l, design, backend, withSit) {
  const node = el(`<div class="lesson"><div><div class="ltxt">${esc(l.text)}</div>${withSit ? `<div class="sit">${esc((l.situation?.violations || []).join(","))} · 关键路径：${esc(l.situation?.critical || "")} · ${esc((l.situation?.structure || []).join(", "))} · 来自 ${esc(l.run_id || "")}#${l.iteration ?? ""} · 被引用 ${l.retrieved ?? 0} 次</div>` : ""}</div><div class="vote"><span class="sc">${l.score >= 0 ? "+" : ""}${fmt(l.score)}</span><button data-v="up">👍</button><button data-v="down">👎</button></div></div>`);
  node.querySelectorAll("button").forEach((btn) => { btn.onclick = async () => { const r = await post(`/memory/${design}/${backend}/vote`, { id: l.id, vote: btn.dataset.v }); node.querySelector(".sc").textContent = `${r.score >= 0 ? "+" : ""}${fmt(r.score)}`; refreshMemCount(); }; });
  return node;
}

function renderMemoryTab(rows) {
  const p = $("#pane-memory"); p.innerHTML = "";
  const spec = detail.spec, design = spec.design || "matmul", backend = spec.backend || "ecp5";
  const recalled = rows.flatMap((r) => r.memory?.lessons || []);
  const learned = rows.filter((r) => r.lesson).map((r) => r.lesson.lesson);
  if (!recalled.length && !learned.length) { p.appendChild(el(`<div class="muted">${spec.no_memory ? "本运行关闭了设计记忆 (A/B)" : "还没有召回或学到的教训"}</div>`)); return; }
  if (learned.length) { const s = el(`<div class="sec"><h3>本次运行学到的</h3></div>`); for (const l of learned) s.appendChild(lessonCard({ ...l, score: l.outcome === "helped" ? 1 : l.outcome === "hurt" ? -1.5 : 0 }, design, backend)); p.appendChild(s); }
  if (recalled.length) { const s = el(`<div class="sec"><h3>召回的</h3></div>`); const ids = new Set(); for (const l of recalled) { if (ids.has(l.id)) continue; ids.add(l.id); s.appendChild(lessonCard(l, design, backend)); } p.appendChild(s); }
}

// the bench: one column per tag (latest report), one row per case; cells = first-feasible round + best point, red = outside expectation
async function renderBenchView() {
  const box = $("#bench-view"); box.innerHTML = `<div class="muted">加载中…</div>`;
  const b = await api("/bench");
  const tags = Object.keys(b.by_tag || {}).sort((x, y) => (b.by_tag[y].t || 0) - (b.by_tag[x].t || 0));
  box.innerHTML = "";
  box.appendChild(el(`<div class="muted small-text" style="margin-bottom:10px">${esc(b.status || "还没有夜间回归")}</div>`));
  if (!tags.length) { box.appendChild(el(`<div class="muted">没有报告</div>`)); return; }
  const cases = [...new Set(tags.flatMap((t) => Object.keys(b.by_tag[t].cases)))].sort();
  const head = `<tr><th>用例</th>${tags.map((t) => `<th>${esc(t)}<div class="muted small-text">${new Date((b.by_tag[t].t || 0) * 1000).toLocaleDateString()}${b.by_tag[t].planner ? ` · ${esc(b.by_tag[t].planner)}` : ""}${b.by_tag[t].agent ? " · 经对话" : ""}</div></th>`).join("")}</tr>`;
  const rows = cases.map((c) => `<tr><td class="mono">${esc(c)}</td>${tags.map((t) => { const r = b.by_tag[t].cases[c]; if (!r) return "<td class='muted'>–</td>"; if (r.skipped) return `<td class='muted' title="${esc(r.skipped)}">跳过</td>`; const bb = r.best; const drift = r.drift != null ? ` · ${r.drift >= 0 ? "+" : ""}${Math.round(r.drift * 100)}%` : ""; return `<td class="${r.ok ? "" : "bad"}"><a href="#${esc(r.job || "")}">${r.ok ? "✓" : "✕"} ${b.by_tag[t].golden ? "golden" : "@" + (r.first ?? "–")}</a>${bb ? `<div class="muted small-text">${fmt(bb.fmax_mhz)} MHz${drift} · ${bb.lut} ${bb.area_um2 ? "cells" : "LUT"} · ${bb.dsp} DSP</div>` : "<div class='muted small-text'>不可行</div>"}</td>`; }).join("")}</tr>`).join("");
  const sums = `<tr><th>汇总</th>${tags.map((t) => { const s = b.by_tag[t].summary || {}; return `<th class="muted small-text">${s.ok ?? "?"}/${s.n ?? "?"} 达标 · 平均首次可行 ${s.mean_first_feasible ?? "?"} 轮${s.agent_usd != null ? ` · $${s.agent_usd}` : ""}</th>`; }).join("")}</tr>`;
  box.appendChild(el(`<table class="bench"><thead>${head}</thead><tbody>${rows}</tbody><tfoot>${sums}</tfoot></table>`));
  box.querySelectorAll("a[href^='#']").forEach((a) => { a.onclick = (e) => { e.preventDefault(); const id = a.getAttribute("href").slice(1); if (id) { location.hash = id; select(id); } }; });
}

// ---------------------------------------------------------------- catalog: best-so-far per generator x device
async function renderCatalogView() {
  const box = $("#catalog-view"); box.innerHTML = `<div class="muted">加载中…</div>`;
  const rows = await api("/catalog");
  box.innerHTML = "";
  if (!rows.length) { box.appendChild(el(`<div class="muted">还没有实测过的设计</div>`)); return; }
  const res = (p) => p.area_um2 != null ? `${p.lut} cells` : `${p.lut} LUT · ${p.dsp} DSP`;
  const pt = (p, unit) => `<a href="#${esc(p.job)}" title="${esc(JSON.stringify(p.ir))} · ${p.throughput} ${esc(unit)}">${fmt(p.fmax_mhz)} MHz · ${res(p)}</a>`;
  const head = `<tr><th>生成器</th><th>器件</th><th>已验证点</th><th>最快</th><th>最小</th><th>最近运行</th></tr>`;
  const body = rows.map((r) => `<tr><td class="mono">${esc(r.design)}</td><td class="mono">${esc(r.backend)}</td><td>${r.n_points}<div class="muted small-text">${r.n_front} 个在前沿 · ${r.n_runs} 次运行</div></td><td>${pt(r.fastest, r.throughput_unit)}</td><td>${pt(r.smallest, r.throughput_unit)}</td><td><a href="#${esc(r.last_job)}" class="${r.last_status === "done" ? "" : "muted"}">${esc(r.last_task || r.last_job)}</a> <span class="muted small-text">${esc(STATUS_ZH[r.last_status] || r.last_status)}</span></td></tr>`).join("");
  box.appendChild(el(`<table class="bench catalog"><thead>${head}</thead><tbody>${body}</tbody></table>`));
  box.querySelectorAll("a[href^='#']").forEach((x) => { x.onclick = (ev) => { ev.preventDefault(); const id = x.getAttribute("href").slice(1); if (id) { location.hash = id; select(id); } }; });
}

// ---------------------------------------------------------------- proposals: every generator change the agent proposed, its gate verdict, its A/B
async function renderProposalsView() {
  const box = $("#proposals-view"); box.innerHTML = `<div class="muted">加载中…</div>`;
  const rows = await api("/proposals");
  box.innerHTML = "";
  if (!rows.length) { box.appendChild(el(`<div class="muted">还没有提案 — 在对话里把智能体切到<b>改生成器</b>模式，让它改生成器（加旋钮、加新设计类型），提案会连同门禁报告出现在这里</div>`)); return; }
  const want = location.hash.startsWith("#proposals/") ? location.hash.slice("#proposals/".length) : null;
  const counts = rows.reduce((c, p) => { c[p.status] = (c[p.status] || 0) + 1; return c; }, {});
  box.appendChild(el(`<div class="muted small-text" style="margin-bottom:10px">${rows.length} 份提案 · ${Object.entries(counts).map(([k, v]) => `${v} ${esc(PROP_ZH[k] || k)}`).join(" · ")}${rows.some((p) => p.merged) ? ` · ${rows.filter((p) => p.merged).length} 已合并` : ""}</div>`));
  for (const p of rows) {
    const verdict = p.status === "gating" ? `<span class="live">门禁中…</span>` : p.status === "passed" ? `<span class="ok">门禁通过</span>` : `<span class="bad">门禁未通过${p.scope_errors?.length ? " · 范围" : p.apply_error ? " · 补丁" : ""}</span>`;
    const ab = p.ab?.job ? (() => { const j = jobs.find((x) => x.id === p.ab.job); const b = j?.summary?.best; return `A/B 实测 <a href="#${esc(p.ab.job)}">${esc(p.ab.job)}</a>${b ? ` · ${fmt(b.metrics.fmax_mhz)} MHz · ${b.metrics.lut} ${b.metrics.area_um2 ? "cells" : "LUT"} · ${b.metrics.dsp} DSP` : j ? ` · ${esc(j.status)}` : ""}`; })() : p.ab?.error ? `<span class="bad">A/B: ${esc(p.ab.error.slice(0, 120))}</span>` : "";
    const g = el(`<div class="memgroup prop${want === p.id ? " sel" : ""}" id="prop-${esc(p.id)}">
      <h3>${esc(p.title || p.id)} <span class="muted">${new Date((p.created || 0) * 1000).toLocaleString()}${p.thread ? ` · <a href="/?thread=${encodeURIComponent(p.thread)}" target="_blank">对话</a>` : ""}</span></h3>
      <div class="small-text">${verdict} · <span class="mono">${esc((p.files || []).map((f) => f.replace(/^silicon_agent\//, "")).join(", "))}</span>${p.diff_lines ? ` · ${p.diff_lines} 行` : ""} · 生成器 ${esc((p.designs || []).join(", "))}${p.merged ? ` · <span class="ok">已合并</span>` : ""}</div>
      ${p.rationale ? `<p class="muted small-text">${esc(p.rationale)}</p>` : ""}
      <div class="gate-steps">${(p.steps || []).map((s) => `<div class="gstep"><span class="${s.ok ? "ok" : s.ok === false ? "bad" : "muted"}">${s.ok ? "✓" : s.ok === false ? "✕" : "·"}</span> ${esc(s.name)}${s.seconds != null ? ` <span class="muted">${s.seconds} s</span>` : ""}${s.mutation ? `<div class="muted small-text">${s.mutation.map((x) => x.baseline === false ? `${esc(x.design)}: <span class="bad">测试台在未变异的 RTL 上就失败</span>` : `${esc(x.design)}: ${x.killed}/${x.total} 被抓住${x.survived?.length ? ` · 漏网 ${esc(x.survived.join(", "))}` : ""}`).join(" · ")}</div>` : ""}${s.equivalence ? `<div class="muted small-text">${s.equivalence.map((x) => `${esc(x.case)}: ${x.identical ? "完全相同" : x.ok ? "等价 @" + x.depth : "<span class='bad'>不等价</span>"}`).join(" · ")}</div>` : ""}${s.failures?.length ? `<pre class="log">${esc(s.failures.join("\n").slice(0, 1200))}</pre>` : ""}${s.error ? `<pre class="log">${esc(s.error)}</pre>` : ""}</div>`).join("")}</div>
      ${p.scope_errors?.length ? `<div class="bad small-text">${esc(p.scope_errors.join("; "))}</div>` : ""}${p.apply_error ? `<pre class="log">${esc(p.apply_error)}</pre>` : ""}
      ${ab ? `<div class="small-text">${ab}</div>` : ""}
      <div class="row gap"><button class="ghost small diff">查看 diff</button>${p.status === "passed" && !p.merged ? `<code class="mono muted small-text" title="由操作员在服务器上执行">deploy/merge-proposal.sh ${esc(p.id)}</code>` : ""}</div>
    </div>`);
    g.querySelector("button.diff").onclick = async (e) => { let pre = g.querySelector("pre.diff"); if (pre) { pre.remove(); e.target.textContent = "查看 diff"; return; } const r = await api(`/proposals/${p.id}`); pre = el(`<pre class="log diff"></pre>`); pre.textContent = r.diff || ""; g.appendChild(pre); e.target.textContent = "收起 diff"; };
    g.querySelectorAll("a[href^='#']").forEach((x) => { x.onclick = (ev) => { ev.preventDefault(); const id = x.getAttribute("href").slice(1); location.hash = id; select(id); }; });
    box.appendChild(g);
  }
  if (want) document.getElementById(`prop-${want}`)?.scrollIntoView({ block: "start" });
  if (rows.some((p) => p.status === "gating")) setTimeout(() => { if (view === "proposals") renderProposalsView(); }, 8000);
}

let memWired = false;
async function memSearch() {
  const q = $("#mem-q").value.trim(), list = $("#memory-list");
  if (!q) return renderMemoryView();
  const qs = new URLSearchParams({ q, k: "20", ...($("#mem-design").value ? { design: $("#mem-design").value } : {}), ...($("#mem-backend").value ? { backend: $("#mem-backend").value } : {}) });
  const hits = await api(`/memory/search?${qs}`);
  if ($("#mem-q").value.trim() !== q) return;
  list.innerHTML = "";
  if (!hits.length) { list.appendChild(el(`<div class="muted">没有匹配的教训</div>`)); return; }
  for (const l of hits) {
    const card = lessonCard(l, l.design, l.backend, true);
    card.querySelector(".sit").insertAdjacentHTML("afterbegin", `<span class="pair ${l.transfer ? "transfer" : ""}">${esc(l.design)} / ${esc(l.backend)}${l.transfer ? " · 迁移" : ""}</span> · `);
    list.appendChild(card);
  }
}
async function renderMemoryView() {
  const list = $("#memory-list"); list.innerHTML = `<div class="muted">加载中…</div>`;
  if (!memWired) {
    memWired = true;
    try { const sc = await api("/schema"); for (const d of Object.keys(sc.designs || {})) $("#mem-design").appendChild(el(`<option value="${esc(d)}">${esc(d)}</option>`)); for (const b of Object.keys(sc.backends || {})) $("#mem-backend").appendChild(el(`<option value="${esc(b)}">${esc(b)}</option>`)); } catch {}
    let tmr = null;
    const go = () => { clearTimeout(tmr); tmr = setTimeout(memSearch, 250); };
    $("#mem-q").oninput = go; $("#mem-design").onchange = go; $("#mem-backend").onchange = go;
  }
  if ($("#mem-q").value.trim()) return memSearch();
  const mem = await api("/memory");
  list.innerHTML = "";
  if (!mem.length) { list.appendChild(el(`<div class="muted">还是空的 — 第一次跑够两轮以上就会有教训</div>`)); return; }
  for (const g of mem) {
    const grp = el(`<div class="memgroup"><h3>${esc(g.design)} · ${esc(g.backend)} <span class="muted">${g.count} 条教训</span></h3></div>`);
    for (const l of g.lessons) grp.appendChild(lessonCard(l, g.design, g.backend, true));
    list.appendChild(grp);
  }
}

// theme: dark / light / auto, remembered in localStorage (the <html> attribute is set before paint by an inline script)
(() => {
  const order = ["dark", "light", "auto"], btn = document.getElementById("theme");
  if (!btn) return;
  const label = () => { const t = document.documentElement.dataset.theme || "dark"; btn.textContent = t === "dark" ? "◐" : t === "light" ? "☀" : "◑"; btn.title = `theme: ${t}`; };
  btn.onclick = () => { const t = document.documentElement.dataset.theme || "dark"; const n = order[(order.indexOf(t) + 1) % order.length]; document.documentElement.dataset.theme = n; localStorage.setItem("sa.theme", n); label(); };
  label();
})();
