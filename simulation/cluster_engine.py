"""Online baseline placement over independent cards with fixed physical time."""
from __future__ import annotations

from collections import Counter
import csv
from dataclasses import asdict
import json
from pathlib import Path
import time

from schedule.baselines import BaselinePolicy, CardSnapshot
from schedule.stps import CardForecastState, ForecastTask, choose_stps
from simulation.card_runtime import CardRuntime
from simulation.cluster_scenario import ClusterScenario, load_cluster_scenario
from simulation.engine import SimulationResult
from util.metrics import percentile


MAPPER_VERSION = 'row-major-free-v1'


def _record(request, card, tick, rejected):
    record = {
        'task_id': request.task_id, 'arrival_tick': request.arrival_tick,
        'population_count': request.workload.population_count, 'logical_ticks': request.workload.T,
        'workload_path': str(request.workload_path), 'workload_sha256': request.workload_hash,
        'source': request.workload.source, 'metadata': request.workload.metadata,
        'profile_path': str(request.profile_path) if request.profile_path else None,
        'profile_sha256': request.profile_hash,
        'input_totals': request.workload.totals,
        'mean_compute_sops': request.mean_compute_sops, 'mean_noc_endpoint': request.mean_noc_endpoint,
        'profile_total_compute_sops': request.profile.total_compute_sops if request.profile else None,
        'profile_total_noc_endpoint': request.profile.total_noc_endpoint if request.profile else None,
        'card_id': None, 'mapping': [], 'placement_tick': None, 'requested_start_tick': None,
        'phase_offset_ticks': 0, 'phase_wait_ticks': 0, 'actual_start_tick': None, 'completion_tick': None,
        'resource_wait_ticks': max(0, tick - request.arrival_tick + 1), 'boundary_wait_ticks': 0,
        'task_start_wait_ticks': max(0, tick - request.arrival_tick + 1),
        'observed_execution_ticks': 0, 'task_execution_ticks': None, 'task_end_to_end_ticks': None,
        'communication_extension_ticks': 0, 'slowdown': None, 'steps_started': 0, 'steps_completed': 0,
    }
    if rejected:
        record.update(status='unschedulable', resource_wait_ticks=0, task_start_wait_ticks=0,
                      rejection_reason='task exceeds per-card core, neuron or memory capacity')
    elif card is None:
        record['status'] = 'pending' if request.arrival_tick <= tick else 'not_arrived'
    else:
        p = card.progress[request.task_id]
        record.update(p.summary(tick))
        placement = card.placement_ticks[request.task_id]
        requested = p.placement.start_tick
        record.update(card_id=card.card_id, mapping=list(p.placement.mapping),
                      placement_tick=placement, requested_start_tick=requested,
                      phase_offset_ticks=requested - placement,
                      phase_wait_ticks=min(requested - placement, tick - placement + 1),
                      resource_wait_ticks=placement - request.arrival_tick,
                      boundary_wait_ticks=p.actual_start_tick - requested if p.actual_start_tick is not None else max(0, tick - requested + 1),
                      task_start_wait_ticks=p.actual_start_tick - request.arrival_tick if p.actual_start_tick is not None else tick - request.arrival_tick + 1,
                      task_end_to_end_ticks=p.completion_tick - request.arrival_tick + 1 if p.completion_tick is not None else None)
        if p.actual_start_tick is None:
            record['status'] = 'placed'
    return record


