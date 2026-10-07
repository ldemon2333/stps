#!/usr/bin/env python3
"""Predeclared, paired synthetic STPS/baseline experiment with held-out traces.

The experiment uses independent calibration, predictor validation and replay
samples. Parameters and sample generation rules are written before calibration
or policy execution. No parameter is fitted to the comparison outcomes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import html
import itertools
import json
import math
from pathlib import Path
import statistics
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fingerprint.scheduling import build_fingerprint, save_fingerprint
from fingerprint.workload import Workload, load_workload, save_workload
from schedule.baselines import POLICIES as BASELINES
from simulation.arrivals import make_arrival_ticks
from simulation.cluster_engine import run_cluster_simulation
from simulation.cluster_scenario import load_cluster_scenario
from simulation.noc import NoCConfig, NoCNetwork


POLICIES = (*BASELINES, 'STPS')
TEMPLATES = ('light_sparse', 'compute_heavy', 'fan_in', 'wide_burst')
CONFIG = NoCConfig(4, 4, 2, 2, 2, 1)
CYCLES_PER_TICK = 8
CALIBRATION_COUNT = 8
VALIDATION_COUNT = 4
GAMMA_QUANTILE = 0.9
KEY_METRICS = (
    'full_compute_sops_cv', 'full_endpoint_events_cv',
    'steady_compute_sops_cv', 'steady_endpoint_events_cv',
    'mean_task_end_to_end_ticks', 'mean_communication_extension_ticks',
    'mean_rx_excess_latency_cycles', 'router_wait_per_generated_flit',
    'makespan_ticks', 'task_throughput_per_tick', 'scheduling_seconds',
)
BALANCE_METRICS = tuple(f'{scope}_{load}_{metric}'
                        for scope in ('full', 'steady')
                        for load in ('compute_sops', 'tx_injected', 'rx_ejected', 'endpoint_events')
                        for metric in ('cv', 'jfi', 'lif'))
COMPARISON_METRICS = tuple(dict.fromkeys((*KEY_METRICS, *BALANCE_METRICS,
    'p95_task_end_to_end_ticks', 'mean_task_execution_ticks',
    'mean_resource_wait_ticks', 'mean_boundary_wait_ticks',
    'mean_rx_latency_cycles', 'rx_excess_latency_p95_cycles',
    'mean_source_wait_cycles')))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                               sort_keys=True, indent=2) + '\n', encoding='utf-8')


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def perturbed_workload(template: Workload, *, seed: int | list[int], split: str,
                       template_name: str) -> Workload:
    """Independent traffic/SOP amplitudes; topology and all zero slots persist."""
    traffic_seed, compute_seed = np.random.SeedSequence(seed).spawn(2)
    traffic_rng = np.random.default_rng(traffic_seed)
    compute_rng = np.random.default_rng(compute_seed)
    return Workload(
        template.pop_size, template.edge_src, template.edge_dst,
        template.edge_expected_flits * traffic_rng.uniform(0.6, 1.4, template.edge_expected_flits.shape),
        template.compute_sops * compute_rng.uniform(0.6, 1.4, template.compute_sops.shape),
        template.state_size_mb, f'synthetic:stps-comparison:{split}:{template_name}',
        metadata={
            'split': split, 'seed': seed, 'template': template_name,
            'amplitude_distribution': 'independent entrywise U(0.6,1.4) for traffic and SOP',
            'quantization': 'Workload decimal per-edge cumulative floor',
            'zeros_and_topology': 'preserved from manually specified template',
            'compute_model': 'independent SOP accounting; no compute service time',
            'provenance': 'synthetic variation; not measured SNN or hardware data',
        },
    )


def frozen_plan(seeds: list[int], task_counts: list[int]) -> dict:
    source_paths = [ROOT / 'script/compare_stps.py', ROOT / 'schedule/stps.py',
                    ROOT / 'fingerprint/scheduling.py', ROOT / 'simulation/noc.py',
                    ROOT / 'simulation/cluster_engine.py', ROOT / 'simulation/card_runtime.py']
    return {
        'schema_version': 1,
        'written_before': 'calibration, validation, test input generation and policy execution',
        'test_outcome_tuning': False,
        'provenance': 'synthetic small graphs with independent traffic and SOP variation',
        'policies': list(POLICIES), 'seeds': seeds, 'task_counts': task_counts,
        'arrival_modes': ['poisson', 'bursty'],
        'total_runs': len(POLICIES) * len(seeds) * len(task_counts) * 2,
        'network': asdict(CONFIG), 'cycles_per_tick': CYCLES_PER_TICK,
        'cluster': {'cards': 4, 'mesh_x': 4, 'mesh_y': 4, 'neurons_per_core': 64,
                    'memory_mb': 8, 'compute_budget_sops': 500, 'noc_budget_endpoint': 40},
        'max_ticks': 800, 'steady_window': [5, 40], 'mapper': 'row-major-free-v1',
        'stps': {'d_max': 4, 'gamma_quantile': GAMMA_QUANTILE, 'max_rounds': 10000,
                 'gamma_rule': 'max(1, linear_quantile(actual_cycle_drain / demand_cycles, 0.9))'},
        'sampling': {
            'calibration_per_template': CALIBRATION_COUNT,
            'validation_per_template': VALIDATION_COUNT,
            'calibration_seed': '1000 + 100 * template_index + sample_index',
            'validation_seed': '2000 + 100 * template_index + sample_index',
            'test_seed': '[3000 + trial_seed, task_index]',
            'task_order_seed': '[4000, trial_seed]',
            'task_mix': 'equal template counts when divisible by four; deterministic shuffle',
            'amplitudes': 'independent entrywise U(0.6,1.4); preserve zeros; Workload quantization',
            'logical_steps': 8,
            'mean_information': 'all policies receive means from the same eight-sample profile',
            'same_input': 'all policies share exact workloads and arrivals; modes share workloads',
        },
        'arrival_parameters': {
            'normal_task_count_lte_24': {'poisson_rate': 1.2, 'burst_size': 6, 'burst_interval': 6},
            'heavy_task_count_gt_24': {'poisson_rate': 2.4, 'burst_size': 12, 'burst_interval': 6},
        },
        'calibration': {
            'combinations': 'four single templates and every two-template multiset that fits 16 cores',
            'rounds': 'each logical step offered simultaneously at boundary zero',
            'mapping': 'contiguous row-major; alternate starting at core 0 and last fitting block',
            'pair_samples': '(sample_index + 3 * position) modulo split sample count',
            'sink_at_start': 'empty',
            'validation': 'independent samples; known-flow and mean-profile error plus residual after K cycles',
            'round_error': 'ceil(max(1, gamma * L) / K) versus max(1, ceil(actual_cycles/K))',
        },
        'analysis': {
            'primary_balance': ['full_compute_sops_cv', 'full_endpoint_events_cv',
                                'steady_compute_sops_cv', 'steady_endpoint_events_cv'],
            'all_balance': 'CV/JFI/LIF for SOP, Tx, Rx, Tx+Rx; full and pre-fixed steady window',
            'latency': 'end-to-end includes arrival wait and deliberate phase delay',
            'pairing': 'arrival mode, task count, seed; STPS minus each baseline',
            'statistics': 'mean, sample standard deviation, paired win/tie/loss; no significance claim',
            'invalid_runs': 'retained and counted; excluded from completed-run metric comparisons',
        },
        'template_sha256': {name: sha256(ROOT / 'examples/cluster/workloads' / f'{name}.json')
                            for name in TEMPLATES},
        'source_sha256': {str(path.relative_to(ROOT)): sha256(path)
                          for path in source_paths if path.exists()},
    }


def prepare_profiles(output: Path, plan: dict):
    templates = {name: load_workload(ROOT / 'examples/cluster/workloads' / f'{name}.json')
                 for name in TEMPLATES}
    profiles, calibration, validation = {}, {}, {}
    for index, (name, template) in enumerate(templates.items()):
        for split, count, start, target in (
                ('calibration', CALIBRATION_COUNT, 1000, calibration),
                ('validation', VALIDATION_COUNT, 2000, validation)):
            samples = []
            for sample in range(count):
                seed = start + index * 100 + sample
                workload = perturbed_workload(template, seed=seed, split=split, template_name=name)
                save_workload(output / 'samples' / split / name / f'{sample:02}.json', workload)
                samples.append(workload)
            target[name] = samples
        profile = build_fingerprint(
            calibration[name], source=f'synthetic:stps-comparison:calibration:{name}',
            metadata={'template': name, 'split': 'calibration',
                      'sample_seeds': [1000 + index * 100 + i for i in range(CALIBRATION_COUNT)],
                      'template_sha256': plan['template_sha256'][name],
                      'mean_definition': 'each sample quantized before averaging',
                      'validation_and_test_samples_used': False})
        save_fingerprint(output / 'profiles' / f'{name}.json', profile)
        profiles[name] = profile
    return templates, profiles, calibration, validation


def mapped_flows(items, mappings, logical_tick: int, *, profile=False):
    flows = []
    for item, mapping in zip(items, mappings):
        counts = item.expected_edge_flits[logical_tick] if profile else item.flits[logical_tick]
        for src, dst, count in zip(item.edge_src, item.edge_dst, counts):
            if src != dst and count > 0:
                flows.append((mapping[int(src)], mapping[int(dst)], float(count)))
    return flows


def actual_drain_cycles(flows, *, config: NoCConfig = CONFIG, max_cycles=100000,
                        residual_snapshots: list | None = None) -> int:
    network = NoCNetwork(config)
    for edge, (src, dst, count) in enumerate(flows):
        if int(count) != count:
            raise ValueError('actual network calibration requires integer flits')
        network.offer('calibration', 0, edge, src, dst, int(count), 0)
    cycle = 0
    while network.pending_delivery():
        if cycle >= max_cycles:
            raise RuntimeError('independent calibration network did not drain')
        network.advance_cycle(cycle)
        cycle += 1
        if cycle == CYCLES_PER_TICK and network.pending_delivery() and residual_snapshots is not None:
            residual_snapshots.append(network.outstanding())
    return cycle


def calibration_rows(samples, profiles):
    from schedule.stps import demand_cycles, residual_demand_cycles

    combinations = [(name,) for name in TEMPLATES]
    combinations += list(itertools.combinations_with_replacement(TEMPLATES, 2))
    rows = []
    sample_count = len(samples[TEMPLATES[0]])
    for names in combinations:
        required = sum(profiles[name].population_count for name in names)
        if required > CONFIG.core_count:
            continue
        for sample in range(sample_count):
            items = [samples[name][(sample + 3 * pos) % sample_count]
                     for pos, name in enumerate(names)]
            offset = 0 if sample % 2 == 0 else CONFIG.core_count - required
            mappings = []
            for item in items:
                mappings.append(tuple(range(offset, offset + item.population_count)))
                offset += item.population_count
            for logical_tick in range(items[0].T):
                flows = mapped_flows(items, mappings, logical_tick)
                profile_flows = mapped_flows([profiles[name] for name in names], mappings,
                                             logical_tick, profile=True)
                proxy = demand_cycles(CONFIG, flows)
                profile_proxy = demand_cycles(CONFIG, profile_flows)
                snapshots = []
                actual = actual_drain_cycles(flows, residual_snapshots=snapshots)
                rows.append({
                    'templates': '+'.join(names), 'sample': sample,
                    'logical_tick': logical_tick, 'mapping': json.dumps(mappings),
                    'remote_flits': sum(int(flow[2]) for flow in flows),
                    'actual_cycles': actual, 'known_flow_proxy_cycles': proxy,
                    'profile_proxy_cycles': profile_proxy,
                    'ratio_actual_over_proxy': actual / proxy if proxy else None,
                    'residual_actual_cycles': actual - CYCLES_PER_TICK if snapshots else None,
                    'residual_proxy_cycles': residual_demand_cycles(CONFIG, snapshots[0]) if snapshots else None,
                    'residual_inventory': json.dumps(snapshots[0], sort_keys=True) if snapshots else '',
                })
    return rows


def error_statistics(rows: list[dict], prefix: str) -> dict:
    # Silence is meaningful but would otherwise make prediction look better.
    active = [row for row in rows if row['actual_cycles'] > 0]
    result = {'samples': len(rows), 'active_samples': len(active), 'silent_samples': len(rows) - len(active)}
    for scope, selected in (('all', rows), ('active', active)):
        cycle_errors = [float(row[f'{prefix}_cycles']) - row['actual_cycles'] for row in selected]
        round_errors = [int(row[f'{prefix}_rounds']) - row['actual_rounds'] for row in selected]
        count = len(selected)
        result[scope] = {
            'samples': count,
            'cycle_mae': statistics.fmean(map(abs, cycle_errors)) if count else 0.0,
            'cycle_bias': statistics.fmean(cycle_errors) if count else 0.0,
            'cycle_p95_absolute_error': float(np.quantile(np.abs(cycle_errors), 0.95)) if count else 0.0,
            'round_mae': statistics.fmean(map(abs, round_errors)) if count else 0.0,
            'round_bias': statistics.fmean(round_errors) if count else 0.0,
            'round_exact_count': sum(error == 0 for error in round_errors),
            'round_under_count': sum(error < 0 for error in round_errors),
            'round_over_count': sum(error > 0 for error in round_errors),
            'round_exact_ratio': sum(error == 0 for error in round_errors) / count if count else 0.0,
        }
    return result


def calibrate(output, profiles, calibration, validation):
    started = time.perf_counter()
    rows = calibration_rows(calibration, profiles)
    ratios = [row['ratio_actual_over_proxy'] for row in rows if row['ratio_actual_over_proxy'] is not None]
    gamma = max(1.0, float(np.quantile(ratios, GAMMA_QUANTILE))) if ratios else 1.0
    write_csv(output / 'calibration' / 'calibration.csv', rows)
    validation_rows = calibration_rows(validation, profiles)
    residual_rows = []
    for row in validation_rows:
        row['actual_rounds'] = max(1, math.ceil(row['actual_cycles'] / CYCLES_PER_TICK))
        for prefix, source in (('known_flow', 'known_flow_proxy_cycles'),
                               ('mean_profile', 'profile_proxy_cycles')):
            predicted = gamma * row[source]
            row[f'{prefix}_cycles'] = predicted
            row[f'{prefix}_rounds'] = max(1, math.ceil(predicted / CYCLES_PER_TICK))
        if row['residual_actual_cycles'] is not None:
            predicted = gamma * row['residual_proxy_cycles']
            row['residual_predicted_cycles'] = predicted
            row['residual_actual_rounds'] = math.ceil(row['residual_actual_cycles'] / CYCLES_PER_TICK)
            row['residual_predicted_rounds'] = max(1, math.ceil(predicted / CYCLES_PER_TICK))
            residual_rows.append({
                'actual_cycles': row['residual_actual_cycles'], 'actual_rounds': row['residual_actual_rounds'],
                'residual_cycles': predicted, 'residual_rounds': row['residual_predicted_rounds'],
            })
    write_csv(output / 'calibration' / 'validation.csv', validation_rows)
    calibration_summary = {
        'gamma': gamma, 'gamma_quantile': GAMMA_QUANTILE,
        'fit_samples': len(rows), 'nonzero_fit_samples': len(ratios),
        'network': asdict(CONFIG), 'cycles_per_tick': CYCLES_PER_TICK,
        'mapper': 'row-major-free-v1',
        'known_flow_validation': error_statistics(validation_rows, 'known_flow'),
        'mean_profile_validation': error_statistics(validation_rows, 'mean_profile'),
        'residual_validation': error_statistics(residual_rows, 'residual'),
        'elapsed_seconds': time.perf_counter() - started,
        'limits': 'new rounds start with empty sink; residual validation observes K-cycle snapshots; no hardware fidelity claim',
    }
    write_json(output / 'calibration' / 'summary.json', calibration_summary)
    return calibration_summary


def prepare_scene(output, templates, profiles, task_count, seed, mode, gamma, plan):
    trial_root = output / 'inputs' / f'tasks_{task_count}' / f'seed_{seed}'
    kinds = [TEMPLATES[index % len(TEMPLATES)] for index in range(task_count)]
    np.random.default_rng(np.random.SeedSequence([4000, seed])).shuffle(kinds)
    arrival_parameters = plan['arrival_parameters'][
        'normal_task_count_lte_24' if task_count <= 24 else 'heavy_task_count_gt_24']
    arrivals = make_arrival_ticks(mode, task_count, seed=seed, **arrival_parameters)
    requests = []
    for index, (kind, arrival) in enumerate(zip(kinds, arrivals)):
        filename = trial_root / 'workloads' / f'task_{index:04}.json'
        if not filename.exists():
            workload = perturbed_workload(templates[kind], seed=[3000 + seed, index],
                                          split='test', template_name=kind)
            save_workload(filename, workload)
        requests.append({
            'task_id': f'task-{index:04}', 'arrival_tick': arrival,
            'workload': f'workloads/task_{index:04}.json',
            'profile': str((output / 'profiles' / f'{kind}.json').resolve()),
            'mean_compute_sops': profiles[kind].mean_compute_sops,
            'mean_noc_endpoint': profiles[kind].mean_noc_endpoint,
        })
    scene = {
        'schema_version': 1, 'name': f'stps-{mode}-n{task_count}-seed{seed}',
        'cluster': plan['cluster'],
        'noc': {'cycles_per_tick': CYCLES_PER_TICK, **{
            key: value for key, value in asdict(CONFIG).items() if key not in ('mesh_x', 'mesh_y')}},
        'max_ticks': plan['max_ticks'], 'steady_window': plan['steady_window'],
        'scheduler_seed': seed, 'tasks': requests,
        'stps': {'d_max': 4, 'gamma': gamma, 'max_rounds': 10000},
        'metadata': {
            'provenance': plan['provenance'], 'arrival_mode': mode,
            'arrival_seed': seed, 'arrival_parameters': arrival_parameters,
            'mean_profile_source': 'independent calibration profiles shared by every policy',
            'mapping': plan['mapper'],
            'plan_sha256': sha256(output / 'plan.json'),
            'calibration_summary_sha256': sha256(output / 'calibration/summary.json'),
            'task_order': kinds,
        },
    }
    filename = trial_root / f'{mode}.json'
    write_json(filename, scene)
    return filename


def run_row(summary, *, mode, task_count, seed, policy, folder, output):
    row = {
        'arrival_mode': mode, 'task_count': task_count, 'seed': seed, 'policy': policy,
        'status': summary['status'], 'valid': summary['valid'],
        'scenario_sha256': summary['scenario_sha256'],
        'manifest': str((folder / 'manifest.json').relative_to(output)),
        'report': str((folder / 'report.html').relative_to(output)) if (folder / 'report.html').exists() else '',
        'elapsed_seconds': summary['elapsed_seconds'], 'cpu_seconds': summary['cpu_seconds'],
    }
    for section in ('timing', 'derived', 'totals'):
        row.update({key: value for key, value in summary[section].items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)})
    for key, value in summary.get('scheduling', {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            row[f'scheduling_{key}'] = value
    for scope, window in summary['balance_windows'].items():
        row[f'{scope}_complete'] = window['complete']
        row[f'{scope}_observed_ticks'] = window['observed_ticks']
        for load, metrics in window['balance'].items():
            for metric in ('cv', 'jfi', 'lif'):
                for part in ('value', 'numerator', 'denominator'):
                    suffix = '' if part == 'value' else '_' + part
                    row[f'{scope}_{load}_{metric}{suffix}'] = metrics[metric][part]
    tasks = summary['tasks']
    for key in ('completion_prediction_error_ticks', 'start_prediction_error_ticks'):
        values = [task[key] for task in tasks if task.get(key) is not None]
        if values:
            row[f'{key}_mae'] = statistics.fmean(map(abs, values))
            row[f'{key}_bias'] = statistics.fmean(values)
            row[f'{key}_samples'] = len(values)
    return row


def higher_is_better(metric: str) -> bool:
    return metric.endswith('_jfi') or metric == 'task_throughput_per_tick'


def paired_comparison(stps_rows, baseline_rows, metric: str) -> dict:
    pairs = [(stps_rows[seed][metric], baseline_rows[seed][metric])
             for seed in sorted(stps_rows.keys() & baseline_rows.keys())
             if stps_rows[seed]['valid'] and baseline_rows[seed]['valid']
             and metric in stps_rows[seed] and metric in baseline_rows[seed]]
    # Positive improvement always favors STPS; raw delta preserves its sign.
    direction = 1 if higher_is_better(metric) else -1
    deltas = [stps - baseline for stps, baseline in pairs]
    improvement = [direction * value for value in deltas]
    relative = [100 * direction * (stps - baseline) / abs(baseline)
                for stps, baseline in pairs if baseline != 0]
    return {
        'paired_samples': len(pairs),
        'mean_stps_minus_baseline': statistics.fmean(deltas) if pairs else None,
        'mean_improvement': statistics.fmean(improvement) if pairs else None,
        'mean_relative_improvement_percent': statistics.fmean(relative) if relative else None,
        'relative_samples': len(relative),
        'wins': sum(value > 1e-12 for value in improvement),
        'ties': sum(abs(value) <= 1e-12 for value in improvement),
        'losses': sum(value < -1e-12 for value in improvement),
    }


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['arrival_mode'], row['task_count'], row['policy'])].append(row)
    metrics = sorted({key for row in rows for key, value in row.items()
                      if isinstance(value, (int, float)) and not isinstance(value, bool)
                      and key not in ('seed', 'task_count')})
    summaries, paired, best = [], [], []
    for (mode, count, policy), members in sorted(groups.items()):
        complete = [row for row in members if row['valid']]
        entry = {'arrival_mode': mode, 'task_count': count, 'policy': policy,
                 'runs': len(members), 'completed_runs': len(complete), 'metrics': {}}
        for metric in metrics:
            values = [row[metric] for row in complete if metric in row]
            if values:
                entry['metrics'][metric] = {
                    'mean': statistics.fmean(values),
                    'std': statistics.stdev(values) if len(values) > 1 else 0.0,
                    'min': min(values), 'max': max(values), 'samples': len(values),
                }
        summaries.append(entry)
    for mode, count in sorted({(row['arrival_mode'], row['task_count']) for row in rows}):
        stps = {row['seed']: row for row in groups[(mode, count, 'STPS')]}
        selected = [entry for entry in summaries if entry['arrival_mode'] == mode
                    and entry['task_count'] == count]
        for policy in BASELINES:
            baseline = {row['seed']: row for row in groups[(mode, count, policy)]}
            for metric in COMPARISON_METRICS:
                result = paired_comparison(stps, baseline, metric)
                if result['paired_samples']:
                    paired.append({'arrival_mode': mode, 'task_count': count,
                                   'baseline': policy, 'metric': metric, **result})
        for metric in KEY_METRICS:
            options = [entry for entry in selected if entry['policy'] != 'STPS'
                       and metric in entry['metrics']]
            if not options:
                continue
            winner = min(options, key=lambda entry: (
                (-1 if higher_is_better(metric) else 1) * entry['metrics'][metric]['mean'],
                entry['policy']))
            baseline = {row['seed']: row for row in groups[(mode, count, winner['policy'])]}
            result = paired_comparison(stps, baseline, metric)
            best.append({'arrival_mode': mode, 'task_count': count, 'metric': metric,
                         'best_baseline': winner['policy'],
                         'baseline_mean': winner['metrics'][metric]['mean'],
                         'selection': 'best observed cross-seed baseline mean for this metric', **result})
    return {'runs': len(rows), 'completed_runs': sum(row['valid'] for row in rows),
            'groups': summaries, 'paired_vs_baselines': paired, 'versus_best_baseline': best,
            'statistical_scope': 'descriptive paired experiments; five seeds do not establish general superiority'}


def report_html(output, summary, calibration, plan):
    escape = html.escape
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
            '<title>STPS 与五种基线：独立校准、配对实验</title>',
            '<style>body{font:15px/1.55 system-ui;margin:32px;color:#17202a}'
            'table{border-collapse:collapse;margin:14px 0}th,td{border:1px solid #ccd;padding:7px;text-align:right}'
            'th:first-child,td:first-child{text-align:left}.good{color:#176037}.bad{color:#a32626}'
            'svg{max-width:100%;height:auto}a{color:#155d99}</style>',
            '<h1>4 卡 × 每卡 4×4：STPS 与五种基线</h1>',
            '<p>相同到达、任务、网络与固定空核行优先映射。独立合成校准指纹；测试工作负载只由执行器读取。'
            'SOP 是计算工作量记账，通信拥塞可延长逻辑步。未用测试结果调整参数。</p>',
            f'<p>完成 {summary["completed_runs"]}/{summary["runs"]} 次运行；'
            f'种子 {escape(str(plan["seeds"]))}；gamma={calibration["gamma"]:.4f}；'
            '稳态窗口预先固定为物理 Tick [5,40]。完整窗口包含全部执行时间。</p>',
            '<p><a href="plan.json">预先冻结计划</a> · <a href="input_hashes.json">输入哈希</a> · '
            '<a href="raw.csv">每次运行</a> · <a href="summary.csv">均值与标准差</a> · '
            '<a href="paired.csv">各基线配对差值</a> · <a href="calibration/summary.json">独立预测验证</a></p>']
    labels = {
        'full_compute_sops_cv': '全程计算 CV', 'full_endpoint_events_cv': '全程通信 CV',
        'steady_compute_sops_cv': '窗口计算 CV', 'steady_endpoint_events_cv': '窗口通信 CV',
        'mean_task_end_to_end_ticks': '端到端 Tick',
        'mean_communication_extension_ticks': '通信延长 Tick',
        'mean_rx_excess_latency_cycles': '额外包延迟 cycle',
        'router_wait_per_generated_flit': 'Router 等待/生成 flit',
        'makespan_ticks': '总完成 Tick', 'task_throughput_per_tick': '任务/Tick',
        'scheduling_seconds': '在线决策秒数',
    }
    body += ['<h2>相对每项指标最好的基线</h2>',
             '<p>改进为正代表 STPS 更优；CV、延迟与等待越小越好，吞吐越大越好。'
             '最佳基线按本组五种基线的跨种子均值选出，每项可能不同。胜/平/负是逐种子的配对比较。'
             '这些是描述性结果，不能证明 STPS 对一般任务始终更好。</p>',
             '<table><tr><th>场景</th><th>指标</th><th>最佳基线</th><th>基线均值</th>'
             '<th>STPS−基线</th><th>平均相对改进</th><th>胜/平/负</th></tr>']
    for entry in summary['versus_best_baseline']:
        improvement = entry['mean_improvement']
        relative = entry['mean_relative_improvement_percent']
        css = 'good' if improvement is not None and improvement > 0 else 'bad' if improvement else ''
        body.append('<tr><td>' + escape(f'{entry["arrival_mode"]}/{entry["task_count"]}') + '</td><td>'
                    + labels[entry['metric']] + '</td><td>' + escape(entry['best_baseline']) + '</td>'
                    + f'<td>{entry["baseline_mean"]:.4f}</td><td class="{css}">'
                    + (f'{entry["mean_stps_minus_baseline"]:+.4f}' if improvement is not None else '无有效配对')
                    + f'</td><td class="{css}">' + (f'{relative:+.2f}%' if relative is not None else '分母为零')
                    + f'</td><td>{entry["wins"]}/{entry["ties"]}/{entry["losses"]}</td></tr>')
    body.append('</table>')
    for mode, count in sorted({(entry['arrival_mode'], entry['task_count']) for entry in summary['groups']}):
        entries = [entry for entry in summary['groups'] if entry['arrival_mode'] == mode
                   and entry['task_count'] == count]
        entries.sort(key=lambda entry: POLICIES.index(entry['policy']))
        body.append(f'<h2>{escape(mode)} / {count} 个任务</h2>')
        body.append('<p>均值 ± 样本标准差；完整数值、分子分母见 CSV 与每次运行 manifest。</p>')
        body.append('<table><tr><th>策略</th><th>完成</th>' + ''.join(
            '<th>' + labels[key] + '</th>' for key in KEY_METRICS[:6]) + '</tr>')
        for entry in entries:
            body.append('<tr><td>' + escape(entry['policy']) + '</td>'
                        + f'<td>{entry["completed_runs"]}/{entry["runs"]}</td>')
            for key in KEY_METRICS[:6]:
                metric = entry['metrics'].get(key)
                body.append(f'<td>{metric["mean"]:.3f} ± {metric["std"]:.3f}</td>' if metric else '<td>—</td>')
            body.append('</tr>')
        body.append('</table>')
        for key in ('full_endpoint_events_cv', 'mean_task_end_to_end_ticks'):
            available = [entry for entry in entries if key in entry['metrics']]
            maximum = max((entry['metrics'][key]['mean'] for entry in available), default=1) or 1
            svg = [f'<svg role="img" aria-label="{labels[key]}" width="640" height="230" viewBox="0 0 640 230">',
                   f'<text x="8" y="20">{labels[key]}（越小越好）</text>']
            for index, entry in enumerate(available):
                value = entry['metrics'][key]['mean']
                width, y = 380 * value / maximum, 36 + index * 30
                fill = '#1c8064' if entry['policy'] == 'STPS' else '#6995bd'
                svg.append(f'<text x="5" y="{y+16}">{escape(entry["policy"])}</text>'
                           f'<rect x="95" y="{y}" width="{width:.2f}" height="21" fill="{fill}"/>'
                           f'<text x="{103+width:.2f}" y="{y+16}">{value:.4f}</text>')
            body.append(''.join(svg) + '</svg>')
    validation = calibration['mean_profile_validation']['active']
    body.append('<h2>指纹与代理误差</h2><p>独立验证集活跃轮次：'
                + f'cycle MAE={validation["cycle_mae"]:.3f}，物理 Tick 轮长 MAE={validation["round_mae"]:.3f}，'
                + f'轮长完全命中 {validation["round_exact_count"]}/{validation["samples"]}。'
                + '新轮次从空 sink 开始；同一 gamma 的 K-cycle 残余状态验证另见 calibration/summary.json。'
                + '均值指纹会平滑样本幅度；真实任务时延与均衡必须以完成仿真结果判断。</p>')
    body.append('<h2>可查看的运行</h2><ul>')
    for path in sorted((output / 'runs').glob('*/*/*/*/report.html')):
        relative = str(path.relative_to(output))
        body.append(f'<li><a href="{escape(relative, quote=True)}">{escape(relative)}</a></li>')
    body.append('</ul><p>本实验使用四类小型合成拓扑，脉冲零值位置固定、幅度随机变化；'
                '没有真实数据集、神经元反馈计算、计算服务时延或硬件验证。</p></html>')
    (output / 'index.html').write_text(''.join(body), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'data/stps_comparison')
    parser.add_argument('--seeds', type=int, nargs='+', default=[11, 23, 37, 53, 71])
    parser.add_argument('--task-counts', type=int, nargs='+', default=[24, 48])
    parser.add_argument('--no-reports', action='store_true', help='disable selected per-run HTML reports')
    parser.add_argument('--prepare-only', action='store_true', help='freeze plan, calibrate and export test inputs only')
    args = parser.parse_args(argv)
    if any(seed < 0 for seed in args.seeds) or len(set(args.seeds)) != len(args.seeds):
        parser.error('seeds must be distinct nonnegative integers')
    if any(count <= 0 for count in args.task_counts) or len(set(args.task_counts)) != len(args.task_counts):
        parser.error('task-counts must be distinct positive integers')
    # The timestamp only names the artifact; RNG seeds are explicit above.
    output = args.output_root.resolve() / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True, exist_ok=False)
    plan = frozen_plan(args.seeds, args.task_counts)
    write_json(output / 'plan.json', plan)
    print(f'Frozen plan: {output / "plan.json"}', flush=True)
    templates, profiles, calibration, validation = prepare_profiles(output, plan)
    calibration_summary = calibrate(output, profiles, calibration, validation)
    print(f'Independent gamma calibration: {calibration_summary["gamma"]:.6f}', flush=True)
    inputs = []
    for count in args.task_counts:
        for seed in args.seeds:
            for mode in ('poisson', 'bursty'):
                filename = prepare_scene(output, templates, profiles, count, seed, mode,
                                         calibration_summary['gamma'], plan)
                inputs.append((mode, count, seed, filename))
    hashes = {str(path.relative_to(output)): sha256(path)
              for directory in ('samples', 'profiles', 'inputs', 'calibration')
              for path in sorted((output / directory).rglob('*')) if path.is_file()}
    write_json(output / 'input_hashes.json', hashes)
    if args.prepare_only:
        print(output, flush=True)
        return 0
    rows = []
    for mode, count, seed, filename in inputs:
        scenario = load_cluster_scenario(filename)
        expected_remote = sum(sum(int(value) for value in request.workload.flits[
            :, request.workload.edge_src != request.workload.edge_dst].flat) for request in scenario.tasks)
        expected_sops = math.fsum(request.workload.totals['compute_sops'] for request in scenario.tasks)
        for policy in POLICIES:
            folder = output / 'runs' / mode / f'tasks_{count}' / f'seed_{seed}' / policy
            make_report = not args.no_reports and seed == args.seeds[0] and count == min(args.task_counts)
            result = run_cluster_simulation(scenario, folder, policy=policy, seed=seed,
                                            trace=False, report=make_report)
            summary = result.summary
            if not summary['conservation']['valid']:
                raise AssertionError(f'{mode}/{count}/{seed}/{policy}: flit conservation failed')
            if result.status == 'completed':
                if not all(summary['totals'][field] == expected_remote
                           for field in ('generated_tx', 'tx_injected', 'rx_ejected')):
                    raise AssertionError('policy changed or lost replay traffic')
                if not math.isclose(summary['totals']['compute_sops'], expected_sops, rel_tol=1e-12):
                    raise AssertionError('policy repeated or lost SOP work')
            row = run_row(summary, mode=mode, task_count=count, seed=seed,
                          policy=policy, folder=folder, output=output)
            rows.append(row)
            write_csv(output / 'raw.csv', rows)
            print(f'{len(rows)}/{plan["total_runs"]} {mode}/{count}/seed{seed}/{policy}: '
                  f'{result.status}, ticks={result.ticks_executed}, '
                  f'E2E={row["mean_task_end_to_end_ticks"]:.3f}, '
                  f'NoC CV={row["full_endpoint_events_cv"]:.4f}', flush=True)
    summary = summarize(rows)
    write_json(output / 'summary.json', summary)
    flat = []
    for group in summary['groups']:
        for metric, stats in group['metrics'].items():
            flat.append({key: group[key] for key in ('arrival_mode', 'task_count', 'policy', 'runs', 'completed_runs')}
                        | {'metric': metric, **stats})
    write_csv(output / 'summary.csv', flat)
    write_csv(output / 'paired.csv', summary['paired_vs_baselines'])
    write_csv(output / 'best_baseline.csv', summary['versus_best_baseline'])
    report_html(output, summary, calibration_summary, plan)
    print(output / 'index.html', flush=True)
    return 0 if summary['completed_runs'] == summary['runs'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
