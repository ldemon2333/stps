# STPS Redesign Implementation Plan (TODO)

> **Scope:** Redesign two parts of the GLaSS codebase to align with the SYSTEM DESIGN section of `article.tex` (Spatio-Temporal Proactive Scheduling, STPS):
> 1. **Offline DTDG workload fingerprint extraction** (paper §4.2)
> 2. **Online 3-stage STPS scheduling simulation** (paper §4.3)
>
> **For human review.** Do not start coding until this plan is approved.

**Goal:** Add an offline DTDG fingerprint extractor and an online STPS scheduler (`stps`) with 3 hierarchical stages — Macro-Card Dispatching, Micro-Temporal Phase-Shifting, Micro-Spatial Mapping — integrated alongside the existing schedulers.

**Architecture:**
- Offline path: SNN model + dataset → SpikingJelly trace → DTDG `{G^(t)}` → fingerprint file `*.npz` consumed by the scheduler.
- Online path: New scheduler `STPSScheduler` reads each task's fingerprint at arrival, runs 3-stage pipeline, updates a per-card forecast traffic timeline `E_m^(t)`, defers task start by `Δt_start*`.
- Existing baselines (RR/BestFit/DRF/P2C/GLaSS) are untouched. STPS-Spatial and STPS-Temporal ablation variants share code via flags.

**Tech Stack:** Python 3, NumPy, SpikingJelly (offline only), existing simulation engine.

---

## File Structure

### New files
- [fingerprint/__init__.py](fingerprint/__init__.py) — public API (`extract_fingerprint`, `Fingerprint`, `load_fingerprint`, `save_fingerprint`).
- [fingerprint/dtdg.py](fingerprint/dtdg.py) — `DTDGBuilder`: turns a SpikingJelly forward trace into `{G^(t) = (V, E^(t), W^(t))}`.
- [fingerprint/extractor.py](fingerprint/extractor.py) — computes the three fingerprints `Var(c^(t))`, `(β, E^(t))`, `K̄`. Pure NumPy; no torch dependency at runtime.
- [fingerprint/centrality.py](fingerprint/centrality.py) — `power_iteration_in_eigen_centrality(W, iters, tol)` returning `c^(t)` per step.
- [fingerprint/io.py](fingerprint/io.py) — `save_fingerprint(path, fp)` / `load_fingerprint(path)` (`.npz` schema below).
- [fingerprint/cli.py](fingerprint/cli.py) — CLI: `python -m fingerprint.cli --model VGG_CIFAR --dataset CIFAR10 --T 16 --out npz/vgg_cifar.npz`.
- [fingerprint/synth.py](fingerprint/synth.py) — synthetic fingerprint generator for tests and for tasks that lack a real SpikingJelly trace (parametrised by `β`, `K̄`, `centrality_var`).
- [schedule/stps.py](schedule/stps.py) — `STPSScheduler` (the new online scheduler) plus `STPSSpatialScheduler` and `STPSTemporalScheduler` ablations.
- [schedule/phase_shift.py](schedule/phase_shift.py) — `find_optimal_offset(E_m, E_new, D_max, BW_max)` (Algorithm 1 from the paper).
- [schedule/hotspot_split.py](schedule/hotspot_split.py) — `split_population(centrality, threshold)` returning a list of split indices (used for the spatial mapping stage).
- [tests/test_fingerprint_extractor.py](tests/test_fingerprint_extractor.py)
- [tests/test_phase_shift.py](tests/test_phase_shift.py)
- [tests/test_stps_scheduler.py](tests/test_stps_scheduler.py)

### Modified files
- [schedule/__init__.py](schedule/__init__.py) — register `stps`, `stps-spatial`, `stps-temporal`.
- [util/task.py](util/task.py) — add optional fields: `fingerprint_path: Optional[str]`, `fingerprint: Optional[Fingerprint]`, `start_offset: int = 0`. No behavior change for tasks without a fingerprint.
- [util/card.py](util/card.py) — add `forecast_traffic: np.ndarray` (length = horizon `H`) and helpers `add_traffic(E, offset)`, `peak()`. Lazy-allocated; baselines that never call them stay unaffected.
- [simulation/engine.py](simulation/engine.py) — (a) load per-task fingerprints when `--fingerprint-dir` is set; (b) honor `task.start_offset` so a task held by phase-shifting begins ticking only at `arrival_step + start_offset`.
- [main.py](main.py) — add CLI flags: `--fingerprint-dir`, `--bw-max`, `--d-max`, `--horizon`, `--centrality-split-threshold`. Keep them off by default so existing scripts/Makefile keep working.
- [Makefile](Makefile) — add `make stps`, `make stps-spatial`, `make stps-temporal`, `make fingerprints` targets.
- [docs/](docs/) — add `docs/stps.md` (short design doc cross-referencing the paper sections).

