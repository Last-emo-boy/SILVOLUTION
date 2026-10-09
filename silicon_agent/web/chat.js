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
  pass: "通过", fail: "失败", viewRtl: "查看 Verilog", hideRtl: "收起 Verilog", download: "下载 {f}", pkg: "下载交付包 (zip)", worktable: "打开 worktable", compare: "与上一次比较", left: "剩余（最多）", del: "删除对话", alts: "也可以试试：", designs: "个设计", covTitle: "行为级仿真覆盖的场景（次数）", formal: "形式验证", formalTitle: "SymbiYosys：实测 RTL 与当前生成器输出的有界等价 + 流协议属性", formalRunning: "验证中…", queued: "排队中，等这一轮结束后发送", attach: "附加 RTL 文件（.sv / .v）：用你自己的设计跑同一条阶梯", tooBig: "文件超过 400 KB", attachLead: "这是我自己的 RTL，请用同一条阶梯（仿真、综合、布局布线、形式验证）评估并优化它暴露的参数。", failedCalls: "{n} 次失败", modeFast: "快", modeDeep: "深思", modeAuthor: "改生成器", proposal: "生成器改动提案", gating: "门禁中…", gatePassed: "门禁通过", gateFailed: "门禁未通过", gateSteps: ["发射 + 展开所有预设", "行为级仿真", "形式等价（未改动的 IR）", "测试台变异分数"], abRun: "A/B 实测", merged: "已合并", viewDiff: "查看 diff", lines: "行", modeTitle: "智能体模式（下一轮生效）：", pendingMode: "新对话将以此模式开始", agentSteps: "智能体过程 · {n} 次工具调用", usage: "{calls} 次模型调用 · {tok} tokens · {usd} · {s}", usageTitle: "Agnes Harness 账本：这一轮的模型调用、输入/输出 token（含缓存命中）、估算费用与用时", sessUsage: "本会话 {turns} 轮 · {tok} tokens · {usd}", sessUsageTitle: "本会话累计（Agnes Harness 账本）· 当前上下文 {ctx} tokens", today: "今天", earlier: "更早", agentAt: "智能体 ·", runs: "次运行", resumed: "重启后续接", resumedTitle: "服务器重启打断了上一轮，智能体根据自己的记录继续", delConfirm: "删除这个对话？运行结果会保留在 worktable 的未归属列表里。", rename: "重命名对话：", refine: "在此基础上继续",
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
  pass: "pass", fail: "fail", viewRtl: "View Verilog", hideRtl: "Hide Verilog", download: "Download {f}", pkg: "Download package (zip)", worktable: "Open worktable", compare: "Compare with previous", left: "left at most", del: "Delete conversation", alts: "Also worth trying:", designs: "designs", covTitle: "scenarios the behavioral testbench exercised (hit counts)", formal: "Formal check", formalTitle: "SymbiYosys: bounded equivalence of the measured RTL vs the current generator + stream properties", formalRunning: "checking…", queued: "queued · sent when this turn ends", attach: "Attach RTL (.sv / .v): run your own design through the same ladder", tooBig: "file over 400 KB", attachLead: "This is my own RTL. Run it through the same ladder (simulation, synthesis, place-and-route, formal) and tune the parameters it exposes.", failedCalls: "{n} failed", modeFast: "fast", modeDeep: "deep", modeAuthor: "author", proposal: "Generator change proposal", gating: "gating…", gatePassed: "gate passed", gateFailed: "gate failed", gateSteps: ["emit + elaborate every preset", "behavioral simulation", "formal equivalence (unchanged IRs)", "testbench mutation score"], abRun: "measured A/B", merged: "merged", viewDiff: "View diff", lines: "lines", modeTitle: "agent mode (from the next turn): ", pendingMode: "a new conversation starts in this mode", agentSteps: "Agent steps · {n} tool calls", usage: "{calls} model calls · {tok} tokens · {usd} · {s}", usageTitle: "Agnes Harness ledger: this turn's model calls, input/output tokens (cache hits included), estimated cost and wall time", sessUsage: "{turns} turns · {tok} tokens · {usd}", sessUsageTitle: "this conversation so far (Agnes Harness ledger) · context {ctx} tokens", today: "Today", earlier: "Earlier", agentAt: "agent ·", runs: "runs", resumed: "resumed", resumedTitle: "a server restart cut the previous turn short; the agent continued from its own records", delConfirm: "Delete this conversation? Its runs stay available under the unassigned worktable.", rename: "Rename conversation:", refine: "Refine from here",
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
const TOP = { matmul: "matmul_top", fir: "fir_top", conv2d: "conv2d_top", fir_chain: "fir_chain_top", cordic: "cordic_top", fft: "fft_top", graph: "graph_top" };
let DEV = { ecp5: "ECP5 LFE5U-45F", xc7s50: "Spartan-7 XC7S50", ice40: "iCE40 UP5K", gowin: "Gowin GW1NR-9" };
let BSTAGES = { ecp5: ["behavioral", "post_synth_func"], xc7s50: STAGE_KEYS_ALL(), ice40: ["behavioral", "post_synth_func"], gowin: ["behavioral", "post_synth_func"] };
function STAGE_KEYS_ALL() { return ["behavioral", "post_synth_func", "post_synth_time", "post_impl_func", "post_impl_time"]; }
const stagesOf = (backend) => BSTAGES[backend] || ["behavioral"];

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
  try { const sc = await api("/schema"); DEV = Object.fromEntries(Object.entries(sc.backends).map(([k, v]) => [k, v])); BFACTS = sc.backend_facts; BSTAGES = Object.fromEntries(Object.entries(sc.backend_facts).map(([k, v]) => [k, v.sim_stages])); } catch {}
  // agent modes (the harness model slot behind the chat): a per-conversation switch next to the composer
  try { const h = await api("/health"); if (h.modes) { MODES = h.modes; defaultMode = h.default_mode || Object.keys(MODES)[0]; renderModes(); } } catch {}
  $("#new-chat").onclick = () => openThread(null);
  $("#send").onclick = () => send();
  $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("#input").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 180) + "px"; });
  $("#t-rail").onclick = () => { const sh = $(".shell"); if (window.innerWidth <= 800) sh.classList.toggle("rail-shown"); else sh.classList.toggle("rail-hidden"); };
  $("#scrim").onclick = () => $(".shell").classList.remove("rail-shown");
  // bring your own RTL: the files go into the message as fenced blocks; the agent builds the bundle (bench,
  // parameters) and registers it with sa_rtl_upload
  $("#attach").title = T.attach; $("#attach").onclick = () => $("#attach-file").click();
  $("#attach-file").onchange = async (e) => {
    const ta = $("#input");
    for (const f of e.target.files) {
      const text = await f.text();
      if (text.length > 400000) { alert(`${f.name}: ${T.tooBig}`); continue; }
      ta.value = (ta.value ? ta.value + "\n\n" : T.attachLead + "\n\n") + "```systemverilog " + f.name + "\n" + text.replace(/\n$/, "") + "\n```";
    }
    e.target.value = ""; ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 180) + "px"; ta.focus();
  };
  $("#stop").onclick = async () => {
    // stops the agent's turn (and the job it is running); without a turn in flight, just the live job
    $("#stop").disabled = true;
    if (thread?.busy && thread.id) await post(`/threads/${thread.id}/stop`);
    else { const j = liveJob(); if (j) await post(`/jobs/${j}/stop`); }
  };
  await refreshThreads();
  setInterval(refreshThreads, 6000);
  const h = location.hash.slice(1);
  if (h) openThread(h);
  window.addEventListener("hashchange", () => { const id = location.hash.slice(1) || null; if (id !== tid) openThread(id); });
  $("#input").focus();
})();

