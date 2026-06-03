"""Metrics collection and calculation for simulation evaluation."""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:
    from util.card import Card

logger = logging.getLogger(__name__)


@dataclass
class TaskDelay:
    """Record of a single task's timing information."""
    task_id: int
    arrival_step: int
    placement_step: int
    completion_step: int
    host_card_id: int = -1
    # STPS Stage-B phase-shift delay before the task emits its first spike.
    # 0 for non-STPS schedulers; ∈ [0, d_max] for STPS-family.
    # Defined as "cold start" per the paper: ticks where the task occupies a
    # card but is not yet computing.
    cold_start_ticks: int = 0

    @property
    def total_delay(self) -> int:
        """Total time from arrival to completion (includes cold-start)."""
        if self.completion_step < 0:
            return -1
        return self.completion_step - self.arrival_step

    @property
    def effective_delay(self) -> int:
        """Delay excluding the STPS cold-start (Stage-B phase-shift) ticks.

        For non-STPS schedulers cold_start_ticks == 0 so this equals
        total_delay. For STPS, the actual spike-emission start is
        placement_step + cold_start_ticks; the cold-start window is treated
        as setup time and not charged against task latency.
        """
        if self.completion_step < 0:
            return -1
        eff = self.completion_step - self.arrival_step - int(self.cold_start_ticks)
        return eff if eff >= 0 else 0


@dataclass
class LoadSnapshot:
    """Snapshot of card loads at a single time step."""
    time_step: int
    card_loads: Dict[int, float]  # card_id -> load (served traffic)
    card_task_counts: Dict[int, int]  # card_id -> task count
    # docs/traffic_optim.md §A.3 — bandwidth-contention bookkeeping.
    card_demand: Dict[int, float] = field(default_factory=dict)
    card_served: Dict[int, float] = field(default_factory=dict)
    card_backlog: Dict[int, float] = field(default_factory=dict)
    card_congestion_ratio: Dict[int, float] = field(default_factory=dict)
    card_utilization: Dict[int, float] = field(default_factory=dict)
    
    @property
    def mean_load(self) -> float:
        """Average load across all cards."""
        if not self.card_loads:
            return 0.0
        return sum(self.card_loads.values()) / len(self.card_loads)
    
    @property
    def load_variance(self) -> float:
        """Variance of loads across cards (measure of imbalance)."""
        if len(self.card_loads) < 2:
            return 0.0
        mean = self.mean_load
        return sum((l - mean) ** 2 for l in self.card_loads.values()) / len(self.card_loads)
    
    @property
    def cv(self) -> float:
        """Coefficient of variation of card loads (std / mean). 0 if mean is 0."""
        if len(self.card_loads) < 2:
            return 0.0
        mean = self.mean_load
        if mean <= 0:
            return 0.0
        var = self.load_variance
        return float(np.sqrt(var) / mean)

    @property
    def jfi(self) -> float:
        """Jain's Fairness Index across card loads. Range (0, 1], higher is fairer."""
        loads = list(self.card_loads.values())
        n = len(loads)
        if n == 0:
            return 0.0
        s = sum(loads)
        sq = sum(l * l for l in loads)
        if sq <= 0:
            return 1.0
        return float((s * s) / (n * sq))

    @property
    def lif(self) -> float:
        """Load Imbalance Factor: max load / mean load. 0 if mean is 0."""
        if not self.card_loads:
            return 0.0
        mean = self.mean_load
        if mean <= 0:
            return 0.0
        return float(max(self.card_loads.values()) / mean)

    @property
    def max_min_ratio(self) -> float:
        """Max load over min positive load. Returns 0 if no positive loads."""
        positives = [l for l in self.card_loads.values() if l > 0]
        if not positives:
            return 0.0
        return float(max(positives) / min(positives))


