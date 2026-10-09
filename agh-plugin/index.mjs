// silicon-agent as an Agnes Harness (AGH) plugin.
//
// AGH is the agent runtime: it runs the model loop on the configured provider (DeepSeek), records every
// tool call with its inputs and structured output, handles approvals, sessions and compaction. This
// plugin gives that loop silicon-agent's capabilities as tools; the heavy lifting (interpretation,
// planner iterations, simulation, synthesis, place-and-route) stays in the silicon-agent job server,
// reached over HTTP with the bearer token from the environment named in the plugin config.
//
// No imports: the installed snapshot needs no dependency. Tool parameters are TypeBox-tagged JSON Schema.
const Kind = Symbol.for('TypeBox.Kind')
const str = (extra = {}) => ({ [Kind]: 'String', type: 'string', ...extra })
const int = (extra = {}) => ({ [Kind]: 'Integer', type: 'integer', ...extra })
const num = (extra = {}) => ({ [Kind]: 'Number', type: 'number', ...extra })
const bool = () => ({ [Kind]: 'Boolean', type: 'boolean' })
const obj = (properties, required = []) => ({ [Kind]: 'Object', type: 'object', properties, required, additionalProperties: false })
const any = () => ({ [Kind]: 'Any' })

const readOnly = {
  isReadOnly: true, isDestructive: false, isConcurrencySafe: true, isOpenWorld: true,
  replay: 'safe', costHint: undefined, deferLoading: false, requiresApproval: 'never',
}
// Starting a run spends minutes of EDA time on the server; it is not destructive, but a replay must
// not start it twice.
const starts = {
  isReadOnly: false, isDestructive: false, isConcurrencySafe: false, isOpenWorld: true,
  replay: 'never', costHint: { wallMs: 600000 }, deferLoading: false, requiresApproval: 'never',
}

// AGH validates the structured value as strict JSON (no `undefined` members, no NaN), so the result is
// round-tripped through JSON before it leaves the tool.
const text = (value) => {
  const structured = JSON.parse(JSON.stringify(value === undefined ? null : value))
  return { content: [{ type: 'text', text: JSON.stringify(structured) }], structured }
}

