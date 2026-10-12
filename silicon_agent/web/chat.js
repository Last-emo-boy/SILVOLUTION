// silicon-agent chat. Plain ES module, no build step.
// One conversation per design task: the user writes what they want, the agent answers in prose and
// attaches collapsed cards (understood spec, progress/trajectory, result). Details live inside the cards.

const $ = (s) => document.querySelector(s);
const api = async (path, opts) => {
  const r = await fetch(`/api${path}`, { cache: "no-store", ...opts });
  // the session cookie is gone (token rotated, cookie expired): back to the login page
  if (r.status === 401 && !path.startsWith("/login")) { location.replace("/login"); throw new Error("401 unauthorized"); }
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 300)}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
};
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const fmt = (n, d = 1) => (n == null ? "–" : Number(n).toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
// UI language: the conversation's (first user message) if known, else the browser's
// the product UI is Chinese; ?lang=en switches the chrome to English for a visitor who needs it (the agent's replies
// follow the language of the message either way)
const UI_LANG = new URLSearchParams(location.search).get("lang") || localStorage.getItem("sa.lang") || "zh";
if (new URLSearchParams(location.search).get("lang")) localStorage.setItem("sa.lang", UI_LANG);
let zh = UI_LANG !== "en";
const hasHan = (s) => /[\u4e00-\u9fff]/.test(s || "");
const L = {};
L.zh = {
  hello: "你想要什么硬件？", blurb: "用一句话描述。我会生成 Verilog，跑仿真、综合和布局布线，直到满足要求，然后把结果交给你。",
  placeholder: "描述你要的硬件，或者对当前结果提要求…", hint: "⏎ 发送 · ⇧⏎ 换行", stop: "■ 停止", thinking: "思考中", newChat: "新对话",
  spec: "理解的规格", progress: "运行中", traj: "轨迹", result: "结果", noResult: "未找到可行设计", iter: "第 {n} 轮", iters: "{n} 轮", feasible: "{n} 轮可行",
  planning: "规划下一轮", sim: "行为级仿真", synth: "综合", pnr: "布局布线", queued: "排队中", stopped: "已停止", error: "出错",
  pass: "通过", fail: "失败", viewRtl: "查看 Verilog", hideRtl: "收起 Verilog", download: "下载 {f}", pkg: "下载交付包 (zip)", worktable: "打开工作台", compare: "与上一次比较", left: "剩余（最多）", del: "删除对话", alts: "也可以试试：", designs: "个设计", covTitle: "行为级仿真覆盖的场景（次数）", formal: "形式验证", formalTitle: "SymbiYosys：实测 RTL 与当前生成器输出的有界等价 + 流协议属性", formalRunning: "验证中…", queued: "排队中，等这一轮结束后发送", exportTitle: "导出这个对话为 Markdown（规格、每轮结果、最佳设计、提案）", mutation: "变异分数", mutationTitle: "往 RTL 注入 24 处错误（加减互换、比较翻转、使能卡死…），看行为级测试台能抓住多少：验证强度", mutationRunning: "注入中…", survived: "漏网的变异", sweep: "设计空间扫描", points: "个点", paretoPts: "个 Pareto 最优", point: "旋钮", paretoNote: "在 {a} 与 {b} 上没有其他可行点同时更优", attach: "附加 RTL 文件（.sv / .v）：用你自己的设计跑同一条阶梯", tooBig: "文件超过 400 KB", attachLead: "这是我自己的 RTL，请用同一条阶梯（仿真、综合、布局布线、形式验证）评估并优化它暴露的参数。", failedCalls: "{n} 次失败", modeFast: "快", modeDeep: "深思", modeAuthor: "改生成器", proposal: "生成器改动提案", gating: "门禁中…", gatePassed: "门禁通过", gateFailed: "门禁未通过", gateSteps: ["发射 + 展开所有预设", "行为级仿真", "形式等价（未改动的 IR）", "测试台变异分数"], abRun: "A/B 实测", merged: "已合并", viewDiff: "查看 diff", lines: "行", modeTitle: "智能体模式（下一轮生效）：", pendingMode: "新对话将以此模式开始", agentSteps: "智能体过程 · {n} 次工具调用", usage: "{calls} 次模型调用 · {tok} tokens · {usd} · {s}", usageTitle: "Agnes Harness 账本：这一轮的模型调用、输入/输出 token（含缓存命中）、估算费用与用时", sessUsage: "本会话 {turns} 轮 · {tok} tokens · {usd}", sessUsageTitle: "本会话累计（Agnes Harness 账本）· 当前上下文 {ctx} tokens", today: "今天", earlier: "更早", agentAt: "智能体 ·", runs: "次运行", resumed: "重启后续接", resumedTitle: "服务器重启打断了上一轮，智能体根据自己的记录继续", delConfirm: "删除这个对话？运行结果会保留在工作台的未归属列表里。", rename: "重命名对话：", refine: "在此基础上继续", llmDown: "模型账户不可用（DeepSeek 返回 {n}）：对话与规划器暂停，已启动的运行照常出结果。充值后再发一条消息即恢复。", llmDownTitle: "服务器上次调用模型时收到的状态；下一次成功的调用会清除这条提示", search: "搜索对话：标题、你的提问、设计、器件", noMatch: "没有匹配的对话", projects: "专案", convs: "对话", newProject: "新建专案", newProjectPrompt: "专案名称（例如：板卡 A 的 DSP 前端）：", projectIns: "专案说明 · 这个专案里每个对话都从这些约定开始（器件、时钟、接口、偏好……）", projectInsPh: "例如：所有设计都跑在 ECP5 LFE5U-25F 上，系统时钟 100 MHz，AXI-Stream 接口，优先用 BRAM，DSP 不超过 24 个。", saved: "已保存", saving: "保存中…", inProject: "这个专案里的对话", newInProject: "＋ 在此专案新建对话", noThreads: "还没有对话。在下面输入第一句话，它就会归到这个专案。", renameProject: "重命名专案：", delProject: "删除这个专案？里面的对话会保留为独立对话。", moveTo: "移到专案", moveOut: "移出专案", standalone: "独立对话", menuExport: "导出 Markdown", menuWorktable: "打开工作台", menuRename: "重命名", menuDelete: "删除对话", menuRenameP: "重命名专案", menuDeleteP: "删除专案", workbench: "工作台", helloInProject: "新对话将归入专案", menuAgh: "在 AGH 工作台打开（完整记录）", aghTitle: "Agnes Harness 工作台：每个会话的完整记录、工具调用、审批与模型设置（操作员）", tournament: "多路竞标", leader: "领先", tie: "打平（差距在布局布线种子噪声 6% 以内）", cmpVariants: "并排比较两路", variants: "路", tourDone: "全部完成", tourLive: "进行中", tourOpen: "看这一路", clampTitle: "点击展开 / 收起规划器的完整诊断", projBest: "这个专案交付的设计", projBestHint: "每个生成器 × 器件里实测最好的可行设计；点开看它的对话", pkgShort: "交付包", projRtl: "这个专案里上传的 RTL", projRtlHint: "后续对话可以直接以 rtl:<id> 作为设计继续优化它们", dropHere: "拖到这里归入专案", dropOut: "拖到这里变成独立对话", convCount: "{n} 个对话", runCount: "{n} 次运行", seedsLabel: "种子", median: "中位数", target: "目标", latency: "延迟（周期）", equiv: "等价", props: "流协议", trust: "验证", trustTitle: "这个设计经过了哪些验证：行为级仿真、网表仿真、多种子布局布线、形式验证、变异测试", notRun: "未跑", seeds: "{n} 粒种子", spread: "离散 {n}%", netlist: "网表仿真", tBehav: "行为级", refIndep: "独立参考", refGen: "生成器向量", refSelf: "仅自检", refTitle: "行为级期望值的来源：独立参考模型（另一个看不到 RTL 的会话按规格写）/ 生成器自己的向量 / 设计自带的自检台", studio: "设计工作室", studioSteps: ["设计师编写", "独立参考模型", "检查", "注册"], studioDesign: "设计师", studioRef: "独立参考", studioRevise: "改写请求", studioRevised: "修订",
  stages: ["行为级仿真", "综合后功能", "综合后时序", "实现后功能", "实现后时序"], signoff: "实现后时序仿真通过",
  design: "设计", backend: "器件", constraints: "约束", objective: "目标", locked: "固定参数", start: "起点", closest: "最接近",
  secs: "秒", critical: "关键路径", startFrom: "从起点设计开始。", fork: "基于上一次的最佳设计继续。",
  examples: ["一个 32 阶低通 FIR，Spartan-7，采样率 250 MHz，尽量少用 DSP", "INT8 矩阵乘，Lattice ECP5，180 MHz 以上，48 个 DSP 以内",
             "Spartan-7 上跑 300 MHz 的 INT8 systolic 阵列，DSP 不超过 32", "一个小的 16 阶 FIR，16 位系数，越省资源越好",
             "4 倍抽取 FIR，32 阶，ECP5，120 MHz", "NCO 加复数混频器，16 位，ECP5 上 150 MHz，相位抖动打开", "4 阶 Butterworth IIR 低通，截止 0.1，iCE40，位精确",
             "sky130 流片：64 阶 FIR，200 MHz，面积最小", "扫描 fold = 1, 2, 4 和 pipe_mul = 0, 1：32 阶 FIR 在 ECP5 上每个点都实测"],
  more: "更多", less: "收起",
};
L.en = {
  hello: "What hardware do you want?", blurb: "Describe it in a sentence. I generate the Verilog, run simulation, synthesis and place-and-route until it meets your requirements, then hand you the result.",
  placeholder: "Describe the hardware you want, or ask for changes to the current result…", hint: "⏎ send · ⇧⏎ newline", stop: "■ Stop", thinking: "Thinking", newChat: "New chat",
  spec: "Understood spec", progress: "Running", traj: "Trajectory", result: "Result", noResult: "No feasible design", iter: "iteration {n}", iters: "{n} iterations", feasible: "{n} feasible",
  planning: "planning", sim: "behavioral sim", synth: "synthesis", pnr: "place & route", queued: "queued", stopped: "stopped", error: "error",
  pass: "pass", fail: "fail", viewRtl: "View Verilog", hideRtl: "Hide Verilog", download: "Download {f}", pkg: "Download package (zip)", worktable: "Open worktable", compare: "Compare with previous", left: "left at most", del: "Delete conversation", alts: "Also worth trying:", designs: "designs", covTitle: "scenarios the behavioral testbench exercised (hit counts)", formal: "Formal check", formalTitle: "SymbiYosys: bounded equivalence of the measured RTL vs the current generator + stream properties", formalRunning: "checking…", queued: "queued · sent when this turn ends", exportTitle: "export this conversation as Markdown (specs, every iteration, best design, proposals)", mutation: "Mutation score", mutationTitle: "inject 24 bugs into the RTL (add/sub swaps, flipped compares, stuck enables ...) and count how many the behavioral bench catches: the strength of the verification", mutationRunning: "mutating…", survived: "survived", sweep: "Design-space sweep", points: "points", paretoPts: "Pareto-optimal", point: "knobs", paretoNote: "no other feasible point is at least as good on both {a} and {b}", attach: "Attach RTL (.sv / .v): run your own design through the same ladder", tooBig: "file over 400 KB", attachLead: "This is my own RTL. Run it through the same ladder (simulation, synthesis, place-and-route, formal) and tune the parameters it exposes.", failedCalls: "{n} failed", modeFast: "fast", modeDeep: "deep", modeAuthor: "author", proposal: "Generator change proposal", gating: "gating…", gatePassed: "gate passed", gateFailed: "gate failed", gateSteps: ["emit + elaborate every preset", "behavioral simulation", "formal equivalence (unchanged IRs)", "testbench mutation score"], abRun: "measured A/B", merged: "merged", viewDiff: "View diff", lines: "lines", modeTitle: "agent mode (from the next turn): ", pendingMode: "a new conversation starts in this mode", agentSteps: "Agent steps · {n} tool calls", usage: "{calls} model calls · {tok} tokens · {usd} · {s}", usageTitle: "Agnes Harness ledger: this turn's model calls, input/output tokens (cache hits included), estimated cost and wall time", sessUsage: "{turns} turns · {tok} tokens · {usd}", sessUsageTitle: "this conversation so far (Agnes Harness ledger) · context {ctx} tokens", today: "Today", earlier: "Earlier", agentAt: "agent ·", runs: "runs", resumed: "resumed", resumedTitle: "a server restart cut the previous turn short; the agent continued from its own records", delConfirm: "Delete this conversation? Its runs stay available under the unassigned worktable.", rename: "Rename conversation:", refine: "Refine from here", llmDown: "The model account is unavailable (DeepSeek answered {n}): chat and planner are paused; runs already started still finish. Top up, then send a message to resume.", llmDownTitle: "the status the server got from its last model call; the next successful call clears this", search: "Search conversations: title, your requests, design, device", noMatch: "no matching conversation", projects: "Projects", convs: "Conversations", newProject: "New project", newProjectPrompt: "Project name (e.g. DSP front end for board A):", projectIns: "Project instructions · every conversation in this project starts from them (device, clock, interfaces, preferences ...)", projectInsPh: "e.g. Every design targets the ECP5 LFE5U-25F, 100 MHz system clock, AXI-Stream interfaces, prefer BRAM, at most 24 DSPs.", saved: "saved", saving: "saving…", inProject: "Conversations in this project", newInProject: "＋ New conversation here", noThreads: "No conversations yet. Type the first request below and it will be filed here.", renameProject: "Rename project:", delProject: "Delete this project? Its conversations stay as standalone conversations.", moveTo: "Move to project", moveOut: "Remove from project", standalone: "Standalone", menuExport: "Export Markdown", menuWorktable: "Open worktable", menuRename: "Rename", menuDelete: "Delete conversation", menuRenameP: "Rename project", menuDeleteP: "Delete project", workbench: "Workbench", helloInProject: "This conversation will be filed under", menuAgh: "Open in the AGH workbench (full record)", aghTitle: "Agnes Harness workbench: every session's full record, tool calls, approvals and model settings (operator)", tournament: "Tournament", leader: "leader", tie: "tie (within the 6% P&R seed noise)", cmpVariants: "compare the variants side by side", variants: "variants", tourDone: "all done", tourLive: "in progress", tourOpen: "open this run", clampTitle: "click to expand / fold the planner's full diagnosis", projBest: "Designs this project delivers", projBestHint: "the best measured feasible design per generator × device; open its conversation", pkgShort: "package", projRtl: "RTL uploaded in this project", projRtlHint: "later conversations can keep tuning them as design \"rtl:<id>\"", dropHere: "drop here to file under the project", dropOut: "drop here to make standalone", convCount: "{n} conversations", runCount: "{n} runs", seedsLabel: "seeds", median: "median", target: "target", latency: "latency cyc", equiv: "equivalence", props: "props", trust: "Verified by", trustTitle: "what this design has been through: behavioral simulation, netlist simulation, multi-seed place-and-route, formal check, mutation test", notRun: "not run", seeds: "{n} seeds", spread: "spread {n}%", netlist: "netlist sim", tBehav: "behavioral", refIndep: "independent reference", refGen: "generator vectors", refSelf: "self-check only", refTitle: "where the behavioral expectation comes from: an independent reference model (another session, never sees the RTL, writes it from the spec) / the generator's own vectors / the design's own bench", studio: "Design studio", studioSteps: ["designer writes", "independent reference", "check", "registered"], studioDesign: "designer", studioRef: "independent reference", studioRevise: "rewrite requested", studioRevised: "revision",
  stages: ["behavioral", "post-synth func", "post-synth timing", "post-impl func", "post-impl timing"], signoff: "post-implementation timing sim passed",
  design: "design", backend: "device", constraints: "constraints", objective: "objective", locked: "locked", start: "start", closest: "closest",
  secs: "s", critical: "critical path", startFrom: "Start from the initial design.", fork: "Continue from the previous best design.",
  examples: ["A 32-tap lowpass FIR on Spartan-7 at 250 MS/s, as few DSPs as possible", "INT8 matrix multiply on Lattice ECP5, 180 MHz or better, at most 48 DSPs",
             "An INT8 systolic array on Spartan-7 running at 300 MHz with no more than 32 DSPs", "A small 16-tap FIR with 16-bit coefficients, as cheap as possible",
             "A decimate-by-4 FIR, 32 taps, ECP5, 120 MHz", "NCO with complex mixer, 16-bit, 150 MHz on ECP5, phase dither on", "4th-order Butterworth IIR lowpass, cutoff 0.1, iCE40, bit-exact",
             "sky130 tapeout: a 64-tap FIR at 200 MHz, smallest area", "Sweep fold = 1, 2, 4 and pipe_mul = 0, 1: measure every point of a 32-tap FIR on ECP5"],
  more: "more", less: "fewer",
};
let T = zh ? L.zh : L.en;
const t = (k, v) => T[k].replace("{n}", v).replace("{f}", v);
function setLang(isZh) { zh = isZh; T = zh ? L.zh : L.en; $("#input").placeholder = T.placeholder; $("#chint").textContent = T.hint; $("#stop").textContent = T.stop; const sb = $("#thread-search"); if (sb) { sb.placeholder = T.search; sb.title = T.search; } }
const STAGE_KEYS = ["behavioral", "post_synth_func", "post_synth_time", "post_impl_func", "post_impl_time"];
const TOP = { matmul: "matmul_top", fir: "fir_top", conv2d: "conv2d_top", fir_chain: "fir_chain_top", cordic: "cordic_top", fft: "fft_top", graph: "graph_top" };
let DEV = { ecp5: "ECP5 LFE5U-45F", xc7s50: "Spartan-7 XC7S50", ice40: "iCE40 UP5K", gowin: "Gowin GW1NR-9" };
let BSTAGES = { ecp5: ["behavioral", "post_synth_func"], xc7s50: STAGE_KEYS_ALL(), ice40: ["behavioral", "post_synth_func"], gowin: ["behavioral", "post_synth_func"] };
function STAGE_KEYS_ALL() { return ["behavioral", "post_synth_func", "post_synth_time", "post_impl_func", "post_impl_time"]; }
const stagesOf = (backend) => BSTAGES[backend] || ["behavioral"];

// ---------------------------------------------------------------- state
let threads = [], tid = null, thread = null, timer = null;
let projects = [], pid = null;                       // the project page shown, or the project a new conversation goes into
const collapsed = new Set(JSON.parse(localStorage.getItem("sa.collapsed") || "[]"));
const jobs = {};                 // job id -> {events, seen, status, summary, error, spec, max_iter}
const open = new Set();          // "<job>:<card>" of expanded cards
let openWanted = null;           // card kinds to expand on first render (?open=result,traj)
const rtlShown = new Set();
const trustCache = new Map();   // jid -> {stages, seeds, formal, mutation, running} from GET /jobs/<id>/trust

// ---------------------------------------------------------------- boot
(async function boot() {
  $("#hello h1").textContent = T.hello; $("#hello p").textContent = T.blurb; $("#input").placeholder = T.placeholder; $("#chint").textContent = T.hint; $("#stop").textContent = T.stop; $("#new-chat").title = T.newChat;
  // four examples up front, the rest (the newer generators, ASIC, sweeps) behind "more"
  T.examples.forEach((ex, i) => { const b = el(`<button${i >= 4 ? ' class="extra" hidden' : ""}>${esc(ex)}</button>`); b.onclick = () => send(ex); $("#examples").appendChild(b); });
  const more = el(`<button class="more">${T.more} ▾</button>`);
  more.onclick = () => { const on = $("#examples .extra").hidden; document.querySelectorAll("#examples .extra").forEach((b) => { b.hidden = !on; }); more.textContent = on ? `${T.less} ▴` : `${T.more} ▾`; };
  $("#examples").appendChild(more);
  try { $("#who").textContent = (await api("/me")).subject; } catch { $("#who").textContent = "未登录"; }
  try { const sc = await api("/schema"); DEV = Object.fromEntries(Object.entries(sc.backends).map(([k, v]) => [k, v])); BFACTS = sc.backend_facts; BSTAGES = Object.fromEntries(Object.entries(sc.backend_facts).map(([k, v]) => [k, v.sim_stages])); } catch {}
  // agent modes (the harness model slot behind the chat): a per-conversation switch next to the composer
  try { const h = await api("/health"); if (h.modes) { MODES = h.modes; defaultMode = h.default_mode || Object.keys(MODES)[0]; renderModes(); } llmBanner(h.llm); AGH_WEB = !!h.agh_web; if (h.agh_web) { const a = el(`<a class="wb agh" href="/agh/" target="_blank" title="${esc(T.aghTitle)}">⚙ AGH</a>`); $("#wb-link").insertAdjacentElement("afterend", a); } } catch {}
  setInterval(async () => { try { llmBanner((await api("/health")).llm); } catch {} }, 30000);
  $("#new-chat").onclick = () => openThread(null, thread?.project ?? pid);   // a new conversation stays in the project you are in
  $("#wb-label").textContent = T.workbench;
  $("#more").onclick = (e) => { e.stopPropagation(); topMenu($("#more")); };
  $("#p-ins-label").textContent = T.projectIns; $("#p-ins").placeholder = T.projectInsPh; $("#p-threads-label").textContent = T.inProject; $("#p-new").textContent = T.newInProject;
  $("#p-new").onclick = () => openThread(null, pid);
  $("#p-name").onchange = async () => { const name = $("#p-name").value.trim(); if (!name || !pid) return; await post(`/projects/${pid}`, { name }); await refreshThreads(); renderCrumbs(); };
  $("#p-ins").oninput = () => { $("#p-ins-state").textContent = T.saving; clearTimeout(insTimer); insTimer = setTimeout(saveInstructions, 800); };
  $("#p-ins").onblur = () => { clearTimeout(insTimer); saveInstructions(); };
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
  const openQ = new URLSearchParams(location.search).get("open");
  if (openQ) openWanted = new Set(openQ.split(","));   // ?open=result,traj|all expands those cards on load
  route();
  window.addEventListener("hashchange", route);
  const sb = $("#thread-search"); sb.placeholder = T.search; sb.title = T.search;
  sb.oninput = () => { threadQuery = sb.value; renderThreads(); };
  sb.onkeydown = (e) => { if (e.key === "Escape") { sb.value = ""; threadQuery = ""; renderThreads(); sb.blur(); } if (e.key === "Enter") { const first = $("#rail-list .row.thread"); if (first) openThread(first.dataset.id); } };
  document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); sb.focus(); sb.select(); } });
  $("#input").focus();
})();

