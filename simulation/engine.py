"""Unified simulation engine supporting pluggable schedulers.

This module provides the main simulation loop for SNN cluster experiments.
It supports any scheduler that implements the BaseScheduler interface.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np

from schedule.base import BaseScheduler, get_scheduler
from util.card import Card
from util.metrics import MetricsWriter, SimulationMetrics
from util.sim import build_arrival_plan, create_task, setup_logging
from util.task import Task
from fingerprint import Fingerprint

logger = logging.getLogger(__name__)


class SimulationEngine:
    def __init__(
        self,
        scheduler_name: str,
        card_count: int = 4,
        task_count: int = 100,
        steps: int = 60,
        seed: Optional[int] = None,
        log_dir: str = "log",
        data_dir: str = "data",
        arrival_mode: str = "poisson",
        data_output: Optional[str] = None,
        fingerprint_dir: Optional[str] = None,
        bw_max: float = 1e9,
        bw_cap: Optional[float] = None,
        d_max: int = 16,
        horizon: int = 64,
        centrality_split_threshold: float = 0.2,
        wandb: bool = False,
        wandb_project: str = "stps-simulation",
        wandb_run_name: Optional[str] = None,
        wandb_entity: Optional[str] = None,
        wandb_mode: Optional[str] = None,
        **scheduler_kwargs,
    ):
        self.scheduler_name = scheduler_name
        self.card_count = card_count
        self.task_count = task_count
        self.steps = steps
        self.seed = seed
        self.log_dir = log_dir
        self.data_dir = data_dir
        self.arrival_mode = arrival_mode
        self.data_output = data_output
        self.fingerprint_dir = fingerprint_dir
        self.bw_max = bw_max
        self.bw_cap = bw_cap
        self.d_max = d_max
        self.horizon = horizon
        self.centrality_split_threshold = centrality_split_threshold
        self.wandb = wandb
        self.wandb_project = wandb_project
        self.wandb_run_name = wandb_run_name
        self.wandb_entity = wandb_entity
        self.wandb_mode = wandb_mode
        self.scheduler_kwargs = scheduler_kwargs
        self._fingerprint_paths: List[str] = []

        self.cards: List[Card] = []
        self.active_tasks: List[Task] = []
        self.pending_tasks: List[Task] = []
        self.scheduler: Optional[BaseScheduler] = None
        self.metrics: Optional[SimulationMetrics] = None
        self.metrics_writer: Optional[MetricsWriter] = None
        self.wandb_run = None
        self._card_epoch_load: Dict[int, float] = {}
        self._card_epoch_demand: Dict[int, float] = {}
        self._card_epoch_backlog: Dict[int, float] = {}
        self._max_backlog_ticks: int = 0

    def _init_wandb(self):
        if not self.wandb:
            return None
        try:
            import wandb
        except ImportError as exc:
            raise RuntimeError(
                "wandb logging requested but wandb is not installed in this Python environment"
            ) from exc

        config = {
            "scheduler": self.scheduler_name,
            "cards": self.card_count,
            "tasks": self.task_count,
            "steps": self.steps,
            "seed": self.seed,
            "arrival_mode": self.arrival_mode,
            "fingerprint_dir": self.fingerprint_dir,
            "bw_max": self.bw_max,
            "d_max": self.d_max,
            "horizon": self.horizon,
            "centrality_split_threshold": self.centrality_split_threshold,
        }
        init_kwargs = {
            "project": self.wandb_project,
            "name": self.wandb_run_name,
            "entity": self.wandb_entity,
            "mode": self.wandb_mode,
            "config": config,
        }
        init_kwargs = {k: v for k, v in init_kwargs.items() if v is not None}
        return wandb.init(**init_kwargs)

    def _log_wandb_snapshot(self, snapshot, arrivals: int) -> None:
        if self.wandb_run is None:
            return

        payload = {
            "arrival/tasks": arrivals,
            "cluster/total_load": float(sum(snapshot.card_loads.values())),
            "cluster/mean_load": float(snapshot.mean_load),
            "cluster/cv": float(snapshot.cv),
            "cluster/jfi": float(snapshot.jfi),
            "cluster/active_tasks": len(self.active_tasks),
            "cluster/pending_tasks": len(self.pending_tasks),
        }
        for card_id in sorted(snapshot.card_loads):
            payload[f"card/{card_id}_load"] = float(snapshot.card_loads[card_id])
            payload[f"card/{card_id}_tasks"] = int(snapshot.card_task_counts[card_id])

        self.wandb_run.log(payload, step=snapshot.time_step)

    def _finish_wandb(self) -> None:
        if self.wandb_run is not None:
            self.wandb_run.finish()
            self.wandb_run = None

    def _initialize_scheduler(self, scheduler_class: type) -> BaseScheduler:
        scheduler_kwargs = {
            "cards": self.cards,
            **self.scheduler_kwargs,
        }

        if self._uses_phase_shift():
            scheduler_kwargs.update(
                horizon=self.horizon,
                d_max=self.d_max,
                bw_max=self.bw_max,
                centrality_split_threshold=self.centrality_split_threshold,
            )

        return scheduler_class(**scheduler_kwargs)

    def _is_stps(self) -> bool:
        return self.scheduler_name.lower().startswith("stps")

    def _uses_phase_shift(self) -> bool:
        name = self.scheduler_name.lower()
        return name.startswith("stps") or name.endswith("-phase")

    def _load_fingerprint_dir(self) -> None:
        """Index *.npz fingerprints; required because per-tick load is read from E^(t)."""
        if not self.fingerprint_dir:
            raise ValueError(
                "fingerprint_dir is required: per-tick task load is sampled from Fingerprint.E. "
                "Run `make fingerprints` to generate synthetic .npz files."
            )
        d = Path(self.fingerprint_dir)
        if not d.is_dir():
            raise FileNotFoundError(f"Fingerprint dir {d} not found")
        self._fingerprint_paths = sorted(str(p) for p in d.glob("*.npz"))
        if not self._fingerprint_paths:
            raise FileNotFoundError(f"No *.npz fingerprints in {d}")
        logger.info("Loaded %d fingerprints from %s", len(self._fingerprint_paths), d)

    def _pick_fingerprint(self, task_id: int) -> tuple[str, Fingerprint]:
        """Round-robin fingerprint selection from the indexed dir."""
        from fingerprint import load_fingerprint
        path = self._fingerprint_paths[task_id % len(self._fingerprint_paths)]
        fp = load_fingerprint(path)
        return path, fp

    def run(self) -> SimulationMetrics:
        setup_logging(self.log_dir)

        scheduler_class = get_scheduler(self.scheduler_name)

        logger.info(
            "Starting %s simulation | cards=%d tasks=%d steps=%d seed=%s arrival=%s",
            scheduler_class.__name__ if hasattr(scheduler_class, '__name__') else self.scheduler_name,
            self.card_count,
            self.task_count,
            self.steps,
            self.seed,
            self.arrival_mode,
        )

        if self.seed is not None:
            random.seed(self.seed)
            np.random.seed(self.seed)

        self.cards = [Card(card_id=i, bw_cap=self.bw_cap) for i in range(self.card_count)]
        self._card_epoch_load = {card.card_id: 0.0 for card in self.cards}
        self._card_epoch_demand = {card.card_id: 0.0 for card in self.cards}
        self._card_epoch_backlog = {card.card_id: 0.0 for card in self.cards}

        self.scheduler = self._initialize_scheduler(scheduler_class)
        # Hand the scheduler a read-only view of cluster epoch loads.
        self.scheduler.cluster_epoch_loads = self._card_epoch_load
        # Also expose the backlog view (used by stps-la, docs/Q0_result.md §5.2 改动 D).
        if hasattr(self.scheduler, "cluster_epoch_backlog"):
            self.scheduler.cluster_epoch_backlog = self._card_epoch_backlog

        self.metrics = SimulationMetrics(
            scheduler_name=self.scheduler.name,
            arrival_mode=self.arrival_mode,
            card_count=self.card_count,
            task_count=self.task_count,
            steps=self.steps,
            seed=self.seed,
        )
        self.metrics.start_time = datetime.now()
        self.metrics.bw_cap_value = self.bw_cap

        self.metrics_writer = MetricsWriter(self.data_dir)
        self.metrics_writer.start_csv(
            self.scheduler.name,
            suffix=self.arrival_mode,
            output_prefix=self.data_output,
        )

        self.wandb_run = self._init_wandb()

        arrival_plan = build_arrival_plan(self.arrival_mode, self.task_count, self.steps)
        logger.info("Arrival plan (%s): %s", self.arrival_mode, arrival_plan)

        self._load_fingerprint_dir()

        # Set MAX_BACKLOG_TICKS = 2 * max(T_fingerprint) once fingerprints are known.
        # We don't pre-load every fp; use 2 * steps as a generous cap for safety.
        self._max_backlog_ticks = max(2 * self.steps, 256)

        self.active_tasks = []
        self.pending_tasks = []
        next_task_id = 0

        t = 1
        while t <= self.steps or self.active_tasks or self.pending_tasks:
            logger.info("Time step %d", t)
            arrivals = 0

            if t <= self.steps:
                arrivals = arrival_plan[t - 1]
                if arrivals:
                    logger.info("Arrivals at step %d: %d", t, arrivals)
                for _ in range(arrivals):
                    fp_path, fp = self._pick_fingerprint(next_task_id)
                    task = create_task(next_task_id, t, fp)
                    task.fingerprint_path = fp_path
                    self.pending_tasks.append(task)
                    self.scheduler.on_task_arrival(task, t)
                    next_task_id += 1

            self._place_pending_tasks(t)

            self._tick(t)
            self.scheduler.step(t)

            snapshot = self.metrics.record_load_snapshot(
                t, self.cards, self._card_epoch_load,
                epoch_demand=self._card_epoch_demand,
                epoch_backlog=self._card_epoch_backlog,
            )
            self.metrics_writer.write_snapshot(snapshot)
            self._log_wandb_snapshot(snapshot, arrivals)

            for card in self.cards:
                logger.info(
                    "Card %d load=%.2f tasks=%d",
                    card.card_id,
                    snapshot.card_loads[card.card_id],
                    snapshot.card_task_counts[card.card_id],
                )

            self._reset_epoch_loads()

            self._handle_completions(t)

            t += 1

        self.metrics.end_time = datetime.now()
        self.metrics.tasks_pending_at_end = len(self.pending_tasks)

        csv_final_path = self.metrics_writer.close()
        logger.info("Saved load trace to %s", csv_final_path)

        self.metrics_writer.write_summary(self.metrics)

        self.metrics_writer.write_summary_csv(
            self.metrics,
            output_prefix=self.data_output,
        )

        self._finish_wandb()

        return self.metrics

    def _tick(self, t: int) -> None:
        """Per-tick bandwidth-aware service (docs/traffic_optim.md §A.2).

        For each card:
          1. compute task-level demand (pending_traffic preferred, else next trace quantum);
          2. if bw_cap is set, scale per-task served traffic proportionally;
          3. write pending back to tasks whose quantum wasn't fully served;
          4. only fully-served tasks advance tick_index and become eligible for
             duration_steps decrement in _handle_completions.
        """
        # Determine task demand for this tick.
        executable: List[Task] = []
        task_demand: Dict[int, float] = {}
        for task in self.active_tasks:
            if task.start_offset > 0 and task.placement_step >= 0 \
                    and t < task.placement_step + task.start_offset:
                task.current_traffic = 0.0
                continue
            if task.pending_traffic > 0.0:
                demand = task.pending_traffic
            else:
                demand = task.next_trace_quantum()
            task_demand[id(task)] = demand
            executable.append(task)

        # Per-tick demand per card (used for cap/scale this tick).
        tick_demand: Dict[int, float] = {}
        for card in self.cards:
            if card.card_id not in self._card_epoch_demand:
                self._card_epoch_demand[card.card_id] = 0.0
                self._card_epoch_backlog[card.card_id] = 0.0
            d_tick = sum(task_demand.get(id(tk), 0.0) for tk in card.tasks)
            tick_demand[card.card_id] = d_tick
            self._card_epoch_demand[card.card_id] += d_tick

        # Apply per-card bandwidth limit.
        for card in self.cards:
            d = tick_demand[card.card_id]
            cap = card.bw_cap
            served_total = float(cap) if cap is not None and d > cap else d
            if cap is None or d <= cap:
                scale = 1.0
            else:
                scale = served_total / d if d > 0 else 0.0
            self._card_epoch_load[card.card_id] += served_total
            backlog_total = 0.0
            for tk in card.tasks:
                if id(tk) not in task_demand:
                    tk.current_traffic = 0.0
                    continue
                demand = task_demand[id(tk)]
                served = demand * scale
                tk.current_traffic = served
                leftover = demand - served
                if leftover > 1e-12 and cap is not None:
                    tk.pending_traffic = leftover
                    tk.blocked_ticks += 1
                    tk.congestion_wait_ticks += 1
                    backlog_total += leftover
                    # Timeout circuit-breaker: force drain.
                    if tk.blocked_ticks > self._max_backlog_ticks:
                        self.metrics.congestion_timeouts += 1  # type: ignore[union-attr]
                        tk.pending_traffic = 0.0
                        tk.blocked_ticks = 0
                        tk.advance_trace_tick()
                else:
                    tk.pending_traffic = 0.0
                    tk.blocked_ticks = 0
                    tk.advance_trace_tick()
            self._card_epoch_backlog[card.card_id] = backlog_total

    def _record_load(self) -> None:
        # Kept for API compatibility; _tick already populates epoch loads.
        pass

    def _reset_epoch_loads(self) -> None:
        for card_id in self._card_epoch_load:
            self._card_epoch_load[card_id] = 0.0
            self._card_epoch_demand[card_id] = 0.0
            self._card_epoch_backlog[card_id] = 0.0

    def _place_pending_tasks(self, time_step: int) -> int:
        assigned = 0
        for task in self.pending_tasks[:]:
            target = self.scheduler.select_card_for_task(task)  # type: ignore
            if target is None:
                if getattr(task, "rejected", False):
                    if self.metrics is not None:
                        self.metrics.record_rejection(getattr(task, "reject_reason", "rejected"))
                    self.pending_tasks.remove(task)
                    logger.info(
                        "Task %s dropped from pending queue: %s",
                        task.task_id,
                        getattr(task, "reject_reason", "rejected"),
                    )
                continue
            if not target.put(task):
                continue
            task.placement_step = time_step
            if self.metrics is not None:
                self.metrics.record_start_offset(task.start_offset)
            self.active_tasks.append(task)
            self.pending_tasks.remove(task)
            assigned += 1

        if assigned:
            logger.info(
                "Assigned %d pending tasks; remaining pending=%d",
                assigned,
                len(self.pending_tasks),
            )
        elif self.pending_tasks:
            logger.info("Pending tasks awaiting capacity: %d", len(self.pending_tasks))

        return assigned

    def _handle_completions(self, time_step: int) -> int:
        assert self.metrics is not None, "Metrics not initialized"

        finished: List[Task] = []
        for task in self.active_tasks:
            if task.start_offset > 0 and task.placement_step >= 0 \
                    and time_step < task.placement_step + task.start_offset:
                continue
            # docs/traffic_optim.md §A.2: if NoC backlog blocked this task's quantum
            # from fully draining, hold its lifecycle counter — wait time gets paid in
            # extra ticks rather than free progress.
            if task.pending_traffic > 0.0:
                continue
            task.duration_steps -= 1
            if task.duration_steps <= 0:
                finished.append(task)

        if finished:
            for task in finished:
                task.completion_step = time_step
                self.metrics.record_task_delay(
                    task_id=task.task_id,
                    arrival_step=task.arrival_step,
                    placement_step=task.placement_step,
                    completion_step=task.completion_step,
                    host_card_id=int(task.host_card_id),
                    cold_start_ticks=int(task.start_offset),
                )
                self.metrics.congestion_wait_ticks.append(int(task.congestion_wait_ticks))
                if 0 <= task.host_card_id < len(self.cards):
                    host_card = self.cards[task.host_card_id]
                    host_card.evict(task)
                self.active_tasks.remove(task)
                self.scheduler.on_task_completion(task, time_step)  # type: ignore
                self.metrics.tasks_completed += 1  # type: ignore

            logger.info(
                "Tasks completed this step: %s",
                [t.task_id for t in finished],
            )

        return len(finished)


def run_simulation(
    scheduler: str,
    cards: int = 4,
    tasks: int = 100,
    steps: int = 60,
    seed: Optional[int] = None,
    log_dir: str = "log",
    data_dir: str = "data",
    arrival_mode: str = "poisson",
    fingerprint_dir: Optional[str] = None,
    bw_max: float = 1e9,
    bw_cap: Optional[float] = None,
    d_max: int = 16,
    horizon: int = 64,
    centrality_split_threshold: float = 0.2,
    wandb: bool = False,
    wandb_project: str = "stps-simulation",
    wandb_run_name: Optional[str] = None,
    wandb_entity: Optional[str] = None,
    wandb_mode: Optional[str] = None,
    **kwargs,
) -> SimulationMetrics:
    """Convenience function to run a simulation."""
    engine = SimulationEngine(
        scheduler_name=scheduler,
        card_count=cards,
        task_count=tasks,
        steps=steps,
        seed=seed,
        log_dir=log_dir,
        data_dir=data_dir,
        arrival_mode=arrival_mode,
        fingerprint_dir=fingerprint_dir,
        bw_max=bw_max,
        bw_cap=bw_cap,
        d_max=d_max,
        horizon=horizon,
        centrality_split_threshold=centrality_split_threshold,
        wandb=wandb,
        wandb_project=wandb_project,
        wandb_run_name=wandb_run_name,
        wandb_entity=wandb_entity,
        wandb_mode=wandb_mode,
        **kwargs,
    )
    return engine.run()
