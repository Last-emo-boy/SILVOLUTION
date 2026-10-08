"""Build the silicon-agent pitch deck (docs/silicon-agent-pitch.pptx) with python-pptx.
All numbers are from real runs on the netcup EDA host (results/ on the server)."""

from pptx import Presentation
from pptx.chart.data import XyChartData, CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt

BG = RGBColor(0x0E, 0x0E, 0x0D); SURF = RGBColor(0x1C, 0x1C, 0x1B); SURF2 = RGBColor(0x26, 0x26, 0x24); BORDER = RGBColor(0x33, 0x33, 0x31)
TEXT = RGBColor(0xEC, 0xEC, 0xE6); TEXT2 = RGBColor(0xA9, 0xA8, 0x9F); MUTED = RGBColor(0x6F, 0x6E, 0x67)
ACCENT = RGBColor(0x4A, 0x90, 0xE2); GOOD = RGBColor(0x34, 0xC7, 0x59); BAD = RGBColor(0xE0, 0x52, 0x52); WARN = RGBColor(0xD9, 0xA2, 0x1B); ORANGE = RGBColor(0xD9, 0x59, 0x26)
FONT = "PingFang SC"; MONO = "Menlo"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
W, H = prs.slide_width, prs.slide_height
BLANK = prs.slide_layouts[6]


def slide():
    s = prs.slides.add_slide(BLANK)
    bg = s.background.fill; bg.solid(); bg.fore_color.rgb = BG
    return s


def text(s, x, y, w, h, runs, size=18, color=TEXT, bold=False, align=PP_ALIGN.LEFT, font=FONT, anchor=MSO_ANCHOR.TOP, spacing=1.15):
    """runs: str, or list of (str, {overrides}) or list of paragraphs (each a str or list of runs)."""
    tb = s.shapes.add_textbox(x, y, w, h); tf = tb.text_frame; tf.word_wrap = True; tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Emu(0); tf.margin_top = tf.margin_bottom = Emu(0)
    paras = runs if isinstance(runs, list) and runs and (isinstance(runs[0], list) or isinstance(runs[0], str)) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align; p.line_spacing = spacing
        items = para if isinstance(para, list) else [(para, {})]
        for it in items:
            t, o = it if isinstance(it, tuple) else (it, {})
            r = p.add_run(); r.text = t
            r.font.size = Pt(o.get("size", size)); r.font.bold = o.get("bold", bold); r.font.name = o.get("font", font)
            r.font.color.rgb = o.get("color", color)
    return tb


def box(s, x, y, w, h, fill=SURF, line=BORDER, radius=True):
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE, x, y, w, h)
    if radius: sh.adjustments[0] = 0.06
    sh.fill.solid(); sh.fill.fore_color.rgb = fill
    if line: sh.line.color.rgb = line; sh.line.width = Pt(0.75)
    else: sh.line.fill.background()
    sh.shadow.inherit = False
    sh.text_frame.margin_left = sh.text_frame.margin_right = Emu(0)
    return sh


def header(s, title, kicker=None, n=None):
    if kicker: text(s, Inches(0.7), Inches(0.45), Inches(8), Inches(0.3), kicker, size=12, color=ACCENT, bold=True, font=MONO)
    text(s, Inches(0.7), Inches(0.75), Inches(11.5), Inches(0.8), title, size=30, color=TEXT, bold=True)
    if n is not None: text(s, W - Inches(1.3), H - Inches(0.5), Inches(0.6), Inches(0.3), str(n), size=10, color=MUTED, font=MONO, align=PP_ALIGN.RIGHT)
    text(s, Inches(0.7), H - Inches(0.5), Inches(3), Inches(0.3), "▣ silicon-agent", size=10, color=MUTED, font=MONO)


def card(s, x, y, w, h, title, body, title_color=TEXT, body_size=13, mono_title=False):
    box(s, x, y, w, h)
    text(s, x + Inches(0.22), y + Inches(0.18), w - Inches(0.44), Inches(0.4), title, size=15, color=title_color, bold=True, font=MONO if mono_title else FONT)
    text(s, x + Inches(0.22), y + Inches(0.62), w - Inches(0.44), h - Inches(0.8), body, size=body_size, color=TEXT2)


