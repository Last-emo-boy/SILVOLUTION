// silicon-agent chat. Plain ES module, no build step.
// One conversation per design task: the user writes what they want, the agent answers in prose and
// attaches collapsed cards (understood spec, progress/trajectory, result). Details live inside the cards.

const $ = (s) => document.querySelector(s);
const api = async (path, opts) => {
  const r = await fetch(`/api${path}`, { cache: "no-store", ...opts });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 300)}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
};
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const fmt = (n, d = 1) => (n == null ? "–" : Number(n).toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
// UI language: the conversation's (first user message) if known, else the browser's
let zh = (navigator.language || "").toLowerCase().startsWith("zh");
const hasHan = (s) => /[\u4e00-\u9fff]/.test(s || "");
const L = {};
L.zh = {
  hello: "你想要什么硬件？", blurb: "用一句话描述。我会生成 Verilog，跑仿真、综合和布局布线，直到满足要求，然后把结果交给你。",
  placeholder: "描述你要的硬件，或者对当前结果提要求…", hint: "⏎ 发送 · ⇧⏎ 换行", stop: "■ 停止", thinking: "思考中", newChat: "新对话",
  spec: "理解的规格", progress: "运行中", traj: "轨迹", result: "结果", noResult: "未找到可行设计", iter: "第 {n} 轮", iters: "{n} 轮", feasible: "{n} 轮可行",
  planning: "规划下一轮", sim: "行为级仿真", synth: "综合", pnr: "布局布线", queued: "排队中", stopped: "已停止", error: "出错",
  pass: "通过", fail: "失败", viewRtl: "查看 Verilog", hideRtl: "收起 Verilog", download: "下载 {f}", worktable: "打开 worktable", refine: "在此基础上继续",
  stages: ["行为级仿真", "综合后功能", "综合后时序", "实现后功能", "实现后时序"], signoff: "实现后时序仿真通过",
  design: "设计", backend: "器件", constraints: "约束", objective: "目标", locked: "固定参数", start: "起点", closest: "最接近",
  secs: "秒", critical: "关键路径", startFrom: "从起点设计开始。", fork: "基于上一次的最佳设计继续。",
  examples: ["一个 32 阶低通 FIR，Spartan-7，采样率 250 MHz，尽量少用 DSP", "INT8 矩阵乘，Lattice ECP5，180 MHz 以上，48 个 DSP 以内",
             "Spartan-7 上跑 300 MHz 的 INT8 systolic 阵列，DSP 不超过 32", "一个小的 16 阶 FIR，16 位系数，越省资源越好"],
};
L.en = {
  hello: "What hardware do you want?", blurb: "Describe it in a sentence. I generate the Verilog, run simulation, synthesis and place-and-route until it meets your requirements, then hand you the result.",
  placeholder: "Describe the hardware you want, or ask for changes to the current result…", hint: "⏎ send · ⇧⏎ newline", stop: "■ Stop", thinking: "Thinking", newChat: "New chat",
  spec: "Understood spec", progress: "Running", traj: "Trajectory", result: "Result", noResult: "No feasible design", iter: "iteration {n}", iters: "{n} iterations", feasible: "{n} feasible",
  planning: "planning", sim: "behavioral sim", synth: "synthesis", pnr: "place & route", queued: "queued", stopped: "stopped", error: "error",
  pass: "pass", fail: "fail", viewRtl: "View Verilog", hideRtl: "Hide Verilog", download: "Download {f}", worktable: "Open worktable", refine: "Refine from here",
  stages: ["behavioral", "post-synth func", "post-synth timing", "post-impl func", "post-impl timing"], signoff: "post-implementation timing sim passed",
  design: "design", backend: "device", constraints: "constraints", objective: "objective", locked: "locked", start: "start", closest: "closest",
  secs: "s", critical: "critical path", startFrom: "Start from the initial design.", fork: "Continue from the previous best design.",
  examples: ["A 32-tap lowpass FIR on Spartan-7 at 250 MS/s, as few DSPs as possible", "INT8 matrix multiply on Lattice ECP5, 180 MHz or better, at most 48 DSPs",
             "An INT8 systolic array on Spartan-7 running at 300 MHz with no more than 32 DSPs", "A small 16-tap FIR with 16-bit coefficients, as cheap as possible"],
};
let T = zh ? L.zh : L.en;
const t = (k, v) => T[k].replace("{n}", v).replace("{f}", v);
function setLang(isZh) { zh = isZh; T = zh ? L.zh : L.en; $("#input").placeholder = T.placeholder; $("#chint").textContent = T.hint; $("#stop").textContent = T.stop; }
const STAGE_KEYS = ["behavioral", "post_synth_func", "post_synth_time", "post_impl_func", "post_impl_time"];
const TOP = { matmul: "matmul_top", fir: "fir_top" };
const DEV = { ecp5: "ECP5 LFE5U-45F", xc7s50: "Spartan-7 XC7S50" };

// ---------------------------------------------------------------- state
let threads = [], tid = null, thread = null, timer = null;
const jobs = {};                 // job id -> {events, seen, status, summary, error, spec, max_iter}
const open = new Set();          // "<job>:<card>" of expanded cards
const rtlShown = new Set();

// ---------------------------------------------------------------- boot
(async function boot() {
  $("#hello h1").textContent = T.hello; $("#hello p").textContent = T.blurb; $("#input").placeholder = T.placeholder; $("#chint").textContent = T.hint; $("#stop").textContent = T.stop; $("#new-chat").title = T.newChat;
  for (const ex of T.examples) { const b = el(`<button>${esc(ex)}</button>`); b.onclick = () => send(ex); $("#examples").appendChild(b); }
  try { $("#who").textContent = (await api("/me")).subject; } catch { $("#who").textContent = "unauthenticated"; }
  $("#new-chat").onclick = () => openThread(null);
  $("#send").onclick = () => send();
  $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("#input").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 180) + "px"; });
  $("#t-rail").onclick = () => { const sh = $(".shell"); if (window.innerWidth <= 800) sh.classList.toggle("rail-shown"); else sh.classList.toggle("rail-hidden"); };
  $("#stop").onclick = async () => { const j = liveJob(); if (j) { await post(`/jobs/${j}/stop`); $("#stop").disabled = true; } };
  await refreshThreads();
  setInterval(refreshThreads, 6000);
  const h = location.hash.slice(1);
  if (h) openThread(h);
  $("#input").focus();
})();

