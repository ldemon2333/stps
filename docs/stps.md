# STPS Design Notes

This document is a short bridge between [`article.tex`](../article.tex) §4 and the codebase. It is not a rewrite of the paper.

## Offline path — `fingerprint/`

Implements §4.2.

| Paper symbol | Field | Producer |
|---|---|---|
| `E^(t)` | `Fingerprint.E` | `fingerprint.extractor.extract_fingerprint_from_W` |
| `β = max(E)/E[E]` | `Fingerprint.beta` | same |
| `Var(c^(t))` | `Fingerprint.centrality_var` | `fingerprint.centrality.power_iteration_in_eigen_centrality` per snapshot |
| `c^(T)` | `Fingerprint.centrality_last` | last-snapshot centrality |
| `Ḱ` | `Fingerprint.K_mean` | mean active connected components per snapshot |

CLI:

```bash
python -m fingerprint.cli --synthetic --T 64 --beta 4 --K 2 \
    --out npz/synthetic_bursty.npz
```

The DTDG builder ([`fingerprint/dtdg.py`](../fingerprint/dtdg.py)) hooks `nn.Linear` / `nn.Conv*` outputs of a SpikingJelly model and aggregates per-step inter-population flows. Torch is imported lazily so the rest of the package works in CPU-only / no-torch environments.

## Online path — `schedule/stps.py`

Implements §4.3. `STPSScheduler.select_card_for_task` runs the three stages:

1. **Stage 1 — Macro-Card Dispatching.** Score every candidate card by `frag_weight * |free_block_ratio − 1/Ḱ| + beta_weight * β_card` (β penalty only triggers when `fp.beta > beta_high_threshold`).
2. **Stage 2 — Phase-Shifting.** [`schedule/phase_shift.py`](../schedule/phase_shift.py) is a direct port of Algorithm 1: for each candidate card, search `Δt ∈ [0, D_max]` for the offset that minimises the combined transient peak under `BW_max`. Tasks for which no offset stays under `BW_max` are rejected.
3. **Stage 3 — Hotspot Splitting.** [`schedule/hotspot_split.py`](../schedule/hotspot_split.py) flags populations whose centrality exceeds `centrality_split_threshold` for splitting; v1 records indices on the task and leaves physical mapping unchanged.

Per simulation step the scheduler advances every card's forecast timeline left by one tick (`Card.advance_forecast`). `start_offset` on a placed task is honoured by the simulation engine, which skips `Task.simulate_tick` while the task is still inside its phase-shift window.

### Ablation variants

* `STPSSpatialScheduler` — Stage 1 + Stage 3 only. Picks the lowest-score Stage 1 card and zeroes out the offset.
* `STPSTemporalScheduler` — Stage 2 only. Drops `Ḱ`-based fragmentation matching and hotspot splitting.

Both are reachable via `--scheduler stps-spatial` / `--scheduler stps-temporal`.

## Engine integration — `simulation/engine.py`

* `_load_fingerprint_dir` indexes `*.npz` files.
* `_assign_fingerprint(task)` assigns a fingerprint deterministically by `task.task_id % len(paths)`. With no directory it falls back to a synthetic fingerprint so STPS still has signal.
* The physical-tick loop gates `task.simulate_tick()` on `t >= placement_step + start_offset`.

CLI flags introduced for STPS (no-ops elsewhere): `--fingerprint-dir`, `--bw-max`, `--d-max`, `--horizon`, `--centrality-split-threshold`.

## Make targets

```bash
make fingerprints       # generate synthetic fingerprints
make stps               # full STPS
make stps-spatial       # ablation: spatial only
make stps-temporal      # ablation: temporal only
make compare-stps       # baselines + STPS family
```
