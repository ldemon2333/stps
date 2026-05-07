from dataclasses import dataclass, field
from typing import List, Optional
from fingerprint import Fingerprint


@dataclass
class Task:
    """SNN task whose per-tick load is driven by an attached DTDG fingerprint."""
    task_id: int
    state_size_mb: float
    neuron_count: int
    complexity_ratio: float
    arrival_step: int

    cores_required: int = field(init=False)
    synapses_required: int = field(init=False)
    memory_gb_required: float = field(init=False)
    current_traffic: float = 0.0
    host_card_id: int = -1
    duration_steps: int = 0
    tick_index: int = 0

    placement_step: int = -1
    completion_step: int = -1

    # Fingerprint-driven load source (paper §4.2). Required for simulate_tick.
    fingerprint_path: Optional[str] = None
    fingerprint: Optional["Fingerprint"] = None
    start_offset: int = 0
    split_plan: List[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cores_required = max(1, int((self.neuron_count / 64) * self.complexity_ratio))

        synapses = int(self.neuron_count * 32 * self.complexity_ratio)
        self.synapses_required = max(500, synapses)

        state_gb = self.state_size_mb / 1024.0
        self.memory_gb_required = round(max(state_gb * (1 + 0.25 * self.complexity_ratio), 0.01), 4)

    def simulate_tick(self) -> None:
        """Sample the fingerprint traffic timeline at the current active-tick index.

        E describes one finite inference pass of length T; once exhausted the task
        emits no further traffic (no cyclic replay).
        """
        self.tick_index += 1
        fp = self.fingerprint
        assert fp is not None, "simulate_tick requires a loaded fingerprint"
        idx = self.tick_index - 1
        if idx < int(fp.T):
            self.current_traffic = float(fp.traffic_sequence[idx])
        else:
            self.current_traffic = 0.0
