# Contributing

- Run the stack with `docker compose up -d --build`; for a fast loop without Docker, `./setup.sh` installs
  oss-cad-suite into `tools/` and `SA_ROOT=/tmp/sa SA_AGH=0 tools/oss-cad-suite/py3bin/python3 -m silicon_agent.server`
  runs the server with the built-in planner loop (no Agnes Harness).
- A new design kind is a generator module (`silicon_agent/<kind>.py`: an IR dataclass, `emit`, `golden`, a
  cocotb bench or vectors) registered in `designs.py` with its schema and prompts; see "Platform: adding a design
  or a device" in the README. A new device is a `Backend` under `silicon_agent/backends/`.
- Every number in a README or a doc must come from an artifact (`results/`, `bench/`, a check report). Failures
  are documented as failures.
- Match the surrounding code: comments explain why, names are plain, no suppression of warnings or tests to make
  something pass. `python -m silicon_agent.bench cases` lists the regression cases; run the ones your change
  touches against a real backend before opening a PR.
