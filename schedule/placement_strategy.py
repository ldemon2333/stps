"""Task placement strategies for scheduling algorithms.

This module defines abstract and concrete placement strategies that can be
reused across different schedulers, enabling flexible composition and code reuse.

Strategies support two key operations:
1. select_card(): Initial task placement (used at task arrival)
2. select_migration_target(): Migration target selection (used by dynamic schedulers)
"""
from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class PlacementStrategy(ABC):
    """Abstract base class for task placement strategies."""
    
    def __init__(self, cards: List["Card"], alpha: float = 1.0, beta: float = 0.01):
        """
        Initialize placement strategy.
        
        Args:
            cards: List of available cards
            alpha: Weight for spike count
            beta: Weight for synaptic operations
        """
        self.cards = cards
        self.alpha = alpha
        self.beta = beta
    
    @abstractmethod
    def select_card(self, task: "Task") -> Optional["Card"]:
        """
        Select a card for task placement (initial placement).
        
        Args:
            task: Task to place
            
        Returns:
            Selected card, or None if no suitable card found
        """
        raise NotImplementedError
    
    def select_migration_target(
        self,
        task: "Task",
        candidate_cards: List["Card"],
        task_load: float = 0.0,
        card_loads: Optional[Dict[int, float]] = None,
        load_threshold: float = 0.85,
    ) -> Optional["Card"]:
        """
        Select a target card for task migration.
        
        Default implementation filters by capacity and delegates to select_card logic.
        Subclasses can override for custom migration behavior.
        
        Args:
            task: Task to migrate
            candidate_cards: List of candidate target cards (e.g., AVAILABLE cards)
            task_load: Normalized load of the task being migrated
            card_loads: Dict mapping card_id to current normalized load
            load_threshold: Maximum load threshold (e.g., THETA_HIGH)
            
        Returns:
            Selected target card, or None if no suitable target
        """
        if not candidate_cards:
            return None
        
        card_loads = card_loads or {}
        
        # Filter cards that can host the task and have enough headroom
        eligible = []
        for card in candidate_cards:
            if not card.can_host(task):
                continue
            
            current_load = card_loads.get(card.card_id, 0.0)
            headroom = load_threshold - current_load
            
            if headroom >= task_load:
                eligible.append(card)
        
        if not eligible:
            return None
        
        # Temporarily swap cards list and delegate to select_card logic
        original_cards = self.cards
        self.cards = eligible
        try:
            return self.select_card(task)
        finally:
            self.cards = original_cards
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(cards={len(self.cards)})"


class BestFitStrategy(PlacementStrategy):
    """
    Best-Fit placement strategy.
    
    Select the card with most remaining resources that can host the task.
    Sorting priority: cores > memory > synapses > task_count.
    
    For migration: selects card that best "fills the fragmented space"
    (minimizes remaining headroom after placement).
    """
    
    def select_card(self, task: "Task") -> Optional["Card"]:
        """Select card with most remaining capacity."""
        eligible = [c for c in self.cards if c.can_host(task)]
        if not eligible:
            return None
        
        def remaining_capacity(card: "Card") -> tuple:
            """Compute remaining capacity tuple for comparison."""
            used_cores = sum(t.cores_required for t in card.tasks)
            used_mem = sum(t.memory_gb_required for t in card.tasks)
            used_syn = sum(t.synapses_required for t in card.tasks)
            return (
                card.cores - used_cores,
                card.memory_gb - used_mem,
                card.synapses - used_syn,
                -len(card.tasks),  # Prefer cards with fewer tasks
            )
        
        return max(eligible, key=remaining_capacity)
    
    def select_migration_target(
        self,
        task: "Task",
        candidate_cards: List["Card"],
        task_load: float = 0.0,
        card_loads: Optional[Dict[int, float]] = None,
        load_threshold: float = 0.85,
    ) -> Optional["Card"]:
        """
        Select migration target using Best-Fit strategy.
        
        Based on algorithm.md Section 3.2 Step 3:
        k* = argmin_k |( Θ_high - L_k ) - L_task|
        
        This finds the card that best "fills the fragmented space" rather than
        the emptiest card, preventing Ping-Pong effect.
        """
        if not candidate_cards:
            return None
        
        card_loads = card_loads or {}
        
        best_card = None
        best_fit_score = float('inf')
        
        for card in candidate_cards:
            if not card.can_host(task):
                continue
            
            current_load = card_loads.get(card.card_id, 0.0)
            headroom = load_threshold - current_load
            
            if headroom < task_load:
                continue  # Cannot fit
            
            # Best-Fit: minimize remaining headroom after placement
            fit_score = headroom - task_load
            
            if fit_score < best_fit_score:
                best_fit_score = fit_score
                best_card = card
        
        return best_card


