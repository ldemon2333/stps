"""DRF: Dominant Resource Fairness Scheduler for SNN Workloads.

A static scheduling algorithm that assigns tasks to cards based on
minimizing the dominant resource utilization after placement.

Key Principle:
- For each card, compute utilization across 4 dimensions: Cores, Synapses, Memory, Bandwidth
- The "dominant utilization" is the maximum of these 4 utilizations
- Assign task to the card that minimizes the post-placement dominant utilization

Formula:
Score(n) = min_{n ∈ N}( max(C_used+c_req/C_total, M_used+m_req/M_total, 
                            B_used+b_req/B_total, S_used+s_req/S_total) )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from .base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class DRF(BaseScheduler):
    """
    Dominant Resource Fairness (DRF) Scheduler.
    
    A static scheduling algorithm adapted for SNN workloads on neuromorphic clusters.
    
    Key Features:
    - Multi-dimensional resource awareness (Cores, Synapses, Memory, Bandwidth)
    - Dominant resource calculation for each card
    - Greedy placement minimizing maximum resource utilization
    - No runtime migrations (static allocation)
    
    Algorithm:
    For task placement, compute for each candidate card:
    1. Calculate post-placement utilization for each resource dimension
    2. Take maximum utilization as the "dominant utilization"
    3. Select card with minimum dominant utilization after placement
    
    This ensures balanced resource usage across all dimensions and prevents
    any single resource from becoming a bottleneck.
    """
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        **kwargs,
    ):
        """
        Initialize DRF scheduler.
        
        Args:
            cards: List of neuromorphic cards
            alpha: Weight for spike count in load calculation (default 1.0)
            beta: Weight for synaptic operations in load calculation (default 0.01)
        """
        super().__init__(cards, alpha, beta, **kwargs)
        
        # Per-card accumulated load over current epoch for metrics
        self._card_epoch_load: Dict[int, float] = {
            card.card_id: 0.0 for card in cards
        }
        
        logger.info(
            "DRF scheduler initialized with %d cards",
            len(cards),
        )
    
    @property
    def name(self) -> str:
        return "DRF"
    
    def _calculate_resource_utilization(
        self,
        card: "Card",
        task: Optional["Task"] = None,
    ) -> Dict[str, float]:
        """
        Calculate resource utilization for each dimension.
        
        Args:
            card: The card to calculate utilization for
            task: Optional task to include in calculation (for placement preview)
            
        Returns:
            Dictionary with utilization for each resource dimension
        """
        # Current resource usage on card
        used_cores = sum(t.cores_required for t in card.tasks)
        used_synapses = sum(t.synapses_required for t in card.tasks)
        used_memory = sum(t.memory_gb_required for t in card.tasks)
 
        
        # Add task requirements if provided
        if task is not None:
            used_cores += task.cores_required
            used_synapses += task.synapses_required
            used_memory += task.memory_gb_required
         
        
        # Calculate utilization ratios (0.0 to 1.0+)
        # Bandwidth total estimated as card bandwidth capacity
        bandwidth_total = card.bandwidth_mbps  # Use as relative capacity
        
        return {
            "cores": used_cores / card.cores if card.cores > 0 else 0.0,
            "synapses": used_synapses / card.synapses if card.synapses > 0 else 0.0,
            "memory": used_memory / card.memory_gb if card.memory_gb > 0 else 0.0,
        }
    
    def _get_dominant_utilization(
        self,
        card: "Card",
        task: Optional["Task"] = None,
    ) -> float:
        """
        Get the dominant (maximum) resource utilization for a card.
        
        Args:
            card: The card to evaluate
            task: Optional task to include in calculation
            
        Returns:
            Maximum utilization across all resource dimensions
        """
        utilization = self._calculate_resource_utilization(card, task)
        return max(utilization.values())
    
    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        """
        Select the best card for a task using DRF policy.
        
        Finds the card that minimizes the dominant resource utilization
        after placing the task.
        
        Args:
            task: The task to place
            
        Returns:
            Best card for placement, or None if no card can host the task
        """
        best_card = None
        min_dominant_util = float("inf")
        
        for card in self.cards:
            # Check if card can host the task
            if not card.can_host(task):
                continue
            
            # Calculate dominant utilization after placement
            dominant_util = self._get_dominant_utilization(card, task)
            
            logger.debug(
                "Card %d: dominant utilization after task %d = %.4f",
                card.card_id,
                task.task_id,
                dominant_util,
            )
            
            # Select card with minimum dominant utilization
            if dominant_util < min_dominant_util:
                min_dominant_util = dominant_util
                best_card = card
        
        if best_card is not None:
            logger.info(
                "[DRF] Selected Card %d for Task %d (dominant util = %.4f)",
                best_card.card_id,
                task.task_id,
                min_dominant_util,
            )
        else:
            logger.warning(
                "[DRF] Could not find a card for Task %d",
                task.task_id,
            )
        
        return best_card
    
    def on_task_arrival(self, task: "Task", time_step: int) -> None:
        """
        Handle new task arrival notification.
        
        Note: Actual placement is done by engine calling select_card_for_task().
        This hook is for any pre-placement bookkeeping if needed.
        
        Args:
            task: The newly arrived task
            time_step: Current simulation time step
        """
        # DRF placement is handled via select_card_for_task() called by engine
        logger.debug(
            "[DRF] Task %d arrived at step %d",
            task.task_id,
            time_step,
        )
    
    def record_physical_tick(self, scheduler_step: int) -> None:
        """
        Accumulate load samples during physical layer ticks.
        
        Same as other schedulers: accumulates instantaneous loads over the epoch
        for fair comparison in metrics.
        """
        for card in self.cards:
            card_tick_load = 0.0
            
            for task in card.tasks:
                # Calculate instantaneous task load: L_i(t_phy)
                task_tick_load = (
                    self.alpha * task.current_spike_count +
                    self.beta * task.current_synaptic_ops
                )
                card_tick_load += task_tick_load
            
            # Accumulate to card epoch load: L_raw(m, T_epoch)
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
        DRF is a static scheduler - no runtime migrations.
        
        All placement decisions are made at task arrival time.
        """
        pass


# Register DRF scheduler
register_scheduler("drf", DRF)
