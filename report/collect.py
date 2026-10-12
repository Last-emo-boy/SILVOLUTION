"""Collect the run records of the examples the project report cites, from the production server and the
development checkout, into one directory tree (the 运行记录 archive).

    ssh -f -N -L 18765:127.0.0.1:8765 netcup
    SA_URL=http://127.0.0.1:18765 SA_TOKEN=... python3 report/collect.py dist/silicon-agent_run-records

Nothing is edited: every file is an API response, a file from the server's data directory (rsync/tar over ssh),
or a file of this checkout. Binary simulator images (*.vvp) and the multi-MB yosys JSON netlists are left out.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = os.environ.get("SA_URL", "http://127.0.0.1:18765").rstrip("/") + "/api"
TOKEN = os.environ.get("SA_TOKEN") or subprocess.run(["security", "find-generic-password", "-s", "silicon-agent-token", "-w"], capture_output=True, text=True).stdout.strip()
HOST = os.environ.get("SA_HOST", "netcup")
EXCLUDE = ["--exclude", "*.vvp", "--exclude", "synth.json", "--exclude", "core.json", "--exclude", "report_[0-9].json", "--exclude", "__pycache__"]

# the examples (production ids unless noted)
MED = {"thread": "20261011-100330-7fd8", "studio": "20261011-100357-s0d36", "job": "20261011-102713-f6f7"}
TOUR = {"thread": "20261009-093036-d309", "tournament": "20261009-093056-t7b81", "jobs": ["20261009-093056-4b91", "20261009-093056-315c"]}
FFT = {"studio": "20261011-130919-s7916", "job_before": "20261011-140059-331a", "job_after": "20261011-171217-66cf", "origin_job": "20261011-023520-4f4c"}
REC = {"thread": "20261011-131102-f808", "studio1": "20261011-131130-sd75d", "job1": "20261011-133113-f604", "studio2": "20261011-143729-sf892", "job2": "20261011-150743-6b2e"}
LADDER = "20261008-053528-a4f1"
CIC = {"thread": "20261009-141051-a320", "job": "20261009-141103-d59f", "run": "fb972960b8c3d5e1"}  # development checkout


def _fetch(path: str) -> bytes:
    """GET with retries: the SSH tunnel drops now and then; reopen it and try again."""
    import time
    for attempt in range(5):
        try:
            r = urllib.request.Request(f"{API}/{path}", headers={"Authorization": f"Bearer {TOKEN}"})
            return urllib.request.urlopen(r, timeout=600).read()
        except urllib.error.HTTPError:
            raise
        except Exception:  # noqa: BLE001 -- connection reset / refused: the tunnel
            if attempt == 4:
                raise
            port = API.split(":")[-1].split("/")[0]
            subprocess.run(["pkill", "-f", f"ssh -f -N -o ServerAliveInterval=30 -L {port}"], capture_output=True)
            subprocess.run(["ssh", "-f", "-N", "-o", "ServerAliveInterval=30", "-L", f"{port}:127.0.0.1:8765", HOST], capture_output=True)
            time.sleep(2 + attempt * 2)
    raise RuntimeError("unreachable")


def get(path: str, out: Path, binary: bool = False) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    data = _fetch(path)
    if not binary and out.suffix == ".json":
        # keep the server's bytes, but indent JSON so a reader can follow it
        try:
            data = json.dumps(json.loads(data), ensure_ascii=False, indent=1).encode()
        except json.JSONDecodeError:
            pass
    out.write_bytes(data)
    print(f"  {out.relative_to(ROOT)}  ({len(data):,} B)  ← GET /api/{path}")


def rsync(remote: str, dest: Path, extra: list[str] | None = None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-az", *EXCLUDE, *(extra or []), f"{HOST}:{remote.rstrip('/')}/", str(dest) + "/"], check=True)
    n = sum(1 for _ in dest.rglob("*") if _.is_file())
    print(f"  {dest.relative_to(ROOT)}/  ({n} files)  ← {HOST}:{remote}")


def agh(keys: list[str], out: Path) -> None:
    """The harness's own records of AGH sessions (deploy/scripts/agh-export.py runs inside the server container)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["ssh", HOST, "docker exec -i silicon-agent python3 /app/deploy/scripts/agh-export.py " + " ".join(keys)], capture_output=True, text=True, check=True)
    d = json.loads(p.stdout)
    out.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    what = [k + ": " + (v.get("error") or f"{len(v['nodes'])} nodes, {len(v['turns'] or [])} turns") for k, v in d.items()]
    print(f"  {out.relative_to(ROOT)}  ({'; '.join(what)})")


def copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.vvp", "synth.json", "core.json", "report_[0-9].json", "__pycache__"))
    else:
        shutil.copy2(src, dest)
    print(f"  {dest.relative_to(ROOT)}  ← {src.relative_to(REPO)}")


def runs_of(design: str) -> list[str]:
    """EDA work directories (runs/<hash>) whose arch.json names this design kind."""
    p = subprocess.run(["ssh", HOST, f"grep -l '\"{design}\"' /srv/sa/runs/*/arch.json"], capture_output=True, text=True)
    return [Path(l).parent.name for l in p.stdout.split()]


