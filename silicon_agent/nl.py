"""Natural-language front: turn what the user says into a spec, narrate what the agent did.

Three LLM calls, each with a tight JSON contract:

  interpret(message, context)  -> {"spec": {...}, "summary": "...", "questions": [...], "assumptions": [...]}
                                  context carries the current run (if any), so "make it faster" or
                                  "DSP can go to 16" becomes a fork with edited constraints
  narrate(run, lang)           -> short prose for a finished or stopped run: what was tried, what won,
                                  what the user should know (throughput, margins), in the user's language
  reply(message, run)          -> answer a question about a run without starting anything

The planner loop itself is unchanged; this layer only produces and explains specs.
"""

from __future__ import annotations

import json
import re

from . import llm
from .designs import DESIGNS
from .eda import BACKENDS
from .schema import SCHEMAS, OBJECTIVES

INTERPRET_SYSTEM = """You turn a hardware request written in natural language into a job spec for an autonomous FPGA design agent.
Reply in the user's language for prose fields. Be decisive: pick sensible defaults and state them as assumptions
rather than asking, unless something essential is genuinely unknowable (then ask at most 2 short questions and still
return your best-guess spec).

Available designs:
  matmul  output-stationary systolic INT matrix multiply. locked: data_width (4/8/12/16).
  fir     fixed-coefficient FIR filter; a windowed-sinc lowpass is generated from taps + cutoff (fraction of fs, 0.01-0.49).
          locked: taps, data_width, coef_width, cutoff, coef_port (true when the user wants to change/load/program the
          coefficients at runtime, e.g. "configurable", "programmable taps", "load coefficients over AXI").
          Throughput is 1/fold samples per cycle.
  conv2d  streaming single-channel K x K 2-D convolution (image filter / blur / edge kernel) with line buffers.
          locked: k (3/5/7), width (image width in pixels), data_width (8/12/16), coef_width (8/12/16).
          Throughput is par_rows/k pixels per cycle. A pixel rate R MHz means fmax >= R*k/par_rows.
  cordic  sin/cos (rotate) or magnitude/atan2 (vector) engine, no multipliers. locked: mode, width.
          Pipelined 1 sample/cycle or iterative (fold = iterations) for tiny area. Precision ~ iterations bits.
  fft     streaming N-point complex FFT, radix-2 SDF pipeline, 1 sample/cycle. locked: n (power of 2), data_width.
          4 DSPs per stage; natural-order output needs bit_reverse=true (+N latency).
  graph   a datapath of 2-6 heterogeneous stream nodes in series (any of fir / conv2d / cordic / fft) with FIFOs.
          locked: {"nodes": [{"kind": ..., "ir": {<that node's task fields>}}, ...]}; initial_ir: {"nodes": [{"kind", "ir": <full IR>}], "fifo_depth": 32, "adapt": "truncate"}.
          Use it when the user describes a pipeline of different blocks ("filter then FFT", "CORDIC then low-pass").
  fir_chain  a cascade of 2-4 FIR stages with FIFOs between them (e.g. anti-alias then shaping, or decimation
          chains). locked: {"stages": [{"taps","data_width","coef_width","cutoff"}, ...]} -- one entry per stage.
          initial_ir: {"stages": [<full FIR IR per stage>], "fifo_depth": 16, "round_mode": "truncate"}.
          Use it only when the user describes more than one filter in series.
Available backends (choose from device words; default xc7s50 when timing signoff is implied, else ecp5):
@BACKENDS@

Spec shape:
{
  "task": "<short title, user's language>",
  "design": "matmul" | "fir",
  "backend": "ecp5" | "xc7s50",
  "locked": {<task-defining fields>},
  "planner": <string or null>   (only when the user names a planner model to compare, e.g. "deepseek-reasoner"; otherwise null)
  "constraints": {"fmax_mhz": <number>, "max_dsp": <number or null>, "max_lut": <number or null>, "max_power_mw": <number or null, Vivado devices only>,
                  "objective": [<ordered subset of maximize_throughput, minimize_dsp, minimize_lut, maximize_fmax, minimize_latency, minimize_power>]},
  "initial_ir": {<a deliberately naive starting point so the agent has something to improve; matmul: pe_rows/pe_cols sized
                 to the throughput hint, acc_width 32, pipe_mul 0, reg_in false, dsp_regs "none", acc_in_dsp true;
                 fir: fold 1, symmetric false, acc_width dw+cw+8, pipe_mul 0, reg_in false, dsp_regs "none", acc_in_dsp true;
                 conv2d: par_rows k, line_mem "bram", acc_width dw+cw+8, tree_regs 0, pipe_mul 0, dsp_regs "none";
                 cordic: iterations = width, unroll 1, fold 1, guard_bits 2, scale_comp false;
                 fft: tw_width 16, growth "scale", delay_mem "auto", bram_min 64, mul_regs 0, bf_regs 1, dsp_regs "none">}
}
Objective order matters: the agent optimizes lexicographically, so whatever comes first is pushed to its extreme. Unless
the user says the design itself should be as small/cheap as possible, maximize_throughput comes first -- someone asking
for a matrix multiply with "save LUTs" wants a useful array that is lean, not a 1x1 PE; "save LUT/DSP" then means
minimize_lut/minimize_dsp second. A DSP budget is meant to be used: set max_dsp and let throughput fill it.
Rules of thumb: "fast"/"high clock" -> fmax 250-300 on xc7s50, 150-200 on ecp5; "few DSPs" -> max_dsp around a quarter of
the naive count; "as small as possible" -> minimize_lut first; a sample rate R MHz on a FIR means throughput >= R/fmax samples/cycle --
put that in assumptions and choose max_dsp/fold accordingly. Numbers the user states are used exactly as given (no added
margin, no rounding): "160 MHz or better" is fmax_mhz 160. Keep "assumptions" to the 1-3 that matter (things you had to
choose), each one short. If the user is refining an existing run (context given), return "fork": true and only the fields
that change in "constraints"/"locked"; keep the rest.

If a different *device* or a meaningfully different *architecture setting* would also serve the request well (e.g. the
same FIR on ECP5 instead of Spartan-7 for a 30 s iteration; a row-serial conv2d with a third of the multipliers; a
smaller matmul array that still meets the rate), list up to 2 in "alternatives" as complete follow-up requests the
user could send verbatim, each with a one-line trade-off. Leave it empty when nothing is worth offering.

Respond with JSON:
{"spec": {...}, "fork": false, "summary": "<one sentence: what you will build and the key assumptions>",
 "assumptions": ["..."], "questions": ["..."],
 "alternatives": [{"request": "<a follow-up request in the user's language>", "why": "<trade-off in one line>"}]}"""

