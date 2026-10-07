"""Online card selectors sharing resource eligibility and a fixed mapper.

Only P2C-Mean reads mean planned compute/NoC demand. No policy receives
successful throughput, queue occupancy, future completion, or routing state.
The caller supplies currently feasible snapshots and updates them after each
placement. Selection does not reserve resources or map MicroPopulations.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Sequence


POLICIES = ("RR", "WorstFit", "DRU", "BestFit", "P2C-Mean")


def _integer(value: int, name: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} must be a {'positive' if positive else 'non-negative'} integer")


def _number(value: float, name: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'} and finite")


@dataclass(frozen=True)
class CardSnapshot:
    card_id: int
    total_cores: int
    used_cores: int
    memory_mb: float
    used_memory_mb: float
    mean_compute_sops: float
    mean_noc_endpoint: float
    compute_budget_sops: float
    noc_budget_endpoint: float

    def __post_init__(self) -> None:
        _integer(self.card_id, "card_id")
        _integer(self.total_cores, "total_cores", positive=True)
        _integer(self.used_cores, "used_cores")
        if self.used_cores > self.total_cores:
            raise ValueError("used_cores exceeds total_cores")
        _number(self.memory_mb, "memory_mb", positive=True)
        _number(self.used_memory_mb, "used_memory_mb")
        if self.used_memory_mb > self.memory_mb:
            raise ValueError("used_memory_mb exceeds memory_mb")
        _number(self.mean_compute_sops, "mean_compute_sops")
        _number(self.mean_noc_endpoint, "mean_noc_endpoint")
        _number(self.compute_budget_sops, "compute_budget_sops", positive=True)
        _number(self.noc_budget_endpoint, "noc_budget_endpoint", positive=True)


class BaselinePolicy:
    """Choose one feasible card, with deterministic ties and isolated RNG.

    With card_count, RR cycles through the complete contiguous ID range
    [0, card_count). Without it, RR walks ascending IDs from its pointer and
    wraps to the lowest eligible ID. Failed selection changes neither the RR
    pointer nor the P2C random stream. last_decision is replaced on every call.
    """

    def __init__(self, name: str, seed: int = 0, card_count: int | None = None):
        if name not in POLICIES:
            raise ValueError(f"Unknown baseline policy {name!r}; choose from {POLICIES}")
        _integer(seed, "seed")
        if card_count is not None:
            _integer(card_count, "card_count", positive=True)
        self.name = name
        self.seed = seed
        self.card_count = card_count
        self._rr_pointer = 0
        self._random = random.Random(seed)
        self.last_decision: dict = {}

    def select(
        self,
        candidates: Sequence[CardSnapshot],
        required_cores: int,
        state_size_mb: float,
        mean_compute_sops: float,
        mean_noc_endpoint: float,
    ) -> int | None:
        _integer(required_cores, "required_cores", positive=True)
        _number(state_size_mb, "state_size_mb")
        _number(mean_compute_sops, "mean_compute_sops")
        _number(mean_noc_endpoint, "mean_noc_endpoint")
        cards = sorted(candidates, key=lambda card: card.card_id)
        ids = [card.card_id for card in cards]
        if len(set(ids)) != len(ids):
            raise ValueError("Candidate card_id values must be unique")
        if self.card_count is not None and any(card_id >= self.card_count for card_id in ids):
            raise ValueError("Candidate card_id exceeds card_count")
        # Eligibility is owned by the shared admission path. Catch caller
        # mistakes instead of allowing different policies to filter differently.
        for card in cards:
            if (card.used_cores + required_cores > card.total_cores
                    or card.used_memory_mb + state_size_mb > card.memory_mb):
                raise ValueError(f"Candidate card {card.card_id} is not resource-feasible")
        self.last_decision = {
            "policy": self.name,
            "eligible_card_ids": ids,
            "sampled_card_ids": [],
            "scores": {},
            "selected_card_id": None,
        }
        if not cards:
            return None

        if self.name == "RR":
            selected = next((card for card in cards if card.card_id >= self._rr_pointer), cards[0])
            self._rr_pointer = selected.card_id + 1
            if self.card_count is not None:
                self._rr_pointer %= self.card_count
        elif self.name in ("WorstFit", "BestFit"):
            scores = {
                card.card_id: (
                    card.total_cores - card.used_cores - required_cores,
                    card.memory_mb - card.used_memory_mb - state_size_mb,
                ) for card in cards
            }
            self.last_decision["scores"] = scores
            if self.name == "WorstFit":
                selected = min(cards, key=lambda card: (
                    -scores[card.card_id][0], -scores[card.card_id][1], card.card_id))
            else:
                selected = min(cards, key=lambda card: (*scores[card.card_id], card.card_id))
        elif self.name == "DRU":
            scores = {
                card.card_id: max(
                    (card.used_cores + required_cores) / card.total_cores,
                    (card.used_memory_mb + state_size_mb) / card.memory_mb,
                ) for card in cards
            }
            self.last_decision["scores"] = scores
            selected = min(cards, key=lambda card: (scores[card.card_id], card.card_id))
        else:  # P2C-Mean
            sampled = self._random.sample(cards, 2) if len(cards) > 1 else cards
            scores = {
                card.card_id: max(
                    (card.mean_compute_sops + mean_compute_sops) / card.compute_budget_sops,
                    (card.mean_noc_endpoint + mean_noc_endpoint) / card.noc_budget_endpoint,
                ) for card in sampled
            }
            self.last_decision["sampled_card_ids"] = [card.card_id for card in sampled]
            self.last_decision["scores"] = scores
            selected = min(sampled, key=lambda card: (scores[card.card_id], card.card_id))

        self.last_decision["selected_card_id"] = selected.card_id
        return selected.card_id
