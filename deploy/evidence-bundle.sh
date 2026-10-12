#!/usr/bin/env bash
# Build the "运行与验证证据" bundle for the submission: human-checkable records of the runs the project
# claims, pulled from the production server's API and data directory. Nothing is typed by hand.
#
#   ssh -f -N -L 18765:127.0.0.1:8765 netcup
#   SA_URL=http://127.0.0.1:18765 SA_TOKEN=... deploy/evidence-bundle.sh [out-dir]
#
# Layout of the bundle:
#   README.md                   what is in here and which claim each file backs (normal / boundary / failure)
#   normal/                     the median-filter conversation end to end; the CIC run; their trust / formal / mutation
#   boundary/                   the planner stopping at a bound; the FFT fork at the LUT limit; knob-value rejection
#   failure/                    the failed studio check and its fix; the cancelled designer; failed runs; the sim-model bug
#   agh/                        Agnes Harness session records (turns, tool calls with argument/result previews, ledger)
#   threads/                    Markdown exports of the conversations
set -euo pipefail
cd "$(dirname "$0")/.."
B="${SA_URL:-http://127.0.0.1:18765}/api"
H="${SA_HOST:-netcup}"
O="${1:-evidence}"
tok="${SA_TOKEN:?SA_TOKEN}"
mkdir -p "$O"/{normal,boundary,failure,agh,threads}
get() { curl -sf -H "Authorization: Bearer $tok" "$B/$1" -o "$2" || { echo "GET $1 failed (is the tunnel up?)" >&2; exit 1; }; printf '%-44s %9s B  %s\n' "$2" "$(wc -c < "$2" | tr -d ' ')" "$1"; }

