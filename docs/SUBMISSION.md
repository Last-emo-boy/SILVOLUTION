# 硅演 Silicon Agent — 项目说明（作品提交）

> 按「七、作品提交」的材料清单组织。每一条可核查的数字都指向仓库里的产物或 netcup 生产服务器上的 API 记录；
> 证据包（`deploy/evidence-bundle.sh` 的输出）把这些记录打包成可人工核查的文件。

## 1. 基本信息

| 项 | 内容 |
|---|---|
| 项目名称 | 硅演 Silicon Agent — 用真实 EDA 闭环的 FPGA 数字设计智能体 |
| 参赛组别 | （填写） |
| 学校与专业 | （填写） |
| 团队成员 | （填写） |
| 可访问应用 | https://silicon.w33d.xyz （对话）· https://silicon.w33d.xyz/work （工作台）· 访问令牌见提交表单 |
| 代码 | 本仓库（`silicon_agent/` 服务端，`agh-plugin/` Agnes Harness 插件，`sa` 命令行，`broll/` 演示视频工程） |

## 2. 问题来源

写代码的智能体已经很会写软件，但硬件不是「能跑就行」：一段 RTL 的好坏要等综合、布局布线、时序分析之后才知道，
而且同一功能在不同器件上的最优结构完全不同。现有做法要么让大模型直接吐 Verilog（没有度量、不可信），要么
把 EDA 当黑盒跑一次（没有迭代）。我们要的是：**一句话需求 → 智能体自己迭代架构 → 每一轮都用真实工具实测 →
给出经过验证的 RTL 和证据**。

## 3. 项目目标

1. 自然语言到可交付 RTL：规格、约束（Fmax / DSP / LUT / 面积）、目标顺序由对话确定，结果是可综合的 SystemVerilog + 测试台 + 交付包。
2. 真实度量，不估算：行为级仿真、综合后/实现后网表仿真、多种子布局布线、形式验证、变异测试，全部由真实工具产出（yosys/nextpnr、Vivado、iverilog/cocotb、SymbiYosys、OpenROAD）。
3. 设计空间不被预先枚举：11 个参数化生成器之外，**设计工作室**让智能体为生成器没有的块写 RTL，并由独立参考模型把关；已有设计可以「从生成器出走」被重构。
4. 可审计：每一轮的诊断、候选、实测、教训、每一次工具调用和模型费用都有记录；失败如实呈现。

## 4. 核心方法

```
需求（一句话） ─▶ 智能体（Agnes Harness 会话）─▶ 规格 {design, backend, constraints, locked}
                       │ sa_interpret: 生成器覆盖？
          ┌────────────┴─────────────┐
   有生成器：IR 旋钮                 没有：设计工作室
   generator.emit(IR) → RTL          设计师会话写 rtl/*.sv + golden.py + design.json
                                     独立参考会话只凭 SPEC.md 写 reference(params, xs)
                                     检查：elaborate → golden vs 参考 → 行为级仿真 → SymbiYosys 流属性
                                           → 每个声明的旋钮值 → 变异分数 ≥ 0.6 → 注册为 rtl:<id>
          └────────────┬─────────────┘
                 验证阶梯（每一轮）
   行为级仿真 → 综合 → 综合后网表仿真 → 布局布线（3 粒种子取中位）→ [Vivado: 实现后功能/时序仿真]
                       │ 指标 + 关键路径（逻辑/布线分段）
                 规划器（DeepSeek）：诊断 → 候选（筛选估算排序）→ 并行实测
                       │ 旋钮够不到关键路径 → `revise`：请设计师改写 RTL → 新修订 → 重过检查 → 实测
                 设计记忆：每轮动作写成教训（确认/反驳计分），下次按情境召回（含跨器件迁移）
                 交付：RTL、AXI4-Stream 包装、测试台与向量、报告、Vivado/开源构建脚本、SPEC/golden/reference
```

关键设计决策：

- **真实工具是唯一的事实来源**。筛选估算只决定候选顺序，不作为结果；无法复现的数字不进入任何展示。
- **写与查分离**。设计工作室里，写 RTL 的会话和写参考模型的会话互相看不到；RTL 必须在随机激励（含极值、窗口未满、valid 间隙）上与参考逐位一致，再加形式属性与变异分门槛，才会被注册。
- **规划器在旋钮之外的唯一出口是 `revise`**，且指令必须引用实测的关键路径；设计师的修订同样走完整检查。
- **失败是数据**：不收敛的运行、漏网的变异、检查失败的报告都保留并展示。

## 5. Agnes Harness 执行流程

