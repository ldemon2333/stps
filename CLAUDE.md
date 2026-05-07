# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Overview

**STPS** (Spatio-Temporal Proactive Scheduling) — a simulation framework for scheduling Spiking Neural Network (SNN) inference tasks on a multi-card Compute-in-Memory (CIM / Darwin) cluster. The framework decouples the NP-hard time-dependent MINLP scheduling problem into:

1. **Offline DTDG workload fingerprinting** (`fingerprint/`) — turns an SNN trace into a Discrete-Time Dynamic Graph and extracts four physical fingerprints: traffic timeline `E^(t)`, global burstiness `β`, in-eigenvector centrality variance `Var(c^(t))`, and active connected components `K̄`.
2. **Online 3-stage hierarchical scheduler** (`schedule/stps.py`) — Macro-Card Dispatching → Micro-Temporal Phase-Shifting (Algorithm 1) → Micro-Spatial Mapping with Hotspot Splitting.

Language: Python. Workflow driver: `Makefile`. Project Python interpreter: `/root/miniconda3/envs/snn/bin/python` (conda env `snn`). Use this interpreter for tests, scripts, fingerprint extraction, and Make targets unless the user says otherwise. See [article.tex](article.tex) §4 for the full design and [docs/stps.md](docs/stps.md) for the code-side bridge.

## Architecture

- [main.py](main.py) — CLI entry point. Parses args and calls `simulation.engine.run_simulation`.
- [fingerprint/](fingerprint/) — offline DTDG fingerprint extraction:
  - `__init__.py` — `Fingerprint` dataclass + public API
  - `centrality.py` — power-iteration in-eigenvector centrality
  - `extractor.py` — `extract_fingerprint_from_W(W)` over a `(T, V, V)` weight tensor
  - `dtdg.py` — `DTDGBuilder.from_spikingjelly(...)` (lazy torch import)
  - `synth.py` — synthetic fingerprint generator (no torch needed)
  - `io.py` — `.npz` save/load
  - `cli.py` — `python -m fingerprint.cli`
- [schedule/](schedule/) — pluggable schedulers, registered via `schedule/__init__.py`:
  - `stps.py` — `STPSScheduler`, `STPSSpatialScheduler`, `STPSTemporalScheduler` (the main algorithm + ablations)
  - `phase_shift.py` — Algorithm 1 (cross-correlation kernel for Stage 2)
  - `hotspot_split.py` — centrality-driven population splitting helper for Stage 3
  - `bestfit.py`, `drf.py`, `p2c.py`, `roundrobin.py` — baselines
  - `placement_strategy.py` — shared placement helpers (e.g. `_estimated_task_traffic`)
  - `base.py` — `BaseScheduler`, scheduler registry
- [simulation/engine.py](simulation/engine.py) — simulation lifecycle (arrivals, placement, two-tier ticks, metrics, completions). Loads fingerprints from `--fingerprint-dir`, gates `Task.simulate_tick` on `start_offset` for STPS.
- [util/](util/) — `card.py` (resource model + lazy forecast timeline + `beta_card`), `task.py` (task model + STPS fields), `sim.py`, `metrics.py`.
- Output dirs: `data/` (CSVs), `log/` (run logs), `npz/` (`.npz` fingerprints).

## Common Commands

Defaults: `CARDS=4 TASKS=512 STEPS=512 SEED=21 ARRIVAL_MODE=bursty`.

Use the project interpreter explicitly when running Python directly:
```bash
/root/miniconda3/envs/snn/bin/python -m pytest tests/
/root/miniconda3/envs/snn/bin/python main.py --list-schedulers
```

Baselines:
```bash
make bestfit drf p2c rr      # run individually or chain on one line
make compare                 # all baselines
```

STPS family (paper §4.3):
```bash
make fingerprints            # generate synthetic *.npz fingerprints into npz/
make stps                    # full 3-stage STPS
make stps-spatial            # ablation: Stage 1 + Stage 3 only (no phase shift)
make stps-temporal           # ablation: Stage 2 only (no fragmentation / no hotspot split)
make compare-stps            # baselines + STPS family for paper Q1/Q3
```

Override knobs via env vars: `CARDS=8 TASKS=200 ARRIVAL_MODE=poisson SEED=99 BW_MAX=5e6 make stps`.

Direct CLI:
```bash
python main.py --scheduler stps --cards 4 --tasks 128 --steps 128 \
    --arrival-mode bursty --fingerprint-dir npz \
    --bw-max 5e6 --d-max 16 --horizon 64
python main.py --list-schedulers
```

Offline fingerprint extraction:

```bash
python -m fingerprint.cli --synthetic --T 64 --beta 4 --K 2 \
    --out npz/synthetic_bursty.npz
```

Cleanup: `make clean` removes `log/*.log`, `data/*.csv`, `figures/*`.

## Key Parameters

| Var | Default | Meaning |
|-----|---------|---------|
| `CARDS` | 4 | accelerator card count |
| `TASKS` | 512 | total tasks scheduled |
| `STEPS` | 512 | simulation time steps |
| `SEED` | 21 | RNG seed |
| `ARRIVAL_MODE` | bursty | `poisson` / `bursty` / `mixed` |
| `FINGERPRINT_DIR` | `npz` | dir of `.npz` fingerprints (STPS only) |
| `BW_MAX` | `5e6` | NoC bandwidth ceiling per card |
| `D_MAX` | 16 | max phase-shift delay (ticks) |
| `HORIZON` | 64 | forecast traffic horizon (ticks) |
| `SPLIT_THRESHOLD` | 0.2 | hotspot-split threshold on centrality |

## Conventions

- New schedulers go in `schedule/`, subclass `BaseScheduler`, override `select_card_for_task`, and register via `register_scheduler(...)` so `--list-schedulers` and Make targets pick them up.
- Fingerprint files are `.npz` produced by `fingerprint.io.save_fingerprint`; the schema is documented in [docs/stps.md](docs/stps.md). Tasks reference fingerprints by path (lazy-loaded inside `STPSScheduler._resolve_fingerprint`).
- Data filename pattern: `data/{scheduler}_loads_*.csv` and `data/{scheduler}_summary_*.csv`. Use `--data-output <prefix>` (or `DATA_OUTPUT=<prefix>`) to override timestamp-based names.
- Keep CLI args in [main.py](main.py) and Makefile `COMMON_ARGS` / `STPS_ARGS` in sync when adding parameters.
- STPS knobs (`--fingerprint-dir`, `--bw-max`, `--d-max`, `--horizon`, `--centrality-split-threshold`) are no-ops for non-STPS schedulers; defaults are chosen so existing baseline runs are bit-equivalent.

## Documentation

- [README.md](README.md) — quick start
- [article.tex](article.tex) — paper source (§4 = SYSTEM DESIGN, §5 = Evaluation)
- [docs/stps.md](docs/stps.md) — code-side bridge for the STPS design
- [TODO.md](TODO.md) — implementation plan (kept for traceability)
