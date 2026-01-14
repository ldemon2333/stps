"""Gandiva-Spike: Smallest-First Dynamic Load Balancing Baseline.

A dynamic baseline scheduler for comparison with GLaSS. It inherits from GLaSS
and only differs in the migration task selection strategy:
- GLaSS: ROI-Greedy (prioritize high-load tasks with small state)
- Gandiva-Spike: Smallest-First (prioritize low-load tasks)

This design minimizes code changes while providing a controlled comparison
that isolates the effect of the migration selection strategy.

Reference: Gandiva-Spike.md in docs/
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List

from .base import register_scheduler
from .glass import GLaSS, CardState

if TYPE_CHECKING:
    from util.card import Card

logger = logging.getLogger(__name__)


class GandivaSpike(GLaSS):
    """
    Gandiva-Spike: Smallest-First Dynamic Scheduler Baseline.
    
    Inherits from GLaSS and reuses:
    - Accumulated load tracking (_task_epoch_load, _card_epoch_load)
    - record_physical_tick() method
    - reset_epoch_loads() method  
    - _classify_card() method
    - _execute_migration() method
    - _placement_strategy (Best-Fit by default)
    - Same thresholds (Θ_high=0.85, Θ_low=0.60, Θ_safe=0.75)
    
    Only overrides:
    - step(): Smallest-First task selection instead of ROI-Greedy
    
    The key difference is in Phase 2 (Decision):
    - GLaSS sorts tasks by efficiency_score DESCENDING (high-load first)
    - Gandiva-Spike sorts tasks by epoch_load ASCENDING (low-load first)
    """
    
    @property
    def name(self) -> str:
        return "GandivaSpike"
    
    def step(self, time_step: int) -> None:
        """
        Perform global load balancing using Smallest-First strategy.
        
        Three-phase algorithm (same structure as GLaSS):
        1. Sense Phase: Classify cards (CRITICAL/AVAILABLE/STABLE)
        2. Decision Phase: Smallest-First task selection [KEY DIFFERENCE]
        3. Execution Phase: Best-Fit target allocation
        """
        logger.info("--- Time Step %s Global Balancing (GandivaSpike) ---", time_step)
        
        # =================================================================
        # Phase 1: Sense - Classify cards (identical to GLaSS)
        # =================================================================
        card_normalized_loads: Dict[int, float] = {}
        critical_cards: List["Card"] = []
        available_cards: List["Card"] = []
        
        for card in self.cards:
            raw_load = self._card_epoch_load.get(card.card_id, 0.0)
            normalized_load = self._get_normalized_card_load(card)
            card_normalized_loads[card.card_id] = normalized_load
            
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
        
        if not critical_cards:
            logger.info(">> System is stable. No migration needed.")
            return
        
        if not available_cards:
            logger.info(">> System is saturated. No AVAILABLE cards for migration.")
            return
        
        # =================================================================
        # Phase 2: Decision - Smallest-First task selection [KEY DIFFERENCE]
        # =================================================================
        for source_card in critical_cards:
            if not available_cards:
                logger.info("   >>> No more AVAILABLE cards; stopping migration.")
                break
            
            logger.info(">> Analyzing CRITICAL Card %s (Smallest-First)...", source_card.card_id)
            
            source_load = card_normalized_loads[source_card.card_id]
            
            # Skip if only one active task
            if len(source_card.tasks) == 1:
                task = source_card.tasks[0]
                if task.current_spike_count > 0:
                    logger.info("   Only one active task present; skipping migration.")
                    continue
            
            # Get active tasks (skip finished tasks with spike_count == 0)
            active_tasks = [
                task for task in source_card.tasks
                if task.current_spike_count > 0
            ]
            
            if not active_tasks:
                logger.info("   All tasks on this card are inactive; cannot perform migration.")
                continue
            
            # =================================================================
            # [KEY DIFFERENCE] Sort by epoch load ASCENDING (smallest first)
            # GLaSS uses efficiency_score DESCENDING (ROI-Greedy)
            # =================================================================
            sorted_tasks = sorted(
                active_tasks,
                key=lambda t: self._get_task_epoch_load(t),
                reverse=False  # ASCENDING: smallest load first
            )
            
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
            
            # Greedy selection: accumulate small tasks until target reached
            accumulated_load = 0.0
            tasks_to_migrate = []
            
            for task in sorted_tasks:
                task_epoch_load = self._get_task_epoch_load(task)
                normalized_task_load = task_epoch_load / self.card_capacity
                
                logger.info(
                    "   Candidate Task %s (EpochLoad=%.1f, NormalizedLoad=%.3f, StateSize=%.1fMB) [Smallest-First]",
                    task.task_id,
                    task_epoch_load,
                    normalized_task_load,
                    task.state_size_mb,
                )
                
                tasks_to_migrate.append((task, normalized_task_load))
                accumulated_load += normalized_task_load
                
                if accumulated_load >= delta_target:
                    break
            
            logger.info(
                "   Selected %d tasks for migration (accumulated=%.3f, target=%.3f)",
                len(tasks_to_migrate),
                accumulated_load,
                delta_target,
            )
            
            # =================================================================
            # Phase 3: Execution - Best-Fit target allocation (same as GLaSS)
            # =================================================================
            for task, normalized_task_load in tasks_to_migrate:
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
                    # Update epoch load tracking
                    task_epoch_load = self._task_epoch_load.get(task.task_id, 0.0)
                    self._card_epoch_load[source_card.card_id] -= task_epoch_load
                    self._card_epoch_load[target_card.card_id] += task_epoch_load
                    
                    # Update normalized load estimates
                    card_normalized_loads[source_card.card_id] -= normalized_task_load
                    card_normalized_loads[target_card.card_id] += normalized_task_load
                    
                    logger.info(
                        "   >>> [MIGRATION] Task %s: Card %s -> Card %s",
                        task.task_id,
                        source_card.card_id,
                        target_card.card_id,
                    )
                    logger.info(
                        "   >>> Source Card %s load: %.3f, Target Card %s load: %.3f",
                        source_card.card_id,
                        card_normalized_loads[source_card.card_id],
                        target_card.card_id,
                        card_normalized_loads[target_card.card_id],
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


# Register Gandiva-Spike in the scheduler registry
register_scheduler("gandiva", GandivaSpike)
