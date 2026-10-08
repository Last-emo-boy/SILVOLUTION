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
text(s, Inches(0.9), Inches(4.1), Inches(11), Inches(1.2), "自然语言 → 架构 IR → RTL → 仿真 → 综合 → 布局布线 → 诊断 → 下一轮，直到时序和资源约束全部满足。\n真实 EDA 工具是唯一的 ground truth：没有一个数字是估出来的。4 种器件 · 4 类生成器 · 已部署。", size=18, color=TEXT2)
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
s = slide(); n += 1; header(s, "真实结果：13 个任务 · 4 种器件 · 4 类设计，全部由真实 EDA 签核", "05 · 结果", n)
res = [("INT8 matmul", "ECP5", "≥180 MHz · ≤48 DSP", "5", "4×12 · 188.5 MHz · 48 DSP · 48 MAC/cyc"),
       ("INT8 matmul", "XC7S50", "≥200 MHz · ≤64 DSP", "3", "8×8 · 239.6 MHz · 64 DSP · 1005 LUT"),
       ("INT8 matmul · 五级仿真", "XC7S50", "≥300 MHz · ≤32 DSP", "3", "2×16 · 315–319.5 MHz · 32 DSP · 五级全过"),
       ("32 阶 FIR", "ECP5", "≥120 MHz · ≤6 DSP", "2", "对称 fold=4 · 125.8 MHz · 4 DSP"),
       ("32 阶 FIR", "XC7S50", "≥250 MHz · ≤8 DSP", "7", "非对称 fold=8 · 259.1 MHz · 8 DSP · 324 LUT"),
       ("INT8 matmul（聊天 + 追问 fork）", "ECP5", "≥160 MHz · ≤32 DSP", "2+8", "1×1 216.8 → 1×32 · 214.2 MHz · 32 MAC/cyc"),
       ("3×3 高斯卷积（聊天）", "iCE40 UP5K", "≥60 MHz · ≤1500 LUT", "4", "全并行 · 70.6 MHz · 273 LUT · 0 DSP · 2 BRAM"),
       ("5×5 卷积 · 640 宽", "ECP5", "≥100 MHz · 省 LUT", "8", "125.7 MHz · 630 LUT · 9 DSP · 4 BRAM"),
       ("16 阶 FIR（聊天）", "Gowin GW1NR-9", "≥100 MHz · ≤4 DSP", "4", "fold=2 · 103.5 MHz · 4 MULT18X18 · 733 LUT"),
       ("INT8 matmul", "Gowin GW1NR-9", "≥120 MHz · ≤16 DSP", "1", "153.5 MHz · 16 DSP · 976 LUT"),
       ("INT8 matmul", "iCE40 UP5K", "≥40 MHz · ≤8 DSP", "1", "78.2 MHz · 8 SB_MAC16 · 304 LUT"),
       ("3×3 卷积 · 640 宽", "ECP5", "≥100 MHz · ≤1500 LUT", "1", "140.2 MHz · 213 LUT · 0 DSP · 2 BRAM"),
       ("两级 FIR 级联 8→32 阶", "ECP5", "≥120 MHz · ≤12 DSP", "4", "两级对称 fold=4 · 127.4 MHz · 5 DSP · 989 LUT")]
cols = [Inches(0.7), Inches(3.6), Inches(5.3), Inches(8.0), Inches(8.75)]
for h_, c in zip(["任务", "器件", "约束", "轮数", "最终设计（真实 P&R）"], cols):
    text(s, c, Inches(1.75), Inches(3), Inches(0.3), h_, size=11, color=MUTED, font=MONO)
y = Inches(2.05)
for i, (task, dev, cons, it, out) in enumerate(res):
    box(s, Inches(0.6), y, Inches(12.2), Inches(0.34), fill=SURF if i % 2 == 0 else BG, line=None)
    text(s, cols[0], y + Inches(0.05), Inches(2.9), Inches(0.3), task, size=11, color=TEXT)
    text(s, cols[1], y + Inches(0.07), Inches(1.7), Inches(0.3), dev, size=10, color=TEXT2, font=MONO)
    text(s, cols[2], y + Inches(0.07), Inches(2.7), Inches(0.3), cons, size=10, color=TEXT2, font=MONO)
    text(s, cols[3], y + Inches(0.05), Inches(0.8), Inches(0.3), it, size=11, color=TEXT, font=MONO)
    text(s, cols[4], y + Inches(0.05), Inches(4.2), Inches(0.3), [[("✓ " if it != "运行中" else "… ", {"color": GOOD if it != "运行中" else ACCENT, "bold": True}), out]], size=10.5, color=TEXT)
    y += Inches(0.36)
