"""Build the 运行记录 archive: per-case timelines (Markdown), the index README, the project report PDF
(Simplified Chinese), and the zip.

    python3 report/collect.py dist/silicon-agent_run-records        # the records (production + checkout)
    /tmp/pdfenv/bin/python report/build.py dist/silicon-agent_run-records

Needs pypdf (for the table of contents' page numbers and to put the cover in front) and Playwright's
Chromium (report/render.mjs, PLAYWRIGHT_MJS). Every figure in the report is computed from a record file
of the archive (report/records.py); the prose says where each comes from.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import records as R  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
e = lambda s: html.escape(str(s if s is not None else ""))  # noqa: E731
fmt = lambda x, d=1: "—" if x is None else f"{x:.{d}f}"  # noqa: E731


def tok(n: int) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


# =============================================================================================== facts
def studio_designer_checks(sess: dict) -> list[dict]:
    """The designer's own sa_studio_check / sa_studio_submit calls in its session, with the first failing step."""
    out = []
    for n in R.agh_tools(sess):
        if n.get("name") not in ("sa_studio_check", "sa_studio_submit"):
            continue
        res = n.get("resultPreview") or ""
        ok = bool(re.search(r'^\{"ok":true', res))
        bad = re.search(r'"name":"([^"]+)","ok":false', res)
        err = re.search(r'"error":"([^"]{1,200})', res)
        out.append({"seq": n["seq"], "tool": n["name"], "ok": ok, "failed": bad.group(1) if bad else None, "error": err.group(1) if err and not ok else None})
    return out


def assistant_texts(sess: dict) -> list[dict]:
    return [{"seq": n["seq"], "text": n["text"]} for n in sess.get("nodes") or [] if n.get("kind") == "assistant" and (n.get("text") or "").strip()]


def facts(root: Path) -> dict:
    F: dict = {}
    sysd = root / "system"
    F["health"] = R.load(sysd / "health.json")
    F["versions"] = R.load(sysd / "versions.json")
    F["schema"] = R.load(sysd / "schema.json")
    plugin = (sysd / "agh-plugin_index.mjs").read_text()
    F["tools"] = sorted(set(re.findall(r"name: *['\"](sa_[a-z_]+)['\"]", plugin)))
    F["bench"] = R.load(sysd / "bench.json")

    # ---- case 1: the median filter
    c = root / "case1_median5_studio"
    th = R.load(c / "01_conversation" / "thread.json")
    chat = R.agh_session(c / "02_agent_AGH" / "agh_session_chat.json")
    des = R.agh_session(c / "03_studio" / "agh_session_designer.json")
    st = R.load(c / "03_studio" / "server_record" / "studio.json")
    job = R.load(c / "04_run" / "job.json")
    msgs = th["messages"]
    reply = [m for m in msgs if m["role"] == "assistant" and m.get("kind") == "text"][-1]
    interp = next(n for n in R.agh_tools(chat) if n["name"] == "sa_interpret")
    F["med"] = {
        "thread": th, "request": next(m["text"] for m in msgs if m["role"] == "user"), "mode": th.get("mode"),
        "reply": reply, "chat_turns": R.agh_turns(chat), "chat_tools": R.tool_rows(chat), "n_tools": len(R.agh_tools(chat)),
        "interpret_result": interp.get("resultPreview") or "",
        "studio": st, "spec": (c / "03_studio" / "SPEC.md").read_text(), "reference": (c / "03_studio" / "reference.py").read_text(),
        "golden": (c / "03_studio" / "work" / "golden.py").read_text(), "design_json": R.load(c / "03_studio" / "work" / "design.json"),
        "bundle": R.load(c / "03_studio" / "bundle.json"), "checks": R.studio_checks(c / "03_studio" / "server_record"),
        "designer_turns": R.agh_turns(des), "designer_checks": studio_designer_checks(des), "designer_texts": assistant_texts(des),
        "designer_tools": R.agh_tools(des),
        "props_sv": (c / "03_studio" / "server_record" / "checks" / "101543" / "props" / "props.sv").read_text(),
        "job": job, "rounds": R.run_rounds(job), "best": R.best(job), "run_s": R.run_seconds(job),
        "plans": [ev for ev in job["events"] if ev["kind"] == "plan"],
        "trust": R.load(c / "05_verification" / "trust.json"), "formal": R.load(c / "05_verification" / "formal.json"),
        "mutation": R.load(c / "05_verification" / "mutation.json"),
        "package": sorted(zipfile.ZipFile(c / "06_delivery" / "package.zip").namelist()),
    }
    # ---- case 2: the CIC (generator path, built-in chat, development checkout)
    c = root / "case2_cic_generator"
    cj = R.load(c / "05_verification" / "api_snapshot" / "job-cic.json")
    cth = R.load(c / "01_conversation" / "thread.json")
    F["cic"] = {"job": cj, "rounds": R.run_rounds(cj), "best": R.best(cj), "trust": R.load(c / "05_verification" / "api_snapshot" / "trust-cic.json"),
                "thread": cth, "request": next(m["text"] for m in cth["messages"] if m["role"] == "user"),
                "package": [f["name"] for f in R.load(c / "05_verification" / "api_snapshot" / "package-cic.json")["files"]],
                "formal_mut": R.load(c / "05_verification" / "symbiyosys_and_mutation" / "cic_formal_mutation.json")}
    # ---- case 3: the tournament
    c = root / "case3_tournament"
    tth = R.load(c / "01_conversation" / "thread.json")
    tchat = R.agh_session(c / "02_agent_AGH" / "agh_session_chat.json")
    tjobs = {p.name.removeprefix("run_"): R.load(p / "job.json") for p in sorted(c.glob("run_*"))}
    F["tour"] = {"thread": tth, "request": next(m["text"] for m in tth["messages"] if m["role"] == "user"),
                 "reply": [m for m in tth["messages"] if m["role"] == "assistant" and m.get("kind") == "text"][-1],
                 "tools": R.tool_rows(tchat), "turns": R.agh_turns(tchat), "tournament": R.load(c / "tournament.json"),
                 "jobs": {k: {"job": j, "best": R.best(j), "rounds": R.run_rounds(j)} for k, j in tjobs.items()}}
    # ---- case 4: the FFT fork
    c = root / "case4_fft_fork_gowin"
    fst = R.load(c / "03_studio" / "studio.json")
    fdes = R.agh_session(c / "03_studio" / "agh_session_designer.json")
    jb, ja = R.load(c / "04_run_before_fix" / "job.json"), R.load(c / "04_run_after_fix" / "job.json")
    fix = next(c.glob("fix_*_gowin_bram_sim_model.diff"))
    F["fft"] = {"studio": fst, "spec": (c / "03_studio" / "SPEC.md").read_text(), "designer_turns": R.agh_turns(fdes), "designer_checks": studio_designer_checks(fdes),
                "before": {"job": jb, "rounds": R.run_rounds(jb)}, "after": {"job": ja, "rounds": R.run_rounds(ja), "best": R.best(ja)},
                "fix_name": fix.name, "fix_hash": fix.name.split("_")[1], "fix_diff": fix.read_text(),
                "bundle": R.load(c / "03_studio" / "bundle.json")}
    # ---- case 5: failures and recovery
    c = root / "case5_failure_recovery"
    rth = R.load(c / "01_conversation" / "thread.json")
    rchat = R.agh_session(c / "02_agent_AGH" / "agh_session_chat.json")
    s1, s2 = R.load(c / "03_studio_first" / "studio.json"), R.load(c / "03_studio_second" / "studio.json")
    d1 = R.agh_session(c / "03_studio_first" / "agh_session_designer.json")
    j1, j2 = R.load(c / "04_run_first_failed" / "job.json"), R.load(c / "04_run_second" / "job.json")
    F["rec"] = {"thread": rth, "request": next(m["text"] for m in rth["messages"] if m["role"] == "user"), "chat_turns": R.agh_turns(rchat), "chat_tools": R.tool_rows(rchat),
                "studio1": s1, "studio2": s2, "designer1_turns": R.agh_turns(d1), "designer1_texts": assistant_texts(d1),
                "job1": {"job": j1, "rounds": R.run_rounds(j1), "notes": [ev["message"] for ev in j1["events"] if ev["kind"] == "note"]},
                "job2": {"job": j2, "rounds": R.run_rounds(j2), "best": R.best(j2), "notes": [ev["message"] for ev in j2["events"] if ev["kind"] == "note"]},
                "report": next((m for m in rth["messages"] if m.get("kind") == "report"), None), "fixes": (c / "fixes_git_log.txt").read_text()}
    # ---- case 6: the Vivado ladder
    lj = R.load(root / "case6_vivado_ladder" / "04_run" / "job.json")
    F["ladder"] = {"job": lj, "rounds": R.run_rounds(lj)}
    return F


# =============================================================================================== Markdown timelines
def md_tools(sess_tools: list[dict], limit: int = 600) -> list[str]:
    out = ["| # | 工具 | 状态 | 参数（预览） | 结果（预览） |", "|---|---|---|---|---|"]
    for n in sess_tools:
        cell = lambda s: R.brief(s, limit).replace("|", "\\|")  # noqa: E731
        out.append(f"| {n.get('seq')} | `{n.get('name')}` | {n.get('status')} | {cell(n.get('argsPreview'))} | {cell(n.get('resultPreview'))} |")
    return out


def md_turns(turns: list[dict], who: str) -> list[str]:
    out = [f"| {who}轮次 | 开始 | 结束 | 用时 | 模型 | 模型调用 | 输入/缓存/输出 token | 推理 token | 估算费用 |", "|---|---|---|---|---|---|---|---|---|"]
    for t in turns:
        out.append(f"| {t['turn']} | {t['started']} | {t['ended']} | {R.mmss(t['seconds'])} | {t['model']} | {t['calls']} | {tok(t['input'])}/{tok(t['cache'])}/{tok(t['output'])} | {tok(t['reasoning'])} | {R.usd(t['usd_micros'])} |")
    return out


def md_run(job: dict, rounds: list[dict]) -> list[str]:
    out = []
    ev = job["events"]
    t0 = ev[0]["t"] if ev else 0
    for x in ev:
        k = x["kind"]
        t = f"+{x['t'] - t0:.0f}s"
        if k == "memory":
            ls = x.get("lessons") or []
            out.append(f"- {t} **设计记忆召回**（第 {x['iteration']} 轮规划前）：{len(ls)} 条，情境 `{json.dumps(x.get('situation'), ensure_ascii=False)}`")
        elif k == "plan":
            out.append(f"- {t} **规划器（第 {x['iteration']} 轮）**：{R.brief(x.get('action'), 400)}")
            if x.get("diagnosis"):
                out.append(f"  - 诊断：{R.brief(x['diagnosis'], 1200)}")
            for cnd in x.get("candidates") or []:
                est = cnd.get("estimate") or {}
                out.append(f"  - 候选 `{R.ir_str(cnd.get('ir'))}` 估算 {fmt(est.get('fmax_mhz'), 0)} MHz / {est.get('lut', '—')} LUT {'（估算违约：' + ', '.join(cnd['est_violations']) + '）' if cnd.get('est_violations') else ''} — {R.brief(cnd.get('why'), 300)}")
            if x.get("stop"):
                out.append(f"  - **停止**：{R.brief(x.get('stop_reason'), 600)}")
        elif k == "evaluating":
            out.append(f"- {t} 实测第 {x['iteration']}{'.' + str(x['slot']) if x.get('slot') else ''} 轮：`{R.ir_str(x.get('ir'))}`")
        elif k == "stage":
            out.append(f"  - 网表仿真 {x.get('label') or x.get('stage')}：{'通过' if x.get('passed') else '失败' if x.get('passed') is False else '跳过'}")
        elif k == "result":
            m = x.get("metrics") or {}
            v = x.get("violations") or []
            cp = (m.get("critical_path") or [{}])[0].get("total_ns")
            out.append(f"- {t} **结果 {x['iteration']}{'.' + str(x['slot']) if x.get('slot') else ''}**：{fmt(m.get('fmax_mhz'))} MHz（种子 {', '.join(fmt(s) for s in m.get('fmax_seeds') or [])}）· {m.get('lut')} LUT · {m.get('ff')} FF · {m.get('dsp')} DSP · {m.get('bram')} BRAM · 关键路径 {fmt(cp, 2)} ns · 行为级仿真 {'通过' if m.get('sim_passed') else '失败'} · EDA {x.get('eda_seconds')} 秒 → {'**可行**' if not v else '违约：' + '；'.join(v)}")
        elif k == "revising":
            out.append(f"- {t} **改写请求 → 设计师**：{R.brief(x.get('instruction'), 1500)}")
        elif k == "revised":
            out.append(f"- {t} **设计师修订 #{x.get('rev')}（已通过检查）**：{R.brief(x.get('note'), 600)}")
        elif k == "lesson":
            ls = x.get("lesson") or {}
            out.append(f"- {t} 教训 `{ls.get('id')}`：{R.brief(ls.get('text'), 500)}")
        elif k == "note":
            out.append(f"- {t} 说明：{R.brief(x.get('message'), 400)}")
        elif k == "done":
            out.append(f"- {t} **运行结束**")
    return out


