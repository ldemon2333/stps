"""P2C: Power of Two Choices scheduler.

Sample 2 eligible cards uniformly at random; place on the lighter one,
where "lighter" is the cluster's accumulated epoch traffic plus the
incoming task's mean per-tick fingerprint traffic.
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Optional

from .base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class P2C(BaseScheduler):
    """Randomized power-of-two-choices placement."""

    @property
    def name(self) -> str:
        return "P2C"

    def _score(self, card: "Card", task: "Task") -> float:
        load = self.cluster_epoch_loads.get(card.card_id, 0.0)
        fp = task.fingerprint
        task_traffic = float(fp.traffic_sequence.mean()) if fp is not None and fp.traffic_sequence.size else 0.0
        return load + task_traffic

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        eligible = [c for c in self.cards if c.can_host(task)]
        if not eligible:
            return None
        if len(eligible) == 1:
            return eligible[0]
        candidates = random.sample(eligible, 2)
        return min(candidates, key=lambda c: self._score(c, task))

    def step(self, time_step: int) -> None:
        pass


register_scheduler("p2c", P2C)
