"""Generic datapath graph: any chain of registered stream designs (fir, conv2d, cordic, fft, matmul-free), each
with its own IR, connected by valid/ready FIFOs with width adaptation between them. `fir_chain` is the special
case where every node is a FIR; `graph` lets the planner edit the knobs of heterogeneous nodes while the task
fixes the node kinds and their locked fields.

IR:
  nodes       list of {"kind": <design kind>, "ir": <that design's IR>}   (kind and the node's locked fields are task
                                                                            parameters; the planner edits the rest)
  fifo_depth  4..64        inter-node FIFO depth
  adapt       "truncate"|"round"|"saturate"   how a node's output word is fitted into the next node's input width

Supported node kinds (single-data-stream in / single-data-stream out): fir, conv2d (pixel stream; frame_start is
generated from a width counter inside the adapter), cordic (x in, x/y/z out -- only x is forwarded), fft (re in,
im = 0; only re forwarded). Each node's generated module set is renamed n<i>_* so several of the same kind coexist.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field



def DESIGNS_REF():
    from .designs import DESIGNS
    return DESIGNS


KINDS = ("fir", "conv2d", "cordic", "fft")


@dataclass(frozen=True)
class GraphArch:
    nodes: tuple[dict, ...] = field(default_factory=tuple)   # [{"kind":..., "ir": {...}}, ...]
    fifo_depth: int = 16
    adapt: str = "truncate"

    def node_ir(self, i: int):
        n = self.nodes[i]
        return DESIGNS_REF()[n["kind"]].ir_cls.from_dict(n["ir"])

    def validate(self) -> list[str]:
        errs = []
        if not 2 <= len(self.nodes) <= 6:
            errs.append("a graph has 2..6 nodes")
        for i, n in enumerate(self.nodes):
            if n.get("kind") not in KINDS:
                errs.append(f"node {i}: kind must be one of {KINDS}")
                continue
            errs += [f"node {i} ({n['kind']}): {e}" for e in self.node_ir(i).validate()]
        if self.fifo_depth not in (4, 8, 16, 32, 64):
            errs.append("fifo_depth must be 4, 8, 16, 32 or 64")
        # a FIFO must be able to hold the in-flight contents of the node feeding it (nodes have no back-pressure
        # input; the graph gates the *source* instead)
        for i in range(len(self.nodes) - 1):
            try:
                lat = DESIGNS_REF()[self.nodes[i]["kind"]].latency(self.node_ir(i))
            except Exception:
                continue
            if lat + 2 >= self.fifo_depth:
                errs.append(f"fifo_depth {self.fifo_depth} is too shallow for node {i}'s pipeline latency {lat} (need > latency+2)")
        if self.adapt not in ("truncate", "round", "saturate"):
            errs.append("adapt must be truncate, round or saturate")
        return errs

    def to_dict(self) -> dict:
        return {"nodes": [{"kind": n["kind"], "ir": self.node_ir(i).to_dict()} for i, n in enumerate(self.nodes)],
                "fifo_depth": self.fifo_depth, "adapt": self.adapt}

    @classmethod
    def from_dict(cls, d: dict) -> "GraphArch":
        nodes = tuple({"kind": n["kind"], "ir": dict(n.get("ir", {}))} for n in d.get("nodes", []))
        return cls(nodes=nodes, fifo_depth=int(d.get("fifo_depth", 16)), adapt=d.get("adapt", "truncate"))

    def digest(self, backend: str = "ecp5") -> str:
        parts = [self.node_ir(i).digest(backend) for i in range(len(self.nodes))]
        return hashlib.sha256(json.dumps([parts, self.fifo_depth, self.adapt]).encode()).hexdigest()[:12]


def _io(kind: str, ir) -> dict:
    """Stream port names and widths of a node kind."""
    if kind == "fir":
        return {"in": ("in_data", ir.data_width), "out": ("out_data", ir.acc_width), "signed": True}
    if kind == "conv2d":
        return {"in": ("in_data", ir.data_width), "out": ("out_data", ir.acc_width), "signed": True, "frame": ir.width}
    if kind == "cordic":
        return {"in": ("in_x", ir.width), "out": ("out_x", ir.width), "signed": True, "cordic": True}
    if kind == "fft":
        return {"in": ("in_re", ir.data_width), "out": ("out_re", ir.out_width), "signed": True, "fft": ir.n}
    raise ValueError(kind)


def throughput(a: GraphArch) -> float:
    return round(min(DESIGNS_REF()[n["kind"]].throughput(a.node_ir(i)) for i, n in enumerate(a.nodes)), 4)


def latency_cycles(a: GraphArch) -> int:
    return sum(DESIGNS_REF()[n["kind"]].latency(a.node_ir(i)) for i, n in enumerate(a.nodes)) + 2 * (len(a.nodes) - 1)


def data_width(a: GraphArch) -> int:
    return _io(a.nodes[0]["kind"], a.node_ir(0))["in"][1]


def out_width(a: GraphArch) -> int:
    i = len(a.nodes) - 1
    return _io(a.nodes[i]["kind"], a.node_ir(i))["out"][1]


def _rename_modules(text: str, prefix: str) -> str:
    names = sorted(set(re.findall(r"^\s*module\s+(\w+)", text, re.M)), key=len, reverse=True)
    for n in names:
        text = re.sub(rf"\b{n}\b", f"{prefix}_{n}", text)
    return text


def emit_graph(a: GraphArch, backend: str = "ecp5") -> str:
    designs = DESIGNS_REF()
    parts = [f"// Generated by silicon-agent -- do not edit. IR: {a.to_dict()}\n", "typedef logic signed [63:0] sd;\n"]
    for i, n in enumerate(a.nodes):
        body = designs[n["kind"]].emit(a.node_ir(i), backend=backend)
        body = body.split("\n", 1)[1].replace("`default_nettype none\n", "").replace("`default_nettype wire\n", "").replace("typedef logic signed [63:0] sd;\n", "")
        parts.append(_rename_modules(body, f"n{i}"))
    N = len(a.nodes)
    ios = [_io(n["kind"], a.node_ir(i)) for i, n in enumerate(a.nodes)]
    DW0, OWn = ios[0]["in"][1], ios[-1]["out"][1]
    inst = []
    for i, (n, io) in enumerate(zip(a.nodes, ios)):
        kind = n["kind"]; top = designs[kind].top
        src_v = "in_valid & gate" if i == 0 else f"q{i}_valid"
        src_d = "in_data" if i == 0 else f"q{i}_data"
        rdy = f"n{i}_in_ready"
        ov, od = f"n{i}_ov", f"n{i}_od"
        inw, outw = io["in"][1], io["out"][1]
        if kind == "fir":
            extra = ", .coef_we(1'b0), .coef_addr('0), .coef_data('0)" if getattr(a.node_ir(i), "coef_port", False) else ""
            inst.append(f"    n{i}_{top} u_n{i} (.clk(clk), .rst(rst), .in_valid({src_v}), .in_data({src_d}), .in_ready({rdy}), .out_valid({ov}), .out_data({od}){extra});")
        elif kind == "conv2d":
            W = io["frame"]
            inst.append(f"""    // frame_start for the conv node: every {W} accepted samples starts a new row... the node needs a frame start
    // once per frame; here frames are `rows` rows of {W} pixels, rows = the number the upstream delivers -- we mark a
    // frame start on the very first sample and whenever `in_frame` is asserted at the graph input (passed down the chain)
    logic n{i}_fs;
    n{i}_{top} u_n{i} (.clk(clk), .rst(rst), .in_valid({src_v}), .frame_start(n{i}_fs), .in_data({src_d}), .in_ready({rdy}), .out_valid({ov}), .out_data({od}));""")
        elif kind == "cordic":
            inst.append(f"    logic signed [{inw - 1}:0] n{i}_y, n{i}_z; logic signed [{outw - 1}:0] n{i}_oy, n{i}_oz; assign n{i}_y = '0; assign n{i}_z = '0;")
            inst.append(f"    n{i}_{top} u_n{i} (.clk(clk), .rst(rst), .in_valid({src_v}), .in_x({src_d}), .in_y(n{i}_y), .in_z(n{i}_z), .in_ready({rdy}), .out_valid({ov}), .out_x({od}), .out_y(n{i}_oy), .out_z(n{i}_oz));")
        elif kind == "fft":
            inst.append(f"    logic signed [{inw - 1}:0] n{i}_im; assign n{i}_im = '0; logic signed [{outw - 1}:0] n{i}_oim; logic n{i}_ff;")
            inst.append(f"    n{i}_{top} u_n{i} (.clk(clk), .rst(rst), .in_valid({src_v}), .in_first(n{i}_ff), .in_re({src_d}), .in_im(n{i}_im), .in_ready({rdy}), .out_valid({ov}), .out_re({od}), .out_im(n{i}_oim));")
        if i < N - 1:
            nxt_w = ios[i + 1]["in"][1]
            inst.append(_adapter(i, outw, nxt_w, a.adapt))
            inst.append(f"""    logic q{i + 1}_valid, q{i + 1}_ready; logic signed [{nxt_w - 1}:0] q{i + 1}_data; logic [$clog2(FIFO+1)-1:0] q{i + 1}_count;
    sa_graph_fifo #(.W({nxt_w}), .D(FIFO)) u_q{i + 1} (.clk(clk), .rst(rst), .wr({ov}), .wdata(n{i}_adp),
        .rd(q{i + 1}_valid & q{i + 1}_ready), .rdata(q{i + 1}_data), .valid(q{i + 1}_valid), .count(q{i + 1}_count));
    assign q{i + 1}_ready = n{i + 1}_in_ready;
    // headroom: everything already inside node {i}'s pipeline will still land in this FIFO after `gate` drops
    assign n{i}_room = (q{i + 1}_count + {min(designs[kind].latency(a.node_ir(i)) + 2, a.fifo_depth - 1)} <= FIFO);""")
    # frame/first markers: counted at the graph input from in_frame; conv/fft nodes get their marker from sample counting
    markers = []
    for i, (n, io) in enumerate(zip(a.nodes, ios)):
        if n["kind"] == "conv2d":
            src_v = "in_valid & gate & n0_in_ready" if i == 0 else f"q{i}_valid & q{i}_ready"
            markers.append(f"""    // conv node {i}: frame start = first accepted sample after reset or after in_frame
    logic n{i}_seen; always_ff @(posedge clk) if (rst) n{i}_seen <= 0; else if ({src_v}) n{i}_seen <= 1;
    assign n{i}_fs = ({src_v}) & (~n{i}_seen | frame_q{i});""")
        if n["kind"] == "fft":
            src_v = "in_valid & gate & n0_in_ready" if i == 0 else f"q{i}_valid & q{i}_ready"
            markers.append(f"""    // fft node {i}: first = every {io['fft']}-th accepted sample
    logic [$clog2({io['fft']})-1:0] n{i}_cnt; always_ff @(posedge clk) if (rst) n{i}_cnt <= '0; else if ({src_v}) n{i}_cnt <= n{i}_cnt + 1'b1;
    assign n{i}_ff = (n{i}_cnt == 0);""")
    # in_frame travels with the data: a 1-bit sideband through the FIFOs is overkill -- we re-derive frame starts per
    # node by counting, and expose `in_frame` only to node 0 (conv as first node)
    zero = "1'b0"
    frame_sidebands = "\n".join(f"    logic frame_q{i}; assign frame_q{i} = {'in_frame' if i == 0 else zero};" for i, n in enumerate(a.nodes) if n["kind"] == "conv2d")
    rooms = " & ".join(f"n{i}_room" for i in range(N - 1)) or "1'b1"
    top = f"""