// ---------------------------------------------------------------- rail: projects (folders with standing instructions) and standalone conversations
let threadQuery = "";
function threadMatches(th, q) {
  if (!q) return true;
  const hay = `${th.title || ""} ${th.text || ""} ${th.design || ""} ${th.backend || ""} ${th.id}`.toLowerCase();
  return q.split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
}
async function refreshThreads() {
  [threads, projects] = await Promise.all([api("/threads"), api("/projects")]);
  renderThreads();
}
function threadRow(th) {
  const b = th.best?.metrics;
  const m = th.busy ? (th.agent ? `${T.agentAt} ${th.agent}` : T.thinking)
    : th.status === "done" && b ? `${fmt(b.fmax_mhz, 0)} MHz · ${b.area_um2 != null ? `${b.lut} cells` : `${b.dsp} DSP`}${th.n_runs > 1 ? ` · ${th.n_runs} ${T.runs}` : ""}`
    : th.status ? ({ running: T.progress, queued: T.queued, failed: T.noResult, error: T.error, interrupted: T.stopped }[th.status] || th.status) : "";
  const dev = th.backend ? `<span class="dev">${esc(th.backend)}</span>` : "";
  const li = el(`<div class="row thread ${th.id === tid ? "sel" : ""}" data-id="${esc(th.id)}" title="${esc(th.title)}"><span class="dot ${th.busy ? "busy" : th.status || ""}"></span><div class="tx"><div class="t">${esc(th.title)}</div><div class="m">${dev}<span class="mt">${esc(m)}</span></div></div><button class="dots" title="…">⋯</button></div>`);
  li.onclick = () => openThread(th.id);
  li.querySelector(".dots").onclick = (e) => { e.stopPropagation(); threadMenu(th, e.currentTarget); };
  li.draggable = true;
  li.ondragstart = (e) => { e.dataTransfer.setData("text/plain", th.id); e.dataTransfer.effectAllowed = "move"; li.classList.add("dragging"); $("#rail-list").classList.add("dnd"); };
  li.ondragend = () => { li.classList.remove("dragging"); $("#rail-list").classList.remove("dnd"); document.querySelectorAll("#rail-list .over").forEach((x) => x.classList.remove("over")); };
  return li;
}
function dropTarget(node, project) {
  // `project`: a project id, or null for "standalone"
  node.ondragover = (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; node.classList.add("over"); };
  node.ondragleave = () => node.classList.remove("over");
  node.ondrop = async (e) => {
    e.preventDefault(); node.classList.remove("over");
    const id = e.dataTransfer.getData("text/plain");
    const th = threads.find((x) => x.id === id);
    if (!th || (th.project || null) === project) return;
    await post(`/threads/${id}/project`, { project });
    if (project) collapsed.delete(project);
    await refreshThreads();
    if (id === tid && thread) { thread.project = project; renderCrumbs(); }
  };
}
function renderThreads() {
  const box = $("#rail-list"); box.innerHTML = "";
  const q = threadQuery.trim().toLowerCase();
  const shown = threads.filter((th) => threadMatches(th, q));
  if (q) {
    // a search flattens everything into one list
    if (!shown.length) { box.appendChild(el(`<div class="group">${esc(T.noMatch)}</div>`)); return; }
    box.appendChild(el(`<div class="group">${shown.length} / ${threads.length}</div>`));
    for (const th of shown) box.appendChild(threadRow(th));
    return;
  }
  // projects: a folder each, collapsible, its conversations inside
  const head = el(`<div class="group projects"><span>${esc(T.projects)}</span><button class="ghost mini" title="${esc(T.newProject)}">＋</button></div>`);
  head.querySelector("button").onclick = newProject;
  box.appendChild(head);
  for (const p of projects) {
    const open = !collapsed.has(p.id);
    const ths = shown.filter((th) => th.project === p.id);
    const f = el(`<div class="folder ${open ? "open" : ""}"><div class="row proj ${pid === p.id && !tid ? "sel" : ""}" data-pid="${esc(p.id)}"><span class="chev">${open ? "▾" : "▸"}</span><span class="pname">${esc(p.name)}</span><span class="cnt">${p.busy ? '<i class="busy"></i>' : ""}${ths.length}</span><button class="dots" title="…">⋯</button></div><div class="items"></div></div>`);
    const row = f.querySelector(".row");
    dropTarget(row, p.id); dropTarget(f.querySelector(".items"), p.id);
    row.onclick = () => openProject(p.id);
    row.querySelector(".chev").onclick = (e) => { e.stopPropagation(); if (collapsed.has(p.id)) collapsed.delete(p.id); else collapsed.add(p.id); localStorage.setItem("sa.collapsed", JSON.stringify([...collapsed])); renderThreads(); };
    row.querySelector(".dots").onclick = (e) => { e.stopPropagation(); projectMenu(p, e.currentTarget); };
    const items = f.querySelector(".items");
    if (open) for (const th of ths) items.appendChild(threadRow(th));
    box.appendChild(f);
  }
  // standalone conversations, by recency
  const loose = shown.filter((th) => !th.project || !projects.some((p) => p.id === th.project));
  const convHead = el(`<div class="group convs">${esc(T.convs)}<span class="hint">${esc(T.dropOut)}</span></div>`);
  dropTarget(convHead, null);
  if (loose.length || projects.length) box.appendChild(convHead);
  const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
  let group = null;
  for (const th of loose) {
    const g = (th.last || th.created) * 1000 >= dayStart.getTime() ? T.today : T.earlier;
    if (g !== group) { group = g; box.appendChild(el(`<div class="sub">${esc(g)}</div>`)); }
    box.appendChild(threadRow(th));
  }
}

