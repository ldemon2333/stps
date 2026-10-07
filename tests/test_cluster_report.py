"""Cluster report rendering: independent loads, portable links and censored tasks."""
from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET

import pytest

from simulation.cluster_metrics import balance_metrics
from simulation.cluster_report import write_cluster_report


FIELDS = ('card_id', 'physical_tick', 'compute_sops', 'generated_tx',
          'tx_injected', 'rx_ejected', 'router_wait_flit_cycles',
          'communication_extension_tick')


def _csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _window(loads, *, start=1, end=2, observed=2, complete=True):
    cards = [{'card_id': index, 'compute_sops': compute, 'tx_injected': flits,
              'rx_ejected': flits, 'endpoint_events': flits * 2,
              'offered_endpoint_events': flits * 2}
             for index, (compute, flits) in enumerate(loads)]
    balance = {field: balance_metrics([card[field] for card in cards])
               for field in ('compute_sops', 'offered_endpoint_events', 'endpoint_events')}
    ratios = ('cv', 'jfi', 'lif', 'p99_to_mean', 'max_to_mean')
    temporal = {field: {ratio: {'samples': observed,
                                'mean': metrics[ratio]['value'] if observed else 0,
                                'p95': metrics[ratio]['value'] if observed else 0,
                                'p99': metrics[ratio]['value'] if observed else 0,
                                'max': metrics[ratio]['value'] if observed else 0}
                        for ratio in ratios}
                for field, metrics in balance.items()}
    distribution = {}
    for field in balance:
        values = [card[field] for card in cards]
        samples = len(cards) * observed
        mean = sum(values) / samples if samples else 0
        maximum = max(values, default=0)
        distribution[field] = {
            'samples': samples, 'mean_load': mean, 'p99_load': maximum, 'max_load': maximum,
            'p99_to_mean': maximum / mean if mean else 0,
            'max_to_mean': maximum / mean if mean else 0,
        }
    return {'start_tick': start, 'end_tick': end, 'observed_ticks': observed,
            'complete': complete, 'padded_zero_ticks': max(0, end - start + 1 - observed) if complete else 0,
            'cards': cards, 'balance': balance, 'temporal_hotspots': temporal,
            'time_card_distribution': distribution}


def _legacy_window(loads):
    window = _window(loads)
    for card in window['cards']:
        card.pop('offered_endpoint_events')
    window['balance'].pop('offered_endpoint_events')
    for metrics in window['balance'].values():
        for field in ('p99_load', 'p99_to_mean', 'max_to_mean'):
            metrics.pop(field)
    window.pop('temporal_hotspots')
    window.pop('time_card_distribution')
    return window


def _artifacts(tmp_path, *, manifest=None, rows=(), resources=()):
    source = {'schema_version': 3, 'scenario': 'test', 'policy': 'RR', 'cards': 4,
              'config': {'mesh_x': 4, 'mesh_y': 4}, 'ticks_executed': 2,
              'physical_tick_period_cycles': 4, 'status': 'completed', 'valid': True,
              'tasks': [], 'totals': {}, 'derived': {}, 'timing': {},
              'balance_windows': {'full': _window([(0, 0)] * 4)}}
    if manifest:
        source.update(manifest)
    (tmp_path / 'manifest.json').write_text(json.dumps(source), encoding='utf-8')
    _csv(tmp_path / 'card_tick.csv', FIELDS, rows)
    _csv(tmp_path / 'card_resources.csv',
         ('card_id', 'physical_tick', 'used_cores', 'used_memory_mb'), resources)


def _assert_svg_is_valid(document):
    charts = re.findall(r'<svg\b.*?</svg>', document, flags=re.S)
    assert charts
    for chart in charts:
        assert ET.fromstring(chart).tag.endswith('svg')


