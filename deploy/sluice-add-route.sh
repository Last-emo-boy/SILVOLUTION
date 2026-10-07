#!/usr/bin/env bash
# Register silicon.w33d.xyz in sluice: append to the route seed (source of truth,
# with a timestamped backup like the vivado rollout did) and upsert the live Postgres
# row so the gateway picks it up on its next route reload. Run ON the host as root.
set -euo pipefail
INFRA=/root/w33d_infra
SEED=$INFRA/deploy/routes.seed.json
ROUTE=/opt/silicon-agent/app/deploy/sluice-route.json
PG="docker exec -i steadholme-postgres-1 psql -U steadholme -d steadholme -v ON_ERROR_STOP=1"

cp -n "$SEED" "$SEED.before-silicon-$(date -u +%Y%m%dT%H%M%S)"
python3 - "$SEED" "$ROUTE" <<'PY'
import json, sys
seed_p, route_p = sys.argv[1], sys.argv[2]
seed = json.load(open(seed_p)); routes = seed["routes"] if isinstance(seed, dict) else seed
new = json.load(open(route_p))
names = {r["name"] for r in routes}
for r in new:
    if r["name"] in names:
        routes[[x["name"] for x in routes].index(r["name"])] = r
    else:
        routes.append(r)
json.dump(seed, open(seed_p, "w"), indent=2, ensure_ascii=False); open(seed_p, "a").write("\n")
print(f"seed now has {len(routes)} routes")
PY

python3 - "$ROUTE" <<'PY' | $PG
import json, sys
for r in json.load(open(sys.argv[1])):
    m = r["match"]
    cols = dict(name=r["name"], host=m["host"], path_prefix=m["path_prefix"], upstream=r["upstream"],
                protected=r["protected"], auth=r["auth"], waf=r["waf"], require_group=r["require_group"],
                require_scope=r["require_scope"], internal_only=r["internal_only"],
                require_permission=r["require_permission"], permission_resource=r["permission_resource"],
                risk=r["risk"], step_up_resume_path=r.get("step_up_resume_path", ""))
    q = lambda v: ("true" if v else "false") if isinstance(v, bool) else "'" + str(v).replace("'", "''") + "'"
    keys = ",".join(cols); vals = ",".join(q(v) for v in cols.values())
    upd = ",".join(f"{k}=EXCLUDED.{k}" for k in cols if k != "name")
    print(f"INSERT INTO routes ({keys}) VALUES ({vals}) ON CONFLICT (name) DO UPDATE SET {upd};")
PY

echo "live routes for silicon.w33d.xyz:"
$PG -At -c "select name,host,path_prefix,upstream,auth,require_permission from routes where host='silicon.w33d.xyz'"
