# Repository guidance

This repository contains the experimental simulator for the paper [article/article.tex](article/article.tex), **STPS: Spatio-Temporal Proactive Scheduling for Spiking Neural Networks on Compute-in-Memory Clusters**. `AGENTS.md` delegates agent guidance to this file. Keep the paper and its reproducibility trail central when working here.

## Source of truth and layout

- `article/article.tex` is the paper source. Its relative `picture/` inputs, `tables/`, `references.bib`, and `usenix-2019-v3.sty` stay together in `article/`. Compile from that directory.
- `main.py` is the single-run CLI; `simulation/engine.py` runs arrivals, admission, physical ticks, queues, completions, and metrics. `util/` holds card/task models and metric writers.
- `fingerprint/` builds and loads SNN traffic fingerprints; `npz/` is a local fingerprint directory. Binary inputs are ignored by Git. See `docs/fingerprint.md` for the current format.
- `schedule/` holds STPS, its ablations, RR/BestFit/DRF/P2C and phase wrappers. The scheduler registry supplies `main.py --list-schedulers`.
- `script/` contains experiment and figure entrypoints; `data/` holds local CSV results, `figures/` and `article/picture/` hold local figure artifacts. These generated directories are ignored by Git. `docs/` records experiment setup and metric definitions; `tests/` tests simulator behavior.
- `extras/` and `archive/` hold local materials outside the versioned paper simulator.

The legacy `SNN schedule/` path is a symlink to `article/`. Some existing figure and table scripts still write through that path. Preserve this alias if code is not being changed. Do not recreate a second paper tree.

## Paper-to-code boundaries

The current paper presents an offline fingerprint and two online decisions: card dispatch and bounded phase shift. `schedule/stps.py` also records hotspot split candidates as `task.split_plan`; this is not a physical remapping step in the simulator. Check the implementation and the particular experiment data before claiming an effect. This is a discrete-time simulation, not a hardware measurement.

The paper's evaluation setup uses different parameters from the convenient Makefile and `main.py` defaults. For a result claim, identify the script, CSV, seeds, scheduler variant, cards, tasks, steps, bandwidth cap, and metric definition. Preserve the distinction between existing recorded artifacts and a run verified in the current workspace. Use `article/experiment_regen.md` and relevant `docs/Q*_result.md` as provenance pointers, then verify claims against local CSV and source. The CSVs, NPZ inputs, and paper figure binaries are not shipped in Git.

## Commands

The environment previously used for this project is `/root/miniconda3/envs/snn/bin/python`; Makefile uses it by default and accepts `PYTHON=...` override.

```bash
/root/miniconda3/envs/snn/bin/python main.py --list-schedulers
/root/miniconda3/envs/snn/bin/python -m pytest tests/
make q0
make q1
python script/q2_run.py main4
```

`make q0`, `make q1`, and the Q2 runner can run substantial experiment matrices and write into existing result directories. The current `make q2-scale16` target passes `scale16`, which `script/q2_run.py` does not accept; use the runner directly with `main16` for that scale. For a small simulator run, call `main.py` with explicit `--cards`, `--tasks`, `--steps`, `--seed`, `--arrival-mode`, `--fingerprint-dir`, `--bw-max`, and `--d-max`; inspect `python main.py --help` for current flags. Makefile defaults: `CARDS=4`, `TASKS=512`, `STEPS=512`, `SEED=21`, `ARRIVAL_MODE=bursty`, `FINGERPRINT_DIR=npz`, `BW_MAX=5e6`, `D_MAX=16`, `HORIZON=64`. These are not the paper's default evaluation workpoint.

```bash
cd article
latexmk -pdf -interaction=nonstopmode -outdir=build article.tex
```

The paper build needs six `article/picture/*.pdf` inputs. Generate them with the plotting scripts or restore them locally before compiling a fresh clone.

## Maintenance conventions

- Keep paper citations and figure paths relative to `article/`; README and docs should link to `article/article.tex` from the repository root.
- Keep local `data/` CSVs and `.npz` inputs available during experiments. They are ignored by Git along with result figures, runtime logs, Python caches, LaTeX auxiliary files, and W&B logs.
- Do not run `make clean` as a general repository cleanup: its target removes top-level CSVs and figure files.
- When changing code, keep CLI arguments, Makefile calls, output paths, and documentation aligned. Place schedulers in `schedule/`, register them through `schedule/base.py`, and test the affected behavior.
- Preserve unrelated workspace modifications and avoid committing unless requested.