def test_renders_dual_loads_lifecycle_and_escapes_user_content(tmp_path):
    task_id = '<script>alert("bad")</script>'
    scenario = '<img src=x onerror="bad()">'
    _artifacts(tmp_path,
               manifest={'scenario': scenario, 'policy': 'P2C-Mean',
                         'tasks': [{'task_id': task_id, 'status': 'completed', 'card_id': 0,
                                    'arrival_tick': 1, 'placement_tick': 1, 'actual_start_tick': 1,
                                    'completion_tick': 2, 'resource_wait_ticks': 0,
                                    'boundary_wait_ticks': 0, 'task_execution_ticks': 2,
                                    'task_end_to_end_ticks': 2, 'communication_extension_ticks': 1,
                                    'slowdown': 2, 'mapping': [0, 1]}],
                         'totals': {'compute_sops': 9, 'generated_tx': 3, 'tx_injected': 3,
                                    'rx_ejected': 3, 'router_wait_flit_cycles': 7},
                         'balance_windows': {'full': _window([(9, 3), (0, 0), (0, 0), (0, 0)]),
                                             'steady': _window([(0, 0)] * 4, start=3, end=5, observed=0)}},
               rows=[{'card_id': 0, 'physical_tick': 1, 'compute_sops': 9, 'generated_tx': 3,
                      'tx_injected': 3, 'rx_ejected': 2, 'router_wait_flit_cycles': 7,
                      'communication_extension_tick': False},
                     {'card_id': 0, 'physical_tick': 2, 'rx_ejected': 1,
                      'communication_extension_tick': True}],
               resources=[{'card_id': 0, 'physical_tick': 1, 'used_cores': 2, 'used_memory_mb': 3}])

    path = write_cluster_report(tmp_path)
    document = path.read_text(encoding='utf-8')
    assert path == tmp_path / 'report.html'
    assert document.startswith('<!doctype html>')
    assert '<script>' not in document and '<img src=x' not in document
    assert '&lt;script&gt;alert(&quot;' in document
    assert '&lt;img src=x onerror=&quot;' in document
    assert 'cards/card_0/report.html' in document and 'cards/card_3/report.html' in document
    assert '计算 SOP' in document and '通信供给端点' in document and '通信服务端点' in document
    assert '全程窗口' in document and '预先固定稳态窗口' in document
    assert all(metric in document for metric in ('CV', 'JFI', 'LIF', 'P99 / Mean', 'Max / Mean'))
    assert '4 卡时 P99 就是最大值' in document
    assert '窗口累计分布' in document and '逐 Tick 热点 / 慢卡' in document
    assert 'Tick × 卡时空样本分布' in document and '热点 / 慢卡</span>' in document
    assert '分子' in document and '分母' in document and '共同归一化尺度' in document
    assert '卡 0；物理 Tick 2；rx_ejected 1' in document
    assert '卡 0；物理 Tick 2；communication_extension_tick 1' in document
    assert '预留核数' in document and '预留内存 MB' in document
    assert '任务到达 → 放置 → 实际启动 → 完成' in document
    assert 'row-major' in document and 'MicroPopulation' in document
    assert '尚未模拟计算服务时间' in document
    assert 'http://' not in document.replace('http://www.w3.org/2000/svg', '')
    assert 'https://' not in document
    _assert_svg_is_valid(document)


def test_all_zero_run_keeps_idle_cards_and_zero_denominators(tmp_path):
    _artifacts(tmp_path,
               manifest={'tasks': [{'task_id': 'silent', 'status': 'completed', 'card_id': 2,
                                    'arrival_tick': 1, 'placement_tick': 1, 'actual_start_tick': 1,
                                    'completion_tick': 2, 'task_execution_ticks': 2,
                                    'task_end_to_end_ticks': 2, 'mapping': [0]}]},
               rows=[{'card_id': card, 'physical_tick': tick,
                      **{field: 0 for field in FIELDS[2:]}}
                     for card in range(4) for tick in (1, 2)])
    document = write_cluster_report(tmp_path).read_text(encoding='utf-8')
    assert '全零窗口五个比率均为 0' in document
    assert '<td>计算 SOP</td><td>CV</td><td>0</td><td>0</td><td>0</td><td>0</td><td>是</td>' in document
    assert '<td>通信服务端点</td><td>JFI</td><td>0</td><td>0</td><td>0</td><td>0</td><td>是</td>' in document
    assert '<td>通信供给端点</td><td>Max / Mean</td><td>0</td><td>0</td><td>0</td><td>0</td><td>是</td>' in document
    assert '卡 3；物理 Tick 1；compute_sops 0' in document
    assert '卡 3；物理 Tick 2；tx_injected 0' in document
    assert '0 → 0' in document
    _assert_svg_is_valid(document)


