# Repository guidance

AGENTS.md delegates here. The active code implements fixed single-card replay plus online multi-card baseline placement using the same elastic NoC model. Paper source article/article.tex and historical results must not be represented as results of the new network.

## Source of truth

- main.py uses --scenario for fixed mapping or --cluster-scenario + --policy for online RR/WorstFit/DRU/BestFit/P2C-Mean/STPS. No legacy FIFO.
- simulation/scenario.py validates schema_version=1 JSON, earliest start_tick, explicit mapping, single-task capacity and workload paths.
- fingerprint/workload.py owns directed traffic plus independent SOP in JSON/no-pickle NPZ. Aggregate E-only input is rejected.
- fingerprint/dtdg.py captures explicitly named model modules; fingerprint/extractor.py uses caller-supplied topology. Slicing/mask/edge helpers are offline, not physical placement.
- simulation/noc.py is deterministic single-VC XY with bounded source/router/sink queues; decisions read old state and commit atomically.
- simulation/engine.py runs fixed K-cycle physical ticks. Queues persist across ticks; all active tasks wait at the card-wide Rx barrier before issuing their next logical step. SOP/traffic are issued once. Actual core/memory lifetimes control admission; max_ticks truncation exits 2.
- util/metrics.py writes core/task/card CSV plus queues/links. simulation/report.py renders self-contained HTML/SVG.
- examples/single_card uses synthetic sparse traces. script/validate_single_card.py runs positive, carry-over, buffer, manually staggered and truncated scenes.
- docs/hyperparam.md and docs/metrics.md list parameters/outputs; docs/arch.md and docs/experiment.md describe current behavior. docs/rfc.md and docs/algo.md also mark future scheduling work.

## Commands

```bash
python -m pytest -q tests/
python script/validate_single_card.py --output-root data/phase1
python main.py --scenario examples/single_card/positive.json --output-dir data/new_run --trace
```

Python >=3.10 + NumPy suffice. Pytest is a development dependency. Model hook profiling additionally needs torch; hook tests skip without it. /root/miniconda3/envs/snn/bin/python is usable locally.

## Conventions

- Preserve unrelated workspace edits and local input/result artifacts. Do not commit unless asked.
- Output paths must be new or empty. Do not use broad cleanup or make clean.
- Preserve task identity for delivered-but-unconsumed flits even after endpoint reuse.
- Each link transfer takes a full NoC cycle. No-contention delivery is Manhattan hops + 2. A transfer completing at K is checked before the next round; logical steps may span physical ticks.
- Maintain generated=Tx+source, Tx=Rx+network, Rx=consumed+sink. Pending source batches must not materialize arbitrarily many flits.
- Distinguish synthetic, modeled operator SOP, model profiling and hardware evidence. Do not infer compute from traffic or occupancy.
- Joint STPS and delayed start are implemented. SchedulingFingerprint uses independent calibration edge means and total SOP[T]. No future replay arrays enter choose_stps. Large-model/hardware validation remains future work.
- Keep article/ LaTeX files together. Do not alter ignored datasets/weights/results or the SNN schedule alias to article/.

- simulation/cluster_engine.py uses one physical clock and independent card_runtime.py networks/barriers. Resources are reserved at placement and released only on actual completion. All policies use row-major-free-v1 mapping.
- schedule/baselines.py receives resource snapshots and declared offline means; P2C does not read future replay traces or successful throughput.
- cluster_metrics.py merges all cards including idle Tick rows and emits full/steady balance with numerators/denominators. Cluster report links card detail reports.
- Run make cluster-validate for all tests and the ten fixed synthetic baseline cases.
- Cluster metrics include offered and served communication, CV/JFI/LIF/P99-to-mean/max-to-mean, temporal tails, and full 1/4/8-Tick sliding windows. LIF is max/mean; with four cards nearest-rank P99 equals max.
- Use sliding_window_ms only with explicit physical_tick_ms calibration. Historical STPS artifacts predate hotspot metrics; use make stps-hotspots for same-binary comparisons.

- schedule/stps.py evaluates all feasible card/offset combinations. Completion mode ranks by (J, pressure, delay, card_id); balance mode applies its J slack gate before projected dual-load balance ranking. Do not silently change objective/parameters using held-out comparison outcomes.
- CardRuntime reserves at placement, stores requested_start separately, and starts due tasks only at the card round boundary; ongoing tasks continue while another is delayed.
- make stps-compare freezes independent synthetic calibration/validation/test inputs; make stps-hotspots re-runs the five baselines and balance-objective STPS under one current binary. docs/results/stps_hotspots.md is the retained comparison and reports mixed results without general superiority claims.
