"""Render an offline, self-contained cluster report from finalized simulator logs."""
from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path


LABELS = {
    'compute_sops': '计算工作量 SOP', 'generated_tx': '跨核生成 flit',
    'tx_injected': '源端 Tx', 'rx_ejected': '目的端 Rx',
    'router_wait_flit_cycles': 'Router 队头等待 flit-cycle',
    'communication_extension_tick': '通信延长 Tick',
    'used_cores': '预留核数', 'used_memory_mb': '预留内存 MB',
}
COLORS = {'compute_sops': '#2563eb', 'generated_tx': '#ea580c',
          'tx_injected': '#059669', 'rx_ejected': '#7c3aed',
          'router_wait_flit_cycles': '#be123c',
          'communication_extension_tick': '#b45309',
          'used_cores': '#0891b2', 'used_memory_mb': '#4f46e5'}
STATUS = {'completed': '全部完成', 'completed_with_rejections': '结束，包含无法部署任务',
          'max_ticks': '达到最大物理 Tick，仍有未完成任务', 'unfinished': '运行未完成',
          'pending': '等待资源', 'placed': '已放置，等待卡轮次边界',
          'not_arrived': '尚未到达', 'unschedulable': '无法部署', 'not_started': '尚未启动'}


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _number(value) -> float:
    if value in (None, ''):
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return float(value.lower() == 'true')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'non-numeric report value: {value!r}') from exc
    if not math.isfinite(result):
        raise ValueError(f'nonfinite report value: {value!r}')
    return result


def _integer(value, name: str, minimum: int = 0) -> int:
    result = _number(value)
    if result < minimum or not result.is_integer():
        raise ValueError(f'invalid {name}: {value!r}')
    return int(result)


def _fmt(value) -> str:
    number = _number(value)
    if number.is_integer():
        return f'{number:,.0f}'
    return f'{number:,.3f}'.rstrip('0').rstrip('.')


def _optional(value) -> str:
    return '—' if value in (None, '') else _fmt(value)


def _metric(mapping, key) -> str:
    """Format an optional metric without turning an absent legacy field into zero."""
    return _optional(mapping.get(key)) if isinstance(mapping, dict) else '—'


def _table(headers, rows) -> str:
    head = ''.join(f'<th>{_escape(value)}</th>' for value in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{value}</td>' for value in row) + '</tr>' for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _csv_rows(path: Path, required=()):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(required).issubset(reader.fieldnames):
            raise ValueError(f'{path.name} lacks required columns: {required}')
        return list(reader)


def _series(rows, card_ids, ticks_executed):
    data = {card_id: {} for card_id in card_ids}
    for row in rows:
        card_id = _integer(row.get('card_id'), 'card_id')
        tick = _integer(row.get('physical_tick'), 'physical_tick', 1)
        if card_id not in data:
            raise ValueError(f'card log references unconfigured card {card_id}')
        if tick > ticks_executed:
            continue
        if tick in data[card_id]:
            raise ValueError(f'duplicate card {card_id}, physical Tick {tick}')
        data[card_id][tick] = row
    return data


def _at(series, tick, field):
    return _number(series.get(tick, {}).get(field))


def _chart(card_id, series, ticks) -> str:
    width, height, left, right = 1000, 650, 85, 25
    draw_width = width - left - right
    def x_at(index):
        return left + draw_width * (index / (len(ticks) - 1) if len(ticks) > 1 else 0.5)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'role="img" aria-label="卡 {card_id} 逐物理 Tick 负载和通信延长曲线">']
    panels = ((50, ('compute_sops',), '计算工作量 · SOP / 物理 Tick'),
              (200, ('generated_tx', 'tx_injected', 'rx_ejected'), '通信端点 · flit / 物理 Tick'),
              (350, ('router_wait_flit_cycles',), 'Router 队头等待 · flit-cycle / 物理 Tick'),
              (500, ('communication_extension_tick',), '当前 Tick 是否为通信延长 · 0 / 1'))
    for top, fields, label in panels:
        peak = max((_at(series, tick, field) for tick in ticks for field in fields), default=0.0)
        scale = max(1.0, peak) if fields == ('communication_extension_tick',) else peak or 1.0
        parts.append(f'<text x="{left}" y="{top - 15}" class="svg-title">{label}</text>')
        for fraction in (0.0, 0.5, 1.0):
            y = top + 95 * (1 - fraction)
            parts.append(f'<line x1="{left}" y1="{y}" x2="{width - right}" y2="{y}" stroke="#e2e8f0"/>'
                         f'<text x="{left - 8}" y="{y + 4}" text-anchor="end" class="svg-label">{_fmt(scale * fraction)}</text>')
        for field in fields:
            points = ' '.join(f'{x_at(index):.2f},{top + 95 * (1 - _at(series, tick, field) / scale):.2f}'
                              for index, tick in enumerate(ticks))
            total = sum(_at(series, tick, field) for tick in ticks)
            parts.append(f'<polyline points="{points}" fill="none" stroke="{COLORS[field]}" stroke-width="2.3">'
                         f'<title>{LABELS[field]}：{_fmt(total)}</title></polyline>')
        if len(fields) > 1:
            for index, field in enumerate(fields):
                x = left + index * 180
                parts.append(f'<line x1="{x}" y1="{top + 118}" x2="{x + 18}" y2="{top + 118}" '
                             f'stroke="{COLORS[field]}" stroke-width="3"/>'
                             f'<text x="{x + 25}" y="{top + 122}" class="svg-label">{LABELS[field]}</text>')
    if ticks:
        for index in sorted({round(i * (len(ticks) - 1) / 5) for i in range(6)}):
            parts.append(f'<text x="{x_at(index):.2f}" y="620" text-anchor="middle" '
                         f'class="svg-label">{ticks[index]}</text>')
    else:
        parts.append('<text x="85" y="620" class="svg-label">未执行物理 Tick</text>')
    parts.append('<text x="500" y="645" text-anchor="middle" class="svg-label">物理 Tick</text></svg>')
    return ''.join(parts)


