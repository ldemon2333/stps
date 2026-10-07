#!/usr/bin/env python3
"""Run five online baselines on identical Poisson/bursty four-card request traces."""
import argparse
import csv
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from schedule.baselines import POLICIES
from simulation.cluster_engine import run_cluster_simulation
from simulation.cluster_scenario import load_cluster_scenario


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'data/cluster')
    parser.add_argument('--scheduler-seed', type=int, default=17)
    parser.add_argument('--no-trace', action='store_true')
    args = parser.parse_args(argv)
    output = args.output_root / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for mode in ('poisson', 'bursty'):
        scenario = load_cluster_scenario(ROOT / 'examples/cluster' / (mode + '.json'))
        expected_remote = sum(sum(int(value) for value in request.workload.flits[:, request.workload.edge_src != request.workload.edge_dst].flat)
                              for request in scenario.tasks)
        expected_sops = sum(request.workload.totals['compute_sops'] for request in scenario.tasks)
        for policy in POLICIES:
            folder = f'{mode}/{policy}'
            result = run_cluster_simulation(scenario, output / folder, policy=policy,
                                            seed=args.scheduler_seed, trace=not args.no_trace)
            summary = result.summary
            if result.status != 'completed':
                raise RuntimeError(f'{mode}/{policy} did not complete: {result.status}')
            if not (summary['totals']['generated_tx'] == summary['totals']['tx_injected'] == summary['totals']['rx_ejected'] == expected_remote):
                raise AssertionError(f'{mode}/{policy} changed or lost traffic')
            if summary['totals']['compute_sops'] != expected_sops:
                raise AssertionError(f'{mode}/{policy} repeated/lost SOP work')
            balance = summary['balance_windows']['full']['balance']
            row = {'arrival_mode': mode, 'policy': policy, 'status': result.status,
                   'ticks_executed': result.ticks_executed, **summary['timing'],
                   **summary['derived'], **summary['totals'],
                   'compute_cv': balance['compute_sops']['cv']['value'],
                   'noc_endpoint_cv': balance['endpoint_events']['cv']['value'],
                   'report': folder + '/report.html'}
            rows.append(row)
            print(f'{mode}/{policy}: completed {len(scenario.tasks)} tasks in {result.ticks_executed} ticks')
    with (output / 'comparison.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    # An independent portable overview; detailed per-card/step plots are linked.
    fields = [('arrival_mode', '到达'), ('policy', '策略'), ('ticks_executed', '总Tick'),
              ('mean_task_end_to_end_ticks', '平均端到端Tick'),
              ('mean_task_execution_ticks', '平均执行Tick'),
              ('mean_rx_latency_cycles', '平均包延迟cycle'), ('router_wait_flit_cycles', 'Router等待'),
              ('compute_cv', '计算CV'), ('noc_endpoint_cv', '通信CV')]
    body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>四卡基线比较</title>'
    body += '<style>body{font:15px/1.6 system-ui;margin:32px}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd}</style>'
    body += '<h1>4卡 × 每卡4×4：五种基线比较</h1><p>相同任务、到达、网络参数，统一row-major空核映射。SOP是工作量记账。结果为合成小图单种子演示。</p>'
    body += '<table><tr>' + ''.join('<th>' + title + '</th>' for _, title in fields) + '<th>报告</th></tr>'
    for row in rows:
        body += '<tr>' + ''.join('<td>' + html.escape(f'{row[key]:.3f}' if isinstance(row[key], float) else str(row[key])) + '</td>' for key, _ in fields)
        body += '<td><a href="' + html.escape(row['report'], quote=True) + '">打开</a></td></tr>'
    body += '</table><p><a href="comparison.csv">完整指标CSV</a></p></html>'
    (output / 'index.html').write_text(body, encoding='utf-8')
    print(output / 'index.html')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
