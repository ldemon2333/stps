"""STPS (Spatio-Temporal Proactive Scheduling) — paper §4.3.

Three-stage hierarchical pipeline:
    Stage 1: Macro-Card Dispatching (fragmentation + temporal isolation)
    Stage 2: Micro-Temporal Phase-Shifting (Algorithm 1)
    Stage 3: Micro-Spatial Mapping with Hotspot Splitting

Two ablation variants are exported alongside the full scheduler:
    STPSSpatialScheduler  — Stage 1 + Stage 3 only (no phase shifting).
    STPSTemporalScheduler — Stage 2 only (no fragmentation / no hotspot split).
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, List, Optional

import numpy as np

from .base import BaseScheduler, register_scheduler
from .hotspot_split import split_population
from .phase_shift import find_optimal_offset
from fingerprint import Fingerprint

if TYPE_CHECKING:
    from util.card import Card
    from util.task import Task


logger = logging.getLogger(__name__)


class STPSScheduler(BaseScheduler):
    """Full STPS scheduler with all three stages enabled."""

    USE_STAGE1 = True
    USE_STAGE2 = True
    USE_STAGE3 = True

    def __init__(
        self,
        cards: List["Card"],
        horizon: int = 64,
        d_max: int = 16,
        bw_max: float = 1e9,
        centrality_split_threshold: float = 0.2,
        frag_weight: float = 1.0,
        beta_weight: float = 1.0,
        beta_high_threshold: float = 1.5,
        ema_alpha: float = 0.3,
        **kwargs,
    ) -> None:
        super().__init__(cards=cards, **kwargs)
        self.horizon = int(horizon)
        self.d_max = int(d_max)
        self.bw_max = float(bw_max)
        self.centrality_split_threshold = float(centrality_split_threshold)
        self.frag_weight = float(frag_weight)
        self.beta_weight = float(beta_weight)
        self.beta_high_threshold = float(beta_high_threshold)
        self.ema_alpha = float(ema_alpha)

        for card in self.cards:
            card.ensure_forecast(self.horizon)

    @property
    def name(self) -> str:
        return "stps"

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def step(self, time_step: int) -> None:
        for card in self.cards:
            card.advance_forecast()

    def on_task_completion(self, task: "Task", time_step: int) -> None:
        # Forecast already rolls forward each step; nothing else to undo.
        pass

    # ------------------------------------------------------------------
    # Placement -- replaces select_card_for_task entirely.
    # ------------------------------------------------------------------

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        fp = self._resolve_fingerprint(task)

        candidates = [c for c in self.cards if c.can_host(task)]
        if not candidates:
            return None

        if self.USE_STAGE1 and fp is not None:
            candidates = self._stage1_filter(candidates, fp)
        if not candidates:
            return None

        if self.USE_STAGE2 and fp is not None:
            chosen, offset, peak = self._stage2_phase_shift(candidates, fp)
            if chosen is None:
                return None
            if peak > self.bw_max:
                logger.info(
                    "[STPS] Task %s rejected: best peak %.2f exceeds BW_max %.2f",
                    task.task_id, peak, self.bw_max,
                )
                return None
            task.start_offset = int(offset)
            chosen.ensure_forecast(self.horizon)
            chosen.add_forecast(fp.traffic_sequence, offset)
        else:
            # No fingerprint or temporal stage disabled -- pick best Stage-1 candidate.
            chosen = candidates[0]

        if self.USE_STAGE3 and fp is not None:
            task.split_plan = split_population(
                fp.max_centrality, self.centrality_split_threshold
            )

        if fp is not None:
            chosen.update_beta_card(fp.global_burstiness, self.ema_alpha)

        return chosen

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------

    def _stage1_filter(self, candidates: List["Card"], fp: Fingerprint) -> List["Card"]:
        """Score candidates by fragmentation match + β isolation, return best subset."""
        K = max(float(fp.mean_components), 1.0)
        target_block = 1.0 / K  # cohesive task -> high fraction; decoupled -> low

        scored = []
        for card in candidates:
            block = card.largest_free_block_ratio()
            frag_score = abs(block - target_block)
            beta_penalty = card.beta_card if fp.global_burstiness > self.beta_high_threshold else 0.0
            score = self.frag_weight * frag_score + self.beta_weight * beta_penalty
            scored.append((score, card))

        scored.sort(key=lambda x: x[0])
        # Keep all candidates ordered by score; Stage 2 picks the lowest peak among them.
        return [c for _, c in scored]

    def _stage2_phase_shift(self, candidates: List["Card"], fp: Fingerprint)-> tuple[Optional["Card"], int, float]:
        best = None  # (card, offset, peak)
        for card in candidates:
            card.ensure_forecast(self.horizon)
            forecast = card.forecast
            assert forecast is not None  # guaranteed by ensure_forecast above
            offset, peak = find_optimal_offset(
                forecast, fp.traffic_sequence, self.d_max, self.bw_max
            )
            if best is None or peak < best[2]:
                best = (card, offset, peak)
        if best is None:
            return None, 0, math.inf
        return best

    def _resolve_fingerprint(self, task: "Task")-> Optional[Fingerprint]:
        """Lazy-load a fingerprint from disk if the task only carries a path."""
        if task.fingerprint is not None:
            assert isinstance(task.fingerprint, Fingerprint)
            return task.fingerprint
        path = getattr(task, "fingerprint_path", None)
        if path is None:
            return None
        try:
            from fingerprint import load_fingerprint  # local import to keep dep light
            fp = load_fingerprint(path)
        except Exception as exc:
            logger.warning("[STPS] Failed to load fingerprint %s: %s", path, exc)
            return None
        task.fingerprint = fp
        return fp


class STPSSpatialScheduler(STPSScheduler):
    """Ablation: only Stage 1 fragmentation + Stage 3 hotspot splitting."""

    USE_STAGE1 = True
    USE_STAGE2 = False
    USE_STAGE3 = True

    @property
    def name(self) -> str:
        return "stps-spatial"


class STPSTemporalScheduler(STPSScheduler):
    """Ablation: only Stage 2 phase-shifting; ignores K̄ and hotspot split."""

    USE_STAGE1 = False
    USE_STAGE2 = True
    USE_STAGE3 = False

    @property
    def name(self) -> str:
        return "stps-temporal"


register_scheduler("stps", STPSScheduler)
register_scheduler("stps-spatial", STPSSpatialScheduler)
register_scheduler("stps-temporal", STPSTemporalScheduler)
