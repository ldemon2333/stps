"""Metrics collection and calculation for simulation evaluation."""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from schedule.base import SchedulerMetrics
    from util.card import Card

logger = logging.getLogger(__name__)


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
    
    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
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
                summary.get("total_migrations", 0),
                summary.get("total_migration_cost", 0.0),
                round(migrations_per_task, 4),
            ])
        
        logger.info("Saved summary CSV to %s", csv_path)
        return csv_path
