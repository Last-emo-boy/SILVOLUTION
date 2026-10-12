# 硅演 Silicon Agent — Product Film

A 4:29 (268.8 s, 112 bars at 100 BPM), 4K / 60 fps demo film for silicon-agent, built as a Remotion + React +
TypeScript project. Every frame is a pure function of the frame number, every number on
screen is read from a repository artifact, and the score and sound design are synthesised
by this project.

| Deliverable | Path |
|---|---|
| 4K master (3840×2160, 60 fps, H.264, AAC 48 kHz stereo) | `out/Silicon_Agent_Siyan_Master.mp4` |
| 1080p preview | `out/preview/Silicon_Agent_Siyan_1080p_preview.mp4` |
| Soundtrack master / score-only mix | `out/audio/Silicon_Agent_Siyan_Master_Mix.wav`, `…_Score_Mix.wav` |
| Stems (13 instruments, 4 buses, sfx) + 17 UI cues + cue sheet | `out/audio/stem-*.wav`, `sfx-*.wav`, `cue-sheet.json` |
| Feature coverage report | `FEATURE_COVERAGE_REPORT.md` |
| Studio storyline snapshots (production API, 2026-10-11/12) | `public/assets/product/studio/`, captured by `tools/snapshot-studio.sh` |
| Evidence (all on-screen data) | `public/assets/evidence.json`, `docs/evidence/` |
| QA results, contact sheets | `out/qa/` |
| Design system / music | `docs/DESIGN_SYSTEM.md`, `docs/MUSIC.md` |

## Install

```bash
pnpm install
```

Node 22+, pnpm, ffmpeg on `PATH`. All Remotion packages are pinned to `4.0.534`.
Fonts (Space Grotesk, IBM Plex Sans/Mono, Noto Sans SC) come from `@remotion/google-fonts`
and are inlined at bundle time; rendering makes no network request.

## Work on it

```bash
pnpm dev                      # Remotion Studio: the film and every shot as its own composition
npx tsx tools/stills.ts /tmp/s.png chat:200 loop:600            # stills of shot:frame pairs, one bundle, tiled
npx tsx tools/stills.ts --anchors /tmp/a.png sim:400             # + print the product controls' measured boxes
```

Each shot is also registered as `shot-<id>` (e.g. `shot-pnr`), so it can be scrubbed
and rendered alone.

## Regenerate the data, the music, the film

```bash
# 1. evidence — read from the silicon-agent checkout one level up (results/, runs/,
#    bench/, memory/), reproduce the CIC placement with nextpnr, collect SymbiYosys logs
PATH=$PWD/../tools/oss-cad-suite/bin:$PATH ../tools/oss-cad-suite/py3bin/python3 tools/extract-evidence.py
python3 tools/extract-logo.py          # brand-mark geometry from guiyan.png

# 2. soundtrack — score, stems, UI cues, sample-accurate sfx stem, master (−15 LUFS, −1 dBTP)
pnpm audio

# 3. picture
pnpm preview                           # 1080p
pnpm render                            # 4K master (device scale 2)
pnpm render -- --frames=4464-7919      # one segment of the master

# 4. QA
pnpm timeline-check && pnpm coverage && npx tsx scripts/test-core.ts && pnpm typecheck
npx tsx scripts/quality-check.ts out/Silicon_Agent_Siyan_Master.mp4
npx tsx scripts/contact-sheet.ts out/Silicon_Agent_Siyan_Master.mp4
```

The formal-equivalence and mutation evidence in `docs/evidence/` was produced by running the
product's own `formal.equivalence`, `formal.properties` and `mutate.score` on the CIC best
design (equivalence at depth 20 takes ~13 minutes of solver time).

## Change things

