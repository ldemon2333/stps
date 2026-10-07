import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fingerprint.workload import Workload, save_workload
from fingerprint.scheduling import build_fingerprint, save_fingerprint
from simulation.card_runtime import CardRuntime
from simulation.cluster_engine import run_cluster_simulation
from simulation.cluster_scenario import load_cluster_scenario


def scene(tmp_path, *, cards=2, cycles=2):
    w = Workload([4, 4], [0], [1], [[1], [0]], [[1, 2], [3, 4]], 1, 'test:held-out')
    save_workload(tmp_path / 'test.json', w)
    calibration = Workload([4, 4], [0], [1], [[2], [0]], [[2, 2], [2, 4]], 1, 'test:calibration')
    save_fingerprint(tmp_path / 'profile.json', build_fingerprint([calibration], 'test:offline'))
    doc = {'schema_version': 1, 'name': 'STPS integration', 'cluster': {
        'cards': cards, 'mesh_x': 4, 'mesh_y': 1, 'neurons_per_core': 4,
        'memory_mb': 4, 'compute_budget_sops': 10, 'noc_budget_endpoint': 10},
        'noc': {'cycles_per_tick': cycles}, 'max_ticks': 30, 'steady_window': [1, 10],
        'stps': {'d_max': 3, 'gamma': 1.0, 'max_rounds': 1000},
        'tasks': [{'task_id': 'a', 'arrival_tick': 1, 'workload': 'test.json', 'profile': 'profile.json'}]}
    path = tmp_path / 'scene.json'; path.write_text(json.dumps(doc))
    return path, doc


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def ledger_scene(tmp_path, *, profile=True):
    workload = Workload(
        [4, 4], [0, 0], [0, 1], [[5, 2], [7, 0]],
        [[2, 3], [5, 7]], 1, 'test:ledger-actual')
    save_workload(tmp_path / 'ledger.json', workload)
    calibration = Workload(
        [4, 4], [0, 0], [0, 1], [[100, 3], [200, 4]],
        [[10, 10], [20, 20]], 1, 'test:ledger-profile')
    save_fingerprint(tmp_path / 'ledger-profile.json',
                     build_fingerprint([calibration], 'test:ledger-offline'))
    task = {'task_id': 'ledger', 'arrival_tick': 1, 'workload': 'ledger.json'}
    if profile:
        task['profile'] = 'ledger-profile.json'
    else:
        task.update(mean_compute_sops=8.5, mean_noc_endpoint=2)
    doc = {'schema_version': 1, 'name': 'adaptive committed ledger', 'cluster': {
        'cards': 1, 'mesh_x': 2, 'mesh_y': 1, 'neurons_per_core': 4,
        'memory_mb': 4, 'compute_budget_sops': 100, 'noc_budget_endpoint': 100},
        'noc': {'cycles_per_tick': 1}, 'max_ticks': 30,
        'stps': {'adaptive_ledger': profile}, 'tasks': [task]}
    path = tmp_path / ('ledger-profile-scene.json' if profile else 'ledger-actual-scene.json')
    path.write_text(json.dumps(doc))
    return load_cluster_scenario(path)


def test_stps_profiles_are_separate_and_means_shared_with_baselines(tmp_path):
    path, _ = scene(tmp_path)
    s = load_cluster_scenario(path)
    assert s.tasks[0].mean_noc_endpoint == 2
    assert s.tasks[0].workload.totals['quantized_flits'] == 1
    assert s.tasks[0].profile.total_noc_endpoint == 4
    result = run_cluster_simulation(path, tmp_path / 'out', policy='STPS', report=False)
    assert result.status == 'completed'
    assert result.summary['totals']['generated_tx'] == 1
    assert result.summary['scheduling']['evaluated_candidates'] >= 2
    assert result.summary['tasks'][0]['profile_sha256']
    assert result.summary['tasks'][0]['phase_offset_ticks'] == 0
    # Online generated work comes from the test trace, not the predicted profile.
    assert result.summary['totals']['compute_sops'] == 10


def test_adaptive_committed_ledger_replaces_issued_steps_once(tmp_path):
    scenario = ledger_scene(tmp_path)
    card = CardRuntime(0, scenario, tmp_path / 'ledger-card', trace=False)
    try:
        card.place(scenario.tasks[0], 1)
        # Profile totals exclude self traffic: compute=20+40, remote NoC=2*(3+4).
        assert card.assigned_compute_sops == 60
        assert card.assigned_noc_endpoint == 14
        assert card.ledger_adjustment_steps == 0

        card.advance_tick(1)
        # Issued step 0 is actual, while the future step remains predicted.
        assert card.assigned_compute_sops == 5 + 40
        assert card.assigned_noc_endpoint == 2 * 2 + 2 * 4
        assert card.ledger_adjustment_steps == 1
        first = (card.assigned_compute_sops, card.assigned_noc_endpoint,
                 card.ledger_compute_correction_sops, card.ledger_noc_correction_endpoint)

        # The remote transfer extends the round; waiting must not adjust it again.
        while card.progress['ledger'].steps_started == 1:
            card.advance_tick(card.metrics.current_tick + 1)
            if card.progress['ledger'].steps_started == 1:
                assert (card.assigned_compute_sops, card.assigned_noc_endpoint,
                        card.ledger_compute_correction_sops,
                        card.ledger_noc_correction_endpoint) == first
                assert card.ledger_adjustment_steps == 1

        # Once all steps are issued and completed, the ledger is the actual total.
        assert card.progress['ledger'].completion_tick is not None
        assert card.assigned_compute_sops == 17
        assert card.assigned_noc_endpoint == 4
        assert card.ledger_adjustment_steps == 2
        assert card.ledger_compute_correction_sops == -43
        assert card.ledger_noc_correction_endpoint == -10
        card._release('ledger')
        assert card.assigned_compute_sops == 17
        assert card.assigned_noc_endpoint == 4
    finally:
        card.close()