def md_checks(checks: list[dict]) -> list[str]:
    out = ["| 检查 | 结果 | 步骤 | 用时 |", "|---|---|---|---|"]
    for c in checks:
        steps = " · ".join(f"{'✓' if s.get('ok') else '✕'} {s['name']}{' (' + str(s['samples']) + ' 样本)' if s.get('samples') else ''}{' (' + str(s.get('killed')) + '/' + str(s.get('total')) + ')' if s.get('total') else ''}" for s in c["steps"])
        out.append(f"| `{c['tag']}` | {'通过' if c['ok'] else '**失败**'} | {steps} | {c['seconds']} 秒 |")
    bad = [c for c in checks if not c["ok"]]
    for c in bad:
        s = next(x for x in c["steps"] if not x.get("ok"))
        out += ["", f"**检查 `{c['tag']}` 失败的步骤：{s['name']}**", "", "```", (s.get("log") or s.get("error") or json.dumps(s.get("mismatch"), ensure_ascii=False) or "")[-1500:], "```"]
    return out


def md_studio(st: dict, checks: list[dict], dturns: list[dict], dtools: list[dict], spec: str) -> list[str]:
    out = [f"- 工作室 `{st['id']}` · 名称 `{st.get('name')}` · 状态 **{st.get('status')}** · 注册为 `{st.get('design')}` · {st.get('revisions')} 个修订",
           f"- 参考模型：{(st.get('reference') or {}).get('kind')}" + (f"（生成器向量：{st['origin']['design']} 运行 {st['origin']['job']} {st['origin'].get('label')}）" if st.get("origin") else "（独立会话只凭 SPEC.md 写出，见 `03_studio/reference.py`）"),
           "", "### 规格 SPEC.md", "", "```", spec.strip()[:6000], "```", "", "### 流水线步骤（工作室记录）", ""]
    for s in st.get("steps") or []:
        out.append(f"- {R.when(s.get('t'))} {'✓' if s.get('ok') else '✕' if s.get('ok') is False else '·'} {s['name']}" + (f" → rev {s['rev']}" if s.get("rev") is not None else "")
                   + (f"：{R.brief(s.get('instruction'), 500)}" if s.get("instruction") else "") + (f"（{R.brief(s.get('error'), 300)}）" if s.get("error") else ""))
    out += ["", "### 修订", ""]
    for r in (st.get("bundle") or {}).get("revisions") or []:
        ch = r.get("check") or {}
        g = ch.get("golden_vs_reference") or {}
        mu = ch.get("mutation") or {}
        out.append(f"- rev {r['rev']}（{R.when(r.get('created'))}）：{R.brief(r.get('note'), 400)} — 参考一致 {g.get('samples', '—')} 样本 · 行为级 {'✓' if ch.get('sim') else '✕'} · 流属性 {'✓' if ch.get('properties') else '✕'} · 变异 {mu.get('killed', '—')}/{mu.get('total', '—')}")
    out += ["", "### 设计师会话（Agnes Harness）的轮次与账本", ""] + md_turns(dturns, "设计师")
    out += ["", "### 检查报告（每一次 sa_studio_check / 注册门禁）", ""] + md_checks(checks)
    out += ["", f"### 设计师会话的全部工具调用（{len(dtools)} 次，按顺序）", ""] + md_tools(dtools, 400)
    return out


def write_timelines(root: Path, F: dict) -> None:
    # case 1
    M = F["med"]
    c = root / "case1_median5_studio"
    chat_sess = R.agh_session(c / "02_agent_AGH" / "agh_session_chat.json")
    L = ["# 案例一：5 点滑动中值滤波器 —— 一句话到交付（设计工作室路径）", "",
         "本文件由 `report/build.py` 从同目录的原始记录自动生成；每一行都能在对应 JSON 里找到。", "",
         "## 1. 任务输入", "", f"- 对话 `{M['thread']['id']}`，时间 {R.when(M['thread'].get('created'))}；这一轮实际运行模式 `{M['thread'].get('agh_mode_applied')}`（Agnes Harness 账本中全部模型调用为 {M['chat_turns'][0]['model']}；对话的 mode 字段之后被切到 `{M['mode']}`）", "", "> " + M["request"], "",
         "## 2. 智能体执行（Agnes Harness 会话 `sa-thread-" + M["thread"]["id"] + "`）", ""] + md_turns(M["chat_turns"], "对话") + ["", f"全部 {M['n_tools']} 次工具调用（原始记录：`02_agent_AGH/agh_session_chat.json`）：", ""] + md_tools(R.agh_tools(chat_sess))
    L += ["", "## 3. 设计工作室", ""] + md_studio(M["studio"], M["checks"], M["designer_turns"], M["designer_tools"], M["spec"])
    L += ["", "## 4. 运行（规划闭环，完整事件流 `04_run/results/events.jsonl`）", ""] + md_run(M["job"], M["rounds"])
    t, fo, mu = M["trust"], M["formal"], M["mutation"]
    L += ["", "## 5. 结果验证", "",
          f"- 参考来源：{t['reference']['kind']}，{t['reference']['samples']} 个样本逐位一致（rev {t['reference']['rev']}，共 {t['reference']['revisions']} 个修订）",
          f"- 行为级仿真：{'通过' if t['stages'].get('behavioral') else '失败'}；综合后网表仿真：{'通过' if t['stages'].get('post_synth_func') else '失败'}",
          f"- 布局布线种子：{', '.join(fmt(s) for s in t['seeds'])} MHz（中位数为报告值）",
          f"- 形式验证（SymbiYosys）：等价 {'PASS' if (fo.get('equivalence') or {}).get('ok') else 'FAIL'}（深度 {(fo.get('equivalence') or {}).get('depth')}），流协议属性 {'PASS' if (fo.get('properties') or {}).get('ok') else 'FAIL'}",
          f"- 变异测试：{mu['killed']}/{mu['total']} 被抓住，漏网 {', '.join(mu.get('survived') or []) or '无'}",
          "", "## 6. 智能体的回复", "", M["reply"]["text"], "",
          f"账本：{M['reply']['usage'].get('calls')} 次模型调用 · {tok((M['reply']['usage'].get('input') or 0) + (M['reply']['usage'].get('cache_read') or 0) + (M['reply']['usage'].get('output') or 0))} tokens · {R.usd(M['reply']['usage'].get('usd_micros'))} · {R.mmss((M['reply']['usage'].get('ms') or 0) / 1000)} · {M['reply']['usage'].get('model')}",
          "", "## 7. 交付包（`06_delivery/package.zip`）", ""] + [f"- `{n}`" for n in M["package"]]
    (c / "00_timeline.md").write_text("\n".join(L) + "\n")

    # case 2
    C = F["cic"]
    c = root / "case2_cic_generator"
    L = ["# 案例二：CIC 抽取滤波器 —— 生成器路径（内置对话，开发机检出上运行）", "", "> " + C["request"], "",
         f"- 运行 `{C['job']['id']}`，设计 `{C['job']['spec']['design']}`，器件 `{C['job']['spec']['backend']}`，约束 `{json.dumps(C['job']['spec']['constraints'], ensure_ascii=False)}`",
         f"- 固定参数 `{json.dumps(C['job']['spec']['locked'], ensure_ascii=False)}`", "", "## 运行事件", ""] + md_run(C["job"], C["rounds"])
    t = C["trust"]
    L += ["", "## 验证", "", f"- 形式验证：等价 {'PASS' if t['formal']['equivalence'] else 'FAIL'}，流协议 {'PASS' if t['formal']['properties'] else 'FAIL'}，深度 {t['formal']['depth']}",
          f"- 变异测试：{t['mutation']['killed']}/{t['mutation']['total']}", f"- 种子：{', '.join(fmt(s) for s in t['seeds'])} MHz", "",
          "SymbiYosys 原始日志在 `05_verification/symbiyosys_and_mutation/`，产品 API 快照在 `05_verification/api_snapshot/`。", "", "## 交付包文件", ""] + [f"- `{n}`" for n in C["package"]]
    (c / "00_timeline.md").write_text("\n".join(L) + "\n")

    # case 3
    T = F["tour"]
    c = root / "case3_tournament"
    tsess = R.agh_session(c / "02_agent_AGH" / "agh_session_chat.json")
    L = ["# 案例三：锦标赛 —— 两种规划器跑同一份规格（Agnes Harness）", "", "> " + T["request"], "", "## 智能体执行", ""] + md_turns(T["turns"], "对话") + [""] + md_tools(R.agh_tools(tsess))
    for jid, J in T["jobs"].items():
        L += ["", f"## 运行 `{jid}`（{J['job']['spec'].get('tournament', {}).get('label')}）", ""] + md_run(J["job"], J["rounds"])
    L += ["", "## 智能体的回复", "", T["reply"]["text"]]
    (c / "00_timeline.md").write_text("\n".join(L) + "\n")

    # case 4
    X = F["fft"]
    c = root / "case4_fft_fork_gowin"
    fsess = R.agh_session(c / "03_studio" / "agh_session_designer.json")
    L = ["# 案例四：从生成器出走 —— 64 点 FFT 在 Gowin 上的结构重构，以及它暴露的仿真模型缺陷", "", "## 设计工作室（分叉）", ""] + md_studio(X["studio"], R.studio_checks(c / "03_studio" / "server_record"), X["designer_turns"], R.agh_tools(fsess), X["spec"])
    L += ["", "## 修复前的运行（修订 1/2 被误判为综合后仿真失败）", ""] + md_run(X["before"]["job"], X["before"]["rounds"])
    L += ["", f"## 仿真模型修复（`{X['fix_name']}`）", "", "```diff", X["fix_diff"][:6000], "```"]
    L += ["", "## 修复后的运行", ""] + md_run(X["after"]["job"], X["after"]["rounds"])
    (c / "00_timeline.md").write_text("\n".join(L) + "\n")

    # case 5
    Y = F["rec"]
    c = root / "case5_failure_recovery"
    rsess = R.agh_session(c / "02_agent_AGH" / "agh_session_chat.json")
    d1 = R.agh_session(c / "03_studio_first" / "agh_session_designer.json")
    d2 = R.agh_session(c / "03_studio_second" / "agh_session_designer.json")
    L = ["# 案例五：失败与恢复 —— 设计师空转、会话忙、运行失败，以及随后的修复", "", "> " + Y["request"], "", "## 对话智能体", ""] + md_turns(Y["chat_turns"], "对话") + [""] + md_tools(R.agh_tools(rsess), 300)
    L += ["", "## 第一个工作室（设计师在不存在的结构上搜索）", ""] + md_studio(Y["studio1"], R.studio_checks(c / "03_studio_first" / "server_record"), Y["designer1_turns"], R.agh_tools(d1), (c / "03_studio_first" / "SPEC.md").read_text())
    L += ["", "## 第一次运行（失败）", ""] + md_run(Y["job1"]["job"], Y["job1"]["rounds"])
    L += ["", "## 第二个工作室", ""] + md_studio(Y["studio2"], R.studio_checks(c / "03_studio_second" / "server_record"), R.agh_turns(d2), R.agh_tools(d2), (c / "03_studio_second" / "SPEC.md").read_text())
    L += ["", "## 第二次运行", ""] + md_run(Y["job2"]["job"], Y["job2"]["rounds"])
    L += ["", "## 这些失败带来的修复（git log）", "", "```", Y["fixes"], "```"]
    (c / "00_timeline.md").write_text("\n".join(L) + "\n")

    # case 6
    Z = F["ladder"]
    c = root / "case6_vivado_ladder"
    L = ["# 案例六：Spartan-7 / Vivado 上的五级仿真阶梯", "", f"- 运行 `{Z['job']['id']}`，规格 `{json.dumps(Z['job']['spec'], ensure_ascii=False)}`", ""] + md_run(Z["job"], Z["rounds"])
    (c / "00_timeline.md").write_text("\n".join(L) + "\n")
    print("timelines written")


# =============================================================================================== charts (SVG)
INK, INK2, GRID, S1 = "#0b0b0b", "#52514e", "#e6e5e1", "#2a78d6"


