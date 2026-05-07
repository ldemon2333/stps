"""Round-Robin static scheduler."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from .base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class RoundRobin(BaseScheduler):
    """Place tasks on cards in circular order; static, no migrations."""

    def __init__(self, cards: List["Card"], **kwargs):
        super().__init__(cards, **kwargs)
        self._current_index: int = 0

    @property
    def name(self) -> str:
        return "RoundRobin"

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        if not self.cards:
            return None

        num_cards = len(self.cards)
        start_index = self._current_index

        for i in range(num_cards):
            candidate_index = (start_index + i) % num_cards
            candidate = self.cards[candidate_index]
            if candidate.can_host(task):
                self._current_index = (candidate_index + 1) % num_cards
                return candidate

        return None

    def step(self, time_step: int) -> None:
        pass


register_scheduler("roundrobin", RoundRobin)
register_scheduler("rr", RoundRobin)
