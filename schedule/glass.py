"""GG (GLaSS-Greedy): Global Load-aware SNN Scheduler with ROI-Greedy strategy.

Implementation based on algorithm.md specification:
- Two-Tier Timing Architecture (physical ~1ms, scheduler ~500ms epoch)
- Accumulated load over epoch window (not P95)
- Fixed dual-hysteresis thresholds (Θ_high=0.85, Θ_low=0.60, Θ_safe=0.75)
- ROI-Greedy migration strategy with Best-Fit target allocation
"""
from __future__ import annotations

import logging
import math
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
    GG (GLaSS-Greedy): Global Load-aware SNN Scheduler with ROI-Greedy strategy.
    
    A dynamic load balancing algorithm designed for neuromorphic computing clusters.
    
    Key Features (from algorithm.md):
    - Two-tier timing: physical layer (~1ms) vs scheduler layer (~500ms epoch)
    - Accumulated load over epoch for scheduling decisions
    - Fixed dual-hysteresis thresholds for stability
    - ROI-Greedy strategy: prioritize high-load but light-state tasks
    - Best-Fit target allocation to maximize fragmented resource usage
    
    Load Definitions:
    - L_comp(i,t) = (N_active·tau_update + S_in·tau_synapse) / T_tick
    - L_comm(i,t) = Σ(λ_k·FanOut_k·D_hops(k)) for tasks on card i
    - L_node(i,t) = α·L_comp(i,t) + β·Sigmoid(L_comm(i,t))
    - L_i(T_epoch) = Σ L_node(i,t) over physical ticks in one scheduler epoch
    
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
        tau_update: float = 1.0,
        tau_synapse: float = 0.01,
        tick_duration: float = 1.0,
        **kwargs,
    ):
        """
        Initialize GG (GLaSS-Greedy) scheduler.
        
        Args:
            cards: List of neuromorphic cards
            alpha: Weight for L_comp in L_node calculation
            beta: Weight for Sigmoid(L_comm) in L_node calculation
            card_capacity: Max capacity constant for normalization (C_capacity)
            gamma: Heat preference factor for ROI calculation (default 1.5)
            tau_update: Processing time constant per active neuron
            tau_synapse: Processing time constant per synaptic input
            tick_duration: Physical time tick duration (T_tick)
        """
        super().__init__(cards, alpha, beta, **kwargs)
        self.gamma = gamma
        self.card_capacity = card_capacity
        self.tau_update = tau_update
        self.tau_synapse = tau_synapse
        self.tick_duration = max(tick_duration, 1e-6)
        # Per-task accumulated load over current epoch: task_id -> L_i(T_epoch)
        self._task_epoch_load: Dict[int, float] = {}
        
        # Per-card accumulated load over current epoch: card_id -> L_raw(m, T_epoch)
        self._card_epoch_load: Dict[int, float] = {
            card.card_id: 0.0 for card in cards
        }
        
        # Per-card accumulated communication load over current epoch
        self._card_epoch_comm_load: Dict[int, float] = {
            card.card_id: 0.0 for card in cards
        }
        
        logger.info(
            "GG Load bands | Θ_high=%.2f Θ_low=%.2f Θ_safe=%.2f capacity=%.0f | alpha=%.3f beta=%.3f",
            self.THETA_HIGH,
            self.THETA_LOW,
            self.THETA_SAFE,
            self.card_capacity,
            self.alpha,
            self.beta,
        )
    
    @property
    def name(self) -> str:
        return "Glass"
    
    def get_epoch_loads(self) -> Dict[int, float]:
        """
        Get accumulated load for each card over the current epoch.
        
        Overrides BaseScheduler to return GG's accumulated epoch loads
        instead of instantaneous loads.
        
        Returns:
            Dictionary mapping card_id to accumulated epoch load L_node(m, T_epoch).
        """
        return dict(self._card_epoch_load)

    @staticmethod
    def _sigmoid(value: float) -> float:
        """Numerically stable congestion factor in [0, 1] with zero baseline."""
        clipped = max(min(value, 60.0), -60.0)
        # Shift sigmoid so L_comm=0 maps to 0 instead of 0.5.
        return max(0.0, (2.0 / (1.0 + math.exp(-clipped))) - 1.0)
    
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
        for card_id in self._card_epoch_comm_load:
            self._card_epoch_comm_load[card_id] = 0.0
    
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
        E_i = (L_i^epoch)^γ / (S_state + ε)
        
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
        
        Per physical tick, we compute card-level composite load:
        - L_comp(i,t) = (N_active·tau_update + S_in·tau_synapse) / T_tick
        - L_comm(i,t) = Σ(λ_k·FanOut_k·D_hops(k))
        - L_node(i,t) = α·L_comp(i,t) + β·Sigmoid(L_comm(i,t))
        
        Args:
            scheduler_step: Current scheduler time step
        """
        for card in self.cards:
            card_comp_load = 0.0
            card_comm_load = 0.0
            
            for task in card.tasks:
                # L_comp task contribution uses active spikes and synaptic inputs.
                task_comp_load = (
                    task.current_spike_count * self.tau_update +
                    task.current_synaptic_ops * self.tau_synapse
                ) / self.tick_duration

                # L_comm task contribution with weighted fan-out and hop distance.
                firing_rate = task.current_spike_count / max(task.neuron_count, 1)
                task_comm_load = firing_rate * task.fan_out * task.avg_hop_distance

                # Task-level proxy for ROI sorting and migration delta estimates.
                task_node_load = (
                    self.alpha * task_comp_load +
                    self.beta * self._sigmoid(task_comm_load)
                )
                
                # Accumulate to task epoch load: L_i(T_epoch) += L_i(t_phy)
                if task.task_id not in self._task_epoch_load:
                    self._task_epoch_load[task.task_id] = 0.0
                self._task_epoch_load[task.task_id] += task_node_load
                
                card_comp_load += task_comp_load
                card_comm_load += task_comm_load
            
            # Card-level composite load for this physical tick.
            card_tick_load = (
                self.alpha * card_comp_load +
                self.beta * self._sigmoid(card_comm_load)
            )
            
            # Accumulate to card epoch load: L_raw(m, T_epoch)
            if card.card_id not in self._card_epoch_load:
                self._card_epoch_load[card.card_id] = 0.0
            self._card_epoch_load[card.card_id] += card_tick_load
            
            # Accumulate communication load for this card
            if card.card_id not in self._card_epoch_comm_load:
                self._card_epoch_comm_load[card.card_id] = 0.0
            self._card_epoch_comm_load[card.card_id] += card_comm_load
            
    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        """
        Select the best card for placing a task.
        
        GG uses the placement strategy (default: Best-Fit).
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
        
        # Phase 1: Sense - Classify cards and identify critical/available sets
        card_normalized_loads, critical_cards, available_cards = self._sense_phase()
        
        if not critical_cards:
            logger.info(">> System is stable. No migration needed.")
            return
        
        if not available_cards:
            logger.info(">> System is saturated. No AVAILABLE cards for migration.")
            return
        
        # Phase 2 & 3: Decision and Execution - Process each critical card
        self._decision_execution_phase(
            time_step, critical_cards, available_cards, card_normalized_loads
        )
    
    def _sense_phase(self) -> tuple[Dict[int, float], List["Card"], List["Card"]]:
        """
        Phase 1: Sense - Classify cards using accumulated epoch loads.
        
        Returns:
            Tuple of (card_normalized_loads, critical_cards, available_cards)
        """
        card_normalized_loads: Dict[int, float] = {}
        critical_cards: List["Card"] = []
        available_cards: List["Card"] = []
        
        for card in self.cards:
            # Get normalized card load: L_m^sched = L_raw / C_capacity
            raw_load = self._card_epoch_load.get(card.card_id, 0.0)
            normalized_load = self._get_normalized_card_load(card)
            card_normalized_loads[card.card_id] = normalized_load
            
            # Classify card state
            state = self._classify_card(normalized_load)
            
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
        
        return card_normalized_loads, critical_cards, available_cards
    
    def _decision_execution_phase(
        self,
        time_step: int,
        critical_cards: List["Card"],
        available_cards: List["Card"],
        card_normalized_loads: Dict[int, float],
    ) -> None:
        """
        Phase 2 & 3: Decision and Execution - ROI-Greedy task selection and migration.
        
        Args:
            time_step: Current simulation time step
            critical_cards: Cards above THETA_HIGH threshold
            available_cards: Cards below THETA_LOW threshold (mutable)
            card_normalized_loads: Current normalized loads per card (mutable)
        """
        for source_card in critical_cards:
            if not available_cards:
                logger.info("   >>> No more AVAILABLE cards; stopping migration.")
                break
            
            logger.info(">> Analyzing CRITICAL Card %s...", source_card.card_id)
            
            # Select tasks to migrate from this source card
            tasks_to_migrate = self._select_tasks_for_migration(
                source_card, card_normalized_loads
            )
            
            if not tasks_to_migrate:
                continue
            
            # Execute migrations for selected tasks
            self._execute_migrations(
                time_step,
                source_card,
                tasks_to_migrate,
                available_cards,
                card_normalized_loads,
            )
    
    def _select_tasks_for_migration(
        self,
        source_card: "Card",
        card_normalized_loads: Dict[int, float],
    ) -> List[TaskROI]:
        """
        Select tasks to migrate from a critical card using ROI-Greedy strategy.
        
        Args:
            source_card: The overloaded card to migrate from
            card_normalized_loads: Current normalized loads per card
            
        Returns:
            List of TaskROI objects for tasks to migrate (sorted by efficiency)
        """
        source_load = card_normalized_loads[source_card.card_id]
        
        # Skip if only one active task
        if len(source_card.tasks) == 1:
            task = source_card.tasks[0]
            if task.current_spike_count > 0:
                logger.info("   Only one task present and it is active; skipping migration.")
                return []
            else:
                logger.info("   Single task on card is inactive (spike_count=0); treating as idle.")
        
        # Calculate ROI for all active tasks on this card
        task_rois: List[TaskROI] = []
        for task in source_card.tasks:
            if task.current_spike_count == 0:
                logger.info(
                    "   Task %s is inactive (spike_count=0); skipping from migration pool.",
                    task.task_id
                )
                continue
            
            roi = self._calculate_efficiency_score(task)
            task_rois.append(roi)
        
        # Sort by efficiency score (highest first) - Greedy selection
        task_rois.sort(key=lambda r: r.efficiency_score, reverse=True)
        
        if not task_rois:
            logger.info("   All tasks on this card are inactive; cannot perform migration.")
            return []
        
        # Calculate migration target: reduce to Θ_safe
        delta_target = source_load - self.THETA_SAFE
        if delta_target <= 0:
            logger.info("   Load already below Θ_safe; no migration needed.")
            return []
        
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
        
        return tasks_to_migrate
    
    def _execute_migrations(
        self,
        time_step: int,
        source_card: "Card",
        tasks_to_migrate: List[TaskROI],
        available_cards: List["Card"],
        card_normalized_loads: Dict[int, float],
    ) -> None:
        """
        Execute migrations for selected tasks.
        
        Args:
            time_step: Current simulation time step
            source_card: Source card to migrate from
            tasks_to_migrate: List of TaskROI objects to migrate
            available_cards: List of available target cards (mutable)
            card_normalized_loads: Current normalized loads per card (mutable)
        """
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
                self._update_load_tracking_after_migration(
                    task, source_card, target_card, normalized_task_load,
                    card_normalized_loads,
                )
                
                # Re-classify target card; remove if no longer AVAILABLE
                new_target_state = self._classify_card(
                    card_normalized_loads[target_card.card_id]
                )
                if new_target_state != CardState.AVAILABLE:
                    available_cards.remove(target_card)

                if not available_cards:
                    logger.info("   >>> No more AVAILABLE cards; stopping migration.")
                    break
    
    def _update_load_tracking_after_migration(
        self,
        task: "Task",
        source_card: "Card",
        target_card: "Card",
        normalized_task_load: float,
        card_normalized_loads: Dict[int, float],
    ) -> None:
        """
        Update internal load tracking after a successful migration.
        
        Args:
            task: The migrated task
            source_card: Card the task migrated from
            target_card: Card the task migrated to
            normalized_task_load: Normalized load of the migrated task
            card_normalized_loads: Current normalized loads per card (mutable)
        """
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


# Register GLaSS in the scheduler registry
register_scheduler("glass", GLaSS)