text(s, Inches(0.7), Inches(6.85), Inches(12), Inches(0.4), "每轮成本：ECP5 / iCE40 / Gowin 15–60 秒（3 个布线种子取中位数），XC7S50 1.5–4 分钟（含五级仿真）。规划器 DeepSeek，每轮一次调用。回归基准集 8/8 通过，平均第 1.6 轮可行。", size=11, color=MUTED)

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
pic = s.shapes.add_picture("docs/img/chat-conv.png", Inches(0.7), Inches(1.8), width=Inches(7.3))
pic.line.color.rgb = BORDER; pic.line.width = Pt(0.75)
# right: bullets
text(s, Inches(8.3), Inches(1.85), Inches(4.5), Inches(5.0), [[("输入只有自然语言。", {"color": TEXT, "bold": True}), " nl.interpret 产出完整规格：设计类型、器件、锁定参数、约束、目标顺序、朴素起点，并说明假设。"], [""], [("追问即迭代。", {"color": TEXT, "bold": True}), " 同一会话里再说一句：改约束 → 从上一次最佳设计 fork；运行中 → 作为工程师指令插话；提问 → 直接回答。"], [""], [("细节按需展开。", {"color": TEXT, "bold": True}), " 卡片默认折叠；展开才看到每轮 IR 差分、五级仿真、关键路径、日志。"], [""], [("工程师视图。", {"color": TEXT, "bold": True}), " 每个会话有独立的 worktable：轨迹图、架构草图、IR 旋钮、比较、fork、RTL。"], [""], [("部署：", {"color": TEXT, "bold": True}), " netcup 上 Vivado 容器里的任务服务器，sluice SSO；笔记本只是瘦客户端（sa ask \"…\"）。", ]], size=12.5, color=TEXT2, spacing=1.25)

# ------------------------------------------------------------------ 9b. worktable
s = slide(); n += 1; header(s, "工程师视图：每个会话一张独立的 worktable", "08 · 产品", n)
pic = s.shapes.add_picture("docs/img/worktable-pareto.png", Inches(0.7), Inches(1.8), width=Inches(8.6))
pic.line.color.rgb = BORDER; pic.line.width = Pt(0.75)
text(s, Inches(9.6), Inches(1.85), Inches(3.3), Inches(5.0), [[("一轮一个 turn。", {"color": TEXT, "bold": True}), " 规划器的动作、IR 差分、判定行、仿真阶段；假设 / 召回的经验 / 候选与估算 / 关键路径 / 本轮经验折叠在 details 里。"], [""], [("右侧 inspector。", {"color": TEXT, "bold": True}), " 架构草图、IR 旋钮、关键路径分解条、轨迹图、五级仿真表、RTL、记忆，以及这个设计在这颗芯片上所有历史实测点的 Pareto 前沿（图中 20 个点、10 个非支配）。"], [""], [("动作。", {"color": TEXT, "bold": True}), " 从任意一轮 fork 并改约束、同会话两次运行并排比较、重跑、删除——都留在这个会话里，并在聊天里出现为一个 turn。"], [""], [("与聊天互通。", {"color": TEXT, "bold": True}), " 聊天负责「要什么」，worktable 负责「为什么」。"]], size=12.5, color=TEXT2, spacing=1.25)

# ------------------------------------------------------------------ 9c. platform grid
s = slide(); n += 1; header(s, "平台：生成器 × 器件是一张可以继续长的表", "09 · 平台", n)
devs = [("ECP5", "LFE5U-45F · yosys + nextpnr", "15–60 s"), ("Spartan-7", "XC7S50 · Vivado 2025.2 · 五级仿真", "1.5–4 min"), ("iCE40", "UP5K · nextpnr-ice40", "10–40 s"), ("Gowin", "GW1NR-9 · himbächel", "15–60 s")]
gens = [("matmul", "output-stationary systolic INT 阵列"), ("fir", "定系数 FIR：转置 / 折叠 / 对称共享"), ("conv2d", "K×K 流式卷积：BRAM 行缓冲 + 窗口 + 加法树"), ("fir_chain", "组合：多级 FIR 经 FIFO 级联（每级独立旋钮）")]
x0, y0, cw, rh = Inches(3.3), Inches(1.85), Inches(2.35), Inches(0.95)
for j, (d, sub, t_) in enumerate(devs):
    x = x0 + j * cw
    box(s, x, y0, cw - Inches(0.1), Inches(0.75), fill=SURF, line=BORDER)
    text(s, x + Inches(0.12), y0 + Inches(0.08), cw - Inches(0.3), Inches(0.3), d, size=13, color=TEXT, bold=True)
    text(s, x + Inches(0.12), y0 + Inches(0.36), cw - Inches(0.3), Inches(0.4), f"{sub} · {t_}", size=9, color=MUTED, font=MONO)