// one popup menu for everything: [{label, run, danger, head}]
function showMenu(anchor, items) {
  const m = $("#menu"); m.innerHTML = ""; m.hidden = false;
  for (const it of items) {
    if (it.head) { m.appendChild(el(`<div class="mh">${esc(it.head)}</div>`)); continue; }
    const b = el(`<button class="${it.danger ? "danger" : ""} ${it.on ? "on" : ""}">${esc(it.label)}</button>`);
    b.onclick = async (e) => { e.stopPropagation(); hideMenu(); await it.run(); };
    m.appendChild(b);
  }
  const r = anchor.getBoundingClientRect();
  m.style.top = `${Math.min(r.bottom + 4, window.innerHeight - m.offsetHeight - 8)}px`;
  m.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - m.offsetWidth - 8))}px`;
  setTimeout(() => document.addEventListener("click", hideMenu, { once: true }), 0);
}
function hideMenu() { $("#menu").hidden = true; }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });

function moveItems(th) {
  const items = projects.map((p) => ({ label: `→ ${p.name}`, on: th.project === p.id, run: async () => { await post(`/threads/${th.id}/project`, { project: p.id }); await refreshThreads(); if (th.id === tid && thread) { thread.project = p.id; renderCrumbs(); } } }));
  if (th.project) items.push({ label: T.moveOut, run: async () => { await post(`/threads/${th.id}/project`, { project: null }); await refreshThreads(); if (th.id === tid && thread) { thread.project = null; renderCrumbs(); } } });
  return items.length ? [{ head: T.moveTo }, ...items] : [];
}
function threadMenu(th, anchor) {
  showMenu(anchor, [
    { label: T.menuRename, run: async () => { const nt = prompt(T.rename, th.title); if (nt != null && nt.trim()) { await post(`/threads/${th.id}/title`, { title: nt.trim() }); await refreshThreads(); if (th.id === tid && thread) { thread.title = nt.trim(); renderCrumbs(); } } } },
    { label: T.menuExport, run: () => { location.href = `/api/threads/${encodeURIComponent(th.id)}/export.md`; } },
    { label: T.menuWorktable, run: () => window.open(`/work?thread=${encodeURIComponent(th.id)}`, "_blank") },
    ...(AGH_WEB && th.id === tid && (thread?.agh_session || thread?.agh_author_session) ? [{ label: T.menuAgh, run: () => window.open(`/agh/?session=${encodeURIComponent((thread.mode === "author" && thread.agh_author_session) || thread.agh_session || thread.agh_author_session)}`, "_blank") }] : []),
    ...moveItems(th),
    { label: T.menuDelete, danger: true, run: async () => { if (!confirm(T.delConfirm)) return; try { await api(`/threads/${th.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; } if (th.id === tid) openThread(null, th.project); await refreshThreads(); } },
  ]);
}
function projectMenu(p, anchor) {
  showMenu(anchor, [
    { label: T.newInProject.replace(/^＋ /, ""), run: () => openThread(null, p.id) },
    { label: T.menuRenameP, run: async () => { const nt = prompt(T.renameProject, p.name); if (nt != null && nt.trim()) { await post(`/projects/${p.id}`, { name: nt.trim() }); await refreshThreads(); renderCrumbs(); if (pid === p.id && !tid) $("#p-name").value = nt.trim(); } } },
    { label: T.menuWorktable, run: () => window.open(`/work?project=${encodeURIComponent(p.id)}`, "_blank") },
    { label: T.menuDeleteP, danger: true, run: async () => { if (!confirm(T.delProject)) return; await api(`/projects/${p.id}`, { method: "DELETE" }); if (pid === p.id) pid = null; await refreshThreads(); if (!tid) openThread(null, null); } },
  ]);
}
function topMenu(anchor) {
  if (tid && thread) return threadMenu({ id: tid, title: thread.title || "", project: thread.project || null }, anchor);
  const p = projects.find((x) => x.id === pid);
  if (p) return projectMenu(p, anchor);
}
async function newProject() {
  const name = prompt(T.newProjectPrompt, "");
  if (!name || !name.trim()) return;
  const p = await post("/projects", { name: name.trim() });
  await refreshThreads();
  openProject(p.id);
}