def chip(s, x, y, label, color=TEXT2, fill=SURF2, size=11):
    w = Inches(0.12 + 0.085 * len(label))
    b = box(s, x, y, w, Inches(0.3), fill=fill, line=None)
    b.adjustments[0] = 0.5
    tf = b.text_frame; tf.vertical_anchor = MSO_ANCHOR.MIDDLE; tf.margin_top = tf.margin_bottom = Emu(0)
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER; r = p.add_run(); r.text = label; r.font.size = Pt(size); r.font.name = MONO; r.font.color.rgb = color
    return w


def style_chart(ch, legend=True):
    ch.has_legend = legend
    if legend:
        ch.legend.position = XL_LEGEND_POSITION.BOTTOM; ch.legend.include_in_layout = False
        ch.legend.font.size = Pt(10); ch.legend.font.color.rgb = TEXT2; ch.legend.font.name = FONT
    ch.font.color.rgb = TEXT2; ch.font.size = Pt(10); ch.font.name = FONT
    for ax in (ch.category_axis, ch.value_axis):
        ax.tick_labels.font.color.rgb = TEXT2; ax.tick_labels.font.size = Pt(10)
        ax.format.line.color.rgb = BORDER
        ax.has_major_gridlines = False
    ch.value_axis.has_major_gridlines = True; ch.value_axis.major_gridlines.format.line.color.rgb = SURF2


n = 0
# ------------------------------------------------------------------ 1. title
s = slide(); n += 1
text(s, Inches(0.9), Inches(2.3), Inches(11), Inches(0.5), "▣ silicon-agent", size=16, color=ACCENT, bold=True, font=MONO)
text(s, Inches(0.9), Inches(2.8), Inches(11.8), Inches(1.0), "一句话，换一份签核过的 Verilog", size=48, color=TEXT, bold=True)
text(s, Inches(0.9), Inches(4.1), Inches(11), Inches(1.2), "自然语言 → 架构 IR → RTL → 仿真 → 综合 → 布局布线 → 诊断 → 下一轮，直到时序和资源约束全部满足。\n真实 EDA 工具是唯一的 ground truth：没有一个数字是估出来的。", size=18, color=TEXT2)
text(s, Inches(0.9), Inches(6.3), Inches(11), Inches(0.4), "2026-10 · 路演 · w33d", size=12, color=MUTED, font=MONO)