verified = {("matmul", 0), ("matmul", 1), ("matmul", 2), ("matmul", 3), ("fir", 0), ("fir", 1), ("fir", 3), ("conv2d", 0), ("conv2d", 2), ("conv2d", 3), ("fir_chain", 0), ("fir_chain", 3), ("fir_chain", 2), ("fir", 2)}
real = {("matmul", 0), ("matmul", 1), ("matmul", 2), ("matmul", 3), ("fir", 0), ("fir", 1), ("fir", 3), ("conv2d", 0), ("conv2d", 2), ("fir_chain", 0)}
for i, (g, sub) in enumerate(gens):
    y = y0 + Inches(0.9) + i * rh
    box(s, Inches(0.7), y, Inches(2.5), rh - Inches(0.1), fill=SURF, line=BORDER)
    text(s, Inches(0.85), y + Inches(0.1), Inches(2.3), Inches(0.3), g, size=13, color=TEXT, bold=True, font=MONO)
    text(s, Inches(0.85), y + Inches(0.4), Inches(2.3), Inches(0.45), sub, size=9.5, color=MUTED)
    for j in range(4):
        x = x0 + j * cw
        done_real = (g, j) in real; done_sim = (g, j) in verified
        c = GOOD if done_real else (ACCENT if done_sim else MUTED)
        b = box(s, x, y, cw - Inches(0.1), rh - Inches(0.1), fill=SURF2 if done_sim else BG, line=c if done_sim else BORDER)
        text(s, x + Inches(0.12), y + Inches(0.28), cw - Inches(0.3), Inches(0.4), "● 真实任务达标" if done_real else ("● EDA + 网表仿真通过" if done_sim else "○ 未跑"), size=10.5, color=c)
text(s, Inches(0.7), Inches(6.15), Inches(12), Inches(0.8), [["加一种器件 = backends/ 下一个文件（器件事实 + 跑工具 + 解析报告）；加一类设计 = 一个 IR + 生成器 + 黄金模型 + 估算 + 规划知识。提示词、估算、表单、自然语言解释器都从注册表取。"], [("黑盒原语自己建模：", {"color": TEXT2}), "DP16KD / Gowin DPX9B、SP、SPX9、MULT9X9/18X18 的仿真模型按 yosys 的驱动方式写出，所以每个开源后端都能跑综合后网表仿真。"]], size=12, color=TEXT2, spacing=1.3)

# ------------------------------------------------------------------ 9d. trust + delivery
s = slide(); n += 1; header(s, "可信与交付：让结论可证明，让结果能直接用", "10 · 可信 · 交付", n)
items = [("回归基准集", "11 个固定规格 × 期望（可行 / 几轮内可行），当作普通任务提交、打分、落盘；`bench show` 并排看最近几次。首轮开源 8 例 8/8，平均第 1.6 轮可行。", GOOD),
         ("多种子 Fmax", "开源后端每个设计布线 3 个种子，报中位数并显示区间（ECP5 上 ±3–5%）。规划器也看到区间，不再被一次布线的运气判「差 4 MHz」。", GOOD),
         ("停机必须给证据", "规划器只能带着 stop_reason 停：触到了哪个上界（DSP 上限、累加位宽下限、器件本征级）、试过哪些旋钮没用。叙述里直接说「为什么停」。", GOOD),
         ("跨运行 Pareto 前沿", "同一设计类型在同一芯片上的所有实测点，取非支配集；「≥180 MHz、≤32 DSP 最好能到多少」直接从 20 个实测点里回答，不用重新跑。聊天回答也用它。", ACCENT),
         ("功耗维度", "Vivado report_power 动态功耗 → power_mw，可作约束 max_power_mw 和目标 minimize_power。", ACCENT),
         ("交付包", "zip：RTL + AXI4-Stream 包装（带输出 FIFO，随机反压下验证）+ 自检向量 testbench + 工具报告 + 轨迹 + README。拖进 Vivado IP Integrator 就能用。", ORANGE)]
