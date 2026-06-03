"""Best-Fit scheduler — static baseline with no migrations."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from schedule.base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class BestFitScheduler(BaseScheduler):
    """Static Best-Fit placement; accumulates fingerprint-driven epoch loads for metrics."""

    @property
    def name(self) -> str:
        return "BestFit"

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        eligible = [c for c in self.cards if c.can_host(task)]
        if not eligible:
            return None

        def remaining_capacity(card: "Card") -> tuple:
            used_neurons = sum(t.neuron_count for t in card.tasks)
            used_mem = sum(t.memory_gb_required for t in card.tasks)
            return (
                card.neuron_capacity - used_neurons,
                card.memory_gb - used_mem,
                -len(card.tasks),
            )

        return max(eligible, key=remaining_capacity)

    def step(self, time_step: int) -> None:
        pass


register_scheduler("bestfit", BestFitScheduler)
