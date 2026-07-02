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
from fingerprint import Fingerprint, effective_traffic_trace

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
        # docs/Q0_result.md §5.2 改动 A / D — load-aware extension to STPS.
        # Both default to 0.0 so the base STPS scheduler remains bit-equivalent.
        load_weight: float = 0.0,
        backlog_weight: float = 0.0,
        load_ema_alpha: float = 0.2,
        # Stage 1 candidate pruning: drop top-fraction by score (= most loaded)
        # so Stage 2 cannot pick a heavily-loaded card just because its forecast
        # happens to be low. 0.0 = no pruning (old behavior).
        stage1_cull_frac: float = 0.0,
        # EXP-3 (robustness): multiplicative +/- noise applied to the *scheduler's
        # view* of each fingerprint's traffic timeline and burstiness, modelling
        # calibration-to-deployment drift. The engine still simulates the true,
        # unperturbed fingerprint, so this isolates decision robustness. 0.0 =
        # off (bit-equivalent to the base scheduler).
        fingerprint_noise: float = 0.0,
        fingerprint_noise_seed: int = 0,
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
        self.load_weight = float(load_weight)
        self.backlog_weight = float(backlog_weight)
        self.load_ema_alpha = float(load_ema_alpha)
        self.stage1_cull_frac = float(stage1_cull_frac)
        self.fingerprint_noise = float(fingerprint_noise)
        self.fingerprint_noise_seed = int(fingerprint_noise_seed)
        self._noisy_fp_cache: dict[int, "Fingerprint"] = {}
        # Stage-3 hotspot split is a pure function of the offline fingerprint;
        # memoize per fingerprint so its O(V') scan is not paid per admission.
        self._split_cache: dict = {}
        # Per-card EMA of served epoch load (改动 A). Maintained in step().
        self._load_ema: dict[int, float] = {c.card_id: 0.0 for c in cards}
        # Per-card EMA of epoch backlog (改动 D). Read from engine each step.
        self._backlog_ema: dict[int, float] = {c.card_id: 0.0 for c in cards}
        self.cluster_epoch_backlog: dict[int, float] = {c.card_id: 0.0 for c in cards}

        for card in self.cards:
            card.ensure_forecast(self.horizon)

    @property
    def name(self) -> str:
        return "stps"

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def step(self, time_step: int) -> None:
        # Update per-card EMAs of served load + backlog (改动 A / D).
        a = self.load_ema_alpha
        for card in self.cards:
            cid = card.card_id
            load = float(self.cluster_epoch_loads.get(cid, 0.0))
            backlog = float(self.cluster_epoch_backlog.get(cid, 0.0))
            self._load_ema[cid] = (1.0 - a) * self._load_ema.get(cid, 0.0) + a * load
            self._backlog_ema[cid] = (1.0 - a) * self._backlog_ema.get(cid, 0.0) + a * backlog
            card.advance_forecast()

    def on_task_completion(self, task: "Task", time_step: int) -> None:
        # Forecast already rolls forward each step; nothing else to undo.
        pass

    # ------------------------------------------------------------------
    # Placement -- replaces select_card_for_task entirely.
    # ------------------------------------------------------------------

    def select_card_for_task(self, task: "Task") -> Optional["Card"]:
        fp = self._resolve_fingerprint(task)
        if fp is not None and self.fingerprint_noise > 0.0:
            fp = self._noisy_view(task, fp)

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
                logger.debug(
                    "[STPS] Task %s peak %.2f exceeds BW_max %.2f; using min-peak offset %d, NoC queue absorbs overflow",
                    task.task_id, peak, self.bw_max, offset,
                )
            task.start_offset = int(offset)
            chosen.ensure_forecast(self.horizon)
            chosen.add_forecast(effective_traffic_trace(fp), offset)
        else:
            # No fingerprint or temporal stage disabled -- pick best Stage-1 candidate.
            chosen = candidates[0]

        if self.USE_STAGE3 and fp is not None:
            task.split_plan = self._cached_split(task, fp)

        if fp is not None:
            chosen.update_beta_card(fp.global_burstiness, self.ema_alpha)

        return chosen

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------

    def _stage1_filter(self, candidates: List["Card"], fp: Fingerprint) -> List["Card"]:
        """Score candidates by fragmentation match + β isolation + (optional) load / backlog penalty."""
        K = max(float(fp.mean_components), 1.0)
        target_block = 1.0 / K  # cohesive task -> high fraction; decoupled -> low

        # docs/Q0_result.md §5.2 改动 A / D: normalize EMA-load and EMA-backlog
        # by the cluster max so the new terms are O(1) regardless of bw_cap.
        max_load = max(self._load_ema.values(), default=0.0)
        max_backlog = max(self._backlog_ema.values(), default=0.0)

        scored = []
        for card in candidates:
            block = card.largest_free_block_ratio()
            frag_score = abs(block - target_block)
            beta_penalty = card.beta_card if fp.global_burstiness > self.beta_high_threshold else 0.0
            load_penalty = (self._load_ema.get(card.card_id, 0.0) / (max_load + 1e-9)) if max_load > 0 else 0.0
            backlog_penalty = (self._backlog_ema.get(card.card_id, 0.0) / (max_backlog + 1e-9)) if max_backlog > 0 else 0.0
            score = (
                self.frag_weight * frag_score
                + self.beta_weight * beta_penalty
                + self.load_weight * load_penalty
                + self.backlog_weight * backlog_penalty
            )
            scored.append((score, card))

        scored.sort(key=lambda x: x[0])
        ranked = [c for _, c in scored]
        # Cull worst-scoring fraction so Stage 2 cannot recover them.
        if self.stage1_cull_frac > 0.0 and len(ranked) > 1:
            keep = max(1, int(math.ceil(len(ranked) * (1.0 - self.stage1_cull_frac))))
            ranked = ranked[:keep]
        return ranked

    def _stage2_phase_shift(self, candidates: List["Card"], fp: Fingerprint)-> tuple[Optional["Card"], int, float]:
        best = None  # (card, offset, peak)
        for card in candidates:
            card.ensure_forecast(self.horizon)
            forecast = card.forecast
            assert forecast is not None  # guaranteed by ensure_forecast above
            offset, peak = find_optimal_offset(
                forecast, effective_traffic_trace(fp), self.d_max, self.bw_max
            )
            if best is None or peak < best[2]:
                best = (card, offset, peak)
        if best is None:
            return None, 0, math.inf
        return best

    def _cached_split(self, task: "Task", fp: Fingerprint) -> List[int]:
        """Memoize Stage-3 hotspot indices per fingerprint.

        ``split_population`` depends only on the offline fingerprint's per-neuron
        centrality and the fixed threshold -- not on any runtime state -- so it
        is computed once per distinct fingerprint rather than per admission.
        Without this the O(V') scan over multi-million-neuron centrality arrays
        dominates the admission decision (EXP-2); memoization restores the
        O(M)+O(D_max*H) online cost the design intends. Result is identical to
        recomputing every call.
        """
        key = getattr(task, "fingerprint_path", None) or id(fp.max_centrality)
        cached = self._split_cache.get(key)
        if cached is None:
            cached = split_population(fp.max_centrality, self.centrality_split_threshold)
            self._split_cache[key] = cached
        return cached

    def _noisy_view(self, task: "Task", fp: Fingerprint) -> Fingerprint:
        """Return a perturbed copy of ``fp`` for scheduling decisions only.

        Applies deterministic multiplicative +/- ``fingerprint_noise`` noise to
        the effective traffic timeline and to the burstiness scalar, modelling
        the gap between the offline-calibrated fingerprint and the tenant's
        actual deployment traffic (EXP-3). The engine keeps simulating the true
        fingerprint via ``task.fingerprint``; only the scheduler sees the noisy
        view. Cached per task so repeated stage calls are consistent.
        """
        import dataclasses
        tid = int(getattr(task, "task_id", id(task)))
        cached = self._noisy_fp_cache.get(tid)
        if cached is not None:
            return cached
        rng = np.random.default_rng(
            (self.fingerprint_noise_seed * 1_000_003 + tid) & 0xFFFFFFFF
        )
        p = self.fingerprint_noise

        def _perturb(arr: np.ndarray) -> np.ndarray:
            if arr is None or arr.size == 0:
                return arr
            factor = 1.0 + p * rng.uniform(-1.0, 1.0, size=arr.shape)
            return np.maximum(arr.astype(np.float32) * factor, 0.0).astype(np.float32)

        beta_factor = 1.0 + p * float(rng.uniform(-1.0, 1.0))
        noisy = dataclasses.replace(
            fp,
            mean_injection_trace=_perturb(fp.mean_injection_trace),
            sample_measured_injection_trace=_perturb(fp.sample_measured_injection_trace),
            global_burstiness=max(0.0, float(fp.global_burstiness) * beta_factor),
        )
        self._noisy_fp_cache[tid] = noisy
        return noisy

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


class STPSLoadAwareScheduler(STPSScheduler):
    """STPS + 改动 A (累计负载惩罚) + 改动 D (backlog-aware feedback).

    See docs/Q0_result.md §5.2. Defaults chosen so the new penalty terms are
    comparable in magnitude to the existing frag/β terms (which sit in [0, 1]).
    """

    USE_STAGE1 = True
    USE_STAGE2 = True
    USE_STAGE3 = True

    def __init__(
        self,
        cards: List["Card"],
        load_weight: float = 1.0,
        backlog_weight: float = 0.5,
        load_ema_alpha: float = 0.2,
        **kwargs,
    ) -> None:
        super().__init__(
            cards=cards,
            load_weight=load_weight,
            backlog_weight=backlog_weight,
            load_ema_alpha=load_ema_alpha,
            **kwargs,
        )

    @property
    def name(self) -> str:
        return "stps-la"


register_scheduler("stps", STPSScheduler)
register_scheduler("stps-spatial", STPSSpatialScheduler)
register_scheduler("stps-temporal", STPSTemporalScheduler)
register_scheduler("stps-la", STPSLoadAwareScheduler)