NARRATE_SYSTEM = """You explain the outcome of an autonomous FPGA design run to the engineer who asked for it, in their language,
like a capable colleague reporting back: what was tried and why it failed or worked, in 3-6 short sentences, numbers
included (MHz, DSP, LUT, samples/cycle or MAC/cycle). Say why the agent stopped (why_it_stopped: the bound it hit, or
the iteration budget). Then one sentence of advice or a caveat if there is one (e.g. low throughput, thin timing margin,
a constraint that looks unnecessary, a noisy Fmax margin when the P&R seed spread straddles the target). No headings, no bullet lists, no jargon
explanations. Respond with JSON: {"text": "<prose>"}"""

REPLY_SYSTEM = """You answer a question about an autonomous FPGA design run, in the user's language, briefly and concretely,
using the run data given (status "running" means it is still iterating; say what it is doing). The measured Pareto front
lists real place-and-route results of the same design kind on the same device from all runs: use it to answer "what is
the best achievable" questions with numbers, and say they are measured. If the message is not a
question but a request -- to change the design, constraints or device, to try something, to continue, or an instruction
for the planner -- set is_request true and leave text empty; the caller will act on it.
Respond with JSON: {"text": "<answer>", "is_request": false}"""


def _backend_lines() -> str:
    from .backends import BACKENDS as REG
    return "\n".join(f"  {b.key:7s} {b.name} ({b.toolchain}; {b.seconds_per_iter} per iteration; {len(b.sim_stages)}-stage sim ladder). "
                     f"{b.dsp} {b.dsp_kind}, {b.lut // 1000}k {b.lut_kind}, {b.bram} BRAM. Fmax realistic {b.fmax_range}. Words: {', '.join(b.aliases)}"
                     for b in REG.values())


def _lang(text: str) -> str:
    return "zh" if re.search(r"[一-鿿]", text) else "en"