// ---------------------------------------------------------------- views: a conversation, a project page, or a blank page (optionally inside a project)
function route() {
  const h = location.hash.slice(1);
  if (h.startsWith("project/")) openProject(h.slice(8), true);
  else if (h.startsWith("new/")) openThread(null, h.slice(4), true);
  else if (h !== (tid || "")) openThread(h || null, h ? null : pid, true);
}
function openThread(id, project = null, fromHash = false) {
  clearTimeout(timer);
  tid = id; thread = null; pid = id ? null : (project || null); open.clear(); rtlShown.clear();
  if (!fromHash) location.hash = id || (pid ? `new/${pid}` : "");
  $("#msgs").innerHTML = ""; $("#hello").hidden = !!id; $("#project").hidden = true; $("#stop").hidden = true; $("#more").hidden = !id;
  const hp = $("#hello-project"), p = projects.find((x) => x.id === pid);
  hp.hidden = !p; if (p) hp.innerHTML = `<span class="muted">${esc(T.helloInProject)}</span> <b>▣ ${esc(p.name)}</b>${p.instructions ? `<div class="pins-preview">${esc(p.instructions.slice(0, 400))}</div>` : ""}`;
  pendingMode = null; if (MODES) renderModes();
  renderCrumbs();
  document.querySelectorAll("#rail-list .row").forEach((r) => r.classList.toggle("sel", r.dataset.id === id));
  // on a phone the rail is a drawer: picking a conversation closes it
  $(".shell").classList.remove("rail-shown");
  if (id) poll();
  $("#input").focus();
}
let insTimer = null;
async function openProject(id, fromHash = false) {
  clearTimeout(timer);
  tid = null; thread = null; pid = id; open.clear(); rtlShown.clear();
  if (!fromHash) location.hash = `project/${id}`;
  let p;
  try { p = await api(`/projects/${id}`); } catch { openThread(null, null); return; }
  $("#msgs").innerHTML = ""; $("#hello").hidden = true; $("#project").hidden = false; $("#stop").hidden = true; $("#more").hidden = false;
  $("#p-name").value = p.name; $("#p-ins").value = p.instructions || ""; $("#p-ins-state").textContent = "";
  $("#p-stats").textContent = `${t("convCount", p.n_threads)} · ${t("runCount", p.threads.reduce((a, x) => a + (x.n_runs || 0), 0))}`;
  const list = $("#p-threads"); list.innerHTML = "";
  if (!p.threads.length) list.appendChild(el(`<div class="muted small">${esc(T.noThreads)}</div>`));
  for (const th of p.threads) {
    const b = th.best?.metrics;
    const m = th.busy ? T.thinking : th.status === "done" && b ? `${fmt(b.fmax_mhz, 0)} MHz · ${b.area_um2 != null ? `${b.lut} cells` : `${b.lut} LUT · ${b.dsp} DSP`}` : th.status ? ({ running: T.progress, queued: T.queued, failed: T.noResult, error: T.error, interrupted: T.stopped }[th.status] || th.status) : "";
    const row = el(`<div class="pth"><span class="dot ${th.busy ? "busy" : th.status || ""}"></span><div class="tx"><div class="t">${esc(th.title)}</div><div class="m">${th.design ? `<span class="dev">${esc(th.design)} · ${esc(th.backend)}</span>` : ""}${esc(m)}</div></div><span class="when muted">${new Date(th.last * 1000).toLocaleDateString()}</span></div>`);
    row.onclick = () => openThread(th.id);
    list.appendChild(row);
  }
  const bb = $("#p-best"); bb.innerHTML = "";
  if (p.best?.length) {
    bb.appendChild(el(`<div class="plist-head"><span>${esc(T.projBest)}</span><span class="muted" style="text-transform:none;letter-spacing:0">${esc(T.projBestHint)}</span></div>`));
    const grid = el(`<div class="bestgrid"></div>`);
    for (const b of p.best) {
      const res = b.area_um2 != null ? `${b.lut} cells · ${fmt(b.area_um2 / 1000, 1)}k µm²` : `${b.lut} LUT · ${b.dsp} DSP`;
      const card = el(`<div class="bestcard"><div class="bh"><span class="mono">${esc(b.design)}</span><span class="dev">${esc(b.backend)}</span>${b.signoff ? `<span class="so" title="${esc(T.signoff)}">✓</span>` : ""}</div><div class="bn"><b>${fmt(b.fmax_mhz, 0)} MHz</b><span>${res}</span><span>${b.throughput} ${esc(b.throughput_unit)}</span></div><div class="bt muted" title="${esc(b.task)}">${esc(b.title || b.task)}</div><div class="bl"><a href="/api/jobs/${esc(b.job)}/package">${esc(T.pkgShort)} ↓</a></div></div>`);
      card.onclick = (e) => { if (e.target.tagName !== "A") openThread(b.thread); };
      grid.appendChild(card);
    }
    bb.appendChild(grid);
  }
  const rtl = $("#p-rtl"); rtl.innerHTML = "";
  if (p.rtl?.length) {
    rtl.appendChild(el(`<div class="plist-head"><span>${esc(T.projRtl)}</span><span class="muted" style="text-transform:none;letter-spacing:0">${esc(T.projRtlHint)}</span></div>`));
    for (const b of p.rtl) rtl.appendChild(el(`<div class="prtl"><span class="mono">rtl:${esc(b.id)}</span><span class="t">${esc(b.name || b.top)}</span><span class="muted small">top ${esc(b.top)} · ${(b.files || []).length} files · ${(b.params || []).length ? "knobs " + esc(b.params.join(", ")) : "no knobs"}</span></div>`));
  }
  renderCrumbs();
  document.querySelectorAll("#rail-list .row").forEach((r) => r.classList.toggle("sel", r.dataset.pid === id));
  $(".shell").classList.remove("rail-shown");
}
async function saveInstructions() {
  if (!pid || $("#project").hidden) return;
  try { await post(`/projects/${pid}`, { instructions: $("#p-ins").value }); $("#p-ins-state").textContent = T.saved; } catch (e) { $("#p-ins-state").textContent = e.message; }
}
function renderCrumbs() {
  const c = $("#crumbs"); c.innerHTML = "";
  const p = projects.find((x) => x.id === (thread?.project ?? pid));
  if (p) { const a = el(`<a class="crumb proj">▣ ${esc(p.name)}</a>`); a.onclick = () => openProject(p.id); c.appendChild(a); }
  if (tid) { if (p) c.appendChild(el(`<span class="sep">›</span>`)); c.appendChild(el(`<span class="crumb title">${esc(thread?.title || "")}</span>`)); }
}

