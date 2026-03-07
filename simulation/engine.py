"""Unified simulation engine supporting pluggable schedulers.

This module provides the main simulation loop for SNN cluster experiments.
It supports any scheduler that implements the BaseScheduler interface.

Key Components:
- SimulationEngine: Main class managing simulation lifecycle
- run_simulation: Convenience function for running experiments

Two-Tier Timing Architecture:
- Physical layer (~1ms): High-frequency task execution ticks
- Scheduler layer (~500ms): Low-frequency migration decisions
"""
from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import List, Optional
import numpy as np

from schedule.base import BaseScheduler, get_scheduler
from schedule.placement_strategy import (
    BestFitStrategy,
    P2CStrategy,
    DRFStrategy,
    RoundRobinStrategy,
)
from util.card import Card
from util.metrics import MetricsWriter, SimulationMetrics
from util.sim import build_arrival_plan, create_task, setup_logging
from util.task import Task

logger = logging.getLogger(__name__)

# Default load weights
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 0.01

# Two-tier timing architecture constants
# Physical layer runs at higher frequency within each scheduler epoch
DEFAULT_TICKS_PER_STEP = 10  # Number of physical ticks per scheduler step


class SimulationEngine:
    """
    Unified simulation engine for SNN cluster experiments.
    
    Supports any scheduler that implements BaseScheduler interface.
    Handles task arrival, execution, completion, and metrics collection.
    """
    
    def __init__(
        self,
        scheduler_name: str = "static",
        card_count: int = 4,
        task_count: int = 100,
        steps: int = 60,
        seed: Optional[int] = None,
        log_dir: str = "log",
        data_dir: str = "data",
        arrival_mode: str = "poisson",
        alpha: float = DEFAULT_ALPHA,
        beta: float = DEFAULT_BETA,
        placement_strategy: Optional[str] = None,
        load_metric: str = "weighted",
        ticks_per_step: int = DEFAULT_TICKS_PER_STEP,
        data_output: Optional[str] = None,
        card_capacity: float = 4000.0,
        **scheduler_kwargs,
    ):
        """
        Initialize simulation engine.
        
        Args:
            scheduler_name: Name of scheduler to use (from registry)
            card_count: Number of neuromorphic cards
            task_count: Total tasks to generate
            steps: Simulation duration in scheduler time steps (epochs)
            seed: Random seed for reproducibility
            log_dir: Directory for log files
            data_dir: Directory for data files
            arrival_mode: Task arrival pattern ("poisson", "bursty", "mixed")
            alpha: Weight for spike count in load calculation
            beta: Weight for synaptic operations in load calculation
            placement_strategy: Placement strategy to use (None, "bestfit", "p2c", "drf", "rr")
            load_metric: Load metric for P2C strategy ("weighted", "drf", "tasks")
            ticks_per_step: Physical ticks per scheduler step (two-tier timing)
            card_capacity: Load threshold for SLA violation detection
            **scheduler_kwargs: Additional arguments for scheduler
        """
        self.scheduler_name = scheduler_name
        self.card_count = card_count
        self.task_count = task_count
        self.steps = steps
        self.seed = seed
        self.log_dir = log_dir
        self.data_dir = data_dir
        self.arrival_mode = arrival_mode
        self.alpha = alpha
        self.beta = beta
        self.placement_strategy = placement_strategy
        self.load_metric = load_metric
        self.ticks_per_step = ticks_per_step
        self.data_output = data_output
        self.card_capacity = card_capacity
        self.scheduler_kwargs = scheduler_kwargs
        
        # Will be initialized in run()
        self.cards: List[Card] = []
        self.active_tasks: List[Task] = []
        self.pending_tasks: List[Task] = []
        self.scheduler: Optional[BaseScheduler] = None
        self.metrics: Optional[SimulationMetrics] = None
        self.metrics_writer: Optional[MetricsWriter] = None
    
    def _create_placement_strategy(self):
        """
        Create placement strategy based on configuration.
        
        Returns:
            PlacementStrategy instance or None if using scheduler's default
        """
        if not self.placement_strategy:
            return None
        
        strategy_map = {
            "bestfit": BestFitStrategy,
            "p2c": P2CStrategy,
            "drf": DRFStrategy,
            "rr": RoundRobinStrategy,
        }
        strategy_class = strategy_map.get(self.placement_strategy.lower())
        if strategy_class is None:
            raise ValueError(
                f"Unknown placement strategy: {self.placement_strategy}. "
                f"Available: {', '.join(strategy_map.keys())}"
            )
        
        # P2C strategy needs load_metric parameter
        if self.placement_strategy.lower() == "p2c":
            strategy = strategy_class(
                self.cards, 
                self.alpha, 
                self.beta,
                load_metric=self.load_metric
            )
        else:
            strategy = strategy_class(
                self.cards, 
                self.alpha, 
                self.beta
            )
        
        logger.info("Using placement strategy: %s", self.placement_strategy.upper())
        return strategy
    
    def _initialize_scheduler(self, scheduler_class: type, placement_strategy) -> BaseScheduler:
        """
        Initialize the scheduler with the given class and strategy.
        
        Args:
            scheduler_class: Scheduler class to instantiate
            placement_strategy: Optional placement strategy
            
        Returns:
            Initialized scheduler instance
        """
        scheduler_kwargs = {
            "cards": self.cards,
            "alpha": self.alpha,
            "beta": self.beta,
            "card_capacity": self.card_capacity,
            **self.scheduler_kwargs,
        }
        
        if placement_strategy is not None:
            scheduler_kwargs["placement_strategy"] = placement_strategy
        
        return scheduler_class(**scheduler_kwargs)
    
    def run(self) -> SimulationMetrics:
        """
        Run the complete simulation.
        
        Returns:
            Collected simulation metrics
        """
        # Setup logging
        setup_logging(self.log_dir)
        
        # Get scheduler class
        scheduler_class = get_scheduler(self.scheduler_name)
        
        logger.info(
            "Starting %s simulation | cards=%d tasks=%d steps=%d seed=%s arrival=%s, card_capacity=%.2f",
            scheduler_class.__name__ if hasattr(scheduler_class, '__name__') else self.scheduler_name,
            self.card_count,
            self.task_count,
            self.steps,
            self.seed,
            self.arrival_mode,
            self.card_capacity,
        )
        
        # Set random seed
        if self.seed is not None:
            random.seed(self.seed)
            np.random.seed(self.seed)
        
        # Initialize cards
        self.cards = [Card(card_id=i) for i in range(self.card_count)]
        
        # Create placement strategy and scheduler
        placement_strategy = self._create_placement_strategy()
        self.scheduler = self._initialize_scheduler(scheduler_class, placement_strategy)
        
        # Initialize metrics
        self.metrics = SimulationMetrics(
            scheduler_name=self.scheduler.name,
            arrival_mode=self.arrival_mode,
            card_count=self.card_count,
            task_count=self.task_count,
            steps=self.steps,
            seed=self.seed,
            card_capacity=self.card_capacity,
        )
        self.metrics.start_time = datetime.now()
        
        # Setup metrics writer
        self.metrics_writer = MetricsWriter(self.data_dir)
        self.metrics_writer.start_csv(
            self.scheduler.name,
            suffix=self.arrival_mode,
            output_prefix=self.data_output,
        )
        
        # Build arrival plan
        arrival_plan = build_arrival_plan(self.arrival_mode, self.task_count, self.steps)
        logger.info("Arrival plan (%s): %s", self.arrival_mode, arrival_plan)
        
        # Initialize task tracking
        self.active_tasks = []
        self.pending_tasks = []
        next_task_id = 0
        
        # Main simulation loop
        t = 1
        while t <= self.steps or self.active_tasks or self.pending_tasks:
            logger.info("Time step %d", t)
            
            # 1. Generate new task arrivals
            if t <= self.steps:
                arrivals = arrival_plan[t - 1]
                if arrivals:
                    logger.info("Arrivals at step %d: %d", t, arrivals)
                for _ in range(arrivals):
                    task = create_task(next_task_id, t)
                    self.pending_tasks.append(task)
                    self.scheduler.on_task_arrival(task, t)
                    next_task_id += 1
            
            # 2. Try to place pending tasks
            self._place_pending_tasks(t)
            
            # 3. Execute physical layer ticks (two-tier timing architecture)
            # Physical layer (~1ms) runs multiple times per scheduler step (~500ms epoch)
            for _ in range(self.ticks_per_step):
                for task in self.active_tasks:
                    task.simulate_tick()
                # Record load samples after each physical tick 
                self.scheduler.record_physical_tick(t)
            
            # 4. Run scheduler step (may trigger migrations)
            # This is the scheduler layer decision point
            # only for dynamic schedulers
            self.scheduler.step(t)
            
            # 5. Record metrics using epoch loads AFTER migrations
            # This captures the final epoch loads after load balancing
            epoch_loads = self.scheduler.get_epoch_loads()
            snapshot = self.metrics.record_load_snapshot(
                t, self.cards, self.alpha, self.beta, epoch_loads=epoch_loads
            )
            self.metrics_writer.write_snapshot(snapshot)
            
            # 6. Check SLA violations (card load > card_capacity)
            self.metrics.check_sla_violation(t, snapshot)
            
            # Log card states
            for card in self.cards:
                logger.info(
                    "Card %d load=%.2f tasks=%d",
                    card.card_id,
                    snapshot.card_loads[card.card_id],
                    snapshot.card_task_counts[card.card_id],
                )
            
            # 7. Reset epoch loads for next epoch
            self.scheduler.reset_epoch_loads()
            
            # 8. Handle task completions
            self._handle_completions(t)
            
            t += 1
        
        # Finalize
        self.metrics.end_time = datetime.now()
        self.metrics.tasks_pending_at_end = len(self.pending_tasks)
        
        # Close CSV file
        csv_final_path = self.metrics_writer.close()
        logger.info("Saved load trace to %s", csv_final_path)
        
        # Write summary
        scheduler_metrics = self.scheduler.get_metrics()
        self.metrics_writer.write_summary(self.metrics, scheduler_metrics)
        
        # Write summary CSV with throughput and migration info
        self.metrics_writer.write_summary_csv(
            self.metrics, 
            scheduler_metrics,
            output_prefix=self.data_output,
        )
        
        return self.metrics
    
    # ------------------------------------------------------------------
    # Single-step execution interface (for RL environment)
    # These methods expose individual phases of the main loop for
    # step-by-step control without modifying the existing run() method.
    # ------------------------------------------------------------------

    def step_arrivals(
        self, t: int, arrival_plan: list, next_task_id: int
    ) -> int:
        """
        Process task arrivals for time step *t*.
        
        Args:
            t: Current scheduler time step
            arrival_plan: Pre-built arrival schedule
            next_task_id: Next task ID counter value
            
        Returns:
            Number of new arrivals
        """
        arrivals = 0
        if t <= self.steps and t <= len(arrival_plan):
            arrivals = arrival_plan[t - 1]
            for i in range(arrivals):
                task = create_task(next_task_id + i, t)
                self.pending_tasks.append(task)
                self.scheduler.on_task_arrival(task, t)  # type: ignore
        return arrivals

    def step_placement(self, t: int) -> int:
        """Place pending tasks. Returns number placed."""
        return self._place_pending_tasks(t)

    def step_physical_ticks(self, t: int) -> None:
        """Execute physical-layer ticks for one scheduler epoch."""
        for _ in range(self.ticks_per_step):
            for task in self.active_tasks:
                task.simulate_tick()
            self.scheduler.record_physical_tick(t)  # type: ignore

    def step_record_metrics(self, t: int) -> "LoadSnapshot":
        """Record metrics snapshot after migrations. Returns the snapshot."""
        from util.metrics import LoadSnapshot
        epoch_loads = self.scheduler.get_epoch_loads()  # type: ignore
        snapshot = self.metrics.record_load_snapshot(  # type: ignore
            t, self.cards, self.alpha, self.beta, epoch_loads=epoch_loads
        )
        if self.metrics_writer is not None:
            self.metrics_writer.write_snapshot(snapshot)
        return snapshot

    def step_reset_epoch(self) -> None:
        """Reset scheduler epoch loads for the next epoch."""
        self.scheduler.reset_epoch_loads()  # type: ignore

    # ------------------------------------------------------------------

    def _place_pending_tasks(self, time_step: int) -> int:
        """
        Attempt to place pending tasks on cards.
        
        Uses the scheduler's select_card_for_task() method for placement
        strategy. This allows different schedulers to implement their own
        policies (e.g., DRF uses dominant resource fairness).
        
        Args:
            time_step: Current time step (for recording placement time)
        
        Returns:
            Number of tasks placed
        """
        assigned = 0
        for task in self.pending_tasks[:]:  # Iterate copy
            # Use scheduler's placement strategy instead of default Best-Fit
            target = self.scheduler.select_card_for_task(task) # type: ignore
            if target is None:
                continue
            if not target.put(task):
                continue
            # Record placement time for latency tracking
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
        """
        Handle completed tasks.
        
        Args:
            time_step: Current time step
            
        Returns:
            Number of tasks completed
        """
        assert self.metrics is not None, "Metrics not initialized"
        
        finished: List[Task] = []
        for task in self.active_tasks:
            task.duration_steps -= 1
            if task.duration_steps <= 0:
                finished.append(task)
        
        if finished:
            for task in finished:
                # Record completion time for latency tracking
                task.completion_step = time_step
                
                # Record task delay in metrics
                self.metrics.record_task_delay(
                    task_id=task.task_id,
                    arrival_step=task.arrival_step,
                    placement_step=task.placement_step,
                    completion_step=task.completion_step,
                )
                
                # Remove from host card
                if 0 <= task.host_card_id < len(self.cards):
                    host_card = self.cards[task.host_card_id]
                    host_card.evict(task)
                self.active_tasks.remove(task)
                self.scheduler.on_task_completion(task, time_step) # type: ignore
                self.metrics.tasks_completed += 1 # type: ignore
            
            logger.info(
                "Tasks completed this step: %s",
                [t.task_id for t in finished],
            )
        
        return len(finished)


def run_simulation(
    scheduler: str = "static",
    cards: int = 4,
    tasks: int = 100,
    steps: int = 60,
    seed: Optional[int] = None,
    log_dir: str = "log",
    data_dir: str = "data",
    arrival_mode: str = "poisson",
    **kwargs,
) -> SimulationMetrics:
    """
    Convenience function to run a simulation.
    
    Args:
        scheduler: Scheduler name ("static", "glass", "dynamic", etc.)
        cards: Number of cards
        tasks: Number of tasks
        steps: Simulation steps
        seed: Random seed
        log_dir: Log directory
        data_dir: Data directory
        arrival_mode: Arrival pattern
        **kwargs: Additional scheduler parameters
        
    Returns:
        Simulation metrics
    """
    engine = SimulationEngine(
        scheduler_name=scheduler,
        card_count=cards,
        task_count=tasks,
        steps=steps,
        seed=seed,
        log_dir=log_dir,
        data_dir=data_dir,
        arrival_mode=arrival_mode,
        **kwargs,
    )
    return engine.run()