# ---- normal: one task from the sentence to the delivered design -------------------------------
T_MED=20261011-100330-7fd8; S_MED=20261011-100357-s0d36; J_MED=20261011-102713-f6f7
get "threads/$T_MED"                 "$O/normal/thread-median5.json"
get "threads/$T_MED/export.md"       "$O/threads/median5.md"
get "studio/$S_MED"                  "$O/normal/studio-median5.json"
get "studio/$S_MED/spec"             "$O/normal/studio-median5-spec.json"
get "studio/$S_MED/file/golden.py"   "$O/normal/studio-median5-golden.json"
get "studio/$S_MED/file/design.json" "$O/normal/studio-median5-design.json"
get "rtl/$S_MED"                     "$O/normal/bundle-median5.json"
get "jobs/$J_MED"                    "$O/normal/job-median5.json"
get "jobs/$J_MED/trust"              "$O/normal/trust-median5.json"
get "jobs/$J_MED/formal?depth=20"    "$O/normal/formal-median5.json"
get "jobs/$J_MED/mutation"           "$O/normal/mutation-median5.json"
get "jobs/$J_MED/rtl"                "$O/normal/median5.sv"
curl -sf -H "Authorization: Bearer $tok" "$B/jobs/$J_MED/package" -o "$O/normal/package-median5.zip" && echo "$O/normal/package-median5.zip"
# the CIC run the film's worktable shots follow (built-in chat, generator design; 271.8 MHz, formal PASS, 8/8
# mutants): it ran on the development checkout, so its records come from the repository (results/, threads/)
# and from the film's product snapshots (captured from the product served on that checkout), not the server
J_CIC=20261009-141103-d59f; T_CIC=20261009-141051-a320
mkdir -p "$O/normal/cic-local"
cp -R "results/$J_CIC" "$O/normal/cic-local/results-$J_CIC"
cp "threads/$T_CIC.json" "$O/normal/cic-local/thread-$T_CIC.json"
for f in job-cic trust-cic formal-cic; do cp "broll/public/assets/product/$f.json" "$O/normal/cic-local/$f.json"; done
cp broll/docs/evidence/*.json "$O/normal/cic-local/" 2>/dev/null || true
echo "$O/normal/cic-local/  (development-checkout run: results/, threads/, film snapshots)"
# the tournament (two planners on one spec) and the bench
get "tournaments/20261009-093056-t7b81" "$O/normal/tournament-fir32.json"
get "bench"                          "$O/normal/bench.json"
get "catalog"                        "$O/normal/catalog.json"
get "health"                         "$O/normal/health.json"

# ---- boundary -----------------------------------------------------------------------------------
# the FFT fork on Gowin: LUT <= 1200 asked, 1191 measured; the planner's knob moves and one revise
get "studio/20261011-130919-s7916"   "$O/boundary/studio-fft.json"
get "jobs/20261011-171217-66cf"      "$O/boundary/job-fft-after-fix.json"
get "jobs/20261011-171217-66cf/trust" "$O/boundary/trust-fft.json"
# the planner stopping at a bound on the median run is inside job-median5.json (plan events with stop_reason);
# knob-value rejection at submit is inside the designer's AGH record (sa_studio_submit → register-knob1)
cp "$O/normal/job-median5.json" "$O/boundary/job-median5-stop-at-bound.json"

# ---- failure ------------------------------------------------------------------------------------
# the studio's own check failing (stream properties) and passing after the designer's fix
ssh "$H" "cd /srv/sa/studio/$S_MED/checks && tar czf - 101543/report.json 101543/props/props.sv 101543/props/props/engine_0/logfile.txt 102125/report.json register/report.json register-knob0/report.json register-knob2/report.json 2>/dev/null" > "$O/failure/studio-median5-checks.tgz"
echo "$O/failure/studio-median5-checks.tgz"
# the designer that searched for two hours (the first bench studio) and the run that failed waiting for it
get "studio/20261011-131130-sd75d"   "$O/failure/studio-median5-first.json"
get "jobs/20261011-133113-f604"      "$O/failure/job-median5-first-failed.json"
get "threads/20261011-131102-f808"   "$O/failure/thread-bench-median5.json"
# the FFT run before the Gowin BRAM sim-model fix: rev 1/2 reported "post-synthesis simulation failed" wrongly
get "jobs/20261011-140059-331a"      "$O/failure/job-fft-before-fix.json"
# failed runs on the server (planner could not reach the constraints): as the product lists them
curl -sf -H "Authorization: Bearer $tok" "$B/jobs" | python3 -c '
import json, sys
rows = [j for j in json.load(sys.stdin) if j["status"] == "failed"]
json.dump(rows, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print(sys.argv[1], len(rows), "failed runs")' "$O/failure/failed-runs.json"

# ---- AGH session records --------------------------------------------------------------------------
ssh "$H" "docker exec -i silicon-agent python3 /app/deploy/scripts/agh-export.py sa-thread-$T_MED sa-studio-$S_MED:/srv/sa/studio/$S_MED/work sa-thread-20261011-131102-f808 sa-studio-20261011-143729-sf892:/srv/sa/studio/20261011-143729-sf892/work sa-studio-20261011-130919-s7916:/srv/sa/studio/20261011-130919-s7916/work" > "$O/agh/agh-sessions.json"
echo "$O/agh/agh-sessions.json ($(wc -c < "$O/agh/agh-sessions.json" | tr -d ' ') B)"

# ---- the index ------------------------------------------------------------------------------------
cat > "$O/README.md" <<'MD'
# 运行与验证证据

所有文件都是生产服务器（https://silicon.w33d.xyz）API 的原始响应或其数据目录里的记录，由
`deploy/evidence-bundle.sh` 导出，未经手工编辑。时间为 2026-10-11/12。

## 正常样例（normal/）
| 文件 | 支撑的说法 |
|---|---|
| `thread-median5.json`, `../threads/median5.md` | 一句话需求 → 智能体 `sa_interpret` 返回 custom → `sa_studio_open` → `sa_run` → 回复；每一步工具调用（`tools[]`）与账本（`usage`） |
| `studio-median5*.json`, `bundle-median5.json` | 设计工作室：SPEC.md、独立参考模型源码、golden.py、design.json、4 个修订及每个修订的检查摘要 |
| `job-median5.json` | 运行的每一轮：`result` 事件（指标、关键路径、仿真日志）、`revising`/`revised` 事件、`plan` 事件（诊断、候选、stop_reason） |
| `trust-median5.json`, `formal-median5.json`, `mutation-median5.json` | 信任条：独立参考 648 样本一致；SymbiYosys 等价（深度 22）与流属性 PASS；变异 2/6（4 个漏网，如实） |
| `median5.sv`, `package-median5.zip` | 交付的 RTL 与交付包（AXI4-Stream 包装、测试台、向量、报告、SPEC/golden/reference） |
| `cic-local/` | 生成器路径的完整样例：CIC 抽取滤波器 271.8 MHz，形式 PASS，变异 8/8 —— 跑在开发机检出上（`results/20261009-141103-d59f`、对话 JSON、产品 API 快照与 SymbiYosys 日志），不在生产服务器 |
| `tournament-fir32.json` | 两种规划器同一规格并行 |
| `bench.json`, `catalog.json`, `health.json` | 夜间回归、实测目录、服务状态（模型、器件、设计种类含 rtl:<id>） |

## 边界样例（boundary/）
| 文件 | 支撑的说法 |
|---|---|
| `job-median5-stop-at-bound.json` | 旋钮穷尽后规划器停在「no legal candidate」，`stop_reason` 说明了依据，没有编造更多轮 |
| `studio-fft.json`, `job-fft-after-fix.json`, `trust-fft.json` | 任一生成器的设计可以分叉：64 点 FFT 在 Gowin 上 LUT ≤ 1200 的边界内（1191）可行；参考 = 生成器向量 |
| `../agh/agh-sessions.json`（设计师会话） | 注册时的旋钮值检查：设计师声明 DATA_W 的其他值，`sa_studio_submit` 的 `register-knob1` 失败，设计师收窄范围后通过 |

## 失败样例（failure/）
| 文件 | 支撑的说法 |
|---|---|
| `studio-median5-checks.tgz` | 检查 101543：SymbiYosys 流属性在第 1 步给出反例（`props.sv`、`logfile.txt`）；设计师修改后 102125 全部通过；`register*/report.json` 是注册门禁 |
| `studio-median5-first.json`, `job-median5-first-failed.json`, `thread-bench-median5.json` | 一次设计师在不存在的结构上空转两小时的记录，对应运行因此失败；由此加入的单轮超时与 SESSION_BUSY 跟随（代码提交 850ffaf、cf8a3d3） |
| `job-fft-before-fix.json` | 同一设计在仿真模型 bug 修复前被误判「综合后仿真失败」（Gowin SP/SPX9 模型把 ROM 初始化清零，提交 6a82fe2） |
| `failed-runs.json` | 服务器上所有以 failed 结束的运行，按产品自己的列表 |

## AGH 执行记录（agh/）
`agh-sessions.json`：对话智能体、设计师会话（中值滤波器、FFT 分叉、第二个中值）的 `turns[]`（模型、token、费用、用时）
与 `nodes[]`（每次工具调用的名称、参数预览、结果预览、状态），由 `deploy/scripts/agh-export.py` 通过 AGH 的
`session.projectUI` 导出。
MD
echo "bundle at $O/"
du -sh "$O"
