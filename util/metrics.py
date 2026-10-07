"""Streaming endpoint accounting; every row retains task and logical-step identity."""
from __future__ import annotations

from collections import Counter
import csv
import json
import math
from pathlib import Path

MEASURES = (
    "compute_sops", "expected_tx", "generated_tx", "expected_rx", "tx_injected",
    "rx_ejected", "rx_consumed", "local_flits", "tx_stall_cycles", "rx_blocked_cycles", "router_wait_flit_cycles",
    "source_wait_cycles_sum", "rx_latency_cycles_sum", "pending_tx", "in_network",
    "pending_rx", "sink_unconsumed", "rx_excess_latency_cycles_sum",
)
GAUGES = ("pending_tx", "in_network", "pending_rx", "sink_unconsumed")
CORE_FIELDS = ("task_id", "physical_tick", "logical_tick", "population_id", "core_id",
               *MEASURES, "barrier_ready")
TASK_TIMING_FIELDS = (
    "actual_start_tick", "completion_tick", "steps_started", "steps_completed",
    "task_start_wait_ticks", "observed_execution_ticks", "task_execution_ticks",
    "task_end_to_end_ticks", "communication_extension_ticks", "slowdown",
)
EVENT_FIELDS = ("kind", "cycle", "physical_tick", "task_id", "logical_tick", "edge_id",
                "src_core", "dst_core", "count", "generated_cycle", "router", "port",
                "next_router", "wait_cycles", "input_port", "requested_output", "reason")


def percentile(hist: Counter, q: float) -> int:
    """Nearest-rank percentile; exact sparse histogram, no per-flit history."""
    count = sum(hist.values())
    if not count:
        return 0
    rank = math.ceil(q * count)
    for value, n in sorted(hist.items()):
        rank -= n
        if rank <= 0:
            return value
    raise AssertionError("invalid histogram")


