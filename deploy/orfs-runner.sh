#!/usr/bin/env bash
# The ORFS runner: a long-lived openroad/orfs container that waits for flow requests under $SA_ASIC_QUEUE
# (a directory on the shared /srv/sa volume) and runs OpenROAD-flow-scripts on each. The job server's sky130
# backend writes <id>/request.json + the RTL + config.mk + constraint.sdc, then polls <id>/done. No docker socket
# in the job server; the only coupling is the shared volume.
set -uo pipefail
: "${SA_ASIC_QUEUE:=/srv/sa/asic/queue}"
: "${ORFS:=/OpenROAD-flow-scripts/flow}"
mkdir -p "$SA_ASIC_QUEUE"
# a `running` marker left by a previous runner (the container was recreated mid-flow) is ours to redo
for stale in "$SA_ASIC_QUEUE"/*/running; do [ -e "$stale" ] && { echo "orfs-runner: redoing $(dirname "$stale")"; rm -f "$stale"; }; done
echo "orfs-runner: watching $SA_ASIC_QUEUE (platforms: $(ls $ORFS/platforms | tr '\n' ' '))"
while true; do
  for req in "$SA_ASIC_QUEUE"/*/request.json; do
    [ -e "$req" ] || continue
    d=$(dirname "$req")
    [ -e "$d/done" ] && continue
    [ -e "$d/running" ] && continue
    : > "$d/running"
    echo "orfs-runner: $(basename "$d") start $(date -Is)"
    # one request = one ORFS make in its own WORK_HOME; the result lands next to the request
    ( cd "$ORFS" && timeout "${SA_ASIC_TIMEOUT:-3600}" make DESIGN_CONFIG="$d/config.mk" WORK_HOME="$d/flow" > "$d/make.log" 2>&1 )
    rc=$?
    echo "$rc" > "$d/exit"
    # the files the backend reads, copied out of ORFS's deep tree
    plat=$(sed -n 's/^export PLATFORM *= *//p' "$d/config.mk"); design=$(sed -n 's/^export DESIGN_NAME *= *//p' "$d/config.mk")
    base="$d/flow"
    for f in "$base/logs/$plat/$design/base/6_report.json" "$base/reports/$plat/$design/base/6_finish.rpt" "$base/reports/$plat/$design/base/synth_stat.txt" \
             "$base/results/$plat/$design/base/1_2_yosys.v" "$base/results/$plat/$design/base/6_final.v" "$base/logs/$plat/$design/base/1_2_yosys.log"; do
      [ -e "$f" ] && cp "$f" "$d/"
    done
    # the odb/gds/def tree is large; keep the GDS (the deliverable) and drop the rest unless asked to keep it
    [ -e "$base/results/$plat/$design/base/6_final.gds" ] && cp "$base/results/$plat/$design/base/6_final.gds" "$d/"
    [ -e "$d/keep_flow" ] || rm -rf "$base/results" "$base/objects"
    rm -f "$d/running"
    : > "$d/done"
    echo "orfs-runner: $(basename "$d") exit $rc $(date -Is)"
  done
  sleep 2
done