### Fingerprint `.npz` schema
| key | shape | meaning |
|-----|-------|---------|
| `E` | `(T,)` float32 | traffic timeline `E^(t)` (NoC volume per step) |
| `beta` | scalar float32 | global burstiness `β = max(E)/mean(E)` |
| `centrality_var` | `(T,)` float32 | `Var(c^(t))` per step (and we also store mean for hotspot decisions) |
| `centrality_last` | `(|V|,)` float32 | last-step centrality vector `c^(T)`, used by hotspot splitting |
| `K_mean` | scalar float32 | average active connected components `K̄` |
| `T` | scalar int32 | window size |
| `meta` | json string | `{model, dataset, dt, generated_at}` |

---

## Self-Review Checklist (apply at the end of implementation)

- [ ] Every fingerprint produced by `fingerprint.cli` loads successfully via `load_fingerprint` and round-trips bit-exact.
- [ ] `STPSScheduler` runs end-to-end with the existing simulation engine using synthetic fingerprints (no SpikingJelly install needed for CI).
- [ ] `STPSSpatialScheduler` (no phase-shifting) and `STPSTemporalScheduler` (no `K̄` / hotspot logic) are reachable from the CLI.
- [ ] `make compare` still runs every existing baseline unchanged.
- [ ] No new mandatory CLI args for existing schedulers.

---

## Task 1 — Fingerprint package skeleton

**Files:**
- Create: [fingerprint/__init__.py](fingerprint/__init__.py)
- Create: [fingerprint/io.py](fingerprint/io.py)
- Create: [tests/test_fingerprint_io.py](tests/test_fingerprint_io.py)

- [ ] **Step 1 — write failing test** for round-trip save/load of a `Fingerprint` dataclass.
  ```python
  def test_fingerprint_round_trip(tmp_path):
      fp = Fingerprint(
          E=np.arange(16, dtype=np.float32),
          beta=np.float32(2.5),
          centrality_var=np.full(16, 0.1, dtype=np.float32),
          centrality_last=np.array([0.2, 0.8], dtype=np.float32),
          K_mean=np.float32(1.0),
          T=16,
          meta={"model": "synthetic"},
      )
      path = tmp_path / "fp.npz"
      save_fingerprint(path, fp)
      loaded = load_fingerprint(path)
      np.testing.assert_array_equal(loaded.E, fp.E)
      assert loaded.beta == fp.beta
      assert loaded.meta["model"] == "synthetic"
  ```
- [ ] **Step 2 — run** `pytest tests/test_fingerprint_io.py -v` → expect FAIL (`Fingerprint` undefined).
- [ ] **Step 3 — implement** `Fingerprint` dataclass in `fingerprint/__init__.py`:
  ```python
  @dataclass(frozen=True)
  class Fingerprint:
      E: np.ndarray             # (T,) float32
      beta: float
      centrality_var: np.ndarray  # (T,) float32
      centrality_last: np.ndarray # (|V|,) float32
      K_mean: float
      T: int
      meta: Dict[str, str]
  ```
  And in `fingerprint/io.py`:
  ```python
  def save_fingerprint(path: Path, fp: Fingerprint) -> None:
      np.savez_compressed(
          path,
          E=fp.E, beta=fp.beta,
          centrality_var=fp.centrality_var,
          centrality_last=fp.centrality_last,
          K_mean=fp.K_mean, T=np.int32(fp.T),
          meta=json.dumps(fp.meta),
      )

  def load_fingerprint(path: Path) -> Fingerprint:
      d = np.load(path, allow_pickle=False)
      return Fingerprint(
          E=d["E"].astype(np.float32),
          beta=float(d["beta"]),
          centrality_var=d["centrality_var"].astype(np.float32),
          centrality_last=d["centrality_last"].astype(np.float32),
          K_mean=float(d["K_mean"]),
          T=int(d["T"]),
          meta=json.loads(str(d["meta"])),
      )
  ```
- [ ] **Step 4 — run tests** → expect PASS.
- [ ] **Step 5 — commit:** `feat(fingerprint): add Fingerprint dataclass and npz round-trip`.