// ---------------------------------------------------------------- rail
async function refreshThreads() {
  threads = await api("/threads");
  const ul = $("#threads"); ul.innerHTML = "";
  // grouped by recency: today / earlier (the list is already newest first)
  const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
  let group = null;
  for (const th of threads) {
    const g = (th.last || th.created) * 1000 >= dayStart.getTime() ? T.today : T.earlier;
    if (g !== group) { group = g; ul.appendChild(el(`<li class="group">${esc(g)}</li>`)); }
    const b = th.best?.metrics;
    const m = th.busy ? (th.agent ? `${T.agentAt} ${th.agent}` : T.thinking)
      : th.status === "done" && b ? `${fmt(b.fmax_mhz, 0)} MHz · ${b.dsp} DSP${th.n_runs > 1 ? ` · ${th.n_runs} ${T.runs}` : ""}`
      : th.status ? ({ running: T.progress, queued: T.queued, failed: T.noResult, error: T.error, interrupted: T.stopped }[th.status] || th.status) : "";
    const dev = th.backend ? `<span class="dev">${esc(th.backend)}</span>` : "";
    const li = el(`<li class="${th.id === tid ? "sel" : ""}" data-id="${esc(th.id)}" title="${esc(th.title)}"><span class="dot ${th.busy ? "busy" : th.status || ""}"></span><div><div class="t">${esc(th.title)}</div><div class="m">${dev}${esc(m)}</div></div><button class="x" title="${T.del}">×</button></li>`);
    li.onclick = () => openThread(th.id);
    li.querySelector(".t").ondblclick = async (e) => { e.stopPropagation(); const nt = prompt(T.rename, th.title); if (nt != null && nt.trim()) { await post(`/threads/${th.id}/title`, { title: nt.trim() }); refreshThreads(); if (th.id === tid) $("#title").textContent = nt.trim(); } };
    li.querySelector(".x").onclick = async (e) => { e.stopPropagation(); if (!confirm(T.delConfirm)) return; try { await api(`/threads/${th.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; } if (th.id === tid) openThread(null); refreshThreads(); };
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- thread
function openThread(id) {
  clearTimeout(timer);
  tid = id; thread = null; open.clear(); rtlShown.clear();
  if (!id) setLang((navigator.language || "").toLowerCase().startsWith("zh"));
  location.hash = id || "";
  $("#msgs").innerHTML = ""; $("#hello").hidden = !!id; $("#title").textContent = ""; $("#stop").hidden = true; $("#wt-link").hidden = !id;
  pendingMode = null; if (MODES) renderModes();
  document.querySelectorAll("#threads li").forEach((li) => li.classList.toggle("sel", li.dataset.id === id));
  // on a phone the rail is a drawer: picking a conversation closes it
  $(".shell").classList.remove("rail-shown");
  $(".shell").classList.remove("rail-shown");
  if (id) poll();
  $("#input").focus();
}

let MODES = null, defaultMode = "fast", pendingMode = null;
function renderModes() {
  const box = $("#modes");
  if (!MODES) { box.hidden = true; return; }
  const cur = thread?.mode || pendingMode || defaultMode;
  box.hidden = false; box.innerHTML = "";
  box.appendChild(el(`<span class="muted">${esc(T.modeTitle)}</span>`));
  for (const [k, desc] of Object.entries(MODES)) {
    const b = el(`<button class="${k === cur ? "on" : ""}" title="${esc(desc)}">${esc(k === "fast" ? T.modeFast : k === "deep" ? T.modeDeep : k === "author" ? T.modeAuthor : k)}</button>`);
    b.onclick = async () => {
      if (thread?.id && tid) { try { await post(`/threads/${tid}/mode`, { mode: k }); thread.mode = k; } catch (e) { alert(e.message); return; } }
      else pendingMode = k;   // applied to the thread the first message opens
      renderModes();
    };
    box.appendChild(b);
  }
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
  for (const jid of Object.keys(thread.jobs || {})) {
    const j = jobs[jid] || (jobs[jid] = { events: [], seen: 0 });
    if (j.done) continue;
    const d = await api(`/jobs/${jid}?after=${j.seen}`);
    j.events.push(...d.events); j.seen = d.n_events;
    Object.assign(j, { status: d.status, summary: d.summary, error: d.error, spec: d.spec, max_iter: d.max_iter, parent: d.parent });
    if (!["queued", "running"].includes(d.status)) j.done = true;
  }
  render();
  const live = liveJob();
  const busy = thread.busy || !!live || thread.messages.some((m) => m.kind === "run" && !m.narrated && jobs[m.job]?.done) || Object.values(thread.proposals || {}).some((p) => p.status === "gating");
  timer = setTimeout(poll, busy ? 2000 : 8000);
}

// ---------------------------------------------------------------- send
async function send(text) {
  const ta = $("#input"), msg = (text ?? ta.value).trim();
  if (!msg) return;
  ta.value = ""; ta.style.height = "auto";
  $("#hello").hidden = true;
  // optimistic echo; while the agent is mid-turn the message queues behind it instead
  if (!thread) thread = { id: tid, title: "", messages: [], jobs: {}, busy: true };
  if (thread.busy) (thread.queue = thread.queue || []).push({ text: msg, t: Date.now() / 1000 });
  else { thread.messages.push({ role: "user", kind: "text", text: msg, t: Date.now() / 1000 }); thread.busy = true; }
  render();
  $("#scroll").scrollTop = $("#scroll").scrollHeight;
  try {
    const r = await post(`/threads/${tid || "new"}/chat`, { text: msg });
    if (!tid) { tid = r.thread; location.hash = tid; if (pendingMode) { try { await post(`/threads/${tid}/mode`, { mode: pendingMode }); } catch {} pendingMode = null; } }
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
  $("#wt-link").href = `/work?thread=${encodeURIComponent(tid)}`;
  if (MODES && $("#modes").dataset.mode !== (thread.mode || "")) { $("#modes").dataset.mode = thread.mode || ""; renderModes(); }
  // the conversation's running cost, from the last turn's session totals
  const su = [...thread.messages].reverse().find((m) => m.usage?.session)?.usage.session;
  const sb = $("#sess-usage");
  if (su) { sb.hidden = false; sb.textContent = T.sessUsage.replace("{turns}", su.turns).replace("{tok}", kTok((su.input || 0) + (su.cache_read || 0) + (su.output || 0))).replace("{usd}", usd(su.usd_micros)); sb.title = T.sessUsageTitle.replace("{ctx}", kTok(su.context_tokens || 0)); }
  else sb.hidden = true;
  const live = liveJob();
  $("#stop").hidden = !live && !thread.busy; $("#stop").disabled = false;
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
  if (thread.busy && !th) { th = el(`<li class="msg assistant thinking-row"><span class="thinking"><i></i>${T.thinking}</span><div class="live-note prose dim" hidden></div><div class="agent-tools live"></div></li>`); ol.appendChild(th); }
  if (!thread.busy && th) th.remove();
  if (thread.busy && th) {
    // the agent's live tool trail (Agnes Harness tool records as they happen) and its working narration
    const live = thread.live || [], box = th.querySelector(".agent-tools");
    const sig = JSON.stringify(live.map((t) => [t.name, t.status, t.args]));
    if (box.dataset.sig !== sig) { box.dataset.sig = sig; box.innerHTML = live.map((t) => `<span class="tchip ${t.status === "failed" ? "x" : t.status === "completed" ? "" : "run"}" title="${esc(t.args || "")}">${esc(t.name || "?")}${t.args ? `<i>${esc(t.args)}</i>` : ""}</span>`).join(""); }
    const note = (thread.live_note || "").trim(), nb = th.querySelector(".live-note");
    if (nb.textContent !== note) nb.textContent = note;
    nb.hidden = !note;
  }
  // messages waiting behind the current turn
  for (const q of ol.querySelectorAll("li.queued")) q.remove();
  if (thread.busy) for (const q of thread.queue || []) ol.appendChild(el(`<li class="msg user queued" title="${esc(T.queued)}"><div class="bubble">${esc(q.text)}</div><span class="qtag">${esc(T.queued)}</span></li>`));
  if (atBottom) sc.scrollTop = sc.scrollHeight;
}

// a small, safe markdown subset for assistant prose: escapes first, then bold / code / lists / paragraphs
function md(text) {
  const lines = esc(text).split("\n");
  let out = "", list = null, table = null;
  const inline = (t) => t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\*(?!\s)([^*]+?)\*/g, "<i>$1</i>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const flush = () => { if (list) { out += `</${list}>`; list = null; } if (table) { out += "</tbody></table>"; table = null; } };
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  for (const raw of lines) {
    const l = raw.trimEnd();
    let m;
    if (/^\s*\|.*\|\s*$/.test(l)) {
      if (/^\s*\|?\s*:?-{2,}/.test(l)) continue;   // the header separator row
      if (!table) { if (list) { out += `</${list}>`; list = null; } out += `<table><thead><tr>${cells(l).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>`; table = "t"; }
      else out += `<tr>${cells(l).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`;
      continue;
    }
    if ((m = l.match(/^\s*#{1,6}\s+(.*)$/))) { flush(); out += `<h4>${inline(m[1])}</h4>`; }
    else if ((m = l.match(/^\s*[-*]\s+(.*)$/))) { if (table) flush(); if (list !== "ul") { flush(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)$/))) { if (table) flush(); if (list !== "ol") { flush(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else if (!l.trim()) { flush(); }
    else { flush(); out += `<p>${inline(l)}</p>`; }
  }
  flush();
  return out;
}

function msgSignature(m) {
  const j = m.job && jobs[m.job];
  const pr = m.proposal && thread.proposals?.[m.proposal];
  return JSON.stringify([m.role, m.kind, m.text, m.job, j?.seen, j?.status, j?.error && 1, [...open].filter((k) => k.startsWith((m.job || m.proposal) + ":")), rtlShown.has(m.job), pr?.status, pr?.steps?.length, pr?.ab?.job, pr?.merged && 1]);
}

// a generator change the agent proposed: the gate's verdict per step, then the A/B run (its own run card follows)
function proposalCard(m) {
  const pr = thread.proposals?.[m.proposal];
  if (!pr) return el(`<div class="prose dim">${esc(T.proposal)} · ${esc(m.proposal)}</div>`);
  const st = pr.status;
  const sub = st === "gating" ? `<span class="live">${T.gating}</span>` : st === "passed" ? `<span class="ok">${T.gatePassed}</span>` : `<span class="bad">${T.gateFailed}${pr.scope_errors ? " · scope" : pr.apply_error ? " · patch" : ""}</span>`;
  const d = card(`${m.proposal}:prop`, T.proposal, `${sub} · ${esc((pr.files || []).map((f) => f.replace(/^silicon_agent\//, "")).join(", "))}${pr.diff_lines ? ` · ${pr.diff_lines} ${T.lines}` : ""}${pr.merged ? ` · <span class="ok">${T.merged}</span>` : ""}`);
  const inner = d.querySelector(".inner");
  inner.appendChild(el(`<div class="prose"><b>${esc(pr.title || "")}</b>${pr.rationale ? `<p>${esc(pr.rationale)}</p>` : ""}</div>`));
  if (pr.scope_errors?.length) inner.appendChild(el(`<div class="x">${esc(pr.scope_errors.join("; "))}</div>`));
  if (pr.apply_error) inner.appendChild(el(`<pre class="log">${esc(pr.apply_error)}</pre>`));
  const steps = el(`<div class="gate-steps"></div>`);
  (pr.steps || []).forEach((s) => {
    const row = el(`<div class="gstep"><span class="${s.ok ? "v" : s.ok === false ? "x" : "muted"}">${s.ok ? "✓" : s.ok === false ? "✕" : "·"}</span> ${esc(s.name)}${s.seconds != null ? ` <span class="muted">${s.seconds} s</span>` : ""}</div>`);
    if (s.mutation) row.appendChild(el(`<div class="muted small">${s.mutation.map((x) => `${esc(x.design)}: ${x.killed}/${x.total} killed (${x.score == null ? "—" : Math.round(x.score * 100) + "%"})${x.survived?.length ? ` · survived ${esc(x.survived.join(", "))}` : ""}`).join(" · ")}</div>`));
    if (s.equivalence) row.appendChild(el(`<div class="muted small">${s.equivalence.map((x) => `${esc(x.case)}: ${x.identical ? "identical" : x.ok ? "equivalent @" + x.depth : "<span class='x'>NOT equivalent</span>"}`).join(" · ")}</div>`));
    if (s.failures?.length) row.appendChild(el(`<pre class="log">${esc(s.failures.join("\n").slice(0, 1500))}</pre>`));
    if (s.sims?.length) row.appendChild(el(`<pre class="log">${esc(s.sims.map((x) => `${x.case}: ${x.log || "failed"}`).join("\n").slice(0, 1500))}</pre>`));
    if (s.error) row.appendChild(el(`<pre class="log">${esc(s.error)}</pre>`));
    steps.appendChild(row);
  });
  if (st === "gating" && !(pr.steps || []).length) steps.appendChild(el(`<div class="muted small">${T.gateSteps.map((x) => "· " + esc(x)).join("<br>")}</div>`));
  inner.appendChild(steps);
  if (pr.ab?.job) inner.appendChild(el(`<div class="small">${T.abRun}: <a href="/work?job=${encodeURIComponent(pr.ab.job)}" target="_blank">${esc(pr.ab.job)}</a></div>`));
  if (pr.ab?.error) inner.appendChild(el(`<div class="x small">${esc(pr.ab.error)}</div>`));
  const links = el(`<div class="links"></div>`);
  const vd = el(`<button class="ghost small">${T.viewDiff}</button>`);
  vd.onclick = async () => { const r = await api(`/proposals/${m.proposal}`); let pre = inner.querySelector("pre.diff"); if (pre) { pre.remove(); return; } pre = el(`<pre class="log diff"></pre>`); pre.textContent = r.diff || ""; inner.appendChild(pre); };
  links.appendChild(vd);
  inner.appendChild(links);
  return d;
}

// the harness's ledger for a turn: tokens, estimated USD, wall time
const kTok = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n || 0);
const usd = (micros) => micros == null ? "—" : micros < 10000 ? `$${(micros / 1e6).toFixed(4)}` : `$${(micros / 1e6).toFixed(2)}`;
const secs = (ms) => ms == null ? "—" : ms < 60000 ? `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s` : `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s`;
function usageLine(u) {
  const tok = (u.input || 0) + (u.cache_read || 0) + (u.output || 0);
  const line = T.usage.replace("{calls}", u.calls ?? "?").replace("{tok}", kTok(tok)).replace("{usd}", usd(u.usd_micros)).replace("{s}", secs(u.ms));
  return el(`<div class="usage" title="${esc(T.usageTitle)}${u.model ? ` · ${esc(u.model)}` : ""}">${esc(line)}${u.model ? ` <span class="model">${esc(u.model)}</span>` : ""}</div>`);
}

function buildMsg(m) {
  if (m.role === "user") {
    // attached RTL (fenced blocks) folds into a code block with the file name
    const parts = m.text.split(/```(?:systemverilog|verilog|sv)?\s*([^\n`]*)\n([\s\S]*?)```/g);
    if (parts.length > 1) {
      const li = el(`<li class="msg user"><div class="bubble"></div></li>`), b = li.querySelector(".bubble");
      for (let i = 0; i < parts.length; i += 3) {
        if (parts[i].trim()) b.appendChild(el(`<div>${esc(parts[i].trim())}</div>`));
        if (i + 2 < parts.length) b.appendChild(el(`<details class="attached"><summary>${esc(parts[i + 1] || "rtl.sv")} · ${parts[i + 2].split("\n").length} ${T.lines}</summary><pre></pre></details>`)).querySelector("pre").textContent = parts[i + 2];
      }
      return li;
    }
    return el(`<li class="msg user"><div class="bubble">${esc(m.text)}</div></li>`);
  }
  const li = el(`<li class="msg assistant"></li>`);
  if (m.text) li.appendChild(el(`<div class="prose ${m.kind === "run" ? "dim" : ""}">${m.source === "worktable" ? `<span class="tag">worktable</span> ` : ""}${m.resumed ? `<span class="tag" title="${T.resumedTitle}">${T.resumed}</span> ` : ""}${md(m.text)}</div>`));
  // the agent's own trajectory (Agnes Harness records): its working notes and tool calls in order, folded by default
  if (m.steps?.length && m.steps.some((s) => s.kind === "tool")) {
    const n = m.steps.filter((s) => s.kind === "tool").length, nx = m.steps.filter((s) => s.kind === "tool" && s.status === "failed").length;
    const d = el(`<details class="agent-steps"><summary><span class="ic">▶</span>${T.agentSteps.replace("{n}", n)}${nx ? ` <span class="x">· ${T.failedCalls.replace("{n}", nx)}</span>` : ""}</summary><div class="inner"></div></details>`);
    const inner = d.querySelector(".inner");
    let run = [];
    const flushTools = () => { if (run.length) { inner.appendChild(el(`<div class="agent-tools">${run.map((t) => `<span class="tchip ${t.status === "failed" ? "x" : ""}" title="${esc(t.brief || "")}">${esc(t.name || "?")}${t.brief ? `<i>${esc(t.brief)}</i>` : ""}</span>`).join("")}</div>`)); run = []; } };
    const last = m.steps.length - 1;
    m.steps.forEach((st, i) => {
      if (st.kind === "tool") run.push(st);
      else if (st.text.trim() && !(i === last)) { flushTools(); inner.appendChild(el(`<div class="note">${esc(st.text.trim())}</div>`)); }
    });
    flushTools();
    if (m.usage) inner.appendChild(usageLine(m.usage));
    li.appendChild(d);
  } else if (m.tools?.length) {
    li.appendChild(el(`<div class="agent-tools">${m.tools.map((t) => `<span class="tchip ${t.status === "failed" ? "x" : ""}" title="${esc(JSON.stringify(t.args ?? {}).slice(0, 300))}">${esc(t.name || "?")}</span>`).join("")}</div>`));
  } else if (m.usage && m.source === "agh") {
    li.appendChild(usageLine(m.usage));
  }
  if (m.kind === "proposal" && m.proposal) {
    li.appendChild(proposalCard(m));
    return li;
  }
  if (m.kind === "run" && m.job) {
    const j = jobs[m.job], spec = j?.spec || thread.jobs[m.job]?.spec;
    if (spec) li.appendChild(specCard(m.job, spec, m.fork));
    if (m.alternatives?.length) {
      const alt = el(`<div class="alts"><span class="muted">${T.alts}</span></div>`);
      for (const a of m.alternatives) { const b = el(`<button title="${esc(a.why || "")}">${esc(a.request)}</button>`); b.onclick = () => send(a.request); alt.appendChild(b); }
      li.appendChild(alt);
    }
    if (j) {
      const rows = turns(j.events), done = rows.filter((r) => !r.pending);
      const liveRow = rows.find((r) => r.pending && !r.slot) || rows.find((r) => r.pending);
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
const chips = (ir, prev, locked) => {
  // composite designs: one row of chips per stage, prefixed s0./s1.
  if (Array.isArray(ir.nodes)) {
    const lockedN = Array.isArray(locked?.nodes) ? locked.nodes : [];
    return ir.nodes.map((nd, i) => `<div class="chips"><span class="chip"><span class="k">n${i}</span> ${esc(nd.kind)}</span>` + Object.entries(nd.ir || {}).filter(([k]) => k !== "coefs").map(([k, v]) => {
      const lock = lockedN[i]?.ir && k in lockedN[i].ir, changed = !lock && prev?.nodes?.[i] && JSON.stringify(prev.nodes[i].ir?.[k]) !== JSON.stringify(v);
      return `<span class="chip ${lock ? "lock" : changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
    }).join("") + `</div>`).join("") + `<div class="chips">${Object.entries(ir).filter(([k]) => k !== "nodes").map(([k, v]) => `<span class="chip ${prev && JSON.stringify(prev[k]) !== JSON.stringify(v) ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`).join("")}</div>`;
  }
  if (Array.isArray(ir.stages)) {
    const lockedStages = Array.isArray(locked?.stages) ? locked.stages : [];
    const rows = ir.stages.map((st, i) => `<div class="chips"><span class="chip"><span class="k">s${i}</span></span>` + Object.entries(st).filter(([k]) => k !== "coefs").map(([k, v]) => {
      const lock = lockedStages[i] && k in lockedStages[i], changed = !lock && prev?.stages?.[i] && JSON.stringify(prev.stages[i][k]) !== JSON.stringify(v);
      return `<span class="chip ${lock ? "lock" : changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
    }).join("") + `</div>`);
    const rest = Object.entries(ir).filter(([k]) => k !== "stages").map(([k, v]) => `<span class="chip ${prev && JSON.stringify(prev[k]) !== JSON.stringify(v) ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`).join("");
    return rows.join("") + (rest ? `<div class="chips">${rest}</div>` : "");
  }
  return `<div class="chips">` + Object.entries(ir).filter(([k]) => k !== "coefs").map(([k, v]) => {
    const lock = locked && k in locked, changed = !lock && prev && JSON.stringify(prev[k]) !== JSON.stringify(v);
    return `<span class="chip ${lock ? "lock" : changed ? "changed" : ""}"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(v))}</span>`;
  }).join("") + `</div>`;
};
const consText = (c) => [`≥${c.fmax_mhz} MHz`, c.max_dsp != null ? `≤${c.max_dsp} DSP` : "", c.max_lut != null ? `≤${c.max_lut} LUT` : "", c.max_area_um2 != null ? `≤${fmt(c.max_area_um2 / 1000, 0)}k µm²` : ""].filter(Boolean).join(" · ");

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
  // one row per (iteration, slot): slot 0 is the chosen candidate, slots 1.. are siblings measured in parallel
  const rows = new Map(); let pendingSteer = [];
  const key = (e) => `${e.iteration}.${e.slot || 0}`;
  const get = (e) => { const k = key(e); if (!rows.has(k)) rows.set(k, { n: e.iteration, slot: e.slot || 0, pending: true, user: [], stages: {} }); return rows.get(k); };
  for (const e of events) {
    switch (e.kind) {
      case "user": pendingSteer.push(e); break;
      case "steer": get({ iteration: e.iteration }).user.push(...pendingSteer.splice(0)); break;
      case "plan": { const prev = rows.get(`${e.iteration - 1}.0`); if (prev) prev.next = e; if (!e.stop) get({ iteration: e.iteration }).plan = e; break; }
      case "evaluating": get(e).ir = e.ir; break;
      case "stage": get(e).stages[e.stage] = e; break;
      case "result": Object.assign(get(e), e, { pending: false }); break;
      case "note": if (e.iteration) get({ iteration: e.iteration }).note = e.message; break;
    }
  }
  const out = [...rows.values()].sort((a, b) => a.n - b.n || a.slot - b.slot);
  if (pendingSteer.length && out.length) out.at(-1).userAfter = pendingSteer;
  return out;
}

const seedSpan = (m) => (m.fmax_seeds && m.fmax_seeds.length > 1) ? ` <span class="muted" title="${m.fmax_seeds.length} P&R seeds">${fmt(Math.min(...m.fmax_seeds), 0)}–${fmt(Math.max(...m.fmax_seeds), 0)}</span>` : "";

// critical path as a proportional bar: one segment per stage (logic solid, routing hatched), the target period as a tick
function cpBar(cp, spec) {
  const total = cp[0].total_ns, target = spec?.constraints?.fmax_mhz ? 1000 / spec.constraints.fmax_mhz : 0;
  const span = Math.max(total, target) || 1;
  const segs = cp.slice(1).map((s) => {
    const l = Math.max(0, s.logic_ns || 0), rt = Math.max(0, s.route_ns || 0), name = (s.rtl || "").split(".").slice(-2).join(".");
    return `<span class="seg" style="width:${(100 * (l + rt) / span).toFixed(2)}%" title="${esc(s.kind)} ${esc(name)}: ${fmt(l, 2)} ns logic + ${fmt(rt, 2)} ns route">` +
      `<i class="lg ${esc((s.kind || "").toLowerCase().replace(/[^a-z0-9]+/g, "-"))}" style="width:${l + rt ? (100 * l / (l + rt)).toFixed(1) : 0}%"></i></span>`;
  }).join("");
  return `<div class="cp"><span>${T.critical} ${fmt(total, 2)} ns${target ? ` / ${fmt(target, 2)} ns` : ""}:</span>` +
    `<div class="cpbar">${segs}${target ? `<b class="tick" style="left:${(100 * target / span).toFixed(2)}%"></b>` : ""}</div>` +
    cp.slice(1).map((s) => `<span>${esc(s.kind)} <i>${esc((s.rtl || "").split(".").slice(-2).join("."))}</i> ${fmt((s.logic_ns || 0) + (s.route_ns || 0), 2)}</span>`).join("") + `</div>`;
}

function stageChips(r, backend) {
  const st = r.pending ? r.stages : (r.metrics?.sim_stages || {});
  const keys = stagesOf(backend);
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

function eta(j, rows, done) {
  // seconds per round from this job's own results (event timestamps), else the device's band
  const ts = j.events.filter((e) => e.kind === "result").map((e) => e.t);
  let per = ts.length >= 2 ? (ts[ts.length - 1] - ts[0]) / (ts.length - 1) : ts.length === 1 ? ts[0] : null;
  if (per == null) { const band = (BFACTS[j.spec?.backend] || {}).seconds_per_iter || ""; const m = band.match(/([\d.]+)-([\d.]+)\s*(s|min)/); if (m) per = ((+m[1] + +m[2]) / 2) * (m[3] === "min" ? 60 : 1) + 20; }
  if (per == null) return "";
  const left = Math.max(0, (j.max_iter - new Set(done.map((r) => r.n)).size)) * per;
  return left >= 90 ? `~${Math.round(left / 60)} min` : `~${Math.round(left)} s`;
}
let BFACTS = {};

function trajCard(jid, j, rows, done, liveRow, spec) {
  const live = ["queued", "running"].includes(j.status), unit = j.summary?.throughput_unit || (spec.design === "fir" ? "samples/cycle" : "MAC/cycle");
  const feas = done.filter((r) => r.violations?.length === 0).length;
  const remaining = live ? eta(j, rows, done) : "";
  const nRounds = new Set(done.map((r) => r.n)).size, nPar = rows.filter((r) => r.pending).length;
  const sub = live ? `<span class="live">${t("iter", liveRow?.n ?? nRounds + 1)} / ${j.max_iter}${nPar > 1 ? ` ×${nPar}` : ""} · ${phase(liveRow, j)}</span>${remaining ? ` · ${remaining} ${T.left}` : ""}` : `${t("iters", nRounds)}${done.length > nRounds ? ` (${done.length} ${T.designs})` : ""}${feas ? ` · ${t("feasible", feas)}` : ""}`;
  const d = card(`${jid}:traj`, live ? T.progress : T.traj, sub);
  const inner = d.querySelector(".inner"), list = el(`<div class="iters"></div>`);
  let prev = null;
  for (const r of rows) {
    const m = r.metrics, ok = m && r.violations.length === 0;
    const what = r.slot ? (r.action ?? "") : r.n === 1 ? (j.parent ? T.fork : T.startFrom) : (r.plan?.action ?? r.action ?? "");
    const row = el(`<div class="iter ${r.slot ? "sib" : ""}"><span class="n ${r.pending ? "live" : ""}">${r.slot ? `${r.n}.${r.slot}` : r.n}</span><div>
      ${r.user.map((u) => `<div class="user">${esc(u.message)}</div>`).join("")}
      <div class="what">${esc(what)}</div>
      ${r.ir ? chips(r.ir, prev, spec.locked) : ""}
      ${r.pending ? `<div class="nums"><span class="live" style="color:var(--accent)">${esc(phase(r, j))}</span></div>` : `<div class="nums"><span class="${ok ? "v" : "x"}">${ok ? "✓" : "✕"}</span> <b>${fmt(m.fmax_mhz)} MHz</b>${seedSpan(m)} · ${m.area_um2 != null ? `${m.lut} cells · ${fmt(m.area_um2 / 1000, 1)}k µm²` : `${m.lut} LUT · ${m.dsp} DSP`} · ${r.throughput} ${esc(unit)} · ${r.eda_seconds}${T.secs}${ok ? "" : ` · <span class="x">${esc(r.violations.join(", "))}</span>`}</div>`}
      ${r.ir ? stageChips(r, spec.backend) : ""}
      ${r.n > 1 && !r.slot && (r.plan?.diagnosis ?? r.diagnosis) ? `<div class="why">${esc(r.plan?.diagnosis ?? r.diagnosis)}</div>` : ""}
      ${r.note ? `<div class="why">${esc(r.note)}</div>` : ""}
      ${!r.pending && m.critical_path?.length > 1 ? cpBar(m.critical_path, spec) : ""}
      ${r.next?.stop ? `<div class="why">■ ${esc(r.next.stop_reason || r.next.action)}</div>` : ""}
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
  const best = j.summary?.best, unit = j.summary?.throughput_unit || "", top = TOP[spec.design] || "top", keys = stagesOf(spec.backend);
  const target = done.filter((r) => r.violations?.length).sort((a, b) => a.violations.length - b.violations.length || b.n - a.n)[0];
  const stg = best?.metrics?.sim_stages || {};
  const signoff = keys.includes("post_impl_time") && stg.post_impl_time?.passed === true;
  let sub;
  if (best) sub = `<span class="ok">${top}.sv</span> · ${fmt(best.metrics.fmax_mhz)} MHz · ${best.metrics.area_um2 != null ? `${fmt(best.metrics.area_um2 / 1000, 1)}k µm² · ${best.metrics.lut} cells` : `${best.metrics.dsp} DSP · ${best.metrics.lut} LUT`} · ${best.throughput} ${esc(unit)}${signoff ? ` · ${T.signoff}` : ""}`;
  else if (j.status === "interrupted") sub = `<span class="bad">${T.stopped}</span>`;
  else if (j.status === "error") sub = `<span class="bad">${T.error}</span>`;
  else sub = `<span class="bad">${T.noResult}</span>${target ? ` · ${T.closest}: ${fmt(target.metrics.fmax_mhz, 0)} MHz · ${target.metrics.dsp} DSP · ${esc(target.violations.join(", "))}` : ""}`;
  const d = card(`${jid}:result`, T.result, sub, "result");
  const inner = d.querySelector(".inner");
  const row = best || target;
  if (row) {
    const m = row.metrics;
    inner.appendChild(el(`<div class="head"><b>${fmt(m.fmax_mhz)} MHz</b>${m.fmax_seeds && m.fmax_seeds.length > 1 ? `<span title="P&R seeds">${m.fmax_seeds.map((x) => fmt(x, 0)).join(" / ")} MHz · median</span>` : ""}${m.area_um2 != null ? `<span>${m.lut} cells</span><span>${fmt(m.area_um2 / 1000, 1)}k µm²</span>` : `<span>${m.lut} LUT</span><span>${m.dsp} DSP</span>`}<span>${m.ff ?? "–"} FF</span>${m.bram ? `<span>${m.bram} BRAM</span>` : ""}${m.power_mw != null ? `<span>${m.power_mw} mW</span>` : ""}<span>${row.throughput} ${esc(unit)}</span>${row.latency_cycles != null ? `<span>latency ${row.latency_cycles} cyc</span>` : ""}</div>`));
    inner.appendChild(el(chips({ ...(spec.locked || {}), ...row.ir }, null, spec.locked)));
    // simulation ladder
    const tbl = el(`<table class="simtable"></table>`);
    const purposes = zh ? ["RTL 逻辑 vs Python 黄金模型（cocotb + iverilog）", "综合后网表，unisim 模型，无延迟", "综合后网表 + 估算 SDF 延迟，目标周期", "布线后网表，无延迟", "布线后网表 + 真实 SDF 延迟，目标周期 — 签核"]
                        : ["RTL vs Python golden model (cocotb + iverilog)", "synthesized netlist, unisim models, no delays", "synthesized netlist with estimated SDF delays at target period", "placed-and-routed netlist, no delays", "placed-and-routed netlist with real SDF delays at target period — the signoff"];
    STAGE_KEYS.forEach((k, i) => {
      if (!keys.includes(k)) return;
      let st, log = "";
      if (k === "behavioral") st = m.sim_passed ? "pass" : "fail";
      else { const s = (m.sim_stages || {})[k]; st = !s ? "–" : s.passed === true ? "pass" : s.passed === false ? "fail" : "skipped"; if (s?.passed === false) log = s.log || ""; }
      tbl.insertAdjacentHTML("beforeend", `<tr><td class="st ${st === "pass" ? "ok" : st === "fail" ? "bad" : "na"}">${st === "pass" ? T.pass : st === "fail" ? T.fail : st}</td><td>${i + 1} · ${T.stages[i]}<div class="p">${purposes[i]}</div>${log ? `<div class="log">${esc(log.slice(-600))}</div>` : ""}</td></tr>`);
    });
    inner.appendChild(tbl);
    const covs = Object.values(m.sim_coverage || {})[0];
    if (covs) inner.appendChild(el(`<div class="chips">${Object.entries(covs).map(([k, v]) => `<span class="chip ${v ? "" : "lock"}" title="${T.covTitle}"><span class="k">${esc(k)}</span> ${v}</span>`).join("")}</div>`));
  }
  const links = el(`<div class="links"></div>`);
  if (best) {
    const view = el(`<button>${rtlShown.has(jid) ? T.hideRtl : T.viewRtl}</button>`);
    const dl = el(`<button>${t("download", top + ".sv")}</button>`);
    view.onclick = async () => { if (rtlShown.has(jid)) rtlShown.delete(jid); else rtlShown.add(jid); render(); };
    dl.onclick = async () => { const txt = await api(`/jobs/${jid}/rtl`); const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" })); a.download = `${top}.sv`; a.click(); };
    const pkg = el(`<a href="/api/jobs/${esc(jid)}/package">${T.pkg}</a>`);
    const fm = el(`<button title="${T.formalTitle}">${T.formal}</button>`);
    fm.onclick = async () => {
      fm.disabled = true; fm.textContent = T.formalRunning;
      try {
        // the server runs the check in the background and answers 202 until it is done
        let r;
        for (let i = 0; i < 120; i++) { r = await api(`/jobs/${jid}/formal?depth=20`); if (r.status !== "running") break; await new Promise((ok) => setTimeout(ok, 5000)); }
        const eq = r.equivalence?.ok, pr = r.properties?.ok;
        fm.textContent = r.status === "running" ? T.formalRunning : `${T.formal}: ${eq === true ? "≡ ✓" : eq === false ? "≡ ✗" : "≡ ?"} · ${pr ? "props ✓" : "props ✗"}`;
        fm.title = (r.equivalence?.log || "") + "\n" + (r.properties?.log || "") + (r.error ? "\n" + r.error : "");
      } catch (e) { fm.textContent = e.message.slice(0, 60); }
      fm.disabled = false;
    };
    links.append(view, dl, pkg, fm);
  }
  links.appendChild(el(`<a href="/work?thread=${encodeURIComponent(tid)}#${esc(jid)}" target="_blank">${T.worktable}</a>`));
  if (j.parent?.id) links.appendChild(el(`<a href="/work?thread=${encodeURIComponent(tid)}#compare/${esc(j.parent.id)}/${esc(jid)}" target="_blank">${T.compare}</a>`));
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
