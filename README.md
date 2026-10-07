# silicon-agent

Autonomous hardware design loop: a spec with PPA constraints goes in, and an
LLM planner iterates architecture → RTL → simulation → synthesis → place-and-route
until the constraints are met. Real EDA tools are the ground truth; nothing is
estimated.

```
spec.json ──▶ planner (LLM) ──▶ arch IR ──▶ generator ──▶ SystemVerilog
                  ▲                                            │
                  │                              cocotb + iverilog  (functional, vs Python golden model)
                  │                              yosys             (synthesis, cell counts)
                  │                              nextpnr-ecp5      (place & route, real Fmax, critical path)
                  └────────── metrics + critical path ◀────────────┘
```

The LLM never writes Verilog. It edits a small architecture IR (`silicon_agent/ir.py`);
a deterministic generator (`silicon_agent/emit.py`) emits the RTL, so every design
is correct by construction and the generated names map straight back to IR fields
in the critical-path report.

Current benchmark: output-stationary systolic INT matrix-multiply on Lattice ECP5
(LFE5U-45F). The whole loop runs on a laptop: ~15–50 s per iteration.

## Quick start

```bash
./setup.sh                               # downloads the open-source toolchain (~600 MB), no sudo
export DEEPSEEK_API_KEY=sk-...           # or store it in macOS Keychain, see setup.sh output
./run.sh specs/matmul_int8_180mhz.json   # run the loop, up to 8 iterations
```

Example run (`specs/matmul_int8_180mhz.json`: Fmax ≥ 180 MHz, DSP ≤ 48, INT8 locked):

```
[1] 8x8, no pipelining          fmax=56.8 MHz  dsp=64   ✗ timing, ✗ DSP
    diagnosis: DSP→carry-chain route 5.47 ns + 32-bit carry chain exposed, no pipeline registers
    action:    4x12 array (48 DSP), enable DSP internal regs + fabric pipe + input regs, acc_width 24
[2] 4x12, in_pipe_out, pipe=2   fmax=181.4 MHz dsp=48   ✓ FEASIBLE   lut=3390
    planner: throughput at DSP ceiling, acc_width at floor → stop
```

Outputs land in `results/<run-id>/`: one JSON per iteration (IR, metrics,
critical path, diagnosis, action) plus every LLM prompt/reply, and `summary.json`.
Generated RTL and tool logs are in `runs/<rtl-hash>/`. EDA results are cached by
RTL hash in `cache/`, so re-running a spec is free.

## Spec format

```json
{
  "locked":      {"data_width": 8},
  "constraints": {"fmax_mhz": 180, "max_dsp": 48, "max_lut": 8000,
                  "objective": ["maximize_throughput", "minimize_lut"]},
  "initial_ir":  {"pe_rows": 8, "pe_cols": 8, "data_width": 8, "acc_width": 32,
                  "pipe_mul": 0, "reg_in": false, "dsp_regs": "none"}
}
```

Constraints are hard; a design is feasible only if all hold. Among feasible designs
the objective list is applied lexicographically. `locked` fields are part of the task
and the planner cannot change them.

## IR knobs (matmul)

| field | range | effect |
|---|---|---|
| `pe_rows`, `pe_cols` | 1..32 | array size; throughput = rows×cols MAC/cycle; one MULT18X18D per PE |
| `data_width` | 4/8/12/16 | operand width |
| `acc_width` | 2·dw+8 .. 48 | accumulator carry-chain length (LUTs and timing) |
| `pipe_mul` | 0..3 | fabric registers between multiplier and accumulator |
| `dsp_regs` | none/out/in_out/in_pipe_out | MULT18X18D internal pipeline registers |
| `reg_in` | bool | boundary input registers |

The streaming interface (`in_valid`/`in_last` → `out_valid`/`c_row`) is
latency-insensitive, so any pipeline change is verified by the same testbench.

## Layout

```
silicon_agent/ir.py        architecture IR, constraints, metrics
silicon_agent/emit.py      IR → SystemVerilog generator
silicon_agent/eda.py       simulate / synth / P&R / report parsing / cache
silicon_agent/loop.py      planner prompt + optimization loop
silicon_agent/llm.py       DeepSeek client
silicon_agent/tb/          cocotb testbench, golden model, MULT18X18D sim model
specs/                     task specs
setup.sh  run.sh           toolchain install / entry point
```

## Platforms

`setup.sh` installs the YosysHQ oss-cad-suite build for macOS (arm64, x64) and
Linux (x64, arm64). On Windows use WSL2. Everything lives under `tools/`; nothing
touches the system. The LLM endpoint is OpenAI-compatible, so any such provider
works via `DEEPSEEK_BASE_URL` / `SA_MODEL`.
