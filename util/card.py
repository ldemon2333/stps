from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

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

    def calculate_load(self, alpha: float, beta: float) -> float:
        """Compute and cache current load given weights."""
        total = 0.0
        for task in self.tasks:
            total += (alpha * task.current_spike_count) + (beta * task.current_synaptic_ops)
        self.current_load = total
        return total