import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest

from fingerprint.workload import Workload, save_workload
from schedule.baselines import POLICIES
from simulation.cluster_engine import run_cluster_simulation
from simulation.engine import run_simulation
from util.metrics import MEASURES

ROOT = Path(__file__).resolve().parents[1]


def fixture(tmp_path, *, cards=2, mesh_x=2, cycles=1, traffic=((1,),), max_ticks=30):
    workload = Workload([4, 4], [0], [1], traffic, [[1, 2]] * len(traffic), 1, 'test:cluster')
    save_workload(tmp_path / 'w.json', workload)
    task = {'task_id': 'a', 'arrival_tick': 1, 'workload': 'w.json',
            'mean_compute_sops': 3, 'mean_noc_endpoint': 2}
    doc = {'schema_version': 1, 'name': 'cluster fixture', 'cluster': {
        'cards': cards, 'mesh_x': mesh_x, 'mesh_y': 1, 'neurons_per_core': 4,
        'memory_mb': 2, 'compute_budget_sops': 10, 'noc_budget_endpoint': 10},
        'noc': {'cycles_per_tick': cycles}, 'max_ticks': max_ticks,
        'steady_window': [1, 10], 'tasks': [task]}
    path = tmp_path / 'scene.json'
    path.write_text(json.dumps(doc))
    return path, doc


def read(path):
    with path.open() as handle:
        return list(csv.DictReader(handle))


def test_cards_progress_independently_and_release_at_actual_completion(tmp_path):
    path, doc = fixture(tmp_path)
    save_workload(tmp_path / 'quiet.json', Workload([4], [], [], [[]], [[9]], 0, 'test:quiet'))
    doc['tasks'].append({'task_id': 'b', 'arrival_tick': 1, 'workload': 'quiet.json',
                         'mean_compute_sops': 9, 'mean_noc_endpoint': 0})
    doc['tasks'].append(dict(doc['tasks'][0], task_id='c', arrival_tick=2))
    path.write_text(json.dumps(doc))
    result = run_cluster_simulation(path, tmp_path / 'out', policy='RR', trace=True, report=False)
    tasks = {t['task_id']: t for t in result.summary['tasks']}
    assert tasks['a']['card_id'] == 0 and tasks['a']['completion_tick'] == 3
    assert tasks['b']['card_id'] == 1 and tasks['b']['completion_tick'] == 1
    # RR skips occupied card 0 at tick2 and uses released card1; no early release.
    assert tasks['c']['card_id'] == 1 and tasks['c']['placement_tick'] == 2
    assert tasks['c']['completion_tick'] == 4
    assert result.ticks_executed == 4


def test_pending_resource_wait_and_arrival_anchored_end_to_end(tmp_path):
    path, doc = fixture(tmp_path, cards=1)
    doc['tasks'].append(dict(doc['tasks'][0], task_id='b', arrival_tick=2))
    path.write_text(json.dumps(doc))
    result = run_cluster_simulation(path, tmp_path / 'out', report=False)
    a, b = result.summary['tasks']
    assert a['completion_tick'] == 3
    assert b['arrival_tick'] == 2 and b['placement_tick'] == 4
    assert b['resource_wait_ticks'] == 2 and b['boundary_wait_ticks'] == 0
    assert b['completion_tick'] == 6 and b['task_end_to_end_ticks'] == 5
    summary = {r['task_id']: r for r in read(tmp_path / 'out/task_summary.csv')}
    assert summary['b']['task_start_wait_ticks'] == '2'
    decisions = read(tmp_path / 'out/decisions.csv')
    assert [r['action'] for r in decisions if r['task_id'] == 'b'] == ['wait', 'wait', 'place']
    resource_rows = read(tmp_path / 'out/card_resources.csv')
    assert resource_rows[1]['used_cores'] == '2' and resource_rows[2]['used_cores'] == '0'