class MetricsWriter:
    def __init__(self, output_dir: str | Path, trace: bool):
        self.path = Path(output_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        if any(self.path.iterdir()):
            raise ValueError(f"output directory must be empty: {self.path}")
        self.handles = []
        self.writers = {}
        fields = {
            "core_tick": CORE_FIELDS,
            "task_tick": ("task_id", "physical_tick", "logical_tick", *MEASURES,
                          "step_started", "step_elapsed_ticks", "waiting_for_communication",
                          "waiting_for_card_barrier", "barrier_ready"),
            "card_tick": ("physical_tick", *MEASURES, "barrier_ready", "round_started",
                          "round_elapsed_ticks", "communication_extension_tick",
                          "active_tasks", "waiting_tasks", "completed_tasks"),
            "queue_stats": ("physical_tick", "kind", "router", "port", "occupancy_peak",
                            "occupancy_sum", "samples", "full_cycles", "capacity"),
            "link_stats": ("physical_tick", "router", "port", "next_router", "flits", "busy_cycles"),
            "task_summary": ("task_id", "status", "start_tick", "planned_end_tick",
                             "population_count", "logical_ticks", *TASK_TIMING_FIELDS,
                             *MEASURES, "rx_latency_p95_cycles", "rx_excess_latency_p95_cycles",
                             "mean_source_wait_cycles", "mean_rx_latency_cycles",
                             "mean_rx_excess_latency_cycles", "router_wait_per_generated_flit"),
            "step_timing": ("task_id", "logical_tick", "step_start_tick", "step_end_tick",
                            "logical_step_duration_ticks", "communication_extension_ticks",
                            "last_rx_cycle", "rx_complete", "step_completed"),
            "outstanding": (*EVENT_FIELDS, "delivery_pending"),
        }
        if trace:
            fields["events"] = EVENT_FIELDS
        for name, columns in fields.items():
            handle = (self.path / f"{name}.csv").open("w", newline="", encoding="utf-8")
            self.handles.append(handle)
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            self.writers[name] = writer

    def write(self, table, row):
        if table in self.writers:
            self.writers[table].writerow(row)

    def json(self, filename, data):
        (self.path / filename).write_text(json.dumps(data, ensure_ascii=False, allow_nan=False,
                                                     indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def close(self):
        for handle in self.handles:
            handle.close()
        self.handles.clear()


class SimulationMetrics:
    def __init__(self, tasks, writer: MetricsWriter, mesh_x: int):
        self.tasks = {task.task_id: task for task in tasks}
        self.writer = writer
        self.inverse = {task.task_id: {core: pop for pop, core in enumerate(task.mapping)}
                        for task in tasks}
        self.totals = {task.task_id: {name: 0 for name in MEASURES} for task in tasks}
        self.hist = {task.task_id: Counter() for task in tasks}
        self.excess_hist = {task.task_id: Counter() for task in tasks}
        self.mesh_x = mesh_x
        self.core_rows = {}
        self.queues = {}
        self.links = {}
        self.current_tick = 0
        self.last_card = {}
        self.peak_inventory = {"source_pending": 0, "source_ni": 0,
                               "router": 0, "sink_ni": 0, "undelivered": 0}

    def begin_tick(self, physical_tick):
        self.current_tick = physical_tick
        self.core_rows = {}
        self.queues = {}
        self.links = {}

    def add_task(self, task):
        """Register an online placement without resetting network counters."""
        if task.task_id in self.tasks:
            raise ValueError(f"task already registered: {task.task_id}")
        self.tasks[task.task_id] = task
        self.inverse[task.task_id] = {core: pop for pop, core in enumerate(task.mapping)}
        self.totals[task.task_id] = {name: 0 for name in MEASURES}
        self.hist[task.task_id] = Counter()
        self.excess_hist[task.task_id] = Counter()

    def core(self, task_id, logical_tick, core_id):
        key = (task_id, int(logical_tick), int(core_id))
        if key not in self.core_rows:
            self.core_rows[key] = {
                "task_id": task_id, "logical_tick": int(logical_tick), "core_id": int(core_id),
                "population_id": self.inverse[task_id][int(core_id)],
                "physical_tick": self.current_tick, **{name: 0 for name in MEASURES},
            }
        return self.core_rows[key]

    def add(self, task_id, logical_tick, core_id, name, amount):
        self.core(task_id, logical_tick, core_id)[name] += amount
        self.totals[task_id][name] += amount

    def record_step(self, task, logical_tick):
        w = task.workload
        for pop, core in enumerate(task.mapping):
            self.add(task.task_id, logical_tick, core, "compute_sops", float(w.compute_sops[logical_tick, pop]))
        for edge, count in enumerate(w.flits[logical_tick]):
            src = task.mapping[int(w.edge_src[edge])]
            dst = task.mapping[int(w.edge_dst[edge])]
            count = int(count)
            if src == dst:
                self.add(task.task_id, logical_tick, src, "local_flits", count)
            else:
                self.add(task.task_id, logical_tick, src, "expected_tx", count)
                self.add(task.task_id, logical_tick, src, "generated_tx", count)
                self.add(task.task_id, logical_tick, dst, "expected_rx", count)

    def event(self, event):
        event = {**event, "physical_tick": self.current_tick}
        self.writer.write("events", event)
        kind = event["kind"]
        task, logical = event["task_id"], event["logical_tick"]
        src, dst, n = event["src_core"], event["dst_core"], event["count"]
        if kind == "tx":
            self.add(task, logical, src, "tx_injected", n)
            self.add(task, logical, src, "source_wait_cycles_sum", event["wait_cycles"] * n)
        elif kind == "rx":
            self.add(task, logical, dst, "rx_ejected", n)
            self.add(task, logical, dst, "rx_latency_cycles_sum", event["wait_cycles"] * n)
            self.hist[task][event["wait_cycles"]] += n
            hops = abs(src % self.mesh_x - dst % self.mesh_x) + abs(src // self.mesh_x - dst // self.mesh_x)
            excess = event["wait_cycles"] - (hops + 2)
            self.add(task, logical, dst, "rx_excess_latency_cycles_sum", excess * n)
            self.excess_hist[task][excess] += n
        elif kind == "consume":
            self.add(task, logical, dst, "rx_consumed", n)
        elif kind == "tx_stall":
            self.add(task, logical, src, "tx_stall_cycles", 1)
        elif kind == "rx_blocked":
            self.add(task, logical, dst, "rx_blocked_cycles", 1)
        elif kind == "router_wait":
            self.add(task, logical, src, "router_wait_flit_cycles", n)
        elif kind == "link":
            key = (event["router"], event["port"], event["next_router"])
            row = self.links.setdefault(key, {"physical_tick": self.current_tick,
                                             "router": key[0], "port": key[1],
                                             "next_router": key[2], "flits": 0, "busy_cycles": 0})
            row["flits"] += n
            row["busy_cycles"] += 1

    def sample_queues(self, inventory):
        self.record_inventory_peak(inventory)
        for queue in inventory:
            key = (queue["kind"], queue["router"], queue["port"])
            row = self.queues.setdefault(key, {
                "physical_tick": self.current_tick, "kind": key[0], "router": key[1],
                "port": key[2], "occupancy_peak": 0, "occupancy_sum": 0, "samples": 0,
                "full_cycles": 0, "capacity": queue["capacity"],
            })
            value = queue["occupancy"]
            row["occupancy_peak"] = max(row["occupancy_peak"], value)
            row["occupancy_sum"] += value
            row["samples"] += 1
            row["full_cycles"] += int(queue["capacity"] is not None and value == queue["capacity"])

    def record_inventory_peak(self, inventory):
        stock = {name: 0 for name in self.peak_inventory}
        for queue in inventory:
            stock[queue["kind"]] += queue["occupancy"]
        stock["undelivered"] = stock["source_pending"] + stock["source_ni"] + stock["router"]
        for name, value in stock.items():
            self.peak_inventory[name] = max(self.peak_inventory[name], value)

    def sample_end_boundary(self, inventory):
        """Include committed-boundary peaks without adding a cycle to integrals."""
        self.record_inventory_peak(inventory)
        for queue in inventory:
            key = (queue["kind"], queue["router"], queue["port"])
            row = self.queues[key]
            row["occupancy_peak"] = max(row["occupancy_peak"], queue["occupancy"])

    def end_tick(self, outstanding, active_steps, completed, barrier_ready, round_start,
                 waiting_tasks):
        # Inventory columns are end-boundary gauges, never cumulative throughput.
        for totals in self.totals.values():
            for name in GAUGES:
                totals[name] = 0
        for entry in outstanding:
            task, logical, src, dst, count = (entry[k] for k in (
                "task_id", "logical_tick", "src_core", "dst_core", "count"))
            kind = entry["kind"]
            if kind in ("source_pending", "source_ni"):
                self.add(task, logical, src, "pending_tx", count)
            elif kind == "router":
                self.add(task, logical, src, "in_network", count)
            else:
                self.add(task, logical, dst, "sink_unconsumed", count)
            if kind != "sink_ni":
                self.add(task, logical, dst, "pending_rx", count)
        task_rows = {}
        for task_id, info in active_steps.items():
            task_rows[(task_id, info["logical_tick"])] = {"physical_tick": self.current_tick,
                "task_id": task_id, **info, **{n: 0 for n in MEASURES}}
        for key, row in sorted(self.core_rows.items()):
            row["barrier_ready"] = barrier_ready
            if any(row[name] for name in MEASURES):
                self.writer.write("core_tick", row)
            task_row = task_rows.setdefault(key[:2], {"physical_tick": self.current_tick,
                "task_id": key[0], "logical_tick": key[1], **{n: 0 for n in MEASURES}})
            for name in MEASURES:
                task_row[name] += row[name]
        card = {"physical_tick": self.current_tick, **{n: 0 for n in MEASURES},
                "barrier_ready": barrier_ready,
                "round_started": round_start == self.current_tick,
                "round_elapsed_ticks": self.current_tick - round_start + 1 if round_start is not None else 0,
                "communication_extension_tick": round_start is not None and self.current_tick > round_start,
                "active_tasks": len(active_steps), "waiting_tasks": waiting_tasks,
                "completed_tasks": completed}
        for _, row in sorted(task_rows.items()):
            row["barrier_ready"] = barrier_ready
            self.writer.write("task_tick", row)
            for name in MEASURES:
                card[name] += row[name]
        self.writer.write("card_tick", card)
        for _, row in sorted(self.queues.items()):
            if row["occupancy_peak"]:
                self.writer.write("queue_stats", row)
        for _, row in sorted(self.links.items()):
            self.writer.write("link_stats", row)
        self.last_card = card

    def finish_tasks(self, task_states):
        for task_id, task in self.tasks.items():
            row = {"task_id": task_id, "start_tick": task.start_tick, "planned_end_tick": task.planned_end_tick,
                   "population_count": task.workload.population_count,
                   "logical_ticks": task.workload.T,
                   **task_states[task_id], **self.totals[task_id],
                   "rx_latency_p95_cycles": percentile(self.hist[task_id], 0.95),
                   "rx_excess_latency_p95_cycles": percentile(self.excess_hist[task_id], 0.95),
                   **self.derived(self.totals[task_id])}
            self.writer.write("task_summary", row)

    def total(self):
        return {name: sum(row[name] for row in self.totals.values()) for name in MEASURES}

    @staticmethod
    def derived(counts):
        def ratio(numerator, denominator):
            return counts[numerator] / counts[denominator] if counts[denominator] else 0.0
        return {
            "mean_source_wait_cycles": ratio("source_wait_cycles_sum", "tx_injected"),
            "mean_rx_latency_cycles": ratio("rx_latency_cycles_sum", "rx_ejected"),
            "mean_rx_excess_latency_cycles": ratio("rx_excess_latency_cycles_sum", "rx_ejected"),
            "router_wait_per_generated_flit": ratio("router_wait_flit_cycles", "generated_tx"),
        }