class P2CStrategy(PlacementStrategy):
    """
    Power of Two Choices (P2C) placement strategy.
    
    Randomly sample 2 eligible cards and pick the one with lower load.
    Provides near-optimal load balancing with O(1) time complexity.
    """
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        load_metric: str = "weighted",
        epoch_loads: Optional[dict] = None,
    ):
        """
        Initialize P2C strategy.
        
        Args:
            cards: List of available cards
            alpha: Weight for spike count
            beta: Weight for synaptic operations
            load_metric: Load comparison method ("weighted", "drf", "tasks")
            epoch_loads: Optional dict mapping card_id to accumulated epoch load
        """
        super().__init__(cards, alpha, beta)
        self.load_metric = load_metric
        self.epoch_loads = epoch_loads or {}
    
    def _calculate_load_score(
        self,
        card: "Card",
        task: Optional["Task"] = None,
    ) -> float:
        """Calculate load score for a card (lower is better)."""
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
        """Calculate weighted load score (α * spikes + β * ops)."""
        # Current epoch load
        score = self.epoch_loads.get(card.card_id, 0.0)
        
        # Add task's estimated contribution if provided
        if task is not None:
            estimated_spikes = task.neuron_count * 0.5
            estimated_ops = task.neuron_count * task.complexity_ratio * 100
            score += self.alpha * estimated_spikes + self.beta * estimated_ops
        
        return score
    
    def _calculate_drf_score(
        self,
        card: "Card",
        task: Optional["Task"] = None,
    ) -> float:
        """Calculate DRF-style dominant resource utilization."""
        used_cores = sum(t.cores_required for t in card.tasks)
        used_synapses = sum(t.synapses_required for t in card.tasks)
        used_memory = sum(t.memory_gb_required for t in card.tasks)
        
        if task is not None:
            used_cores += task.cores_required
            used_synapses += task.synapses_required
            used_memory += task.memory_gb_required
        
        util_cores = used_cores / card.cores if card.cores > 0 else 0.0
        util_synapses = used_synapses / card.synapses if card.synapses > 0 else 0.0
        util_memory = used_memory / card.memory_gb if card.memory_gb > 0 else 0.0
        
        return max(util_cores, util_synapses, util_memory)
    
    def select_card(self, task: "Task") -> Optional["Card"]:
        """Select card using Power of Two Choices."""
        eligible = [c for c in self.cards if c.can_host(task)]
        
        if not eligible:
            return None
        
        if len(eligible) == 1:
            return eligible[0]
        
        # Randomly sample 2 candidates
        candidates = random.sample(eligible, min(2, len(eligible)))
        
        # Select card with minimum score
        best_card = min(candidates, key=lambda c: self._calculate_load_score(c, task))
        return best_card
    
    def select_migration_target(
        self,
        task: "Task",
        candidate_cards: List["Card"],
        task_load: float = 0.0,
        card_loads: Optional[Dict[int, float]] = None,
        load_threshold: float = 0.85,
    ) -> Optional["Card"]:
        """
        Select migration target using Power of Two Choices.
        
        Randomly samples 2 eligible cards and picks the one with lower load.
        """
        if not candidate_cards:
            return None
        
        card_loads = card_loads or {}
        
        # Filter eligible cards
        eligible = []
        for card in candidate_cards:
            if not card.can_host(task):
                continue
            
            current_load = card_loads.get(card.card_id, 0.0)
            headroom = load_threshold - current_load
            
            if headroom >= task_load:
                eligible.append(card)
        
        if not eligible:
            return None
        
        if len(eligible) == 1:
            return eligible[0]
        
        # Randomly sample 2 candidates
        candidates = random.sample(eligible, min(2, len(eligible)))
        
        # Select card with minimum load (from card_loads)
        best_card = min(candidates, key=lambda c: card_loads.get(c.card_id, 0.0))
        return best_card


