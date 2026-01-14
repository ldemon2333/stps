"""GLaSS: Global Load-aware SNN Scheduler.

Implementation based on algorithm.md specification:
- Two-Tier Timing Architecture (physical ~1ms, scheduler ~500ms epoch)
- Accumulated load over epoch window (not P95)
- Fixed dual-hysteresis thresholds (Θ_high=0.85, Θ_low=0.60, Θ_safe=0.75)
- ROI-Greedy migration strategy with Best-Fit target allocation
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

from .base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class CardState(Enum):
    """Card state classification based on normalized load."""
    CRITICAL = "CRITICAL"      # L >= Θ_high: trigger migration out
    AVAILABLE = "AVAILABLE"    # L <= Θ_low: can accept migration in
    STABLE = "STABLE"          # otherwise: maintain status quo


@dataclass
class TaskROI:
    """Task ROI (Return on Investment) for migration decision."""
    task: "Task"
    epoch_load: float        # Accumulated load in current epoch: L_i(T_epoch)
    state_size_mb: float     # Migration data size
    efficiency_score: float  # E_i = (L_i^epoch)^γ / (S_state + ε)


class GLaSS(BaseScheduler):
    """
    Global Load-aware SNN Scheduler (GLaSS).
    
    A dynamic load balancing algorithm designed for neuromorphic computing clusters.
    
    Key Features (from algorithm.md):
    - Two-tier timing: physical layer (~1ms) vs scheduler layer (~500ms epoch)
    - Accumulated load over epoch for scheduling decisions
    - Fixed dual-hysteresis thresholds for stability
    - ROI-Greedy strategy: prioritize high-load but light-state tasks
    - Best-Fit target allocation to maximize fragmented resource usage
    
    Load Definitions (from algorithm.md Section 2.B):
    - L_i(t_phy) = α·SpikeCount + β·SynapticOps  (instantaneous task load)
    - L_i(T_epoch) = Σ L_i(t_phy)  (accumulated task load over epoch)
    - L_m^sched = L_raw(m, T_epoch) / C_capacity  (normalized card load)
    
    Thresholds:
    - Θ_high = 0.85: Overload threshold (trigger migration out)
    - Θ_low = 0.60: Reception threshold (can accept migration in)
    - Θ_safe = 0.75: Safe target after migration
    """
    
    # Fixed thresholds from algorithm.md Section 3
    THETA_HIGH = 0.85   # Overload threshold
    THETA_LOW = 0.60    # Reception threshold
    THETA_SAFE = 0.75   # Safe target after migration
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        card_capacity: float = 5000.0,
        gamma: float = 1.5,
        **kwargs,
    ):
        """
        Initialize GLaSS scheduler.
        
        Args:
            cards: List of neuromorphic cards
            alpha: Weight for spike count in load calculation (default 1.0)
            beta: Weight for synaptic operations in load calculation (default 0.01)
            card_capacity: Max capacity constant for normalization (C_capacity)
            gamma: Heat preference factor for ROI calculation (default 1.5)
        """
        super().__init__(cards, alpha, beta, **kwargs)
        self.card_capacity = card_capacity
        self.gamma = gamma
        
        # Per-task accumulated load over current epoch: task_id -> L_i(T_epoch)
        self._task_epoch_load: Dict[int, float] = {}
        
        # Per-card accumulated load over current epoch: card_id -> L_raw(m, T_epoch)
        self._card_epoch_load: Dict[int, float] = {
            card.card_id: 0.0 for card in cards
        }
        
        logger.info(
            "GLaSS Load bands | Θ_high=%.2f Θ_low=%.2f Θ_safe=%.2f capacity=%.0f",
            self.THETA_HIGH,
            self.THETA_LOW,
            self.THETA_SAFE,
            self.card_capacity,
        )
    
    @property
    def name(self) -> str:
        return "GLaSS"
    
    def get_epoch_loads(self) -> Dict[int, float]:
        """
        Get accumulated load for each card over the current epoch.
        
        Overrides BaseScheduler to return GLaSS's accumulated epoch loads
        instead of instantaneous loads.
        
        Returns:
            Dictionary mapping card_id to accumulated epoch load L_raw(m, T_epoch).
        """
        return dict(self._card_epoch_load)
    
    def reset_epoch_loads(self) -> None:
        """
        Reset accumulated epoch loads for the next epoch.
        
        Called by engine after recording metrics snapshot.
        """
        self._reset_epoch_loads()
    
    def _reset_epoch_loads(self) -> None:
        """Reset accumulated loads at the start of each epoch."""
        self._task_epoch_load.clear()
        for card_id in self._card_epoch_load:
            self._card_epoch_load[card_id] = 0.0
    
    def _get_normalized_card_load(self, card: "Card") -> float:
        """
        Get normalized card load for scheduling decision.
        
        L_m^sched = L_raw(m, T_epoch) / C_capacity
        """
        raw_load = self._card_epoch_load.get(card.card_id, 0.0)
        return raw_load / self.card_capacity
    
    def _get_task_epoch_load(self, task: "Task") -> float:
        """Get accumulated load of a task over the epoch: L_i(T_epoch)."""
        return self._task_epoch_load.get(task.task_id, 0.0)
    
    def _classify_card(self, normalized_load: float) -> CardState:
        """
        Classify card state based on normalized load.
        
        Based on algorithm.md Section 3.1:
        - CRITICAL if L >= Θ_high (trigger migration out)
        - AVAILABLE if L <= Θ_low (can accept migration in)
        - STABLE otherwise
        """
        if normalized_load >= self.THETA_HIGH:
            return CardState.CRITICAL
        elif normalized_load <= self.THETA_LOW:
            return CardState.AVAILABLE
        else:
            return CardState.STABLE
    
    def _calculate_efficiency_score(self, task: "Task") -> TaskROI:
        """
        Calculate ROI efficiency score for a task.
        
        Based on algorithm.md Section 3.2:
        $$E_i = (L_i^epoch)^γ / (S_state + ε)$$s
        
        Higher γ (default 1.5) favors high-load tasks to avoid "ant moving" problem.
        """
        epoch_load = self._get_task_epoch_load(task)
        state_size = task.state_size_mb
        
        # E_i = (L_i^epoch)^γ / (S_state + ε)
        epsilon = 0.001
        efficiency = (epoch_load ** self.gamma) / (state_size + epsilon)
        
        return TaskROI(
            task=task,
            epoch_load=epoch_load,
            state_size_mb=state_size,
            efficiency_score=efficiency,
        )
    
    def record_physical_tick(self, scheduler_step: int) -> None:
        """
        Accumulate load samples during physical layer ticks.
        
        In two-tier timing architecture, this is called multiple times per scheduler
        step. We accumulate instantaneous loads to compute L_i(T_epoch).
        
        From algorithm.md Section 2.B:
        - L_i(t_phy) = α·SpikeCount + β·SynapticOps  (instantaneous)
        - L_i(T_epoch) = Σ L_i(t_phy)  (accumulated over epoch)
        
        Args:
            scheduler_step: Current scheduler time step
        """
        for card in self.cards:
            card_tick_load = 0.0
            
            for task in card.tasks:
                # Calculate instantaneous task load: L_i(t_phy)
                task_tick_load = (
                    self.alpha * task.current_spike_count +
                    self.beta * task.current_synaptic_ops
                )
                
                # Accumulate to task epoch load: L_i(T_epoch) += L_i(t_phy)
                if task.task_id not in self._task_epoch_load:
                    self._task_epoch_load[task.task_id] = 0.0
                self._task_epoch_load[task.task_id] += task_tick_load
                
                card_tick_load += task_tick_load
            
            # Accumulate to card epoch load: L_raw(m, T_epoch)
            if card.card_id not in self._card_epoch_load:
                self._card_epoch_load[card.card_id] = 0.0
            self._card_epoch_load[card.card_id] += card_tick_load
            
    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        """
        Select the best card for placing a task.
        
        GLaSS uses the placement strategy (default: Best-Fit).
        Can be customized via __init__ placement_strategy parameter.
        """
        return super().select_card_for_task(task)
    
    def step(self, time_step: int) -> None:
        """
        Perform global load balancing for this scheduler step (epoch).
        
        Implements the three-phase algorithm from algorithm.md Section 3:
        1. Sense Phase: Classify cards using accumulated normalized load
        2. Decision Phase: ROI-Greedy task selection with Best-Fit allocation
        3. Execution Phase: Perform migrations with resource constraint checks
        
        Note: Load samples are accumulated in record_physical_tick() during
        physical layer execution. After this step, epoch loads are reset.
        """
        logger.info("--- Time Step %s Global Balancing ---", time_step)
        
        # =================================================================
        # Phase 1: Sense - Classify cards using accumulated epoch loads
        # =================================================================
        card_normalized_loads: Dict[int, float] = {}
        card_states: Dict[int, CardState] = {}
        
        critical_cards: List["Card"] = []
        available_cards: List["Card"] = []
        
        for card in self.cards:
            # Get normalized card load: L_m^sched = L_raw / C_capacity
            raw_load = self._card_epoch_load.get(card.card_id, 0.0)
            normalized_load = self._get_normalized_card_load(card)
            card_normalized_loads[card.card_id] = normalized_load
            
            # Classify card state
            state = self._classify_card(normalized_load)
            card_states[card.card_id] = state
            
            logger.info(
                "Card %s: EpochLoad = %.2f (Normalized = %.2f, State = %s, Tasks: %s)",
                card.card_id,
                raw_load,
                normalized_load,
                state.value,
                len(card.tasks),
            )
            
            if state == CardState.CRITICAL:
                critical_cards.append(card)
            elif state == CardState.AVAILABLE:
                available_cards.append(card)
        
        if not critical_cards:
            logger.info(">> System is stable. No migration needed.")
            return
        
        if not available_cards:
            logger.info(">> System is saturated. No AVAILABLE cards for migration.")
            return
        
        assert available_cards, "There should be AVAILABLE cards for migration"
        # =================================================================
        # Phase 2: Decision - ROI-Greedy task selection
        # =================================================================
        for source_card in critical_cards:
            # Exit early if no more AVAILABLE cards to accept migrations
            if not available_cards:
                logger.info("   >>> out For: No more AVAILABLE cards; stopping migration.")
                break
            
            logger.info(">> Analyzing CRITICAL Card %s...", source_card.card_id)
            
            source_load = card_normalized_loads[source_card.card_id]
            
            # Skip if only one active task
            if len(source_card.tasks) == 1:
                task = source_card.tasks[0]
                # Only skip if the task is actually active (has positive spike count)
                if task.current_spike_count > 0:
                    logger.info("   Only one task present and it is active; skipping migration.")
                    continue
                else:
                    logger.info("   Single task on card is inactive (spike_count=0); treating as idle.")
            
            # Calculate ROI for all tasks on this card
            task_rois: List[TaskROI] = []
            for task in source_card.tasks:
                # Skip tasks that have finished (spike_count == 0)
                # These are already completed or will be removed in next step
                if task.current_spike_count == 0:
                    logger.info("   Task %s is inactive (spike_count=0); skipping from migration pool.", task.task_id)
                    continue
                
                roi = self._calculate_efficiency_score(task)
                task_rois.append(roi)
            
            # Sort by efficiency score (highest first) - Greedy selection
            task_rois.sort(key=lambda r: r.efficiency_score, reverse=True)
            
            # If no active tasks to migrate, skip this card
            if not task_rois:
                logger.info("   All tasks on this card are inactive; cannot perform migration.")
                continue
            
            # Calculate migration target: reduce to Θ_safe
            delta_target = source_load - self.THETA_SAFE
            if delta_target <= 0:
                logger.info("   Load already below Θ_safe; no migration needed.")
                continue
            
            logger.info(
                "   Migration target: reduce normalized load by %.3f (%.2f -> %.2f)",
                delta_target,
                source_load,
                self.THETA_SAFE,
            )
            
            # Knapsack-like selection: accumulate until target reached
            accumulated_load = 0.0
            tasks_to_migrate: List[TaskROI] = []
            
            for roi in task_rois:
                # Normalize task epoch load for comparison
                normalized_task_load = roi.epoch_load / self.card_capacity
                
                logger.info(
                    "   Candidate Task %s (Efficiency=%.2f, EpochLoad=%.1f, StateSize=%.1fMB, NormalizedLoad=%.3f)",
                    roi.task.task_id,
                    roi.efficiency_score,
                    roi.epoch_load,
                    roi.state_size_mb,
                    normalized_task_load,
                )
                
                tasks_to_migrate.append(roi)
                accumulated_load += normalized_task_load
                
                if accumulated_load >= delta_target:
                    break
            
            # =================================================================
            # Phase 3: Execution - Perform migrations with strategy allocation
            # =================================================================
            for roi in tasks_to_migrate:
                task = roi.task
                normalized_task_load = roi.epoch_load / self.card_capacity
                
                # Use placement strategy for migration target selection
                target_card = self._placement_strategy.select_migration_target(
                    task=task,
                    candidate_cards=available_cards,
                    task_load=normalized_task_load,
                    card_loads=card_normalized_loads,
                    load_threshold=self.THETA_HIGH,
                )
                
                if target_card is None:
                    logger.info(
                        "   >>> [MIGRATION] No suitable target for Task %s; skip",
                        task.task_id,
                    )
                    continue
                
                # Execute migration with resource constraint check
                success = self._execute_migration(task, source_card, target_card, time_step)
                
                if success:
                    # Update epoch load tracking to reflect migration
                    task_epoch_load = self._task_epoch_load.get(task.task_id, 0.0)
                    self._card_epoch_load[source_card.card_id] -= task_epoch_load
                    self._card_epoch_load[target_card.card_id] += task_epoch_load
                    
                    # Update normalized load estimates
                    card_normalized_loads[source_card.card_id] -= normalized_task_load
                    card_normalized_loads[target_card.card_id] += normalized_task_load
                    
                    logger.info(
                        "   >>> [MIGRATION] Target Card %s, Normalized Load rise to: %.3f",
                        target_card.card_id,
                        card_normalized_loads[target_card.card_id],
                    )
                    logger.info(
                        "   >>> [MIGRATION] Source Card %s, Normalized Load drop to: %.3f",
                        source_card.card_id,
                        card_normalized_loads[source_card.card_id],
                    )
                    
                    
                    # Re-classify target card; remove if no longer AVAILABLE
                    new_target_state = self._classify_card(card_normalized_loads[target_card.card_id])
                    if new_target_state != CardState.AVAILABLE:
                        available_cards.remove(target_card)

                    if not available_cards:
                        logger.info("   >>> inner For: No more AVAILABLE cards; stopping migration.")
                        break
        # Note: epoch loads are NOT reset here.
        # Engine will call reset_epoch_loads() after recording metrics.


# Register GLaSS in the scheduler registry
register_scheduler("glass", GLaSS)

