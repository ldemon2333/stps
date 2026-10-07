"""Joint card/start-offset selection using offline profiles and observed state.

This is a bounded aggregate forecast, not a second packet simulator. XY paths,
source/destination demand, shared links and receive-buffer pressure determine
a calibrated cycle proxy. No future replay Workload is accepted by this API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from numbers import Integral, Real
from typing import Iterable, Mapping, Sequence

from fingerprint.scheduling import SchedulingFingerprint
from simulation.noc import NoCConfig


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _number(value, name, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not math.isfinite(value) or value < 0 or (positive and value == 0)):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")
    return float(value)


def _mapping(mapping, profile, network=None):
    result = tuple(_integer(core, "mapping core") for core in mapping)
    if len(result) != profile.population_count or len(set(result)) != len(result):
        raise ValueError("mapping must assign one distinct core per MicroPopulation")
    if network is not None and any(core >= network.core_count for core in result):
        raise ValueError("mapping core exceeds mesh")
    return result


@dataclass(frozen=True)
class STPSConfig:
    d_max: int = 4
    gamma: float = 1.0
    max_rounds: int = 10000
    objective: str = "completion"
    balance_slack: float = 0.10
    compute_weight: float = 1.0
    noc_weight: float = 1.0
    adaptive_ledger: bool = False

    def __post_init__(self):
        object.__setattr__(self, "d_max", _integer(self.d_max, "d_max"))
        gamma = _number(self.gamma, "gamma", positive=True)
        if gamma < 1:
            raise ValueError("gamma must be >= 1")
        object.__setattr__(self, "gamma", gamma)
        object.__setattr__(self, "max_rounds", _integer(self.max_rounds, "max_rounds", 1))
        if self.objective not in ("completion", "balance"):
            raise ValueError("objective must be 'completion' or 'balance'")
        object.__setattr__(self, "balance_slack", _number(
            self.balance_slack, "balance_slack"))
        object.__setattr__(self, "compute_weight", _number(
            self.compute_weight, "compute_weight", positive=True))
        object.__setattr__(self, "noc_weight", _number(
            self.noc_weight, "noc_weight", positive=True))
        if type(self.adaptive_ledger) is not bool:
            raise ValueError("adaptive_ledger must be bool")


@dataclass(frozen=True)
class ForecastTask:
    task_id: str
    profile: SchedulingFingerprint
    mapping: tuple[int, ...]
    next_step: int
    requested_start_tick: int
    running: bool

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("task_id must be nonempty")
        if not isinstance(self.profile, SchedulingFingerprint):
            raise ValueError("profile must be a SchedulingFingerprint")
        object.__setattr__(self, "mapping", _mapping(self.mapping, self.profile))
        step = _integer(self.next_step, "next_step")
        if step > self.profile.T or (not self.running and step != 0):
            raise ValueError("next_step is the next unissued profile step; pending tasks must have step 0")
        object.__setattr__(self, "next_step", step)
        object.__setattr__(self, "requested_start_tick", _integer(
            self.requested_start_tick, "requested_start_tick", 1))
        if type(self.running) is not bool:
            raise ValueError("running must be bool")


@dataclass(frozen=True)
class CardForecastState:
    card_id: int
    tasks: tuple[ForecastTask, ...]
    network_config: NoCConfig
    cycles_per_tick: int
    current_tick: int
    round_open: bool
    outstanding: tuple[dict, ...]
    compute_budget_sops: float
    noc_budget_endpoint: float
    mapping: tuple[int, ...]
    cumulative_assigned_compute_sops: float = 0.0
    cumulative_assigned_noc_endpoint: float = 0.0
    candidate_feasible: bool = True

    def __post_init__(self):
        object.__setattr__(self, "card_id", _integer(self.card_id, "card_id"))
        if not isinstance(self.network_config, NoCConfig):
            raise ValueError("network_config must be NoCConfig")
        object.__setattr__(self, "cycles_per_tick", _integer(
            self.cycles_per_tick, "cycles_per_tick", 1))
        object.__setattr__(self, "current_tick", _integer(self.current_tick, "current_tick", 1))
        if type(self.round_open) is not bool:
            raise ValueError("round_open must be bool")
        for name in ("compute_budget_sops", "noc_budget_endpoint"):
            object.__setattr__(self, name, _number(getattr(self, name), name, positive=True))
        for name in ("cumulative_assigned_compute_sops",
                     "cumulative_assigned_noc_endpoint"):
            object.__setattr__(self, name, _number(getattr(self, name), name))
        tasks = tuple(self.tasks)
        if any(not isinstance(task, ForecastTask) for task in tasks):
            raise ValueError("tasks must contain ForecastTask snapshots")
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ValueError("task_id values must be unique within a card")
        reserved = set()
        for task in tasks:
            _mapping(task.mapping, task.profile, self.network_config)
            if reserved.intersection(task.mapping):
                raise ValueError("forecast tasks cannot share reserved cores")
            reserved.update(task.mapping)
            if task.next_step == task.profile.T and not (self.round_open and task.running):
                raise ValueError("completed tasks must be removed from forecast state")
            if self.round_open and task.running and task.next_step == 0:
                raise ValueError("running tasks in an open round must have an issued step")
        mapping = tuple(_integer(core, "candidate mapping core") for core in self.mapping)
        if (len(set(mapping)) != len(mapping)
                or any(core >= self.network_config.core_count for core in mapping)
                or reserved.intersection(mapping)):
            raise ValueError("candidate mapping must contain distinct currently free cores")
        if self.round_open and not any(task.running for task in tasks):
            raise ValueError("an open round requires a running task")
        if type(self.candidate_feasible) is not bool:
            raise ValueError("candidate_feasible must be bool")
        if not self.candidate_feasible and mapping:
            raise ValueError("an infeasible candidate card must use an empty mapping")
        object.__setattr__(self, "tasks", tasks)
        object.__setattr__(self, "mapping", mapping)
        object.__setattr__(self, "outstanding", tuple(dict(row) for row in self.outstanding))


@dataclass(frozen=True)
class STPSDecision:
    card_id: int
    delay: int
    mapping: tuple[int, ...]
    predicted_actual_start: int
    predicted_completion: int
    J: int
    peak_comp: float
    peak_noc: float
    pressure: float
    projected_compute_cv: float
    projected_noc_cv: float
    balance_primary: float
    balance_secondary: float
    candidate_count: int
    candidates: tuple[dict, ...]


@dataclass
class _Demand:
    source: dict[int, float] = field(default_factory=dict)
    destination: dict[int, float] = field(default_factory=dict)
    links: dict[tuple[int, int], float] = field(default_factory=dict)
    latency: int = 0
    endpoints: float = 0.0

    def add(self, other):
        for ours, theirs in ((self.source, other.source),
                             (self.destination, other.destination), (self.links, other.links)):
            for key, value in theirs.items():
                ours[key] = ours.get(key, 0.0) + value
        self.latency = max(self.latency, other.latency)
        self.endpoints += other.endpoints


def _path(config, src, dst):
    """Directed XY links, with row-major core IDs (x changes first)."""
    path = []
    current = src
    x, y = src % config.mesh_x, src // config.mesh_x
    dx, dy = dst % config.mesh_x, dst // config.mesh_x
    while x != dx:
        x += 1 if dx > x else -1
        following = y * config.mesh_x + x
        path.append((current, following))
        current = following
    while y != dy:
        y += 1 if dy > y else -1
        following = y * config.mesh_x + x
        path.append((current, following))
        current = following
    return tuple(path)


def _add_flow(demand, src, dst, count, path, *, source=True):
    if count == 0:
        return
    if source:
        demand.source[src] = demand.source.get(src, 0.0) + count
    demand.destination[dst] = demand.destination.get(dst, 0.0) + count
    for link in path:
        demand.links[link] = demand.links.get(link, 0.0) + count
    demand.latency = max(demand.latency, len(path) + (2 if source else 1))
    demand.endpoints += count * (2 if source else 1)


def _sink_vector(config, occupancy):
    sink = [0.0] * config.core_count
    if occupancy is None:
        return sink
    items = occupancy.items() if isinstance(occupancy, Mapping) else enumerate(occupancy)
    if not isinstance(occupancy, Mapping) and len(occupancy) != config.core_count:
        raise ValueError("sink_occupancy must contain one value per core")
    for core, count in items:
        core = _integer(core, "sink core")
        if core >= config.core_count:
            raise ValueError("sink core exceeds mesh")
        count = _number(count, "sink occupancy")
        if count > config.sink_buffer_depth:
            raise ValueError("sink occupancy exceeds buffer capacity")
        sink[core] = count
    return sink


def _bound(config, demand, sink):
    values = [float(demand.latency)]
    values.extend(demand.source.values())
    values.extend(demand.destination.values())
    values.extend(demand.links.values())
    values.extend(config.sink_service_period * max(
        0.0, count - (config.sink_buffer_depth - sink[dst]))
        for dst, count in demand.destination.items())
    result = max(values)
    if not math.isfinite(result) or not math.isfinite(demand.endpoints):
        raise ValueError("aggregate prediction demand exceeds finite numeric range")
    return result


def demand_cycles(
    config: NoCConfig,
    flows: Iterable[tuple[int, int, float]],
    sink_occupancy: Mapping[int, float] | Sequence[float] | None = None,
) -> float:
    """Uncalibrated cycle proxy L for new (source, destination, flit-count) flows.

    Fractional calibration means stay fractional. Self traffic is local and
    contributes zero. L is neither a worst-case bound nor actual Rx latency.
    """
    if not isinstance(config, NoCConfig):
        raise ValueError("config must be NoCConfig")
    demand = _Demand()
    for src, dst, count in flows:
        src, dst = _integer(src, "source core"), _integer(dst, "destination core")
        if max(src, dst) >= config.core_count:
            raise ValueError("flow endpoint exceeds mesh")
        count = _number(count, "flow count")
        if src != dst:
            _add_flow(demand, src, dst, count, _path(config, src, dst))
    return _bound(config, demand, _sink_vector(config, sink_occupancy))


class _ProjectionCache:
    """Shared by a card's baseline and all delay candidates in one decision."""

    def __init__(self, config):
        self.config = config
        self.paths = {}
        self.steps = {}

    def project(self, task, step):
        key = (id(task.profile), task.mapping)
        if key not in self.paths:
            self.paths[key] = tuple(
                (task.mapping[int(src)], task.mapping[int(dst)],
                 _path(self.config, task.mapping[int(src)], task.mapping[int(dst)]))
                for src, dst in zip(task.profile.edge_src, task.profile.edge_dst))
        step_key = (*key, step)
        if step_key not in self.steps:
            demand = _Demand()
            for (src, dst, path), count in zip(
                    self.paths[key], task.profile.expected_edge_flits[step]):
                if src != dst:
                    _add_flow(demand, src, dst, float(count), path)
            self.steps[step_key] = demand
        return self.steps[step_key]


