"""P2C: Power of Two Choices Scheduler for SNN Workloads.

A randomized load balancing algorithm that provides near-optimal load distribution
with O(1) decision time by sampling only 2 random candidates.

Key Principle:
- For each task placement, randomly select 2 candidate cards
- Compare their loads (using weighted sum or DRF-style dominant utilization)
- Place task on the card with lower load

This achieves exponentially better load balancing than random placement
while maintaining constant-time decisions regardless of cluster size.

Reference:
- "The Power of Two Choices in Randomized Load Balancing" (Mitzenmacher, 2001)
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Dict, List, Optional

from .base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class P2C(BaseScheduler):
    """
    Power of Two Choices (P2C) Scheduler.
    
    A randomized load balancing algorithm for SNN workloads on neuromorphic clusters.
    
    Key Features:
    - O(1) decision time: only samples 2 random cards
    - Near-optimal load balancing (exponentially better than random)
    - Simple implementation with strong theoretical guarantees
    - No runtime migrations (static allocation)
    
    Algorithm:
    For task placement:
    1. Randomly select 2 candidate cards that can host the task
    2. Calculate load score for each candidate
    3. Place task on the card with lower load score
    
    Load Score Options (configurable via load_metric):
    - "weighted": α * spikes + β * synaptic_ops (default, uses epoch load)
    - "drf": Dominant resource utilization (max of all resource dimensions)
    - "tasks": Simple task count
    """
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        load_metric: str = "weighted",
        **kwargs,
    ):
        """
        Initialize P2C scheduler.
        
        Args:
            cards: List of neuromorphic cards
            alpha: Weight for spike count in load calculation (default 1.0)
            beta: Weight for synaptic operations in load calculation (default 0.01)
            load_metric: Load comparison method ("weighted", "drf", "tasks")
        """
        super().__init__(cards, alpha, beta, **kwargs)
        self.load_metric = load_metric
        
        # Per-card accumulated load over current epoch for metrics
        self._card_epoch_load: Dict[int, float] = {
            card.card_id: 0.0 for card in cards
        }
        
        logger.info(
            "P2C scheduler initialized with %d cards, load_metric=%s",
            len(cards),
            self.load_metric,
        )
    
    @property
    def name(self) -> str:
        return "P2C"
    
    def _calculate_load_score(self, card: "Card", task: Optional["Task"] = None) -> float:
        """
        Calculate load score for a card.
        
        Args:
            card: The card to evaluate
            task: Optional task to include in calculation (for placement preview)
            
        Returns:
            Load score (lower is better)
        """
        if self.load_metric == "drf":
            return self._calculate_drf_score(card, task)
        elif self.load_metric == "tasks":
            return len(card.tasks) + (1 if task else 0)
        else:  # "weighted" (default)
            return self._calculate_weighted_score(card, task)
    
    def _calculate_weighted_score(
        self,
        card: "Card",
        task: Optional["Task"] = None,
    ) -> float:
        """
        Calculate weighted load score (α * spikes + β * ops).
        
        Uses current epoch accumulated load plus task estimate.
        """
        # Current epoch load
        score = self._card_epoch_load.get(card.card_id, 0.0)
        
        # Add task's estimated contribution if provided
        if task is not None:
            # Estimate based on task properties
            # Use neuron count as proxy for expected spike activity
            estimated_spikes = task.neuron_count * 0.5  # Assume 50% firing rate
            estimated_ops = task.neuron_count * task.complexity_ratio * 100
            score += self.alpha * estimated_spikes + self.beta * estimated_ops
        
        return score
    
    def _calculate_drf_score(
        self,
        card: "Card",
        task: Optional["Task"] = None,
    ) -> float:
        """
        Calculate DRF-style dominant resource utilization.
        
        Returns maximum utilization across all resource dimensions.
        """
        # Current resource usage
        used_cores = sum(t.cores_required for t in card.tasks)
        used_synapses = sum(t.synapses_required for t in card.tasks)
        used_memory = sum(t.memory_gb_required for t in card.tasks)
        
        # Add task requirements if provided
        if task is not None:
            used_cores += task.cores_required
            used_synapses += task.synapses_required
            used_memory += task.memory_gb_required
        
        # Calculate utilization ratios
        util_cores = used_cores / card.cores if card.cores > 0 else 0.0
        util_synapses = used_synapses / card.synapses if card.synapses > 0 else 0.0
        util_memory = used_memory / card.memory_gb if card.memory_gb > 0 else 0.0
        
        # Return dominant (maximum) utilization
        return max(util_cores, util_synapses, util_memory)
    
    
    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        """
        Select a card for the task using Power of Two Choices.
        
        Randomly samples 2 eligible cards and picks the one with lower load.
        
        Args:
            task: The task to place
            
        Returns:
            Selected card, or None if no card can host the task
        """
        # Find all cards that can host the task
        eligible = [c for c in self.cards if c.can_host(task)]
        
        if not eligible:
            logger.warning("[P2C] No eligible card for Task %d", task.task_id)
            return None
        
        # If only 1 eligible, use it
        if len(eligible) == 1:
            selected = eligible[0]
            logger.info(
                "[P2C] Only 1 eligible card for Task %d -> Card %d",
                task.task_id,
                selected.card_id,
            )
            return selected
        
        # Randomly sample 2 candidates
        candidates = random.sample(eligible, min(2, len(eligible)))
        
        # Calculate load scores
        scores = []
        for card in candidates:
            score = self._calculate_load_score(card, task)
            scores.append((card, score))
            logger.debug(
                "[P2C] Candidate Card %d: score=%.4f",
                card.card_id,
                score,
            )
        
        # Select card with minimum score
        best_card, best_score = min(scores, key=lambda x: x[1])
        other_card, other_score = max(scores, key=lambda x: x[1])
        
        logger.info(
            "[P2C] Task %d: Card %d (%.2f) vs Card %d (%.2f) -> selected Card %d",
            task.task_id,
            candidates[0].card_id,
            scores[0][1],
            candidates[1].card_id,
            scores[1][1],
            best_card.card_id,
        )
        
        return best_card
    
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
    
    def get_epoch_loads(self) -> Dict[int, float]:
        """Return accumulated epoch loads for each card."""
        return dict(self._card_epoch_load)
    
    def reset_epoch_loads(self) -> None:
        """Reset accumulated epoch loads for the next epoch."""
        for card_id in self._card_epoch_load:
            self._card_epoch_load[card_id] = 0.0
    
    def step(self, time_step: int) -> None:
        """
        P2C is a static scheduler - no runtime migrations.
        
        All placement decisions are made at task arrival time.
        """
        pass


# Register P2C scheduler
register_scheduler("p2c", P2C)
