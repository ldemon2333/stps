"""Strict, explicit single-card experiment configuration. No scheduler."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from fingerprint.workload import Workload, load_workload
from simulation.noc import NoCConfig

DEFAULT_MAX_TICKS = 10_000


def positive_int(value, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def finite_number(value, name: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'} and finite")
    return value


def keys(obj, allowed, name):
    if not isinstance(obj, dict):
        raise ValueError(f"{name} must be an object")
    unknown = set(obj) - set(allowed)
    if unknown:
        raise ValueError(f"{name}: unknown fields {sorted(unknown)}")


@dataclass(frozen=True)
class TaskPlacement:
    task_id: str
    start_tick: int
    mapping: tuple[int, ...]
    workload: Workload
    workload_path: Path
    workload_hash: str

    @property
    def planned_end_tick(self) -> int:
        return self.start_tick + self.workload.T - 1


@dataclass(frozen=True)
class Scenario:
    name: str
    network: NoCConfig
    cycles_per_tick: int
    neurons_per_core: int
    memory_mb: float
    tasks: tuple[TaskPlacement, ...]
    max_ticks: int
    path: Path
    sha256: str


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path).resolve()
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid scenario JSON: {path}: {exc}") from exc
    keys(data, {"schema_version", "name", "card", "noc", "tasks", "max_ticks"}, "scenario")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("scenario requires schema_version=1")
    name = data.get("name", path.stem)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("scenario.name must be a nonempty string")
    card, noc = data.get("card"), data.get("noc")
    keys(card, {"mesh_x", "mesh_y", "neurons_per_core", "memory_mb"}, "card")
    keys(noc, {"cycles_per_tick", "router_buffer_depth", "source_buffer_depth",
               "sink_buffer_depth", "sink_service_period"}, "noc")
    mesh_x = positive_int(card.get("mesh_x"), "card.mesh_x")
    mesh_y = positive_int(card.get("mesh_y"), "card.mesh_y")
    capacity = positive_int(card.get("neurons_per_core"), "card.neurons_per_core")
    memory = finite_number(card.get("memory_mb"), "card.memory_mb", positive=True)
    cycles = positive_int(noc.get("cycles_per_tick"), "noc.cycles_per_tick")
    config = NoCConfig(mesh_x=mesh_x, mesh_y=mesh_y, **{
        key: positive_int(noc.get(key, default), f"noc.{key}")
        for key, default in (("router_buffer_depth", 4), ("source_buffer_depth", 4),
                             ("sink_buffer_depth", 4), ("sink_service_period", 1))
    })
    task_data = data.get("tasks")
    if not isinstance(task_data, list) or not task_data:
        raise ValueError("tasks must be a nonempty list")
    tasks = []
    ids = set()
    cache = {}
    for item in task_data:
        keys(item, {"task_id", "workload", "start_tick", "mapping"}, "task")
        task_id = item.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in ids:
            raise ValueError("task_id must be a unique nonempty string")
        ids.add(task_id)
        filename = item.get("workload")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"{task_id}: workload must be a file path")
        workload_path = (path.parent / filename).resolve()
        if workload_path not in cache:
            cache[workload_path] = (load_workload(workload_path),
                                   hashlib.sha256(workload_path.read_bytes()).hexdigest())
        workload, digest = cache[workload_path]
        mapping = item.get("mapping")
        if (not isinstance(mapping, list) or len(mapping) != workload.population_count
                or any(type(c) is not int or not 0 <= c < mesh_x * mesh_y for c in mapping)
                or len(set(mapping)) != len(mapping)):
            raise ValueError(f"{task_id}: mapping needs one distinct valid core per MicroPopulation")
        if any(int(n) > capacity for n in workload.pop_size):
            raise ValueError(f"{task_id}: MicroPopulation exceeds neurons_per_core")
        if workload.state_size_mb > memory:
            raise ValueError(f"{task_id}: state exceeds card memory")
        tasks.append(TaskPlacement(task_id, positive_int(item.get("start_tick"), "start_tick"),
                                   tuple(mapping), workload, workload_path, digest))
    tasks.sort(key=lambda task: (task.start_tick, task.task_id))
    try:
        math.fsum(task.workload.totals["compute_sops"] for task in tasks)
    except OverflowError as exc:
        raise ValueError("combined task compute_sops exceeds finite float64 range") from exc
    # Actual lifetimes depend on communication. Overlapping requests wait for
    # fixed mapped cores/memory at runtime; each task is individually feasible.
    max_ticks = positive_int(data.get("max_ticks", DEFAULT_MAX_TICKS), "max_ticks")
    return Scenario(name, config, cycles, capacity, memory, tuple(tasks), max_ticks,
                    path, hashlib.sha256(raw).hexdigest())
