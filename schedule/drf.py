"""DRF: Dominant Resource Fairness scheduler.

Place tasks to minimize the post-placement maximum utilization across
{cores, synapses, memory}.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from .base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class DRF(BaseScheduler):
    """Static DRF: pick the card minimizing dominant resource utilization."""

    @property
    def name(self) -> str:
        return "DRF"

    def _get_dominant_utilization(self, card: "Card", task: "Task") -> float:
        used_neurons = sum(t.neuron_count for t in card.tasks) + task.neuron_count
        used_memory = sum(t.memory_gb_required for t in card.tasks) + task.memory_gb_required
        return max(
            used_neurons / card.neuron_capacity if card.neuron_capacity > 0 else 0.0,
            used_memory / card.memory_gb if card.memory_gb > 0 else 0.0,
        )

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        best_card = None
        min_dominant_util = float("inf")
        for card in self.cards:
            if not card.can_host(task):
                continue
            dominant_util = self._get_dominant_utilization(card, task)
            if dominant_util < min_dominant_util:
                min_dominant_util = dominant_util
                best_card = card
        return best_card

    def step(self, time_step: int) -> None:
        pass


register_scheduler("drf", DRF)