---

## Task 2 — In-eigenvector centrality via power iteration

**Files:**
- Create: [fingerprint/centrality.py](fingerprint/centrality.py)
- Create: [tests/test_centrality.py](tests/test_centrality.py)

- [ ] **Step 1 — failing test:** centrality of a star graph (1 hub, N spokes) concentrates mass on the hub.
  ```python
  def test_star_graph_centrality_concentrates_on_hub():
      N = 5
      W = np.zeros((N, N), dtype=np.float32)
      W[1:, 0] = 1.0   # all spokes feed hub 0
      c = power_iteration_in_eigen_centrality(W, iters=200, tol=1e-9)
      assert np.argmax(c) == 0
      assert c[0] > 0.7
  ```
- [ ] **Step 2 — run** → FAIL.
- [ ] **Step 3 — implement** `power_iteration_in_eigen_centrality(W, iters=100, tol=1e-6)` operating on the **transposed** weight matrix (in-edges → in-eigenvector), L1-normalising the iterate every step and stopping on `|c_{k+1} - c_k|_1 < tol`. Complexity `O(iters · |E^(t)|)` when `W` is sparse; accept dense `np.ndarray` for v1.
- [ ] **Step 4 — run** → PASS.
- [ ] **Step 5 — add second test:** disconnected graph returns finite, non-NaN values (degenerate case must not divide by zero).
- [ ] **Step 6 — commit:** `feat(fingerprint): in-eigenvector centrality via power iteration`.

---

## Task 3 — DTDG builder + extractor

**Files:**
- Create: [fingerprint/dtdg.py](fingerprint/dtdg.py)
- Create: [fingerprint/extractor.py](fingerprint/extractor.py)
- Create: [fingerprint/synth.py](fingerprint/synth.py)
- Create: [tests/test_fingerprint_extractor.py](tests/test_fingerprint_extractor.py)

- [ ] **Step 1 — failing test** on synthetic spike-rate tensor with shape `(T, |V|, |V|)`:
  ```python
  def test_synthetic_bursty_fingerprint():
      T, V = 16, 8
      W = np.zeros((T, V, V), dtype=np.float32)
      # Burst at t=4, otherwise flat low traffic
      W += 1.0
      W[4] += 50.0
      fp = extract_fingerprint_from_W(W)
      assert fp.T == T
      assert fp.beta > 5.0          # clearly bursty
      assert fp.E.shape == (T,)
      assert np.argmax(fp.E) == 4
      assert fp.K_mean >= 1.0
  ```
- [ ] **Step 2 — run** → FAIL.
- [ ] **Step 3 — implement** in `fingerprint/extractor.py`:
  ```python
  def extract_fingerprint_from_W(W: np.ndarray, meta: dict | None = None) -> Fingerprint:
      T = W.shape[0]
      E = W.sum(axis=(1, 2)).astype(np.float32)        # NoC volume per step
      mean_E = float(E.mean()) or 1e-9
      beta = float(E.max() / mean_E)
      cvar = np.zeros(T, dtype=np.float32)
      for t in range(T):
          c_t = power_iteration_in_eigen_centrality(W[t])
          cvar[t] = float(c_t.var())
      c_last = power_iteration_in_eigen_centrality(W[-1])
      K_mean = float(np.mean([
          _active_components(W[t] > 0) for t in range(T)
      ]))
      return Fingerprint(E, beta, cvar, c_last, K_mean, T, meta or {})
  ```
  `_active_components` uses `scipy.sparse.csgraph.connected_components` if available, else a small DFS over the boolean adjacency, counting only components that contain at least one active node.
- [ ] **Step 4 — implement** `fingerprint/dtdg.py` `DTDGBuilder.from_spikingjelly(net, dataloader, T)` returning the `(T, V, V)` weight tensor by hooking each `MultiStepLayer`'s spike output and recording per-step inter-layer flow `E^(t)_{ij}`. Population `i` = source layer, population `j` = downstream layer; `w_{ij}^(t)` = (#spikes leaving `i` at step `t`) × (synapses `i → j`). SpikingJelly is imported lazily inside the function so the module import does not require torch.
- [ ] **Step 5 — implement** `fingerprint/synth.py` `make_synthetic_fingerprint(beta_target, K, var_target, T, seed)` for tests and dev workflows (no SpikingJelly install needed).
- [ ] **Step 6 — run** all `tests/test_fingerprint_extractor.py` → PASS.
- [ ] **Step 7 — commit:** `feat(fingerprint): DTDG extractor for E, beta, centrality var, K`.

