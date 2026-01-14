"""Base scheduler interface for extensible scheduling algorithms."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Type

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task
    from schedule.placement_strategy import PlacementStrategy

logger = logging.getLogger(__name__)


@dataclass
class MigrationEvent:
    """Record of a single task migration."""
    time_step: int
    task_id: int
    source_card_id: int
    target_card_id: int
    task_load: float
    state_size_mb: float
    migration_cost: float


@dataclass
class SchedulerMetrics:
    """Accumulated metrics for a scheduler during simulation."""
    migrations: List[MigrationEvent] = field(default_factory=list)
    total_migrations: int = 0
    total_migration_cost: float = 0.0

    def record_migration(self, event: MigrationEvent) -> None:
        """Record a migration event."""
        self.migrations.append(event)
        self.total_migrations += 1
        self.total_migration_cost += event.migration_cost


class BaseScheduler(ABC):
    """
    Abstract base class for all scheduling algorithms.
    
    Subclasses must implement:
    - name: Human-readable scheduler name
    - step(): Called each simulation step to perform scheduling decisions
    
    Optionally override:
    - on_task_arrival(): Hook when new task arrives
    - on_task_completion(): Hook when task completes
    - get_metrics(): Return scheduler-specific metrics
    """
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        placement_strategy: Optional["PlacementStrategy"] = None,
        **kwargs,
    ):
        """
        Initialize the scheduler.
        
        Args:
            cards: List of neuromorphic cards in the cluster
            alpha: Weight for spike count in load calculation
            beta: Weight for synaptic operations in load calculation
            placement_strategy: Optional PlacementStrategy for task placement.
                If None, uses default BestFitStrategy.
            **kwargs: Additional algorithm-specific parameters
        """
        self.cards = cards
        self.alpha = alpha
        self.beta = beta
        self._metrics = SchedulerMetrics()
        
        # Use provided strategy or default to BestFit
        if placement_strategy is not None:
            self._placement_strategy = placement_strategy
        else:
            from schedule.placement_strategy import BestFitStrategy
            self._placement_strategy = BestFitStrategy(cards, alpha, beta)
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the scheduler."""
        raise NotImplementedError
    
    @abstractmethod
    def step(self, time_step: int) -> None:
        """
        Perform one scheduling step.
        
        This is called after all tasks have executed their tick for this time step.
        The scheduler can analyze current loads and trigger migrations.
        
        Args:
            time_step: Current simulation time step
        """
        raise NotImplementedError
    
    def on_task_arrival(self, task: "Task", time_step: int) -> None:
        """
        Hook called when a new task arrives.
        
        Args:
            task: The newly arrived task
            time_step: Current simulation time step
        """
        pass
    

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        """
        Select the best card for placing a task.
        
        Delegates to the placement strategy. Subclasses can override
        either this method or provide a different strategy.
        
        Args:
            task: Task to place
            
        Returns:
            Selected card, or None if no card can host the task
        """
        return self._placement_strategy.select_card(task)
    
    def on_task_completion(self, task: "Task", time_step: int) -> None:
        """
        Hook called when a task completes execution.
        
        Args:
            task: The completed task
            time_step: Current simulation time step
        """
        pass
    
    def record_physical_tick(self, scheduler_step: int) -> None:
        """
        Hook called after each physical layer tick.
        
        In the two-tier timing architecture:
        - Physical layer (~1ms): High-frequency task execution
        - Scheduler layer (~500ms): Low-frequency migration decisions
        
        This method is called multiple times per scheduler step to allow
        schedulers to collect load samples for statistical analysis (e.g., P95).
        
        Args:
            scheduler_step: Current scheduler time step (not physical tick)
        """
        pass
    
    def get_epoch_loads(self) -> Dict[int, float]:
        """
        Get accumulated load for each card over the current epoch.
        
        This is used for metrics recording to capture the epoch-level load
        that dynamic schedulers use for decision making.
        
        Returns:
            Dictionary mapping card_id to accumulated epoch load.
            Default implementation returns instantaneous loads.
        """
        return {card.card_id: card.calculate_load(self.alpha, self.beta) for card in self.cards}
    
    def reset_epoch_loads(self) -> None:
        """
        Reset accumulated epoch loads for the next epoch.
        
        Called by the simulation engine after recording metrics.
        Default implementation does nothing (for static schedulers).
        """
        pass
    
    def calculate_load(self, card: "Card") -> float:
        """
        Calculate the current load of a card.
        
        Args:
            card: The card to calculate load for
            
        Returns:
            Current load value (α * spikes + β * ops)
        """
        return card.calculate_load(self.alpha, self.beta)
    
    def get_metrics(self) -> SchedulerMetrics:
        """Return accumulated metrics for this scheduler."""
        return self._metrics
    
    def _record_migration(
        self,
        time_step: int,
        task: "Task",
        source: "Card",
        target: "Card",
    ) -> None:
        """
        Record a migration event in metrics.
        
        Args:
            time_step: When the migration occurred
            task: The migrated task
            source: Source card
            target: Target card
        """
        task_load = self.alpha * task.current_spike_count + self.beta * task.current_synaptic_ops
        migration_cost = task.state_size_mb / target.bandwidth_mbps
        
        event = MigrationEvent(
            time_step=time_step,
            task_id=task.task_id,
            source_card_id=source.card_id,
            target_card_id=target.card_id,
            task_load=task_load,
            state_size_mb=task.state_size_mb,
            migration_cost=migration_cost,
        )
        self._metrics.record_migration(event)
    
    def _execute_migration(
        self,
        task: "Task",
        source: "Card",
        target: "Card",
        time_step: int,
    ) -> bool:
        """
        Execute a task migration from source to target card.
        
        Args:
            task: Task to migrate
            source: Source card
            target: Target card
            time_step: Current time step (for metrics)
            
        Returns:
            True if migration succeeded, False otherwise
        """
        if not target.can_host(task):
            # Calculate resource usage to identify the bottleneck
            used_cores = sum(t.cores_required for t in target.tasks)
            used_synapses = sum(t.synapses_required for t in target.tasks)
            used_memory = sum(t.memory_gb_required for t in target.tasks)
            
            # Determine which resource is insufficient
            issues = []
            if used_cores + task.cores_required > target.cores:
                issues.append(f"Cores({used_cores}+{task.cores_required}>{target.cores})")
            if used_synapses + task.synapses_required > target.synapses:
                issues.append(f"Synapses({used_synapses}+{task.synapses_required}>{target.synapses})")
            if used_memory + task.memory_gb_required > target.memory_gb:
                issues.append(f"Memory({used_memory:.2f}+{task.memory_gb_required:.2f}>{target.memory_gb:.2f}GB)")
            
            logger.info(
                "   >>> [MIGRATION] Target Card %s lacks capacity: %s; skip",
                target.card_id,
                ", ".join(issues) if issues else "unknown",
            )
            return False
        
        # Execute migration
        source.evict(task)
        succes = target.put(task)
        assert succes, "Migration should succeed after capacity check"
          
        # Record successful migration
        self._record_migration(time_step, task, source, target)
        logger.info(
            "   >>> [MIGRATION] Moving Task %s from Card %s to Card %s",
            task.task_id,
            source.card_id,
            target.card_id,
        )
        return True


# Registry for available schedulers
_SCHEDULER_REGISTRY: dict[str, type[BaseScheduler]] = {}


def register_scheduler(name: str, scheduler_class: type[BaseScheduler]) -> None:
    """
    Register a scheduler class with a given name.
    
    Args:
        name: Name to register the scheduler under (used in CLI)
        scheduler_class: The scheduler class to register
    """
    _SCHEDULER_REGISTRY[name.lower()] = scheduler_class


def get_scheduler(name: str) -> type[BaseScheduler]:
    """
    Get a scheduler class by name.
    
    Args:
        name: Name of the scheduler
        
    Returns:
        The scheduler class
        
    Raises:
        ValueError: If scheduler not found
    """
    name_lower = name.lower()
    if name_lower not in _SCHEDULER_REGISTRY:
        available = ", ".join(_SCHEDULER_REGISTRY.keys())
        raise ValueError(f"Unknown scheduler '{name}'. Available: {available}")
    return _SCHEDULER_REGISTRY[name_lower]


def list_schedulers() -> List[str]:
    """Return list of available scheduler names."""
    return list(_SCHEDULER_REGISTRY.keys())
