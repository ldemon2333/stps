"""Base scheduler interface for extensible scheduling algorithms.

This module provides:
- BaseScheduler: Abstract base class for all scheduling algorithms
- Registry functions: register_scheduler, get_scheduler, list_schedulers
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class BaseScheduler(ABC):
    """
    Abstract base class for all scheduling algorithms.

    Subclasses must implement:
    - name: Human-readable scheduler name
    - step(): Called each simulation step to perform scheduling decisions

    Optionally override:
    - on_task_arrival(): Hook when new task arrives
    - on_task_completion(): Hook when task completes
    """

    def __init__(
        self,
        cards: List["Card"],
        **kwargs,
    ):
        """
        Initialize the scheduler.

        Args:
            cards: List of neuromorphic cards in the cluster
            **kwargs: Additional algorithm-specific parameters
        """
        self.cards = cards
        # Read-only view of cluster epoch loads, populated by SimulationEngine.
        # Schedulers may read this for placement heuristics but must not mutate it.
        self.cluster_epoch_loads: Dict[int, float] = {card.card_id: 0.0 for card in cards}

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the scheduler."""
        raise NotImplementedError

    @abstractmethod
    def step(self, time_step: int) -> None:
        """
        Perform one scheduling step.

        Called after all tasks have executed their tick for this time step.

        Args:
            time_step: Current simulation time step
        """
        raise NotImplementedError

    def on_task_arrival(self, task: "Task", time_step: int) -> None:
        """Hook called when a new task arrives."""
        pass

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        """Select the best card for placing a task. Subclasses must implement."""
        raise NotImplementedError

    def on_task_completion(self, task: "Task", time_step: int) -> None:
        """Hook called when a task completes execution."""
        pass


_SCHEDULER_REGISTRY: dict[str, type[BaseScheduler]] = {}


def register_scheduler(name: str, scheduler_class: type[BaseScheduler]) -> None:
    """Register a scheduler class with a given name."""
    _SCHEDULER_REGISTRY[name.lower()] = scheduler_class


def get_scheduler(name: str) -> type[BaseScheduler]:
    """Get a scheduler class by name."""
    name_lower = name.lower()
    if name_lower not in _SCHEDULER_REGISTRY:
        available = ", ".join(_SCHEDULER_REGISTRY.keys())
        raise ValueError(f"Unknown scheduler '{name}'. Available: {available}")
    return _SCHEDULER_REGISTRY[name_lower]


def list_schedulers() -> List[str]:
    """Return list of available scheduler names."""
    return list(_SCHEDULER_REGISTRY.keys())