# ------------------------------------------------------------------ 2. problem
s = slide(); n += 1; header(s, "问题：从「想要一个加速器」到「能跑的 RTL」是几天的人肉循环", "01 · 问题", n)
steps = [("写 RTL", "手写几百行 SystemVerilog，一个流水级改动牵一发动全身"), ("写测试", "testbench、golden model、覆盖率，比 RTL 还多"), ("跑综合 / P&R", "ECP5 30 秒，Vivado 几分钟；每天只能试十几种结构"), ("看时序报告", "关键路径在哪一级？是乘法器、进位链还是布线？"), ("改架构", "折叠、流水、DSP 寄存器配置……经验决定一切"), ("再来一遍", "一个 FIR 调到 250 MHz 要试 5–10 版本")]
for i, (t, b) in enumerate(steps):
    x = Inches(0.7 + (i % 3) * 4.05); y = Inches(1.9 + (i // 3) * 2.1)
    card(s, x, y, Inches(3.8), Inches(1.85), f"{i + 1}  {t}", b)
text(s, Inches(0.7), Inches(6.25), Inches(12), Inches(0.6), "LLM 直接写 Verilog 并不解决这个问题：它写出来的代码没法保证可综合、可仿真，更不知道自己写的东西在芯片上跑多快。", size=15, color=WARN)

# ------------------------------------------------------------------ 3. idea
s = slide(); n += 1; header(s, "思路：让 LLM 改「架构」，不让它写「代码」", "02 · 核心思路", n)
card(s, Inches(0.7), Inches(1.8), Inches(3.9), Inches(4.3), "架构 IR", [[("一个不可变的 dataclass，十几个旋钮：", {})], [("pe_rows · pe_cols · acc_width · pipe_mul · reg_in · dsp_regs · acc_in_dsp · fold · symmetric …", {"font": MONO, "size": 11, "color": TEXT})], [("", {})], [("LLM 每轮只输出一个新的 IR（JSON）。它不写 RTL，不写 testbench。", {})]])
card(s, Inches(4.75), Inches(1.8), Inches(3.9), Inches(4.3), "确定性生成器", [[("IR → SystemVerilog，构造即正确。", {})], [("接口是 latency-insensitive 的 valid/ready 流：无论怎么加流水级、折叠，testbench 不用改。", {})], [("", {})], [("ECP5：显式例化 MULT18X18D；Spartan-7：行为级 DSP48E1 推断 + 属性控制。", {})]])
card(s, Inches(8.8), Inches(1.8), Inches(3.9), Inches(4.3), "EDA 是 ground truth", [[("cocotb + iverilog 对 Python 黄金模型；yosys + nextpnr / Vivado 2025.2 真实布局布线。", {})], [("", {})], [("Fmax、LUT、DSP、关键路径逐级分解——全部来自工具报告，喂回 LLM 做诊断。", {})], [("", {})], [("约束 = 硬过滤；目标 = 字典序。", {})]])
text(s, Inches(0.7), Inches(6.35), Inches(12), Inches(0.5), "结果：LLM 的每一个决定都被真实工具立刻打分；它只能在一个「永远可综合、永远可仿真」的空间里搜索。", size=15, color=ACCENT)

# ------------------------------------------------------------------ 4. loop diagram
s = slide(); n += 1; header(s, "闭环：规划 → 生成 → 仿真 → 综合/P&R → 诊断 → 学习", "03 · 系统", n)
nodes = [("用户\n自然语言", MUTED), ("nl.interpret\n→ 规格", ACCENT), ("规划器\n(DeepSeek)\n≤3 个候选 IR", ACCENT), ("筛选\n闭式估算 + 校准", WARN), ("生成器\nIR → SV", TEXT2), ("仿真阶梯\n行为级 … 实现后时序", GOOD), ("综合 + P&R\nnextpnr / Vivado", ORANGE), ("诊断\n关键路径分解", BAD), ("设计记忆\n经验沉淀 / 召回", GOOD)]
x0, y0, bw, bh, gap = Inches(0.6), Inches(2.3), Inches(1.28), Inches(1.3), Inches(0.1)
for i, (t, c) in enumerate(nodes):
    x = x0 + i * (bw + gap)
    b = box(s, x, y0, bw, bh, fill=SURF, line=c); tf = b.text_frame; tf.vertical_anchor = MSO_ANCHOR.MIDDLE; tf.word_wrap = True
    for j, line in enumerate(t.split("\n")):
        p = tf.paragraphs[0] if j == 0 else tf.add_paragraph(); p.alignment = PP_ALIGN.CENTER
        r = p.add_run(); r.text = line; r.font.size = Pt(10 if j else 11); r.font.bold = j == 0; r.font.color.rgb = TEXT if j == 0 else TEXT2; r.font.name = FONT
    if i < len(nodes) - 1:
        text(s, x + bw - Inches(0.02), y0 + Inches(0.5), gap + Inches(0.05), Inches(0.3), "›", size=14, color=MUTED, align=PP_ALIGN.CENTER)
# feedback arrow label
text(s, Inches(2.6), Inches(3.8), Inches(9), Inches(0.4), "◄── 诊断 + 召回的经验 + 用户插话，进入下一轮的规划提示 ──", size=11, color=MUTED, font=MONO, align=PP_ALIGN.CENTER)
bul = [["• ", ("候选筛选：", {"bold": True, "color": TEXT}), "规划器一次给 ≤3 个方案，闭式估算排序，只有第一名进 EDA；估算用岭回归按历史实测自动校准（matmul/xc7s50 的 LUT 误差 105% → 31%）。"],
       ["• ", ("锁定字段：", {"bold": True, "color": TEXT}), "data_width / taps / cutoff 是任务定义，规划器不能为了省 LUT 把 INT8 改成 INT4。"],
       ["• ", ("可插话：", {"bold": True, "color": TEXT}), "运行中用户的一句话会以「工程师指令」进入下一轮提示。"],
       ["• ", ("可恢复：", {"bold": True, "color": TEXT}), "SIGTERM 时做完当前轮，重启后从 EDA 缓存 + 规划回复缓存零秒重放；部署不丢任务。"]]
text(s, Inches(0.7), Inches(4.4), Inches(12), Inches(2.4), bul, size=13, color=TEXT2, spacing=1.3)

# ------------------------------------------------------------------ 5. sim ladder
s = slide(); n += 1; header(s, "五级仿真阶梯：时序仿真失败也是一种「违反约束」", "04 · 验证", n)
rows = [("1", "行为级仿真", "RTL 逻辑 vs Python 黄金模型", "cocotb + iverilog", "ECP5 · XC7S50"), ("2", "综合后功能仿真", "综合网表，无延迟", "iverilog(ECP5 网表) / xsim", "ECP5 · XC7S50"), ("3", "综合后时序仿真", "综合网表 + 估算 SDF 延迟，目标周期", "xsim", "XC7S50"), ("4", "实现后功能仿真", "布线后网表，无延迟", "xsim", "XC7S50"), ("5", "实现后时序仿真", "布线后网表 + 真实 SDF 延迟，目标周期 — 签核", "xsim", "XC7S50")]
y = Inches(1.9)
for i, (k, name, purpose, tool, dev) in enumerate(rows):
    box(s, Inches(0.7), y, Inches(12), Inches(0.72), fill=SURF if i % 2 == 0 else BG, line=None)
    text(s, Inches(0.9), y + Inches(0.18), Inches(0.5), Inches(0.4), k, size=16, color=ACCENT, bold=True, font=MONO)
    text(s, Inches(1.4), y + Inches(0.12), Inches(3), Inches(0.5), name, size=15, color=TEXT, bold=True)
    text(s, Inches(4.3), y + Inches(0.18), Inches(5), Inches(0.5), purpose, size=12, color=TEXT2)
    text(s, Inches(9.2), y + Inches(0.18), Inches(2.2), Inches(0.5), tool, size=11, color=MUTED, font=MONO)
    text(s, Inches(11.3), y + Inches(0.18), Inches(1.5), Inches(0.5), dev, size=10, color=MUTED, font=MONO)
    y += Inches(0.76)
text(s, Inches(0.7), Inches(5.85), Inches(12), Inches(1.0), [["真实案例：8×8 无流水 INT8 阵列 @ 300 MHz 目标——行为级、综合后/实现后功能仿真全部通过，但 ", ("两级时序仿真失败", {"color": BAD, "bold": True}), "；规划器据此加 DSP 寄存器后五级全过。"], ["向量 testbench 复用 cocotb 黄金模型（本版 cocotb 无 xsim VPI）；glbl.GSR 100 ns、负沿驱动 3/4 周期采样、Vivado 2025.2 网表注释多行 os-release 等坑都已踩平。"]], size=12.5, color=TEXT2, spacing=1.3)

# ------------------------------------------------------------------ 6. results table
s = slide(); n += 1; header(s, "真实结果：7 个任务 · 2 种器件 · 2 类设计，全部由真实 EDA 签核", "05 · 结果", n)
res = [("INT8 matmul", "ECP5", "≥180 MHz · ≤48 DSP", "5", "4×12 · 188.5 MHz · 48 DSP · 48 MAC/cyc", GOOD),
       ("INT8 matmul", "XC7S50", "≥200 MHz · ≤64 DSP", "3", "8×8 · 239.6 MHz · 64 DSP · 1005 LUT", GOOD),
       ("INT8 matmul", "XC7S50", "≥300 MHz · ≤32 DSP", "3", "2×16 · 319.5 MHz · 32 DSP（acc 移出 DSP 后）", GOOD),
       ("INT8 matmul · 五级仿真", "XC7S50", "≥300 MHz · ≤32 DSP", "3", "2×16 · 315.0 MHz · 五级仿真全过", GOOD),
       ("32 阶 FIR", "ECP5", "≥120 MHz · ≤6 DSP", "4", "对称 fold=4 · 131 MHz · 4 DSP", GOOD),
       ("32 阶 FIR", "XC7S50", "≥250 MHz · ≤8 DSP", "7", "非对称 fold=8 · 259.1 MHz · 8 DSP · 324 LUT", GOOD),
       ("INT8 matmul（聊天）", "ECP5", "≥160 MHz · ≤32 DSP · 省 LUT", "2 + 8", "1×1 216.8 MHz → 追问后 fork：1×32 · 214 MHz · 32 MAC/cyc", GOOD)]
cols = [Inches(0.7), Inches(3.3), Inches(4.5), Inches(7.4), Inches(8.2)]
for j, (h_, c) in enumerate(zip(["任务", "器件", "约束", "轮数", "最终设计（真实 P&R）"], cols)):
    text(s, c, Inches(1.85), Inches(3), Inches(0.3), h_, size=11, color=MUTED, font=MONO)
y = Inches(2.2)
for i, (task, dev, cons, it, out, c) in enumerate(res):
    box(s, Inches(0.6), y, Inches(12.2), Inches(0.52), fill=SURF if i % 2 == 0 else BG, line=None)
    text(s, cols[0], y + Inches(0.12), Inches(2.6), Inches(0.4), task, size=12.5, color=TEXT)
    text(s, cols[1], y + Inches(0.14), Inches(1.2), Inches(0.4), dev, size=11, color=TEXT2, font=MONO)
    text(s, cols[2], y + Inches(0.14), Inches(2.9), Inches(0.4), cons, size=11, color=TEXT2, font=MONO)
    text(s, cols[3], y + Inches(0.12), Inches(0.8), Inches(0.4), it, size=12.5, color=TEXT, font=MONO)
    text(s, cols[4], y + Inches(0.12), Inches(4.7), Inches(0.4), [[("✓ ", {"color": c, "bold": True}), out]], size=12, color=TEXT)
    y += Inches(0.56)
text(s, Inches(0.7), Inches(6.35), Inches(12), Inches(0.6), "每轮成本：ECP5 15–60 秒，XC7S50 1.5–4 分钟（含五级仿真）。规划器 DeepSeek（reasoning），每轮一次调用。", size=12, color=MUTED)

# ------------------------------------------------------------------ 7. trajectory charts (real data)
s = slide(); n += 1; header(s, "两条真实轨迹：规划器是怎么「想」的", "06 · 轨迹", n)
# FIR xc7s50 250 MHz / 8 DSP
cd = CategoryChartData(); cd.categories = ["1", "2", "3", "4", "5", "6", "7"]
cd.add_series("Fmax (MHz)", (495.5, 163.1, 150.0, 236.6, 225.5, 152.1, 259.1))
gf = s.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Inches(0.6), Inches(1.75), Inches(6.1), Inches(3.3), cd); ch = gf.chart
style_chart(ch, legend=False); ch.has_title = True; ch.chart_title.text_frame.text = "32 阶 FIR · XC7S50 · ≥250 MHz, ≤8 DSP · Fmax/轮"
ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(12); ch.chart_title.text_frame.paragraphs[0].runs[0].font.color.rgb = TEXT
ser = ch.plots[0].series[0]; ser.format.line.color.rgb = ACCENT; ser.format.line.width = Pt(2); ser.marker.style = XL_MARKER_STYLE.CIRCLE; ser.marker.size = 7; ser.marker.format.fill.solid(); ser.marker.format.fill.fore_color.rgb = ACCENT
ser.smooth = False
for idx, col in ((0, BAD), (1, BAD), (2, BAD), (3, BAD), (4, BAD), (5, BAD), (6, GOOD)):
    pt = ser.points[idx]; pt.marker.format.fill.solid(); pt.marker.format.fill.fore_color.rgb = col; pt.marker.style = XL_MARKER_STYLE.CIRCLE; pt.marker.size = 8
text(s, Inches(0.7), Inches(5.1), Inches(6), Inches(1.9), [["1 ", ("转置形式 1 样本/周期：495 MHz 但 45 DSP（Vivado 把整条 FIR 吸进 DSP 级联）", {})], ["2–3 ", ("对称 + fold=2：DSP 降到 8，但乘法器操作数选择器成了墙（150–163 MHz）", {})], ["4–6 ", ("fold=4 / 8、改 DSP 寄存器模式：236 / 225 / 152 MHz，都差一点", {})], ["7 ", ("放弃对称共享、fold=8：", {}), ("259.1 MHz · 8 DSP · 324 LUT ✓", {"color": GOOD, "bold": True})]], size=11.5, color=TEXT2, spacing=1.25)
# matmul 300 MHz full ladder
cd2 = CategoryChartData(); cd2.categories = ["1", "2", "3"]
cd2.add_series("Fmax (MHz)", (189.8, 296.3, 315.0))
gf2 = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(6.9), Inches(1.75), Inches(5.9), Inches(3.3), cd2); ch2 = gf2.chart
style_chart(ch2, legend=False); ch2.has_title = True; ch2.chart_title.text_frame.text = "INT8 matmul · XC7S50 · ≥300 MHz, ≤32 DSP · Fmax/轮"
ch2.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(12); ch2.chart_title.text_frame.paragraphs[0].runs[0].font.color.rgb = TEXT
ser2 = ch2.plots[0].series[0]; ch2.plots[0].gap_width = 120
for idx, col in ((0, BAD), (1, WARN), (2, GOOD)):
    pt = ser2.points[idx]; pt.format.fill.solid(); pt.format.fill.fore_color.rgb = col