---

## Task 4 — Fingerprint CLI

**Files:**
- Create: [fingerprint/cli.py](fingerprint/cli.py)
- Modify: [Makefile](Makefile) (add `npz` target)

- [ ] **Step 1 — implement** `python -m fingerprint.cli`:
  - args: `--model`, `--dataset`, `--T`, `--batches`, `--out`, `--synthetic` (skips SpikingJelly and uses `synth.py`).
  - default behavior with `--synthetic`: produces a small `.npz` so users can run STPS without installing SpikingJelly.
- [ ] **Step 2 — manual smoke check:**
  ```bash
  python -m fingerprint.cli --synthetic --T 32 --beta 4 --K 2 --out npz/demo.npz
  python -c "from fingerprint import load_fingerprint; print(load_fingerprint('npz/demo.npz'))"
  ```
  Expected: prints a `Fingerprint(...)` summary.
- [ ] **Step 3 — Makefile target:**
  ```make
  fingerprints:
      mkdir -p fingerprints
      python -m fingerprint.cli --synthetic --T 32 --beta 4 --K 2 --out npz/synthetic_bursty.npz
      python -m fingerprint.cli --synthetic --T 32 --beta 1.05 --K 1 --out npz/synthetic_flat.npz
  ```
- [ ] **Step 4 — commit:** `feat(fingerprint): CLI entry point for offline extraction`.

---

## Task 5 — Phase-shifting kernel (Algorithm 1)

**Files:**
- Create: [schedule/phase_shift.py](schedule/phase_shift.py)
- Create: [tests/test_phase_shift.py](tests/test_phase_shift.py)

- [ ] **Step 1 — failing test:** two anti-phase pulses must be perfectly interleaved.
  ```python
  def test_phase_shift_interleaves_two_pulses():
      H = 16
      E_m = np.zeros(H, dtype=np.float32); E_m[2] = 10.0
      E_new = np.zeros(H, dtype=np.float32); E_new[2] = 10.0
      offset, peak = find_optimal_offset(E_m, E_new, D_max=8, BW_max=15.0)
      assert offset != 0
      assert peak <= 10.0     # peaks should not stack
  ```
- [ ] **Step 2 — run** → FAIL.
- [ ] **Step 3 — implement** matching the paper's Algorithm 1:
  ```python
  def find_optimal_offset(E_m: np.ndarray, E_new: np.ndarray,
                          D_max: int, BW_max: float
                          ) -> tuple[int, float]:
      H = E_m.shape[0]
      best_offset, best_peak = -1, math.inf
      for dt in range(0, D_max + 1):
          shifted = np.zeros(H, dtype=np.float32)
          end = min(H, dt + E_new.shape[0])
          shifted[dt:end] = E_new[: end - dt]
          combined = E_m + shifted
          peak = float(combined.max())
          if peak <= BW_max and peak < best_peak:
              best_peak, best_offset = peak, dt
      if best_offset == -1:                # no feasible offset under BW_max
          # fall back: pick dt minimising peak, scheduler decides whether to reject
          best_offset = int(np.argmin([
              float((E_m + _shift(E_new, dt, H)).max()) for dt in range(D_max + 1)
          ]))
          best_peak = float((E_m + _shift(E_new, best_offset, H)).max())
      return best_offset, best_peak
  ```
- [ ] **Step 4 — additional tests:** (a) all-zero `E_m` returns offset 0; (b) when no offset stays under `BW_max`, returns the min-peak offset and a peak > `BW_max` so the scheduler can reject.
- [ ] **Step 5 — commit:** `feat(schedule): phase-shift kernel for STPS Stage 2`.

---

## Task 6 — Card forecast traffic state

**Files:**
- Modify: [util/card.py](util/card.py)
- Create: [tests/test_card_forecast.py](tests/test_card_forecast.py)

- [ ] **Step 1 — failing test:** `card.add_forecast(E_new, offset=3)` shifts and accumulates.
- [ ] **Step 2 — implement** on `Card`:
  ```python
  def ensure_forecast(self, horizon: int) -> None:
      if not hasattr(self, "_forecast") or self._forecast.shape[0] != horizon:
          self._forecast = np.zeros(horizon, dtype=np.float32)

  def add_forecast(self, E: np.ndarray, offset: int) -> None:
      H = self._forecast.shape[0]
      end = min(H, offset + E.shape[0])
      self._forecast[offset:end] += E[: end - offset]

  def peak_forecast(self) -> float:
      return float(self._forecast.max(initial=0.0))

  def advance_forecast(self) -> None:
      """Shift left by 1 each simulation step; new tail is zero."""
      self._forecast[:-1] = self._forecast[1:]; self._forecast[-1] = 0.0
  ```