let MODES = null, defaultMode = "fast", pendingMode = null, AGH_WEB = false;
// a strip under the top bar while the model account is out (402 / 401): the user learns before typing, not after
function llmBanner(st) {
  let b = $("#llm-banner");
  if (!st || st.ok !== false) { if (b) b.remove(); return; }
  if (!b) { b = el(`<div id="llm-banner" class="banner"></div>`); $(".topbar").insertAdjacentElement("afterend", b); }
  b.textContent = t("llmDown", st.code || "?") + (st.balance ? ` · ${st.balance}` : ""); b.title = T.llmDownTitle + (st.since ? ` · ${new Date(st.since * 1000).toLocaleString()}` : "");
}
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
  $("#hello").hidden = true; $("#project").hidden = true;
  // optimistic echo; while the agent is mid-turn the message queues behind it instead
  if (!thread) thread = { id: tid, title: "", messages: [], jobs: {}, busy: true };
  if (thread.busy) (thread.queue = thread.queue || []).push({ text: msg, t: Date.now() / 1000 });
  else { thread.messages.push({ role: "user", kind: "text", text: msg, t: Date.now() / 1000 }); thread.busy = true; }
  render();
  $("#scroll").scrollTop = $("#scroll").scrollHeight;
  try {
    const r = await post(`/threads/${tid || "new"}/chat`, { text: msg, ...(pid && !tid ? { project: pid } : {}) });
    if (!tid) { tid = r.thread; thread.id = tid; thread.project = pid; $("#project").hidden = true; $("#more").hidden = false; location.hash = tid; if (pendingMode) { try { await post(`/threads/${tid}/mode`, { mode: pendingMode }); } catch {} pendingMode = null; } }
    clearTimeout(timer); await poll(); refreshThreads();
  } catch (e) {
    thread.messages.push({ role: "assistant", kind: "text", text: e.message, t: Date.now() / 1000 }); thread.busy = false; render();
  }
}

