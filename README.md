# silicon-agent

Autonomous hardware design loop: say what you want in plain language, and an
LLM planner iterates architecture → RTL → simulation → synthesis → place-and-route
until it meets the requirements, then hands you the Verilog. Real EDA tools are
the ground truth; nothing is estimated.

Designs: `matmul` (systolic INT matrix multiply), `fir` (fixed-coefficient FIR), `conv2d`
(streaming K×K convolution with BRAM line buffers), `fir_chain` (a cascade of FIR stages with
FIFOs between them — the first composite datapath). Devices: Lattice ECP5, AMD Spartan-7 (Vivado),
Lattice iCE40 UP5K, Gowin GW1NR-9 — each a plugin under `silicon_agent/backends/`.

```
laptop                              EDA host (netcup)
──────                              ─────────────────────────────────────────────
sa submit spec.json ──HTTP/SSH──▶   job server ──▶ planner (DeepSeek) ──▶ arch IR
sa watch <job>      ◀── events ──        ▲                                   │
                                         │                            generator → SystemVerilog
                                         │                                   │
                                         │             cocotb + iverilog   functional vs. Python golden model
                                         │             ecp5:   yosys + nextpnr     real P&R timing, ~30 s
                                         │             xc7s50: Vivado 2025.2        real P&R timing, ~2-4 min
                                         └──── metrics + critical path ◀─────┘
```

## Verification ladder

Every evaluated design passes through five simulations before its numbers count:

| # | stage | tool | what it proves |
|---|---|---|---|
| 1 | Behavioral | cocotb + iverilog | RTL matches the Python golden model (random + extreme vectors, back-pressure) |
| 2 | Post-synthesis functional | xsim, unisim | the synthesized netlist is logically the same design |
| 3 | Post-synthesis timing | xsim, simprim + SDF | …and still works with estimated cell delays at the target period |
| 4 | Post-implementation functional | xsim, unisim | the placed-and-routed netlist is logically the same design |
| 5 | Post-implementation timing | xsim, simprim + SDF | …and works with real routed delays at the target period — the signoff |

Stages 2–5 run on the Vivado backend; the ECP5 backend runs stage 2 against the
yosys-mapped netlist (nextpnr's routed output has no simulation model or SDF, so
3–5 are unavailable there). A failing stage is a constraint violation the planner sees. Verified both ways: a 2×2 matmul passes all five at 250 MHz; an
unpipelined 4×4 asked for 600 MHz passes 2 and 4 and fails 3 and 5.

## Planner loop

Each iteration the planner reads the last result (metrics, summarized critical
path, simulation ladder), the lessons design memory recalls for that situation,
and any instructions typed into the console, then proposes **up to three
candidate IRs**. A closed-form screening model (`screen.py`) estimates each one —
and every EDA result is recorded, so the estimator learns a per-(design, backend,
metric) multiplicative correction by leave-one-out ridge regression, applied only
when it beats the raw estimate (`GET /api/calibration` reports the current error);
only the top candidate is sent through EDA, estimated-feasible runners-up are
kept as fallbacks and tried without re-planning if the chosen one violates, and
if nothing looks feasible the planner gets one re-plan with the verdicts. After
each result the move is distilled into a lesson and the recalled lessons that
matched the move are confirmed or contradicted automatically.

The LLM never writes Verilog. It edits a small architecture IR (`silicon_agent/ir.py`);
a deterministic generator (`silicon_agent/emit.py`) emits the RTL, so every design
is correct by construction and the generated names map straight back to IR fields
in the critical-path report.

Two design kinds (`"design"` in the spec), two backends (`"backend"`):

| design   | what                                         | planner knobs |
|----------|----------------------------------------------|---------------|
| `matmul` | output-stationary systolic INT matrix-multiply | `pe_rows`, `pe_cols`, `acc_width`, `pipe_mul`, `reg_in`, `dsp_regs`, `acc_in_dsp` |
| `fir`    | fixed-coefficient FIR (transposed or folded)   | `symmetric`, `fold`, `acc_width`, `pipe_mul`, `reg_in`, `dsp_regs`, `acc_in_dsp` |


