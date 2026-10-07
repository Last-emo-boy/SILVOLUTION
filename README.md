# silicon-agent

Autonomous hardware design loop: a spec with PPA constraints goes in, and an
LLM planner iterates architecture → RTL → simulation → synthesis → place-and-route
until the constraints are met. Real EDA tools are the ground truth; nothing is
estimated.

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

## Web console

`https://silicon.w33d.xyz` — behind sluice SSO (route `silicon-agent-root`,
permission `studio.console.enter`, same gate as the Vivado desktop). Pick a spec
template, edit the JSON, Run; the page streams each iteration: constraint tiles,
Fmax / LUT / DSP per iteration, a throughput-vs-LUT Pareto view, and the agent log
(hypothesis → action → result with the summarized critical path). Past runs are
rebuilt from `results/` on server start, so nothing is lost across restarts.

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
./sa submit specs/matmul_int8_xc7s50.json --max-iter 8   # streams progress until done
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

Adding a design kind = one IR dataclass + one emitter + one cocotb test + one
registry entry in `designs.py` + a prompt block in `prompts.py`.

## Layout

```
sa                         remote client (laptop side)
silicon_agent/ir.py        matmul IR, constraints, metrics
silicon_agent/emit.py      matmul IR → SystemVerilog (backend-aware multiplier)
silicon_agent/fir.py       FIR IR + emitter (transposed / folded, symmetric sharing)
silicon_agent/designs.py   design registry
silicon_agent/prompts.py   per-design planner knowledge with measured device facts
silicon_agent/eda.py       simulate / synth / P&R / report parsing / cache, per backend
silicon_agent/loop.py      generic optimization loop
silicon_agent/server.py    HTTP job server + static web UI, token or gateway-HMAC auth
silicon_agent/web/         console (index.html, app.js, app.css — no build step)
silicon_agent/llm.py       DeepSeek client
silicon_agent/tb/          cocotb testbenches, golden models, MULT18X18D sim model
specs/                     task specs
deploy/                    compose.yml, push.sh, sluice-route.json, sluice-add-route.sh
setup.sh  run.sh           local toolchain install / local entry point
```