text(s, Inches(7.0), Inches(5.1), Inches(5.8), Inches(1.9), [["1 ", ("8×8 无流水：189.8 MHz，64 DSP，", {}), ("两级时序仿真失败", {"color": BAD})], ["2 ", ("4×8 · DSP 寄存器 in_pipe_out · 累加器移出 DSP（acc_in_dsp=false）：296.3 MHz，五级全过，差 4 MHz", {})], ["3 ", ("2×16 同配置：", {}), ("315.0 MHz · 32 DSP · 1994 LUT ✓", {"color": GOOD, "bold": True})], [("acc_in_dsp 这个旋钮来自一次实测扫描：DSP48E1 的 M→P 级 2.31 ns 是 277 MHz 的天花板。", {"color": MUTED})]], size=11.5, color=TEXT2, spacing=1.25)

# ------------------------------------------------------------------ 8. design memory
s = slide(); n += 1; header(s, "设计记忆：每一步都沉淀成可召回、可验证的经验", "07 · 学习", n)
card(s, Inches(0.7), Inches(1.8), Inches(6.0), Inches(2.6), "一条「经验」长什么样", [[("情境：", {"color": TEXT, "bold": True}), "违反类型 + 关键路径主导级 + 结构标签"], [("动作：", {"color": TEXT, "bold": True}), "IR 的差分（改了哪些旋钮）"], [("效果：", {"color": TEXT, "bold": True}), "实测 ΔFmax / ΔLUT / ΔDSP"], [("一句话：", {"color": TEXT, "bold": True}), "由 LLM 从以上三者蒸馏，例如——"], [("「ECP5 matmul 受 MULT18X18D 直灌 32 位进位链限制在 76.6 MHz 时，dsp_regs=in_pipe_out + pipe_mul=1 + reg_in 把乘法器隔离，Fmax 升到 216.8 MHz。」", {"font": FONT, "size": 11.5, "color": TEXT})]], body_size=12.5)
card(s, Inches(6.9), Inches(1.8), Inches(5.8), Inches(2.6), "闭环，不是日志", [["• 每轮规划前按特征重叠召回 top-6，写进提示"], ["• 规划器如果照做了：后一轮的实测自动 ", ("确认", {"color": GOOD}), " 或 ", ("推翻", {"color": BAD}), " 这条经验"], ["• 评分 = 确认 − 1.5×推翻 + 0.5×人工投票（UI 上 👍👎）"], ["• 现有 39 条经验（matmul/FIR × ECP5/XC7S50），从历史运行回填"]], body_size=12.5)
cd3 = CategoryChartData(); cd3.categories = ["有记忆", "无记忆"]; cd3.add_series("首次可行所需轮数", (2, 3))
gf3 = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.7), Inches(4.6), Inches(5.6), Inches(2.3), cd3); ch3 = gf3.chart
style_chart(ch3, legend=False); ch3.has_title = True; ch3.chart_title.text_frame.text = "A/B：同一规格（XC7S50 · ≥280 MHz · ≤48 DSP）"
ch3.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(11); ch3.chart_title.text_frame.paragraphs[0].runs[0].font.color.rgb = TEXT
ch3.plots[0].gap_width = 80; ch3.plots[0].series[0].format.fill.solid(); ch3.plots[0].series[0].format.fill.fore_color.rgb = ACCENT
ch3.plots[0].has_data_labels = True; ch3.plots[0].data_labels.font.color.rgb = TEXT; ch3.plots[0].data_labels.font.size = Pt(11)
text(s, Inches(6.9), Inches(4.7), Inches(5.8), Inches(2.2), [["有记忆的那次在第 2 轮就引用了经验 id 直接给出可行设计（295.3 MHz / 48 DSP）；无记忆的要到第 3 轮。"], [("估算校准同理：每次 EDA 结果都记录（特征, 估算, 实测），岭回归 + LOO 选 λ，只有打败原始估算时才启用。", {"color": MUTED})]], size=12.5, color=TEXT2, spacing=1.3)