export const siliconAgent = {
  inject: ['extension', 'skills'],
  apply(ctx, config) {
    const agnes = ctx.extension()
    const cfg = { server: 'http://127.0.0.1:8765', tokenEnv: 'SA_TOKEN', tokenFile: '', ...(config ?? {}) }
    // SA_SERVER in the worker's environment wins over the packaged default (inside compose the job
    // server is another container)
    const server = (process.env.SA_SERVER || cfg.server).replace(/\/$/, '')
    // The worker process does not inherit the operator's shell, so the bearer token is read from a file
    // (the deployment's own secret, 0600) and only falls back to the environment variable.
    let tokenCache = ''
    const token = async () => {
      if (tokenCache) return tokenCache
      const file = process.env.SA_TOKEN_FILE || cfg.tokenFile
      if (file) {
        try {
          const { readFile } = await import('node:fs/promises')
          tokenCache = (await readFile(file, 'utf8')).trim()
        } catch {}
      }
      return tokenCache || process.env[cfg.tokenEnv] || ''
    }
    const headers = async () => {
      const t = await token()
      return { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) }
    }
    // A job-server redeploy leaves the worker's keep-alive sockets pointing at a container that is gone; the first
    // request after that fails at the transport ("fetch failed"). Reads and the side-effect-free POSTs are retried
    // once on a fresh connection; POST /jobs is not (a retry could start the run twice).
    const idempotent = (method, path) => method === 'GET' || path === '/interpret' || path === '/estimate'
    const api = async (method, path, body, signal) => {
      const req = async () => fetch(`${server}/api${path}`, { method, headers: await headers(), body: body === undefined ? undefined : JSON.stringify(body), signal })
      let r
      try { r = await req() } catch (e) {
        if (!idempotent(method, path) || signal?.aborted) throw e
        await new Promise((ok) => setTimeout(ok, 500))
        r = await req()
      }
      const t = await r.text()
      let parsed
      try { parsed = JSON.parse(t) } catch { parsed = { raw: t.slice(0, 4000) } }
      if (!r.ok && r.status !== 202) throw new Error(`silicon-agent ${method} ${path}: HTTP ${r.status} ${t.slice(0, 300)}`)
      return parsed
    }
    const fail = (error) => text({ error: String(error?.message ?? error) })

    // The agent-facing summary of a job: metrics, violations and the planner's reasoning per iteration,
    // without the raw RTL/log bulk (those stay reachable through sa_job_file).
    const summarize = (j) => {
      const results = (j.events ?? []).filter((e) => e.kind === 'result')
      const plans = (j.events ?? []).filter((e) => e.kind === 'plan')
      const best = j.summary?.best ?? null
      return {
        status: j.status,
        design: j.spec?.design, backend: j.spec?.backend, task: j.spec?.task,
        constraints: j.spec?.constraints,
        iterations: results.map((e) => ({
          iteration: e.iteration, slot: e.slot ?? 0, ir: e.ir, action: e.action,
          fmax_mhz: e.metrics?.fmax_mhz, lut: e.metrics?.lut, dsp: e.metrics?.dsp, bram: e.metrics?.bram, ff: e.metrics?.ff,
          fmax_seeds: e.metrics?.fmax_seeds, power_mw: e.metrics?.power_mw, throughput: e.throughput, latency_cycles: e.latency_cycles,
          sim_passed: e.metrics?.sim_passed, sim_stages: Object.fromEntries(Object.entries(e.metrics?.sim_stages ?? {}).map(([k, v]) => [k, v?.passed])),
          violations: e.violations, critical_path: (e.metrics?.critical_path ?? []).slice(0, 6), eda_seconds: e.eda_seconds,
        })),
        plans: plans.map((p) => ({ iteration: p.iteration, diagnosis: p.diagnosis, action: p.action, stop: p.stop ?? false, stop_reason: p.stop_reason })),
        best: best ? { iteration: best.iteration, ir: best.ir, metrics: { fmax_mhz: best.metrics?.fmax_mhz, lut: best.metrics?.lut, dsp: best.metrics?.dsp, bram: best.metrics?.bram }, throughput: best.throughput } : null,
        stop_reason: j.summary?.stop_reason ?? null,
        ...(j.summary?.sweep ? { sweep: j.summary.sweep } : {}),
        error: j.error ? String(j.error).slice(0, 600) : undefined,
      }
    }

    agnes.registerTool({
      name: 'sa_schema',
      description: 'List the design generators (matmul, fir, conv2d, cordic, fft, fir_chain, graph), their IR knobs with legal ranges, the FPGA devices available on this server, and measured device facts. Call this before interpreting a hardware request.',
      parameters: obj({}),
      meta: readOnly,
      async execute() {
        try { return text(await api('GET', '/schema')) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_interpret',
      description: 'Turn a natural-language hardware request into a complete silicon-agent spec (design, device, locked task fields, constraints, objectives, initial IR) using the server\'s interpreter. Returns the spec, assumptions, questions and alternative architectures. Does not start a run.',
      parameters: obj({ request: str({ maxLength: 8000 }), thread: str({ maxLength: 64 }) }, ['request']),
      meta: readOnly,
      async execute({ request, thread }, tctx) {
        try { return text(await api('POST', '/interpret', { text: request, thread }, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_estimate',
      description: 'Closed-form + calibrated estimate (Fmax, LUT, DSP, throughput) of one IR on one device without running EDA; use it to compare candidate architectures before spending minutes on a run.',
      parameters: obj({ design: str(), backend: str(), ir: any() }, ['design', 'backend', 'ir']),
      meta: readOnly,
      async execute({ design, backend, ir }, tctx) {
        try { return text(await api('POST', '/estimate', { design, backend, ir }, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_run',
      description: 'Start an autonomous optimization run from a spec: generate RTL, simulate, synthesize and place-and-route on the real device, diagnose and iterate until the constraints hold (or the planner proves it cannot). Returns the job id immediately; poll with sa_job. Bind it to a conversation with `thread` so the chat shows its cards.',
      parameters: obj({ spec: any(), max_iter: int({ minimum: 1, maximum: 20 }), thread: str({ maxLength: 64 }), parent: any() }, ['spec']),
      meta: starts,
      async execute({ spec, max_iter, thread, parent }, tctx) {
        try { return text(await api('POST', '/jobs', { spec, max_iter: max_iter ?? 8, thread, parent }, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_sweep',
      description: 'Design-space sweep: measure every point of the product of the given knob values (e.g. {"fold":[1,2,4],"sum_tree":[false,true]}) on the real device, no planner -- the result is a table with the Pareto-optimal points marked (the chat shows it as a card). Same spec shape as sa_run; the sweep knobs override initial_ir. Returns the job id; follow with sa_job (its iterations are the points).',
      parameters: obj({ spec: any(), sweep: any(), thread: str({ maxLength: 64 }) }, ['spec', 'sweep']),
      meta: starts,
      async execute({ spec, sweep, thread }, tctx) {
        try { return text(await api('POST', '/jobs', { spec: { ...spec, sweep }, max_iter: 1, thread }, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_job',
      description: 'Status and trajectory of a run: every iteration\'s IR, measured Fmax/LUT/DSP/BRAM, simulation ladder, violations, critical path, the planner\'s diagnosis and action, and the best feasible design. With wait_seconds > 0 it blocks until the run finishes or the wait elapses (at most 90 s per call: a run that is still going is returned with status "running" -- call again).',
      parameters: obj({ job: str({ maxLength: 64 }), wait_seconds: int({ minimum: 0, maximum: 90 }) }, ['job']),
      meta: readOnly,
      async execute({ job, wait_seconds }, tctx) {
        // the harness cuts a tool off at tools.timeout_ms (120 s by default), so one call never waits longer than 90 s
        const deadline = Date.now() + 1000 * Math.min(wait_seconds ?? 0, 90)
        try {
          for (;;) {
            const j = await api('GET', `/jobs/${encodeURIComponent(job)}`, undefined, tctx?.signal)
            const done = !['queued', 'running'].includes(j.status)
            if (done || Date.now() >= deadline) {
              const out = summarize(j)
              out.note = done
                ? `finished: status ${j.status}; this is the complete trajectory, do not poll again. ${out.best ? 'best feasible design is in `best`.' : 'no feasible design was found (best is null).'}`
                : 'still running: call sa_job again (wait_seconds up to 90) until status is done/failed'
              return text(out)
            }
            await new Promise((r) => setTimeout(r, Math.min(5000, Math.max(500, deadline - Date.now()))))
          }
        } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_steer',
      description: 'Give the running planner an instruction for its next proposal (e.g. "try the symmetric form", "do not reduce the array"). Only meaningful while the job is queued or running.',
      parameters: obj({ job: str({ maxLength: 64 }), message: str({ maxLength: 4000 }) }, ['job', 'message']),
      meta: { ...starts, costHint: undefined },
      async execute({ job, message }, tctx) {
        try { return text(await api('POST', `/jobs/${encodeURIComponent(job)}/steer`, { message }, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_stop',
      description: 'Stop a running job after its current iteration; the best design so far is kept.',
      parameters: obj({ job: str({ maxLength: 64 }) }, ['job']),
      meta: { ...starts, costHint: undefined },
      async execute({ job }, tctx) {
        try { return text(await api('POST', `/jobs/${encodeURIComponent(job)}/stop`, {}, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_jobs',
      description: 'List recent runs (newest first), optionally only those bound to one conversation thread.',
      parameters: obj({ thread: str({ maxLength: 64 }), limit: int({ minimum: 1, maximum: 100 }) }),
      meta: readOnly,
      async execute({ thread, limit }, tctx) {
        try {
          const js = await api('GET', `/jobs${thread ? `?thread=${encodeURIComponent(thread)}` : ''}`, undefined, tctx?.signal)
          // the list carries each run's best point directly (not under summary)
          return text(js.slice(0, limit ?? 20).map((j) => ({ id: j.id, status: j.status, design: j.design, backend: j.backend, task: j.task ?? j.spec?.task, iterations: j.iterations, thread: j.thread ?? null, parent: j.parent ?? null, best: j.best ? { iteration: j.best.iteration, fmax_mhz: j.best.metrics?.fmax_mhz, lut: j.best.metrics?.lut, dsp: j.best.metrics?.dsp } : null })))
        } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_pareto',
      description: 'Every measured design point of one generator on one device, its Pareto front, and the best measured point under optional constraints — answer "what is achievable" from data instead of guessing.',
      parameters: obj({ design: str(), backend: str(), fmax_mhz: num(), max_dsp: int(), max_lut: int() }, ['design', 'backend']),
      meta: readOnly,
      async execute({ design, backend, fmax_mhz, max_dsp, max_lut }, tctx) {
        const q = new URLSearchParams({ design, backend, ...(fmax_mhz ? { fmax_mhz: String(fmax_mhz) } : {}), ...(max_dsp ? { max_dsp: String(max_dsp) } : {}), ...(max_lut ? { max_lut: String(max_lut) } : {}) })
        try { return text(await api('GET', `/pareto?${q}`, undefined, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_job_file',
      description: 'Read an artifact of a run. Paths: "rtl" = the best design\'s generated SystemVerilog; "summary.json"; "iter_NN.json" (one iteration: IR, metrics, critical path, sim logs); "llm_NN.json" (the planner\'s reply for iteration NN); "events.jsonl"; "formal/report.json". Large files are truncated to max_bytes (default 32768).',
      parameters: obj({ job: str({ maxLength: 64 }), path: str({ maxLength: 256 }), max_bytes: int({ minimum: 1024, maximum: 262144 }) }, ['job', 'path']),
      meta: readOnly,
      async execute({ job, path, max_bytes }, tctx) {
        try {
          const p = path.replace(/^\/+/, '')
          const url = p === 'rtl' || /^rtl\/[^/]+\.sv$/.test(p)
            ? `${server}/api/jobs/${encodeURIComponent(job)}/rtl`
            : `${server}/api/jobs/${encodeURIComponent(job)}/files/${p.split('/').map(encodeURIComponent).join('/')}`
          const r = await fetch(url, { headers: await headers(), signal: tctx?.signal })
          const t = await r.text()
          if (!r.ok) return fail(`HTTP ${r.status} for ${p}: valid paths are rtl, summary.json, iter_NN.json, llm_NN.json, events.jsonl, formal/report.json`)
          const cap = max_bytes ?? 32768
          return { content: [{ type: 'text', text: t.length > cap ? `${t.slice(0, cap)}\n...[truncated ${t.length - cap} bytes]` : t }], structured: { bytes: t.length, truncated: t.length > cap } }
        } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_formal',
      description: 'SymbiYosys bounded model check of a finished run: equivalence of the measured RTL against a fresh emit of its IR, plus stream-protocol properties. The server runs it in the background: the first call returns status "running"; call again after ~60 s until the report (equivalence.ok, properties.ok) is present.',
      parameters: obj({ job: str({ maxLength: 64 }), depth: int({ minimum: 4, maximum: 64 }) }, ['job']),
      meta: { ...readOnly, costHint: { wallMs: 60000 } },
      async execute({ job, depth }, tctx) {
        // one call waits up to 80 s for the background check so the model is not tempted to spin on it
        try {
          const deadline = Date.now() + 80_000
          for (;;) {
            const r = await api('GET', `/jobs/${encodeURIComponent(job)}/formal?depth=${depth ?? 16}`, undefined, tctx?.signal)
            if (r.status !== 'running' || Date.now() >= deadline) return text(r)
            await new Promise((ok) => setTimeout(ok, 5000))
          }
        } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_rtl_upload',
      description: 'Bring your own RTL: register the user\'s SystemVerilog as a design kind. Needs the files ({name: text}), the top module, a self-checking SystemVerilog bench (instantiates the top, drives clk/rst, prints "RESULT PASS" or "RESULT FAIL ..."), and the parameters the planner may tune ({NAME: {default, min, max | choices, hint}}); optional port_widths ({port: "[hi:lo]"} for ports whose width depends on a parameter), latency and throughput as expressions of the parameters, a description. The server elaborates and runs the bench once; a failing bundle is refused with the log. Returns the design id "rtl:<id>" to use in a spec (locked = {}, initial_ir = parameter values).',
      parameters: obj({ name: str({ maxLength: 120 }), top: str({ maxLength: 64 }), files: any(), tb: str({ maxLength: 400000 }), params: any(), port_widths: any(), latency: str({ maxLength: 200 }), throughput: str({ maxLength: 200 }), throughput_unit: str({ maxLength: 32 }), description: str({ maxLength: 2000 }), notes: str({ maxLength: 4000 }) }, ['top', 'files', 'tb', 'params']),
      meta: { ...starts, costHint: { wallMs: 120000 } },
      async execute(args, tctx) {
        try { return text(await api('POST', '/rtl', args, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_rtl_list',
      description: 'The RTL bundles uploaded so far (id, top, parameters, description): a spec can target any of them with design "rtl:<id>".',
      parameters: obj({}),
      meta: readOnly,
      async execute(_args, tctx) {
        try { return text(await api('GET', '/rtl', undefined, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_propose',
      description: 'Submit a change to silicon-agent\'s own generators (silicon_agent/*.py) as a unified diff. The job server applies it to a scratch copy and gates it: every preset still emits and elaborates, the bench cases\' behavioral simulations pass, the RTL of unchanged IRs stays formally equivalent to the current generator, the testbench\'s mutation score does not drop; then, if ab_spec is given, a measured A/B run on the device. Nothing is merged: the operator decides from the report. Returns the proposal id; poll with sa_proposal.',
      parameters: obj({ title: str({ maxLength: 160 }), rationale: str({ maxLength: 4000 }), diff: str({ maxLength: 400000 }), designs: any(), irs: any(), ab_spec: any(), ab_max_iter: int({ minimum: 1, maximum: 8 }), thread: str({ maxLength: 64 }) }, ['title', 'rationale', 'diff', 'designs']),
      meta: starts,
      async execute({ title, rationale, diff, designs, irs, ab_spec, ab_max_iter, thread }, tctx) {
        try { return text(await api('POST', '/proposals', { title, rationale, diff, designs, irs, ab_spec, ab_max_iter, thread }, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_check',
      description: 'Author inner loop: on this conversation\'s working copy (b/), emit one IR of a design, validate it, elaborate the RTL with iverilog and run the behavioral cocotb testbench; returns validate() errors, the RTL head, the elaboration log and the simulation verdict. Use it after every edit, before sa_propose. The toolchain lives on the job server, not in your shell.',
      parameters: obj({ thread: str({ maxLength: 64 }), design: str(), backend: str(), ir: any() }, ['thread', 'design', 'ir']),
      meta: { ...readOnly, costHint: { wallMs: 60000 } },
      async execute({ thread, design, backend, ir }, tctx) {
        try { return text(await api('POST', '/author/check', { thread, design, backend: backend ?? 'ecp5', ir }, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_proposal',
      description: 'Gate verdict of a proposal: per step (emit/elaborate, behavioral sims, formal equivalence, mutation score) with the failures, and the A/B run id when one was started. With wait_seconds > 0 it blocks (at most 90 s) until the gate is no longer running.',
      parameters: obj({ id: str({ maxLength: 64 }), wait_seconds: int({ minimum: 0, maximum: 90 }) }, ['id']),
      meta: readOnly,
      async execute({ id, wait_seconds }, tctx) {
        const deadline = Date.now() + 1000 * Math.min(wait_seconds ?? 0, 90)
        try {
          for (;;) {
            const r = await api('GET', `/proposals/${encodeURIComponent(id)}`, undefined, tctx?.signal)
            const { diff, ...rest } = r
            if (r.status !== 'gating' || Date.now() >= deadline) {
              rest.note = r.status === 'gating' ? 'still gating: call again (wait_seconds up to 90)' : `gate finished: ${r.status}. ${r.ab?.job ? `A/B run ${r.ab.job} started; follow it with sa_job.` : ''}`
              return text(rest)
            }
            await new Promise((ok) => setTimeout(ok, 5000))
          }
        } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_mutation',
      description: 'Mutation score of a finished run: 24 bugs are injected into the best design\'s RTL (operator swaps, flipped compares, stuck enables, off-by-one indices) and the behavioral testbench is run on each; killed/total is the verification strength, `survived` lists what the bench would miss. Runs in the background: call again until status is done.',
      parameters: obj({ job: str({ maxLength: 64 }) }, ['job']),
      meta: { ...readOnly, costHint: { wallMs: 60000 } },
      async execute({ job }, tctx) {
        try {
          const deadline = Date.now() + 80_000
          for (;;) {
            const r = await api('GET', `/jobs/${encodeURIComponent(job)}/mutation`, undefined, tctx?.signal)
            if (r.status !== 'running' || Date.now() >= deadline) return text(r)
            await new Promise((ok) => setTimeout(ok, 5000))
          }
        } catch (e) { return fail(e) }
      },
    })

    agnes.registerTool({
      name: 'sa_bench',
      description: 'Last nightly regression result of the benchmark suite (which cases converged, in how many rounds, and what regressed).',
      parameters: obj({}),
      meta: readOnly,
      async execute(_args, tctx) {
        try { return text(await api('GET', '/bench', undefined, tctx?.signal)) } catch (e) { return fail(e) }
      },
    })

    // The method: how to drive a hardware request end to end with these tools.
    const METHOD = [
      'You are the front of silicon-agent, an autonomous FPGA design loop. Use its tools instead of writing RTL yourself.',
      '1. Call sa_schema once to know the generators, devices and knobs. Then sa_interpret with the user\'s words to get a complete spec; show its assumptions. Numbers the user states are used exactly as given.',
      '2. If the request allows several architectures (different generator, device or fold), compare them with sa_estimate before running; the interpreter\'s `alternatives` are good candidates.',
      '3. Start with sa_run (pass the conversation `thread` when you have one), then follow it with sa_job (wait_seconds 90, repeated until the status is done or failed; a run takes 1-30 minutes). Report what the measurements say: what changed, measured Fmax/LUT/DSP, whether the constraints hold. Never invent numbers: everything comes from the tool results.',
      '4. When it finishes, explain why it stopped (stop_reason or the budget), the final design, and one caveat if there is one (thin timing margin, low throughput, noisy seeds). Offer the next step: fork with tighter constraints, sa_formal / sa_mutation (how strong the verification is), or the delivery package (the chat shows a download button on the result card; you cannot build or attach files yourself).',
      '5. While a run is in flight, a user instruction about the design is an sa_steer; a question is answered from sa_job data.',
      '5b. "Sweep", "compare all", "how does X scale with Y", "Pareto": sa_sweep with the knob values -- it measures every point instead of searching, and the card shows the Pareto set. Keep sweeps small (<= 12 points on an FPGA, <= 4 on sky130).',
      '6. A follow-up that only asks about results you already have (critical path, why it stopped, numbers) is answered from your own tool records or one sa_job call: do not start another run, do not re-estimate, do not re-read artifacts you have read. Start a new run only when the user asks for a change, a retry, or a different design.',
      '7. The user may bring their own RTL (they paste SystemVerilog, or describe a block that no generator covers but they have code for). Then: ask for (or write from what they pasted) a self-checking bench that prints RESULT PASS/FAIL, decide with them which parameters the planner may move, call sa_rtl_upload, and run the spec with design "rtl:<id>" (locked {}, initial_ir = the parameter defaults). The ladder (behavioral sim, synthesis, P&R, netlist sims, formal properties) is the same; the planner tunes only the exposed parameters. sa_schema does not list rtl designs; sa_rtl_list does.',
      '8. Devices: the FPGAs plus `sky130` (OpenROAD ASIC standard cells, 2-10 min per flow): no DSP/LUT there -- the numbers are cells, area in um2 (max_area_um2 is the budget, minimize_area the objective) and the STA critical path through fa_1/ha_1 adder cells.',
      'Keep the final answer short: the chat already shows the spec, every iteration and the result as cards next to your message, so write 3-8 sentences a colleague would say, not a report. No headings, no tables, no restating every iteration; name the numbers that decide the outcome and what you would do next. Do not narrate your tool calls ("I will call ...").',
      'Answer in the user\'s language.',
    ].join('\n')
    // The author method: how to change a generator and get it gated.
    const AUTHOR_METHOD = [
      'You are improving silicon-agent\'s own generators. Your working directory holds two copies of the silicon_agent/ package: a/ is the deployed code (never edit it), b/ is yours. Edit b/ (write/edit), test it there with shell (cd b && python3 ..., PYTHONPATH=b, iverilog -g2012), and submit with sa_propose the unified diff `diff -ruN a b` (paths a/silicon_agent/x.py b/silicon_agent/x.py). There is no git here.',
      '1. Read what is there first: the IR dataclass (fields, validate, digest), the emitter, the design\'s cocotb test under silicon_agent/tb/, its entry in schema.py and prompts.py, and screen.py\'s estimator. A new knob touches all of them: IR field with a default that keeps old IRs identical, emitter branch, validate rule, prompt line (when the critical path looks like X, this is the knob), schema field, estimator adjustment, and the digest must drop the field when it is off so cached results stay valid.',
      '2. Keep the knob-off RTL byte-identical or formally equivalent to today\'s: the gate checks equivalence on the bench cases\' IRs and fails the proposal otherwise. Latency changes must be reflected in latency_cycles (the formal depth and the netlist testbench depend on it).',
      '3. Test before proposing with sa_check (thread, design, backend, ir): it emits, validates, elaborates and simulates one IR on your b/ copy with the real toolchain (your shell has no python/iverilog). Check the knob on and off; fix what fails; the gate repeats these on a clean copy. Your shell is for `diff -ruN a b`, `ls`, `cat`, `grep`.',
      '4. Produce the diff with `diff -ruN a b > proposal.diff` (exit status 1 means "files differ", not an error), then `read` proposal.diff and pass its text to sa_propose verbatim -- never retype a diff by hand (a hunk whose line counts are off is "malformed patch"). sa_propose takes: title, a rationale that names the critical path the knob targets and the expected effect, the diff, designs (the generators touched), irs (the IR(s) with the knob on that the gate should simulate), and ab_spec (a full spec whose initial_ir has the knob on) so the gate ends with a measured run on the device. Then sa_proposal with wait_seconds 90 until it leaves "gating"; report the verdict per step and the A/B numbers (sa_job on the A/B run). A failed gate is information: read the failing step, fix, propose again.',
      'Never claim a result the gate or the A/B run did not measure. The operator merges; you do not.',
      'Answer in the user\'s language.',
    ].join('\n')
    ctx.skills.register({
      name: 'silicon-agent',
      description: 'Turn a natural-language FPGA accelerator request into signed-off Verilog with silicon-agent: interpret, estimate, run on the real device, explain the trajectory.',
      body: METHOD,
    })

    // Guardrails for silicon-agent chat sessions (the job server opens them with a `sa-thread-` session key):
    // the method is injected as a prompt section so the agent never has to be told to "use the skill", and the
    // host tools that would let it edit files or run commands are refused -- every mutation goes through the
    // job server, whose results are what the chat renders.
    const isChatSession = (session) => typeof session?.key === 'string' && session.key.startsWith('sa-thread-')
    // an author session changes the generators in a scratch copy the job server made (its cwd); it may write there
    // and nowhere else, and the change leaves through sa_propose
    const isAuthorSession = (session) => typeof session?.key === 'string' && session.key.startsWith('sa-author-')
    const HOST_TOOLS = new Set(['write', 'edit', 'shell', 'web_fetch', 'subagent_spawn', 'subagent_fork', 'plugin_helper_install', 'skill_helper_install', 'skill_helper_import', 'mcp_manage'])
    const NEVER = new Set(['web_fetch', 'subagent_spawn', 'subagent_fork', 'plugin_helper_install', 'skill_helper_install', 'skill_helper_import', 'mcp_manage'])
    agnes.registerHook('context', (_payload, hctx) => {
      // a participant returns only its own sections (author shape: id/order/content); the kernel keeps the rest
      if (isChatSession(hctx.session)) return { sections: [{ id: 'silicon-agent', order: 50, content: `# silicon-agent\n${METHOD}` }] }
      if (isAuthorSession(hctx.session)) return { sections: [{ id: 'silicon-agent-author', order: 50, content: `# silicon-agent author\n${AUTHOR_METHOD}` }] }
      return {}
    })
    // the session's workspace is the scratch tree the job server opened the author session in; a relative path is
    // resolved by the host against that same root, an absolute one must sit under it
    const insideRoot = (session, p) => {
      const root = String(session?.workspaceRoot ?? '').replace(/\/$/, '')
      if (typeof p !== 'string' || !root) return false
      if (p.split('/').includes('..')) return false
      return !p.startsWith('/') || p === root || p.startsWith(root + '/')
    }
    agnes.registerHook('tool_call', (payload, hctx) => {
      if (isChatSession(hctx.session) && HOST_TOOLS.has(payload.name))
        return { allow: false, reason: `${payload.name} is not available in a silicon-agent chat: use the sa_* tools (the design, its files and the EDA run live on the job server)` }
      if (isAuthorSession(hctx.session)) {
        if (NEVER.has(payload.name)) return { allow: false, reason: `${payload.name} is not available in an author session` }
        if (payload.name === 'write' || payload.name === 'edit') {
          const p = payload.args?.path
          if (!insideRoot(hctx.session, p)) return { allow: false, reason: `writes are confined to the scratch tree ${hctx.session?.workspaceRoot}; ${p} is outside it` }
          if (/\/(server|agh|proposals|llm)\.py$/.test(String(p))) return { allow: false, reason: 'the server, the AGH client, the proposal gate and the LLM client are not proposal targets' }
        }
        if (payload.name === 'shell') {
          const cmd = String(payload.args?.command ?? '')
          if (payload.args?.cwd && !insideRoot(hctx.session, payload.args.cwd)) return { allow: false, reason: 'shell cwd must stay in the scratch tree' }
          if (/\b(curl|wget|ssh|scp|rsync|git (push|commit|checkout|reset)|rm -rf \/|sudo|docker|pip install|npm )/.test(cmd))
            return { allow: false, reason: 'author shells run tests in the scratch tree only (python/iverilog/yosys/sby/git diff)' }
        }
      }
      return { allow: true }
    })

    agnes.on('session_start', () => {
      agnes.ctx.log.info(`silicon-agent tools bound to ${server}`)
    })
  },
}
