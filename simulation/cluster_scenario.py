"""Validated homogeneous multi-card request trace, separate from fixed single-card scenes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from fingerprint.workload import Workload, load_workload
from fingerprint.scheduling import SchedulingFingerprint, load_fingerprint
from schedule.stps import STPSConfig
from simulation.noc import NoCConfig
from simulation.scenario import DEFAULT_MAX_TICKS, finite_number, keys, positive_int


@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    arrival_tick: int
    workload: Workload
    workload_path: Path
    workload_hash: str
    mean_compute_sops: float
    mean_noc_endpoint: float
    profile: SchedulingFingerprint | None = None
    profile_path: Path | None = None
    profile_hash: str | None = None


@dataclass(frozen=True)
class ClusterScenario:
    name: str
    cards: int
    network: NoCConfig
    cycles_per_tick: int
    neurons_per_core: int
    memory_mb: float
    compute_budget_sops: float
    noc_budget_endpoint: float
    tasks: tuple[TaskRequest, ...]
    max_ticks: int
    steady_window: tuple[int, int] | None
    scheduler_seed: int
    metadata: dict
    path: Path
    sha256: str
    stps: STPSConfig = STPSConfig()
    sliding_window_ticks: tuple[int, ...] = (1, 4, 8)
    physical_tick_ms: float | None = None


def load_cluster_scenario(path: str | Path) -> ClusterScenario:
    path = Path(path).resolve()
    raw = path.read_bytes()
    data = json.loads(raw)
    keys(data, {'schema_version', 'name', 'cluster', 'noc', 'tasks', 'max_ticks',
                'steady_window', 'scheduler_seed', 'metadata', 'stps',
                'sliding_window_ticks', 'sliding_window_ms', 'physical_tick_ms'}, 'cluster scenario')
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('cluster scenario requires schema_version=1')
    name = data.get('name', path.stem)
    if not isinstance(name, str) or not name.strip():
        raise ValueError('name must be a nonempty string')
    cluster, noc = data.get('cluster'), data.get('noc')
    keys(cluster, {'cards', 'mesh_x', 'mesh_y', 'neurons_per_core', 'memory_mb',
                   'compute_budget_sops', 'noc_budget_endpoint'}, 'cluster')
    keys(noc, {'cycles_per_tick', 'router_buffer_depth', 'source_buffer_depth',
               'sink_buffer_depth', 'sink_service_period'}, 'noc')
    cards = positive_int(cluster.get('cards'), 'cluster.cards')
    mesh_x = positive_int(cluster.get('mesh_x'), 'cluster.mesh_x')
    mesh_y = positive_int(cluster.get('mesh_y'), 'cluster.mesh_y')
    network = NoCConfig(mesh_x, mesh_y, **{
        key: positive_int(noc.get(key, default), 'noc.' + key)
        for key, default in (('router_buffer_depth', 4), ('source_buffer_depth', 4),
                             ('sink_buffer_depth', 4), ('sink_service_period', 1))})
    cycles = positive_int(noc.get('cycles_per_tick'), 'noc.cycles_per_tick')
    capacity = positive_int(cluster.get('neurons_per_core'), 'cluster.neurons_per_core')
    memory = finite_number(cluster.get('memory_mb'), 'cluster.memory_mb', positive=True)
    compute_budget = finite_number(cluster.get('compute_budget_sops'), 'cluster.compute_budget_sops', positive=True)
    noc_budget = finite_number(cluster.get('noc_budget_endpoint'), 'cluster.noc_budget_endpoint', positive=True)
    tasks_raw = data.get('tasks')
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError('tasks must be a nonempty list')
    cache, profile_cache, ids, tasks = {}, {}, set(), []
    for item in tasks_raw:
        keys(item, {'task_id', 'arrival_tick', 'workload', 'mean_compute_sops', 'mean_noc_endpoint', 'profile'}, 'task request')
        task_id = item.get('task_id')
        if not isinstance(task_id, str) or not task_id.strip() or task_id in ids:
            raise ValueError('task_id must be unique and nonempty')
        ids.add(task_id)
        filename = item.get('workload')
        if not isinstance(filename, str) or not filename:
            raise ValueError('workload must be a path')
        workload_path = (path.parent / filename).resolve()
        if workload_path not in cache:
            cache[workload_path] = (load_workload(workload_path),
                                   hashlib.sha256(workload_path.read_bytes()).hexdigest())
        workload, digest = cache[workload_path]
        profile = profile_path = profile_hash = None
        if 'profile' in item:
            if not isinstance(item['profile'], str) or not item['profile']:
                raise ValueError('profile must be a nonempty file path')
            profile_path = (path.parent / item['profile']).resolve()
            if profile_path not in profile_cache:
                profile_cache[profile_path] = (load_fingerprint(profile_path),
                    hashlib.sha256(profile_path.read_bytes()).hexdigest())
            profile, profile_hash = profile_cache[profile_path]
            if (profile.T != workload.T or profile.state_size_mb != workload.state_size_mb
                    or any(getattr(profile, field).tolist() != getattr(workload, field).tolist()
                           for field in ('pop_size', 'edge_src', 'edge_dst'))):
                raise ValueError(f'{task_id}: profile topology/T/memory must match replay workload')
        mean_compute = finite_number(item.get('mean_compute_sops', profile.mean_compute_sops if profile else None), 'mean_compute_sops')
        mean_noc = finite_number(item.get('mean_noc_endpoint', profile.mean_noc_endpoint if profile else None), 'mean_noc_endpoint')
        if profile is not None and not (math.isclose(mean_compute, profile.mean_compute_sops, rel_tol=1e-9, abs_tol=1e-9)
                and math.isclose(mean_noc, profile.mean_noc_endpoint, rel_tol=1e-9, abs_tol=1e-9)):
            raise ValueError(f'{task_id}: declared means must match calibration profile means')
        tasks.append(TaskRequest(task_id, positive_int(item.get('arrival_tick'), 'arrival_tick'),
                                 workload, workload_path, digest,
                                 mean_compute, mean_noc, profile, profile_path, profile_hash))
    tasks.sort(key=lambda task: (task.arrival_tick, task.task_id))
    try:
        for field in ('mean_compute_sops', 'mean_noc_endpoint'):
            math.fsum(getattr(task, field) for task in tasks)
        math.fsum(task.workload.totals['compute_sops'] for task in tasks)
    except OverflowError as exc:
        raise ValueError('combined task loads exceed finite float64 range') from exc
    max_ticks = positive_int(data.get('max_ticks', DEFAULT_MAX_TICKS), 'max_ticks')
    window = data.get('steady_window')
    if window is not None:
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError('steady_window must be [start_tick,end_tick]')
        window = tuple(positive_int(v, 'steady_window') for v in window)
        if window[0] > window[1]:
            raise ValueError('steady_window start must be <= end')
    seed = data.get('scheduler_seed', 0)
    if type(seed) is not int or seed < 0:
        raise ValueError('scheduler_seed must be a nonnegative integer')
    metadata = data.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('metadata must be an object')
    try:
        json.dumps(metadata, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError('metadata must be finite JSON') from exc
    stps_raw = data.get('stps', {})
    keys(stps_raw, {'d_max', 'gamma', 'max_rounds', 'objective', 'balance_slack',
                    'compute_weight', 'noc_weight', 'adaptive_ledger'}, 'stps')
    stps = STPSConfig(**stps_raw)
    physical_tick_ms = data.get('physical_tick_ms')
    if physical_tick_ms is not None:
        physical_tick_ms = finite_number(physical_tick_ms, 'physical_tick_ms', positive=True)
    widths = data.get('sliding_window_ticks', [1, 4, 8])
    if not isinstance(widths, list):
        raise ValueError('sliding_window_ticks must be a list of positive integers')
    sliding = {positive_int(value, 'sliding_window_ticks') for value in widths}
    requested_ms = data.get('sliding_window_ms', [])
    if not isinstance(requested_ms, list):
        raise ValueError('sliding_window_ms must be a list')
    if requested_ms and physical_tick_ms is None:
        raise ValueError('sliding_window_ms requires physical_tick_ms calibration')
    for value in requested_ms:
        milliseconds = finite_number(value, 'sliding_window_ms', positive=True)
        sliding.add(max(1, math.ceil(milliseconds / physical_tick_ms)))
    if not sliding:
        raise ValueError('at least one sliding window is required')
    return ClusterScenario(name, cards, network, cycles, capacity, memory, compute_budget,
                           noc_budget, tuple(tasks), max_ticks, window, seed, metadata,
                           path, hashlib.sha256(raw).hexdigest(), stps,
                           tuple(sorted(sliding)), physical_tick_ms)