# ------------------------------------------------------------------ 9. product: chat
s = slide(); n += 1; header(s, "产品形态：一个像 Claude 的聊天，细节折叠在卡片里", "08 · 产品", n)
# real screenshot of the deployed chat (thread with a fork), captured headless
pic = s.shapes.add_picture("docs/img/chat.png", Inches(0.7), Inches(1.8), width=Inches(7.3))
pic.line.color.rgb = BORDER; pic.line.width = Pt(0.75)
# right: bullets
text(s, Inches(8.3), Inches(1.85), Inches(4.5), Inches(5.0), [[("输入只有自然语言。", {"color": TEXT, "bold": True}), " nl.interpret 产出完整规格：设计类型、器件、锁定参数、约束、目标顺序、朴素起点，并说明假设。"], [""], [("追问即迭代。", {"color": TEXT, "bold": True}), " 同一会话里再说一句：改约束 → 从上一次最佳设计 fork；运行中 → 作为工程师指令插话；提问 → 直接回答。"], [""], [("细节按需展开。", {"color": TEXT, "bold": True}), " 卡片默认折叠；展开才看到每轮 IR 差分、五级仿真、关键路径、日志。"], [""], [("工程师视图。", {"color": TEXT, "bold": True}), " 每个会话有独立的 worktable：轨迹图、架构草图、IR 旋钮、比较、fork、RTL。"], [""], [("部署：", {"color": TEXT, "bold": True}), " netcup 上 Vivado 容器里的任务服务器，sluice SSO；笔记本只是瘦客户端（sa ask \"…\"）。", ]], size=12.5, color=TEXT2, spacing=1.25)