@dataclass
class SimulationMetrics:
    """
    Comprehensive metrics collected during simulation.

    Tracks:
    - Load snapshots over time
    - Task statistics (completed, pending)
    - Latency metrics
    """
    scheduler_name: str
    arrival_mode: str
    card_count: int
    task_count: int
    steps: int
    seed: Optional[int]

    # Time series data
    load_snapshots: List[LoadSnapshot] = field(default_factory=list)

    # Task statistics
    tasks_completed: int = 0
    tasks_pending_at_end: int = 0

    # Task delay tracking
    task_delays: List[TaskDelay] = field(default_factory=list)
    start_offsets: List[int] = field(default_factory=list)
    bw_rejections: int = 0

    # docs/traffic_optim.md §A.3 — per-task NoC waiting accounting.
    congestion_wait_ticks: List[int] = field(default_factory=list)
    congestion_timeouts: int = 0
    bw_cap_value: Optional[float] = None

    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    def record_task_delay(self, task_id: int, arrival_step: int,
                          placement_step: int, completion_step: int,
                          host_card_id: int = -1,
                          cold_start_ticks: int = 0) -> None:
        """Record timing information for a completed task.

        `cold_start_ticks` carries the STPS Stage-B phase-shift offset so the
        downstream metrics can compute cold-start-excluded latency and
        throughput.
        """
        delay = TaskDelay(
            task_id=task_id,
            arrival_step=arrival_step,
            placement_step=placement_step,
            completion_step=completion_step,
            host_card_id=host_card_id,
            cold_start_ticks=int(cold_start_ticks),
        )
        self.task_delays.append(delay)

    def record_start_offset(self, start_offset: int) -> None:
        self.start_offsets.append(int(start_offset))

    def record_rejection(self, reason: str) -> None:
        if reason == "bw_max_exceeded":
            self.bw_rejections += 1

    def record_load_snapshot(
        self,
        time_step: int,
        cards: List["Card"],
        epoch_loads: Dict[int, float],
        epoch_demand: Optional[Dict[int, float]] = None,
        epoch_backlog: Optional[Dict[int, float]] = None,
    ) -> LoadSnapshot:
        """Record card loads from the scheduler's epoch-accumulated traffic."""
        card_loads = {c.card_id: epoch_loads.get(c.card_id, 0.0) for c in cards}
        demand = {c.card_id: (epoch_demand or {}).get(c.card_id, card_loads[c.card_id]) for c in cards}
        served = dict(card_loads)
        backlog = {c.card_id: (epoch_backlog or {}).get(c.card_id, 0.0) for c in cards}
        cong = {}
        util = {}
        for c in cards:
            d = demand[c.card_id]
            s = served[c.card_id]
            cong[c.card_id] = ((d - s) / d) if d > 0 else 0.0
            if c.bw_cap is not None and c.bw_cap > 0:
                util[c.card_id] = s / float(c.bw_cap)
            else:
                util[c.card_id] = 0.0
        snapshot = LoadSnapshot(
            time_step=time_step,
            card_loads=card_loads,
            card_task_counts={c.card_id: len(c.tasks) for c in cards},
            card_demand=demand,
            card_served=served,
            card_backlog=backlog,
            card_congestion_ratio=cong,
            card_utilization=util,
        )
        self.load_snapshots.append(snapshot)
        return snapshot
    
    @property
    def avg_load_imbalance(self) -> float:
        """Average load variance across all time steps."""
        if not self.load_snapshots:
            return 0.0
        return sum(s.load_variance for s in self.load_snapshots) / len(self.load_snapshots)
    
    @property
    def max_load_imbalance(self) -> float:
        """Maximum load variance observed."""
        if not self.load_snapshots:
            return 0.0
        return max(s.load_variance for s in self.load_snapshots)

    def _steady_window(self) -> List[LoadSnapshot]:
        """Trim 64-tick warmup/teardown when enough snapshots are present."""
        n = len(self.load_snapshots)
        if n <= 128:
            return list(self.load_snapshots)
        return self.load_snapshots[64 : n - 64]

    def _card_cv_array(self) -> np.ndarray:
        """Get CV time series (one CV value per steady-window snapshot)."""
        snaps = self._steady_window()
        if not snaps:
            return np.empty(0, dtype=np.float64)
        return np.asarray([s.cv for s in snaps], dtype=np.float64)

    @property
    def avg_card_cv(self) -> float:
        arr = self._card_cv_array()
        if arr.size == 0:
            return 0.0
        return float(np.mean(arr))

    @property
    def median_card_cv(self) -> float:
        """Median of CV across steady-window snapshots."""
        arr = self._card_cv_array()
        if arr.size == 0:
            return 0.0
        return float(np.median(arr))

    @property
    def p95_card_cv(self) -> float:
        """95th percentile of CV across steady-window snapshots."""
        arr = self._card_cv_array()
        if arr.size == 0:
            return 0.0
        return float(np.percentile(arr, 95))

    @property
    def std_card_cv(self) -> float:
        """Standard deviation of CV across steady-window snapshots."""
        arr = self._card_cv_array()
        if arr.size == 0:
            return 0.0
        return float(np.std(arr))

    @property
    def avg_card_jfi(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        return float(np.mean([s.jfi for s in snaps]))

    @property
    def avg_card_lif(self) -> float:
        snaps = self._steady_window()
        vals = [s.lif for s in snaps if s.lif > 0]
        if not vals:
            return 0.0
        return float(np.mean(vals))

    @property
    def avg_max_min_ratio(self) -> float:
        snaps = self._steady_window()
        ratios = [s.max_min_ratio for s in snaps if s.max_min_ratio > 0]
        if not ratios:
            return 0.0
        return float(np.mean(ratios))

    # ------------------------------------------------------------------
    # docs/metrics.md §1b — time-domain per-card balance (each card's
    # dispersion along the T axis). N cards → N values per metric;
    # report mean / max / min / std summary stats.
    # ------------------------------------------------------------------

    def _per_card_load_series(self, kind: str = "served") -> Dict[int, np.ndarray]:
        """Per-card time series across the steady window.

        kind:
            "served"  — actual NoC-emitted traffic (post offset, cap clipping,
                        and pending-queue smoothing). Equivalent to card_loads.
            "demand"  — pre-cap demand (what tasks asked for this tick before
                        the NoC bandwidth ceiling clipped it). Reflects raw
                        Stage-2 offset placement without queue smoothing.
        """
        snaps = self._steady_window()
        if not snaps:
            return {}
        series: Dict[int, List[float]] = {}
        for s in snaps:
            src = s.card_demand if kind == "demand" and s.card_demand else s.card_loads
            for cid, v in src.items():
                series.setdefault(cid, []).append(float(v))
        return {cid: np.asarray(vs, dtype=np.float64) for cid, vs in series.items()}

    def _time_card_cv_per_card(self, kind: str = "served") -> Dict[int, float]:
        out = {}
        for cid, arr in self._per_card_load_series(kind).items():
            m = float(arr.mean()) if arr.size else 0.0
            out[cid] = float(arr.std() / m) if m > 0 else 0.0
        return out

    def _time_card_jfi_per_card(self, kind: str = "served") -> Dict[int, float]:
        out = {}
        for cid, arr in self._per_card_load_series(kind).items():
            n = arr.size
            s = float(arr.sum())
            sq = float((arr * arr).sum())
            out[cid] = float((s * s) / (n * sq)) if (n > 0 and sq > 0) else 0.0
        return out

    def _time_card_lif_per_card(self, kind: str = "served") -> Dict[int, float]:
        out = {}
        for cid, arr in self._per_card_load_series(kind).items():
            m = float(arr.mean()) if arr.size else 0.0
            out[cid] = float(arr.max() / m) if m > 0 else 0.0
        return out

    def _time_card_max_min_ratio_per_card(self, kind: str = "served") -> Dict[int, float]:
        out = {}
        for cid, arr in self._per_card_load_series(kind).items():
            pos = arr[arr > 0]
            out[cid] = float(pos.max() / pos.min()) if pos.size > 0 else 0.0
        return out

    def _time_card_load_variance_per_card(self, kind: str = "served") -> Dict[int, float]:
        out = {}
        for cid, arr in self._per_card_load_series(kind).items():
            out[cid] = float(arr.var()) if arr.size else 0.0
        return out

    @staticmethod
    def _summary_stats(vals: List[float]) -> Dict[str, float]:
        if not vals:
            return {"mean": 0.0, "max": 0.0, "min": 0.0, "std": 0.0, "median": 0.0}
        arr = np.asarray(vals, dtype=np.float64)
        return {
            "mean": float(arr.mean()),
            "max": float(arr.max()),
            "min": float(arr.min()),
            "std": float(arr.std()),
            "median": float(np.median(arr)),
        }

    @property
    def time_card_cv_mean(self) -> float:
        return self._summary_stats(list(self._time_card_cv_per_card().values()))["mean"]

    @property
    def time_card_cv_max(self) -> float:
        return self._summary_stats(list(self._time_card_cv_per_card().values()))["max"]

    @property
    def time_card_jfi_mean(self) -> float:
        return self._summary_stats(list(self._time_card_jfi_per_card().values()))["mean"]

    @property
    def time_card_lif_mean(self) -> float:
        return self._summary_stats(list(self._time_card_lif_per_card().values()))["mean"]

    @property
    def time_card_lif_max(self) -> float:
        return self._summary_stats(list(self._time_card_lif_per_card().values()))["max"]

    @property
    def time_card_max_min_ratio_mean(self) -> float:
        return self._summary_stats(list(self._time_card_max_min_ratio_per_card().values()))["mean"]

    @property
    def time_card_load_variance_mean(self) -> float:
        return self._summary_stats(list(self._time_card_load_variance_per_card().values()))["mean"]

    # ------------------------------------------------------------------
    # Demand vs served split — disambiguates "Stage-2 offset succeeded" from
    # "served curve was smoothed by cap clipping / pending queue". `*_served_*`
    # mirrors the legacy `time_card_*` properties; `*_demand_*` uses
    # card_demand (pre-cap, pre-queue) so peaks reflect raw offset placement.
    # ------------------------------------------------------------------

    @property
    def time_card_lif_served_mean(self) -> float:
        return self._summary_stats(list(self._time_card_lif_per_card("served").values()))["mean"]

    @property
    def time_card_lif_served_max(self) -> float:
        return self._summary_stats(list(self._time_card_lif_per_card("served").values()))["max"]

    @property
    def time_card_lif_demand_mean(self) -> float:
        return self._summary_stats(list(self._time_card_lif_per_card("demand").values()))["mean"]

    @property
    def time_card_lif_demand_max(self) -> float:
        return self._summary_stats(list(self._time_card_lif_per_card("demand").values()))["max"]

    @property
    def time_card_cv_demand_mean(self) -> float:
        return self._summary_stats(list(self._time_card_cv_per_card("demand").values()))["mean"]

    @property
    def time_card_cv_demand_max(self) -> float:
        return self._summary_stats(list(self._time_card_cv_per_card("demand").values()))["max"]

    # ------------------------------------------------------------------
    # docs/traffic_optim.md §A.3 — bandwidth-contention aggregates
    # ------------------------------------------------------------------

    @property
    def avg_congestion_ratio(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        vals: List[float] = []
        for s in snaps:
            for v in s.card_congestion_ratio.values():
                vals.append(float(v))
        return float(np.mean(vals)) if vals else 0.0

    @property
    def peak_backlog(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        return float(max((max(s.card_backlog.values(), default=0.0) for s in snaps), default=0.0))

    @property
    def congested_card_tick_frac(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        total = 0
        congested = 0
        for s in snaps:
            for v in s.card_congestion_ratio.values():
                total += 1
                if v > 1e-9:
                    congested += 1
        return float(congested) / float(total) if total else 0.0

    @property
    def avg_utilization(self) -> float:
        snaps = self._steady_window()
        if not snaps or self.bw_cap_value is None:
            return 0.0
        vals: List[float] = []
        for s in snaps:
            for v in s.card_utilization.values():
                vals.append(float(v))
        return float(np.mean(vals)) if vals else 0.0

    @property
    def avg_demand_cv(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        out = []
        for s in snaps:
            demands = list(s.card_demand.values()) if s.card_demand else list(s.card_loads.values())
            if len(demands) < 2:
                continue
            m = float(np.mean(demands))
            if m <= 0:
                continue
            out.append(float(np.std(demands) / m))
        return float(np.mean(out)) if out else 0.0

    @property
    def avg_backlog_cv(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        out = []
        for s in snaps:
            vals = list(s.card_backlog.values())
            if len(vals) < 2:
                continue
            m = float(np.mean(vals))
            if m <= 0:
                continue
            out.append(float(np.std(vals) / m))
        return float(np.mean(out)) if out else 0.0

    @property
    def mean_congestion_wait_ticks(self) -> float:
        if not self.congestion_wait_ticks:
            return 0.0
        return float(np.mean(self.congestion_wait_ticks))

    @property
    def p95_congestion_wait_ticks(self) -> float:
        if not self.congestion_wait_ticks:
            return 0.0
        return float(np.percentile(self.congestion_wait_ticks, 95))
    
    @property
    def throughput(self) -> float:
        """Tasks completed per time step."""
        total_steps = len(self.load_snapshots)
        if total_steps == 0:
            return 0.0
        return self.tasks_completed / total_steps

    @property
    def mean_cold_start(self) -> float:
        """Mean Stage-B phase-shift offset across completed tasks.

        Equals 0.0 for non-STPS schedulers. Pulled from the per-task
        ``TaskDelay.cold_start_ticks`` so it reflects only successfully
        completed tasks (`record_start_offset` includes every placed task,
        completed or not).
        """
        if not self.task_delays:
            return 0.0
        vals = [d.cold_start_ticks for d in self.task_delays
                if d.completion_step >= 0]
        if not vals:
            return 0.0
        return float(np.mean(vals))

    @property
    def throughput_excl_cold(self) -> float:
        """Throughput with the cold-start window discounted from wall time.

        For STPS the per-task spike-emission start is
        ``placement + cold_start_ticks``; the simulator wall clock therefore
        spends ``mean_cold_start`` ticks per task on Stage-B setup that does
        not contribute to useful compute. We deduct that mean from the run
        length:

            throughput_excl_cold = tasks_completed / (total_steps - mean_cold_start)

        For non-STPS schedulers ``mean_cold_start == 0`` and this equals the
        original ``throughput``.
        """
        total_steps = len(self.load_snapshots)
        if total_steps == 0:
            return 0.0
        denom = float(total_steps) - float(self.mean_cold_start)
        if denom <= 0.0:
            return 0.0
        return float(self.tasks_completed) / denom
    
    @property
    def completion_rate(self) -> float:
        """Fraction of tasks completed (vs total submitted)."""
        if self.task_count == 0:
            return 0.0
        return self.tasks_completed / self.task_count

    @property
    def p99_delay(self) -> float:
        """99th percentile of task completion delays (arrival to completion)."""
        if not self.task_delays:
            return 0.0
        delays = [d.total_delay for d in self.task_delays if d.total_delay >= 0]
        if not delays:
            return 0.0
        return float(np.percentile(delays, 99))
    
    @property
    def p95_delay(self) -> float:
        """95th percentile of task completion delays."""
        if not self.task_delays:
            return 0.0
        delays = [d.total_delay for d in self.task_delays if d.total_delay >= 0]
        if not delays:
            return 0.0
        return float(np.percentile(delays, 95))
    
    @property
    def p50_delay(self) -> float:
        """Median task completion delay."""
        if not self.task_delays:
            return 0.0
        delays = [d.total_delay for d in self.task_delays if d.total_delay >= 0]
        if not delays:
            return 0.0
        return float(np.percentile(delays, 50))
    
    @property
    def avg_delay(self) -> float:
        """Average task completion delay."""
        if not self.task_delays:
            return 0.0
        delays = [d.total_delay for d in self.task_delays if d.total_delay >= 0]
        if not delays:
            return 0.0
        return float(np.mean(delays))
    
    @property
    def max_delay(self) -> float:
        """Maximum task completion delay."""
        if not self.task_delays:
            return 0.0
        delays = [d.total_delay for d in self.task_delays if d.total_delay >= 0]
        if not delays:
            return 0.0
        return float(max(delays))

    # ----- Cold-start-excluded delay variants ---------------------------
    # Use TaskDelay.effective_delay = total_delay - cold_start_ticks so the
    # STPS Stage-B phase-shift window is treated as setup, not latency.
    # For non-STPS schedulers cold_start_ticks == 0 and these match the
    # original *_delay properties exactly.

    def _effective_delay_array(self) -> np.ndarray:
        if not self.task_delays:
            return np.empty(0, dtype=np.int64)
        vals = [d.effective_delay for d in self.task_delays
                if d.completion_step >= 0]
        if not vals:
            return np.empty(0, dtype=np.int64)
        return np.asarray(vals, dtype=np.int64)

    @property
    def p99_delay_excl_cold(self) -> float:
        arr = self._effective_delay_array()
        if arr.size == 0:
            return 0.0
        return float(np.percentile(arr, 99))

    @property
    def avg_delay_excl_cold(self) -> float:
        arr = self._effective_delay_array()
        if arr.size == 0:
            return 0.0
        return float(arr.mean())

    @property
    def max_delay_excl_cold(self) -> float:
        arr = self._effective_delay_array()
        if arr.size == 0:
            return 0.0
        return float(arr.max())
    

    @property
    def mean_start_offset(self) -> float:
        if not self.start_offsets:
            return 0.0
        return float(np.mean(self.start_offsets))

    @property
    def p95_start_offset(self) -> float:
        if not self.start_offsets:
            return 0.0
        return float(np.percentile(self.start_offsets, 95))

    @property
    def reject_rate_bw(self) -> float:
        if self.task_count == 0:
            return 0.0
        return float(self.bw_rejections / self.task_count)

    # ------------------------------------------------------------------
    # Per-card breakdown (Q2 phase-shift ablation): one row per card with
    # time-axis dispersion, mean congestion ratio, and per-card throughput.
    # ------------------------------------------------------------------

    def per_card_breakdown(self, kind: str = "served") -> List[Dict[str, float]]:
        snaps = self._steady_window()
        if not snaps:
            return []
        steady_steps = len(snaps)
        # Collect series.
        series: Dict[int, List[float]] = {}
        cong: Dict[int, List[float]] = {}
        for s in snaps:
            src = s.card_demand if kind == "demand" and s.card_demand else s.card_loads
            for cid, v in src.items():
                series.setdefault(cid, []).append(float(v))
            for cid, c in s.card_congestion_ratio.items():
                cong.setdefault(cid, []).append(float(c))
        # Per-card throughput from completed task delays. Tasks whose
        # host_card_id was never set (-1) are skipped.
        completed_by_card: Dict[int, int] = {}
        for d in self.task_delays:
            if d.host_card_id < 0 or d.completion_step < 0:
                continue
            completed_by_card[d.host_card_id] = completed_by_card.get(d.host_card_id, 0) + 1
        rows: List[Dict[str, float]] = []
        for cid in sorted(series.keys()):
            arr = np.asarray(series[cid], dtype=np.float64)
            m = float(arr.mean()) if arr.size else 0.0
            std = float(arr.std()) if arr.size else 0.0
            mx = float(arr.max()) if arr.size else 0.0
            cv = float(std / m) if m > 0 else 0.0
            lif = float(mx / m) if m > 0 else 0.0
            n = arr.size
            s_sum = float(arr.sum())
            sq = float((arr * arr).sum())
            jfi = float((s_sum * s_sum) / (n * sq)) if (n > 0 and sq > 0) else 0.0
            cong_arr = cong.get(cid, [])
            mean_cong = float(np.mean(cong_arr)) if cong_arr else 0.0
            max_cong = float(np.max(cong_arr)) if cong_arr else 0.0
            tp = float(completed_by_card.get(cid, 0)) / float(steady_steps) \
                if steady_steps > 0 else 0.0
            rows.append({
                "card_id": cid,
                "kind": kind,
                "mean_load": m,
                "max_load": mx,
                "std_load": std,
                "time_cv": cv,
                "time_lif": lif,
                "time_jfi": jfi,
                "mean_cong_ratio": mean_cong,
                "max_cong_ratio": max_cong,
                "completed_tasks": completed_by_card.get(cid, 0),
                "throughput": tp,
            })
        return rows

    def to_summary_dict(self) -> dict:
        """Convert metrics to a summary dictionary."""
        result = {
            "scheduler": self.scheduler_name,
            "arrival_mode": self.arrival_mode,
            "cards": self.card_count,
            "tasks": self.task_count,
            "steps": self.steps,
            "seed": self.seed,
            "tasks_completed": self.tasks_completed,
            "tasks_pending": self.tasks_pending_at_end,
            "completion_rate": round(self.completion_rate, 4),
            "throughput": round(self.throughput, 4),
            "throughput_excl_cold": round(self.throughput_excl_cold, 4),
            "mean_cold_start": round(self.mean_cold_start, 4),
            "avg_load_imbalance": round(self.avg_load_imbalance, 2),
            "max_load_imbalance": round(self.max_load_imbalance, 2),
            "card_cv": round(self.avg_card_cv, 4),
            "median_card_cv": round(self.median_card_cv, 4),
            "p95_card_cv": round(self.p95_card_cv, 4),
            "std_card_cv": round(self.std_card_cv, 4),
            "card_jfi": round(self.avg_card_jfi, 4),
            "card_lif": round(self.avg_card_lif, 4),
            "max_min_ratio": round(self.avg_max_min_ratio, 4),
            "p99_delay": round(self.p99_delay, 2),
            "p95_delay": round(self.p95_delay, 2),
            "p50_delay": round(self.p50_delay, 2),
            "avg_delay": round(self.avg_delay, 2),
            "max_delay": round(self.max_delay, 2),
            "p99_delay_excl_cold": round(self.p99_delay_excl_cold, 2),
            "avg_delay_excl_cold": round(self.avg_delay_excl_cold, 2),
            "max_delay_excl_cold": round(self.max_delay_excl_cold, 2),
            "mean_start_offset": round(self.mean_start_offset, 4),
            "p95_start_offset": round(self.p95_start_offset, 4),
            "reject_rate_bw": round(self.reject_rate_bw, 4),
            # docs/traffic_optim.md §A.3
            "bw_cap": (self.bw_cap_value if self.bw_cap_value is not None else ""),
            "avg_congestion_ratio": round(self.avg_congestion_ratio, 4),
            "peak_backlog": round(self.peak_backlog, 4),
            "congested_card_tick_frac": round(self.congested_card_tick_frac, 4),
            "avg_utilization": round(self.avg_utilization, 4),
            "avg_demand_cv": round(self.avg_demand_cv, 4),
            "avg_backlog_cv": round(self.avg_backlog_cv, 4),
            "mean_congestion_wait_ticks": round(self.mean_congestion_wait_ticks, 4),
            "p95_congestion_wait_ticks": round(self.p95_congestion_wait_ticks, 4),
            "congestion_timeouts": self.congestion_timeouts,
        }

        return result

    def cv_cdf_data(self) -> tuple:
        """Return sorted CV values and their cumulative probabilities for CDF.
        
        Returns:
            (cv_values: np.ndarray, cdf: np.ndarray) where cdf[i] = P(CV <= cv_values[i])
        """
        arr = self._card_cv_array()
        if arr.size == 0:
            return np.empty(0), np.empty(0)
        sorted_cv = np.sort(arr)
        cdf = np.arange(1, len(sorted_cv) + 1) / len(sorted_cv)
        return sorted_cv, cdf


class MetricsWriter:
    """Writes simulation metrics to files."""
    
    def __init__(self, data_dir: str = "data"):
        """
        Initialize metrics writer.
        
        Args:
            data_dir: Directory for output files
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._csv_path: Optional[Path] = None
        self._csv_file = None
        self._csv_writer = None
    
    def start_csv(self, scheduler_name: str, suffix: str = "", output_prefix: Optional[str] = None) -> Path:
        """
        Start a new CSV file for load traces.
        
        Args:
            scheduler_name: Name of scheduler (for filename)
            suffix: Optional suffix to append (e.g., arrival mode)
            
        Returns:
            Path to the CSV file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix_part = f"_{suffix}" if suffix else ""
        if output_prefix and str(output_prefix).strip():
            # Use provided prefix for filename (user-controlled)
            filename = f"{str(output_prefix)}_loads_{timestamp}.csv"
        else:
            filename = f"{scheduler_name.lower()}{suffix_part}_loads_{timestamp}.csv"
        self._csv_path = self.data_dir / filename
        self._csv_file = open(self._csv_path, "w", newline="")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow([
            "time_step", "card_id", "load", "tasks",
            "demand", "served", "backlog", "congestion_ratio", "utilization",
        ])
        return self._csv_path

    def write_snapshot(self, snapshot: LoadSnapshot) -> None:
        if self._csv_writer is None:
            raise RuntimeError("CSV not started. Call start_csv() first.")

        for card_id in sorted(snapshot.card_loads.keys()):
            self._csv_writer.writerow([
                snapshot.time_step,
                card_id,
                snapshot.card_loads[card_id],
                snapshot.card_task_counts[card_id],
                snapshot.card_demand.get(card_id, snapshot.card_loads[card_id]),
                snapshot.card_served.get(card_id, snapshot.card_loads[card_id]),
                snapshot.card_backlog.get(card_id, 0.0),
                snapshot.card_congestion_ratio.get(card_id, 0.0),
                snapshot.card_utilization.get(card_id, 0.0),
            ])
    
    def close(self) -> Optional[Path]:
        """
        Close the CSV file.
        
        Returns:
            Path to the closed file, or None if no file was open
        """
        if self._csv_file:
            self._csv_file.close()
            path = self._csv_path
            self._csv_file = None
            self._csv_writer = None
            self._csv_path = None
            return path
        return None
    
    def write_summary(self, metrics: SimulationMetrics) -> None:
        """Log a summary of the simulation metrics."""
        summary = metrics.to_summary_dict()

        logger.info("=" * 60)
        logger.info("SIMULATION SUMMARY")
        logger.info("=" * 60)
        logger.info("Scheduler: %s", summary["scheduler"])
        logger.info("Arrival Mode: %s", summary["arrival_mode"])
        logger.info("Configuration: %d cards, %d tasks, %d steps",
                   summary["cards"], summary["tasks"], summary["steps"])
        logger.info("-" * 60)
        logger.info("Tasks Completed: %d / %d (%.1f%%)",
                   summary["tasks_completed"],
                   summary["tasks"],
                   summary["completion_rate"] * 100)
        logger.info("Throughput: %.4f tasks/step", summary["throughput"])
        logger.info("Avg Load Imbalance (Variance): %.2f", summary["avg_load_imbalance"])
        logger.info("Max Load Imbalance (Variance): %.2f", summary["max_load_imbalance"])

        logger.info("-" * 60)
        logger.info("Avg Delay: %.2f steps", summary["avg_delay"])
        logger.info("P50 Delay: %.2f steps", summary["p50_delay"])
        logger.info("P95 Delay: %.2f steps", summary["p95_delay"])
        logger.info("P99 Delay: %.2f steps", summary["p99_delay"])
        logger.info("Max Delay: %.2f steps", summary["max_delay"])

        logger.info("=" * 60)

    def write_summary_csv(
        self,
        metrics: SimulationMetrics,
        output_prefix: Optional[str] = None,
    ) -> Path:
        """Write throughput summary to a CSV file."""
        summary = metrics.to_summary_dict()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_prefix and str(output_prefix).strip():
            filename = f"{output_prefix}_summary_{timestamp}.csv"
        else:
            filename = f"{metrics.scheduler_name.lower()}_summary_{timestamp}.csv"

        csv_path = self.data_dir / filename

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "scheduler", "arrival_mode", "cards", "tasks", "steps", "seed",
                "tasks_completed", "completion_rate", "throughput",
                "avg_load_imbalance", "max_load_imbalance",
                "card_cv", "card_jfi", "card_lif", "max_min_ratio",
                "avg_delay", "p50_delay", "p95_delay", "p99_delay", "max_delay",
                "bw_cap", "avg_congestion_ratio", "peak_backlog",
                "congested_card_tick_frac", "avg_utilization",
                "avg_demand_cv", "avg_backlog_cv",
                "mean_congestion_wait_ticks", "p95_congestion_wait_ticks",
                "congestion_timeouts",
            ])
            writer.writerow([
                summary["scheduler"], summary["arrival_mode"], summary["cards"],
                summary["tasks"], summary["steps"], summary["seed"],
                summary["tasks_completed"], summary["completion_rate"], summary["throughput"],
                summary["avg_load_imbalance"], summary["max_load_imbalance"],
                summary["card_cv"], summary["card_jfi"], summary["card_lif"], summary["max_min_ratio"],
                summary["avg_delay"], summary["p50_delay"], summary["p95_delay"],
                summary["p99_delay"], summary["max_delay"],
                summary["bw_cap"], summary["avg_congestion_ratio"], summary["peak_backlog"],
                summary["congested_card_tick_frac"], summary["avg_utilization"],
                summary["avg_demand_cv"], summary["avg_backlog_cv"],
                summary["mean_congestion_wait_ticks"], summary["p95_congestion_wait_ticks"],
                summary["congestion_timeouts"],
            ])

        logger.info("Saved summary CSV to %s", csv_path)
        return csv_path
