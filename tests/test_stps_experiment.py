"""Protect split isolation and paired analysis in the comparison driver."""
import json

import numpy as np

from fingerprint.scheduling import build_fingerprint, save_fingerprint
from fingerprint.workload import Workload, load_workload
from script.compare_stps import (
    ROOT, TEMPLATES, frozen_plan, paired_comparison, perturbed_workload,
    prepare_scene, sha256, summarize, write_json,
)


def test_perturbation_keeps_topology_zeros_and_separates_trace_splits():
    template = Workload([2, 3], [0, 1], [1, 1], [[0, 2], [3, 0]],
                        [[1, 0], [0, 4]], 1, 'synthetic:unit')
    first = perturbed_workload(template, seed=1001, split='calibration', template_name='unit')
    repeated = perturbed_workload(template, seed=1001, split='calibration', template_name='unit')
    held_out = perturbed_workload(template, seed=2001, split='validation', template_name='unit')
    assert np.array_equal(first.flits, repeated.flits)
    assert np.array_equal(first.edge_expected_flits == 0, template.edge_expected_flits == 0)
    assert np.array_equal(first.compute_sops == 0, template.compute_sops == 0)
    assert np.array_equal(first.edge_src, template.edge_src)
    assert np.array_equal(first.edge_dst, template.edge_dst)
    assert not np.array_equal(first.edge_expected_flits, held_out.edge_expected_flits)
    # Traffic and SOP use separate spawned random streams.
    assert first.edge_expected_flits[1, 0] / 3 != first.compute_sops[1, 1] / 4


def test_scene_modes_share_test_workloads_and_calibration_means(tmp_path):
    plan = frozen_plan([11], [4])
    write_json(tmp_path / 'plan.json', plan)
    write_json(tmp_path / 'calibration/summary.json', {'gamma': 1.4})
    templates, profiles = {}, {}
    for index, name in enumerate(TEMPLATES):
        template = load_workload(ROOT / 'examples/cluster/workloads' / f'{name}.json')
        templates[name] = template
        calibration = [perturbed_workload(template, seed=1000 + 100 * index + sample,
                                         split='calibration', template_name=name) for sample in range(2)]
        profile = build_fingerprint(calibration, source='synthetic:calibration')
        save_fingerprint(tmp_path / 'profiles' / f'{name}.json', profile)
        profiles[name] = profile
    poisson = prepare_scene(tmp_path, templates, profiles, 4, 11, 'poisson', 1.4, plan)
    hashes = {path.name: sha256(path) for path in poisson.parent.joinpath('workloads').glob('*.json')}
    bursty = prepare_scene(tmp_path, templates, profiles, 4, 11, 'bursty', 1.4, plan)
    assert hashes == {path.name: sha256(path) for path in bursty.parent.joinpath('workloads').glob('*.json')}
    left, right = [json.loads(path.read_text()) for path in (poisson, bursty)]
    for lhs, rhs in zip(left['tasks'], right['tasks']):
        assert {key: value for key, value in lhs.items() if key != 'arrival_tick'} == {
            key: value for key, value in rhs.items() if key != 'arrival_tick'}
        name = lhs['profile'].split('/')[-1].removesuffix('.json')
        assert lhs['mean_compute_sops'] == profiles[name].mean_compute_sops
        workload = load_workload(poisson.parent / lhs['workload'])
        assert workload.metadata['split'] == 'test'
        assert workload.metadata['seed'][0] == 3011


def test_paired_directions_zero_baseline_and_invalid_runs():
    stps = {1: {'valid': True, 'full_endpoint_events_cv': 0.2, 'full_endpoint_events_jfi': 0.9},
            2: {'valid': True, 'full_endpoint_events_cv': 0.1, 'full_endpoint_events_jfi': 0.7},
            3: {'valid': False, 'full_endpoint_events_cv': 0}}
    baseline = {1: {'valid': True, 'full_endpoint_events_cv': 0.4, 'full_endpoint_events_jfi': 0.8},
                2: {'valid': True, 'full_endpoint_events_cv': 0, 'full_endpoint_events_jfi': 0.8},
                3: {'valid': True, 'full_endpoint_events_cv': 1}}
    cv = paired_comparison(stps, baseline, 'full_endpoint_events_cv')
    assert (cv['paired_samples'], cv['wins'], cv['losses']) == (2, 1, 1)
    assert cv['relative_samples'] == 1
    assert cv['mean_relative_improvement_percent'] == 50
    fairness = paired_comparison(stps, baseline, 'full_endpoint_events_jfi')
    assert (fairness['wins'], fairness['losses']) == (1, 1)


def test_summary_selects_baseline_per_metric_without_comparing_denominators():
    rows = []
    for policy, cv, latency in (('RR', .3, 10), ('WorstFit', .4, 8), ('STPS', .35, 9)):
        rows.append({'arrival_mode': 'bursty', 'task_count': 4, 'seed': 11, 'policy': policy,
                     'valid': True, 'full_endpoint_events_cv': cv,
                     'full_endpoint_events_cv_denominator': 3.0,
                     'mean_task_end_to_end_ticks': latency})
    summary = summarize(rows)
    best = {entry['metric']: entry for entry in summary['versus_best_baseline']}
    assert best['full_endpoint_events_cv']['best_baseline'] == 'RR'
    assert best['mean_task_end_to_end_ticks']['best_baseline'] == 'WorstFit'
    assert best['full_endpoint_events_cv']['losses'] == 1
    assert not any('denominator' in entry['metric'] for entry in summary['paired_vs_baselines'])
