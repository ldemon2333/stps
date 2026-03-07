"""Round-Robin: Static Round-Robin Scheduler for SNN Workloads.

A simple static load balancing algorithm that distributes tasks evenly
across all available cards in a circular fashion.

Key Principle:
- Maintain a pointer to the current card
- For each task placement, try to place on current card
- Move pointer to next card after each placement attempt
- If current card cannot host, try next cards in round-robin order

This provides predictable, fair distribution of tasks across the cluster
with O(n) worst-case decision time where n is the number of cards.

Characteristics:
- Simple and predictable behavior
- Fair distribution when tasks have similar resource requirements
- No runtime migrations (static allocation)
- Deterministic placement order
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from .base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class RoundRobin(BaseScheduler):
    """
    Round-Robin Scheduler.
    
    A static load balancing algorithm for SNN workloads on neuromorphic clusters.
    
    Key Features:
    - O(1) average decision time, O(n) worst case
    - Predictable and fair task distribution
    - Simple implementation with deterministic behavior
    - No runtime migrations (static allocation)
    
    Algorithm:
    For task placement:
    1. Start from the current pointer position
    2. Try to place task on current card
    3. If successful, advance pointer to next card
    4. If not, try next card in circular order until all cards checked
    """
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        card_capacity: float = 5000.0,
        **kwargs,
    ):
        """
        Initialize Round-Robin scheduler.
        
        Args:
            cards: List of neuromorphic cards
            alpha: Weight for spike count in load calculation (default 1.0)
            beta: Weight for synaptic operations in load calculation (default 0.01)
        """
        super().__init__(cards, alpha, beta, card_capacity=card_capacity, **kwargs)
        
        # Current position in round-robin cycle
        self._current_index: int = 0
        
        # Per-card accumulated load over current epoch for metrics
        self._card_epoch_load: Dict[int, float] = {
            card.card_id: 0.0 for card in cards
        }
        
        logger.info(
            "RoundRobin scheduler initialized with %d cards, card_capacity=%.2f",
            len(cards),
        )
    
    @property
    def name(self) -> str:
        return "RoundRobin"
    
    def _advance_pointer(self) -> None:
        """Advance the round-robin pointer to the next card."""
        self._current_index = (self._current_index + 1) % len(self.cards)
    
    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        """
        Select a card for task placement using round-robin strategy.
        
        Tries cards starting from current pointer position, advancing
        through all cards in circular order until one can host the task.
        
        Args:
            task: Task to place
            
        Returns:
            Selected card, or None if no card can host the task
        """
        if not self.cards:
            return None
        
        num_cards = len(self.cards)
        start_index = self._current_index
        
        # Try each card starting from current position
        for i in range(num_cards):
            candidate_index = (start_index + i) % num_cards
            candidate = self.cards[candidate_index]
            
            if candidate.can_host(task):
                # Found a card that can host the task
                # Update pointer to next card for next placement
                self._current_index = (candidate_index + 1) % num_cards
                
                logger.debug(
                    "RoundRobin: Placing task %s on card %s (index %d)",
                    task.task_id,
                    candidate.card_id,
                    candidate_index,
                )
                return candidate
        
        # No card can host the task
        logger.warning(
            "RoundRobin: No card can host task %s (cores=%d, synapses=%d, memory=%.2fGB)",
            task.task_id,
            task.cores_required,
            task.synapses_required,
            task.memory_gb_required,
        )
        return None
    
    def record_physical_tick(self, scheduler_step: int) -> None:
        """
        Accumulate load samples during physical layer ticks.
        
        Same as other schedulers: accumulates instantaneous loads over the epoch
        for fair comparison in metrics.
        """
        for card in self.cards:
            card_tick_load = 0.0
            
            for task in card.tasks:
                # Calculate instantaneous task load
                task_tick_load = (
                    self.alpha * task.current_spike_count +
                    self.beta * task.current_synaptic_ops
                )
                card_tick_load += task_tick_load
            
            # Accumulate to card epoch load
            self._card_epoch_load[card.card_id] += card_tick_load
    
    def step(self, time_step: int) -> None:
        """
        Perform one scheduling step.
        
        Round-Robin is a static scheduler - no migrations are performed
        during runtime. All placement decisions are made at task arrival time.
        
        Args:
            time_step: Current simulation time step
        """
        # Static scheduler: accumulate load for metrics only
        pass
    
    def on_task_arrival(self, task: "Task", time_step: int) -> None:
        """
        Hook called when a new task arrives.
        
        Task placement is handled by select_card_for_task, called by the
        simulation engine. This hook can be used for logging or metrics.
        
        Args:
            task: The newly arrived task
            time_step: Current simulation time step
        """
        logger.debug(
            "RoundRobin: Task %s arrived at step %d",
            task.task_id,
            time_step,
        )
    
    def on_task_completion(self, task: "Task", time_step: int) -> None:
        """
        Hook called when a task completes execution.
        
        Args:
            task: The completed task
            time_step: Current simulation time step
        """
        logger.debug(
            "RoundRobin: Task %s completed at step %d",
            task.task_id,
            time_step,
        )
    
    def get_epoch_loads(self) -> Dict[int, float]:
        """
        Get accumulated load for each card over the current epoch.
        
        Returns:
            Dictionary mapping card_id to accumulated epoch load.
        """
        return self._card_epoch_load.copy()
    
    def reset_epoch_loads(self) -> None:
        """Reset accumulated epoch loads for the next epoch."""
        for card_id in self._card_epoch_load:
            self._card_epoch_load[card_id] = 0.0


# Register the scheduler
register_scheduler("roundrobin", RoundRobin)
register_scheduler("rr", RoundRobin)  # Short alias