module graph_top #(
    parameter int DW = {DW0},
    parameter int AW = {OWn},
    parameter int FIFO = {a.fifo_depth}
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire                   in_valid,
    input  wire                   in_frame,     // first sample of a frame (only meaningful when node 0 is conv2d)
    input  wire signed [DW-1:0]   in_data,
    output logic                  in_ready,
    output logic                  out_valid,
    output logic signed [AW-1:0]  out_data
);
{chr(10).join(f"    logic n{i}_room;" for i in range(N - 1))}
{chr(10).join(f"    logic n{i}_in_ready, n{i}_ov; logic signed [{ios[i]['out'][1] - 1}:0] n{i}_od;" for i in range(N))}
    wire gate = {rooms};
{frame_sidebands}
{chr(10).join(inst)}
{chr(10).join(markers)}
    assign in_ready  = n0_in_ready & gate;
    assign out_valid = n{N - 1}_ov;
    assign out_data  = n{N - 1}_od;
endmodule

module sa_graph_fifo #(parameter int W = 8, parameter int D = 16) (
    input wire clk, input wire rst, input wire wr, input wire [W-1:0] wdata,
    input wire rd, output logic [W-1:0] rdata, output logic valid, output logic [$clog2(D+1)-1:0] count);
    logic [W-1:0] mem [0:D-1]; logic [$clog2(D)-1:0] wp, rp;
    always_ff @(posedge clk) begin
        if (rst) begin wp <= '0; rp <= '0; count <= '0; end
        else begin
            if (wr) begin mem[wp] <= wdata; wp <= wp + 1'b1; end
            if (rd & valid) rp <= rp + 1'b1;
            count <= count + (wr ? 1 : 0) - ((rd & valid) ? 1 : 0);
        end
    end
    assign valid = count != 0;
    assign rdata = mem[rp];