# ------------------------------------------------------------------ 9b. worktable
s = slide(); n += 1; header(s, "工程师视图：每个会话一张独立的 worktable", "08 · 产品", n)
pic = s.shapes.add_picture("docs/img/worktable.png", Inches(0.7), Inches(1.8), width=Inches(8.6))
pic.line.color.rgb = BORDER; pic.line.width = Pt(0.75)
text(s, Inches(9.6), Inches(1.85), Inches(3.3), Inches(5.0), [[("一轮一个 turn。", {"color": TEXT, "bold": True}), " 规划器的动作、IR 差分、判定行、仿真阶段；假设 / 召回的经验 / 候选与估算 / 关键路径 / 本轮经验折叠在 details 里。"], [""], [("右侧 inspector。", {"color": TEXT, "bold": True}), " 架构草图、IR 旋钮（点一下就起草一条插话）、关键路径分解条、轨迹图、五级仿真表、生成的 RTL、记忆。"], [""], [("动作。", {"color": TEXT, "bold": True}), " 从任意一轮 fork 并改约束、同会话两次运行并排比较、重跑、删除——都留在这个会话里，并在聊天里出现为一个 turn。"], [""], [("与聊天互通。", {"color": TEXT, "bold": True}), " 聊天负责「要什么」，worktable 负责「为什么」。"]], size=12.5, color=TEXT2, spacing=1.25)