| 环节 | AGH 中的实体 | 说明 |
|---|---|---|
| 对话智能体 | 会话 `sa-thread-<thread>`，preset `silicon`（fork 新增） | 每个对话一个 AGH 会话；每条用户消息是一次 `session/prompt`；`context` 钩子注入方法（METHOD 1–9），`tool_call` 钩子禁用 write/edit/shell 等宿主工具 — 智能体改变世界的唯一途径是 `sa_*` 工具 |
| 工具 | 插件 `@silicon-agent/agh-plugin`（`agh-plugin/index.mjs`），30 个 `sa_*` 工具 | 解释/估算（`sa_schema` `sa_interpret` `sa_estimate`）、运行（`sa_run` `sa_job` `sa_jobs` `sa_sweep` `sa_job_file`）、引导（`sa_steer` `sa_stop`）、验证（`sa_formal` `sa_mutation`）、对比（`sa_pareto` `sa_tournament*`）、经验（`sa_memory` `sa_bench`）、自带 RTL（`sa_rtl_*`）、设计工作室（`sa_studio_open/fork/status/revise/list/resume`，设计师专用 `sa_studio_check/submit`）、改生成器（`sa_check` `sa_propose` `sa_proposal`）。每个工具调用的参数、结果预览、状态由 AGH 记录 |
| 设计师 | 会话 `sa-studio-<id>`，preset `author`，cwd = `SA_ROOT/studio/<id>/work/` | 深思模型；`context` 钩子注入 DESIGNER_METHOD；`tool_call` 钩子把 write/edit 限制在 work/，shell 仅 ls/cat/grep/diff；单轮上限 `SA_STUDIO_TURN_S`（40 min） |
| 独立参考 | 会话 `sa-reference-<id>-n`，无工具 | 钩子拒绝所有工具；只收到 SPEC.md 与接口/参数，输出 `reference(params, xs)` |
| 作者模式 | 会话 `sa-author-<thread>` | 在 b/ 副本改生成器，`sa_propose` 提交 diff，门禁（发射、仿真、形式等价、变异分、A/B 实测）通过后由操作员合并 |
| 账本 | `projectUI.turns[].usage` | 每轮模型调用、输入/输出/缓存 token、估算费用、用时；显示在回复下方与顶栏 |
| 恢复 | `session/load` + `session.attach` / `wait_idle` | 服务器重启后重新跟随进行中的轮次；AGH 中止的轮次用会话自己的记录续接；工作室记录 `resume_pending` 自动恢复 |
| 部署 | compose 服务 `agh`（daemon，unix socket）+ `server`（Python 作业服务器）+ `orfs`（OpenROAD） | `deploy/agh-entry.sh`、`deploy/agh-provision.mjs`（DeepSeek 账户、插件安装→信任→启用） |

## 6. 所用模型（名称与版本）与使用环节

| 模型 | 版本/路由 | 使用环节 |
|---|---|---|
| DeepSeek Flash | `deepseek-flash`（AGH 内置 DeepSeek 提供方，pi-ai 目录） | 对话智能体「快」模式；规划器默认模型（`SA_MODEL`）；教训提炼 |
| DeepSeek V4 Pro（thinking high） | `deepseek-v4-pro` | 对话「深思」模式；设计工作室的设计师与独立参考会话（`SA_STUDIO_MODEL`）；作者模式；锦标赛里的深思规划器 |

调用方式：对话与工作室全部通过 Agnes Harness（`session/new` / `session/prompt` / `session.setModel` 的 JSON-RPC，`silicon_agent/agh.py`）；规划器（`silicon_agent/llm.py`）直接调用 DeepSeek 兼容 API，每次调用的 prompt/响应保存在 `results/<job>/llm_NN.json`。密钥只存在于服务器 `/opt/silicon-agent/.env`，不进仓库。

## 7. 验证方法

| 层 | 方法 | 工具 | 记录 |
|---|---|---|---|
| 功能 | 行为级仿真：RTL vs Python 黄金模型（生成器）/ 独立参考模型的向量（工作室）| cocotb 2.1 + Icarus Verilog 14 | `metrics.sim_passed`、`sim_log`；工作室 `checks/<tag>/report.json` |
| 网表 | 综合后功能仿真（全部器件）；综合后/实现后 功能+时序 仿真（Spartan-7，Vivado xsim，SDF 延迟）| yosys 0.69 / Vivado 2025.2 | `metrics.sim_stages` |
| 时序/资源 | 布局布线 3 粒种子取中位 Fmax，关键路径按逻辑/布线分段 | nextpnr-ecp5/ice40/himbaechel 0.11、Vivado | `fmax_seeds`、`critical_path` |
| 形式 | 实测 RTL 与再次发射的 IR 有界等价（深度 ≥ 20）+ 流协议属性（复位期间无 valid、ready 不依赖 valid）| SymbiYosys 0.69 + boolector | `/api/jobs/<id>/formal` |
| 强度 | 变异测试：注入加减互换、比较翻转、使能卡死、索引偏移等，统计测试台抓住的比例 | `silicon_agent/mutate.py` | `/api/jobs/<id>/mutation`；工作室注册门槛 ≥ 0.6 |
| 工作室 | golden vs 独立参考（Python 对 Python，隔离子进程）→ 行为级 → 流属性 → 每个声明的旋钮值 → 变异分 | 同上 | `/api/studio/<id>`、`checks/` |
| 回归 | 夜间基准：黄金点复测（toolchain 真跑）+ 规划器用例 + 智能体用例（含工作室用例）| `silicon_agent/bench.py` | `/api/bench`，`bench/*.json` |

