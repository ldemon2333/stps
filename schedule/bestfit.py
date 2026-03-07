"""Best-Fit scheduler - static baseline with no migrations."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from schedule.base import BaseScheduler, register_scheduler

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task
    


logger = logging.getLogger(__name__)


class BestFitScheduler(BaseScheduler):
    """
    Best-Fit static scheduler (baseline for comparison).
    
    This scheduler performs no load balancing or migrations after initial placement.
    Tasks are placed using Best-Fit strategy (select card with most remaining resources).
    
    Unlike the base class, this scheduler tracks accumulated epoch loads
    (same as GG/GLaSS) to ensure fair comparison in metrics.
    """
    
    def __init__(
        self,
        cards: List["Card"],
        alpha: float = 1.0,
        beta: float = 0.01,
        **kwargs,
    ):
        """Initialize Best-Fit scheduler with epoch load tracking."""
        super().__init__(cards, alpha, beta, **kwargs)
        
        # Per-card accumulated load over current epoch: card_id -> L_raw(m, T_epoch)
        self._card_epoch_load: Dict[int, float] = {
            card.card_id: 0.0 for card in cards
        }
    
    @property
    def name(self) -> str:
        return "BestFit"
    
    def record_physical_tick(self, scheduler_step: int) -> None:
        """
        Accumulate load samples during physical layer ticks.
        
        Same as GG/GLaSS: accumulates instantaneous loads over the epoch
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
        """Best-Fit scheduler does nothing - no migrations."""
        pass


# Register the scheduler
register_scheduler("bestfit", BestFitScheduler)