def test_truncated_run_marks_unfinished_tasks_without_future_observation(tmp_path):
    _artifacts(tmp_path,
               manifest={'status': 'max_ticks', 'valid': False, 'ticks_executed': 1,
                         'tasks': [{'task_id': 'running', 'status': 'unfinished', 'card_id': 0,
                                    'arrival_tick': 1, 'placement_tick': 1, 'actual_start_tick': 1,
                                    'completion_tick': None, 'task_execution_ticks': None,
                                    'task_end_to_end_ticks': None, 'slowdown': None, 'mapping': [0, 1]},
                                   {'task_id': 'reserved', 'status': 'placed', 'card_id': 1,
                                    'arrival_tick': 1, 'placement_tick': 1,
                                    'actual_start_tick': None, 'completion_tick': None, 'mapping': [0]},
                                   {'task_id': 'future', 'status': 'not_arrived', 'card_id': None,
                                    'arrival_tick': 20, 'placement_tick': None,
                                    'actual_start_tick': None, 'completion_tick': None, 'mapping': []}],
                         'balance_windows': {'full': _window([(0, 0)] * 4, end=1, observed=1),
                                             'steady': _window([(0, 0)] * 4, start=2, end=6,
                                                               observed=0, complete=False)}},
               rows=[{'card_id': 0, 'physical_tick': 1, 'tx_injected': 1},
                     {'card_id': 0, 'physical_tick': 20, 'rx_ejected': 999}])
    document = write_cluster_report(tmp_path).read_text(encoding='utf-8')
    assert '达到最大物理 Tick，仍有未完成任务' in document
    assert '完整有效运行：否' in document
    assert '窗口完整：否，截断的部分观察' in document
    assert '运行未完成' in document and '已放置，等待卡轮次边界' in document
    assert '尚未到达' in document
    assert '<td>running</td><td>运行未完成</td><td>0</td><td>1</td><td>1</td><td>1</td><td>—</td>' in document
    assert '物理 Tick 20；rx_ejected 999' not in document
    assert '未完成任务不计为 0 时延' in document
    assert 'stroke-dasharray="4 3"' in document
    _assert_svg_is_valid(document)


def test_empty_execution_has_no_invalid_svg_coordinates(tmp_path):
    _artifacts(tmp_path, manifest={'ticks_executed': 0, 'balance_windows': {}, 'tasks': []})
    document = write_cluster_report(tmp_path).read_text(encoding='utf-8')
    assert '未执行物理 Tick' in document
    assert '无任务' in document
    assert '未提供窗口聚合' in document
    _assert_svg_is_valid(document)


def test_legacy_balance_window_marks_new_metrics_missing_without_fabricating_zero(tmp_path):
    _artifacts(tmp_path, manifest={'balance_windows': {'full': _legacy_window([(4, 2)] * 4)}})
    document = write_cluster_report(tmp_path).read_text(encoding='utf-8')
    assert '<td>通信供给端点</td><td>—</td><td>—</td>' in document
    assert '旧版结果未提供逐 Tick 热点统计' in document
    assert '旧版结果未提供 Tick × 卡联合分布' in document
    assert '不把缺失值伪造为 0' in document


def test_nonfinite_values_are_rejected(tmp_path):
    _artifacts(tmp_path, rows=[{'card_id': 0, 'physical_tick': 1, 'compute_sops': 'nan'}])
    with pytest.raises(ValueError, match='nonfinite'):
        write_cluster_report(tmp_path)
