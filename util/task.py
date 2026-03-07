import math
import random
from dataclasses import dataclass, field
from typing import List


@dataclass
class Task:
    """SNN task abstraction with stochastic spike dynamics."""
    task_id: int
    state_size_mb: float
    neuron_count: int
    complexity_ratio: float
    arrival_step: int

    cores_required: int = field(init=False)
    synapses_required: int = field(init=False)
    memory_gb_required: float = field(init=False)
    current_spike_count: int = 0
    current_synaptic_ops: int = 0
    host_card_id: int = -1
    duration_steps: int = 0
    tick_index: int = 0
    hotspot_period: int = field(init=False)
    hotspot_width: int = field(init=False)
    base_phase: float = field(init=False)
    noise_scale: float = field(init=False)
    
    # Timing for latency tracking
    placement_step: int = -1  # When task was first placed on a card
    completion_step: int = -1  # When task completed

    # Communication-related fields for GLaSS-DRL
    fan_out: int = field(init=False)           # Synaptic fan-out count
    avg_hop_distance: float = 1.0              # Average hop distance (updated at placement)
    firing_rate_history: List[float] = field(default_factory=list)  # Sliding window of firing rates

    def __post_init__(self) -> None:
        # Estimate resource needs from neuron count and complexity to align with card constraints.
        self.cores_required = max(1, int((self.neuron_count / 64) * self.complexity_ratio))

        synapses = int(self.neuron_count * 32 * self.complexity_ratio)
        self.synapses_required = max(500, synapses)

        state_gb = self.state_size_mb / 1024.0
        self.memory_gb_required = round(max(state_gb * (1 + 0.25 * self.complexity_ratio), 0.01), 4)

        # Temporal dynamics parameters for hotspot-style activity
        self.hotspot_period = random.randint(6, 18)
        self.hotspot_width = max(1, self.hotspot_period // 4)
        self.base_phase = random.uniform(0, 2 * math.pi)
        self.noise_scale = random.uniform(0.05, 0.2)

        # Derive fan_out from synaptic connectivity
        self.fan_out = max(1, self.synapses_required // max(self.neuron_count, 1))

    def simulate_tick(self) -> None:
        """Simulate temporally correlated SNN spikes with hotspot bursts and noise."""
        self.tick_index += 1

        # Sinusoidal envelope plus hotspot gating
        phase = (2 * math.pi * self.tick_index / self.hotspot_period) + self.base_phase
        wave = 0.5 * (math.sin(phase) + 1.0)  # 0..1
        in_hotspot = (self.tick_index % self.hotspot_period) < self.hotspot_width
        hotspot_gate = 1.0 if in_hotspot else 0.35

        # Base spike count with noise
        base_spikes = 2000.0 * wave * hotspot_gate
        noise = random.gauss(0.0, self.noise_scale * max(base_spikes, 1.0))
        base_spikes = max(base_spikes + noise, 0.0)

        # Ops per spike: heavier during hotspot
        ops_per_spike = random.uniform(5, 10) if in_hotspot else random.uniform(2, 6)
        base_ops = base_spikes * ops_per_spike

        scale = self.complexity_ratio * (self.cores_required / 50)
        self.current_spike_count = int(base_spikes * scale)
        self.current_synaptic_ops = int(base_ops * scale)

        # Track firing rate history for temporal state (GLaSS-DRL)
        firing_rate = self.current_spike_count / max(self.neuron_count, 1)
        self.firing_rate_history.append(firing_rate)