def run_cluster_simulation(scenario: ClusterScenario | str | Path, output_dir: str | Path,
                           *, policy: str = 'RR', seed: int | None = None,
                           trace: bool = False, report: bool = True) -> SimulationResult:
    if not isinstance(scenario, ClusterScenario):
        scenario = load_cluster_scenario(scenario)
    scheduler_seed = scenario.scheduler_seed if seed is None else seed
    if type(scheduler_seed) is not int or scheduler_seed < 0:
        raise ValueError('scheduler seed must be a nonnegative integer')
    scheduler = None if policy == 'STPS' else BaselinePolicy(policy, scheduler_seed, card_count=scenario.cards)
    if policy == 'STPS' and any(request.profile is None for request in scenario.tasks):
        raise ValueError('STPS requires an independent calibration profile for every task')
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f'output directory must be empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    cards = []
    started, cpu_started = time.perf_counter(), time.process_time()
    owner, rejected = {}, set()
    ticks, status = 0, 'max_ticks'
    schedule_seconds, attempts, evaluated_candidates = 0.0, 0, 0
    forecasts = {}
    journal = (output / 'decisions.csv').open('w', newline='', encoding='utf-8')
    decisions = csv.DictWriter(journal, fieldnames=(
        'physical_tick', 'task_id', 'arrival_tick', 'policy', 'action', 'card_id',
        'eligible_card_ids', 'sampled_card_ids', 'scores', 'mapping',
        'required_cores', 'state_size_mb', 'mean_compute_sops', 'mean_noc_endpoint',
        'phase_offset_ticks', 'predicted_actual_start', 'predicted_completion',
        'balance_compute_cv', 'balance_noc_cv', 'balance_max', 'balance_sum', 'candidates'))
    decisions.writeheader()
    resources_file = (output / 'card_resources.csv').open('w', newline='', encoding='utf-8')
    resources = csv.DictWriter(resources_file, fieldnames=(
        'physical_tick', 'card_id', 'used_cores', 'used_memory_mb', 'reserved_tasks',
        'running_tasks', 'placed_waiting_tasks', 'mean_compute_sops', 'mean_noc_endpoint',
        'assigned_compute_sops', 'assigned_noc_endpoint', 'ledger_adjustment_steps',
        'ledger_compute_correction_sops', 'ledger_noc_correction_endpoint'))
    resources.writeheader()
    try:
        for card_id in range(scenario.cards):
            cards.append(CardRuntime(card_id, scenario, output / 'cards' / f'card_{card_id}', trace))
        for ticks in range(1, scenario.max_ticks + 1):
            # Resource releases were committed at the previous ending boundary.
            # Every placement updates reservations before the next request is scored.
            for request in scenario.tasks:
                if request.arrival_tick > ticks or request.task_id in owner or request.task_id in rejected:
                    continue
                w = request.workload
                permanent = (w.population_count > scenario.network.core_count
                             or any(int(n) > scenario.neurons_per_core for n in w.pop_size)
                             or w.state_size_mb > scenario.memory_mb)
                base = {'physical_tick': ticks, 'task_id': request.task_id,
                        'arrival_tick': request.arrival_tick, 'policy': policy,
                        'required_cores': w.population_count, 'state_size_mb': w.state_size_mb,
                        'mean_compute_sops': request.mean_compute_sops, 'mean_noc_endpoint': request.mean_noc_endpoint}
                if permanent:
                    rejected.add(request.task_id)
                    decisions.writerow({**base, 'action': 'unschedulable'})
                    continue
                scheduling_started = time.perf_counter()
                eligible = [card for card in cards if card.can_host(request)]
                candidates = [CardSnapshot(
                    card.card_id, scenario.network.core_count, len(card.reserved), scenario.memory_mb,
                    card.used_memory_mb, card.mean_compute_sops, card.mean_noc_endpoint,
                    scenario.compute_budget_sops, scenario.noc_budget_endpoint)
                    for card in eligible]
                delay, prediction = 0, {}
                if policy == 'STPS':
                    states = []
                    for card in cards:
                        feasible = card in eligible
                        observed_tasks = tuple(ForecastTask(
                            task_id=task_id, profile=card.requests[task_id].profile,
                            mapping=p.placement.mapping, next_step=p.steps_started,
                            requested_start_tick=p.placement.start_tick, running=p.running)
                            for task_id, p in card.progress.items() if p.completion_tick is None)
                        states.append(CardForecastState(
                            card_id=card.card_id, tasks=observed_tasks, network_config=scenario.network,
                            cycles_per_tick=scenario.cycles_per_tick, current_tick=ticks,
                            round_open=card.round_start is not None,
                            outstanding=tuple(card.network.outstanding()),
                            compute_budget_sops=scenario.compute_budget_sops,
                            noc_budget_endpoint=scenario.noc_budget_endpoint,
                            mapping=card.preview_mapping(request) if feasible else (),
                            cumulative_assigned_compute_sops=card.assigned_compute_sops,
                            cumulative_assigned_noc_endpoint=card.assigned_noc_endpoint,
                            candidate_feasible=feasible))
                    decision = choose_stps(states, request.task_id, request.profile, scenario.stps)
                    selected = decision.card_id if decision is not None else None
                    if decision is not None:
                        delay = decision.delay
                        evaluated_candidates += decision.candidate_count
                        prediction = {'predicted_actual_start': decision.predicted_actual_start,
                                      'predicted_completion': decision.predicted_completion,
                                      'balance_compute_cv': getattr(decision, 'projected_compute_cv', None),
                                      'balance_noc_cv': getattr(decision, 'projected_noc_cv', None),
                                      'balance_max': getattr(decision, 'balance_primary', None),
                                      'balance_sum': getattr(decision, 'balance_secondary', None),
                                      'candidates': json.dumps(decision.candidates, sort_keys=True)}
                        forecasts[request.task_id] = {name: prediction[name] for name in (
                            'predicted_actual_start', 'predicted_completion')}
                    detail = {'eligible_card_ids': [card.card_id for card in eligible],
                              'sampled_card_ids': [], 'scores': list(decision.candidates) if decision else []}
                else:
                    selected = scheduler.select(candidates, w.population_count, w.state_size_mb,
                                                 request.mean_compute_sops, request.mean_noc_endpoint)
                    detail = scheduler.last_decision
                row = {**base, 'action': 'wait' if selected is None else 'place', 'card_id': selected,
                       'phase_offset_ticks': delay, **prediction,
                       **{name: json.dumps(detail[name], sort_keys=True) for name in
                          ('eligible_card_ids', 'sampled_card_ids', 'scores')}}
                if selected is not None:
                    placed = cards[selected].place(request, ticks, delay)
                    if policy == 'STPS' and placed.mapping != decision.mapping:
                        raise AssertionError('STPS preview mapping changed before atomic commit')
                    owner[request.task_id] = selected
                    row['mapping'] = json.dumps(placed.mapping)
                schedule_seconds += time.perf_counter() - scheduling_started
                attempts += 1
                decisions.writerow(row)
            for card in cards:
                card.advance_tick(ticks)
                incomplete = [p for p in card.progress.values() if p.completion_tick is None]
                resources.writerow({
                    'physical_tick': ticks, 'card_id': card.card_id,
                    'used_cores': len(card.reserved), 'used_memory_mb': card.used_memory_mb,
                    'reserved_tasks': len(incomplete), 'running_tasks': sum(p.running for p in incomplete),
                    'placed_waiting_tasks': sum(p.actual_start_tick is None for p in incomplete),
                    'mean_compute_sops': card.mean_compute_sops, 'mean_noc_endpoint': card.mean_noc_endpoint,
                    'assigned_compute_sops': card.assigned_compute_sops,
                    'assigned_noc_endpoint': card.assigned_noc_endpoint,
                    'ledger_adjustment_steps': card.ledger_adjustment_steps,
                    'ledger_compute_correction_sops': card.ledger_compute_correction_sops,
                    'ledger_noc_correction_endpoint': card.ledger_noc_correction_endpoint,
                })
            completed = sum(p.completion_tick is not None for card in cards for p in card.progress.values())
            if completed + len(rejected) == len(scenario.tasks):
                status = 'completed_with_rejections' if rejected else 'completed'
                break
        records = [_record(request, cards[owner[request.task_id]] if request.task_id in owner else None,
                           ticks, request.task_id in rejected) for request in scenario.tasks]
        for record in records:
            if record['task_id'] in forecasts:
                record.update(forecasts[record['task_id']])
                record['start_prediction_error_ticks'] = (record['actual_start_tick'] - record['predicted_actual_start']
                                                          if record['actual_start_tick'] is not None else None)
                record['completion_prediction_error_ticks'] = (record['completion_tick'] - record['predicted_completion']
                                                               if record['completion_tick'] is not None else None)
        for card in cards:
            card_status = ('completed' if all(p.completion_tick is not None for p in card.progress.values())
                           else 'max_ticks')
            card.finish(ticks, card_status, trace)
        from simulation.cluster_metrics import aggregate_cluster
        aggregate = aggregate_cluster(output, range(scenario.cards), records, ticks,
                                      scenario.steady_window, len(scenario.tasks),
                                      sliding_window_ticks=scenario.sliding_window_ticks)
        histogram, excess_histogram = Counter(), Counter()
        for card in cards:
            for histogram_task in card.metrics.hist.values():
                histogram.update(histogram_task)
            for histogram_task in card.metrics.excess_hist.values():
                excess_histogram.update(histogram_task)
        aggregate['derived']['rx_latency_p95_cycles'] = percentile(histogram, 0.95)
        aggregate['derived']['rx_excess_latency_p95_cycles'] = percentile(excess_histogram, 0.95)
        summary = {
            'schema_version': 3, 'scenario': scenario.name, 'policy': policy, 'status': status,
            'valid': status == 'completed', 'ticks_executed': ticks,
            'physical_tick_period_cycles': scenario.cycles_per_tick,
            'cycles_executed_per_card': ticks * scenario.cycles_per_tick,
            'cards': scenario.cards, 'config': asdict(scenario.network),
            'neurons_per_core': scenario.neurons_per_core, 'memory_mb': scenario.memory_mb,
            'compute_budget_sops': scenario.compute_budget_sops,
            'noc_budget_endpoint': scenario.noc_budget_endpoint,
            'mapper': MAPPER_VERSION, 'scheduler_seed': scheduler_seed,
            'stps': asdict(scenario.stps) if policy == 'STPS' else None,
            'scheduling': {'attempts': attempts, 'seconds': schedule_seconds,
                           'mean_seconds': schedule_seconds / attempts if attempts else 0.0,
                           'evaluated_candidates': evaluated_candidates,
                           'delayed_tasks': sum(record['phase_offset_ticks'] > 0 for record in records),
                           'phase_offset_ticks_sum': sum(record['phase_offset_ticks'] for record in records)},
            'max_ticks': scenario.max_ticks, 'steady_window': scenario.steady_window,
            'sliding_window_ticks': scenario.sliding_window_ticks,
            'physical_tick_ms': scenario.physical_tick_ms,
            'metadata': scenario.metadata, 'trace_enabled': trace,
            'scenario_path': str(scenario.path), 'scenario_sha256': scenario.sha256,
            'time_model': 'shared physical time; independent card-local elastic logical steps and Rx barriers',
            'compute_model': 'SOP accounting only; no compute service time',
            'prediction_input': 'independent calibration profile for STPS; same-profile means for baselines when provided; no future replay trace access',
            'tasks': records, **aggregate,
            'elapsed_seconds': time.perf_counter() - started,
            'cpu_seconds': time.process_time() - cpu_started,
        }
        (output / 'manifest.json').write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False,
                                                       sort_keys=True, indent=2) + '\n', encoding='utf-8')
        resolved = json.loads(scenario.path.read_text(encoding='utf-8'))
        paths = {request.task_id: str(request.workload_path) for request in scenario.tasks}
        profile_paths = {request.task_id: str(request.profile_path) for request in scenario.tasks if request.profile_path}
        for item in resolved['tasks']:
            item['workload'] = paths[item['task_id']]
            if item['task_id'] in profile_paths:
                item['profile'] = profile_paths[item['task_id']]
        (output / 'scenario.json').write_text(json.dumps(resolved, indent=2) + '\n', encoding='utf-8')
    finally:
        journal.close()
        resources_file.close()
        for card in cards:
            card.close()
    if report:
        from simulation.report import write_report
        from simulation.cluster_report import write_cluster_report
        for card in cards:
            write_report(card.writer.path)
        write_cluster_report(output)
    return SimulationResult(status, output, ticks, summary)
