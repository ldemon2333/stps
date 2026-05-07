from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .task import Task


def _load_env_config():
    """Load card configuration from .env file."""
    config = {
        'cores': 512,
        'synapses': 50000,
        'memory_gb': 128.0,
        'bandwidth_mbps': 1000.0
    }
    
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if key == 'CARD_TOTAL_CORES':
                        config['cores'] = int(value)
                    elif key == 'CARD_TOTAL_SYNAPSES':
                        config['synapses'] = int(value)
                    elif key == 'CARD_TOTAL_MEMORY_GB':
                        config['memory_gb'] = float(value)
                    elif key == 'CARD_BANDWIDTH_MBPS':
                        config['bandwidth_mbps'] = float(value)
    
    return config


_ENV_CONFIG = _load_env_config()


@dataclass
class Card:
    """Neuromorphic accelerator card that hosts a set of tasks."""
    card_id: int
    cores: int = _ENV_CONFIG['cores']
    synapses: int = _ENV_CONFIG['synapses']
    memory_gb: float = _ENV_CONFIG['memory_gb']
    bandwidth_mbps: float = _ENV_CONFIG['bandwidth_mbps']
    tasks: List[Task] = field(default_factory=list)

    @property
    def current_load(self) -> float:
        return getattr(self, "_cached_load", 0.0)

    @current_load.setter
    def current_load(self, value: float) -> None:
        self._cached_load = value

    def can_host(self, task: Task) -> bool:
        used_cores = sum(t.cores_required for t in self.tasks)
        used_synapses = sum(t.synapses_required for t in self.tasks)
        used_memory = sum(t.memory_gb_required for t in self.tasks)

        return (
            (used_cores + task.cores_required <= self.cores) and
            (used_synapses + task.synapses_required <= self.synapses) and
            (used_memory + task.memory_gb_required <= self.memory_gb)
        )

    def put(self, task: Task) -> bool:
        """Attempt to place a task on this card; returns True on success."""
        if not self.can_host(task):
            return False
        task.host_card_id = self.card_id
        self.tasks.append(task)
        return True

    def evict(self, task: Task) -> None:
        """Remove a task from this card if present."""
        if task in self.tasks:
            self.tasks.remove(task)
        task.host_card_id = -1

    def calculate_load(self) -> float:
        """Sum current fingerprint-driven traffic across resident tasks."""
        total = sum(task.current_traffic for task in self.tasks)
        self.current_load = total
        return total

    # ------------------------------------------------------------------
    # STPS forecast-traffic state (paper §4.3, Algorithm 1).
    # Lazy-allocated: schedulers that don't use phase-shifting never pay for
    # the extra buffer or the EMA update.
    # ------------------------------------------------------------------

    def ensure_forecast(self, horizon: int) -> None:
        if not hasattr(self, "_forecast") or self._forecast.shape[0] != horizon:
            self._forecast = np.zeros(horizon, dtype=np.float32)

    def add_forecast(self, E: np.ndarray, offset: int) -> None:
        if not hasattr(self, "_forecast"):
            return
        H = self._forecast.shape[0]
        end = min(H, offset + E.shape[0])
        if end > offset:
            self._forecast[offset:end] += E[: end - offset].astype(np.float32)

    def peak_forecast(self) -> float:
        if not hasattr(self, "_forecast"):
            return 0.0
        return float(self._forecast.max(initial=0.0))

    def advance_forecast(self) -> None:
        if not hasattr(self, "_forecast"):
            return
        self._forecast[:-1] = self._forecast[1:]
        self._forecast[-1] = 0.0

    @property
    def forecast(self) -> Optional[np.ndarray]:
        return getattr(self, "_forecast", None)

    @property
    def beta_card(self) -> float:
        """Running-average burstiness of tasks placed on this card (EMA)."""
        return float(getattr(self, "_beta_card", 1.0))

    def update_beta_card(self, task_beta: float, ema_alpha: float = 0.3) -> None:
        prev = self.beta_card
        self._beta_card = (1.0 - ema_alpha) * prev + ema_alpha * float(task_beta)

    def largest_free_block_ratio(self) -> float:
        """Approximate fraction of cores that are contiguously free.

        We don't model exact 2D-mesh topology in v1; treat each card's free-core
        fraction as a proxy for "largest contiguous block".
        """
        used = sum(t.cores_required for t in self.tasks)
        free = max(self.cores - used, 0)
        return float(free) / float(self.cores) if self.cores > 0 else 0.0