endmodule
"""
    return "`default_nettype none\n" + "\n".join(parts) + top + "`default_nettype wire\n"


def _adapter(i: int, ow: int, nw: int, mode: str) -> str:
    """Fit node i's ow-bit output into the next node's nw-bit input: top bits (truncate/round) or saturate."""
    if ow == nw:
        return f"    logic signed [{nw - 1}:0] n{i}_adp; assign n{i}_adp = n{i}_od;"
    if ow < nw:
        return f"    logic signed [{nw - 1}:0] n{i}_adp; assign n{i}_adp = {nw}'(n{i}_od);"
    sh = ow - nw
    if mode == "saturate":
        return f"""    logic signed [{nw - 1}:0] n{i}_adp;
    always_comb begin
        if (n{i}_od > {ow}'(sd'({(1 << (nw - 1)) - 1}))) n{i}_adp = {nw}'(sd'({(1 << (nw - 1)) - 1}));
        else if (n{i}_od < {ow}'(sd'({-(1 << (nw - 1))}))) n{i}_adp = {nw}'(sd'({-(1 << (nw - 1))}));
        else n{i}_adp = n{i}_od[{nw - 1}:0];
    end"""
    rc = f"{ow}'(1 << {sh - 1})" if mode == "round" else f"{ow}'(0)"
    return f"""    logic signed [{ow - 1}:0] n{i}_full; always_comb n{i}_full = n{i}_od + {rc};
    logic signed [{nw - 1}:0] n{i}_adp; assign n{i}_adp = n{i}_full[{ow - 1}:{sh}];"""