def _inventory(config, outstanding):
    demand, sink = _Demand(), [0.0] * config.core_count
    for row in outstanding:
        kind = row["kind"]
        count = _number(row["count"], "inventory count")
        src, dst = (_integer(row[name], name) for name in ("src_core", "dst_core"))
        router = _integer(row["router"], "router")
        if max(src, dst, router) >= config.core_count:
            raise ValueError("inventory core exceeds mesh")
        if kind == "sink_ni":
            sink[dst] += count
        elif kind in ("source_pending", "source_ni"):
            _add_flow(demand, src, dst, count, _path(config, src, dst))
        elif kind == "router":
            _add_flow(demand, router, dst, count,
                      _path(config, router, dst), source=False)
        else:
            raise ValueError(f"unknown outstanding inventory kind {kind!r}")
    return demand, _sink_vector(config, sink)


def residual_demand_cycles(config: NoCConfig, outstanding: Iterable[dict]) -> float:
    """Uncalibrated L for observed inventory, routing from each current location.

    Router inventory only needs remaining hops plus Rx. Source inventory also
    needs injection; sink inventory is already delivered and only uses space.
    """
    if not isinstance(config, NoCConfig):
        raise ValueError("config must be NoCConfig")
    demand, sink = _inventory(config, outstanding)
    return _bound(config, demand, sink)