def test_placement_reserves_cores_while_card_barrier_blocks_start(tmp_path):
    path, doc = fixture(tmp_path, cards=1, mesh_x=4)
    doc['tasks'].append(dict(doc['tasks'][0], task_id='b', arrival_tick=2))
    path.write_text(json.dumps(doc))
    result = run_cluster_simulation(path, tmp_path / 'out', trace=True, report=False)
    a, b = result.summary['tasks']
    assert a['mapping'] == [0, 1] and b['mapping'] == [2, 3]
    assert b['placement_tick'] == 2 and b['actual_start_tick'] == 4
    assert b['boundary_wait_ticks'] == 2 and b['resource_wait_ticks'] == 0
    resources = read(tmp_path / 'out/card_resources.csv')
    assert resources[1]['used_cores'] == '4' and resources[1]['placed_waiting_tasks'] == '1'
    events = read(tmp_path / 'out/events.csv')
    assert {r['physical_tick'] for r in events if r['kind'] == 'generate' and r['task_id'] == 'b'} == {'4'}


@pytest.mark.parametrize('policy', POLICIES)
def test_common_mapper_and_counter_conservation_for_all_baselines(tmp_path, policy):
    path, doc = fixture(tmp_path, cards=4, mesh_x=4, cycles=4, traffic=((2,), (0,)))
    doc['tasks'] = [dict(doc['tasks'][0], task_id=f't{i}', arrival_tick=1) for i in range(6)]
    path.write_text(json.dumps(doc))
    result = run_cluster_simulation(path, tmp_path / 'out', policy=policy, report=False)
    assert result.status == 'completed' and result.summary['mapper'] == 'row-major-free-v1'
    for task in result.summary['tasks']:
        assert task['mapping'] in ([0, 1], [2, 3])
        assert task['task_end_to_end_ticks'] == task['resource_wait_ticks'] + task['boundary_wait_ticks'] + task['task_execution_ticks']
    assert result.summary['totals']['generated_tx'] == result.summary['totals']['tx_injected'] == result.summary['totals']['rx_ejected'] == 12
    core, card, cluster = (read(tmp_path / 'out' / (name + '.csv')) for name in ('core_tick', 'card_tick', 'cluster_tick'))
    for row in cluster:
        for field in MEASURES:
            assert float(row[field]) == sum(float(r[field]) for r in card if r['physical_tick'] == row['physical_tick'])
            assert float(row[field]) == sum(float(r[field]) for r in core if r['physical_tick'] == row['physical_tick'])


def test_unschedulable_future_and_truncated_requests_keep_identity(tmp_path):
    path, doc = fixture(tmp_path, max_ticks=2)
    doc['tasks'].append(dict(doc['tasks'][0], task_id='future', arrival_tick=10))
    save_workload(tmp_path / 'huge.json', Workload([5], [], [], [[]], [[0]], 0, 'test:too large'))
    doc['tasks'].append({'task_id': 'bad', 'arrival_tick': 1, 'workload': 'huge.json',
                        'mean_compute_sops': 0, 'mean_noc_endpoint': 0})
    path.write_text(json.dumps(doc))
    result = run_cluster_simulation(path, tmp_path / 'out', report=False)
    tasks = {t['task_id']: t for t in result.summary['tasks']}
    assert result.status == 'max_ticks' and not result.summary['valid']
    assert tasks['a']['status'] == 'unfinished' and tasks['a']['slowdown'] is None
    assert tasks['bad']['status'] == 'unschedulable' and tasks['bad']['card_id'] is None
    assert tasks['future']['status'] == 'not_arrived'
    assert len(read(tmp_path / 'out/task_summary.csv')) == 3
    assert result.summary['totals']['in_network'] == 1