def interpret(message: str, context: dict | None = None) -> dict:
    ctx = ""
    if context:
        ctx = ("\n\n## Current run (the user may be refining it)\n" + json.dumps({
            "spec": context.get("spec"), "status": context.get("status"),
            "best": context.get("best"), "last": context.get("last")}, ensure_ascii=False)[:4000])
    r = llm.chat_json(INTERPRET_SYSTEM.replace("@BACKENDS@", _backend_lines()), f"## Request\n{message}{ctx}")
    spec = r.get("spec") or {}
    # normalize + validate against the real schema; a fork inherits everything it does not mention
    base = context["spec"] if (context and r.get("fork")) else None
    design = spec.get("design") if spec.get("design") in DESIGNS else (base["design"] if base else "matmul")
    backend = spec.get("backend") if spec.get("backend") in BACKENDS else (base["backend"] if base else "xc7s50")
    if base and base["design"] != design:
        base = None  # a different design kind: nothing to inherit
    sch = SCHEMAS[design]
    allowed = {f["name"] for f in sch["fields"]}
    locked = {**(base["locked"] if base else sch["locked_default"]), **{k: v for k, v in (spec.get("locked") or {}).items() if k in allowed}}
    cons_in = spec.get("constraints") or {}
    # only what the user implied: no silent resource caps from the form defaults
    cons = {**(base["constraints"] if base else {"fmax_mhz": sch["constraints_default"]["fmax_mhz"]}), **{k: v for k, v in cons_in.items() if k != "objective"}}
    obj = [o for o in (cons_in.get("objective") or (base["constraints"].get("objective") if base else None) or ["maximize_throughput", "minimize_lut"]) if o in OBJECTIVES]
    cons["objective"] = obj or ["maximize_throughput", "minimize_lut"]
    from .loop import merge_locked
    init = merge_locked({**(base["initial_ir"] if base else list(sch["presets"].values())[0]), **(spec.get("initial_ir") or {})}, locked)
    ir = DESIGNS[design].ir_cls.from_dict(init)
    errs = ir.validate()
    if errs:
        # keep the preset but honor the locked task fields
        init = merge_locked(dict(list(sch["presets"].values())[0]), locked)
        ir = DESIGNS[design].ir_cls.from_dict(init)
        errs = ir.validate()
    out_spec = {"task": spec.get("task") or message[:60], "design": design, "backend": backend, "locked": locked,
                "constraints": cons, "initial_ir": {k: v for k, v in ir.to_dict().items() if k != "coefs"}}
    out_spec["task"] = (base["task"] if base and not spec.get("task") else out_spec["task"])
    if isinstance(spec.get("planner"), str) and spec["planner"].strip():
        out_spec["planner"] = spec["planner"].strip()
    alts = [a for a in (r.get("alternatives") or []) if isinstance(a, dict) and a.get("request")][:2]
    return {"spec": out_spec, "fork": bool(r.get("fork")) and bool(base), "summary": r.get("summary", ""),
            "assumptions": r.get("assumptions") or [], "questions": r.get("questions") or [], "alternatives": alts, "errors": errs, "lang": _lang(message)}


def narrate(summary: dict, events: list[dict], lang: str) -> str:
    hist = []
    for e in events:
        if e["kind"] == "result":
            m = e["metrics"]
            hist.append({"iteration": e["iteration"], "action": e.get("action", "")[:300], "ir": e["ir"],
                         "fmax": m["fmax_mhz"], "lut": m["lut"], "dsp": m["dsp"], "throughput": e["throughput"],
                         "violations": e["violations"], "critical": [s for s in m.get("critical_path", [])[:3]],
                         "sim": {k: v.get("passed") for k, v in (m.get("sim_stages") or {}).items()}})
    stop = next((e for e in reversed(events) if e["kind"] == "plan" and e.get("stop")), None)
    data = {"spec": summary.get("spec"), "status": "feasible" if summary.get("best") else "no feasible design",
            "best": summary.get("best"), "throughput_unit": summary.get("throughput_unit"), "iterations": hist,
            "planner_final_note": (stop or {}).get("diagnosis", ""), "why_it_stopped": (stop or {}).get("stop_reason") or (stop or {}).get("action", ""),
            "language": "Chinese" if lang == "zh" else "English"}
    try:
        return llm.chat_json(NARRATE_SYSTEM, json.dumps(data, ensure_ascii=False)[:12000]).get("text", "")
    except Exception as e:
        return f"(narration unavailable: {e})"


def reply(message: str, context: dict, front: list[dict] | None = None) -> dict:
    data = {"question": message, "run": {k: context.get(k) for k in ("spec", "status", "best", "last", "iterations")},
            "measured_pareto_front_same_design_and_device": [{k: p[k] for k in ("fmax_mhz", "lut", "dsp", "throughput", "ir")} for p in (front or [])[:12]],
            "language": "Chinese" if _lang(message) == "zh" else "English"}
    try:
        r = llm.chat_json(REPLY_SYSTEM, json.dumps(data, ensure_ascii=False)[:12000])
        return {"text": r.get("text", ""), "is_request": bool(r.get("is_request"))}
    except Exception as e:
        return {"text": f"(unavailable: {e})", "is_request": False}