| backend  | device                     | tools                         | per iteration |
|----------|----------------------------|-------------------------------|---------------|
| `ecp5`   | Lattice ECP5 LFE5U-45F-6   | yosys + nextpnr (open source) | 15–60 s       |
| `xc7s50` | AMD Spartan-7 XC7S50-1     | Vivado 2025.2                 | 1.5–4 min     |

## Example runs (all real, from `results/`)

ECP5, Fmax ≥ 180 MHz, DSP ≤ 48, INT8 locked:

```
[1] 8x8  no pipelining                     fmax=57.2 MHz  dsp=64   ✗ timing ✗ DSP
[2] 6x8  in_pipe_out, pipe=1, acc 24       fmax=170.1 MHz dsp=48   ✗ timing
[3] 4x8  (relieve congestion)              fmax=193.5 MHz dsp=32   ✓
[4] 4x10 (grow columns, not rows)          fmax=195.5 MHz dsp=40   ✓
[5] 4x12 (fill the DSP budget)             fmax=188.5 MHz dsp=48   ✓  ← best, planner stops
```

XC7S50 via Vivado, Fmax ≥ 200 MHz, DSP ≤ 64:

```
[1] 8x8  no pipelining                     fmax=196.2 MHz dsp=64   ✗ timing   (critical: DSP48E1 M→P 4.0 ns)
[2] 8x8  dsp_regs=in_pipe_out              fmax=227.4 MHz dsp=64   ✓
[3] 8x8  acc 32→24                         fmax=239.6 MHz lut=1005 ✓  ← best, planner stops
```

XC7S50 via Vivado, Fmax ≥ 300 MHz, DSP ≤ 32 (the hard one):

```
[1] 8x8  no pipelining                     fmax=189.8 MHz dsp=64   ✗ timing ✗ DSP
[2] 4x8  in_pipe_out, acc_in_dsp=false,    fmax=299.9 MHz dsp=32   ✗ by 0.1 MHz
         acc 24                                                       (critical: fabric CARRY4, route-dominated)
[3] 2x16 (halve pe_rows → shorter drain/   fmax=319.5 MHz dsp=32   ✓  ← best, planner stops
         skew nets, same 32 MAC/cycle)
```

Before `acc_in_dsp` existed this spec failed after 7 iterations at ~278 MHz: the
planner correctly diagnosed that the DSP48E1 post-adder M→P stage (2.3 ns) was
an unbreakable wall and that no IR field could move it. Measuring the four
combinations of `dsp_regs × acc_in_dsp` on the real design (190 / 151 / 277 / 299 MHz)
turned that into a new knob plus one line of device facts in the prompt.

## Chat

`https://silicon.w33d.xyz` — behind sluice SSO (route `silicon-agent-root`,
permission `studio.console.enter`). One conversation per design task, like a chat
with an engineer:

```
you   一个 32 阶低通 FIR，Spartan-7，采样率 250 MHz，尽量少用 DSP
agent 在 Spartan-7 上做 32 阶 16 位 FIR，250 MHz，先按对称折叠把乘法器压到 16 个 DSP…
      ▸ 理解的规格 · fir · xc7s50 · ≥250 MHz · ≤16 DSP
      ▸ 运行中 · 第 3 轮 / 8 · 实现后时序仿真          (live, updates in place)
      …
      7 轮后找到可行设计：非对称、fold=8、259 MHz、8 个 DSP…（prose report）
      ▸ 轨迹 · 7 轮 · 1 轮可行
      ▸ 结果 · fir_top.sv · 259.1 MHz · 8 DSP · 实现后时序仿真通过
        [查看 Verilog] [下载 fir_top.sv] [打开 worktable] [在此基础上继续]
you   DSP 可以放宽到 16，吞吐要满速
agent (forks from the best design with the new constraints and continues)
```

`nl.py` turns the message into a spec (design, device, locked task parameters,
constraints, objective order, a naive starting design) and states its
assumptions; follow-ups in the same thread either refine (fork with edited
constraints, seeded from the previous best or closest design), steer a running
job, or are answered as questions. When a run ends, the agent narrates what was
tried and what won. Cards are collapsed by default; simulation stages, IR
changes, critical paths and logs appear only when expanded. The UI follows the
language of the conversation. `sa ask "…" [--thread id]` is the same from a shell.