for i, (t_, b_, c) in enumerate(items):
    x = Inches(0.7 + (i % 2) * 6.15); y = Inches(1.8 + (i // 2) * 1.62)
    card(s, x, y, Inches(5.9), Inches(1.5), t_, b_, title_color=c, body_size=11)

# ------------------------------------------------------------------ 9e. literature
s = slide(); n += 1; header(s, "和最新文献的关系：我们把「意图 IR + 真实 EDA 反馈 + 经验池」做成了产品", "11 · 定位", n)
lit = [("HINT (arXiv 2608.07625)", "规格与 RTL 之间插一层可执行的「硬件意图」IR，RTL 前就能检查；7/7 可综合，面积比手写低 5–26%。", "我们的架构 IR 就是这一层——但它只有十几个旋钮，由确定性生成器落地，正确性靠构造而非检查。"),
       ("RTLScout (Huawei, 2606.06530)", "ReAct 智能体 + Yosys/OpenROAD 反馈，多次运行共享精英池和「经验」，FP16 乘法器面积 −35%、延迟 −45%。", "设计记忆 = 他们的 lessons，但每条带情境/动作/实测效果，并由后续运行自动确认或推翻。"),
       ("REvolution (ASP-DAC 2026)", "进化算法 + LLM：种群、Thought/Code/Feedback 三元组、六种提示算子、PPA 适应度；VerilogEval 95.5%。", "我们每轮取 ≤3 候选、闭式估算筛选只送 1 个进 EDA——在真实 P&R 每轮分钟级的预算下比种群更省。"),
       ("CRADLE / ResBench (2025)", "多智能体会话式 DSE 面向 FPGA 资源目标；ResBench 是首个资源感知的 FPGA 生成基准（56 题）。", "我们的基准集是任务级（约束 + 期望轮数），衡量的是「闭环能否收敛」而不是单次生成。")]
y = Inches(1.8)
for t_, what, ours in lit:
    box(s, Inches(0.7), y, Inches(12.1), Inches(1.12), fill=SURF, line=BORDER)
    text(s, Inches(0.9), y + Inches(0.12), Inches(3.2), Inches(0.9), t_, size=12, color=ACCENT, bold=True, font=MONO)
    text(s, Inches(4.1), y + Inches(0.12), Inches(4.3), Inches(0.95), what, size=10.5, color=TEXT2)
    text(s, Inches(8.5), y + Inches(0.12), Inches(4.2), Inches(0.95), [[("→ ", {"color": GOOD}), ours]], size=10.5, color=TEXT)
    y += Inches(1.2)
text(s, Inches(0.7), Inches(6.65), Inches(12), Inches(0.4), "共同结论：LLM 的价值来自「推理 + 外部定量反馈」，而不是生成质量本身。我们的反馈是真实布局布线 + 五级仿真，每轮都是。", size=12, color=MUTED)

# ------------------------------------------------------------------ 10. tech facts learned (moat)
s = slide(); n += 1; header(s, "护城河是「实测过的器件事实」，不是提示词", "12 · 积累", n)
facts = [("ECP5", "yosys 永远不会把流水寄存器打包进 MULT18X18D → 必须显式例化并用 REG_* 参数（76 → 147 MHz）。级联口 SRIA/SRIB 悬空否则 nextpnr 路由不了 GND 网。"),
         ("XC7S50", "8×8 乘法需要 (* use_dsp=\"yes\" *) 才进 DSP48E1；pipe_mul>0 会让 Vivado 用 2 个 DSP/PE；DSP48E1 的 M→P 级 2.31 ns 是 277 MHz 天花板，累加器移到 CARRY4 后 299–319 MHz。"),
         ("FIR", "yosys 对行为级乘法会自动常量折叠零系数、共享相等系数——symmetric 旗标没用，除非显式 DSP；Vivado 会把整条转置 FIR 吸进 DSP 级联（495 MHz，1 LUT，45 DSP）。"),
         ("仿真", "glbl.GSR 让触发器保持 100 ns；-transport_int_delays -pulse_r 0 会产生伪 X；ECP5 的 cells_sim.v 重复 include 要去重；yosys 的 DP16KD / Gowin BRAM / MULT 都是黑盒，要按它的驱动方式自建仿真模型。"),
         ("iCE40 / Gowin", "nextpnr-ice40 没有 out-of-context → 串行 IO 外壳包住核心再布线；himbächel 必须给引脚约束；synth_gowin 会留下不可放置的 $buf（未驱动的部分总线）→ setundef + opt_clean。"),
         ("LLM 的坏习惯", "为了最小化 LUT 把 INT8 改成 INT4（→ locked）；把 1×1 PE 当成「省资源」（→ 吞吐优先的解释规则）；把 160 MHz 擅自加到 170（→ 按原话取数）。")]
for i, (t, b_) in enumerate(facts):
    x = Inches(0.7 + (i % 2) * 6.15); y = Inches(1.8 + (i // 2) * 1.62)
    card(s, x, y, Inches(5.9), Inches(1.5), t, b_, title_color=ACCENT, body_size=11, mono_title=True)
text(s, Inches(0.7), Inches(6.7), Inches(12), Inches(0.4), "这些都写进了规划器的「器件事实」提示和设计记忆——每一条都附带实测数字。", size=12, color=MUTED)

# ------------------------------------------------------------------ 11. roadmap
s = slide(); n += 1; header(s, "路线图：本周做完的，和下一步", "13 · 路线图", n)
lanes = [("已完成（本周）", GOOD, ["✓ 后端插件化：ECP5 / Spartan-7 / iCE40 / Gowin", "✓ 第三、四类生成器：conv2d（BRAM）、fir_chain（组合数据通路）", "✓ 回归基准集 + 多种子 Fmax + 停机证据", "✓ 跨运行 Pareto 前沿 + 功耗维度", "✓ 交付包：RTL + AXI-Stream + testbench + 报告", "✓ 聊天：ETA、替代方案一键追问、会话隔离/重命名"]),
         ("下一步 · 扩大空间", ACCENT, ["更多生成器：FFT、CORDIC、行缓冲 + 池化、systolic 以外的 matmul 形态（dot-product 折叠）", "架构级 A/B：同一规格先在多个生成器间做选择，再在选中者内优化", "通用 IR 图：任意 valid/ready 模块按图级联（fir_chain 的推广）", "每晚回归常态化 + 记忆/估算退化告警"]),
         ("更远 · 平台化", WARN, ["UltraScale+、Artix-7、GateMate：一个后端文件一个器件", "有板后：ILA/VIO 在板验证作为第六级", "开放 IR 规范，让别人的生成器接入；设计记忆跨用户共享（可审计）", "多模型规划器（DeepSeek / Claude / GPT）A/B 与基准集对照"])]
for i, (t_, c, items) in enumerate(lanes):
    x = Inches(0.7 + i * 4.1)
    box(s, x, Inches(1.8), Inches(3.9), Inches(5.0), fill=SURF, line=BORDER)
    box(s, x, Inches(1.8), Inches(3.9), Inches(0.08), fill=c, line=None, radius=False)
    text(s, x + Inches(0.22), Inches(2.05), Inches(3.5), Inches(0.4), t_, size=14, color=c, bold=True)
    text(s, x + Inches(0.22), Inches(2.55), Inches(3.5), Inches(4.2), [["• " + it if not it.startswith("✓") else it] for it in items], size=11.5, color=TEXT2, spacing=1.3)

# ------------------------------------------------------------------ 12. close
s = slide(); n += 1
text(s, Inches(0.9), Inches(2.0), Inches(11.5), Inches(1.2), "说一句话，拿到签核过的 Verilog。", size=40, color=TEXT, bold=True)
text(s, Inches(0.9), Inches(3.3), Inches(11.5), Inches(2.4), [["• 4 类生成器（matmul、FIR、conv2d、FIR 级联）× 4 种器件（ECP5、Spartan-7、iCE40、Gowin），13 个真实任务达标，最高 319.5 MHz"], ["• 五级仿真阶梯 + 真实布局布线签核 + 3 种子中位数 Fmax，没有一个估算数字进结论"], ["• 88 条自动验证的设计经验；回归基准 8/8，平均第 1.6 轮可行；交付包含 AXI-Stream 包装"], ["• 2 天、约 7,100 行 Python/JS，27 次提交；已部署，笔记本是瘦客户端"]], size=16, color=TEXT2, spacing=1.4)
text(s, Inches(0.9), Inches(6.2), Inches(11), Inches(0.5), "https://silicon.w33d.xyz   ·   ~/Playground/silicon-agent", size=13, color=ACCENT, font=MONO)

out = "docs/silicon-agent-pitch.pptx"
import os; os.makedirs("docs", exist_ok=True)
prs.save(out); print("wrote", out, "slides:", len(prs.slides))
