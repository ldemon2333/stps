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
    from schedule.base import SchedulerMetrics
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
    def queue_delay(self) -> int:
        """Time waiting in queue before placement (arrival to placement)."""
        if self.placement_step < 0:
            return -1  # Never placed
        return self.placement_step - self.arrival_step
    
    @property
    def execution_delay(self) -> int:
        """Time from placement to completion."""
        if self.completion_step < 0 or self.placement_step < 0:
            return -1
        return self.completion_step - self.placement_step
    
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
    def max_load(self) -> float:
        """Maximum load among all cards."""
        return max(self.card_loads.values()) if self.card_loads else 0.0
    
    @property
    def min_load(self) -> float:
        """Minimum load among all cards."""
        return min(self.card_loads.values()) if self.card_loads else 0.0


@dataclass
class SimulationMetrics:
    """
    Comprehensive metrics collected during simulation.
    
    Tracks:
    - Load snapshots over time
    - Task statistics (completed, pending)
    - Scheduler-specific metrics (migrations)
    - SLA violations and latency metrics
    """
    scheduler_name: str
    arrival_mode: str
    card_count: int
    task_count: int
    steps: int
    seed: Optional[int]
    card_capacity: float = 4000.0  # Load threshold for SLA violation
    
    # Time series data
    load_snapshots: List[LoadSnapshot] = field(default_factory=list)
    
    # Task statistics
    tasks_completed: int = 0
    tasks_pending_at_end: int = 0
    
    # Task delay tracking
    task_delays: List[TaskDelay] = field(default_factory=list)
    
    # SLA violation tracking: total count of (card, time_step) pairs exceeding capacity
    sla_violation_count: int = 0
    # Per-step SLA violations for visualization
    sla_violations_per_step: List[int] = field(default_factory=list)
    
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
    
    def check_sla_violation(self, time_step: int, snapshot: LoadSnapshot) -> int:
        """
        Check how many cards exceed capacity at this time step.
        
        SLA violation is defined per (card, time_step) pair:
        f(L_{n,t}) = 1 if L_{n,t} > capacity, else 0
        
        Returns:
            Number of cards that violated SLA at this time step
        """
        violations = sum(1 for load in snapshot.card_loads.values() 
                        if load > self.card_capacity)
        logger.debug("Time step %d: %d SLA violations, card_capacity=%.2f", time_step, violations, self.card_capacity)
        self.sla_violation_count += violations
        self.sla_violations_per_step.append(violations)
        return violations
    
    def record_load_snapshot(
        self,
        time_step: int,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        epoch_loads: Optional[Dict[int, float]] = None,
    ) -> LoadSnapshot:
        """
        Record current load state of all cards.
        
        Args:
            time_step: Current simulation step
            cards: List of cards to record
            alpha: Load weight for spikes
            beta: Load weight for synaptic ops
            epoch_loads: Optional accumulated epoch loads from scheduler.
                         If provided, uses these instead of instantaneous loads.
            
        Returns:
            The created snapshot
        """
        # Use epoch loads if provided, otherwise calculate instantaneous loads
        if epoch_loads is not None:
            card_loads = {c.card_id: epoch_loads.get(c.card_id, 0.0) for c in cards}
        else:
            card_loads = {c.card_id: c.calculate_load(alpha, beta) for c in cards}
        
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
    def sla_violation_rate(self) -> float:
        """
        SLA Violation Rate = sum(f(L_{n,t})) / (N × T)
        
        Where f(L_{n,t}) = 1 if card n's load at time t exceeds capacity.
        Returns a value between 0.0 and 1.0.
        """
        total_steps = len(self.load_snapshots)
        if total_steps == 0 or self.card_count == 0:
            return 0.0
        # Total possible violations = N cards × T time steps
        total_pairs = self.card_count * total_steps
        return self.sla_violation_count / total_pairs
    
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
    
    def to_summary_dict(self, scheduler_metrics: Optional["SchedulerMetrics"] = None) -> dict:
        """
        Convert metrics to a summary dictionary.
        
        Args:
            scheduler_metrics: Optional scheduler-specific metrics
            
        Returns:
            Dictionary with key metrics
        """
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
            # SLA and latency metrics
            "sla_violation_rate": round(self.sla_violation_rate, 4),
            "sla_violation_count": self.sla_violation_count,
            "p99_delay": round(self.p99_delay, 2),
            "p95_delay": round(self.p95_delay, 2),
            "p50_delay": round(self.p50_delay, 2),
            "avg_delay": round(self.avg_delay, 2),
            "max_delay": round(self.max_delay, 2),
        }
        
        if scheduler_metrics:
            result["total_migrations"] = scheduler_metrics.total_migrations
            result["total_migration_cost"] = round(scheduler_metrics.total_migration_cost, 4)
        
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
    
    def write_summary(
        self,
        metrics: SimulationMetrics,
        scheduler_metrics: Optional["SchedulerMetrics"] = None,
    ) -> None:
        """
        Log a summary of the simulation metrics.
        
        Args:
            metrics: Simulation metrics
            scheduler_metrics: Optional scheduler-specific metrics
        """
        summary = metrics.to_summary_dict(scheduler_metrics)
        
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
        
        # SLA and Latency metrics
        logger.info("-" * 60)
        logger.info("SLA Violation Rate: %.4f (%.2f%%)", 
                   summary["sla_violation_rate"],
                   summary["sla_violation_rate"] * 100)
        logger.info("Avg Delay: %.2f steps", summary["avg_delay"])
        logger.info("P50 Delay: %.2f steps", summary["p50_delay"])
        logger.info("P95 Delay: %.2f steps", summary["p95_delay"])
        logger.info("P99 Delay: %.2f steps", summary["p99_delay"])
        logger.info("Max Delay: %.2f steps", summary["max_delay"])
        
        if "total_migrations" in summary:
            logger.info("-" * 60)
            logger.info("Total Migrations: %d", summary["total_migrations"])
            logger.info("Total Migration Cost: %.4f", summary["total_migration_cost"])
            if summary["tasks_completed"] > 0:
                logger.info("Migrations per Completed Task: %.2f",
                           summary["total_migrations"] / summary["tasks_completed"])
        
        logger.info("=" * 60)
    
    def write_summary_csv(
        self,
        metrics: SimulationMetrics,
        scheduler_metrics: Optional["SchedulerMetrics"] = None,
        output_prefix: Optional[str] = None,
    ) -> Path:
        """
        Write throughput and migration summary to a CSV file.
        
        Args:
            metrics: Simulation metrics
            scheduler_metrics: Optional scheduler-specific metrics
            output_prefix: Optional prefix for filename
            
        Returns:
            Path to the summary CSV file
        """
        summary = metrics.to_summary_dict(scheduler_metrics)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if output_prefix and str(output_prefix).strip():
            filename = f"{output_prefix}_summary_{timestamp}.csv"
        else:
            filename = f"{metrics.scheduler_name.lower()}_summary_{timestamp}.csv"
        
        csv_path = self.data_dir / filename
        
        # Calculate migrations per completed task
        migrations_per_task = 0.0
        if scheduler_metrics and summary["tasks_completed"] > 0:
            migrations_per_task = scheduler_metrics.total_migrations / summary["tasks_completed"]
        
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
                "sla_violation_rate",
                "avg_delay",
                "p50_delay",
                "p95_delay",
                "p99_delay",
                "max_delay",
                "total_migrations",
                "total_migration_cost",
                "migrations_per_task",
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
                summary["sla_violation_rate"],
                summary["avg_delay"],
                summary["p50_delay"],
                summary["p95_delay"],
                summary["p99_delay"],
                summary["max_delay"],
                summary.get("total_migrations", 0),
                summary.get("total_migration_cost", 0.0),
                round(migrations_per_task, 4),
            ])
        
        logger.info("Saved summary CSV to %s", csv_path)
        return csv_path