def _duration(state, demand, sink, tick, config):
    """Finite aggregate sink approximation, without clipping overflow to B.

    Service opportunities use the global P-period phase. The model aggregates
    all arrivals across the round and can therefore credit a service slot
    before the corresponding packet would actually arrive. Fractional means,
    queue-empty gaps and FIFO ordering are not modeled packet by packet;
    independent validation must quantify this approximation's errors.
    """
    noc = state.network_config
    length = config.gamma * _bound(noc, demand, sink)
    if not math.isfinite(length):
        raise ValueError("calibrated prediction duration exceeds finite numeric range")
    cycles = math.ceil(length)
    begin_cycle = (tick - 1) * state.cycles_per_tick
    period = noc.sink_service_period
    # A closed-form capacity correction replaces an unbounded length loop.
    # Account for the current global service phase; never clamp overflow to B.
    required_slots = max((math.ceil(max(0.0,
        sink[dst] + count - noc.sink_buffer_depth))
        for dst, count in demand.destination.items()), default=0)
    if required_slots:
        cycles = max(cycles, (begin_cycle // period + required_slots) * period - begin_cycle)
    ticks = max(1, (cycles + state.cycles_per_tick - 1) // state.cycles_per_tick)
    end_cycle = begin_cycle + ticks * state.cycles_per_tick
    slots = end_cycle // period - begin_cycle // period
    following = [max(0.0, old + demand.destination.get(core, 0.0) - slots)
                 for core, old in enumerate(sink)]
    if any(value > noc.sink_buffer_depth + 1e-9 for value in following):
        raise ValueError("fluid receive inventory exceeds capacity after duration correction")
    return ticks, following


@dataclass
class _Tail:
    completions: dict[str, int] = field(default_factory=dict)
    starts: dict[str, int] = field(default_factory=dict)
    rounds: list[tuple[int, int]] = field(default_factory=list)
    peak_comp: float = 0.0
    peak_noc: float = 0.0
    residual_ticks: int = 0


def _predict(state, config, cache, residual, initial_sink, new_task=None):
    tasks = state.tasks + (() if new_task is None else (new_task,))
    steps = {task.task_id: task.next_step for task in tasks}
    running = {task.task_id for task in tasks if task.running}
    todo = {task.task_id: task for task in tasks}
    sink, tick = list(initial_sink), state.current_tick
    tail = _Tail(peak_noc=residual.endpoints)
    if state.round_open:
        length, sink = _duration(state, residual, sink, tick, config)
        tail.residual_ticks = length
        tail.rounds.append((tick, tick + length - 1))
        tick += length
        for task_id in tuple(running):
            if steps[task_id] == todo[task_id].profile.T:
                tail.completions[task_id] = tick - 1
                del todo[task_id]
                running.remove(task_id)
    while todo:
        if len(tail.rounds) >= config.max_rounds:
            raise ValueError("STPS forecast exceeds max_rounds; increase the explicit guard")
        active = [task for task in todo.values()
                  if task.task_id in running or task.requested_start_tick <= tick]
        if not active:
            following = min(task.requested_start_tick for task in todo.values())
            period, k = state.network_config.sink_service_period, state.cycles_per_tick
            slots = ((following - 1) * k) // period - ((tick - 1) * k) // period
            sink = [max(0.0, count - slots) for count in sink]
            tick = following
            continue
        demand, compute = _Demand(), 0.0
        for task in active:
            if task.task_id not in running:
                running.add(task.task_id)
                tail.starts[task.task_id] = tick
            step = steps[task.task_id]
            demand.add(cache.project(task, step))
            compute += float(task.profile.compute_total_sops[step])
        if not math.isfinite(compute):
            raise ValueError("aggregate compute prediction exceeds finite numeric range")
        tail.peak_comp = max(tail.peak_comp, compute)
        tail.peak_noc = max(tail.peak_noc, demand.endpoints)
        length, sink = _duration(state, demand, sink, tick, config)
        tail.rounds.append((tick, tick + length - 1))
        tick += length
        for task in active:
            steps[task.task_id] += 1
            if steps[task.task_id] == task.profile.T:
                tail.completions[task.task_id] = tick - 1
                del todo[task.task_id]
                running.remove(task.task_id)
    return tail


def _delays(state, baseline, d_max):
    """Monotone merge of equal predicted join boundaries in O(D + rounds)."""
    result = []
    cursor, previous = 0, None
    for delay in range(d_max + 1):
        requested = state.current_tick + delay
        while cursor < len(baseline.rounds) and baseline.rounds[cursor][1] < requested:
            cursor += 1
        join = requested
        if cursor < len(baseline.rounds):
            start, end = baseline.rounds[cursor]
            if start < requested <= end or (cursor == 0 and state.round_open and requested == start):
                join = end + 1
        if join != previous:
            result.append((delay, join))
        previous = join
    return result


def _projected_cvs(values, increment):
    """Population CV after adding ``increment`` to each index in turn.

    Scaling avoids overflow, and shared first/second moments make all card
    projections O(M), rather than rescanning M cards for every candidate.
    """
    scale = max(max(values), increment)
    if scale == 0:
        return [0.0] * len(values)
    scaled = [value / scale for value in values]
    delta = increment / scale
    total = math.fsum(scaled)
    total_sq = math.fsum(value * value for value in scaled)
    result = []
    for value in scaled:
        projected_total = total + delta
        mean = projected_total / len(scaled)
        projected_sq = total_sq + 2 * value * delta + delta * delta
        variance = max(0.0, projected_sq / len(scaled) - mean * mean)
        cv = math.sqrt(variance) / mean
        if not math.isfinite(cv):
            raise ValueError("projected cluster balance exceeds finite numeric range")
        result.append(cv)
    return result


def _balance_by_card(cards, profile, config):
    """Projected full-window allocation balance for each possible card.

    The state counters are cumulative assignments, including completed tasks.
    A candidate adds the new task's complete offline-profile totals. Delays do
    not change those totals, so all offsets on one card share this projection.
    """
    new_compute = _number(profile.total_compute_sops,
                          "profile total_compute_sops")
    new_noc = _number(profile.total_noc_endpoint,
                      "profile total_noc_endpoint")
    base_compute = [state.cumulative_assigned_compute_sops for state in cards]
    base_noc = [state.cumulative_assigned_noc_endpoint for state in cards]
    compute_cvs = _projected_cvs(base_compute, new_compute)
    noc_cvs = _projected_cvs(base_noc, new_noc)
    projections = {}
    for index, state in enumerate(cards):
        compute_cv = compute_cvs[index]
        noc_cv = noc_cvs[index]
        weighted_compute = config.compute_weight * compute_cv
        weighted_noc = config.noc_weight * noc_cv
        projections[state.card_id] = (
            compute_cv, noc_cv, max(weighted_compute, weighted_noc),
            weighted_compute + weighted_noc,
        )
    return projections


def choose_stps(
    states: Sequence[CardForecastState], task_id: str,
    profile: SchedulingFingerprint, config: STPSConfig = STPSConfig(),
) -> STPSDecision | None:
    """Evaluate every eligible card and distinct predicted offset together.

    ``completion`` ranks lexicographically by completion/externality cost, dual
    demand pressure, requested delay and card ID. ``balance`` first admits only
    candidates whose J is at most ``min_J * (1 + balance_slack)``, then
    minimizes projected cumulative compute/NoC CV. The caller owns resource
    feasibility and atomically commits the returned fixed mapping and delay.
    No mutation of snapshots, profiles, NoC state or reservations occurs here.
    """
    if not isinstance(config, STPSConfig):
        raise ValueError("config must be STPSConfig")
    if not isinstance(profile, SchedulingFingerprint):
        raise ValueError("profile must be a SchedulingFingerprint")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be nonempty")
    cards = sorted(states, key=lambda state: state.card_id)
    if len({state.card_id for state in cards}) != len(cards):
        raise ValueError("candidate card IDs must be unique")
    if len({state.current_tick for state in cards}) > 1:
        raise ValueError("all candidates must share one current physical Tick")
    if any(task.task_id == task_id for state in cards for task in state.tasks):
        raise ValueError("new task_id is already present in a forecast")
    balance_by_card = _balance_by_card(cards, profile, config) if cards else {}
    candidates = []
    for state in cards:
        if not state.candidate_feasible:
            continue
        mapping = _mapping(state.mapping, profile, state.network_config)
        cache = _ProjectionCache(state.network_config)
        residual, sink = _inventory(state.network_config, state.outstanding)
        if not state.round_open and residual.endpoints:
            raise ValueError("undelivered inventory requires an open card round")
        baseline = _predict(state, config, cache, residual, sink)
        for delay, expected_join in _delays(state, baseline, config.d_max):
            new_task = ForecastTask(task_id, profile, mapping, 0,
                                    state.current_tick + delay, False)
            forecast = _predict(state, config, cache, residual, sink, new_task)
            if forecast.starts[task_id] != expected_join:
                raise AssertionError("joining a candidate changed a preceding forecast round")
            externality = sum(max(0, forecast.completions[old] - finish)
                              for old, finish in baseline.completions.items())
            completion_cost = forecast.completions[task_id] - state.current_tick + 1
            pressure = max(forecast.peak_comp / state.compute_budget_sops,
                           forecast.peak_noc / state.noc_budget_endpoint)
            if not math.isfinite(pressure):
                raise ValueError("normalized prediction pressure exceeds finite numeric range")
            compute_cv, noc_cv, balance_primary, balance_secondary = (
                balance_by_card[state.card_id])
            candidates.append({
                "card_id": state.card_id, "delay": delay, "mapping": list(mapping),
                "predicted_actual_start": forecast.starts[task_id],
                "predicted_completion": forecast.completions[task_id],
                "J": completion_cost + externality, "completion_cost": completion_cost,
                "externality_ticks": externality, "peak_comp": forecast.peak_comp,
                "peak_noc": forecast.peak_noc, "pressure": pressure,
                "projected_compute_cv": compute_cv,
                "projected_noc_cv": noc_cv,
                "balance_primary": balance_primary,
                "balance_secondary": balance_secondary,
                "predicted_rounds": len(forecast.rounds),
                "predicted_residual_ticks": forecast.residual_ticks,
                "predicted_existing_completions": {
                    old: forecast.completions[old] for old in baseline.completions},
                "baseline_existing_completions": dict(baseline.completions),
            })
    if not candidates:
        return None
    if config.objective == "completion":
        best = min(candidates, key=lambda row: (
            row["J"], row["pressure"], row["delay"], row["card_id"]))
    else:
        min_j = min(row["J"] for row in candidates)
        admissible_j = min_j * (1 + config.balance_slack) + 1e-12
        for row in candidates:
            row["balance_admissible"] = row["J"] <= admissible_j
            row["balance_j_limit"] = admissible_j
        best = min((row for row in candidates if row["balance_admissible"]),
                   key=lambda row: (row["balance_primary"],
                                    row["balance_secondary"], row["J"],
                                    row["pressure"], row["delay"],
                                    row["card_id"]))
    return STPSDecision(
        card_id=best["card_id"], delay=best["delay"],
        mapping=tuple(best["mapping"]),
        predicted_actual_start=best["predicted_actual_start"],
        predicted_completion=best["predicted_completion"], J=best["J"],
        peak_comp=best["peak_comp"], peak_noc=best["peak_noc"],
        pressure=best["pressure"],
        projected_compute_cv=best["projected_compute_cv"],
        projected_noc_cv=best["projected_noc_cv"],
        balance_primary=best["balance_primary"],
        balance_secondary=best["balance_secondary"],
        candidate_count=len(candidates), candidates=tuple(candidates),
    )