// ---------------------------------------------------------------- rail
async function refreshThreads() {
  threads = await api("/threads");
  const ul = $("#threads"); ul.innerHTML = "";
  for (const th of threads) {
    const b = th.best?.metrics;
    const m = th.status === "done" && b ? `${fmt(b.fmax_mhz, 0)} MHz · ${b.dsp} DSP` : th.status ? ({ running: T.progress, queued: T.queued, failed: T.noResult, error: T.error, interrupted: T.stopped }[th.status] || th.status) : "";
    const li = el(`<li class="${th.id === tid ? "sel" : ""}" data-id="${esc(th.id)}" title="${esc(th.title)}"><span class="dot ${th.status || ""}"></span><div><div class="t">${esc(th.title)}</div><div class="m">${esc(m)}</div></div></li>`);
    li.onclick = () => openThread(th.id);
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- thread
function openThread(id) {
  clearTimeout(timer);
  tid = id; thread = null; open.clear(); rtlShown.clear();
  if (!id) setLang((navigator.language || "").toLowerCase().startsWith("zh"));
  location.hash = id || "";
  $("#msgs").innerHTML = ""; $("#hello").hidden = !!id; $("#title").textContent = ""; $("#stop").hidden = true;
  document.querySelectorAll("#threads li").forEach((li) => li.classList.toggle("sel", li.dataset.id === id));
  $(".shell").classList.remove("rail-shown");
  if (id) poll();
  $("#input").focus();
}

function liveJob() {
  if (!thread) return null;
  const ids = thread.messages.filter((m) => m.job).map((m) => m.job);
  return ids.reverse().find((j) => ["queued", "running"].includes((jobs[j] || thread.jobs[j])?.status)) || null;
}

async function poll() {
  if (!tid) return;
  try { thread = await api(`/threads/${tid}`); } catch (e) { if (/404/.test(e.message)) { openThread(null); return; } throw e; }
  // pull event streams for every job this conversation talks about
  for (const jid of new Set(thread.messages.filter((m) => m.job && m.kind === "run").map((m) => m.job))) {
    const j = jobs[jid] || (jobs[jid] = { events: [], seen: 0 });
    if (j.done) continue;
    const d = await api(`/jobs/${jid}?after=${j.seen}`);
    j.events.push(...d.events); j.seen = d.n_events;
    Object.assign(j, { status: d.status, summary: d.summary, error: d.error, spec: d.spec, max_iter: d.max_iter, parent: d.parent });
    if (!["queued", "running"].includes(d.status)) j.done = true;
  }
  render();
  const live = liveJob();
  const busy = thread.busy || !!live || thread.messages.some((m) => m.kind === "run" && !m.narrated && jobs[m.job]?.done);
  timer = setTimeout(poll, busy ? 2000 : 8000);
}

// ---------------------------------------------------------------- send
async function send(text) {
  const ta = $("#input"), msg = (text ?? ta.value).trim();
  if (!msg) return;
  ta.value = ""; ta.style.height = "auto";
  $("#hello").hidden = true;
  // optimistic echo
  if (!thread) thread = { id: tid, title: "", messages: [], jobs: {}, busy: true };
  thread.messages.push({ role: "user", kind: "text", text: msg, t: Date.now() / 1000 }); thread.busy = true;
  render();
  $("#scroll").scrollTop = $("#scroll").scrollHeight;
  try {
    const r = await post(`/threads/${tid || "new"}/chat`, { text: msg });
    if (!tid) { tid = r.thread; location.hash = tid; }
    clearTimeout(timer); await poll(); refreshThreads();
  } catch (e) {
    thread.messages.push({ role: "assistant", kind: "text", text: e.message, t: Date.now() / 1000 }); thread.busy = false; render();
  }
}

// ---------------------------------------------------------------- render
function render() {
  if (!thread) return;
  const first = thread.messages.find((m) => m.role === "user");
  if (first && hasHan(first.text) !== zh) { setLang(hasHan(first.text)); $("#msgs").innerHTML = ""; }
  $("#title").textContent = thread.title || "";
  const live = liveJob();
  $("#stop").hidden = !live; $("#stop").disabled = false;
  const sc = $("#scroll"), atBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
  const ol = $("#msgs");
  const existing = [...ol.children];
  thread.messages.forEach((m, i) => {
    const sig = msgSignature(m);
    const cur = existing[i];
    if (cur && cur.dataset.sig === sig) return;
    const li = buildMsg(m); li.dataset.sig = sig;
    if (cur) ol.replaceChild(li, cur); else ol.appendChild(li);
  });
  for (let i = thread.messages.length; i < existing.length; i++) existing[i].remove();
  let th = ol.querySelector("li.thinking-row");
  if (thread.busy && !th) ol.appendChild(el(`<li class="msg assistant thinking-row"><span class="thinking"><i></i>${T.thinking}</span></li>`));
  if (!thread.busy && th) th.remove();
  if (atBottom) sc.scrollTop = sc.scrollHeight;
}

function msgSignature(m) {
  const j = m.job && jobs[m.job];
  return JSON.stringify([m.role, m.kind, m.text, m.job, j?.seen, j?.status, j?.error && 1, [...open].filter((k) => k.startsWith(m.job + ":")), rtlShown.has(m.job)]);
}

function buildMsg(m) {
  if (m.role === "user") return el(`<li class="msg user"><div class="bubble">${esc(m.text)}</div></li>`);
  const li = el(`<li class="msg assistant"></li>`);
  if (m.text) li.appendChild(el(`<div class="prose ${m.kind === "run" ? "dim" : ""}">${esc(m.text)}</div>`));
  if (m.kind === "run" && m.job) {
    const j = jobs[m.job], spec = j?.spec || thread.jobs[m.job]?.spec;
    if (spec) li.appendChild(specCard(m.job, spec, m.fork));
    if (j) {
      const rows = turns(j.events), done = rows.filter((r) => !r.pending);
      const liveRow = rows.find((r) => r.pending);
      if (["queued", "running"].includes(j.status) || done.length) li.appendChild(trajCard(m.job, j, rows, done, liveRow, spec));
      if (j.done) li.appendChild(resultCard(m.job, j, done, spec));
    }
  }
  return li;
}

// ---------------------------------------------------------------- cards
function card(key, label, sub, cls = "") {
  const d = el(`<details class="card ${cls}" ${open.has(key) ? "open" : ""}><summary><span class="ic">▶</span><span class="lbl">${label}</span><span class="sub">${sub}</span></summary><div class="inner"></div></details>`);
  d.addEventListener("toggle", () => { if (d.open) open.add(key); else open.delete(key); const li = d.closest("li"); if (li) li.dataset.sig = ""; });
  return d;
}
const chips = (ir, prev, locked) => `<div class="chips">` + Object.entries(ir).filter(([k]) => k !== "coefs").map(([k, v]) => {
  const lock = locked && k in locked, changed = !lock && prev && JSON.stringify(prev[k]) !== JSON.stringify(v);
  return `<span class="chip ${lock ? "lock" : changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
}).join("") + `</div>`;
const consText = (c) => [`≥${c.fmax_mhz} MHz`, c.max_dsp != null ? `≤${c.max_dsp} DSP` : "", c.max_lut != null ? `≤${c.max_lut} LUT` : ""].filter(Boolean).join(" · ");

function specCard(jid, spec, fork) {
  const c = spec.constraints || {};
  const d = card(`${jid}:spec`, T.spec, `${spec.design} · ${spec.backend} · ${consText(c)}`);
  const inner = d.querySelector(".inner");
  inner.appendChild(el(`<div class="kv">
    <span class="k">${T.design}</span><span>${esc(spec.design)} — ${esc(spec.task || "")}</span>
    <span class="k">${T.backend}</span><span>${esc(DEV[spec.backend] || spec.backend)}</span>
    <span class="k">${T.constraints}</span><span>${esc(consText(c))}</span>
    <span class="k">${T.objective}</span><span class="mono">${esc((c.objective || []).join(" › "))}</span>
    <span class="k">${T.locked}</span><span>${chips(spec.locked || {}, null, spec.locked)}</span>
    <span class="k">${T.start}</span><span>${fork ? `<div class="muted" style="margin-bottom:4px">${T.fork}</div>` : ""}${chips(Object.fromEntries(Object.entries(spec.initial_ir || {}).filter(([k]) => !(k in (spec.locked || {})))), null, null)}</span>
  </div>`));
  return d;
}

function turns(events) {
  const rows = new Map(); let pendingSteer = [];
  const get = (n) => { if (!rows.has(n)) rows.set(n, { n, pending: true, user: [], stages: {} }); return rows.get(n); };
  for (const e of events) {
    switch (e.kind) {
      case "user": pendingSteer.push(e); break;
      case "steer": get(e.iteration).user.push(...pendingSteer.splice(0)); break;
      case "plan": { const prev = rows.get(e.iteration - 1); if (prev) prev.next = e; if (!e.stop) get(e.iteration).plan = e; break; }
      case "evaluating": get(e.iteration).ir = e.ir; break;
      case "stage": get(e.iteration).stages[e.stage] = e; break;
      case "result": Object.assign(get(e.iteration), e, { pending: false }); break;
    }
  }
  const out = [...rows.values()].sort((a, b) => a.n - b.n);
  if (pendingSteer.length && out.length) out.at(-1).userAfter = pendingSteer;
  return out;
}

function stageChips(r, backend) {
  const xc = backend === "xc7s50";
  const st = r.pending ? r.stages : (r.metrics?.sim_stages || {});
  const keys = xc ? STAGE_KEYS : ["behavioral", "post_synth_func"];
  return `<div class="stages">` + keys.map((k, i) => {
    let cls;
    if (k === "behavioral") cls = r.pending ? (r.ir ? "active" : "todo") : r.metrics?.sim_passed ? "done" : "fail";
    else { const s = st[k]; cls = !s ? "todo" : s.passed === true ? "done" : s.passed === false ? "fail" : "todo"; }
    return `<span class="${cls}"><i></i>${T.stages[STAGE_KEYS.indexOf(k)]}</span>`;
  }).join("") + `</div>`;
}

function phase(liveRow, j) {
  if (j.status === "queued") return T.queued;
  if (!liveRow) return T.planning;
  if (!liveRow.ir) return T.planning;
  const st = Object.keys(liveRow.stages);
  if (st.length) return T.stages[Math.max(...st.map((k) => STAGE_KEYS.indexOf(k)))];
  return `${T.sim} · ${T.synth} · ${T.pnr}`;
}

function trajCard(jid, j, rows, done, liveRow, spec) {
  const live = ["queued", "running"].includes(j.status), unit = j.summary?.throughput_unit || (spec.design === "fir" ? "samples/cycle" : "MAC/cycle");
  const feas = done.filter((r) => r.violations?.length === 0).length;
  const sub = live ? `<span class="live">${t("iter", liveRow?.n ?? done.length + 1)} / ${j.max_iter} · ${phase(liveRow, j)}</span>` : `${t("iters", done.length)}${feas ? ` · ${t("feasible", feas)}` : ""}`;
  const d = card(`${jid}:traj`, live ? T.progress : T.traj, sub);
  const inner = d.querySelector(".inner"), list = el(`<div class="iters"></div>`);
  let prev = null;
  for (const r of rows) {
    const m = r.metrics, ok = m && r.violations.length === 0;
    const what = r.n === 1 ? (j.parent ? T.fork : T.startFrom) : (r.plan?.action ?? r.action ?? "");
    const row = el(`<div class="iter"><span class="n ${r.pending ? "live" : ""}">${r.n}</span><div>
      ${r.user.map((u) => `<div class="user">${esc(u.message)}</div>`).join("")}
      <div class="what">${esc(what)}</div>
      ${r.ir ? chips(r.ir, prev, spec.locked) : ""}
      ${r.pending ? `<div class="nums"><span class="live" style="color:var(--accent)">${esc(phase(r, j))}</span></div>` : `<div class="nums"><span class="${ok ? "v" : "x"}">${ok ? "✓" : "✕"}</span> <b>${fmt(m.fmax_mhz)} MHz</b> · ${m.lut} LUT · ${m.dsp} DSP · ${r.throughput} ${esc(unit)} · ${r.eda_seconds}${T.secs}${ok ? "" : ` · <span class="x">${esc(r.violations.join(", "))}</span>`}</div>`}
      ${r.ir ? stageChips(r, spec.backend) : ""}
      ${r.n > 1 && (r.plan?.diagnosis ?? r.diagnosis) ? `<div class="why">${esc(r.plan?.diagnosis ?? r.diagnosis)}</div>` : ""}
      ${!r.pending && m.critical_path?.length > 1 ? `<div class="cp"><span>${T.critical} ${fmt(m.critical_path[0].total_ns, 2)} ns:</span>${m.critical_path.slice(1).map((s) => `<span>${esc(s.kind)} <i>${esc(s.rtl.split(".").slice(-2).join("."))}</i> ${fmt(s.logic_ns + s.route_ns, 2)}</span>`).join("")}</div>` : ""}
      ${r.next?.stop ? `<div class="why">${esc(r.next.action)}</div>` : ""}
      ${(r.userAfter || []).map((u) => `<div class="user">${esc(u.message)}</div>`).join("")}
    </div></div>`);
    list.appendChild(row);
    if (!r.pending) prev = r.ir;
  }
  if (!rows.length) list.appendChild(el(`<div class="muted">${j.status === "queued" ? T.queued : T.planning}…</div>`));
  inner.appendChild(list);
  if (j.error) inner.appendChild(el(`<pre class="code">${esc(j.error)}</pre>`));
  return d;
}

function resultCard(jid, j, done, spec) {
  const best = j.summary?.best, unit = j.summary?.throughput_unit || "", top = TOP[spec.design] || "top", xc = spec.backend === "xc7s50";
  const target = done.filter((r) => r.violations?.length).sort((a, b) => a.violations.length - b.violations.length || b.n - a.n)[0];
  const stg = best?.metrics?.sim_stages || {};
  const signoff = xc && stg.post_impl_time?.passed === true;
  let sub;
  if (best) sub = `<span class="ok">${top}.sv</span> · ${fmt(best.metrics.fmax_mhz)} MHz · ${best.metrics.dsp} DSP · ${best.metrics.lut} LUT · ${best.throughput} ${esc(unit)}${signoff ? ` · ${T.signoff}` : ""}`;
  else if (j.status === "interrupted") sub = `<span class="bad">${T.stopped}</span>`;
  else if (j.status === "error") sub = `<span class="bad">${T.error}</span>`;
  else sub = `<span class="bad">${T.noResult}</span>${target ? ` · ${T.closest}: ${fmt(target.metrics.fmax_mhz, 0)} MHz · ${target.metrics.dsp} DSP · ${esc(target.violations.join(", "))}` : ""}`;
  const d = card(`${jid}:result`, T.result, sub, "result");
  const inner = d.querySelector(".inner");
  const row = best || target;
  if (row) {
    const m = row.metrics;
    inner.appendChild(el(`<div class="head"><b>${fmt(m.fmax_mhz)} MHz</b><span>${m.lut} LUT</span><span>${m.dsp} DSP</span><span>${m.ff ?? "–"} FF</span><span>${row.throughput} ${esc(unit)}</span>${row.latency_cycles != null ? `<span>latency ${row.latency_cycles} cyc</span>` : ""}</div>`));
    inner.appendChild(el(chips({ ...(spec.locked || {}), ...row.ir }, null, spec.locked)));
    // simulation ladder
    const tbl = el(`<table class="simtable"></table>`);
    const purposes = zh ? ["RTL 逻辑 vs Python 黄金模型（cocotb + iverilog）", "综合后网表，unisim 模型，无延迟", "综合后网表 + 估算 SDF 延迟，目标周期", "布线后网表，无延迟", "布线后网表 + 真实 SDF 延迟，目标周期 — 签核"]
                        : ["RTL vs Python golden model (cocotb + iverilog)", "synthesized netlist, unisim models, no delays", "synthesized netlist with estimated SDF delays at target period", "placed-and-routed netlist, no delays", "placed-and-routed netlist with real SDF delays at target period — the signoff"];
    STAGE_KEYS.forEach((k, i) => {
      if (!xc && i > 1) return;
      let st, log = "";
      if (k === "behavioral") st = m.sim_passed ? "pass" : "fail";
      else { const s = (m.sim_stages || {})[k]; st = !s ? "–" : s.passed === true ? "pass" : s.passed === false ? "fail" : "skipped"; if (s?.passed === false) log = s.log || ""; }
      tbl.insertAdjacentHTML("beforeend", `<tr><td class="st ${st === "pass" ? "ok" : st === "fail" ? "bad" : "na"}">${st === "pass" ? T.pass : st === "fail" ? T.fail : st}</td><td>${i + 1} · ${T.stages[i]}<div class="p">${purposes[i]}</div>${log ? `<div class="log">${esc(log.slice(-600))}</div>` : ""}</td></tr>`);
    });
    inner.appendChild(tbl);
  }
  const links = el(`<div class="links"></div>`);
  if (best) {
    const view = el(`<button>${rtlShown.has(jid) ? T.hideRtl : T.viewRtl}</button>`);
    const dl = el(`<button>${t("download", top + ".sv")}</button>`);
    view.onclick = async () => { if (rtlShown.has(jid)) rtlShown.delete(jid); else rtlShown.add(jid); render(); };
    dl.onclick = async () => { const txt = await api(`/jobs/${jid}/rtl`); const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" })); a.download = `${top}.sv`; a.click(); };
    links.append(view, dl);
  }
  links.appendChild(el(`<a href="/work#${esc(jid)}" target="_blank">${T.worktable}</a>`));
  const refine = el(`<button>${T.refine}</button>`); refine.onclick = () => { $("#input").focus(); };
  links.appendChild(refine);
  inner.appendChild(links);
  if (best && rtlShown.has(jid)) {
    const pre = el(`<pre class="code">…</pre>`);
    api(`/jobs/${jid}/rtl`).then((txt) => { pre.textContent = txt; }).catch((e) => { pre.textContent = e.message; });
    inner.appendChild(pre);
  }
  return d;
}