- [ ] **Step 3 — run** → PASS.
- [ ] **Step 4 — commit:** `feat(util): card forecast-traffic timeline for STPS`.

---

## Task 7 — `STPSScheduler` (Stage 1 + Stage 2 + Stage 3)

**Files:**
- Create: [schedule/stps.py](schedule/stps.py)
- Create: [schedule/hotspot_split.py](schedule/hotspot_split.py)
- Create: [tests/test_stps_scheduler.py](tests/test_stps_scheduler.py)
- Modify: [schedule/__init__.py](schedule/__init__.py)

- [ ] **Step 1 — failing test:** with two cards and two synthetic anti-phase fingerprinted tasks, STPS places them on the same card with a non-zero `start_offset` because their interleaved peak fits, while BestFit would pick different cards.
  ```python
  def test_stps_phase_shifts_two_bursty_tasks():
      cards = [Card(0), Card(1)]
      sched = STPSScheduler(cards, horizon=32, d_max=16, bw_max=12.0)
      task_a = make_task_with_fingerprint(synth_pulse_at(t=2))
      task_b = make_task_with_fingerprint(synth_pulse_at(t=2))
      sched.on_task_arrival(task_a, time_step=0)
      sched.on_task_arrival(task_b, time_step=0)
      assert task_a.host_card_id == task_b.host_card_id   # interleaved on same card
      assert task_b.start_offset > 0
  ```
- [ ] **Step 2 — run** → FAIL.
- [ ] **Step 3 — implement** `STPSScheduler.on_task_arrival`:
  ```python
  def on_task_arrival(self, task, time_step):
      fp = task.fingerprint                       # required; engine guarantees it
      # ---- Stage 1: Macro-Card Dispatching ----
      candidates = [c for c in self.cards if c.can_host(task)]
      if not candidates:
          self._reject(task); return
      candidates = self._stage1_filter(candidates, fp)   # K̄ + β_card rules
      # ---- Stage 2: Micro-Temporal Phase-Shifting ----
      best = None  # (card, offset, peak)
      for card in candidates:
          card.ensure_forecast(self.horizon)
          dt, peak = find_optimal_offset(card._forecast, fp.E,
                                          self.d_max, self.bw_max)
          if best is None or peak < best[2]:
              best = (card, dt, peak)
      card, offset, peak = best
      if peak > self.bw_max:
          self._reject(task); return
      # ---- Stage 3: Micro-Spatial Mapping ----
      task.split_plan = split_population(fp.centrality_last,
                                          self.centrality_split_threshold)
      # Commit
      card.put(task)
      card.add_forecast(fp.E, offset)
      task.start_offset = offset
      self._update_card_burstiness(card, fp)
  ```
  - `_stage1_filter` keeps cards whose fragmentation matches `K̄` (cohesive task → card with largest contiguous free cores; decoupled task → most-fragmented card) and whose running `β_card` is lowest among the top-fragmentation-matched subset when `fp.beta` is high. Use simple weighted score:
    ```
    score = w_frag * frag_match(card, K̄) + w_beta * (β_card if fp.beta > 1.5 else 0)
    ```
    Lower score wins. `frag_match` = absolute difference between card's largest-free-block ratio and `1/K̄`.
- [ ] **Step 4 — implement** `STPSScheduler.step(time_step)`: each step calls `card.advance_forecast()` to roll the timeline window forward, decays `β_card` with EMA, and updates `task.tick_index` only if `time_step >= task.placement_step + task.start_offset`.
- [ ] **Step 5 — implement** `schedule/hotspot_split.py`:
  ```python
  def split_population(c_last: np.ndarray, threshold: float) -> list[int]:
      """Return indices of populations that should be split across PIM cores."""
      return [int(i) for i, v in enumerate(c_last) if v >= threshold]
  ```
  v1 only records the indices on the task; physical placement remains as today.