# ------------------------------------------------------------------ 10. tech facts learned (moat)
s = slide(); n += 1; header(s, "护城河是「实测过的器件事实」，不是提示词", "09 · 积累", n)
facts = [("ECP5", "yosys 永远不会把流水寄存器打包进 MULT18X18D → 必须显式例化并用 REG_* 参数（76 → 147 MHz）。级联口 SRIA/SRIB 悬空否则 nextpnr 路由不了 GND 网。"),
         ("XC7S50", "8×8 乘法需要 (* use_dsp=\"yes\" *) 才进 DSP48E1；pipe_mul>0 会让 Vivado 用 2 个 DSP/PE；DSP48E1 的 M→P 级 2.31 ns 是 277 MHz 天花板，累加器移到 CARRY4 后 299–319 MHz。"),
         ("FIR", "yosys 对行为级乘法会自动常量折叠零系数、共享相等系数——symmetric 旗标没用，除非显式 DSP；Vivado 会把整条转置 FIR 吸进 DSP 级联（495 MHz，1 LUT，45 DSP）。"),
         ("仿真", "glbl.GSR 让触发器保持 100 ns；-transport_int_delays -pulse_r 0 会产生伪 X；xelab 要 unset LIBRARY_PATH；ECP5 的 cells_sim.v 重复 include，要预处理去重。"),
         ("规划器", "DeepSeek reasoning 会把 max_tokens 全烧在思考上返回空内容 → reasoning_effort 高→低回退 + 回复缓存；JSON 花括号让 str.format 炸 → @占位符@。"),
         ("LLM 的坏习惯", "为了最小化 LUT 把 INT8 改成 INT4（→ locked）；把 1×1 PE 当成「省资源」（→ 吞吐优先的解释规则）；把 160 MHz 擅自加到 170（→ 按原话取数）。")]