class RoundRobinStrategy(PlacementStrategy):
    """
    Round-Robin placement strategy.
    
    Try cards in circular order, placing task on first available card.
    """
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
    ):
        """Initialize Round-Robin strategy."""
        super().__init__(cards, alpha, beta)
        self._current_index = 0
    
    def select_card(self, task: "Task") -> Optional["Card"]:
        """Select card using round-robin order."""
        if not self.cards:
            return None
        
        num_cards = len(self.cards)
        start_index = self._current_index
        
        # Try each card starting from current position
        for i in range(num_cards):
            candidate_index = (start_index + i) % num_cards
            candidate = self.cards[candidate_index]
            
            if candidate.can_host(task):
                # Update pointer to next card for next placement
                self._current_index = (candidate_index + 1) % num_cards
                return candidate
        
        return None


class DRFStrategy(PlacementStrategy):
    """
    Dominant Resource Fairness (DRF) placement strategy.
    
    Select card that minimizes dominant resource utilization
    after placing the task.
    """
    
    def select_card(self, task: "Task") -> Optional["Card"]:
        """Select card using DRF strategy."""
        best_card = None
        min_dominant_util = float("inf")
        
        for card in self.cards:
            if not card.can_host(task):
                continue
            
            # Calculate dominant utilization after placement
            dominant_util = self._get_dominant_utilization(card, task)
            
            if dominant_util < min_dominant_util:
                min_dominant_util = dominant_util
                best_card = card
        
        return best_card
    
    def _get_dominant_utilization(
        self,
        card: "Card",
        task: "Task",
    ) -> float:
        """Calculate dominant utilization after placing task on card."""
        # Calculate resource utilization after placing the task
        cores_util = (
            (sum(t.cores_required for t in card.tasks) + task.cores_required)
            / card.cores
        )
        synapses_util = (
            (sum(t.synapses_required for t in card.tasks) + task.synapses_required)
            / card.synapses
        )
        memory_util = (
            (sum(t.memory_gb_required for t in card.tasks) + task.memory_gb_required)
            / card.memory_gb
        )
        
        # Return the maximum (dominant) utilization
        return max(cores_util, synapses_util, memory_util)
    
    def select_migration_target(
        self,
        task: "Task",
        candidate_cards: List["Card"],
        task_load: float = 0.0,
        card_loads: Optional[Dict[int, float]] = None,
        load_threshold: float = 0.85,
    ) -> Optional["Card"]:
        """
        Select migration target using DRF strategy.
        
        Minimizes dominant resource utilization after placing the task.
        """
        if not candidate_cards:
            return None
        
        card_loads = card_loads or {}
        
        best_card = None
        min_dominant_util = float("inf")
        
        for card in candidate_cards:
            if not card.can_host(task):
                continue
            
            # Check load headroom
            current_load = card_loads.get(card.card_id, 0.0)
            headroom = load_threshold - current_load
            
            if headroom < task_load:
                continue
            
            # Calculate dominant utilization after placement
            dominant_util = self._get_dominant_utilization(card, task)
            
            if dominant_util < min_dominant_util:
                min_dominant_util = dominant_util
                best_card = card
        
        return best_card
