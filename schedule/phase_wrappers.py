"""Reusable phase-shift scheduler variants for Q2 vertical ablations."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from fingerprint import Fingerprint
from schedule.base import register_scheduler
from schedule.bestfit import BestFitScheduler
from schedule.drf import DRF
from schedule.p2c import P2C
from schedule.phase_shift import find_optimal_offset
from schedule.roundrobin import RoundRobin

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task

logger = logging.getLogger(__name__)


class PhaseShiftMixin:
    """Add bounded temporal phase-shift after a base scheduler picks one card."""

    phase_name = "phase"

    def __init__(
        self,
        *args,
        horizon: int = 64,
        d_max: int = 16,
        bw_max: float = 1e9,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.horizon = int(horizon)
        self.d_max = int(d_max)
        self.bw_max = float(bw_max)
        for card in self.cards:
            card.ensure_forecast(self.horizon)

    @property
    def name(self) -> str:
        return self.phase_name

    def step(self, time_step: int) -> None:
        super().step(time_step)
        for card in self.cards:
            card.advance_forecast()

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        chosen = super().select_card_for_task(task)
        if chosen is None:
            return None

        fp = self._resolve_fingerprint(task)
        if fp is None:
            return chosen

        chosen.ensure_forecast(self.horizon)
        forecast = chosen.forecast
        assert forecast is not None
        offset, peak = find_optimal_offset(
            forecast,
            fp.traffic_sequence,
            self.d_max,
            self.bw_max,
        )
        if peak > self.bw_max:
            logger.info(
                "[%s] Task %s rejected: best peak %.2f exceeds BW_max %.2f",
                self.phase_name,
                task.task_id,
                peak,
                self.bw_max,
            )
            task.rejected = True
            task.reject_reason = "bw_max_exceeded"
            return None

        task.start_offset = int(offset)
        chosen.add_forecast(fp.traffic_sequence, int(offset))
        return chosen

    def _resolve_fingerprint(self, task: "Task") -> Optional[Fingerprint]:
        if task.fingerprint is not None:
            assert isinstance(task.fingerprint, Fingerprint)
            return task.fingerprint
        path = getattr(task, "fingerprint_path", None)
        if path is None:
            return None
        try:
            from fingerprint import load_fingerprint
            fp = load_fingerprint(path)
        except Exception as exc:
            logger.warning("[%s] Failed to load fingerprint %s: %s", self.phase_name, path, exc)
            return None
        task.fingerprint = fp
        return fp


class RoundRobinPhaseScheduler(PhaseShiftMixin, RoundRobin):
    phase_name = "rr-phase"


class BestFitPhaseScheduler(PhaseShiftMixin, BestFitScheduler):
    phase_name = "bestfit-phase"


class DRFPhaseScheduler(PhaseShiftMixin, DRF):
    phase_name = "drf-phase"


class P2CPhaseScheduler(PhaseShiftMixin, P2C):
    phase_name = "p2c-phase"


register_scheduler("rr-phase", RoundRobinPhaseScheduler)
register_scheduler("bestfit-phase", BestFitPhaseScheduler)
register_scheduler("drf-phase", DRFPhaseScheduler)
register_scheduler("p2c-phase", P2CPhaseScheduler)