def test_reproducible_p2c_and_output_safety(tmp_path):
    path, doc = fixture(tmp_path, cards=4, mesh_x=4, cycles=4)
    doc['tasks'] = [dict(doc['tasks'][0], task_id=f't{i}') for i in range(5)]
    path.write_text(json.dumps(doc))
    a = run_cluster_simulation(path, tmp_path / 'a', policy='P2C-Mean', report=False)
    b = run_cluster_simulation(path, tmp_path / 'b', policy='P2C-Mean', report=False)
    assert a.summary['tasks'] == b.summary['tasks']
    for name in ('decisions', 'core_tick', 'card_tick', 'step_timing'):
        assert (tmp_path / 'a' / (name + '.csv')).read_bytes() == (tmp_path / 'b' / (name + '.csv')).read_bytes()
    with pytest.raises(ValueError, match='empty'):
        run_cluster_simulation(path, tmp_path / 'a', report=False)


def test_invalid_cluster_input_before_outputs(tmp_path):
    path, doc = fixture(tmp_path)
    doc['tasks'][0]['mean_noc_endpoint'] = -1
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='mean_noc_endpoint'):
        run_cluster_simulation(path, tmp_path / 'out', report=False)
    assert not (tmp_path / 'out').exists()


def test_core_reuse_keeps_delivered_sink_identity_and_applies_backpressure(tmp_path):
    path, doc = fixture(tmp_path, cards=1, cycles=3)
    doc['noc'].update(sink_buffer_depth=1, sink_service_period=10)
    doc['tasks'].append(dict(doc['tasks'][0], task_id='b', arrival_tick=2))
    path.write_text(json.dumps(doc))
    result = run_cluster_simulation(path, tmp_path / 'out', trace=True, report=False)
    a, b = result.summary['tasks']
    assert a['completion_tick'] == 1 and b['mapping'] == a['mapping']
    assert b['actual_start_tick'] == 2 and b['completion_tick'] == 4
    events = read(tmp_path / 'out/events.csv')
    assert [(r['task_id'], r['cycle']) for r in events if r['kind'] == 'consume'] == [('a','10')]
    assert result.summary['totals']['rx_blocked_cycles'] > 0
    assert result.summary['conservation']['valid']


def test_cluster_cli_and_saved_scene_replay(tmp_path):
    path, _ = fixture(tmp_path, cycles=3)
    completed = subprocess.run([sys.executable, str(ROOT / 'main.py'), '--cluster-scenario',
        str(path), '--policy', 'BestFit', '--output-dir', str(tmp_path / 'out'), '--no-report'],
        capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((tmp_path / 'out/manifest.json').read_text())
    assert manifest['policy'] == 'BestFit' and manifest['cards'] == 2
    replay = run_cluster_simulation(tmp_path / 'out/scenario.json', tmp_path / 'replay',
                                    policy='BestFit', report=False)
    assert replay.summary['tasks'] == manifest['tasks']


def test_one_online_card_matches_fixed_scene_network_and_step_semantics(tmp_path):
    path, doc = fixture(tmp_path, cards=1, mesh_x=4, cycles=2, traffic=((2,), (0,)))
    doc['tasks'].append(dict(doc['tasks'][0], task_id='b'))
    path.write_text(json.dumps(doc))
    single = {'schema_version': 1, 'card': {key: doc['cluster'][key] for key in (
        'mesh_x', 'mesh_y', 'neurons_per_core', 'memory_mb')}, 'noc': doc['noc'],
        'tasks': [{'task_id': item['task_id'], 'workload': item['workload'], 'start_tick': 1,
                   'mapping': [0, 1] if index == 0 else [2, 3]}
                  for index, item in enumerate(doc['tasks'])]}
    single_path = tmp_path / 'single.json'
    single_path.write_text(json.dumps(single))
    fixed = run_simulation(single_path, tmp_path / 'single', trace=True, report=False)
    online = run_cluster_simulation(path, tmp_path / 'online', trace=True, report=False)
    assert fixed.ticks_executed == online.ticks_executed
    assert fixed.summary['totals'] == online.summary['totals']
    for table in ('events', 'core_tick', 'step_timing'):
        assert (tmp_path / 'single' / (table + '.csv')).read_bytes() == (
            tmp_path / 'online/cards/card_0' / (table + '.csv')).read_bytes()
