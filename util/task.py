from dataclasses import dataclass, field
from typing import List, Optional
from fingerprint import Fingerprint, effective_traffic_trace


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
    rejected: bool = False
    reject_reason: str = ""

    # docs/traffic_optim.md §A.1: bandwidth-contention bookkeeping.
    # pending_traffic = current trace quantum not yet fully served on the NoC;
    # while > 0, tick_index / duration_steps must not advance.
    pending_traffic: float = 0.0
    blocked_ticks: int = 0
    congestion_wait_ticks: int = 0

    def __post_init__(self) -> None:
        self.cores_required = max(1, int((self.neuron_count / 64) * self.complexity_ratio))

        synapses = int(self.neuron_count * 32 * self.complexity_ratio)
        self.synapses_required = max(500, synapses)

        state_gb = self.state_size_mb / 1024.0
        self.memory_gb_required = round(max(state_gb * (1 + 0.25 * self.complexity_ratio), 0.01), 4)

    def next_trace_quantum(self) -> float:
        """Read (without advancing) the next-quantum demand from the trace.

        Bandwidth-contention path: engine pulls the quantum, applies the per-card
        bw_cap, then advances tick_index only if the quantum is fully served.
        Returns 0.0 once the trace is exhausted.
        """
        fp = self.fingerprint
        assert fp is not None, "next_trace_quantum requires a loaded fingerprint"
        trace = effective_traffic_trace(fp)
        idx = self.tick_index
        if idx < int(trace.shape[0]):
            return float(trace[idx])
        return 0.0

    def advance_trace_tick(self) -> None:
        self.tick_index += 1

    def simulate_tick(self) -> None:
        """Legacy single-quantum-per-tick path (no bandwidth contention).

        Kept for backwards compatibility with code that has not migrated to the
        engine-driven contention loop.
        """
        self.tick_index += 1
        fp = self.fingerprint
        assert fp is not None, "simulate_tick requires a loaded fingerprint"
        trace = effective_traffic_trace(fp)
        idx = self.tick_index - 1
        if idx < int(trace.shape[0]):
            self.current_traffic = float(trace[idx])
        else:
            self.current_traffic = 0.0
