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
        d_max: int = 16,
        horizon: int = 64,
        centrality_split_threshold: float = 0.2,
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
        self.d_max = d_max
        self.horizon = horizon
        self.centrality_split_threshold = centrality_split_threshold
        self.scheduler_kwargs = scheduler_kwargs
        self._fingerprint_paths: List[str] = []

        self.cards: List[Card] = []
        self.active_tasks: List[Task] = []
        self.pending_tasks: List[Task] = []
        self.scheduler: Optional[BaseScheduler] = None
        self.metrics: Optional[SimulationMetrics] = None
        self.metrics_writer: Optional[MetricsWriter] = None
        self._card_epoch_load: Dict[int, float] = {}

    def _initialize_scheduler(self, scheduler_class: type) -> BaseScheduler:
        scheduler_kwargs = {
            "cards": self.cards,
            **self.scheduler_kwargs,
        }

        if self._is_stps():
            scheduler_kwargs.update(
                horizon=self.horizon,
                d_max=self.d_max,
                bw_max=self.bw_max,
                centrality_split_threshold=self.centrality_split_threshold,
            )

        return scheduler_class(**scheduler_kwargs)

    def _is_stps(self) -> bool:
        return self.scheduler_name.lower().startswith("stps")

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

        self.cards = [Card(card_id=i) for i in range(self.card_count)]
        self._card_epoch_load = {card.card_id: 0.0 for card in self.cards}

        self.scheduler = self._initialize_scheduler(scheduler_class)
        # Hand the scheduler a read-only view of cluster epoch loads.
        self.scheduler.cluster_epoch_loads = self._card_epoch_load

        self.metrics = SimulationMetrics(
            scheduler_name=self.scheduler.name,
            arrival_mode=self.arrival_mode,
            card_count=self.card_count,
            task_count=self.task_count,
            steps=self.steps,
            seed=self.seed,
        )
        self.metrics.start_time = datetime.now()

        self.metrics_writer = MetricsWriter(self.data_dir)
        self.metrics_writer.start_csv(
            self.scheduler.name,
            suffix=self.arrival_mode,
            output_prefix=self.data_output,
        )

        arrival_plan = build_arrival_plan(self.arrival_mode, self.task_count, self.steps)
        logger.info("Arrival plan (%s): %s", self.arrival_mode, arrival_plan)

        self._load_fingerprint_dir()

        self.active_tasks = []
        self.pending_tasks = []
        next_task_id = 0

        t = 1
        while t <= self.steps or self.active_tasks or self.pending_tasks:
            logger.info("Time step %d", t)

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

            snapshot = self.metrics.record_load_snapshot(t, self.cards, self._card_epoch_load)
            self.metrics_writer.write_snapshot(snapshot)

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

        return self.metrics

    def _tick(self, t: int) -> None:
        for task in self.active_tasks:
            if task.start_offset > 0 and task.placement_step >= 0 \
                    and t < task.placement_step + task.start_offset:
                task.current_traffic = 0.0
                continue
            task.simulate_tick()
        self._record_load()

    def _record_load(self) -> None:
        """Accumulate per-card traffic for the current step into epoch load."""
        for card in self.cards:
            self._card_epoch_load[card.card_id] += sum(t.current_traffic for t in card.tasks)

    def _reset_epoch_loads(self) -> None:
        for card_id in self._card_epoch_load:
            self._card_epoch_load[card_id] = 0.0

    def _place_pending_tasks(self, time_step: int) -> int:
        assigned = 0
        for task in self.pending_tasks[:]:
            target = self.scheduler.select_card_for_task(task)  # type: ignore
            if target is None:
                continue
            if not target.put(task):
                continue
            task.placement_step = time_step
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
                )
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
    d_max: int = 16,
    horizon: int = 64,
    centrality_split_threshold: float = 0.2,
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
        d_max=d_max,
        horizon=horizon,
        centrality_split_threshold=centrality_split_threshold,
        **kwargs,
    )
    return engine.run()