// ---------------------------------------------------------------- render
function render() {
  if (!thread) return;
  const first = thread.messages.find((m) => m.role === "user");
  if ($("#crumbs .title")?.textContent !== (thread.title || "")) renderCrumbs();
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
  const st = m.studio && thread.studios?.[m.studio];
  const tj = (m.jobs || []).map((x) => [jobs[x]?.seen, jobs[x]?.status]);
  return JSON.stringify([m.role, m.kind, m.text, m.job, j?.seen, j?.status, j?.error && 1, [...open].filter((k) => k.startsWith((m.job || m.proposal || m.tournament || m.studio) + ":")), rtlShown.has(m.job), pr?.status, pr?.steps?.length, pr?.ab?.job, pr?.merged && 1, tj, st?.status, st?.steps?.length, st?.revisions]);
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
    if (s.mutation) row.appendChild(el(`<div class="muted small">${s.mutation.map((x) => x.baseline === false ? `${esc(x.design)}: <span class="x">bench fails on the unmutated RTL</span>` : `${esc(x.design)}: ${x.killed}/${x.total} killed (${x.score == null ? "—" : Math.round(x.score * 100) + "%"})${x.survived?.length ? ` · survived ${esc(x.survived.join(", "))}` : ""}`).join(" · ")}</div>`));
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

// the design studio: the designer session's pipeline for a block no generator covers (or a forked design), each step
// with its check report; when registered, the design kind the runs use
function studioCard(m) {
  const st = thread.studios?.[m.studio];
  if (!st) return el(`<div class="prose dim">${esc(T.studio)} · ${esc(m.studio)}</div>`);
  const busy = ["designing", "referencing", "checking", "revising"].includes(st.status) || st.running;
  const ZH = { designing: "设计师编写中", referencing: "生成独立参考模型", checking: "检查中", revising: "设计师修订中", registered: "已注册", failed: "未通过", interrupted: "被打断", new: "待开始" };
  const EN = { designing: "designer writing", referencing: "writing the independent reference", checking: "checking", revising: "designer revising", registered: "registered", failed: "failed", interrupted: "interrupted", new: "new" };
  const stat = (zh ? ZH : EN)[st.status] || st.status;
  const refk = st.reference?.kind === "generator" ? T.refGen : st.reference?.kind === "independent" ? T.refIndep : st.reference?.kind === "self-check" ? T.refSelf : "";
  const sub = `${busy ? `<span class="live">${esc(stat)}</span>` : st.status === "registered" ? `<span class="ok">${esc(stat)}</span>` : `<span class="x">${esc(stat)}</span>`}${refk ? ` · ${esc(refk)}` : ""}${st.design ? ` · <span class="mono">${esc(st.design)}</span>` : ""}${st.revisions > 1 ? ` · ${st.revisions} ${zh ? "个修订" : "revisions"}` : ""}`;
  const d = card(`${m.studio}:studio`, T.studio, sub);
  const inner = d.querySelector(".inner");
  inner.appendChild(el(`<div class="prose"><b>${esc(st.name || "")}</b>${st.origin ? `<p class="muted small">${zh ? "分叉自" : "forked from"} ${esc(st.origin.design)} · ${esc(st.origin.job)} · ${esc(st.origin.label || "")}</p>` : ""}${st.brief && !st.origin ? `<p>${esc(st.brief.slice(0, 600))}</p>` : ""}</div>`));
  const steps = el(`<div class="gate-steps"></div>`);
  for (const s of st.steps || []) {
    const row = el(`<div class="gstep"><span class="${s.ok ? "v" : s.ok === false ? "x" : "muted"}">${s.ok ? "✓" : s.ok === false ? "✕" : "·"}</span> ${esc(s.name)}${s.rev != null ? ` → rev ${s.rev}` : ""}${s.instruction ? `<div class="muted small">${esc(s.instruction.slice(0, 300))}</div>` : ""}</div>`);
    if (s.report?.steps) row.appendChild(el(`<div class="muted small">${s.report.steps.map((x) => `${x.ok ? "✓" : "✕"} ${esc(x.name)}${x.samples ? ` (${x.samples})` : ""}${x.seconds != null ? ` ${x.seconds}s` : ""}`).join(" · ")}</div>`));
    const bad = (s.report?.steps || []).find((x) => !x.ok);
    if (bad && (bad.log || bad.error || bad.mismatch)) row.appendChild(el(`<pre class="log">${esc(String(bad.error || bad.log || JSON.stringify(bad.mismatch)).slice(0, 900))}</pre>`));
    if (s.error && !s.report) row.appendChild(el(`<pre class="log">${esc(String(s.error).slice(0, 900))}</pre>`));
    steps.appendChild(row);
  }
  if (busy && !(st.steps || []).length) steps.appendChild(el(`<div class="muted small">${T.studioSteps.map((x) => "· " + esc(x)).join("<br>")}</div>`));
  inner.appendChild(steps);
  if (st.usage?.usd_micros != null) inner.appendChild(el(`<div class="usage">${esc(T.studioDesign)}: ${esc(usd(st.usage.usd_micros))} · ${kTok((st.usage.input || 0) + (st.usage.cache_read || 0) + (st.usage.output || 0))} tokens</div>`));
  const links = el(`<div class="links"></div>`);
  links.appendChild(el(`<a href="/api/studio/${encodeURIComponent(m.studio)}/spec" target="_blank">SPEC.md</a>`));
  for (const f of ["rtl", "golden.py", "design.json"]) {
    if (f === "rtl") continue;
    links.appendChild(el(`<a href="/api/studio/${encodeURIComponent(m.studio)}/file/${encodeURIComponent(f)}" target="_blank">${esc(f)}</a>`));
  }
  if (st.design) links.appendChild(el(`<a href="/work?thread=${encodeURIComponent(tid)}" target="_blank">${T.worktable}</a>`));
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
  if (m.text) li.appendChild(el(`<div class="prose ${m.kind === "run" ? "dim" : ""}">${m.source === "worktable" ? `<span class="tag">${zh ? "工作台" : "worktable"}</span> ` : ""}${m.resumed ? `<span class="tag" title="${T.resumedTitle}">${T.resumed}</span> ` : ""}${md(m.text)}</div>`));
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
  if (m.kind === "studio" && m.studio) {
    li.appendChild(studioCard(m));
    return li;
  }
  if (m.kind === "tournament" && m.jobs?.length) {
    li.appendChild(tournamentCard(m));
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
      if (j.done && j.summary?.sweep) li.appendChild(sweepCard(m.job, j, spec));
      if (j.done) li.appendChild(resultCard(m.job, j, done, spec));
    }
  }
  return li;
}

// a tournament: the same task as several runs, side by side; the leader by the first objective is marked
function tournamentCard(m) {
  const rows = m.jobs.map((jid) => ({ jid, j: jobs[jid], meta: thread.jobs?.[jid] }));
  const label = (r) => (r.meta?.spec || r.j?.spec)?.tournament?.label || r.jid;
  const live = rows.some((r) => ["queued", "running"].includes(r.j?.status || r.meta?.status));
  const obj = ((rows[0].meta?.spec || rows[0].j?.spec)?.constraints?.objective || ["maximize_throughput"])[0];
  const bestOf = (r) => (r.j?.summary || r.meta?.summary)?.best;
  const key = (b) => ({ maximize_throughput: [-(b.throughput || 0), -(b.metrics.fmax_mhz || 0)], maximize_fmax: [-(b.metrics.fmax_mhz || 0)], minimize_lut: [b.metrics.lut || 0], minimize_area: [b.metrics.area_um2 || b.metrics.lut || 0], minimize_dsp: [b.metrics.dsp || 0] })[obj] || [-(b.metrics.fmax_mhz || 0)];
  const feas = rows.filter((r) => bestOf(r));
  const leader = feas.length ? feas.reduce((a, r) => { const ka = key(bestOf(a)), kr = key(bestOf(r)); for (let i = 0; i < ka.length; i++) { if (kr[i] < ka[i]) return r; if (kr[i] > ka[i]) return a; } return a; }) : null;
  // a lead that is only an Fmax difference inside the P&R seed noise (6%) with the same throughput / resources is a tie
  const tie = leader && !live && feas.length > 1 && feas.every((r) => { const a = bestOf(leader), b = bestOf(r); return (b.throughput || 0) === (a.throughput || 0) && b.metrics.dsp === a.metrics.dsp && Math.abs((b.metrics.lut || 0) - (a.metrics.lut || 0)) <= 0.05 * Math.max(a.metrics.lut || 1, 1) && Math.abs((b.metrics.fmax_mhz || 0) - (a.metrics.fmax_mhz || 0)) <= 0.06 * Math.max(a.metrics.fmax_mhz || 1, 1); });
  const d = card(`${m.tournament}:tour`, T.tournament, `${rows.length} ${T.variants} · ${live ? `<span class="live">${T.tourLive}</span>` : T.tourDone}${leader ? (tie ? ` · ${T.tie}` : ` · ${T.leader}: ${esc(label(leader))}`) : ""}`);
  const inner = d.querySelector(".inner");
  const tbl = el(`<table class="tour"></table>`);
  for (const r of rows) {
    const st = r.j?.status || r.meta?.status || "queued", b = bestOf(r), m2 = b?.metrics;
    const done = r.j ? r.j.events.filter((e) => e.kind === "result").length : 0;
    const phase = ["queued", "running"].includes(st) ? `<span class="live">${st === "queued" ? T.queued : t("iter", done + 1)}</span>` : st === "done" ? "✓" : `<span class="x">${esc(st)}</span>`;
    const nums = b ? `<b>${fmt(m2.fmax_mhz)} MHz</b> · ${m2.area_um2 != null ? `${m2.lut} cells` : `${m2.lut} LUT · ${m2.dsp} DSP`} · ${b.throughput}` : `<span class="muted">${st === "failed" ? T.noResult : "–"}</span>`;
    const tr = el(`<tr class="${r === leader && !tie ? "lead" : ""}"><td class="lb">${r === leader && !tie ? "★ " : ""}${esc(label(r))}<div class="muted small">${esc((r.meta?.spec || r.j?.spec)?.planner || "")}</div></td><td class="ph">${phase}</td><td class="nm">${nums}</td><td class="op"><a href="/work?thread=${encodeURIComponent(tid)}#${esc(r.jid)}" target="_blank" title="${T.tourOpen}">↗</a></td></tr>`);
    tbl.appendChild(tr);
  }
  inner.appendChild(tbl);
  if (live) {
    for (const r of rows) {
      if (!r.j || !["queued", "running"].includes(r.j.status)) continue;
      const trs = turns(r.j.events), dn = trs.filter((x) => !x.pending), lr = trs.find((x) => x.pending && !x.slot) || trs.find((x) => x.pending);
      const left = eta(r.j, trs, dn);
      inner.appendChild(el(`<div class="tprog"><span class="muted">${esc(label(r))} · ${t("iter", (lr?.n ?? new Set(dn.map((x) => x.n)).size + 1))} / ${r.j.max_iter}</span>${lr?.ir ? stageChips(lr, (r.j.spec || {}).backend) : `<span class="muted">${T.planning}</span>`}<span class="grow"></span>${left ? `<span class="muted">${left} ${T.left}</span>` : ""}</div>`));
    }
  }
  if (rows.length >= 2 && !live) inner.appendChild(el(`<div class="links"><a href="/work?thread=${encodeURIComponent(tid)}#compare/${esc(rows[0].jid)}/${esc(rows[1].jid)}" target="_blank">${T.cmpVariants}</a></div>`));
  return d;
}