- [ ] **Step 6 — implement** `STPSSpatialScheduler` (no phase-shifting: always offset 0) and `STPSTemporalScheduler` (Stage 1 ignores `K̄`; only `β_card` matters; no hotspot split). Each is a thin subclass.
- [ ] **Step 7 — register** in [schedule/__init__.py](schedule/__init__.py):
  ```python
  from . import stps  # noqa: F401
  ```
  And inside `schedule/stps.py`:
  ```python
  register_scheduler("stps", STPSScheduler)
  register_scheduler("stps-spatial", STPSSpatialScheduler)
  register_scheduler("stps-temporal", STPSTemporalScheduler)
  ```
- [ ] **Step 8 — run** all STPS tests → PASS.
- [ ] **Step 9 — commit:** `feat(schedule): STPS 3-stage scheduler with ablation variants`.

---

## Task 8 — Engine integration

**Files:**
- Modify: [util/task.py](util/task.py)
- Modify: [simulation/engine.py](simulation/engine.py)
- Modify: [main.py](main.py)

- [ ] **Step 1 — extend `Task`** with optional fields:
  ```python
  fingerprint_path: str | None = None
  fingerprint: "Fingerprint | None" = None
  start_offset: int = 0
  ```
  Tasks lacking a fingerprint behave exactly as today.
- [ ] **Step 2 — engine wiring:**
  - When `--fingerprint-dir` is given, the engine assigns each task a fingerprint chosen by `task.task_id % len(fingerprints_in_dir)` (deterministic given seed). For STPS schedulers without a fingerprint dir, fall back to `make_synthetic_fingerprint(beta_target=task.complexity_ratio, ...)`.
  - When iterating ticks, gate `task.simulate_tick()` by `time_step >= task.placement_step + task.start_offset` so a delayed task stays idle during its phase-shift window.
  - **Important:** changes are conditional on the scheduler being `stps*`. All existing schedulers run identical code paths, verified by re-running `make compare`.
- [ ] **Step 3 — CLI flags** in [main.py](main.py):
  ```
  --fingerprint-dir PATH   Directory of *.npz fingerprints (STPS only)
  --bw-max FLOAT           NoC bandwidth ceiling per card (default: 1e9 = effectively off)
  --d-max INT              Max phase-shift delay in ticks (default: 16)
  --horizon INT            Forecast horizon in ticks (default: 64)
  --centrality-split-threshold FLOAT  (default: 0.2)
  ```
  Defaults must keep existing scheduler runs identical.
- [ ] **Step 4 — smoke test:**
  ```bash
  make fingerprints
  python main.py --scheduler stps --cards 4 --tasks 64 --steps 128 \
      --arrival-mode bursty --fingerprint-dir npz --bw-max 5e6
  ```
- [ ] **Step 5 — commit:** `feat(engine): wire fingerprints + start_offset into simulation`.

---

## Task 9 — Makefile + docs

**Files:**
- Modify: [Makefile](Makefile)
- Create: [docs/stps.md](docs/stps.md)

- [ ] **Step 1 — Makefile targets:**
  ```make
  stps:
      python main.py --scheduler stps $(COMMON_ARGS) --fingerprint-dir npz --bw-max $(BW_MAX) --d-max $(D_MAX) --horizon $(HORIZON)
  stps-spatial: ...
  stps-temporal: ...
  compare-stps: bestfit drf p2c rr stps stps-spatial stps-temporal
  ```
  Add `BW_MAX ?= 5e6`, `D_MAX ?= 16`, `HORIZON ?= 64` near the top of the Makefile.
- [ ] **Step 2 — `docs/stps.md`:** ≤ 1 page. Cross-link to `article.tex` §4.2/§4.3, list fingerprint schema, list CLI flags, link to test files.
- [ ] **Step 3 — final smoke run:** `make compare-stps CARDS=4 TASKS=128 STEPS=128 ARRIVAL_MODE=bursty` produces `data/stps_loads_*.csv` alongside the baselines and the existing plotting scripts pick it up.
- [ ] **Step 4 — commit:** `docs(stps): make targets and design doc`.

---

## Out-of-Scope (explicitly NOT in this plan)

- Real SpikingJelly model checkpoints / dataset wiring beyond a working CLI demo. Synthetic fingerprints suffice for v1.
- Replacing `glass.py` or any existing baseline.
- Re-running the full evaluation table from §5 of the paper (Q1/Q2/Q3 plots) — that is a separate experiment-and-plotting task that will reuse the CSVs produced here.
- Hardware-accurate NoC simulation. We model `BW_max` as a scalar ceiling on the per-card forecast traffic, matching the paper's abstraction.

---

**End of plan. Awaiting human review before any code is written.**