def studio(sid: str, base: Path) -> None:
    get(f"studio/{sid}", base / "studio.json")
    get(f"studio/{sid}/spec", base / "spec_and_reference.json")
    sp = json.loads((base / "spec_and_reference.json").read_text())
    (base / "SPEC.md").write_text(sp["spec"])
    if sp.get("reference"):
        (base / "reference.py").write_text(sp["reference"])
    get(f"rtl/{sid}", base / "bundle.json")
    b = json.loads((base / "bundle.json").read_text())
    for r in b.get("revisions") or []:
        for name, text in (r.get("files") or {}).items():
            f = base / "revisions" / f"rev{r['rev']}" / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
    st = json.loads((base / "studio.json").read_text())
    for f in st.get("files") or []:
        if f["path"].startswith("core"):
            continue
        try:
            get(f"studio/{sid}/file/{urllib.request.quote(f['path'], safe='')}", base / "work" / (f["path"] + ".json"))
        except urllib.error.HTTPError as e:
            print(f"  (skip {f['path']}: HTTP {e.code})")
            continue
        doc = json.loads((base / "work" / (f["path"] + ".json")).read_text())
        (base / "work" / f["path"]).write_text(doc["text"])
        (base / "work" / (f["path"] + ".json")).unlink()
    # the record on disk (designer turns with their texts and ledgers), the check reports with their logs
    rsync(f"/srv/sa/studio/{sid}", base / "server_record", ["--exclude", "work/"])


def job(jid: str, base: Path, extras: bool = True) -> None:
    get(f"jobs/{jid}", base / "job.json")
    rsync(f"/srv/sa/results/{jid}", base / "results")
    if extras:
        for p, f in (("trust", "trust.json"), ("rtl", "best.sv")):
            try:
                get(f"jobs/{jid}/{p}", base / f)
            except Exception as e:  # noqa: BLE001 -- a run without a feasible design has no trust/rtl
                print(f"  ({p}: {e})")