def _ticks(hi: float) -> list[float]:
    for step in (10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if hi / step <= 6:
            return [i * step for i in range(int(hi // step) + 2)]
    return [0, hi]


def chart_line(points: list[tuple[str, float, bool]], target: float | None, unit: str, w=660, h=250, label_idx=()) -> str:
    """One series: a 2px line, 9px markers (filled = feasible, hollow = violates a constraint), a dashed target."""
    ml, mr, mt, mb = 52, 18, 18, 34
    pw, ph = w - ml - mr, h - mt - mb
    hi = max([v for _, v, _ in points] + ([target] if target else [])) * 1.08
    ticks = _ticks(hi)
    top = ticks[-1]
    X = lambda i: ml + (i + 0.5) * pw / len(points)  # noqa: E731
    Y = lambda v: mt + ph - v / top * ph  # noqa: E731
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" xmlns="http://www.w3.org/2000/svg" style="font-family:inherit">']
    for t in ticks:
        s.append(f'<line x1="{ml}" x2="{w - mr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{GRID}" stroke-width="1"/><text x="{ml - 8}" y="{Y(t) + 4:.1f}" text-anchor="end" font-size="11" fill="{INK2}">{t:g}</text>')
    s.append(f'<text x="{ml - 40}" y="{mt + ph / 2}" font-size="11" fill="{INK2}" transform="rotate(-90 {ml - 40} {mt + ph / 2})" text-anchor="middle">{e(unit)}</text>')
    if target:
        s.append(f'<line x1="{ml}" x2="{w - mr}" y1="{Y(target):.1f}" y2="{Y(target):.1f}" stroke="{INK2}" stroke-width="1.2" stroke-dasharray="5 4"/>')
        s.append(f'<text x="{w - mr - 4}" y="{Y(target) - 6:.1f}" text-anchor="end" font-size="11" fill="{INK2}">约束 {target:g} {e(unit)}</text>')
    s.append('<polyline fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round" points="%s"/>' % (S1, " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v, _) in enumerate(points))))
    for i, (lab, v, ok) in enumerate(points):
        s.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4.5" fill="{S1 if ok else "#ffffff"}" stroke="{S1}" stroke-width="2"/>')
        s.append(f'<text x="{X(i):.1f}" y="{h - 12}" text-anchor="middle" font-size="11" fill="{INK2}">{e(lab)}</text>')
        if i in label_idx:
            s.append(f'<text x="{X(i):.1f}" y="{Y(v) - 10:.1f}" text-anchor="middle" font-size="11.5" font-weight="600" fill="{INK}">{v:.1f}</text>')
    s.append("</svg>")
    return "".join(s)


def chart_bars(points: list[tuple[str, float]], unit: str, limit: float | None = None, w=660, h=230, label_idx=()) -> str:
    ml, mr, mt, mb = 52, 18, 18, 34
    pw, ph = w - ml - mr, h - mt - mb
    hi = max([v for _, v in points] + ([limit] if limit else [])) * 1.08
    ticks = _ticks(hi)
    top = ticks[-1]
    bw = min(34, pw / len(points) - 10)
    X = lambda i: ml + (i + 0.5) * pw / len(points)  # noqa: E731
    Y = lambda v: mt + ph - v / top * ph  # noqa: E731
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" xmlns="http://www.w3.org/2000/svg" style="font-family:inherit">']
    for t in ticks:
        s.append(f'<line x1="{ml}" x2="{w - mr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{GRID}" stroke-width="1"/><text x="{ml - 8}" y="{Y(t) + 4:.1f}" text-anchor="end" font-size="11" fill="{INK2}">{t:g}</text>')
    s.append(f'<text x="{ml - 40}" y="{mt + ph / 2}" font-size="11" fill="{INK2}" transform="rotate(-90 {ml - 40} {mt + ph / 2})" text-anchor="middle">{e(unit)}</text>')
    for i, (lab, v) in enumerate(points):
        x0, y0 = X(i) - bw / 2, Y(v)
        hh = mt + ph - y0
        r = min(4, hh)
        s.append(f'<path d="M{x0:.1f},{mt + ph:.1f} V{y0 + r:.1f} Q{x0:.1f},{y0:.1f} {x0 + r:.1f},{y0:.1f} H{x0 + bw - r:.1f} Q{x0 + bw:.1f},{y0:.1f} {x0 + bw:.1f},{y0 + r:.1f} V{mt + ph:.1f} Z" fill="{S1}"/>')
        s.append(f'<text x="{X(i):.1f}" y="{h - 12}" text-anchor="middle" font-size="11" fill="{INK2}">{e(lab)}</text>')
        if i in label_idx:
            s.append(f'<text x="{X(i):.1f}" y="{y0 - 6:.1f}" text-anchor="middle" font-size="11.5" font-weight="600" fill="{INK}">{v:g}</text>')
    if limit:
        s.append(f'<line x1="{ml}" x2="{w - mr}" y1="{Y(limit):.1f}" y2="{Y(limit):.1f}" stroke="{INK2}" stroke-width="1.2" stroke-dasharray="5 4"/><text x="{w - mr - 4}" y="{Y(limit) - 6:.1f}" text-anchor="end" font-size="11" fill="{INK2}">约束 {limit:g} {e(unit)}</text>')
    s.append("</svg>")
    return "".join(s)


# =============================================================================================== diagrams (SVG)
def _box(x, y, w, h, title, sub="", fill="#f4f6f9", stroke="#c9d3df", tc=INK):
    t = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
    t += f'<text x="{x + w / 2}" y="{y + (h / 2 - 4 if sub else h / 2 + 5)}" text-anchor="middle" font-size="13" font-weight="600" fill="{tc}">{e(title)}</text>'
    if sub:
        for k, line in enumerate(sub.split("\n")):
            t += f'<text x="{x + w / 2}" y="{y + h / 2 + 13 + k * 13}" text-anchor="middle" font-size="10.5" fill="{INK2}">{e(line)}</text>'
    return t


def _arrow(x1, y1, x2, y2, label="", dash=False, lx=None, ly=None):
    t = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#7a8796" stroke-width="1.4" marker-end="url(#ah)"{" stroke-dasharray=\"4 3\"" if dash else ""}/>'
    if label:
        t += f'<text x="{lx if lx is not None else (x1 + x2) / 2 + 4}" y="{ly if ly is not None else (y1 + y2) / 2 - 4}" font-size="10" fill="{INK2}">{e(label)}</text>'
    return t


DEFS = '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#7a8796"/></marker></defs>'


def diagram_architecture() -> str:
    W, H = 700, 470
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" style="font-family:inherit">', DEFS]
    s.append(_box(20, 14, 660, 44, "入口：对话 · 工作台 · sa 命令行 · HTTP API · MCP", "", "#eef4fc", "#b9d0ee"))
    s.append(_arrow(350, 58, 350, 82))
    s.append(_box(20, 82, 660, 58, "Agnes Harness 智能体会话（对话 / 设计师 / 独立参考 / 作者）", "DeepSeek 模型循环 · 工具调用记录 · 钩子（方法注入、权限）· 账本 · 恢复", "#f3f0fb", "#cfc6ee"))
    s.append(_arrow(350, 140, 350, 164, "agh-plugin 的 30 个 sa_* 工具（HTTP）", lx=358, ly=156))
    s.append(_box(20, 164, 660, 44, "作业服务器 silicon_agent/server.py：规格、运行队列、工作室、证据、交付", ""))
    s.append(_arrow(185, 208, 185, 232))
    s.append(_arrow(515, 208, 515, 232))
    s.append(_box(20, 232, 330, 64, "11 个参数化生成器", "IR 旋钮 → 确定性发射 SystemVerilog\nmatmul FIR CIC IIR NCO CORDIC FFT conv2d …"))
    s.append(_box(370, 232, 310, 64, "设计工作室", "设计师写 RTL · 独立参考模型对拍\n检查门禁 → 注册为 rtl:<id>"))
    s.append(_arrow(185, 296, 300, 322))
    s.append(_arrow(515, 296, 400, 322))
    s.append(_box(20, 322, 660, 58, "验证阶梯（每一轮都真跑）", "行为级仿真 → 综合 → 网表仿真 → 3 粒种子布局布线 → [Vivado 实现后功能/时序仿真] · 形式验证 · 变异测试", "#eef8f2", "#b9dec7"))
    s.append(_arrow(350, 380, 350, 404, "指标 + 关键路径", lx=358, ly=396))
    s.append(_box(20, 404, 430, 54, "规划器（DeepSeek）", "诊断 → 候选（筛选估算排序）→ 并行实测\n旋钮够不到关键路径 → revise：请设计师改写"))
    s.append(_box(470, 404, 210, 54, "设计记忆", "教训：确认/反驳计分\n下次按情境召回"))
    s.append(f'<path d="M20,431 C-2,431 -2,264 18,264" fill="none" stroke="#7a8796" stroke-width="1.4" stroke-dasharray="4 3" marker-end="url(#ah)"/>')
    s.append(_arrow(450, 431, 470, 431))
    s.append("</svg>")
    return "".join(s)


def diagram_studio() -> str:
    W, H = 700, 292
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" style="font-family:inherit">', DEFS]
    s.append(_box(10, 64, 170, 64, "SPEC.md", "功能规格：算术、位宽、\n截断、速率、对齐、复位"))
    s.append(_arrow(180, 84, 218, 62))
    s.append(_arrow(180, 110, 218, 140))
    s.append(_box(220, 36, 190, 52, "设计师会话", "写 rtl/*.sv · golden.py · design.json", "#f3f0fb", "#cfc6ee"))
    s.append(_box(220, 112, 190, 60, "独立参考会话（无工具）", "只看 SPEC.md 与接口\n写 reference(params, xs)", "#f3f0fb", "#cfc6ee"))
    s.append(_arrow(410, 62, 438, 76))
    s.append(_arrow(410, 140, 438, 124))
    labels = ["① elaborate（iverilog）", "② golden 对参考（隔离 Python 子进程）", "③ 行为级仿真：回放参考向量", "④ SymbiYosys 流协议属性", "⑤ 每个声明的旋钮值", "⑥ 变异分数 ≥ 0.6"]
    for i, lab in enumerate(labels):
        s.append(f'<rect x="440" y="{38 + i * 24}" width="250" height="21" rx="4" fill="#eef8f2" stroke="#b9dec7"/><text x="449" y="{53 + i * 24}" font-size="10.5" fill="{INK}">{e(lab)}</text>')
    s.append('<path d="M565,36 C565,22 330,22 315,34" fill="none" stroke="#b42318" stroke-width="1.4" stroke-dasharray="4 3" marker-end="url(#ah)"/>')
    s.append('<text x="440" y="13" text-anchor="middle" font-size="10" fill="#b42318">任一步失败：完整报告（日志、首个不一致样本、属性源码与反例）回到设计师</text>')
    s.append(_box(10, 230, 300, 54, "注册为 rtl:<id>", "和生成器一样可跑；修订号 = IR 字段 rev", "#eef4fc", "#b9d0ee"))
    s.append('<path d="M565,181 L565,257 L314,257" fill="none" stroke="#0a7d35" stroke-width="1.4" marker-end="url(#ah)"/>')
    s.append('<text x="440" y="251" text-anchor="middle" font-size="10" fill="#0a7d35">全部通过</text>')
    s.append("</svg>")
    return "".join(s)


def diagram_sequence() -> str:
    W, H = 700, 430
    cols = [("用户 / 浏览器", 60), ("作业服务器", 210), ("AGH 守护进程", 360), ("DeepSeek", 500), ("sa_* 工具 → 服务器", 630)]
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" style="font-family:inherit">', DEFS]
    for name, x in cols:
        s.append(f'<rect x="{x - 58}" y="8" width="116" height="26" rx="6" fill="#f4f6f9" stroke="#c9d3df"/><text x="{x}" y="25" text-anchor="middle" font-size="11.5" font-weight="600" fill="{INK}">{e(name)}</text>')
        s.append(f'<line x1="{x}" y1="34" x2="{x}" y2="{H - 8}" stroke="#d6dbe1" stroke-dasharray="3 3"/>')
    steps = [(60, 210, "POST /api/threads/<id>/messages"), (210, 360, "session/prompt（会话 sa-thread-<id>）"), (360, 360, "context 钩子注入方法；tool_call 钩子检查权限"),
             (360, 500, "推理（模型调用，计入账本）"), (500, 360, "工具调用：sa_interpret / sa_studio_open / sa_run …"), (360, 630, "插件执行工具 → HTTP /api/…"),
             (630, 210, "服务器：解释、开工作室、排队运行、EDA"), (210, 630, "JSON 结果（严格 JSON，≤120 秒）"), (630, 360, "工具结果写入会话记录"),
             (360, 500, "继续推理 … 直到给出答复"), (360, 210, "session/update 流：说明、工具轨迹、最终文本"), (210, 60, "对话里出现运行卡 / 工作室卡 / 回复 + 账本")]
    y = 56
    for a, b, lab in steps:
        if a == b:
            s.append(f'<rect x="{a - 4}" y="{y - 10}" width="8" height="18" fill="#cfc6ee"/><text x="{a + 10}" y="{y + 3}" font-size="10.5" fill="{INK2}">{e(lab)}</text>')
        else:
            s.append(_arrow(a, y, b + (-6 if b > a else 6), y))
            s.append(f'<text x="{min(a, b) + 6}" y="{y - 5}" font-size="10.5" fill="{INK}">{e(lab)}</text>')
        y += 31
    s.append("</svg>")
    return "".join(s)


# =============================================================================================== HTML
CSS = """
@page { size: A4; margin: 18mm 17mm 20mm 17mm; }
* { box-sizing: border-box; }
html { font-family: "STHeiti", "PingFang SC", "Microsoft YaHei", sans-serif; color: #1b1b1a; font-size: 10.4pt; line-height: 1.72; }
body { margin: 0; }
h1 { font-size: 18pt; margin: 22pt 0 10pt; padding-bottom: 6pt; border-bottom: 2px solid #2a78d6; letter-spacing: .02em; break-after: avoid; }
h1.brk { page-break-before: always; margin-top: 0; }
h1.first { page-break-before: auto; }
h2 { font-size: 13.2pt; margin: 16pt 0 6pt; color: #11335c; break-after: avoid; }
h3 { font-size: 11pt; margin: 12pt 0 4pt; color: #1b1b1a; break-after: avoid; }
p { margin: 4pt 0 6pt; text-align: justify; }
ul, ol { margin: 3pt 0 6pt; padding-left: 18pt; }
li { margin: 1.5pt 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 9pt; font-size: 8.9pt; line-height: 1.5; page-break-inside: auto; }
th, td { border: 1px solid #d9dde3; padding: 3.5pt 5pt; vertical-align: top; text-align: left; }
th { background: #eef2f7; font-weight: 600; }
tr { page-break-inside: avoid; }
tbody tr:nth-child(even) td { background: #fafbfc; }
code, .mono { font-family: "SF Mono", Menlo, "STHeiti", monospace; font-size: 8.6pt; }
td code { font-size: 8.2pt; word-break: normal; overflow-wrap: anywhere; }
td code.nw { white-space: nowrap; }
pre { font-family: "SF Mono", Menlo, "STHeiti", monospace; font-size: 7.9pt; line-height: 1.45; background: #f6f7f9; border: 1px solid #e1e4e8; border-radius: 4pt; padding: 6pt 8pt; white-space: pre-wrap; word-break: break-all; margin: 5pt 0 8pt; page-break-inside: auto; }
blockquote { margin: 6pt 0; padding: 5pt 10pt; border-left: 3px solid #2a78d6; background: #f3f7fd; color: #1b1b1a; }
.note { font-size: 8.8pt; color: #52514e; }
.kpi { display: flex; gap: 8pt; margin: 6pt 0 10pt; }
.kpi div { flex: 1; border: 1px solid #d9dde3; border-radius: 5pt; padding: 6pt 8pt; background: #fbfcfd; }
.kpi b { display: block; font-size: 14pt; color: #11335c; }
.kpi span { font-size: 8.5pt; color: #52514e; }
.fig { margin: 8pt 0 10pt; page-break-inside: avoid; }
.fig .cap { font-size: 8.8pt; color: #52514e; text-align: center; margin-top: 3pt; }
.toc { list-style: none; padding: 0; }
.toc li { display: flex; align-items: baseline; margin: 3pt 0; }
.toc li.l2 { padding-left: 16pt; font-size: 9.4pt; color: #3a3a38; }
.toc li .t { flex: 0 1 auto; }
.toc li .dots { flex: 1; border-bottom: 1px dotted #b8bcc2; margin: 0 4pt; transform: translateY(-3pt); }
.toc li .p { flex: 0 0 auto; font-variant-numeric: tabular-nums; }
.toc a { color: inherit; text-decoration: none; }
.mk { font-size: 1px; color: #ffffff; }
.ok { color: #0a7d35; font-weight: 600; } .bad { color: #b42318; font-weight: 600; }
.tag { display: inline-block; font-size: 8pt; padding: 0 5pt; border-radius: 3pt; background: #eef2f7; color: #11335c; margin-right: 3pt; }
.avoid { page-break-inside: avoid; }
"""


TAGS = r"(?:b|i|br|code|span|p|div|a|sub|sup|small|strong|em)"


def safe(h: str) -> str:
    """Escape a '<' that does not open one of the few tags the prose uses (placeholders like <id>, <tag>)."""
    return re.sub(r"<(?!/?" + TAGS + r"\b)", "&lt;", h)


class Doc:
    def __init__(self):
        self.parts: list[str] = []
        self.toc: list[tuple[int, str, str]] = []
        self.n1 = 0
        self.n2 = 0

    def h1(self, title: str, first=False, brk=False):
        self.n1 += 1
        self.n2 = 0
        hid = f"s{self.n1}"
        self.toc.append((1, hid, f"{self.n1}  {title}"))
        self.parts.append(f'<h1 id="{hid}" class="{"first" if first else ""} {"brk" if brk else ""}">{self.n1}　{e(title)}<span class="mk">QQ{hid}QQ</span></h1>')

    def h2(self, title: str):
        self.n2 += 1
        hid = f"s{self.n1}_{self.n2}"
        self.toc.append((2, hid, f"{self.n1}.{self.n2}  {title}"))
        self.parts.append(f'<h2 id="{hid}">{self.n1}.{self.n2}　{e(title)}<span class="mk">QQ{hid}QQ</span></h2>')

    def h3(self, title: str):
        self.parts.append(f"<h3>{e(title)}</h3>")

    def p(self, *htmls: str):
        for h in htmls:
            self.parts.append(f"<p>{safe(h)}</p>")

    def raw(self, h: str):
        self.parts.append(h)

    def ul(self, items: list[str]):
        self.parts.append("<ul>" + "".join(f"<li>{safe(i)}</li>" for i in items) + "</ul>")

    def table(self, head: list[str], rows: list[list[str]], widths: list[str] | None = None, cls=""):
        cg = "".join(f'<col style="width:{w}">' for w in widths) if widths else ""
        self.parts.append(f'<table class="{cls}"><colgroup>{cg}</colgroup><thead><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
                          + "".join("<tr>" + "".join(f"<td>{safe(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")

    def pre(self, text: str, limit=4000):
        t = text if len(text) <= limit else text[:limit] + "\n…（截断，全文见压缩包）"
        self.parts.append(f"<pre>{e(t)}</pre>")

    def fig(self, svg: str, cap: str):
        self.parts.append(f'<div class="fig">{svg}<div class="cap">{cap}</div></div>')


def c(x) -> str:
    return f"<code>{e(x)}</code>"


def body(F: dict, pages: dict[str, int] | None) -> str:
    D = Doc()
    M, C, T, X, Y, Z = F["med"], F["cic"], F["tour"], F["fft"], F["rec"], F["ladder"]
    H, V = F["health"], F["versions"]
    gens = [k for k in F["schema"]["designs"]]
    backends = list(F["schema"]["backends"].keys())
    mb = M["best"]
    r1 = M["rounds"][0]
    revs = [r for r in M["rounds"] if r["revise"]]
    des_usd = sum(t["usd_micros"] or 0 for t in M["designer_turns"])
    chat_t = M["chat_turns"][0]
    fo, mu, tr = M["formal"], M["mutation"], M["trust"]

    # ------------------------------------------------------------------ 1
    D.h1("项目背景", first=True)
    D.p("数字硬件（FPGA 与 ASIC）的设计是一个由真实工具反复度量的过程：从需求出发选择架构，写出 RTL，做功能仿真，再经过综合、布局布线和静态时序分析，"
        "得到这一版设计在目标器件上能跑多快（Fmax）、占多少资源（LUT / FF / DSP / BRAM）、功能是否正确。结果不理想就回到架构或代码重来。一轮迭代少则几十秒，"
        "多则数十分钟，而同一个功能在不同器件上的最优结构往往完全不同——ECP5 的 18×18 乘法器、Spartan-7 的 DSP48E1、Gowin 的 BSRAM，都会改变“最好的写法”。")
    D.p("大语言模型已经能写出看起来合理的 Verilog，但硬件的好坏不能凭代码观感判断，只能由综合、布局布线与仿真工具给出。把模型接入这个“写—测—改”的闭环，"
        "让每一个决定都建立在实测数字之上，是本项目的出发点。")
    D.p(f"<b>硅演 Silicon Agent</b> 是这样一个系统：用户用一句话描述想要的硬件，智能体（运行在 Agnes Harness 上）把它变成规格，选择或编写设计，"
        f"在真实 EDA 工具链上逐轮实测、诊断、改进，直到满足约束，然后交付经过多层验证的 RTL 与可审计的全部过程记录。当前支持 {len(gens)} 个参数化生成器、"
        f"{len(backends)} 个目标后端（{e('、'.join(backends))}），并能为生成器没有覆盖的模块由智能体自己编写 RTL（设计工作室）。")

    # ------------------------------------------------------------------ 2
    D.h1("问题来源")
    D.p("我们在开发与使用中反复遇到以下四个问题，它们决定了本项目的设计：")
    D.table(["问题", "具体表现", "后果"], [
        ["生成代码缺少度量", "模型直接写 Verilog，没有综合、布局布线和仿真的结果作依据", "代码能否综合、能跑多快、功能是否正确都不知道，不可信"],
        ["工具调用没有闭环", "把 EDA 当成一次性的黑盒：跑一次、看一眼报告", "关键路径的信息没有被用来指导下一轮，优化靠人"],
        ["设计空间被预先枚举", "模板/生成器只暴露固定的旋钮；要的模块不在模板里就做不了，结构性的改进也做不了", f"例如“5 点滑动中值滤波器”不在 {len(gens)} 个生成器里；中值网络的流水化是结构问题，转旋钮解决不了"],
        ["验证强度不可见", "“仿真通过”不说明测试台能不能抓住错误；参考模型与 RTL 出自同一人之手时会一起错", "结果是否可信无法量化，评审无从核查"],
    ], ["17%", "45%", "38%"])
    D.p(f"真实例子：在本文第 10 章的实例一里，用户要的中值滤波器没有任何生成器覆盖（{c('sa_interpret')} 返回 {c('design: custom')}）；"
        f"智能体开出的第一版设计实测只有 {fmt(r1['fmax'])} MHz，离 {M['job']['spec']['constraints']['fmax_mhz']} MHz 的约束差约 {M['job']['spec']['constraints']['fmax_mhz'] / r1['fmax']:.0f} 倍，"
        f"关键路径 {fmt(r1['cp_ns'], 1)} ns 全部落在一拍组合逻辑里——任何参数旋钮都碰不到它，只能改写结构。")

    # ------------------------------------------------------------------ 3
    D.h1("项目目标")
    D.table(["目标", "衡量方式（本文给出的实测证据）"], [
        ["一句话到可交付 RTL", "对话输入 → 规格 → 运行 → 交付包（RTL、AXI4-Stream 包装、测试台与向量、报告、构建脚本）；实例一、二、三"],
        ["真实度量，不估算", "每一轮的 Fmax / 资源 / 仿真结果来自真实工具；筛选估算只用来排候选顺序；每个数字可在压缩包中找到原始记录"],
        ["设计空间不被预先枚举", "生成器之外由设计工作室编写 RTL 并由独立参考把关（实例一）；已有设计可以“从生成器出走”重构（实例四）"],
        ["反馈驱动的迭代", "规划器读关键路径给出诊断与候选；旋钮够不到时请设计师改写（实例一 3 次改写，26.5 → 212.8 MHz）"],
        ["可验证、可审计", "五级仿真阶梯、形式等价与流协议属性、变异分数、独立参考样本数；Agnes Harness 记录每一次工具调用与模型费用"],
        ["失败可见、异常可恢复", "检查失败的报告原样回到设计师；服务器重启、会话忙、模型超时都有恢复路径（实例五）"],
    ], ["26%", "74%"])

    # ------------------------------------------------------------------ 4
    D.h1("核心方法", brk=True)
    D.h2("总体架构")
    D.fig(diagram_architecture(), "图 4-1　系统总体架构：入口 → Agnes Harness 智能体 → sa_* 工具 → 作业服务器 → 生成器 / 设计工作室 → 验证阶梯 → 规划器与设计记忆（虚线为下一轮）")
    D.p("系统分三层。最上层是 Agnes Harness 中的智能体会话，负责理解需求、编排工具、写 RTL 与参考模型；中间是作业服务器，它是唯一能改变世界的地方（运行、工作室、证据）；"
        "最下层是真实的 EDA 工具链。智能体不能直接执行宿主机命令：它的一切动作都通过插件提供的 sa_* 工具变成对作业服务器的 HTTP 请求，因此每个动作都有记录、可审计。")
    D.h2("设计表示：IR 与生成器")
    D.p(f"常见的数据通路（{e('、'.join(gens))}）由参数化生成器覆盖。每个生成器定义一个冻结的架构表示（IR）：可调的旋钮（并行度、折叠、流水级、DSP 寄存器、存储类型等）、"
        "合法范围与校验规则。发射器把 IR 确定性地转成 SystemVerilog（没有随机源、没有时间戳：同一份 IR 永远得到同一份 RTL），并给出功能的 Python 黄金模型与测试向量。"
        "规划器只改 IR，不逐行改代码，这让“这一轮改了什么”永远清楚。")
    D.h2("设计工作室：生成器之外")
    D.fig(diagram_studio(), "图 4-2　设计工作室：写与查分离。设计师会话与独立参考会话互相看不到对方的产出；RTL 必须通过 ①–⑥ 才会注册")
    D.p("当需求不在任何生成器里，或者需要在旋钮之外重构结构时，智能体打开一个设计工作室。<b>设计师会话</b>（深思模型、独立工作目录）写出 RTL、逐位一致的 golden.py 与 design.json"
        "（端口、参数及范围、延迟、吞吐）；另一个<b>看不到 RTL 的参考会话</b>只凭 SPEC.md 写出 reference(params, xs)。检查依次进行：elaborate；golden 与参考在随机激励"
        "（含极值、窗口未满、valid 间隙）上逐位比对（在隔离的 Python 子进程中执行模型生成的代码）；用参考的向量回放行为级仿真；SymbiYosys 检查流协议属性；"
        "对 design.json 声明的每一个旋钮值再检查一次；变异分数不低于 0.6。任一步失败，完整报告（日志、首个不一致样本、属性源码与反例）回到设计师；全部通过才注册为 "
        f"{c('rtl:<id>')}，此后和生成器一样可跑，修订号 rev 成为 IR 的一个字段。已有运行的某一轮也可以“分叉”进工作室：此时参考就是原生成器自己的向量。")
    D.h2("验证阶梯")
    D.table(["#", "阶段", "工具", "说明"], [
        ["1", "行为级仿真", "cocotb + Icarus Verilog", "RTL 对黄金模型 / 独立参考的向量；所有器件"],
        ["2", "综合 + 综合后功能仿真", "yosys（ECP5/iCE40/Gowin/GateMate）· Vivado（Spartan-7）", "网表行为与 RTL 一致；原语有自己的仿真模型"],
        ["3", "布局布线与静态时序", "nextpnr（3 粒种子取中位）· Vivado · OpenROAD（sky130）", "Fmax、关键路径按逻辑/布线分段、资源"],
        ["4", "综合后时序仿真", "Vivado xsim + SDF", "目标周期下的门级时序（Spartan-7）"],
        ["5", "实现后功能 + 时序仿真（签核）", "Vivado xsim + 布线后 SDF", "最终签核（Spartan-7）"],
    ], ["5%", "24%", "33%", "38%"])
    D.p("在此之上，结果卡上的按钮可以对最佳设计运行 SymbiYosys 有界等价检查（实测 RTL 与再次发射的 IR 等价）与流协议属性检查，以及变异测试（量化测试台的检错能力）。")
    D.h2("规划闭环与改写请求")
    D.p("每一轮，规划器（DeepSeek）收到约束、固定参数、历史（每一轮的 IR、Fmax 与种子离散、资源、关键路径分段、仿真结果、上一轮的理由）与从设计记忆召回的教训，"
        "输出诊断与 1–3 个候选。候选先经筛选估算排序（按已测数据校准，只决定顺序），然后并行实测。旋钮明显够不到关键路径时，规划器对工作室设计可以提出 "
        f"{c('revise')}：一段引用关键路径的改写指令。服务器把它交给该工作室的设计师；设计师改写、重过同一套检查后，新修订作为下一轮实测。"
        "设计师的修订说明会进入规划器的历史，避免重复请求同一种改写。")
    D.h2("设计记忆与作者模式")
    D.p("每一轮的动作都被提炼成一条教训（情境 → 动作 → 效果），后续运行采纳后被确认或反驳并计分；规划前按情境（器件、违约类型、关键路径类别、结构特征）用 BM25 召回，"
        "跨生成器/器件的教训标为“迁移”。作者模式下，智能体可以在生成器源码的副本中提出改动，经门禁（发射、仿真、形式等价、变异分、A/B 实测）后由人合并——"
        "FIR 生成器的 sum_tree 与 pipe_in 两个旋钮就是这样由智能体提出并实测验证后加入的。")

    # ------------------------------------------------------------------ 5
    D.h1("Agnes Harness 执行流程", brk=True)
    D.h2("部署与连接")
    D.table(["组件", "形态", "说明"], [
        ["Agnes Harness 守护进程", f"compose 服务 agh（Node {e(V.get('node'))}，AGH 源码 {e(V.get('agh'))}，含我们的 silicon/author 预设）", "模型循环、会话存储、工具调用记录、钩子、账本；unix socket 上的 JSON-RPC（ACP）"],
        ["silicon-agent 插件", f"agh-plugin/index.mjs，{len(F['tools'])} 个 sa_* 工具 + 技能 silicon-agent", "工具 = 对作业服务器 HTTP API 的调用；结果为严格 JSON；单次调用 ≤ 120 秒"],
        ["作业服务器", f"compose 服务 server（{e(V.get('python'))}，oss-cad-suite，Vivado {e(V.get('vivado', '').replace('vivado ', ''))}）", "规格解释、运行队列、EDA、工作室、证据、交付；silicon_agent/agh.py 是它的 AGH 客户端"],
        ["OpenROAD", f"compose 服务 orfs（{e(V.get('orfs'))}）", "sky130 后端"],
    ], ["22%", "40%", "38%"])
    D.h2("会话类型")
    D.table(["会话键", "角色", "预设 / 模型", "钩子约束"], [
        [c("sa-thread-<对话>"), "对话智能体：理解需求、编排工具、汇报", "silicon；快 = deepseek-flash，深思 = deepseek-v4-pro（thinking high）", "context 注入方法（METHOD 1–9）；tool_call 拒绝 write/edit/shell 等宿主工具"],
        [c("sa-studio-<工作室>"), "设计师：写 RTL、golden、design.json，自测并提交", "author；deepseek-v4-pro（thinking high）；cwd = 工作室 work/", "写入只限 work/；shell 仅 ls/cat/grep/diff；单轮 ≤ 40 分钟"],
        [c("sa-reference-<工作室>-n"), "独立参考：只凭规格写 reference()", "deepseek-v4-pro；无工具", "拒绝全部工具调用"],
        [c("sa-author-<对话>"), "作者模式：改生成器、提出 diff", "author；deepseek-v4-pro", "写入只限 scratch 副本"],
    ], ["19%", "28%", "28%", "25%"])
    D.h2("一次对话轮次")
    D.fig(diagram_sequence(), "图 5-1　一次对话轮次的执行时序（以实例一为例：sa_interpret → sa_studio_open → sa_studio_status ×16 → sa_run → sa_job ×10 → 回复）")
    D.p(f"实例一的这一轮由 Agnes Harness 记录为：开始 {e(chat_t['started'])}，结束 {e(chat_t['ended'])}，用时 {e(R.mmss(chat_t['seconds']))}，{chat_t['calls']} 次模型调用（{e(chat_t['model'])}），"
        f"{M['n_tools']} 次工具调用，输入 {tok(chat_t['input'])} / 缓存命中 {tok(chat_t['cache'])} / 输出 {tok(chat_t['output'])} token，估算 {R.usd(chat_t['usd_micros'])}。"
        "轮次中智能体的工作说明与工具调用实时流入对话的“思考中”行；轮次结束后，回复下方显示“智能体过程”（说明 + 每一次调用及其状态）与账本。")
    D.h2("工具与钩子")
    groups = [("理解与估算", ["sa_schema", "sa_interpret", "sa_estimate"]), ("运行", ["sa_run", "sa_job", "sa_jobs", "sa_sweep", "sa_job_file"]), ("引导", ["sa_steer", "sa_stop"]),
              ("验证", ["sa_formal", "sa_mutation"]), ("对比", ["sa_pareto", "sa_tournament", "sa_tournament_status"]), ("经验", ["sa_memory", "sa_bench"]),
              ("自带 RTL", ["sa_rtl_upload", "sa_rtl_list"]), ("设计工作室", ["sa_studio_open", "sa_studio_fork", "sa_studio_status", "sa_studio_revise", "sa_studio_list", "sa_studio_resume", "sa_studio_check", "sa_studio_submit"]),
              ("改生成器", ["sa_check", "sa_propose", "sa_proposal"])]
    listed = {t for _, ts in groups for t in ts}
    rest = [t for t in F["tools"] if t not in listed]
    D.table(["类别", "工具"], [[g, " ".join(c(t) for t in ts if t in F["tools"])] for g, ts in groups] + ([["其他", " ".join(c(t) for t in rest)]] if rest else []), ["18%", "82%"])
    D.p(f"共 {len(F['tools'])} 个工具（从 agh-plugin/index.mjs 解析）。插件还注册两个钩子：<b>context</b> 按会话键注入方法说明（对话的 METHOD、设计师的 DESIGNER_METHOD）；"
        "<b>tool_call</b> 按会话类型裁决每一次工具调用（例如对话会话不能写文件，设计师只能写自己的 work/，参考会话不能调用任何工具）。")
    D.h2("中断与恢复")
    D.ul(["作业服务器重启：正在跑的运行完成当前一轮后重新排队，从缓存续跑；进行中的对话轮次如果 AGH 还在执行就重新跟随（session.attach），已被中止则让会话根据自己的工具记录继续，回复标注“重启后续接”。",
          "工作室：重启时标记 resume_pending，启动后自动续接设计师；工作副本若已是某个修订，直接重新注册，不再花一轮设计师（实例五）。",
          "设计师会话：单轮上限 40 分钟，超时取消，运行改用旋钮继续；会话忙（SESSION_BUSY）时跟随正在执行的轮次再发送；会话存储故障（E_STORAGE_FAULT）时重新打开会话重试一次。",
          "模型账户：服务器每 10 分钟探测 DeepSeek 余额；余额不足时对话与规划器暂停，界面显示横幅，已开始的运行照常出结果。"])

    # ------------------------------------------------------------------ 6
    D.h1("所用模型的名称与版本")
    modes = H.get("modes") or {}
    D.table(["模型", "标识 / 版本", "提供方与接入", "配置位置"], [
        ["DeepSeek Flash", c("deepseek-flash"), "DeepSeek 开放平台；在 AGH 中为内置 DeepSeek 提供方（pi-ai 目录）", f"对话“快”模式（当前：{e(modes.get('fast'))}）；规划器默认 SA_MODEL"],
        ["DeepSeek V4 Pro（推理强度 high）", c("deepseek-v4-pro") + "，thinking = high", "同上；规划器直连时为 /chat/completions，reasoning_effort = high（重试时降为 low）", f"对话“深思”模式（{e(modes.get('deep'))}）；作者模式（{e(modes.get('author'))}）；设计工作室 SA_STUDIO_MODEL"],
    ], ["22%", "24%", "30%", "24%"])
    D.p(f"运行时依据：服务器 /api/health 报告的模式表（压缩包 system/health.json），各会话轮次在 Agnes Harness 账本中的 finalModel 与每次调用的 model 字段（各案例 *agh_session*.json）。"
        f"实例一中：对话智能体 {e(chat_t['model'])}；设计师 {e(M['designer_turns'][0]['model'])}；规划器 {e(M['job']['spec'].get('planner') or 'deepseek-flash（SA_MODEL 默认）')}。")

    # ------------------------------------------------------------------ 7
    D.h1("使用环节")
    D.table(["环节", "谁", "模型", "输入", "输出", "实例中的记录"], [
        ["需求解释", "sa_interpret → nl.interpret", "deepseek-flash", "一句话需求 + 专案约定", "规格（生成器/custom、器件、约束、目标、spec_text）", "case1 02_agent_AGH（seq 41）"],
        ["对话编排", "对话智能体（AGH）", "快：flash；深思：v4-pro", "用户消息、工具结果", "工具调用序列、最终回复", "case1/3/5 agh_session_chat.json"],
        ["规划", "规划器 loop.py", "deepseek-flash（可按运行指定）", "约束、历史、关键路径、召回的教训", "诊断、候选、stop、revise", "04_run/results/llm_*.json"],
        ["教训提炼", "loop.py", "deepseek-flash", "这一轮的情境、动作、效果", "一条可复用的教训", "events.jsonl 的 lesson 事件"],
        ["RTL 编写/修订", "设计师（AGH）", "deepseek-v4-pro", "SPEC.md、检查报告、改写指令", "rtl/*.sv、golden.py、design.json", "03_studio agh_session_designer.json"],
        ["独立参考", "参考会话（AGH，无工具）", "deepseek-v4-pro", "SPEC.md + 接口/参数", "reference(params, xs)", "03_studio reference.py"],
        ["生成器改进", "作者模式（AGH）", "deepseek-v4-pro", "生成器源码副本", "diff + 门禁报告", "（本次压缩包未收录）"],
        ["锦标赛", "两路规划器", "flash 对 v4-pro", "同一份规格", "两次运行并排比较", "case3"],
    ], ["11%", "17%", "15%", "19%", "20%", "18%"])

    # ------------------------------------------------------------------ 8
    D.h1("调用方式")
    D.h2("作业服务器 → Agnes Harness（JSON-RPC over unix socket）")
    D.p(f"silicon_agent/agh.py 是作业服务器内的 AGH 客户端。连接时发送 ACP {c('initialize')}（本地认证），之后使用以下方法：")
    D.table(["方法", "用途"], [
        [c("session/new"), "新建会话，_meta 中带 sessionKey（sa-thread-* / sa-studio-* / …）与预设"],
        [c("_agnes/v1/session.setModel"), "设置主模型槽位：route=deepseek，model=deepseek-flash 或 deepseek-v4-pro，thinking"],
        [c("session/prompt"), "发送一轮用户输入；轮次过程以 session/update 通知流回（说明文本、tool_call、tool_call_update）"],
        [c("session/load"), "重新打开已有会话（重启后、导出记录前）"],
        [c("_agnes/v1/session.attach"), "跟随另一个连接发起的进行中轮次"],
        [c("_agnes/v1/session.projectUI"), "读取会话的 UI 投影：turns（账本、模型、用时）与 nodes（每次工具调用的参数/结果预览、状态）——压缩包中的会话记录即由此导出"],
        [c("session/cancel"), "取消轮次（用户停止、设计师超时）"],
        [c("_agnes/v1/workspace.add"), "登记工作室目录为工作区"],
        [c("_agnes/v1/config.save / config.get"), "部署时配置 DeepSeek 账户与插件"],
    ], ["30%", "70%"])
    D.h2("Agnes Harness → 产品（sa_* 工具）")
    D.p("每个 sa_* 工具是插件中的一个函数：它把参数转成对作业服务器的 HTTP 请求（携带 0600 文件中的令牌），把结果整理为严格 JSON 返回给模型。"
        "需要等待的工具（sa_job、sa_studio_status、sa_tournament_status）在一次调用内最多等 90 秒，智能体按需反复轮询——实例一中 sa_studio_status 被调用 16 次、sa_job 10 次。")
    D.pre("// agh-plugin/index.mjs（节选）：一个工具 = 一个 HTTP 调用\n{ name: 'sa_studio_open',\n  description: 'Open a design studio for a block NO generator covers ...',\n  parameters: { brief, spec_text, name, thread },\n  execute: async (args) => text(await api('POST', '/studio', args)) }")
    D.h2("规划器 → DeepSeek（HTTPS）")
    D.p(f"规划器在 silicon_agent/llm.py 中直接调用 DeepSeek 兼容接口 {c('POST https://api.deepseek.com/chat/completions')}：system + user 两条消息、"
        f"response_format = json_object、reasoning_effort（首次 high，重试降低），超时 480 秒，401/402/403 立即停止并在界面提示。每一次调用的完整 system、prompt 与 reply 保存在运行目录的 "
        f"{c('llm_NN.json')}（实例一的 7 次规划调用都在压缩包 case1_median5_studio/04_run/results/）。")
    D.h2("账本与记录")
    D.p("Agnes Harness 为每一轮记录账本：模型调用次数、输入/输出/缓存 token、推理 token、估算费用（usdMicros）、用时与每次调用的首 token 延迟。作业服务器为每次运行写 events.jsonl（规划、实测、阶段、改写、教训）、"
        "iter_NN.json（每个设计点的完整指标）、llm_NN.json（规划器对话）、summary.json；工作室为每次检查写 checks/<tag>/report.json 及其仿真与形式验证的工作目录。")

    # ------------------------------------------------------------------ 9
    D.h1("验证方法")
    D.h2("分层验证")
    D.table(["层", "方法", "工具", "通过标准"], [
        ["功能", "行为级仿真：RTL 对黄金模型 / 独立参考的向量", "cocotb + Icarus Verilog", "全部样本逐位一致（RESULT PASS）"],
        ["网表", "综合后功能仿真；Spartan-7 另有综合后时序、实现后功能与时序仿真", "yosys / Vivado 2025.2 xsim", "与 RTL 一致；时序仿真在目标周期下通过"],
        ["时序与资源", "布局布线 3 粒种子取中位；关键路径按逻辑/布线分段", "nextpnr / Vivado / OpenROAD", "中位 Fmax ≥ 约束，资源 ≤ 约束"],
        ["形式", "实测 RTL 与再次发射的 IR 有界等价；流协议属性（复位期间无 valid、复位后首拍无 valid、ready 不依赖 valid）", "SymbiYosys + boolector", "深度 ≥ 20（且超过流水线延迟）内无反例"],
        ["验证强度", "变异测试：注入加减互换、比较翻转、使能卡死、索引偏移等，统计测试台抓住的比例", "silicon_agent/mutate.py", "分数如实报告；工作室注册门槛 ≥ 0.6"],
        ["工作室", "golden 对独立参考 → 行为级 → 流属性 → 每个旋钮值 → 变异分", "同上", "全部通过才注册"],
        ["回归", "夜间基准：黄金点复测（工具链真跑）+ 规划器用例 + 智能体用例（含工作室用例）", "silicon_agent/bench.py", "与上次报告对比，偏差报警"],
    ], ["12%", "42%", "22%", "24%"])
    D.h2("诚实规则")
    D.ul(["界面与报告上的每一个 Fmax / LUT / DSP 数字都来自真实工具的输出文件；筛选估算只用于排序候选，从不作为结果展示。",
          "失败如实呈现：不收敛的运行、违约的点、漏网的变异、检查失败的报告都保留并显示（实例一变异分 2/6、实例五的失败运行）。",
          "独立参考的信任条写的是“独立参考 N 样本一致”，不是“已证明”；规格本身写进交付包，可审计。",
          "LLM 生成的 Python（golden、reference）只在隔离子进程（python -I、超时、工作目录内）中运行，从不在服务器进程中执行。"])

    # ------------------------------------------------------------------ 10
    D.h1("运行实例", brk=True)
    D.p("以下六个实例都是生产服务器（或开发机）上真实跑过的运行。每个实例对应压缩包中的一个目录，目录里有原始记录与自动生成的时间线 00_timeline.md。")
    D.table(["实例", "内容", "压缩包目录"], [
        ["一", "5 点滑动中值滤波器：一句话 → 设计工作室 → 检查失败与修复 → 运行 → 三次改写 → 验证 → 交付", c("case1_median5_studio")],
        ["二", "CIC 抽取滤波器：生成器路径，并行候选，形式验证与变异 8/8", c("case2_cic_generator")],
        ["三", "锦标赛：快规划器与深思规划器跑同一份 32 阶 FIR 规格", c("case3_tournament")],
        ["四", "从生成器出走：64 点 FFT 在 Gowin 上重构，暴露并修复仿真模型缺陷", c("case4_fft_fork_gowin")],
        ["五", "失败与恢复：设计师长时间空转、会话忙、运行失败与随后的修复", c("case5_failure_recovery")],
        ["六", "Spartan-7 / Vivado 五级仿真阶梯", c("case6_vivado_ladder")],
    ], ["8%", "67%", "25%"])

    # ---- 10.1
    D.h2("实例一：5 点滑动中值滤波器（端到端）")
    D.raw(f'<div class="kpi"><div><b>{fmt(r1["fmax"])} → {fmt(mb["fmax"])} MHz</b><span>第 1 轮 → 最佳（约束 ≥ {M["job"]["spec"]["constraints"]["fmax_mhz"]}）</span></div>'
          f'<div><b>{r1["lut"]} → {mb["lut"]} LUT</b><span>0 DSP · 0 BRAM · 1 样本/周期</span></div><div><b>{len(revs)} 次改写</b><span>规划器请求，设计师修订并重过检查</span></div>'
          f'<div><b>{tr["reference"]["samples"]} 样本</b><span>独立参考逐位一致</span></div></div>')
    D.h3("① 任务输入")
    D.raw(f"<blockquote>{e(M['request'])}</blockquote>")
    D.p(f"对话 {c(M['thread']['id'])}。这一轮在“快”模式下执行：Agnes Harness 账本记录的 {chat_t['calls']} 次模型调用全部是 {e(chat_t['model'])}"
        f"（对话的模式字段在这一轮之后才被切到“深思”，记录为 mode = {e(M['thread'].get('mode'))}、agh_mode_applied = {e(M['thread'].get('agh_mode_applied'))}）。写 RTL 的设计师会话固定使用 deepseek-v4-pro。")
    D.h3("② 外部能力调用：智能体的工具轨迹")
    rows = []
    for r in M["chat_tools"]:
        rows.append([str(r["seq"]) + (f"–{r['seq_to']}" if r["n"] > 1 else ""), f'<code class="nw">{e(r["name"])}</code>' + (f" ×{r['n']}" if r["n"] > 1 else ""), e(R.brief(r["args"], 120)), e(R.brief(r["result"], 150))])
    D.table(["seq", "工具", "参数（预览）", "结果（预览，轮询取最后一次）"], rows, ["9%", "21%", "33%", "37%"])
    D.p(f"关键一步是 {c('sa_interpret')}：解释器判断没有生成器覆盖这个块，返回 {c('design: custom')} 并写出精确的功能规格 spec_text；智能体随即用它调用 {c('sa_studio_open')}。"
        f"之后它每 90 秒查询一次工作室（16 次）直到注册，再以 {c('design: rtl:' + M['studio']['id'])} 调用 {c('sa_run')}，并轮询运行（10 次）直到结束。")
    D.h3("③ 设计工作室：规格、参考模型与设计师")
    D.p("规格（SPEC.md，节选）：")
    D.pre(M["spec"], 1800)
    D.p("看不到 RTL 的参考会话写出的参考模型（reference.py，全文）：")
    D.pre(M["reference"])
    drows = [[str(t["turn"]), e(t["started"]), e(R.mmss(t["seconds"])), str(t["calls"]), f"{tok(t['input'])} / {tok(t['cache'])} / {tok(t['output'])}", tok(t["reasoning"]), R.usd(t["usd_micros"]),
              e(", ".join(sorted(set(t["tools"]), key=t["tools"].index))[:120])] for t in M["designer_turns"]]
    D.table(["轮", "开始", "用时", "模型调用", "输入/缓存/输出", "推理 token", "费用", "用到的工具"], drows, ["5%", "17%", "10%", "8%", "17%", "9%", "8%", "26%"])
    D.p(f"设计师（{e(M['designer_turns'][0]['model'])}）共 {len(M['designer_turns'])} 轮：第 1 轮写出初版并注册，后 3 轮分别响应运行中的 3 次改写请求；合计估算 {R.usd(des_usd)}。")
    D.h3("④ 异常处理：设计师自测失败 → 修复 → 通过")
    fail = next(x for x in M["checks"] if not x["ok"])
    fs = next(s for s in fail["steps"] if not s.get("ok"))
    pas = next(x for x in M["checks"] if x["ok"] and x["mtime"] > fail["mtime"])
    D.p(f"设计师的第一次 {c('sa_studio_check')}（报告 {c(fail['tag'])}）前四步都通过，第五步 {c(fs['name'])} 失败。SymbiYosys 在第 1 步给出反例：断言 "
        f"{c('if (rst) assert (!out_valid);')}（props.sv 第 11 行）被违反——上电时 out_valid 寄存器的初值可以是 1，而此时 rst 已经为高。失败日志原样回到设计师：")
    D.pre(fs.get("log") or "", 1400)
    fixtxt = next((t["text"] for t in M["designer_texts"] if "SBY failure" in t["text"] or "out_valid" in t["text"]), "")
    D.p(f"设计师的诊断（会话记录原文）：<i>{e(R.brief(fixtxt, 400))}</i> 它把输出改为 {c('assign out_valid = ~rst & pipe_v[OUT_PIPE];')}，再次检查（报告 {c(pas['tag'])}）全部通过："
        + "；".join(f"{e(s['name'])} {'✓' if s.get('ok') else '✕'}" + (f"（{s['samples']} 样本）" if s.get("samples") else "") for s in pas["steps"]) + "。")
    sub = [x for x in M["designer_checks"] if x["tool"] == "sa_studio_submit"]
    if sub and not sub[0]["ok"]:
        D.p(f"边界情况：第一次 {c('sa_studio_submit')} 被注册门禁拒绝——设计师在 design.json 中声明 DATA_W 可取其他值，但门禁对每个声明值单独检查时失败（流端口是固定 16 位）。"
            f"设计师把 DATA_W 的范围收窄为 16 后再次提交，注册成功。注册门禁的报告见压缩包 {c('03_studio/server_record/checks/register*')}。")
    D.h3("⑤ 执行过程与反馈调整：运行的每一轮")
    rr = []
    for r in M["rounds"]:
        lab = f"{r['it']}" + (f".{r['slot']}" if r["slot"] else "")
        act = R.brief(R.action_zh(r["action"]), 90) if r["it"] > 1 or r["slot"] else "初版（工作室注册的 rev 0）"
        rr.append([lab, f"+{r['t']:.0f}s", c(R.ir_str(r["ir"])), fmt(r["fmax"]), str(r["lut"]), fmt(r["cp_ns"], 2), ('<span class="ok">可行</span>' if r["feasible"] else '<span class="bad">' + e("；".join(r["violations"])) + "</span>"), e(act)])
    D.table(["轮", "时刻", "IR", "Fmax MHz", "LUT", "关键路径 ns", "判定", "这一轮做了什么"], rr, ["5%", "7%", "22%", "8%", "6%", "8%", "17%", "27%"])
    pts = [((f"{r['it']}" + (f".{r['slot']}" if r["slot"] else "")), r["fmax"], r["feasible"]) for r in M["rounds"]]
    bi = max(range(len(pts)), key=lambda i: pts[i][1])
    D.fig(chart_line(pts, M["job"]["spec"]["constraints"]["fmax_mhz"], "MHz", label_idx=(0, bi)), "图 10-1　实例一每个实测设计的 Fmax（实心 = 满足全部约束，空心 = 违约；数据见上表）")
    D.fig(chart_bars([(p[0], r["lut"]) for p, r in zip(pts, M["rounds"])], "LUT", label_idx=(0, 2, 3)), "图 10-2　实例一每个实测设计的 LUT（DSP 恒为 0）")
    D.p("三次改写请求（规划器原文，运行事件 revising）与设计师的修订说明（revised）：")
    for r in revs:
        ask = next(x for x in r["revise"] if x["kind"] == "revising")
        got = next((x for x in r["revise"] if x["kind"] == "revised"), None)
        D.raw(f'<div class="avoid"><p><span class="tag">第 {r["it"]} 轮请求</span>{e(R.brief(ask.get("instruction"), 420))}</p>'
              + (f'<p><span class="tag">修订 #{got.get("rev")}</span>{e(R.brief(got.get("note"), 300))}</p>' if got else "") + "</div>")
    nolegal = [p for p in M["plans"] if (p.get("action") or "").startswith("no legal candidate")]
    D.p(f"第 4 轮修订 #3 达到 {fmt(mb['fmax'])} MHz / {mb['lut']} LUT 后，规划器在第 5 轮把 OUT_PIPE 的其他三个合法值并行测完（LUT 不变、Fmax 在种子噪声内），"
        f"之后连续 {len(nolegal)} 轮没有合法候选，运行在 {R.mmss(M['run_s'])} 后结束，最佳设计为第 {mb['it']} 轮 {c(R.ir_str(mb['ir']))}。")
    D.h3("⑥ 结果验证")
    eqd = (fo.get("equivalence") or {}).get("depth")
    D.table(["项", "结果", "来源"], [
        ["独立参考", f"{tr['reference']['samples']} 样本逐位一致（rev {tr['reference']['rev']}，{tr['reference']['revisions']} 个修订）", "trust.json · 工作室检查报告"],
        ["行为级仿真", "通过（216 样本）" if tr["stages"].get("behavioral") else "失败", "iter_04.json sim_log"],
        ["综合后网表仿真", "通过" if tr["stages"].get("post_synth_func") else "失败", "metrics.sim_stages"],
        ["布局布线种子", " / ".join(fmt(s) for s in tr["seeds"]) + " MHz（取中位）", "fmax_seeds"],
        ["关键路径", f"{fmt(mb['cp'][0]['total_ns'], 2)} ns / 目标 {1000 / M['job']['spec']['constraints']['fmax_mhz']:.2f} ns：" + " → ".join(f"{e(s.get('kind'))}" for s in mb["cp"][1:]), "critical_path"],
        ["形式验证", f"等价 {'PASS' if (fo.get('equivalence') or {}).get('ok') else 'FAIL'}（深度 {eqd}）· 流协议 {'PASS' if (fo.get('properties') or {}).get('ok') else 'FAIL'}", "formal.json（SymbiYosys 日志）"],
        ["变异测试", f"{mu['killed']}/{mu['total']} 被抓住；漏网：{e(', '.join(mu.get('survived') or []))}", "mutation.json"],
    ], ["16%", "58%", "26%"])
    D.p(f"变异分数 {mu['killed']}/{mu['total']} 偏低，如实显示：漏网的变异集中在输出流水级（第 209–210 行的加减互换与索引偏移），说明随机激励对这一段的覆盖不足——这是下一步要加强的测试点，而不是被隐藏的结果。")
    D.h3("⑦ 智能体的回复与交付")
    D.raw(f"<blockquote>{'<br>'.join(e(l) for l in M['reply']['text'].splitlines() if l.strip())}</blockquote>")
    u = M["reply"]["usage"]
    D.p(f"账本：{u.get('calls')} 次模型调用 · {tok((u.get('input') or 0) + (u.get('cache_read') or 0) + (u.get('output') or 0))} tokens · {R.usd(u.get('usd_micros'))} · {R.mmss((u.get('ms') or 0) / 1000)}；设计师另计 {R.usd(des_usd)}。"
        f"交付包（{len(M['package'])} 个文件）：" + "、".join(c(n) for n in M["package"][:14]) + ("…" if len(M["package"]) > 14 else "") + "。")

    # ---- 10.2
    D.h2("实例二：CIC 抽取滤波器（生成器路径）")
    cb = C["best"]
    D.raw(f"<blockquote>{e(C['request'])}</blockquote>")
    D.p(f"这次对话走内置对话路径（nl.py 直接解释与汇报），设计为 {c('cic')} 生成器，器件 ECP5，固定参数 {c(json.dumps(C['job']['spec']['locked'], ensure_ascii=False))}，约束 ≥ {C['job']['spec']['constraints']['fmax_mhz']} MHz、0 DSP、LUT 优先。")
    rr = [[f"{r['it']}" + (f".{r['slot']}" if r["slot"] else ""), c(R.ir_str({k: v for k, v in r["ir"].items() if k not in C["job"]["spec"]["locked"]})), fmt(r["fmax"]), str(r["lut"]),
           ('<span class="ok">可行</span>' if r["feasible"] else '<span class="bad">' + e("；".join(r["violations"])) + "</span>"), e(R.brief(R.action_zh(r["action"]), 80) if r["it"] > 1 or r["slot"] else "起点设计")] for r in C["rounds"]]
    D.table(["轮", "可调 IR", "Fmax MHz", "LUT", "判定", "理由"], rr, ["6%", "32%", "9%", "7%", "18%", "28%"])
    D.p(f"第 2 轮规划器并行实测了 3 个候选，其中并行候选 2.1（pipe_comb=True）达到 {fmt(cb['fmax'])} MHz / {cb['lut']} LUT 并赢下这一轮；第 3 轮把积分器流水关掉做对照，Fmax 回落到 75.6–125.2 MHz，"
        f"作为反例记入设计记忆。最佳设计：种子 {', '.join(fmt(s) for s in C['trust']['seeds'])} MHz；形式等价与流协议 PASS（深度 {C['trust']['formal']['depth']}）；变异 {C['trust']['mutation']['killed']}/{C['trust']['mutation']['total']}。")

    # ---- 10.3
    D.h2("实例三：锦标赛（两种规划器，同一份规格）")
    D.raw(f"<blockquote>{e(T['request'])}</blockquote>")
    trows = []
    for jid, J in T["jobs"].items():
        b = J["best"]
        trows.append([c(jid), e(J["job"]["spec"].get("tournament", {}).get("label")), str(len(J["rounds"])), f"{fmt(b['fmax'])} MHz · {b['lut']} LUT · {b['dsp']} DSP" if b else "—",
                      c(R.ir_str({k: v for k, v in (b or {}).get("ir", {}).items() if k in ("fold", "pipe_mul", "symmetric", "dsp_regs", "sum_tree", "pipe_in")}))])
    D.table(["运行", "规划器", "实测设计数", "最佳", "关键旋钮"], trows, ["22%", "24%", "10%", "20%", "24%"])
    tt = T["turns"][0]
    D.p(f"对话智能体调用 {c('sa_interpret')}、{c('sa_memory')}（召回 FIR/ECP5 的教训）后用 {c('sa_tournament')} 同时开出两路运行，再交替轮询 {c('sa_tournament_status')} 与 {c('sa_job')}。"
        f"这一轮 {R.mmss(tt['seconds'])}、{tt['calls']} 次模型调用、{R.usd(tt['usd_micros'])}。两路落在同一架构（对称、fold=4、4 DSP），深思规划器多加了一级乘法流水 pipe_mul=1，"
        "Fmax 高出约 13%。")

    # ---- 10.4
    D.h2("实例四：从生成器出走——FFT 在 Gowin 上的重构")
    ori = X["studio"]["origin"]
    bx, ax = X["before"]["rounds"], X["after"]["rounds"]
    ab = X["after"]["best"]
    D.p(f"运行 {c(ori['job'])} 的最佳设计（{e(ori['label'])}，64 点 12 位 radix-2 SDF FFT，Gowin GW1NR-9）实测 1472 LUT。用户在工作台运行菜单里选“分叉到设计工作室”，"
        f"目标是 LUT ≤ 1200、Fmax ≥ 100 MHz、DSP ≤ 16。分叉后参考就是原生成器自己的测试向量，设计师可以任意改结构，但函数与端口冻结。设计师 {len(X['designer_turns'])} 轮共估算 {R.usd(X['studio']['usage'].get('usd_micros'))}。")
    D.table(["运行", "轮", "IR", "Fmax", "LUT", "BRAM", "判定"],
            [["修复前", f"{r['it']}", c(R.ir_str(r["ir"])), fmt(r["fmax"]), str(r["lut"]), str(r["bram"]), ('<span class="ok">可行</span>' if r["feasible"] else '<span class="bad">' + e("；".join(r["violations"])) + "</span>")] for r in bx]
            + [["修复后", f"{r['it']}", c(R.ir_str(r["ir"])), fmt(r["fmax"]), str(r["lut"]), str(r["bram"]), ('<span class="ok">可行</span>' if r["feasible"] else '<span class="bad">' + e("；".join(r["violations"])) + "</span>")] for r in ax],
            ["10%", "6%", "22%", "10%", "8%", "8%", "36%"])
    D.p(f"修复前，设计师的修订 1、2 把旋转因子表改成同步 BSRAM 读，LUT 降到 1192，却被判“综合后功能仿真失败”。我们用同一份网表在本地复现，发现问题不在设计：Gowin 单口 BRAM（SP/SPX9）的仿真模型"
        f"在载入 INIT_RAM 之后又执行了一次清零，所有单口 ROM 在网表仿真里读出 0。修复（提交 {c(X['fix_hash'])}）把清零与载入放进同一个 initial 块，并提升缓存版本，使旧结论全部重测。"
        f"修复后同一修订通过网表仿真，第 2 轮 {c(R.ir_str(ab['ir']))} 达到 {fmt(ab['fmax'])} MHz / {ab['lut']} LUT / {ab['dsp']} DSP，满足 LUT ≤ 1200 的边界约束。"
        "这个缺陷是由智能体的结构改写暴露出来的：正是同一条验证阶梯让它可见、可复现、可修复。")
    D.pre("\n".join(l for l in X["fix_diff"].splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))[:1600], 1600)

    # ---- 10.5
    D.h2("实例五：失败与恢复")
    s1 = Y["studio1"]
    d1t = Y["designer1_turns"]
    j1r, j2r, j2b = Y["job1"]["rounds"], Y["job2"]["rounds"], Y["job2"]["best"]
    D.p(f"这是夜间基准中的智能体用例（同样的中值滤波器需求，英文）。第一个工作室 {c(s1['id'])} 注册了初版，运行 {c(Y['job1']['job']['id'])} 第 1 轮 {fmt(j1r[0]['fmax'])} MHz 后规划器请求改写。"
        f"设计师的第 2 轮持续了 {R.mmss(d1t[1]['seconds'])}（{d1t[1]['calls']} 次模型调用、{tok(d1t[1]['reasoning'])} 推理 token、{R.usd(d1t[1]['usd_micros'])}）：它在用穷举搜索寻找一个"
        "“6 比较器、深度 4 的中值网络”，而这样的网络并不存在。等待它的运行收到如下说明并改用旋钮继续，最终失败：")
    D.ul([e(R.brief(n, 200)) for n in Y["job1"]["notes"]])
    D.p("由此加入的修复（均已部署，git 记录在压缩包 case5_failure_recovery/fixes_git_log.txt）：")
    D.ul(["设计师单轮上限 40 分钟，超时取消并等待会话空闲，运行改用旋钮继续；改写指令明确是“方向而非合同”，结构不存在时做最接近的可靠设计并说明差异。",
          "SESSION_BUSY 时先跟随正在执行的轮次再发送；对话轮次超过 1 小时继续跟随而不是报错；会话存储故障时重新打开会话重试一次。",
          "设计师的修订说明进入规划器历史；规划器只在约束仍违约或目标还有结构性空间时才请求改写。",
          "SPEC.md 放进设计师的工作目录（Agnes Harness 把文件工具限制在工作区，读 ../SPEC.md 曾被拒绝）；失败的属性检查直接附带属性源码、失败断言与反例测试台。"])
    D.p(f"智能体在同一对话里开了第二个工作室 {c(Y['studio2']['id'])}，运行 {c(Y['job2']['job']['id'])} 经两次修订达到 {fmt(j2b['fmax'])} MHz / {j2b['lut']} LUT（DATA_WIDTH=8，rev 2），满足约束：")
    D.table(["轮", "IR", "Fmax", "LUT", "判定"], [[f"{r['it']}" + (f".{r['slot']}" if r["slot"] else ""), c(R.ir_str(r["ir"])), fmt(r["fmax"]), str(r["lut"]), ('<span class="ok">可行</span>' if r["feasible"] else '<span class="bad">' + e("；".join(r["violations"])) + "</span>")] for r in j2r], ["8%", "38%", "12%", "10%", "32%"])

    # ---- 10.6
    D.h2("实例六：Spartan-7 / Vivado 五级仿真阶梯")
    zr = Z["rounds"]
    lab = {"post_synth_func": "综合后功能", "post_synth_time": "综合后时序", "post_impl_func": "实现后功能", "post_impl_time": "实现后时序（签核）"}
    D.table(["轮", "IR（节选）", "Fmax", "DSP", "行为级", *lab.values()],
            [[str(r["it"]), c(R.ir_str({k: v for k, v in r["ir"].items() if k in ("pe_rows", "pe_cols", "acc_width", "reg_in", "dsp_regs")})), fmt(r["fmax"]), str(r["dsp"]), "✓" if r["sim_passed"] else "✕",
              *[("✓" if (r["sim_stages"].get(k) or {}).get("passed") else "✕" if (r["sim_stages"].get(k) or {}).get("passed") is False else "—") for k in lab]] for r in zr],
            ["5%", "33%", "9%", "6%", "8%", "9%", "9%", "9%", "12%"])
    D.p(f"INT8 矩阵乘在 Spartan-7 上：第 1 轮 8×8 阵列在网表功能仿真中正确，但带 SDF 延迟的两级时序仿真失败（{fmt(zr[0]['fmax'])} MHz、{zr[0]['dsp']} DSP 超预算）；"
        f"第 2 轮改为 4×8、DSP 寄存器 in_pipe_out，五级全部通过，{fmt(zr[1]['fmax'])} MHz、{zr[1]['dsp']} DSP。这说明“行为级通过”并不够：只有带真实延迟的时序仿真才能签核。")

    # ------------------------------------------------------------------ 11
    D.h1("运行环境与复现", brk=True)
    D.table(["项", "版本 / 规格（服务器实测）"], [
        ["操作系统", e(V.get("os"))], ["硬件", e(V.get("cpu"))], ["作业服务器 Python", e(V.get("python"))], ["Agnes Harness", e(V.get("agh")) + f"（Node {e(V.get('node'))}）"],
        ["yosys", e(V.get("yosys"))], ["nextpnr", e(V.get("nextpnr"))], ["Icarus Verilog", e(V.get("iverilog"))], ["SymbiYosys", e(V.get("sby"))], ["cocotb", e(V.get("cocotb"))],
        ["Vivado", e(V.get("vivado"))], ["OpenROAD", e(V.get("orfs"))], ["silicon-agent 代码", e(V.get("silicon_agent_commit"))],
    ], ["24%", "76%"])
    D.p(f"后端：{e('、'.join(backends))}（Artix-7 后端已实现，因服务器缺少器件文件而关闭）。设计种类：{e('、'.join(H.get('designs', [])))}。")
    D.pre("# 远程使用（笔记本是薄客户端）\nssh -f -N -L 18765:127.0.0.1:8765 netcup\nexport SA_SERVER=http://127.0.0.1:18765 SA_TOKEN=...\n"
          "sa ask \"一个 5 点滑动中值滤波器：16 位有符号，ECP5，150 MHz 以上，LUT 越少越好\"\nsa studio list ; sa studio status <id> --watch\n"
          "sa trust <job> ; sa formal <job> ; sa mutation <job> ; sa package <job>\nsa export <thread> report.md\n\n"
          "# 重新生成本压缩包\nSA_URL=http://127.0.0.1:18765 SA_TOKEN=... python3 report/collect.py dist/silicon-agent_run-records\n/tmp/pdfenv/bin/python report/build.py dist/silicon-agent_run-records")

    # ------------------------------------------------------------------ 12
    D.h1("运行记录压缩包说明")
    D.p("压缩包保留了上述实例运行时的全部过程记录。每个案例目录的 00_timeline.md 是由原始记录自动生成的可读时间线（任务输入 → 工具调用 → 工作室 → 每一轮 → 验证 → 回复）。")
    D.table(["路径", "内容", "来源"], [
        [c("*/01_conversation/thread.json, thread.md"), "对话原文、运行卡、工作室卡、回复（含工具轨迹与账本）；Markdown 导出", "GET /api/threads/<id>[/export.md]"],
        [c("*/02_agent_AGH/agh_session_chat.json"), "对话智能体的 Agnes Harness 会话记录：turns（模型、token、费用、用时）与 nodes（每次工具调用的参数/结果预览、状态）", "AGH session.projectUI（deploy/scripts/agh-export.py）"],
        [c("*/03_studio*/"), "SPEC.md、reference.py、bundle.json（全部修订的源文件）、revisions/rev*/、work/（设计师的工作副本）、server_record/（工作室记录与每次检查的报告、仿真与形式验证工作目录）、设计师会话记录", "GET /api/studio/<id> · /api/rtl/<id> · rsync /srv/sa/studio/<id>"],
        [c("*/04_run*/results/"), "events.jsonl（全部运行事件）、iter_*.json（每个设计点的指标）、llm_*.json（规划器的完整 prompt 与回复）、summary.json、formal/、mutation/", "rsync /srv/sa/results/<job>"],
        [c("*/04_run/eda_workdirs/<hash>/"), "每个设计点的 EDA 工作目录：RTL、测试台与向量、仿真日志、yosys/nextpnr 脚本与日志、时序报告、网表", "rsync /srv/sa/runs/<hash>（不含 .vvp 与大 JSON 网表）"],
        [c("case1*/05_verification/"), "trust.json、formal.json、mutation.json", "GET /api/jobs/<id>/{trust,formal,mutation}"],
        [c("case1*/06_delivery/"), "交付包 package.zip 与最佳 RTL", "GET /api/jobs/<id>/package · /rtl"],
        [c("system/"), "服务器状态、schema、目录、基准报告、工作室列表、版本、插件源码", "GET /api/health 等 · ssh 版本探测"],
    ], ["30%", "48%", "22%"])

    # ------------------------------------------------------------------ appendix
    D.h1("第三方材料与独立完成声明")
    D.p("全体成员确认作品由学生团队独立完成。使用的第三方材料如下：")
    D.table(["材料", "来源", "使用方式"], [
        ["Agnes Harness", "github.com/AgnesAI-Labs/agnes-harness（fork 中加入 silicon / author 预设）", "智能体运行时；本项目是它的插件"],
        ["DeepSeek 模型", "DeepSeek 开放平台 API", "规划器与智能体推理"],
        ["oss-cad-suite（yosys、nextpnr、Icarus Verilog、SymbiYosys、boolector）、cocotb", "YosysHQ 等开源项目", "EDA 工具链"],
        ["AMD Vivado 2025.2", "AMD", "Spartan-7 综合、实现与时序仿真"],
        ["OpenROAD-flow-scripts、sky130 PDK", "The OpenROAD Project、SkyWater", "ASIC 后端"],
        ["Playwright / Chromium", "Microsoft / Google 开源", "生成本 PDF"],
    ], ["32%", "38%", "30%"])

    # ---- TOC
    toc = ['<h1 class="first" style="page-break-before:auto">目录</h1><ul class="toc">']
    for lvl, hid, title in D.toc:
        pg = str(pages.get(hid, "")) if pages else ""
        toc.append(f'<li class="l{lvl}"><a href="#{hid}"><span class="t">{e(title)}</span></a><span class="dots"></span><span class="p">{pg}</span></li>')
    toc.append("</ul>")
    return "<html><head><meta charset='utf-8'><style>" + CSS + "</style></head><body>" + "".join(toc) + "<div style='page-break-after:always'></div>" + "".join(D.parts) + "</body></html>"


def cover(F: dict) -> str:
    V = F["versions"]
    return ("<html><head><meta charset='utf-8'><style>" + CSS + """
.cv { height: 257mm; display: flex; flex-direction: column; justify-content: space-between; padding: 30mm 6mm 6mm; }
.cv .t1 { font-size: 34pt; font-weight: 700; color: #11335c; letter-spacing: .06em; }
.cv .t2 { font-size: 15pt; color: #2a78d6; margin-top: 6pt; letter-spacing: .14em; }
.cv .t3 { font-size: 15pt; margin-top: 26pt; color: #1b1b1a; font-weight: 600; }
.cv .t4 { font-size: 11pt; margin-top: 10pt; color: #52514e; line-height: 1.8; }
.cv .bar { height: 4pt; width: 60mm; background: #2a78d6; margin: 22pt 0 0; }
.cv .meta { font-size: 9.5pt; color: #52514e; line-height: 1.9; }
</style></head><body><div class='cv'><div><div class='t1'>硅演</div><div class='t2'>SILICON AGENT</div><div class='bar'></div>
<div class='t3'>项目说明与运行记录</div>
<div class='t4'>用真实 EDA 闭环的 FPGA 数字设计智能体<br>项目背景 · 问题来源 · 项目目标 · 核心方法 · Agnes Harness 执行流程<br>所用模型与版本 · 使用环节 · 调用方式 · 验证方法 · 六个运行实例</div></div>
<div class='meta'>""" + f"生成时间：{R.when(__import__('time').time())}<br>代码版本：{e(V.get('silicon_agent_commit'))}<br>运行环境：{e(V.get('os'))} · Agnes Harness {e(V.get('agh'))}<br>"
            "附件：运行记录压缩包（与本文同包），每个实例的原始记录与自动生成的时间线</div></div></body></html>")


# =============================================================================================== README, render, zip
def write_readme(root: Path, F: dict) -> None:
    M = F["med"]
    sizes = {p.name: sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) for p in root.iterdir() if p.is_dir()}
    L = ["# 硅演 Silicon Agent —— 运行记录", "",
         "本压缩包保留了项目说明（`SiliconAgent_Report_zh-CN.pdf`）中六个运行实例的全部运行过程：对话原文、Agnes Harness 会话记录（每一次模型调用与工具调用）、",
         "设计工作室的规格 / 参考模型 / 每个修订的源文件 / 每一次检查报告、运行的全部事件与规划器对话、EDA 工作目录（脚本、日志、报告）、验证报告与交付包。",
         "", "所有文件都是生产服务器 API 的原始响应、服务器数据目录中的记录或代码仓库中的文件，由 `report/collect.py` 收集，未经手工编辑；",
         "每个案例目录下的 `00_timeline.md` 由 `report/build.py` 从这些记录自动生成，便于按时间顺序阅读。", "",
         "| 目录 | 内容 | 大小 |", "|---|---|---|"]
    desc = {"case1_median5_studio": "实例一：5 点滑动中值滤波器，一句话 → 设计工作室 → 检查失败与修复 → 运行 → 三次改写 → 验证 → 交付",
            "case2_cic_generator": "实例二：CIC 抽取滤波器，生成器路径（开发机检出上的运行与快照）",
            "case3_tournament": "实例三：锦标赛，快 / 深思两种规划器跑同一份规格",
            "case4_fft_fork_gowin": "实例四：64 点 FFT 分叉到设计工作室（Gowin），以及它暴露并修复的仿真模型缺陷",
            "case5_failure_recovery": "实例五：失败与恢复（设计师空转、会话忙、运行失败、第二个工作室成功）",
            "case6_vivado_ladder": "实例六：Spartan-7 / Vivado 五级仿真阶梯",
            "system": "服务器状态、schema、设计目录、夜间基准报告、工具版本、插件源码"}
    for k in sorted(sizes):
        L.append(f"| `{k}/` | {desc.get(k, '')} | {sizes[k] / 1e6:.1f} MB |")
    L += ["", "## 每个案例目录的结构", "",
          "- `00_timeline.md` —— 可读时间线（自动生成）",
          "- `01_conversation/` —— `thread.json`（对话原文，含工具轨迹与账本）、`thread.md`（产品导出的 Markdown）",
          "- `02_agent_AGH/agh_session_chat.json` —— 对话智能体在 Agnes Harness 中的会话记录：`turns[]`（模型、token、费用、用时、每次模型调用）与 `nodes[]`（每次工具调用的名称、参数预览、结果预览、状态）",
          "- `03_studio*/` —— `SPEC.md`、`reference.py`（独立参考模型）、`bundle.json`（注册的 bundle，含全部修订）、`revisions/rev*/`（每个修订的 RTL）、`work/`（设计师工作副本）、",
          "  `server_record/`（工作室记录 `studio.json` 与 `checks/<tag>/report.json` 及仿真、SymbiYosys 工作目录）、`agh_session_designer.json`（设计师会话记录）",
          "- `04_run*/` —— `job.json`（运行的全部事件）、`results/`（`events.jsonl`、`iter_*.json`、`llm_*.json` 规划器的完整 prompt 与回复、`summary.json`、`formal/`、`mutation/`）、",
          "  `eda_workdirs/<hash>/`（每个设计点的 EDA 工作目录：`rtl/`、`sim/`、`nsim/`、`pnr/` 的脚本、日志与报告）",
          "- `05_verification/` —— 信任条、形式验证、变异测试报告；`06_delivery/` —— 交付包与最佳 RTL", "",
          "## 说明", "",
          f"- 实例一的关键记录：对话 `{M['thread']['id']}`、工作室 `{M['studio']['id']}`、运行 `{M['job']['id']}`。",
          "- 实例二（CIC）跑在开发机检出上，记录来自代码仓库的 `results/`、`threads/` 与演示视频工程的产品 API 快照，不在生产服务器上。",
          "- 二进制仿真镜像（`*.vvp`）与数 MB 的 yosys JSON 网表未收录（可由工作目录中的脚本重新生成）。",
          "- 独立参考会话是一次无工具的单轮对话，其产出就是 `reference.py`；该会话由另一条连接创建，Agnes Harness 不允许从导出脚本重新打开，故未单独导出。",
          "- 重新生成：`python3 report/collect.py <dir>` 收集记录，`report/build.py <dir>` 生成时间线、PDF 与压缩包。"]
    (root / "README.md").write_text("\n".join(L) + "\n")
    print("README written")


def render(html_path: Path, pdf_path: Path, footer: bool) -> None:
    subprocess.run(["node", str(HERE / "render.mjs"), str(html_path), str(pdf_path), "1" if footer else "0"], check=True)


def headings_pages(pdf: Path) -> dict[str, int]:
    from pypdf import PdfReader
    out = {}
    for i, page in enumerate(PdfReader(str(pdf)).pages):
        for m in re.finditer(r"Q\s*Q\s*(s[\d\s_]+?)\s*Q\s*Q", page.extract_text() or ""):
            out.setdefault(re.sub(r"\s+", "", m.group(1)), i + 1)
    return out


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    build = root.parent / "build"
    build.mkdir(exist_ok=True)
    F = facts(root)
    write_timelines(root, F)
    write_readme(root, F)
    (build / "cover.html").write_text(cover(F))
    render(build / "cover.html", build / "cover.pdf", False)
    (build / "body.html").write_text(body(F, None))
    render(build / "body.html", build / "body.pdf", True)
    pages = headings_pages(build / "body.pdf")
    (build / "body.html").write_text(body(F, pages))
    render(build / "body.html", build / "body.pdf", True)
    pages2 = headings_pages(build / "body.pdf")
    if pages2 != pages:
        print("TOC pages moved on the second pass; third pass")
        (build / "body.html").write_text(body(F, pages2))
        render(build / "body.html", build / "body.pdf", True)
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for f in (build / "cover.pdf", build / "body.pdf"):
        for pg in PdfReader(str(f)).pages:
            w.add_page(pg)
    w.add_metadata({"/Title": "硅演 Silicon Agent —— 项目说明与运行记录", "/Author": "silicon-agent", "/Subject": "项目背景、问题来源、目标、方法、Agnes Harness 执行流程、模型、调用方式、验证方法与运行实例"})
    pdf = root / "SiliconAgent_Report_zh-CN.pdf"
    with open(pdf, "wb") as fh:
        w.write(fh)
    n = len(PdfReader(str(pdf)).pages)
    print(f"PDF: {pdf} ({n} pages, {pdf.stat().st_size / 1e6:.1f} MB); TOC entries resolved: {len(pages2)}")
    z = root.parent / f"{root.name}_{__import__('time').strftime('%Y%m%d')}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in sorted(root.rglob("*")):
            if f.is_file():
                zf.write(f, Path(root.name) / f.relative_to(root))
    print(f"ZIP: {z} ({z.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
