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
    
    @property
    def total_delay(self) -> int:
        """Total time from arrival to completion."""
        if self.completion_step < 0:
            return -1
        return self.completion_step - self.arrival_step


@dataclass
class LoadSnapshot:
    """Snapshot of card loads at a single time step."""
    time_step: int
    card_loads: Dict[int, float]  # card_id -> load
    card_task_counts: Dict[int, int]  # card_id -> task count
    
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

    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    def record_task_delay(self, task_id: int, arrival_step: int,
                          placement_step: int, completion_step: int) -> None:
        """Record timing information for a completed task."""
        delay = TaskDelay(
            task_id=task_id,
            arrival_step=arrival_step,
            placement_step=placement_step,
            completion_step=completion_step,
        )
        self.task_delays.append(delay)

    def record_load_snapshot(
        self,
        time_step: int,
        cards: List["Card"],
        epoch_loads: Dict[int, float],
    ) -> LoadSnapshot:
        """Record card loads from the scheduler's epoch-accumulated traffic."""
        card_loads = {c.card_id: epoch_loads.get(c.card_id, 0.0) for c in cards}
        snapshot = LoadSnapshot(
            time_step=time_step,
            card_loads=card_loads,
            card_task_counts={c.card_id: len(c.tasks) for c in cards},
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

    @property
    def avg_card_cv(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        return float(np.mean([s.cv for s in snaps]))

    @property
    def avg_card_jfi(self) -> float:
        snaps = self._steady_window()
        if not snaps:
            return 0.0
        return float(np.mean([s.jfi for s in snaps]))

    @property
    def avg_max_min_ratio(self) -> float:
        snaps = self._steady_window()
        ratios = [s.max_min_ratio for s in snaps if s.max_min_ratio > 0]
        if not ratios:
            return 0.0
        return float(np.mean(ratios))
    
    @property
    def throughput(self) -> float:
        """Tasks completed per time step."""
        total_steps = len(self.load_snapshots)
        if total_steps == 0:
            return 0.0
        return self.tasks_completed / total_steps
    
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
            "avg_load_imbalance": round(self.avg_load_imbalance, 2),
            "max_load_imbalance": round(self.max_load_imbalance, 2),
            "card_cv": round(self.avg_card_cv, 4),
            "card_jfi": round(self.avg_card_jfi, 4),
            "max_min_ratio": round(self.avg_max_min_ratio, 4),
            "p99_delay": round(self.p99_delay, 2),
            "p95_delay": round(self.p95_delay, 2),
            "p50_delay": round(self.p50_delay, 2),
            "avg_delay": round(self.avg_delay, 2),
            "max_delay": round(self.max_delay, 2),
        }

        return result


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
        self._csv_writer.writerow(["time_step", "card_id", "load", "tasks"])
        return self._csv_path
    
    def write_snapshot(self, snapshot: LoadSnapshot) -> None:
        """
        Write a load snapshot to CSV.
        
        Args:
            snapshot: The snapshot to write
        """
        if self._csv_writer is None:
            raise RuntimeError("CSV not started. Call start_csv() first.")
        
        for card_id in sorted(snapshot.card_loads.keys()):
            self._csv_writer.writerow([
                snapshot.time_step,
                card_id,
                snapshot.card_loads[card_id],
                snapshot.card_task_counts[card_id],
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
                "scheduler",
                "arrival_mode",
                "cards",
                "tasks",
                "steps",
                "seed",
                "tasks_completed",
                "completion_rate",
                "throughput",
                "avg_load_imbalance",
                "max_load_imbalance",
                "card_cv",
                "card_jfi",
                "max_min_ratio",
                "avg_delay",
                "p50_delay",
                "p95_delay",
                "p99_delay",
                "max_delay",
            ])
            writer.writerow([
                summary["scheduler"],
                summary["arrival_mode"],
                summary["cards"],
                summary["tasks"],
                summary["steps"],
                summary["seed"],
                summary["tasks_completed"],
                summary["completion_rate"],
                summary["throughput"],
                summary["avg_load_imbalance"],
                summary["max_load_imbalance"],
                summary["card_cv"],
                summary["card_jfi"],
                summary["max_min_ratio"],
                summary["avg_delay"],
                summary["p50_delay"],
                summary["p95_delay"],
                summary["p99_delay"],
                summary["max_delay"],
            ])

        logger.info("Saved summary CSV to %s", csv_path)
        return csv_path