| To change | Edit |
|---|---|
| Colours, type, easing, effect strengths | `src/design/tokens.ts`, `src/fx/light.tsx` (`FX` switches) |
| Shot order, lengths (in bars), act grouping | `src/core/timeline.ts` (`SHOTS`) — lengths are bars at 100 BPM, 144 frames each |
| Total duration | add/remove bars in `SHOTS` and the matching sections in `src/audio/composition.ts`; `pnpm timeline-check` asserts they agree |
| Tempo / key / arrangement | `src/audio/composition.ts`, `src/audio/arrangement.ts` |
| A shot | `src/shots/*.tsx` (camera keys are at the top of each shot) |
| Transitions, feature claims | `src/shots/entries.ts` |
| Which run the film follows | `tools/extract-evidence.py` (`cic_job`, tournament jobs) — shots read only `evidence.json` |
| Sound design | `src/audio/sfx.ts` (cue synthesis), `src/audio/cues.ts` (cue sheet) |
| Language of on-screen copy | strings in `src/shots/*.tsx` (Chinese primary, English technical terms) |

## How it is built

- **Timeline** (`src/core/timeline.ts`) — one table of shots in bars; every cut is on a bar
  line, so picture and score are locked for the whole film.
- **Camera** — `src/core/stage3d.tsx` is a CSS 3D camera (target, distance, yaw, pitch, roll,
  depth of field) for UI planes, which keeps text crisp at 4K; `src/three/kit.tsx` drives
  Three.js scenes (the board, the die, place-and-route, the planner loop, the device stage,
  the design space, the memory constellation) from the frame number.
- **Pointer** — drawn inside the UI plane it operates, so camera moves can't misalign it;
  its targets are named, and its click frames come from `src/shots/timing.ts`, which the
  sound design reads too.
- **Kinetic text** (`src/fx/text.tsx`) — typewriter, token stream, code stream, terminal,
  scrambling titles, counters.
- **Effects** (`src/fx/light.tsx`) — tracers, light sweeps, lock-on brackets, stamps, and
  overlapping shot transitions (zoom / pull / whip / wipe / iris / flash), each switchable;
  `src/fx/ActSlam.tsx` chapter cards; `src/fx/atmos.tsx` room light and lens flares;
  `src/fx/beat.ts` the kick track as a picture signal.
- **Evidence** (`src/product/evidence.ts`) — the only source of measured values.
- **Product screens** (`src/product/ui/`) — the chat surface and the worktable rebuilt from the
  product's own front end: same markup and class names as `silicon_agent/web/chat.js` /
  `app.js` / `form.js`, the product's own `chat.css` / `app.css` scoped by
  `tools/scope-product-css.mjs`, and data from the product's API, captured by
  `tools/snapshot-product.sh` while the product ran locally on a copy of the repository's
  results (read-only, no model key). The result card's formal check and mutation score in the
  evidence shot were produced by the product's own `/api/jobs/<id>/formal` and `/mutation`
  endpoints. `ProductStage.tsx` puts a window in 3D, measures each control's position from the
  DOM so the pointer clicks it wherever layout puts it, and can separate the window into its
  depth layers.
- **Product shots** — from the request act to the results act the film is the product itself:
  `src/shots/Product.tsx` (chat, the Agnes Harness tournament conversation, the result card's
  checks, the project page) and `src/shots/Worktable.tsx` (new-run form, catalog, RTL, the
  simulation ladder, design tab, transcript, trajectory, compare, memory, bench). Each is a
  pointer script (`PRODUCT_MOVES` in `src/shots/timing.ts`, which the cue sheet also reads), a
  camera aimed at the product's own controls by their measured position (`src/shots/kit.tsx`
  `focusCam`), and a lower-third caption that says in one sentence what the viewer is looking at.
  The rendered-only shots are the brand opening, the premise, place-and-route and the close.

## Honesty rules

The specification forbids fabricated PPA figures, test results or EDA output. In this film:

- every Fmax / LUT / FF / DSP / seed value comes from `results/*/summary.json`,
  `results/*/iter_*.json`, `bench/nightly-*.json` or a re-run nextpnr report;
- logs on screen (nextpnr, cocotb, SymbiYosys) are the real logs, path-stripped;
- the RTL on screen is the generator's output and is byte-identical to the measured run's RTL;
- the planner's diagnosis, the agent's tool calls and its reply are shown verbatim, as written;
- failures stay in: the unconverged 5×5 conv, infeasible candidates, the −193.3 MHz lesson,
  the 495.5 MHz point that violates its DSP budget, the Artix-7 backend that is implemented but
  disabled on the deployed host.