def _heatmap(title, card_ids, data, ticks, field, *, mean=False):
    chunk = max(1, math.ceil(len(ticks) / 96))
    bins = [ticks[start:start + chunk] for start in range(0, len(ticks), chunk)]
    cell = min(24.0, 840 / max(1, len(bins)))
    left, top, row_height = 110, 65, 28
    width, height = left + cell * len(bins) + 25, top + len(card_ids) * row_height + 35
    matrix = [[sum(_at(data[card_id], tick, field) for tick in bucket) /
               (len(bucket) if mean else 1) for bucket in bins] for card_id in card_ids]
    peak = max((value for values in matrix for value in values), default=0.0)
    color = COLORS[field]
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'role="img" aria-label="{_escape(title)}">',
             f'<text x="12" y="23" class="svg-title">{_escape(title)}</text>',
             f'<text x="12" y="43" class="svg-label">0 → {_fmt(peak)}；'
             f'{"区间平均" if mean else "区间合计"}；所有配置卡和零活动 Tick 均保留</text>']
    for row_index, card_id in enumerate(card_ids):
        y = top + row_index * row_height
        parts.append(f'<text x="{left - 10}" y="{y + 16}" text-anchor="end" class="svg-label">卡 {card_id}</text>')
        for col, bucket in enumerate(bins):
            value = matrix[row_index][col]
            fraction = max(0.0, min(1.0, value / peak)) if peak else 0.0
            shade = '#f1f5f9' if fraction == 0 else '#' + ''.join(
                f'{round(pale + (dark - pale) * fraction):02x}' for pale, dark in zip((239, 246, 255), rgb))
            interval = str(bucket[0]) if len(bucket) == 1 else f'{bucket[0]}–{bucket[-1]}'
            parts.append(f'<rect x="{left + col * cell:.2f}" y="{y}" width="{cell - 1:.2f}" height="24" '
                         f'fill="{shade}"><title>卡 {card_id}；物理 Tick {interval}；{field} {_fmt(value)}</title></rect>')
    if bins:
        for col in sorted({0, len(bins) // 2, len(bins) - 1}):
            parts.append(f'<text x="{left + col * cell:.2f}" y="{height - 10}" class="svg-label">{bins[col][0]}</text>')
    else:
        parts.append(f'<text x="{left}" y="{top + 16}" class="svg-label">未执行物理 Tick</text>')
    parts.append('</svg>')
    return ''.join(parts)


def _timeline(tasks, ticks_executed):
    left, width, top, row_height = 220, 1120, 65, 33
    height, plot_width = top + max(1, len(tasks)) * row_height + 50, width - left - 25
    limit = max(1, ticks_executed)
    def x_at(tick):
        return left + (tick - 1) / limit * plot_width
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             'role="img" aria-label="任务到达、放置、实际启动与完成时间线">',
             '<text x="12" y="23" class="svg-title">任务到达 → 放置 → 实际启动 → 完成</text>',
             '<text x="12" y="43" class="svg-label">灰：等待资源；紫：主动延迟；橙：等待卡轮次边界；蓝：实际执行；虚线：未完成</text>']
    for ordinal, task in enumerate(tasks):
        y = top + ordinal * row_height
        task_id = str(task.get('task_id', ''))
        card = task.get('card_id')
        label = f'{task_id} · 卡 {card}' if card is not None else f'{task_id} · 未放置'
        short = label if len(label) <= 27 else label[:26] + '…'
        parts.append(f'<text x="{left - 10}" y="{y + 16}" text-anchor="end" class="svg-label">'
                     f'{_escape(short)}<title>{_escape(label)}</title></text>')
        arrival = task.get('arrival_tick')
        placement, start, completion = (task.get(field) for field in
                                         ('placement_tick', 'actual_start_tick', 'completion_tick'))
        requested = task.get('requested_start_tick', placement)
        def span(begin, end, color, label, unfinished=False):
            if begin is None or _number(begin) > ticks_executed:
                return
            begin, end = max(1.0, _number(begin)), min(limit + 1.0, _number(end))
            if end <= begin:
                return
            dash = ' stroke="#334155" stroke-dasharray="4 3"' if unfinished else ''
            parts.append(f'<rect x="{x_at(begin):.2f}" y="{y + 5}" width="{x_at(end) - x_at(begin):.2f}" '
                         f'height="18" fill="{color}"{dash}><title>{_escape(task_id)}；{label}；'
                         f'Tick {_fmt(begin)}–{_fmt(end - 1)}</title></rect>')
        if task.get('status') != 'unschedulable':
            span(arrival, placement if placement is not None else limit + 1, '#94a3b8', '等待资源', placement is None)
            span(placement, requested if requested is not None else limit + 1, '#7c3aed', '主动延迟',
                 requested is None or _number(requested) > limit + 1)
            span(requested, start if start is not None else limit + 1, '#f59e0b', '等待卡轮次边界', start is None)
            span(start, _number(completion) + 1 if completion is not None else limit + 1,
                 '#2563eb', '实际执行' if completion is not None else '实际执行，尚未完成', completion is None)
        for value, label, color in ((arrival, '到达', '#475569'), (placement, '放置', '#f59e0b'),
                                    (start, '实际启动', '#2563eb'), (completion, '完成', '#059669')):
            if value is not None and 1 <= _number(value) <= ticks_executed:
                center = x_at(_number(value) + (1 if label == '完成' else 0))
                parts.append(f'<circle cx="{center:.2f}" cy="{y + 14}" r="3.5" fill="{color}">'
                             f'<title>{_escape(task_id)}；{label} Tick {_fmt(value)}</title></circle>')
        if arrival is None or _number(arrival) > ticks_executed or task.get('status') == 'unschedulable':
            parts.append(f'<text x="{left}" y="{y + 17}" class="svg-label">'
                         f'{_escape(STATUS.get(task.get("status"), task.get("status", "未观测")))}</text>')
    if not tasks:
        parts.append(f'<text x="{left}" y="{top + 17}" class="svg-label">无任务</text>')
    for tick in sorted({1, max(1, math.ceil(limit / 2)), limit}):
        parts.append(f'<text x="{x_at(tick):.2f}" y="{height - 21}" class="svg-label">{tick}</text>')
    parts.append(f'<text x="{left + plot_width / 2}" y="{height - 3}" text-anchor="middle" class="svg-label">'
                 '物理 Tick（图仅覆盖已执行窗口）</text></svg>')
    return ''.join(parts)


def _balance_windows(windows):
    parts = ['<h2>跨卡计算与通信负载均衡</h2>',
             '<p>计算为窗口内 SOP 累计。通信供给为 2 × 跨核生成 flit，表示源与目的两个端点的应服务量；'
             '通信服务端点为已发生的 Tx + Rx，可反映注入和排空进度。转发其它核的包不重复计为端点负载。</p>',
             '<p>CV 越低越均衡，JFI 越接近 1 越均衡。LIF 定义为 Max / Mean，报告仍单列 Max / Mean 以直接标记慢卡或热点卡，'
             '两者应当相等。P99 使用 nearest-rank：sorted[ceil(0.99 × n) - 1]。因此 4 卡时 P99 就是最大值，P99 / Mean 与 Max / Mean 会相等；'
             '保留两列是为了在更大集群中区分尾部与单个极值。全零窗口五个比率均为 0。</p>']
    if not windows:
        return ''.join(parts) + '<p>未提供窗口聚合。</p>'
    for key, window in windows.items():
        name = '全程窗口' if key == 'full' else '预先固定稳态窗口' if key == 'steady' else str(key)
        complete = bool(window.get('complete'))
        title = f'{name} · Tick {window.get("start_tick", "—")}–{window.get("end_tick", "—")}'
        parts.append(f'<h3>{_escape(title)}</h3><p>窗口完整：{"是" if complete else "否，截断的部分观察"}；'
                     f'已观察 {_fmt(window.get("observed_ticks"))} Tick；自然结束后补零 '
                     f'{_fmt(window.get("padded_zero_ticks"))} Tick。</p>')
        cards = window.get('cards', [])
        parts.append(_table(('卡', '计算 SOP', 'Tx flit', 'Rx flit',
                             '通信供给端点', '通信服务端点'),
                            [(_escape(card.get('card_id', '—')), _metric(card, 'compute_sops'),
                              _metric(card, 'tx_injected'), _metric(card, 'rx_ejected'),
                              _metric(card, 'offered_endpoint_events'), _metric(card, 'endpoint_events'))
                             for card in cards]))
        loads = (('compute_sops', '计算 SOP'),
                 ('offered_endpoint_events', '通信供给端点'),
                 ('endpoint_events', '通信服务端点'))
        balance = window.get('balance', {})
        parts.append('<h4>窗口累计分布</h4>')
        parts.append(_table(('负载', '均值', 'P99 负载', '最大负载', 'CV', 'JFI', 'LIF',
                             'P99 / Mean', 'Max / Mean'),
                            [(_escape(label), _metric(balance.get(field), 'mean_load'),
                              _metric(balance.get(field), 'p99_load'),
                              _metric(balance.get(field), 'normalization_scale'),
                              _metric(balance.get(field, {}).get('cv'), 'value'),
                              _metric(balance.get(field, {}).get('jfi'), 'value'),
                              _metric(balance.get(field, {}).get('lif'), 'value'),
                              _metric(balance.get(field, {}).get('p99_to_mean'), 'value'),
                              _metric(balance.get(field, {}).get('max_to_mean'), 'value'))
                             for field, label in loads]))
        rows = []
        ratio_labels = (('cv', 'CV'), ('jfi', 'JFI'), ('lif', 'LIF'),
                        ('p99_to_mean', 'P99 / Mean'), ('max_to_mean', 'Max / Mean'))
        for field, label in loads:
            measures = balance.get(field, {})
            for ratio, ratio_label in ratio_labels:
                stats = measures.get(ratio) if isinstance(measures, dict) else None
                zero = ('是' if stats.get('zero_denominator') else '否') if isinstance(stats, dict) else '—'
                rows.append((_escape(label), ratio_label, _metric(stats, 'value'),
                             _metric(stats, 'numerator'), _metric(stats, 'denominator'),
                             _metric(measures, 'normalization_scale'), zero))
        parts.append(_table(('负载', '指标', '比率值', '分子', '分母', '共同归一化尺度', '零分母'), rows))
        parts.append('<p class="note">分子、分母使用 y = 卡负载 / 共同归一化尺度；共同缩放不改变比率。'
                     '所有配置卡进入均衡统计，空卡的负载为 0。— 表示旧版结果未提供该字段，不把缺失值伪造为 0。'
                     '稳态窗在运行前固定；截断时不补未来观测。</p>')

        temporal = window.get('temporal_hotspots')
        parts.append('<h4>逐 Tick 热点 / 慢卡</h4>')
        if isinstance(temporal, dict):
            temporal_rows = []
            for field, label in loads:
                load_stats = temporal.get(field, {})
                for ratio, ratio_label in ratio_labels:
                    stats = load_stats.get(ratio) if isinstance(load_stats, dict) else None
                    peak = stats.get('max') if isinstance(stats, dict) else None
                    hotspot = ratio in ('p99_to_mean', 'max_to_mean') and peak is not None and _number(peak) > 1
                    verdict = '<span class="hotspot-tag">热点 / 慢卡</span>' if hotspot else '—'
                    temporal_rows.append((_escape(label), ratio_label, _metric(stats, 'samples'),
                                          _metric(stats, 'mean'), _metric(stats, 'p95'),
                                          _metric(stats, 'p99'), _metric(stats, 'max'), verdict))
            parts.append(_table(('负载', '逐 Tick 比率', '样本 Tick', '均值', 'P95', 'P99', '最大值', '判读'),
                                temporal_rows))
            parts.append('<p class="note">每个 Tick 先在全部卡之间计算比率，再汇总时间上的 mean/P95/P99/max。'
                         '其中 P99 和 max 专门捕获短暂热点，避免全程累计均值把尖峰抹平。</p>')
        else:
            parts.append('<p class="note">旧版结果未提供逐 Tick 热点统计。</p>')

        distribution = window.get('time_card_distribution')
        parts.append('<h4>Tick × 卡时空样本分布</h4>')
        if isinstance(distribution, dict):
            parts.append(_table(('负载', 'Tick × 卡样本', '均值', 'P99 负载', '最大负载',
                                 'P99 / Mean', 'Max / Mean'),
                                [(_escape(label), _metric(distribution.get(field), 'samples'),
                                  _metric(distribution.get(field), 'mean_load'),
                                  _metric(distribution.get(field), 'p99_load'),
                                  _metric(distribution.get(field), 'max_load'),
                                  _metric(distribution.get(field), 'p99_to_mean'),
                                  _metric(distribution.get(field), 'max_to_mean'))
                                 for field, label in loads]))
            parts.append('<p class="note">该表把每个 Tick 的每张卡展平成联合样本，用于判断热点的幅度和占用时间。</p>')
        else:
            parts.append('<p class="note">旧版结果未提供 Tick × 卡联合分布。</p>')
    return ''.join(parts)


def write_cluster_report(output_dir: str | Path) -> Path:
    """Write report.html without rerunning a scenario or fetching any resources."""
    output = Path(output_dir)
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    count = _integer(manifest.get('cards'), 'cards', 1)
    card_ids = list(range(count))
    executed = _integer(manifest.get('ticks_executed'), 'ticks_executed')
    ticks = list(range(1, executed + 1))
    data = _series(_csv_rows(output / 'card_tick.csv', ('card_id', 'physical_tick')), card_ids, executed)
    resources = _series(_csv_rows(output / 'card_resources.csv', ('card_id', 'physical_tick')), card_ids, executed)
    tasks = list(manifest.get('tasks', []))
    tasks.sort(key=lambda row: (_number(row.get('arrival_tick')), str(row.get('task_id', ''))))
    totals, derived, timing = (manifest.get(name, {}) for name in ('totals', 'derived', 'timing'))
    status = manifest.get('status', 'unknown')
    heading = f'{manifest.get("scenario", "多卡仿真")} · {manifest.get("policy", "—")}'
    config = manifest.get('config', {})
    mesh_x, mesh_y = config.get('mesh_x', '—'), config.get('mesh_y', '—')
    valid = manifest.get('valid') is True
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>{_escape(heading)}</title>',
             '<style>body{font:15px/1.65 system-ui,sans-serif;color:#0f172a;background:#f8fafc;margin:0}'
             'main{max-width:1250px;margin:auto;padding:28px}h1{font-size:26px}h2{margin-top:32px}'
             'h3{margin-top:22px}.panel{background:#fff;border:1px solid #e2e8f0;border-radius:10px;'
             'padding:16px;margin:16px 0}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}'
             'th,td{border-bottom:1px solid #e2e8f0;padding:9px 12px;text-align:left;white-space:nowrap}'
             'th{background:#f1f5f9}svg{width:100%;height:auto;min-width:650px;display:block}'
             '.chart{overflow:auto}.svg-title{font:600 15px sans-serif;fill:#0f172a}'
             '.svg-label{font:12px sans-serif;fill:#475569}.note{color:#475569;font-size:13px}'
             '.status{border-left:5px solid #059669;padding:12px 16px;background:#ecfdf5}'
             '.hotspot-tag{display:inline-block;color:#991b1b;background:#fee2e2;border-radius:999px;padding:1px 7px;font-weight:600}'
             '.incomplete{border-color:#d97706;background:#fffbeb}a{color:#2563eb}</style></head><body><main>',
             f'<h1>{_escape(heading)}</h1>',
             f'<p class="status{"" if valid else " incomplete"}">状态：{_escape(STATUS.get(status, status))}；'
             f'完整有效运行：{"是" if valid else "否"}；已执行 {_fmt(executed)} 物理 Tick；'
             f'{_fmt(count)} 张卡，每卡 {_escape(mesh_x)} × {_escape(mesh_y)} mesh；'
             f'每物理 Tick 固定 {_fmt(manifest.get("physical_tick_period_cycles"))} NoC cycle。</p>',
             '<p>每张卡独立运行 NoC 和接收屏障，一卡等待不冻结其它卡。通信超出物理 Tick 窗口后继续排队，'
             '当前逻辑步延长；该卡全体运行任务在共同 Rx 屏障释放后产生下一步。每个物理核连接一个本地 Router。'
             '所有策略共用 row-major 空闲核 mapper，按 MicroPopulation 顺序分配递增空核编号。</p>',
             '<p class="note">SOP 是独立计算工作量记账，尚未模拟计算服务时间。统一 mapper 规则仍可能因选卡历史形成不同路径，'
             '具体核映射保留在任务表。源端 Tx 表示 NI 注入 Router，目的端 Rx 表示 Router 交付目的 NI。</p>']
    completed = sum(task.get('status') == 'completed' for task in tasks)
    parts.append(_table(('任务总数', '已完成', '未完成或无法部署', '跨核生成 flit', 'Tx flit', 'Rx flit', '计算 SOP'),
                        [(_fmt(len(tasks)), _fmt(completed), _fmt(len(tasks) - completed),
                          _fmt(totals.get('generated_tx')), _fmt(totals.get('tx_injected')),
                          _fmt(totals.get('rx_ejected')), _fmt(totals.get('compute_sops')))]))
    parts.append('<h2>运行时间与通信等待</h2>')
    stats_rows = []
    for field, label in (('task_end_to_end_ticks', '完成任务端到端 Tick'),
                         ('task_execution_ticks', '完成任务执行 Tick'),
                         ('resource_wait_ticks', '完成任务资源等待 Tick'),
                         ('boundary_wait_ticks', '完成任务屏障边界等待 Tick'),
                         ('communication_extension_ticks', '完成任务通信延长 Tick'),
                         ('slowdown', '完成任务 slowdown（执行 Tick / 理想逻辑步数）')):
        stats = timing.get('completed_task_statistics', {}).get(field, {})
        stats_rows.append((_escape(label), _fmt(stats.get('samples')),
                           _fmt(stats.get('mean')), _fmt(stats.get('p95')), _fmt(stats.get('sum'))))
    parts.append(_table(('指标', '完成样本数', '均值', 'p95', '分子合计'), stats_rows))
    parts.append('<p class="note">未完成任务不计为 0 时延；均值与分位只包含完成样本。通信延长也包含路径、同源串行发包和同卡屏障等待。</p>')
    ratios = (( '源等待均值 cycle', 'mean_source_wait_cycles', 'source_wait_cycles_sum', 'tx_injected'),
              ('Rx 延迟均值 cycle', 'mean_rx_latency_cycles', 'rx_latency_cycles_sum', 'rx_ejected'),
              ('Rx 超额延迟均值 cycle', 'mean_rx_excess_latency_cycles', 'rx_excess_latency_cycles_sum', 'rx_ejected'),
              ('Router 等待 / 生成 flit', 'router_wait_per_generated_flit', 'router_wait_flit_cycles', 'generated_tx'))
    parts.append(_table(('通信指标', '比率值', '分子', '分母'),
                        [(_escape(label), _fmt(derived.get(field)), _fmt(totals.get(numerator)),
                          _fmt(totals.get(denominator))) for label, field, numerator, denominator in ratios]))
    parts.append(_table(('全卡通信延长合计', '全卡屏障阻塞合计', 'Rx 延迟 p95 cycle', 'Rx 超额延迟 p95 cycle', '任务吞吐 / Tick'),
                        [(_fmt(timing.get('card_communication_extension_ticks')),
                          _fmt(timing.get('card_barrier_blocked_ticks')), _fmt(derived.get('rx_latency_p95_cycles')),
                          _fmt(derived.get('rx_excess_latency_p95_cycles')), _fmt(timing.get('task_throughput_per_tick')))]))
    parts.append(_balance_windows(manifest.get('balance_windows', {})))
    parts.append('<h2>各卡逐物理 Tick 负载</h2><p>延长 Tick 中旧包继续流动；SOP 和新包只在逻辑步首次发出时记一次。曲线零值保留。</p>')
    for field in ('compute_sops', 'tx_injected', 'rx_ejected', 'router_wait_flit_cycles', 'communication_extension_tick'):
        parts.append(f'<div class="panel chart">{_heatmap(LABELS[field] + " · 卡 × 物理 Tick", card_ids, data, ticks, field)}</div>')
    if any(resources.values()):
        parts.append('<h3>每 Tick 末预留资源</h3><p class="note">已放置但尚未启动的任务也预留核与内存；任务实际完成后释放。</p>')
        for field in ('used_cores', 'used_memory_mb'):
            parts.append(f'<div class="panel chart">{_heatmap(LABELS[field], card_ids, resources, ticks, field, mean=True)}</div>')
    for card_id in card_ids:
        parts.append(f'<details class="panel"><summary>卡 {card_id} 负载曲线与详细观测</summary>'
                     f'<p><a href="cards/card_{card_id}/report.html">打开卡 {card_id} 的任务、MicroPopulation、队列与链路详细报告</a></p>'
                     f'<div class="chart">{_chart(card_id, data[card_id], ticks)}</div></details>')
    parts.append('<h2>任务生命周期与实际映射</h2>')
    parts.append(f'<div class="panel chart">{_timeline(tasks, executed)}</div>')
    task_rows = []
    for task in tasks:
        mapping = ', '.join(str(value) for value in task.get('mapping', []))
        task_rows.append((_escape(task.get('task_id', '')), _escape(STATUS.get(task.get('status'), task.get('status', '—'))),
                          _optional(task.get('card_id')), _optional(task.get('arrival_tick')),
                          _optional(task.get('placement_tick')), _optional(task.get('actual_start_tick')),
                          _optional(task.get('completion_tick')), _optional(task.get('resource_wait_ticks')),
                          _optional(task.get('phase_wait_ticks', 0)),
                          _optional(task.get('boundary_wait_ticks')), _optional(task.get('task_execution_ticks')),
                          _optional(task.get('task_end_to_end_ticks')), _optional(task.get('communication_extension_ticks')),
                          _optional(task.get('slowdown')), _escape(mapping or '—')))
    parts.append(_table(('任务', '状态', '卡', '到达 Tick', '放置 Tick', '实际启动 Tick', '完成 Tick',
                         '资源等待', '主动延迟', '边界等待', '执行 Tick', '端到端 Tick', '通信延长 Tick', 'slowdown', '映射核编号'), task_rows))
    parts.append('<p class="note">完成任务：端到端 = 资源等待 + 主动延迟 + 边界等待 + 执行；执行 = 逻辑步数 + 通信延长。'
                 '空白完成时间和最终时延表示未完成或未启动，不推测未来值。</p>')
    parts.append('<p>原始结果：<a href="manifest.json">manifest.json</a> · <a href="card_tick.csv">card_tick.csv</a> · '
                 '<a href="task_summary.csv">task_summary.csv</a> · <a href="decisions.csv">decisions.csv</a> · '
                 '<a href="balance_windows.json">balance_windows.json</a>。本页图表已内嵌，打开时无需网络。</p>')
    parts.append('</main></body></html>')
    path = output / 'report.html'
    path.write_text(''.join(parts), encoding='utf-8')
    return path