// ---------------------------------------------------------------- cards
function card(key, label, sub, cls = "") {
  if (openWanted && (openWanted.has("all") || openWanted.has(key.split(":")[1]))) open.add(key);
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
  // a designer rewrite requested by the planner (authored designs): shown as a note on the round it precedes
  events = events.map((e) => e.kind === "revising" ? { ...e, kind: "note", message: `${T.studioRevise}: ${e.instruction}` } : e.kind === "revised" ? { ...e, kind: "note", message: `${T.studioRevised} #${e.rev}${e.note ? " — " + e.note : ""}` } : e);
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
      case "note": if (e.iteration) { const r = get({ iteration: e.iteration }); r.note = r.note ? r.note + "\n" + e.message : e.message; } break;
    }
  }
  const out = [...rows.values()].sort((a, b) => a.n - b.n || a.slot - b.slot);
  if (pendingSteer.length && out.length) out.at(-1).userAfter = pendingSteer;
  return out;
}

const seedSpan = (m) => (m.fmax_seeds && m.fmax_seeds.length > 1) ? ` <span class="muted" title="${t("seeds", m.fmax_seeds.length)}">${fmt(Math.min(...m.fmax_seeds), 0)}–${fmt(Math.max(...m.fmax_seeds), 0)}</span>` : "";

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

// phrases the loop itself writes (not the planner): shown in the UI language
const LOOP_ZH = { "initial design from spec": "规格的起点设计", "stopped by user": "用户已停止", "stopping with best-so-far": "规划器不可用，以目前最佳设计结束", "no legal candidate": "没有合法候选", "measure every point": "逐点实测", "REJECTED (all candidates invalid or duplicates) -- propose different IRs": "全部候选无效或重复，要求重新提出", "stop": "停止" };
const loopText = (x) => (zh && typeof x === "string") ? (LOOP_ZH[x] || x.replace(/^planner unavailable: /, "规划器不可用：").replace(/^design-space sweep over (.*): (\d+) points$/, "设计空间扫描 $1：$2 个点").replace(/^sweep point (.*) rejected: /, "扫描点 $1 被拒：").replace(/^sweep point (.*) invalid: /, "扫描点 $1 无效：").replace(/^sibling candidate: /, "").replace(/^sibling candidate #(\d+) won this round; continuing from it$/, "并行候选 #$1 赢得本轮，从它继续")) : x;
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
    const what = loopText(r.slot ? (r.action ?? "") : r.n === 1 ? (j.parent ? T.fork : T.startFrom) : (r.plan?.action ?? r.action ?? ""));
    if (r.pending && !r.ir && !live) {
      // a planning round that measured nothing (every candidate invalid or a duplicate) on a finished run
      list.appendChild(el(`<div class="iter skip"><span class="n">${r.n}</span><div><div class="what muted">${esc(what || T.planning)}</div>${r.plan?.diagnosis ? `<div class="why clamp" title="${esc(T.clampTitle)}">${esc(r.plan.diagnosis)}</div>` : ""}</div></div>`));
      continue;
    }
    const row = el(`<div class="iter ${r.slot ? "sib" : ""}"><span class="n ${r.pending ? "live" : ""}">${r.slot ? `${r.n}.${r.slot}` : r.n}</span><div>
      ${r.user.map((u) => `<div class="user">${esc(u.message)}</div>`).join("")}
      <div class="what">${esc(what)}</div>
      ${r.ir ? chips(r.ir, prev, spec.locked) : ""}
      ${r.pending ? `<div class="nums"><span class="live" style="color:var(--accent)">${esc(phase(r, j))}</span></div>` : `<div class="nums"><span class="${ok ? "v" : "x"}">${ok ? "✓" : "✕"}</span> <b>${fmt(m.fmax_mhz)} MHz</b>${seedSpan(m)} · ${m.area_um2 != null ? `${m.lut} cells · ${fmt(m.area_um2 / 1000, 1)}k µm²` : `${m.lut} LUT · ${m.dsp} DSP`} · ${r.throughput} ${esc(unit)} · ${r.eda_seconds}${T.secs}${ok ? "" : ` · <span class="x">${esc(r.violations.join(", "))}</span>`}</div>`}
      ${r.ir ? stageChips(r, spec.backend) : ""}
      ${r.n > 1 && !r.slot && (r.plan?.diagnosis ?? r.diagnosis) ? `<div class="why clamp" title="${esc(T.clampTitle)}">${esc(loopText(r.plan?.diagnosis ?? r.diagnosis))}</div>` : ""}
      ${(r.note || "").split("\n").filter(Boolean).map((line) => `<div class="why">${esc(loopText(line))}</div>`).join("")}
      ${!r.pending && m.critical_path?.length > 1 ? cpBar(m.critical_path, spec) : ""}
      ${r.next?.stop ? `<div class="why">■ ${esc(loopText(r.next.stop_reason || r.next.action))}</div>` : ""}
      ${(r.userAfter || []).map((u) => `<div class="user">${esc(u.message)}</div>`).join("")}
    </div></div>`);
    row.querySelectorAll(".why.clamp").forEach((w) => { w.onclick = () => w.classList.toggle("clamp"); });
    list.appendChild(row);
    if (!r.pending) prev = r.ir;
  }
  if (!rows.length) list.appendChild(el(`<div class="muted">${j.status === "queued" ? T.queued : T.planning}…</div>`));
  inner.appendChild(list);
  if (j.error) inner.appendChild(el(`<pre class="code">${esc(j.error)}</pre>`));
  return d;
}

// a design-space sweep: every measured point as a table, Pareto-optimal rows marked (the objective's first two terms)
function sweepCard(jid, j, spec) {
  const sw = j.summary.sweep, pts = sw.points || [], unit = j.summary?.throughput_unit || "", asic = pts.some((p) => p.area_um2 != null);
  const obj = spec.constraints?.objective || ["maximize_throughput", "minimize_lut"];
  const val = (p, o) => ({ maximize_throughput: p.throughput, maximize_fmax: p.fmax_mhz, minimize_lut: -p.lut, minimize_dsp: -p.dsp, minimize_area: -(p.area_um2 ?? p.lut) })[o] ?? 0;
  const o1 = obj[0], o2 = obj[1] || "minimize_lut";
  const pareto = pts.map((p) => p.feasible && !pts.some((q) => q.feasible && q !== p && val(q, o1) >= val(p, o1) && val(q, o2) >= val(p, o2) && (val(q, o1) > val(p, o1) || val(q, o2) > val(p, o2))));
  const d = card(`${jid}:sweep`, T.sweep, `${pts.length} ${T.points} · ${esc(sw.knobs.join(" × "))} · ${pareto.filter(Boolean).length} ${T.paretoPts}`);
  const inner = d.querySelector(".inner");
  const rows = pts.map((p, i) => `<tr class="${p.feasible ? "" : "infeasible"}${pareto[i] ? " pareto" : ""}"><td>${sw.knobs.map((k) => `<span class="chip"><span class="k">${esc(k)}=</span>${esc(JSON.stringify(p.point[k]))}</span>`).join(" ")}</td><td>${fmt(p.fmax_mhz)}</td><td>${asic ? `${p.lut} · ${fmt((p.area_um2 || 0) / 1000, 1)}k` : `${p.lut} · ${p.dsp}`}</td><td>${p.throughput}</td><td>${p.feasible ? "✓" : "✕"}${pareto[i] ? " ★" : ""}</td></tr>`).join("");
  // scatter: x = the resource of the second objective (LUT / area), y = Fmax; Pareto points filled, infeasible hollow
  const xs = pts.map((p) => asic ? (p.area_um2 || p.lut) : p.lut), ys = pts.map((p) => p.fmax_mhz);
  const W = 420, H = 180, pad = 34, x0 = Math.min(...xs) * 0.95, x1 = Math.max(...xs) * 1.05 || 1, y0 = 0, y1 = Math.max(...ys, spec.constraints?.fmax_mhz || 0) * 1.1 || 1;
  const sx = (v) => pad + (v - x0) / (x1 - x0) * (W - pad - 10), sy = (v) => H - pad + 8 - (v - y0) / (y1 - y0) * (H - pad);
  const tgt = spec.constraints?.fmax_mhz;
  const dots = pts.map((p, i) => `<circle cx="${sx(xs[i]).toFixed(1)}" cy="${sy(ys[i]).toFixed(1)}" r="4.5" class="${pareto[i] ? "pareto" : p.feasible ? "ok" : "bad"}"><title>${esc(sw.knobs.map((k) => `${k}=${JSON.stringify(p.point[k])}`).join(" "))} · ${fmt(p.fmax_mhz)} MHz · ${xs[i]}</title></circle>`).join("");
  const front = pts.map((p, i) => [xs[i], ys[i], pareto[i]]).filter((t) => t[2]).sort((a, b) => a[0] - b[0]).map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ");
  inner.appendChild(el(`<svg class="scatter" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
    <line x1="${pad}" y1="${H - pad + 8}" x2="${W - 10}" y2="${H - pad + 8}" class="axis"/><line x1="${pad}" y1="8" x2="${pad}" y2="${H - pad + 8}" class="axis"/>
    ${tgt ? `<line x1="${pad}" y1="${sy(tgt).toFixed(1)}" x2="${W - 10}" y2="${sy(tgt).toFixed(1)}" class="target"/><text x="${W - 12}" y="${(sy(tgt) - 4).toFixed(1)}" class="lbl" text-anchor="end">${tgt} MHz</text>` : ""}
    ${front ? `<polyline points="${front}" class="front"/>` : ""}${dots}
    <text x="${W - 10}" y="${H - 4}" class="lbl" text-anchor="end">${asic ? "µm²" : "LUT"}</text><text x="${pad - 4}" y="14" class="lbl" text-anchor="end">MHz</text>
  </svg>`));
  inner.appendChild(el(`<table class="sweep"><thead><tr><th>${T.point}</th><th>MHz</th><th>${asic ? "cells · µm²" : "LUT · DSP"}</th><th>${esc(unit)}</th><th></th></tr></thead><tbody>${rows}</tbody></table>`));
  inner.appendChild(el(`<div class="muted small">★ ${T.paretoNote.replace("{a}", o1).replace("{b}", o2)}</div>`));
  return d;
}