def main() -> None:
    global ROOT
    ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/silicon-agent_run-records").resolve()
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)

    print("case1: the median filter, one sentence to a delivered design (design studio, production)")
    c = ROOT / "case1_median5_studio"
    get(f"threads/{MED['thread']}", c / "01_conversation" / "thread.json")
    get(f"threads/{MED['thread']}/export.md", c / "01_conversation" / "thread.md")
    agh([f"sa-thread-{MED['thread']}"], c / "02_agent_AGH" / "agh_session_chat.json")
    studio(MED["studio"], c / "03_studio")
    agh([f"sa-studio-{MED['studio']}:/srv/sa/studio/{MED['studio']}/work"], c / "03_studio" / "agh_session_designer.json")
    job(MED["job"], c / "04_run")
    for h in runs_of(f"rtl:{MED['studio']}"):
        rsync(f"/srv/sa/runs/{h}", c / "04_run" / "eda_workdirs" / h)
    get(f"jobs/{MED['job']}/formal?depth=20", c / "05_verification" / "formal.json")
    get(f"jobs/{MED['job']}/mutation", c / "05_verification" / "mutation.json")
    get(f"jobs/{MED['job']}/trust", c / "05_verification" / "trust.json")
    get(f"jobs/{MED['job']}/package", c / "06_delivery" / "package.zip", binary=True)
    get(f"jobs/{MED['job']}/rtl", c / "06_delivery" / "median5_best.sv")

    print("case2: the CIC decimator, generator path (built-in chat, development checkout)")
    c = ROOT / "case2_cic_generator"
    copy(REPO / "threads" / f"{CIC['thread']}.json", c / "01_conversation" / "thread.json")
    copy(REPO / "results" / CIC["job"], c / "04_run" / "results")
    copy(REPO / "runs" / CIC["run"], c / "04_run" / "eda_workdirs" / CIC["run"])
    for f in ("job-cic.json", "trust-cic.json", "formal-cic.json", "package-cic.json", "rtl-cic.sv"):
        copy(REPO / "broll" / "public" / "assets" / "product" / f, c / "05_verification" / "api_snapshot" / f)
    copy(REPO / "broll" / "docs" / "evidence", c / "05_verification" / "symbiyosys_and_mutation")

    print("case3: the tournament, two planners on one spec (Agnes Harness, production)")
    c = ROOT / "case3_tournament"
    get(f"threads/{TOUR['thread']}", c / "01_conversation" / "thread.json")
    get(f"threads/{TOUR['thread']}/export.md", c / "01_conversation" / "thread.md")
    agh([f"sa-thread-{TOUR['thread']}"], c / "02_agent_AGH" / "agh_session_chat.json")
    get(f"tournaments/{TOUR['tournament']}", c / "tournament.json")
    for jid in TOUR["jobs"]:
        job(jid, c / f"run_{jid}")

    print("case4: forking a generator's design (FFT, Gowin) and the simulation-model bug it exposed (production)")
    c = ROOT / "case4_fft_fork_gowin"
    studio(FFT["studio"], c / "03_studio")
    agh([f"sa-studio-{FFT['studio']}:/srv/sa/studio/{FFT['studio']}/work"], c / "03_studio" / "agh_session_designer.json")
    job(FFT["job_before"], c / "04_run_before_fix", extras=False)
    job(FFT["job_after"], c / "04_run_after_fix")
    for h in runs_of(f"rtl:{FFT['studio']}"):
        rsync(f"/srv/sa/runs/{h}", c / "eda_workdirs" / h)
    h = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h", "--grep=SP/SPX9 models zero-filled"], capture_output=True, text=True).stdout.strip()
    fix = subprocess.run(["git", "-C", str(REPO), "show", h], capture_output=True, text=True, check=True).stdout
    (c / f"fix_{h}_gowin_bram_sim_model.diff").write_text(fix)
    print(f"  {(c / f'fix_{h}_gowin_bram_sim_model.diff').relative_to(ROOT)}  ← git show {h}")

    print("case5: failures and recovery: a designer that searched for hours, a failed run, the second studio (production)")
    c = ROOT / "case5_failure_recovery"
    get(f"threads/{REC['thread']}", c / "01_conversation" / "thread.json")
    get(f"threads/{REC['thread']}/export.md", c / "01_conversation" / "thread.md")
    agh([f"sa-thread-{REC['thread']}"], c / "02_agent_AGH" / "agh_session_chat.json")
    studio(REC["studio1"], c / "03_studio_first")
    agh([f"sa-studio-{REC['studio1']}:/srv/sa/studio/{REC['studio1']}/work"], c / "03_studio_first" / "agh_session_designer.json")
    job(REC["job1"], c / "04_run_first_failed", extras=False)
    studio(REC["studio2"], c / "03_studio_second")
    agh([f"sa-studio-{REC['studio2']}:/srv/sa/studio/{REC['studio2']}/work"], c / "03_studio_second" / "agh_session_designer.json")
    job(REC["job2"], c / "04_run_second")
    log = subprocess.run(["git", "-C", str(REPO), "log", "--format=%h %ad %s", "--date=iso", "--since=2026-10-11T12:00", "--", "silicon_agent/studio.py", "silicon_agent/server.py", "silicon_agent/loop.py", "silicon_agent/rtl.py", "agh-plugin/index.mjs", "silicon_agent/tb/bram_sim.v", "deploy/agh-entry.sh"], capture_output=True, text=True, check=True).stdout
    (c / "fixes_git_log.txt").write_text(log)
    print(f"  {(c / 'fixes_git_log.txt').relative_to(ROOT)}  ← git log (the fixes these failures led to)")

    print("case6: the five-stage simulation ladder on Spartan-7 / Vivado (production)")
    c = ROOT / "case6_vivado_ladder"
    job(LADDER, c / "04_run")

    print("system: the server's state, the schema, the nightly bench")
    c = ROOT / "system"
    for p, f in (("health", "health.json"), ("schema", "schema.json"), ("bench", "bench.json"), ("catalog", "catalog.json"), ("studio", "studios.json"), ("rtl", "rtl_bundles.json")):
        get(p, c / f)
    rsync("/srv/sa/bench", c / "bench_reports", ["--exclude", "*.log"])
    # tool and runtime versions as the server reports them
    probe = r'''T=/opt/silicon-agent/tools/oss-cad-suite/bin; echo "yosys=$($T/yosys -V | head -1)"; echo "nextpnr=$($T/nextpnr-ecp5 --version 2>&1 | head -1)";
echo "iverilog=$($T/iverilog -V 2>/dev/null | head -1)"; echo "sby=$($T/sby --version 2>/dev/null | head -1)";
echo "cocotb=$(/opt/silicon-agent/tools/oss-cad-suite/py3bin/python3 -c 'import cocotb; print(cocotb.__version__)')";
echo "python=$(/opt/silicon-agent/tools/oss-cad-suite/py3bin/python3 --version)";
echo "vivado=$(docker exec silicon-agent bash -lc 'vivado -version 2>/dev/null | head -1')";
echo "orfs=$(docker images openroad/orfs --format '{{.Repository}}:{{.Tag}} {{.ID}}' | head -1)";
echo "agh=$(cd /opt/agnes-harness && git log -1 --format='%h %ad' --date=short)"; echo "node=$(docker exec silicon-agent-agh node --version)";
echo "os=$(. /etc/os-release; echo $PRETTY_NAME)"; echo "cpu=$(nproc) cores, $(free -g | awk '/Mem/{print $2}') GB"'''
    p = subprocess.run(["ssh", HOST, probe], capture_output=True, text=True, check=True)
    v = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
    v["silicon_agent_commit"] = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h %ad", "--date=iso"], capture_output=True, text=True).stdout.strip()
    (c / "versions.json").write_text(json.dumps(v, ensure_ascii=False, indent=1))
    print(f"  {(c / 'versions.json').relative_to(ROOT)}  ← ssh {HOST} (tool --version)")
    copy(REPO / "agh-plugin" / "index.mjs", c / "agh-plugin_index.mjs")
    copy(REPO / "agh-plugin" / "package.json", c / "agh-plugin_package.json")
    print(f"done: {ROOT}")


if __name__ == "__main__":
    main()
