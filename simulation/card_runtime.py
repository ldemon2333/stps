"""One independent card clocked by the cluster, sharing the single-card NoC model."""
from __future__ import annotations

from dataclasses import asdict
import math
from pathlib import Path

from simulation.engine import _Progress, _verify_conservation
from simulation.noc import NoCNetwork
from simulation.scenario import TaskPlacement
from util.metrics import MetricsWriter, SimulationMetrics


class CardRuntime:
    def __init__(self, card_id, scenario, output_dir, trace):
        self.card_id, self.scenario = card_id, scenario
        self.writer = MetricsWriter(output_dir, trace)
        self.metrics = SimulationMetrics((), self.writer, scenario.network.mesh_x)
        self.network = NoCNetwork(scenario.network)
        self.progress, self.requests = {}, {}
        self.placement_ticks = {}
        self.reserved = set()
        self.used_memory_mb = 0.0
        self.mean_compute_sops = self.mean_noc_endpoint = 0.0
        self.assigned_compute_sops = self.assigned_noc_endpoint = 0.0
        self.ledger_adjustment_steps = 0
        self.ledger_compute_correction_sops = 0.0
        self.ledger_noc_correction_endpoint = 0.0
        self.round_start = None
        self.rounds_started = self.rounds_completed = 0
        self.extension_ticks = self.blocked_ticks = 0
        self.last_outstanding = []

    def can_host(self, request):
        w = request.workload
        return (w.population_count <= self.network.config.core_count - len(self.reserved)
                and all(int(n) <= self.scenario.neurons_per_core for n in w.pop_size)
                and self.used_memory_mb + w.state_size_mb <= self.scenario.memory_mb + 1e-9)

    def preview_mapping(self, request):
        """Read-only common mapper, also used for STPS candidate evaluation."""
        if not self.can_host(request):
            raise ValueError('placement no longer feasible')
        return tuple(core for core in range(self.network.config.core_count)
                     if core not in self.reserved)[:request.workload.population_count]

    def place(self, request, tick, phase_offset_ticks=0):
        if not self.can_host(request):
            raise ValueError('placement no longer feasible')
        if type(phase_offset_ticks) is not int or phase_offset_ticks < 0:
            raise ValueError('phase_offset_ticks must be a nonnegative integer')
        # Shared mapper for every policy: ascending available core IDs, population order.
        mapping = self.preview_mapping(request)
        task = TaskPlacement(request.task_id, tick + phase_offset_ticks, mapping, request.workload,
                             request.workload_path, request.workload_hash)
        self.metrics.add_task(task)
        self.progress[request.task_id] = _Progress(task)
        self.requests[request.task_id] = request
        self.placement_ticks[request.task_id] = tick
        self.reserved.update(mapping)
        self.used_memory_mb += request.workload.state_size_mb
        self.mean_compute_sops += request.mean_compute_sops
        self.mean_noc_endpoint += request.mean_noc_endpoint
        profile = request.profile
        self.assigned_compute_sops += (profile.total_compute_sops if profile else
                                       request.workload.totals['compute_sops'])
        self.assigned_noc_endpoint += (profile.total_noc_endpoint if profile else
                                      2 * sum(int(value) for value in request.workload.flits[
                                          :, request.workload.edge_src != request.workload.edge_dst].flat))
        return task

    @staticmethod
    def _checked_ledger_update(value, delta, name):
        updated = value + delta
        if not math.isfinite(updated):
            raise AssertionError(f'{name} committed ledger must remain finite')
        if updated < 0:
            if updated >= -1e-12:
                return 0.0
            raise AssertionError(f'{name} committed ledger became negative: {updated}')
        return updated

    def _commit_observed_step(self, task, logical):
        """Replace one profiled step with its observed offered workload.

        This hook is called exactly once when a logical step is issued.  It
        deliberately reads only that step: completed steps retain their actual
        contribution while unissued steps remain represented by the profile.
        """
        profile = self.requests[task.task_id].profile
        if profile is None or not self.scenario.stps.adaptive_ledger:
            return
        workload = task.workload
        remote = workload.edge_src != workload.edge_dst
        profile_remote = profile.edge_src != profile.edge_dst
        actual_compute = float(workload.compute_sops[logical].sum())
        predicted_compute = float(profile.compute_total_sops[logical])
        actual_noc = 2 * sum(int(value) for value in workload.flits[logical, remote])
        predicted_noc = 2 * float(profile.expected_edge_flits[logical, profile_remote].sum())
        compute_delta = actual_compute - predicted_compute
        noc_delta = actual_noc - predicted_noc
        compute = self._checked_ledger_update(
            self.assigned_compute_sops, compute_delta, 'compute')
        noc = self._checked_ledger_update(
            self.assigned_noc_endpoint, noc_delta, 'NoC endpoint')
        self.assigned_compute_sops = compute
        self.assigned_noc_endpoint = noc
        self.ledger_adjustment_steps += 1
        self.ledger_compute_correction_sops += compute_delta
        self.ledger_noc_correction_endpoint += noc_delta

    def _release(self, task_id):
        self.reserved.difference_update(self.progress[task_id].placement.mapping)
        remaining = [key for key, p in self.progress.items() if p.completion_tick is None]
        self.used_memory_mb = sum(self.requests[key].workload.state_size_mb for key in remaining)
        self.mean_compute_sops = sum(self.requests[key].mean_compute_sops for key in remaining)
        self.mean_noc_endpoint = sum(self.requests[key].mean_noc_endpoint for key in remaining)

    def _write_step(self, p, tick, completed, rx_complete):
        duration = tick - p.step_start_tick + 1
        self.writer.write('step_timing', {
            'task_id': p.placement.task_id, 'logical_tick': p.logical_tick,
            'step_start_tick': p.step_start_tick, 'step_end_tick': tick,
            'logical_step_duration_ticks': duration, 'communication_extension_ticks': duration - 1,
            'last_rx_cycle': p.last_rx_cycle, 'rx_complete': rx_complete, 'step_completed': completed,
        })

    def advance_tick(self, tick):
        self.metrics.begin_tick(tick)
        begin = (tick - 1) * self.scenario.cycles_per_tick
        started_ids, completed_ids = [], []
        if self.round_start is None:
            for p in self.progress.values():
                if p.actual_start_tick is None and p.placement.start_tick <= tick:
                    p.actual_start_tick = tick
                    started_ids.append(p.placement.task_id)
            active = [p for p in self.progress.values() if p.running]
            if active:
                self.round_start = tick
                self.rounds_started += 1
                for p in active:
                    task, logical = p.placement, p.steps_completed
                    p.steps_started += 1
                    p.step_start_tick, p.last_rx_cycle = tick, None
                    self._commit_observed_step(task, logical)
                    self.metrics.record_step(task, logical)
                    for edge, value in enumerate(task.workload.flits[logical]):
                        count = int(value)
                        if not count:
                            continue
                        src = task.mapping[int(task.workload.edge_src[edge])]
                        dst = task.mapping[int(task.workload.edge_dst[edge])]
                        self.writer.write('events', {
                            'kind': 'generate_local' if src == dst else 'generate', 'cycle': begin,
                            'physical_tick': tick, 'task_id': task.task_id, 'logical_tick': logical,
                            'edge_id': edge, 'src_core': src, 'dst_core': dst,
                            'count': count, 'generated_cycle': begin,
                        })
                        if src != dst:
                            self.network.offer(task.task_id, logical, edge, src, dst, count, begin)
        active = [p for p in self.progress.values() if p.running]
        if self.round_start is not None and tick > self.round_start:
            self.extension_ticks += 1
            for p in active:
                p.communication_extension_ticks += 1
        for cycle in range(begin, begin + self.scenario.cycles_per_tick):
            self.metrics.sample_queues(self.network.occupancy())
            for event in self.network.advance_cycle(cycle):
                self.metrics.event(event)
                p = self.progress[event['task_id']]
                if event['kind'] == 'rx' and p.running and p.logical_tick == event['logical_tick']:
                    p.last_rx_cycle = event['cycle']
        self.metrics.sample_end_boundary(self.network.occupancy())
        self.last_outstanding = self.network.outstanding()
        inventory = _verify_conservation(self.metrics, self.last_outstanding)
        ready = not self.network.pending_delivery()
        self.blocked_ticks += int(not ready)
        active_steps = {}
        for p in active:
            task_id = p.placement.task_id
            own_pending = bool(inventory[task_id]['source'] + inventory[task_id]['network'])
            active_steps[task_id] = {
                'logical_tick': p.logical_tick, 'step_started': p.step_start_tick == tick,
                'step_elapsed_ticks': tick - p.step_start_tick + 1,
                'waiting_for_communication': own_pending,
                'waiting_for_card_barrier': not ready and not own_pending,
            }
        if ready and self.round_start is not None:
            self.rounds_completed += 1
            for p in active:
                self._write_step(p, tick, True, True)
                p.steps_completed += 1
                if p.steps_completed == p.placement.workload.T:
                    p.completion_tick = tick
                    completed_ids.append(p.placement.task_id)
        self.metrics.end_tick(self.last_outstanding, active_steps,
                              sum(p.completion_tick is not None for p in self.progress.values()),
                              ready, self.round_start,
                              sum(p.actual_start_tick is None for p in self.progress.values()))
        for task_id in completed_ids:
            self._release(task_id)
        if ready:
            self.round_start = None
        return started_ids, completed_ids

    def finish(self, tick, status, trace):
        if self.round_start is not None:
            pending = {key: 0 for key in self.progress}
            for row in self.last_outstanding:
                if row['kind'] != 'sink_ni':
                    pending[row['task_id']] += row['count']
            for p in self.progress.values():
                if p.running:
                    self._write_step(p, tick, False, pending[p.placement.task_id] == 0)
        states = {key: p.summary(tick) for key, p in self.progress.items()}
        self.metrics.finish_tasks(states)
        for row in self.last_outstanding:
            self.writer.write('outstanding', {**row, 'physical_tick': tick,
                                              'delivery_pending': row['kind'] != 'sink_ni'})
        summary = {
            'schema_version': 2, 'scenario': f'{self.scenario.name} / card {self.card_id}',
            'card_id': self.card_id, 'status': status, 'valid': status == 'completed',
            'ticks_executed': tick, 'cycles_executed': tick * self.scenario.cycles_per_tick,
            'physical_tick_period_cycles': self.scenario.cycles_per_tick,
            'config': asdict(self.scenario.network), 'max_ticks': self.scenario.max_ticks,
            'neurons_per_core': self.scenario.neurons_per_core, 'memory_mb': self.scenario.memory_mb,
            'mapper': 'row-major-free-v1', 'trace_enabled': trace,
            'time_model': 'fixed physical tick; card-local Rx barrier; cross-tick queues',
            'totals': self.metrics.total(), 'derived': self.metrics.derived(self.metrics.total()),
            'peak_inventory_flits': self.metrics.peak_inventory,
            'timing': {'rounds_started': self.rounds_started, 'rounds_completed': self.rounds_completed,
                       'communication_extension_ticks': self.extension_ticks,
                       'barrier_blocked_ticks': self.blocked_ticks},
            'adaptive_ledger': {
                'assigned_compute_sops': self.assigned_compute_sops,
                'assigned_noc_endpoint': self.assigned_noc_endpoint,
                'adjustment_steps': self.ledger_adjustment_steps,
                'compute_correction_sops': self.ledger_compute_correction_sops,
                'noc_correction_endpoint': self.ledger_noc_correction_endpoint,
            },
            'tasks': [{
                'task_id': key, 'start_tick': p.placement.start_tick,
                'planned_end_tick': p.placement.planned_end_tick,
                'population_count': p.placement.workload.population_count,
                'logical_ticks': p.placement.workload.T, 'mapping': list(p.placement.mapping),
                'source': p.placement.workload.source, 'metadata': p.placement.workload.metadata,
                'input_totals': p.placement.workload.totals,
                'active_edges': int(sum((p.placement.workload.flits > 0).any(axis=0))),
                'workload_path': str(p.placement.workload_path),
                'workload_sha256': p.placement.workload_hash, **states[key],
            } for key, p in self.progress.items()],
        }
        self.writer.json('manifest.json', summary)
        self.writer.close()
        return summary

    def close(self):
        self.writer.close()
