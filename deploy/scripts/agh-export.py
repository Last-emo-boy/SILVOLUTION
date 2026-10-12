"""Export AGH session records (the harness's UI projection: turns, tool calls with argument / result previews,
model, ledger) as JSON, for human-checkable evidence. Runs inside the server container:

    docker exec -i silicon-agent python3 /app/deploy/scripts/agh-export.py <session-key>[:<cwd>] ... > out.json

An idle session must be loaded before the harness projects it; the designer sessions live in their studio's work/.
"""
import json
import sys

from silicon_agent import agh

c = agh.client()
out = {}
for arg in sys.argv[1:]:
    key, _, cwd = arg.partition(":")
    try:
        c.load_session(key, cwd or agh.workspace())
        p = c.project(key)
        out[key] = {"opState": p.get("opState"), "turns": p.get("turns"),
                    "nodes": [{k: (str(v)[:4000] if k in ("text", "argsPreview", "resultPreview") else v) for k, v in n.items()} for n in (p.get("nodes") or [])]}
    except Exception as e:  # noqa: BLE001
        out[key] = {"error": str(e)[:300]}
print(json.dumps(out, ensure_ascii=False))
