"""Single-card replay: fixed physical ticks, elastic logical steps and a card barrier."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time

from simulation.noc import NoCNetwork
from simulation.scenario import Scenario, TaskPlacement, load_scenario
from util.metrics import MetricsWriter, SimulationMetrics


@dataclass(frozen=True)
class SimulationResult:
    status: str
    output_dir: Path
    ticks_executed: int
    summary: dict


def _verify_conservation(metrics, outstanding):
    """No drops or duplicates, including delivered flits retained by the sink NI."""
    inventory = {task_id: {"source": 0, "network": 0, "sink": 0} for task_id in metrics.tasks}
    for row in outstanding:
        kind = ("source" if row["kind"] in ("source_pending", "source_ni") else
                "sink" if row["kind"] == "sink_ni" else "network")
        inventory[row["task_id"]][kind] += row["count"]
    for task_id, counts in metrics.totals.items():
        stock = inventory[task_id]
        if not (counts["generated_tx"] == counts["tx_injected"] + stock["source"]
                and counts["tx_injected"] == counts["rx_ejected"] + stock["network"]
                and counts["rx_ejected"] == counts["rx_consumed"] + stock["sink"]):
            raise AssertionError(f"flit conservation failed for {task_id}: {counts}, {stock}")
    return inventory


@dataclass
class _Progress:
    placement: TaskPlacement
    actual_start_tick: int | None = None
    completion_tick: int | None = None
    steps_started: int = 0
    steps_completed: int = 0
    communication_extension_ticks: int = 0
    step_start_tick: int | None = None
    last_rx_cycle: int | None = None

    @property
    def running(self):
        return self.actual_start_tick is not None and self.completion_tick is None

    @property
    def logical_tick(self):
        return self.steps_started - 1

    def summary(self, tick):
        requested = self.placement.start_tick
        started, completed = self.actual_start_tick, self.completion_tick
        observed = (completed or tick) - started + 1 if started is not None else 0
        execution = observed if completed is not None else None
        return {
            "status": "completed" if completed is not None else "unfinished" if started is not None else "not_started",
            "actual_start_tick": started, "completion_tick": completed,
            "steps_started": self.steps_started, "steps_completed": self.steps_completed,
            "task_start_wait_ticks": started - requested if started is not None else max(0, tick - requested + 1),
            "observed_execution_ticks": observed, "task_execution_ticks": execution,
            "task_end_to_end_ticks": completed - requested + 1 if completed is not None else None,
            "communication_extension_ticks": self.communication_extension_ticks,
            "slowdown": execution / self.placement.workload.T if execution is not None else None,
        }


def run_simulation(scenario: Scenario | str | Path, output_dir: str | Path,
                   *, trace: bool = False, report: bool = True) -> SimulationResult:
    """Keep queues across K-cycle windows; issue new work only after the card barrier.

    Waiting tasks are admitted at round boundaries in (requested start, task ID)
    order when their fixed mapped cores and memory are free. A completed Rx is
    delivery; sink consumption can continue later with its original identity.
    """
    if not isinstance(scenario, Scenario):
        scenario = load_scenario(scenario)
    writer = MetricsWriter(output_dir, trace)
    metrics = SimulationMetrics(scenario.tasks, writer, scenario.network.mesh_x)
    network = NoCNetwork(scenario.network)
    started, cpu_started = time.perf_counter(), time.process_time()
    progress = {task.task_id: _Progress(task) for task in scenario.tasks}
    round_start = None
    status, tick = "max_ticks", 0
    last_outstanding = []
    blocked_ticks = extension_ticks = rounds_started = rounds_completed = 0

    def write_step(p, end_tick, completed, rx_complete):
        duration = end_tick - p.step_start_tick + 1
        writer.write("step_timing", {
            "task_id": p.placement.task_id, "logical_tick": p.logical_tick,
            "step_start_tick": p.step_start_tick, "step_end_tick": end_tick,
            "logical_step_duration_ticks": duration,
            "communication_extension_ticks": duration - 1,
            "last_rx_cycle": p.last_rx_cycle, "rx_complete": rx_complete,
            "step_completed": completed,
        })

    try:
        for tick in range(1, scenario.max_ticks + 1):
            metrics.begin_tick(tick)
            begin = (tick - 1) * scenario.cycles_per_tick
            if round_start is None:
                occupied = {core for p in progress.values() if p.running for core in p.placement.mapping}
                occupied_memory = sum(p.placement.workload.state_size_mb for p in progress.values() if p.running)
                for task in scenario.tasks:
                    p = progress[task.task_id]
                    if p.actual_start_tick is not None or task.start_tick > tick:
                        continue
                    if occupied.intersection(task.mapping) or occupied_memory + task.workload.state_size_mb > scenario.memory_mb + 1e-9:
                        continue
                    p.actual_start_tick = tick
                    occupied.update(task.mapping)
                    occupied_memory += task.workload.state_size_mb
                active = [p for p in progress.values() if p.running]
                if active:
                    round_start = tick
                    rounds_started += 1
                    for p in active:
                        task = p.placement
                        logical = p.steps_completed
                        p.steps_started += 1
                        p.step_start_tick, p.last_rx_cycle = tick, None
                        metrics.record_step(task, logical)
                        for edge, value in enumerate(task.workload.flits[logical]):
                            count = int(value)
                            if not count:
                                continue
                            src = task.mapping[int(task.workload.edge_src[edge])]
                            dst = task.mapping[int(task.workload.edge_dst[edge])]
                            writer.write("events", {"kind": "generate_local" if src == dst else "generate",
                                "cycle": begin, "physical_tick": tick, "task_id": task.task_id,
                                "logical_tick": logical, "edge_id": edge, "src_core": src,
                                "dst_core": dst, "count": count, "generated_cycle": begin})
                            if src != dst:
                                network.offer(task.task_id, logical, edge, src, dst, count, begin)
            active = [p for p in progress.values() if p.running]
            if round_start is not None and tick > round_start:
                extension_ticks += 1
                for p in active:
                    p.communication_extension_ticks += 1
            for cycle in range(begin, begin + scenario.cycles_per_tick):
                metrics.sample_queues(network.occupancy())
                for event in network.advance_cycle(cycle):
                    metrics.event(event)
                    if event["kind"] == "rx":
                        p = progress[event["task_id"]]
                        if p.running and event["logical_tick"] == p.logical_tick:
                            p.last_rx_cycle = event["cycle"]
            metrics.sample_end_boundary(network.occupancy())
            last_outstanding = network.outstanding()
            inventory = _verify_conservation(metrics, last_outstanding)
            barrier_ready = not network.pending_delivery()
            blocked_ticks += int(not barrier_ready)
            active_steps = {}
            for p in active:
                stock = inventory[p.placement.task_id]
                own_pending = bool(stock["source"] + stock["network"])
                active_steps[p.placement.task_id] = {
                    "logical_tick": p.logical_tick,
                    "step_started": p.step_start_tick == tick,
                    "step_elapsed_ticks": tick - p.step_start_tick + 1,
                    "waiting_for_communication": own_pending,
                    "waiting_for_card_barrier": not barrier_ready and not own_pending,
                }
            if barrier_ready and round_start is not None:
                rounds_completed += 1
                for p in active:
                    write_step(p, tick, True, True)
                    p.steps_completed += 1
                    if p.steps_completed == p.placement.workload.T:
                        p.completion_tick = tick
            completed = sum(p.completion_tick is not None for p in progress.values())
            waiting = sum(p.actual_start_tick is None and p.placement.start_tick <= tick for p in progress.values())
            metrics.end_tick(last_outstanding, active_steps, completed, barrier_ready, round_start, waiting)
            if completed == len(progress):
                status = "completed"
                break
            if barrier_ready:
                round_start = None
        if status != "completed" and round_start is not None:
            for p in progress.values():
                if p.running:
                    stock = inventory[p.placement.task_id]
                    write_step(p, tick, False, not (stock["source"] + stock["network"]))
        states = {task_id: p.summary(tick) for task_id, p in progress.items()}
        metrics.finish_tasks(states)
        for entry in last_outstanding:
            writer.write("outstanding", {**entry, "physical_tick": tick,
                                          "delivery_pending": entry["kind"] != "sink_ni"})
        totals = metrics.total()
        summary = {
            "schema_version": 2, "scenario": scenario.name, "status": status,
            "valid": status == "completed", "ticks_executed": tick,
            "cycles_executed": tick * scenario.cycles_per_tick,
            "physical_tick_period_cycles": scenario.cycles_per_tick,
            "time_model": "fixed physical tick; cross-tick queues; chip-wide Rx barrier before next logical step",
            "admission_order": "requested start_tick, then task_id; fixed-core/memory feasible tasks at round boundaries",
            "cycle_boundary_order": "cycle c transfers commit at c+1; Rx at ending boundary counts before barrier check",
            "compute_model": "trace SOP work issued once per logical step; no compute service timing",
            "quantization": "per-edge decimal carry, floor, cached per workload",
            "sparse_output": "missing core_tick row means zero activity; task_tick includes silent/waiting active steps",
            "trace_enabled": trace, "config": asdict(scenario.network), "max_ticks": scenario.max_ticks,
            "neurons_per_core": scenario.neurons_per_core, "memory_mb": scenario.memory_mb,
            "scenario_path": str(scenario.path), "scenario_sha256": scenario.sha256,
            "elapsed_seconds": time.perf_counter() - started,
            "cpu_seconds": time.process_time() - cpu_started,
            "peak_inventory_flits": metrics.peak_inventory,
            "units": {"compute_sops": "SOP", "traffic": "flit", "time": "NoC cycle"},
            "totals": totals, "derived": metrics.derived(totals),
            "timing": {"rounds_started": rounds_started, "rounds_completed": rounds_completed,
                       "barrier_blocked_ticks": blocked_ticks, "communication_extension_ticks": extension_ticks,
                       "tasks_completed": completed, "tasks_total": len(progress),
                       "task_throughput_per_tick": completed / tick},
            "tasks": [{"task_id": task.task_id, "start_tick": task.start_tick,
                       "planned_end_tick": task.planned_end_tick,
                       "population_count": task.workload.population_count,
                       "mapping": list(task.mapping), "workload_path": str(task.workload_path),
                       "workload_sha256": task.workload_hash, "source": task.workload.source,
                       "metadata": task.workload.metadata, "logical_ticks": task.workload.T,
                       "active_edges": int(sum((task.workload.flits > 0).any(axis=0))),
                       "input_totals": task.workload.totals, **states[task.task_id]}
                      for task in scenario.tasks],
        }
        writer.json("manifest.json", summary)
        resolved = json.loads(scenario.path.read_text(encoding="utf-8"))
        paths = {task.task_id: str(task.workload_path) for task in scenario.tasks}
        for task in resolved["tasks"]:
            task["workload"] = paths[task["task_id"]]
        writer.json("scenario.json", resolved)
    finally:
        writer.close()
    if report:
        from simulation.report import write_report
        write_report(writer.path)
    return SimulationResult(status, writer.path, tick, summary)