// the trust strip: one glance at how much verification stands behind the number (every pill is a fact the server
// already holds; nothing here starts a check -- the buttons below do that, then refresh the strip)
function trustStrip(jid, m, keys) {
  const tc = trustCache.get(jid) || {};
  const pill = (cls, label, title) => `<span class="tp ${cls}" title="${esc(title || "")}"><i></i>${label}</span>`;
  const out = [];
  const ref = tc.reference;
  if (ref && ref.kind && ref.kind !== "generator-golden") {
    const lbl = ref.kind === "independent" ? `${T.refIndep}${ref.samples ? ` · ${ref.samples}` : ""}` : ref.kind === "generator" ? T.refGen : T.refSelf;
    out.push(pill(ref.kind === "self-check" ? "na" : ref.agree === false ? "bad" : "ok", lbl + (ref.revisions > 1 ? ` · rev ${ref.rev}` : ""), T.refTitle));
  }
  out.push(pill(m.sim_passed ? "ok" : "bad", T.tBehav, ref && ref.kind && ref.kind !== "generator-golden" ? "iverilog, the generic stream bench on the reference's vectors" : "cocotb + iverilog vs the Python golden model"));
  const net = keys.filter((k) => k !== "behavioral").map((k) => (m.sim_stages || {})[k]?.passed);
  if (net.length) out.push(pill(net.every((v) => v === true) ? "ok" : net.some((v) => v === false) ? "bad" : "na", `${T.netlist} ${net.filter((v) => v === true).length}/${net.length}`, keys.filter((k) => k !== "behavioral").map((k) => T.stages[STAGE_KEYS.indexOf(k)]).join(" · ")));
  const seeds = m.fmax_seeds || [];
  if (seeds.length > 1) { const lo = Math.min(...seeds), hi = Math.max(...seeds); out.push(pill("ok", `${t("seeds", seeds.length)} · ${t("spread", fmt(100 * (hi - lo) / Math.max(hi, 1), 0))}`, seeds.map((x) => fmt(x, 0) + " MHz").join(" / "))); }
  const f = tc.formal, mu = tc.mutation;
  out.push(f ? pill(f.equivalence === true && f.properties ? "ok" : "bad", `${T.formal} ${T.equiv} ${f.equivalence === true ? "✓" : f.equivalence === false ? "✗" : "?"} · ${T.props} ${f.properties ? "✓" : "✗"}`, `depth ${f.depth || "?"}`)
             : pill(tc.running?.formal ? "run" : "na", `${T.formal}: ${tc.running?.formal ? T.formalRunning : T.notRun}`, T.formalTitle));
  out.push(mu ? pill(mu.score >= 0.8 ? "ok" : mu.score >= 0.5 ? "warn" : "bad", `${T.mutation} ${mu.killed}/${mu.total} (${Math.round((mu.score || 0) * 100)}%)`, (mu.survived || []).length ? `${T.survived}: ${mu.survived.join(", ")}` : T.mutationTitle)
              : pill(tc.running?.mutation ? "run" : "na", `${T.mutation}: ${tc.running?.mutation ? T.mutationRunning : T.notRun}`, T.mutationTitle));
  return `<div class="trust" id="trust-${esc(jid)}" title="${T.trustTitle}"><span class="lbl">${T.trust}</span>${out.join("")}</div>`;
}
function refreshTrust(jid) {
  return api(`/jobs/${jid}/trust`).then((r) => { if (!r.error) trustCache.set(jid, r); render(); }).catch(() => {});
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
    // the hero: the number that decides the outcome, big; its seeds and the target under it; the rest as labelled stats
    const target = spec.constraints?.fmax_mhz;
    const seeds = m.fmax_seeds && m.fmax_seeds.length > 1 ? `${T.seedsLabel} ${m.fmax_seeds.map((x) => fmt(x, 0)).join(" / ")} · ${T.median}` : "";
    const stat = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
    inner.appendChild(el(`<div class="hero"><div class="fmax"><b>${fmt(m.fmax_mhz)} MHz</b><span>${esc([seeds, target ? `${T.target} ${target}` : ""].filter(Boolean).join(" · "))}</span></div><div class="stats">${m.area_um2 != null ? stat(m.lut, "cells") + stat(`${fmt(m.area_um2 / 1000, 1)}k`, "µm²") : stat(m.lut, "LUT") + stat(m.dsp, "DSP")}${stat(m.ff ?? "–", "FF")}${m.bram ? stat(m.bram, "BRAM") : ""}${m.power_mw != null ? stat(m.power_mw, "mW") : ""}${stat(row.throughput, esc(unit))}${row.latency_cycles != null ? stat(row.latency_cycles, T.latency) : ""}</div></div>`));
    inner.appendChild(el(chips({ ...(spec.locked || {}), ...row.ir }, null, spec.locked)));
    if (best) { inner.appendChild(el(trustStrip(jid, m, keys))); if (!trustCache.has(jid) && j.status !== "running") { trustCache.set(jid, {}); refreshTrust(jid); } }
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
        fm.textContent = r.status === "running" ? T.formalRunning : `${T.formal}: ${T.equiv} ${eq === true ? "✓" : eq === false ? "✗" : "?"} · ${T.props} ${pr ? "✓" : "✗"}`;
        fm.title = (r.equivalence?.log || "") + "\n" + (r.properties?.log || "") + (r.error ? "\n" + r.error : "");
        if (r.status !== "running") await refreshTrust(jid);
      } catch (e) { fm.textContent = e.message.slice(0, 60); }
      fm.disabled = false;
    };
    // mutation score: how many injected RTL bugs the behavioral bench catches (the strength of the verification)
    const mu = el(`<button title="${T.mutationTitle}">${T.mutation}</button>`);
    mu.onclick = async () => {
      mu.disabled = true; mu.textContent = T.mutationRunning;
      try {
        let r;
        for (let i = 0; i < 120; i++) { r = await api(`/jobs/${jid}/mutation`); if (r.status !== "running") break; await new Promise((ok) => setTimeout(ok, 5000)); }
        mu.textContent = r.status === "running" ? T.mutationRunning : r.error ? r.error.slice(0, 60) : `${T.mutation}: ${r.killed}/${r.total} (${Math.round((r.score || 0) * 100)}%)`;
        mu.title = (r.survived || []).length ? `${T.survived}: ${r.survived.join(", ")}` : T.mutationTitle;
        if (r.status !== "running") await refreshTrust(jid);
      } catch (e) { mu.textContent = e.message.slice(0, 60); }
      mu.disabled = false;
    };
    links.append(view, dl, pkg, fm, mu);
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

// theme: dark / light / auto, remembered in localStorage (the <html> attribute is set before paint by an inline script)
(() => {
  const order = ["dark", "light", "auto"], btn = document.getElementById("theme");
  if (!btn) return;
  const label = () => { const t = document.documentElement.dataset.theme || "dark"; btn.textContent = t === "dark" ? "◐" : t === "light" ? "☀" : "◑"; btn.title = `theme: ${t}`; };
  btn.onclick = () => { const t = document.documentElement.dataset.theme || "dark"; const n = order[(order.indexOf(t) + 1) % order.length]; document.documentElement.dataset.theme = n; localStorage.setItem("sa.theme", n); label(); };
  label();
})();