def test_committed_ledger_without_profile_uses_actual_total_without_adjustment(tmp_path):
    scenario = ledger_scene(tmp_path, profile=False)
    card = CardRuntime(0, scenario, tmp_path / 'actual-card', trace=False)
    try:
        card.place(scenario.tasks[0], 1)
        assert card.assigned_compute_sops == 17
        assert card.assigned_noc_endpoint == 4
        for tick in range(1, 20):
            card.advance_tick(tick)
            assert card.assigned_compute_sops == 17
            assert card.assigned_noc_endpoint == 4
            assert card.ledger_adjustment_steps == 0
            if card.progress['ledger'].completion_tick is not None:
                break
        assert card.progress['ledger'].completion_tick is not None
    finally:
        card.close()


@pytest.mark.parametrize('change,match', [
    ('missing', 'requires'), ('means', 'means'), ('topology', 'topology'), ('dmax', 'd_max')])
def test_invalid_stps_input_fails_before_output(tmp_path, change, match):
    path, doc = scene(tmp_path)
    if change == 'missing':
        doc['tasks'][0].pop('profile')
        doc['tasks'][0].update(mean_compute_sops=5, mean_noc_endpoint=1)
    elif change == 'means':
        doc['tasks'][0]['mean_compute_sops'] = 99
    elif change == 'topology':
        bad = Workload([4], [], [], [[], []], [[1], [0]], 1, 'test:bad')
        save_fingerprint(tmp_path / 'profile.json', build_fingerprint([bad], 'test:offline'))
    else:
        doc['stps']['d_max'] = -1
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=match):
        run_cluster_simulation(path, tmp_path / 'out', policy='STPS', report=False)
    assert not (tmp_path / 'out').exists()


def test_card_phase_gate_does_not_pause_existing_work_and_keeps_reservation(tmp_path):
    path, doc = scene(tmp_path, cards=1, cycles=4)
    doc['tasks'].append(dict(doc['tasks'][0], task_id='b'))
    path.write_text(json.dumps(doc))
    s = load_cluster_scenario(path)
    card = CardRuntime(0, s, tmp_path / 'card', trace=True)
    try:
        card.place(s.tasks[0], 1)
        preview = card.preview_mapping(s.tasks[1])
        card.place(s.tasks[1], 1, phase_offset_ticks=4)
        assert preview == (2, 3) and len(card.reserved) == 4
        for tick in range(1, 7):
            card.advance_tick(tick)
            if tick == 2:
                assert card.progress['a'].completion_tick == 2
                assert card.progress['b'].actual_start_tick is None
                assert card.reserved == {2, 3}
        assert card.progress['b'].actual_start_tick == 5
        assert card.progress['b'].completion_tick == 6
        card.finish(6, 'completed', True)
    finally:
        card.close()
    generated = [r for r in rows(tmp_path / 'card/events.csv') if r['kind'] == 'generate']
    assert [(r['task_id'],r['physical_tick']) for r in generated] == [('a','1'),('b','5')]


def force_delay(states, task_id, profile, config):
    if not states:
        return None
    state = states[0]
    return SimpleNamespace(card_id=state.card_id, delay=3, mapping=state.mapping,
                           predicted_actual_start=state.current_tick + 3,
                           predicted_completion=state.current_tick + 4,
                           candidate_count=1, candidates=())


def test_phase_wait_counts_and_completion_are_arrival_anchored(tmp_path, monkeypatch):
    path, _ = scene(tmp_path, cycles=4)
    monkeypatch.setattr('simulation.cluster_engine.choose_stps', force_delay)
    result = run_cluster_simulation(path, tmp_path / 'out', policy='STPS', report=True)
    t = result.summary['tasks'][0]
    assert (t['arrival_tick'],t['placement_tick'],t['requested_start_tick'],t['actual_start_tick']) == (1,1,4,4)
    assert t['completion_tick'] == 5 and t['task_end_to_end_ticks'] == 5
    assert t['resource_wait_ticks'] == t['boundary_wait_ticks'] == 0
    assert t['phase_offset_ticks'] == t['phase_wait_ticks'] == 3
    assert t['task_execution_ticks'] + t['phase_wait_ticks'] == t['task_end_to_end_ticks']
    row = rows(tmp_path / 'out/task_summary.csv')[0]
    assert row['phase_wait_ticks'] == '3' and row['requested_start_tick'] == '4'
    assert '主动延迟' in (tmp_path / 'out/report.html').read_text()
    replay = run_cluster_simulation(tmp_path / 'out/scenario.json', tmp_path / 'replay',policy='STPS',report=False)
    assert replay.summary['tasks'] == result.summary['tasks']


def test_truncation_before_requested_start_counts_only_observed_phase(tmp_path, monkeypatch):
    path, doc = scene(tmp_path)
    doc['max_ticks'] = 2; path.write_text(json.dumps(doc))
    monkeypatch.setattr('simulation.cluster_engine.choose_stps', force_delay)
    result = run_cluster_simulation(path, tmp_path / 'out', policy='STPS', report=False)
    t = result.summary['tasks'][0]
    assert result.status == 'max_ticks' and t['status'] == 'placed'
    assert t['phase_offset_ticks'] == 3 and t['phase_wait_ticks'] == 2
    assert t['actual_start_tick'] is None and t['boundary_wait_ticks'] == 0
    assert t['task_end_to_end_ticks'] is None and result.summary['totals']['generated_tx'] == 0