for i, (t, b_) in enumerate(facts):
    x = Inches(0.7 + (i % 2) * 6.15); y = Inches(1.8 + (i // 2) * 1.62)
    card(s, x, y, Inches(5.9), Inches(1.5), t, b_, title_color=ACCENT, body_size=11, mono_title=True)
text(s, Inches(0.7), Inches(6.7), Inches(12), Inches(0.4), "这些都写进了规划器的「器件事实」提示和设计记忆——每一条都附带实测数字。", size=12, color=MUTED)

# ------------------------------------------------------------------ 11. roadmap
s = slide(); n += 1; header(s, "下一步：从「两个设计、两块芯片」到一个平台", "10 · 路线图", n)
lanes = [("近期 · 让它更可信", GOOD, ["基准集 + 回归：固定 12 个规格每晚跑，对比轮数 / Fmax / 成本，守住记忆与估算不退化", "多种子 Fmax：nextpnr/Vivado 3 个种子取中位数，消掉 ±5% 的噪声误判", "跨运行 Pareto 前沿：同一设计所有历史点，前端直接给「这条约束线上你能拿到的最好」", "停机原因可解释：规划器 stop 时给出「为什么再试也没用」+ 证据"]),
         ("中期 · 扩大设计空间", ACCENT, ["第三类生成器：BRAM 密集型（卷积 / 行缓冲 / FFT），把存储器也纳入旋钮", "组合设计：matmul + FIR 级联成数据通路，顶层由 IR 图描述", "参数化接口：AXI-Stream / AXI-Lite 包装，输出可直接进 Vivado IP Integrator", "功耗：从 Vivado report_power 取数，做第四个约束维度"]),
         ("长期 · 平台化", WARN, ["多器件：UltraScale+、iCE40、Gowin——后端只是「跑工具 + 解析报告」", "板级闭环：有板子后用 ILA/VIO 做在板验证，把实测频率作为第六级", "开放 IR 规范：让别人写生成器接入；设计记忆跨用户共享（可审计）", "从「优化给定架构」到「选架构」：按规格先在多个生成器间做架构级 A/B"])]
for i, (t, c, items) in enumerate(lanes):
    x = Inches(0.7 + i * 4.1)
    box(s, x, Inches(1.8), Inches(3.9), Inches(5.0), fill=SURF, line=BORDER)
    box(s, x, Inches(1.8), Inches(3.9), Inches(0.08), fill=c, line=None, radius=False)
    text(s, x + Inches(0.22), Inches(2.05), Inches(3.5), Inches(0.4), t, size=14, color=c, bold=True)
    text(s, x + Inches(0.22), Inches(2.55), Inches(3.5), Inches(4.2), [["• " + it] for it in items], size=11.5, color=TEXT2, spacing=1.3)

# ------------------------------------------------------------------ 12. close
s = slide(); n += 1
text(s, Inches(0.9), Inches(2.0), Inches(11.5), Inches(1.2), "说一句话，拿到签核过的 Verilog。", size=40, color=TEXT, bold=True)
text(s, Inches(0.9), Inches(3.3), Inches(11.5), Inches(2.4), [["• 两类设计（systolic 矩阵乘、FIR）× 两块芯片（ECP5、Spartan-7），7 个任务全部达标，最高 319.5 MHz"], ["• 五级仿真阶梯 + 真实布局布线签核，没有一个估算数字进结论"], ["• 39 条自动验证的设计经验；有记忆比无记忆少一轮"], ["• 2 天、约 5,000 行 Python/JS，20 次提交；已部署，笔记本是瘦客户端"]], size=16, color=TEXT2, spacing=1.4)
text(s, Inches(0.9), Inches(6.2), Inches(11), Inches(0.5), "https://silicon.w33d.xyz   ·   ~/Playground/silicon-agent", size=13, color=ACCENT, font=MONO)

out = "docs/silicon-agent-pitch.pptx"
import os; os.makedirs("docs", exist_ok=True)
prs.save(out); print("wrote", out, "slides:", len(prs.slides))