## 8. 运行环境、依赖、数据、专业工具、仿真环境、设备接口

- **服务器**：Debian，Docker Compose 三个服务；Python 3.11（oss-cad-suite 自带）；Node 24（AGH）。
- **EDA**：oss-cad-suite（yosys 0.69、nextpnr 0.11、iverilog 14、SymbiYosys 0.69、boolector）、AMD Vivado 2025.2（Spartan-7；Artix-7 设备文件未装，后端已实现但关闭）、OpenROAD-flow-scripts（sky130_fd_sc_hd）。
- **器件**：Lattice ECP5 LFE5U-25F、iCE40 UP5K、Gowin GW1NR-9、Cologne Chip GateMate A1、AMD XC7S50、sky130（ASIC）。无实板；交付包含 Vivado 工程脚本与开源构建脚本、AXI4-Stream 包装与 XDC。
- **数据**：`results/<job>/`（每轮 IR、指标、规划器对话）、`runs/<hash>/`（EDA 工作目录）、`memory/*.jsonl`（教训）、`bench/*.json`、`/srv/sa/studio/<id>/`（工作室记录、检查报告、修订）、`/srv/sa/rtl/<id>/bundle.json`。
- **仿真环境**：每轮一个隔离工作目录；BRAM/DSP 原语有自己的仿真模型（`silicon_agent/tb/`）。

## 9. 可复现运行方式

```bash
# 远程（推荐）：笔记本只是薄客户端
ssh -f -N -L 18765:127.0.0.1:8765 netcup
export SA_SERVER=http://127.0.0.1:18765 SA_TOKEN=...
sa ask "一个 5 点滑动中值滤波器：16 位有符号，ECP5，150 MHz 以上，LUT 越少越好"
sa studio list && sa studio status <id> --watch
sa trust <job> && sa formal <job> && sa mutation <job> && sa package <job>
sa export <thread> report.md

# 本地（ECP5，开源工具链）：README「Quick start (local)」
```

## 10. 运行与验证证据（正常 / 边界 / 失败）

见 `deploy/evidence-bundle.sh` 的产物 `evidence/`：

- **正常**：中值滤波器对话 `20261011-100330-7fd8` → 工作室 `20261011-100357-s0d36` → 运行 `20261011-102713-f6f7`（212.8 MHz / 356 LUT，独立参考 648 样本，形式等价 PASS d22，变异 2/6）；CIC 运行 `20261009-141103-d59f`（271.8 MHz，形式 PASS，变异 8/8）。
- **边界**：规划器把旋钮穷尽后停在「无合法候选」并给出 stop_reason；工作室注册时的旋钮值检查拒绝未实现的值；FFT 分叉在 LUT ≤ 1200 边界上 1191 LUT 可行（`20261011-171217-66cf`）。
- **失败**：工作室检查 101543（流属性 FAIL → 修 → 102125 PASS）；设计师 2 小时空转 → 单轮超时取消（`20261011-131130-sd75d`）；5×5 conv 在 Spartan-7 上不收敛（`results/`）；网表仿真误判（Gowin SP/SPX9 仿真模型 bug）的发现与修复（commit f88cf17）。
- **AGH 执行记录**：`agh-sessions.json`（对话、设计师会话的 turns / tool nodes / ledger，`deploy/scripts/agh-export.py` 导出）。

## 11. 独立完成声明与第三方材料

全体成员确认作品由学生团队独立完成。使用的第三方材料及方式：

| 材料 | 来源 | 使用方式 |
|---|---|---|
| Agnes Harness | github.com/AgnesAI-Labs/agnes-harness（fork 加 `silicon`/`author` preset） | 智能体运行时；本项目是其插件 |
| DeepSeek 模型 | DeepSeek 开放平台 API | 规划器与智能体的推理 |
| oss-cad-suite / yosys / nextpnr / Icarus / SymbiYosys / boolector / cocotb | YosysHQ 等开源项目 | EDA 工具链 |
| AMD Vivado 2025.2 | AMD（标准版许可） | Spartan-7 综合、实现与时序仿真 |
| OpenROAD-flow-scripts、sky130 PDK | The OpenROAD Project、SkyWater | ASIC 后端 |
| Remotion、three.js、React | 开源 | 演示视频工程（`broll/`） |
| 字体 Space Grotesk、IBM Plex、Noto Sans SC | Google Fonts（OFL） | 视频与界面 |