## Worktable

`/work?thread=<id>` is the engineer's view of **one conversation** (the "worktable ↗"
link in the chat header and in each result card): only that conversation's runs are
listed, and everything started there — a new run from the form, a fork, a re-run —
is bound to the conversation and shows up in the chat as a turn tagged `worktable`,
with its own cards and report. Compare only offers runs of the same conversation,
and a deep link to another conversation's run is refused. Bare `/work` lists the
runs that belong to no conversation (CLI `sa submit`, older runs). The view: a rail of runs,
one transcript with a turn per iteration (what the planner did, the IR, one
verdict line, the simulation ladder; hypothesis / recalled memory / candidates
with estimates / critical path / lesson behind a per-turn disclosure), and a
composer that steers the planner before its next proposal. **Details ▸** opens the
inspector: architecture sketch + IR knobs (click to draft a steer) + critical
path, trajectory charts, the simulation ladder with logs, generated RTL, and
memory recalled/learned. Lessons take 👍/👎 votes. Events persist per job.

Actions on a run (⋯ menu): **Fork** — start a new run from any iteration's design
with edited constraints (the new run links back to its parent); **Compare** —
two runs side by side: feasibility, best metrics, overlaid trajectories, mini
transcripts (the memory A/B pair is the canonical use); **Re-run**; **Delete**.
New runs are configured in a form (design, backend, constraints, objective
order by drag, locked fields, starting design from presets or "best from
memory", live screening estimate) with a JSON escape hatch.

Deploys are safe: on SIGTERM the server finishes the in-flight iteration,
requeues the job, and on restart replays it from the EDA and planner caches.

Auth model: sluice injects `X-Auth-Subject` + `X-Auth-Sig` (HMAC-SHA256 over
`subject\ngroups\nepoch-minute` with the gateway key). The server shares that key
(`SA_GATEWAY_HMAC_KEY`) and verifies the signature for the current and previous
minute, so a direct dial on the docker network cannot forge identity. The `sa`
CLI keeps using its bearer token over the SSH tunnel.

## Quick start (remote: laptop is a thin client)

```bash
ssh -f -N -L 8765:127.0.0.1:8765 netcup               # tunnel to the job server
export SA_TOKEN=$(security find-generic-password -s silicon-agent-token -w)
./sa health
./sa ask "INT8 matmul on ECP5, 180 MHz or better, at most 48 DSPs"   # natural language, streams until done
./sa ask "try 300 MHz on Spartan-7 instead" --thread <thread-id>      # refine / ask in the same thread
./sa submit specs/matmul_int8_xc7s50.json --max-iter 8   # explicit spec, streams progress until done
./sa list
./sa get <job-id> summary.json                           # or iter_03.json, llm_02.json
```

`sa` is a single stdlib Python file: no toolchain, no dependencies on the laptop.
`--backend ecp5|xc7s50` overrides the spec's backend.

## Quick start (local, ECP5 only)

```bash
./setup.sh                                 # downloads oss-cad-suite (~600 MB) into tools/, no sudo
export DEEPSEEK_API_KEY=sk-...             # or macOS Keychain item 'deepseek'
./run.sh specs/matmul_int8_180mhz.json 8
```

## Server deployment

The server runs inside the host's existing `local/vivado-desktop:2025.2` image so
both backends are available. Secrets live in `/opt/silicon-agent/.env` on the host
(`SA_TOKEN`, `DEEPSEEK_API_KEY`), outside the synced tree.

```bash
deploy/push.sh netcup      # rsync code → /opt/silicon-agent/app, recreate container, health check
ssh netcup docker logs -f silicon-agent
```

Persistent state on the host: `/srv/sa/{cache,runs,results}`. The oss-cad-suite
Linux build lives in `/opt/silicon-agent/tools`. The server binds to
`127.0.0.1:8765`; reach it through the SSH tunnel (or front it with the reverse proxy).

API (all under `/api/`, legacy unprefixed paths still work for the CLI):
`POST /jobs {spec, max_iter}` → `{id}`; `GET /jobs`; `GET /jobs/<id>?after=N` →
status + incremental events; `GET /jobs/<id>/files/<name>`; `GET /jobs/<id>/rtl`
(best design's SystemVerilog); `GET /specs`; `GET /me`; `GET /health`. One job runs
at a time; others queue.

Gateway route: `deploy/sluice-route.json`, applied with `deploy/sluice-add-route.sh`
on the host (appends to the seed with a timestamped backup and upserts the live
Postgres row; sluice reloads routes every 20 s).

## Spec format

```json
{
  "backend":     "xc7s50",
  "locked":      {"data_width": 8},
  "constraints": {"fmax_mhz": 200, "max_dsp": 64, "max_lut": 8000,
                  "objective": ["maximize_throughput", "minimize_lut"]},
  "initial_ir":  {"pe_rows": 8, "pe_cols": 8, "data_width": 8, "acc_width": 32,
                  "pipe_mul": 0, "reg_in": false, "dsp_regs": "none"}
}
```

Constraints are hard; a design is feasible only if all hold. Among feasible designs
the objective list is applied lexicographically. `locked` fields are part of the task
and the planner cannot change them (without it the planner will happily shrink
INT8 to INT4 to "minimize LUT").

## IR knobs (matmul)

| field | range | effect |
|---|---|---|
| `pe_rows`, `pe_cols` | 1..32 | array size; throughput = rows×cols MAC/cycle; one DSP per PE |
| `data_width` | 4/8/12/16 | operand width |
| `acc_width` | 2·dw+8 .. 48 | accumulator carry-chain length (LUTs and timing) |
| `pipe_mul` | 0..3 | fabric registers between multiplier and accumulator |
| `dsp_regs` | none/out/in_out/in_pipe_out | DSP-internal pipeline registers (MULT18X18D on ECP5; A/B/M/P regs on DSP48E1) |
| `reg_in` | bool | boundary input registers |
| `acc_in_dsp` | bool | xc7s50 only: accumulate in the DSP48E1 post-adder (fewer LUTs, M→P stage caps Fmax ~280 MHz) or in a fabric CARRY4 adder after the P register |

The streaming interface (`in_valid`/`in_last` → `out_valid`/`c_row`) is
latency-insensitive, so any pipeline change is verified by the same testbench.
On ECP5 the DSP is instantiated explicitly (yosys never packs pipeline registers
into MULT18X18D); on XC7S50 the same knob emits behavioral registers that Vivado
retimes into the DSP48E1.

FIR, ECP5, 32-tap 16-bit lowpass, Fmax ≥ 120 MHz, DSP ≤ 6:

```
[1] transposed, behavioral mults           fmax=233.3 MHz dsp=12   ✗ DSP   (yosys already shares equal coefficients)
[2] symmetric, fold=4, in_pipe_out         fmax=126.8 MHz dsp=4    ✓       0.25 samples/cycle
[3] acc 40→38                              fmax=131.4 MHz lut=962  ✓  ← best
[4] pipe_mul=1 (no gain: mux is critical)  fmax=130.1 MHz          ✓       planner stops
```

FIR, **XC7S50 via Vivado**, 32-tap 16-bit lowpass, Fmax ≥ 250 MHz, DSP ≤ 8:

```
[1] transposed, acc in DSP                 fmax=495.5 MHz dsp=45   ✗ DSP   (Vivado absorbs the whole filter into DSP48E1 cascades)
[2] symmetric fold=2, acc in DSP           fmax=163.1 MHz dsp=16   ✗ both  (each MAC = 2 DSPs)
[3] symmetric fold=2, acc in fabric        fmax=150.0 MHz dsp=8    ✗ timing (8-way partial-sum combine)
[4] symmetric fold=4, acc in DSP           fmax=236.6 MHz dsp=8    ✗ by 13 MHz (tap mux + pre-adder)
[5] symmetric fold=8                       fmax=225.5 MHz dsp=4    ✗ (wider mux costs more than it saves)
[6] fold=4, in_out regs                    fmax=152.1 MHz dsp=8    ✗
[7] non-symmetric fold=8, acc 37           fmax=259.1 MHz dsp=8    ✓  ← pre-adder off the critical path, same MAC count
```

Adding a design kind = one IR dataclass + one emitter + one cocotb test + one
registry entry in `designs.py` + a prompt block in `prompts.py`.

## Trust and delivery

- **Benchmark + regression** (`python -m silicon_agent.bench run --tag nightly`): 16 fixed cases over six
  designs and four devices, each with an expectation (feasible, rounds to first feasible). Submitted as
  ordinary jobs, scored, written to `bench/<tag>-<ts>.json`; `bench show` tabulates the last reports and
  `compare()` flags any case that got worse than the previous report. `deploy/nightly.sh` runs the
  open-source cases from cron on the EDA host (03:17) and exposes the result at `GET /api/bench`.
- **Formal checks** (`GET /api/jobs/<id>/formal[?depth=20]`, `formal.py`): SymbiYosys bounded model
  checking. *Equivalence*: a miter instantiates the RTL that was measured and a fresh emit of the same IR,
  drives both with the same free inputs and asserts every output agrees cycle by cycle -- the guard for
  generator refactors. *Properties*: no `out_valid` in reset, defined outputs after reset, no handshake
  retraction. Positive and negative controls (a corrupted copy fails) are part of the check.
- **Functional coverage**: every cocotb bench reports named bins (back-to-back, gaps, stalls, extreme
  operands, frame restarts...) as `sim_coverage`; the chat shows the bins hit, so "simulation passed" says
  what was exercised.
- **Multi-seed Fmax**: the open-source backends place-and-route every design with 3 seeds (`SA_SEEDS`) and
  report the median; the spread (typically ±3-5 %) is shown next to Fmax and given to the planner, so a
  near-miss is not decided by one placement roll.
- **Evidence for stopping**: the planner may stop only with a `stop_reason` naming the bound it hit (DSP cap,
  minimum accumulator width, an intrinsic device stage); it is shown in the trajectory and in the report.
- **Power** (Vivado): `report_power` dynamic power becomes `power_mw`, usable as `max_power_mw` and
  `minimize_power`.
- **Cross-run Pareto front** (`GET /api/pareto?design=&backend=[&fmax_mhz=&max_dsp=&max_lut=]`): every
  measured point of a design kind on a device, the non-dominated set, and the best measured point under given
  constraints; drawn in the worktable's Trajectory tab and given to the chat so "what is the best achievable"
  is answered with measured numbers.
- **Delivery package** (`GET /api/jobs/<id>/package`): RTL, an AXI4-Stream wrapper with output FIFO
  (verified under random back-pressure; an AXI4-Lite coefficient port when the FIR has `coef_port`), the
  self-checking vector testbench, tool reports, the trajectory, a README, and a board project:
  `project/create_vivado.tcl` + `constraints.xdc` for the AMD parts, `build_oss.sh` for the open-source
  devices. "Download package (zip)" in the chat result card and the worktable menu.

## Platform: adding a design or a device

Designs: `matmul`, `fir` (optionally with a runtime-writable coefficient port), `conv2d`, `cordic`
(rotate/vector, unrolled or folded, bit-exact golden), `fft` (radix-2 SDF, 16..4096 points, scale or grow,
BRAM delay lines, bit-reversal reorder; cycle-exact fixed-point golden), `fir_chain` and the generic
`graph`. Devices: ECP5, Spartan-7 and Artix-7 (Vivado), iCE40 UP5K, Gowin GW1NR-9, GateMate CCGM1A1.

A **composite design** (see `chain.py`, `graph.py`) is a design whose IR holds a list of node IRs; its
emitter instantiates the nodes' generated modules (renamed `n<i>_*`) with FIFOs and width adapters between
them, `locked["stages"|"nodes"]` is merged per node, and the planner edits each node's knobs. `graph`
accepts any chain of fir / conv2d / cordic / fft nodes, so "filter then CORDIC" is one task; the FIFO must
cover the upstream node's latency because nodes have no back-pressure input (the graph gates the source).

A **design** is one module (see `fir.py`, `conv.py`): a frozen IR dataclass with `validate()`,
`to_dict()/from_dict()`, `digest()`; an `emit(ir, backend)` that returns SystemVerilog with the
valid/ready streaming interface; a cocotb test with a Python golden model under `tb/`; a vector
generator + SystemVerilog bench in `vectors.py` for the netlist ladder; a closed-form estimator in
`screen.py`; planner knowledge in `prompts.py` (what it is, the IR schema, measured device facts);
and a `schema.py` entry (fields, presets, locked defaults) for the form and the NL interpreter.
Register it in `designs.py`.

A **device** is one module under `backends/` calling `register(Backend(...))` with the device
facts (LUT/DSP/BRAM capacity, primitive names, realistic Fmax band, seconds per iteration), an
`emit_style` telling emitters how to render multipliers (`ecp5` explicit MULT18X18D, `vivado`
attributes, `behavioral` plain `*`), `synth_pnr(work, fmax, top)` returning Fmax / LUT / FF / DSP /
BRAM / critical path, and `netlist_sims(...)` for whichever ladder stages the toolchain can run.
Open-source backends share `_common.py` (nextpnr report parsing, iverilog netlist sims, flattened
cell libraries); black-box primitives get simulation models in `tb/` (`bram_sim.v`,
`gowin_mult_sim.v`). Devices whose P&R needs IO pins (iCE40, Gowin) are evaluated through a serial
shell (`sa_wrap`) and report the core's synthesis counts with the routed Fmax. Prompts, estimates,
the form and the NL interpreter pick up a new device from the registry; measured facts in
`prompts.py` are optional and override the generic paragraph.

## Layout

```
sa                         remote client (laptop side)
silicon_agent/ir.py        matmul IR, constraints, metrics
silicon_agent/emit.py      matmul IR → SystemVerilog (backend-aware multiplier)
silicon_agent/fir.py       FIR IR + emitter (transposed / folded, symmetric sharing)
silicon_agent/conv.py      2-D convolution IR + emitter (BRAM/LUT line buffers, row-serial or parallel, adder tree)
silicon_agent/cordic.py    CORDIC IR + emitter (rotate/vector, unroll/fold, bit-exact golden)
silicon_agent/fft.py       radix-2 SDF FFT IR + emitter + cycle-exact fixed-point golden
silicon_agent/chain.py     composite: cascaded FIR stages with inter-stage FIFOs
silicon_agent/graph.py     composite: generic chain of fir/conv2d/cordic/fft nodes with FIFOs and width adapters
silicon_agent/formal.py    SymbiYosys bounded equivalence (miter) + stream properties
silicon_agent/bench.py     benchmark set + regression runner
silicon_agent/deliver.py   AXI4-Stream wrappers + delivery zip
silicon_agent/pareto.py    cross-run Pareto front
silicon_agent/designs.py   design registry
silicon_agent/backends/    device plugins: ecp5, xc7s50 + xc7a35t (Vivado), ice40, gowin, gatemate; _common.py shared helpers
silicon_agent/prompts.py   per-design planner knowledge with measured device facts (+ generic facts from the registry)
silicon_agent/eda.py       behavioral simulation + cached evaluate() over the backend registry
silicon_agent/loop.py      generic optimization loop
silicon_agent/nl.py        natural language → spec; narration; Q&A about a run
silicon_agent/server.py    HTTP job server + threads + static web UI, token or gateway-HMAC auth
silicon_agent/web/         chat (index.html, chat.js, chat.css) and worktable (work.html, app.js, form.js, app.css) — no build step
silicon_agent/llm.py       DeepSeek client
silicon_agent/tb/          cocotb testbenches, golden models, coverage bins, primitive sim models (MULT18X18D, BRAM, Gowin MULT)
specs/                     task specs
deploy/                    compose.yml, push.sh, nightly.sh (cron regression), sluice-route.json, sluice-add-route.sh
setup.sh  run.sh           local toolchain install / local entry point
```
