"""Build a standalone Chinese HTML report from one NoC simulator run."""

from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path


METRICS = ("compute_sops", "generated_tx", "tx_injected", "rx_ejected")
DIAGNOSTICS = ("tx_stall_cycles", "rx_blocked_cycles", "router_wait_flit_cycles",
               "source_wait_cycles_sum", "rx_latency_cycles_sum")
ALL_FIELDS = METRICS + DIAGNOSTICS
LABELS = ("计算 SOP", "产生 flit", "注入 Tx", "接收 Rx")
COLORS = ("#2563eb", "#ea580c", "#059669", "#7c3aed")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _number(value: object) -> float:
    result = float(value) if value not in (None, "") else 0.0
    if not math.isfinite(result):
        raise ValueError(f"report input contains a nonfinite number: {value!r}")
    return result


def _int(value: object, field: str, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if result < minimum:
        raise ValueError(f"invalid {field}: {value!r}")
    return result


def _fmt(value: float) -> str:
    if value == int(value):
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _rows(path: Path, required: tuple[str, ...]):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(required).issubset(reader.fieldnames):
            raise ValueError(f"{path.name} lacks required columns {required}")
        yield from reader


def _accumulate(table: dict, key: object, tick: int, row: dict) -> None:
    values = table.setdefault(key, {}).setdefault(tick, {field: 0.0 for field in ALL_FIELDS})
    for field in ALL_FIELDS:
        values[field] += _number(row.get(field))


def _at(series: dict, tick: int, metric: str) -> float:
    return series.get(tick, {}).get(metric, 0.0)


def _totals(series: dict) -> tuple[float, ...]:
    return tuple(sum(row.get(metric, 0.0) for row in series.values()) for metric in METRICS)


def _sum(series: dict, field: str) -> float:
    return sum(row.get(field, 0.0) for row in series.values())


def _ticks(manifest: dict, metadata: dict[str, dict], observed: int) -> list[int]:
    end = max(1, observed)
    if manifest.get("ticks_executed") is not None:
        end = max(end, _int(manifest["ticks_executed"], "ticks_executed"))
    if manifest.get("status") == "completed":
        for task in metadata.values():
            if task.get("completion_tick") is not None:
                end = max(end, _int(task["completion_tick"], "completion_tick", 1))
    elif manifest.get("status") == "max_ticks":
        config = manifest.get("config", {})
        candidates = [manifest.get("max_ticks")]
        if isinstance(config, dict):
            candidates.extend((config.get("max_ticks"), config.get("physical_ticks")))
        for candidate in candidates:
            if candidate is not None:
                end = max(end, _int(candidate, "max_ticks", 1))
    return list(range(1, end + 1))


def _queue_samples_per_tick(manifest: dict, kind: str) -> int | None:
    """Count every queue-cycle, including all-zero rows omitted by the writer."""
    config = manifest.get("config")
    if not isinstance(config, dict):
        return None
    try:
        cores = _int(config.get("mesh_x"), "mesh_x", 1) * _int(config.get("mesh_y"), "mesh_y", 1)
        period = _int(manifest.get("physical_tick_period_cycles"),
                      "physical_tick_period_cycles", 1)
    except ValueError:
        return None
    return cores * period * (5 if kind == "router" else 1)


def _line_svg(title: str, series: dict, ticks: list[int]) -> str:
    width, height, left, right = 1000, 635, 77, 23
    draw_width = width - left - right

    def x_at(index: int) -> float:
        return left + (draw_width * index / (len(ticks) - 1) if len(ticks) > 1 else draw_width / 2)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(title)} 逐 tick 曲线">',
        '<style>.axis{font:12px sans-serif;fill:#64748b}.head{font:600 15px sans-serif;fill:#0f172a}'
        '.legend{font:12px sans-serif;fill:#334155}</style>',
    ]
    for top, fields, panel_title in (
        (54, tuple((METRICS[index], COLORS[index], LABELS[index]) for index in (0,)),
         "计算工作量 · SOP/tick"),
        (250, tuple((METRICS[index], COLORS[index], LABELS[index]) for index in (1, 2, 3)),
         "通信端点 · flit/tick"),
        (447, (("router_wait_flit_cycles", "#be123c", "路由器队头等待 flit-cycle"),),
         "路由器队头等待 · flit-cycle/tick"),
    ):
        peak = max((_at(series, tick, metric) for tick in ticks for metric, _, _ in fields), default=0.0)
        scale = peak if peak > 0 else 1.0
        parts.append(f'<text x="{left}" y="{top - 17}" class="head">{panel_title}</text>')
        for fraction in (0.0, 0.5, 1.0):
            y = top + 128 * (1 - fraction)
            parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" '
                         f'stroke="#e2e8f0"/><text x="{left - 8}" y="{y + 4:.2f}" '
                         f'text-anchor="end" class="axis">{_fmt(scale * fraction)}</text>')
        for metric, color, label in fields:
            points = " ".join(f"{x_at(i):.2f},{top + 128 * (1 - _at(series, tick, metric) / scale):.2f}"
                              for i, tick in enumerate(ticks))
            parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" '
                         f'stroke-width="2.5" stroke-linejoin="round"><title>'
                         f'{_escape(label)}：{_fmt(_sum(series, metric))}</title></polyline>')
    for index in (1, 2, 3):
        x = left + (index - 1) * 155
        parts.append(f'<line x1="{x}" y1="220" x2="{x + 22}" y2="220" '
                     f'stroke="{COLORS[index]}" stroke-width="3"/>'
                     f'<text x="{x + 28}" y="224" class="legend">{LABELS[index]}</text>')
    for index in sorted({round(i * (len(ticks) - 1) / 5) for i in range(6)}):
        parts.append(f'<text x="{x_at(index):.2f}" y="596" text-anchor="middle" class="axis">'
                     f'{ticks[index]}</text>')
    parts.append('<text x="500" y="621" text-anchor="middle" class="axis">物理 tick</text></svg>')
    return "".join(parts)


def _heatmap(title: str, labels: list[str], series: list[dict], ticks: list[int], metric: str,
             *, mean: bool = False) -> str:
    chunk = max(1, math.ceil(len(ticks) / 96))
    bins = [ticks[i:i + chunk] for i in range(0, len(ticks), chunk)]
    cell_width = min(27.0, max(8.0, 900.0 / len(bins)))
    left, top, row_height = 190, 67, 23
    width = math.ceil(left + cell_width * len(bins) + 24)
    height = top + max(1, len(labels)) * row_height + 43
    matrix = [[sum(_at(row, tick, metric) for tick in bucket) / (len(bucket) if mean else 1)
               for bucket in bins] for row in series]
    peak = max((value for row in matrix for value in row), default=0.0)
    color = ("#0f766e" if metric == "occupancy" else
             "#be123c" if metric == "router_wait_flit_cycles" else
             "#2563eb" if metric == "logical_step" else
             COLORS[METRICS.index(metric)])
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(title)}">',
        '<style>.head{font:600 15px sans-serif;fill:#0f172a}'
        '.small{font:11px sans-serif;fill:#64748b}.row{font:12px sans-serif;fill:#334155}</style>',
        f'<text x="12" y="23" class="head">{_escape(title)}</text>',
        f'<text x="12" y="42" class="small">0 → {_fmt(peak)}；'
        f'{"区间平均" if mean else "区间合计"}；空白 tick 按 0</text>',
    ]
    if not labels:
        parts.append('<text x="12" y="83" class="small">无可用行</text>')
    for row_index, label in enumerate(labels):
        y = top + row_index * row_height
        short = label if len(label) <= 25 else label[:24] + "…"
        parts.append(f'<text x="{left - 10}" y="{y + 15}" text-anchor="end" class="row">'
                     f'{_escape(short)}<title>{_escape(label)}</title></text>')
        for column, bucket in enumerate(bins):
            value = matrix[row_index][column]
            fraction = min(1.0, max(0.0, value / peak)) if peak else 0.0
            shade = "#f1f5f9" if fraction == 0 else "#" + "".join(
                f"{round(pale + (dark - pale) * fraction):02x}"
                for pale, dark in zip((239, 246, 255), rgb)
            )
            x = left + column * cell_width
            interval = str(bucket[0]) if len(bucket) == 1 else f"{bucket[0]}–{bucket[-1]}"
            parts.append(f'<rect x="{x:.2f}" y="{y}" width="{cell_width - 1:.2f}" '
                         f'height="20" fill="{shade}"><title>{_escape(label)}；物理 tick '
                         f'{interval}；{_escape(metric)} {_fmt(value)}</title></rect>')
    for column in sorted({0, len(bins) // 2, len(bins) - 1}):
        parts.append(f'<text x="{left + column * cell_width:.2f}" y="{height - 13}" '
                     f'class="small">{bins[column][0]}</text>')
    parts.append('</svg>')
    return "".join(parts)


def _workload_svg(name: str, tasks: list[str], data: dict) -> str:
    width, height = 1100, 146 + max(1, len(tasks)) * 75
    totals = {task: _totals(data.get(task, {})) for task in tasks}
    sop_peak = max((values[0] for values in totals.values()), default=0.0) or 1.0
    flit_peak = max((values[index] for values in totals.values() for index in (1, 2, 3)),
                    default=0.0) or 1.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(name)} 工作负载总览">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>.title{font:600 22px sans-serif;fill:#0f172a}'
        '.head{font:600 13px sans-serif;fill:#475569}'
        '.name{font:13px sans-serif;fill:#0f172a}.value{font:11px sans-serif;fill:#475569}</style>',
        f'<text x="24" y="36" class="title">{_escape(name)} · 工作负载总览</text>',
        '<text x="190" y="72" class="head">计算总量（SOP，独立比例尺）</text>',
        '<text x="560" y="72" class="head">通信总量（flit，共同比例尺）</text>',
    ]
    if not tasks:
        parts.append('<text x="24" y="110" class="name">无任务记录</text>')
    for ordinal, task in enumerate(tasks):
        y = 91 + ordinal * 75
        values = totals[task]
        short = task if len(task) <= 20 else task[:19] + "…"
        parts.append(f'<line x1="24" y1="{y + 69}" x2="1070" y2="{y + 69}" '
                     f'stroke="#e2e8f0"/><text x="24" y="{y + 31}" class="name">'
                     f'{_escape(short)}<title>{_escape(task)}</title></text>')
        sop_width = max(0.0, values[0]) / sop_peak * 250
        parts.append(f'<rect x="190" y="{y + 22}" width="{sop_width:.2f}" height="18" '
                     f'fill="{COLORS[0]}"/><text x="{195 + sop_width:.2f}" y="{y + 36}" '
                     f'class="value">{_fmt(values[0])}</text>')
        for index in (1, 2, 3):
            bar_y = y + 2 + (index - 1) * 20
            bar_width = max(0.0, values[index]) / flit_peak * 320
            parts.append(f'<text x="512" y="{bar_y + 10}" class="value">{LABELS[index]}</text>'
                         f'<rect x="560" y="{bar_y}" width="{bar_width:.2f}" height="11" '
                         f'fill="{COLORS[index]}"/><text x="{565 + bar_width:.2f}" '
                         f'y="{bar_y + 10}" class="value">{_fmt(values[index])}</text>')
    parts.append(f'<text x="24" y="{height - 16}" class="value">'
                 'SOP 与 flit 使用不同单位和比例尺；Tx 与 Rx 是两个端点事件。</text></svg>')
    return "".join(parts)


def write_report(output_dir: Path) -> Path:
    """Create report.html and workload.svg from the five simulator artifacts."""
    output_dir = Path(output_dir)
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    task_order: list[str] = []
    metadata: dict[str, dict] = {}
    for item in manifest.get("tasks", []):
        if isinstance(item, dict) and item.get("task_id") is not None:
            task = str(item["task_id"])
            if task not in metadata:
                task_order.append(task)
                metadata[task] = item

    from_core: dict[str, dict] = {}
    by_population: dict[tuple[str, int], dict] = {}
    from_task: dict[str, dict] = {}
    by_card: dict[str, dict] = {}
    queues: dict[str, dict] = {}
    queue_summary: dict[str, dict] = {}
    observed = 0
    for row in _rows(output_dir / "core_tick.csv", ("task_id", "physical_tick", "population_id")):
        tick = _int(row["physical_tick"], "physical_tick", 1)
        population = _int(row["population_id"], "population_id")
        task = str(row["task_id"])
        observed = max(observed, tick)
        _accumulate(from_core, task, tick, row)
        _accumulate(by_population, (task, population), tick, row)

    for row in _rows(output_dir / "task_tick.csv", ("task_id", "physical_tick")):
        tick = _int(row["physical_tick"], "physical_tick", 1)
        observed = max(observed, tick)
        _accumulate(from_task, str(row["task_id"]), tick, row)

    for row in _rows(output_dir / "card_tick.csv", ("physical_tick",)):
        tick = _int(row["physical_tick"], "physical_tick", 1)
        observed = max(observed, tick)
        _accumulate(by_card, str(row.get("card_id") or "0"), tick, row)

    for row in _rows(output_dir / "queue_stats.csv", ("physical_tick", "kind")):
        tick = _int(row["physical_tick"], "physical_tick", 1)
        observed = max(observed, tick)
        kind = str(row["kind"])
        values = queues.setdefault(kind, {}).setdefault(tick, {"occupancy_sum": 0.0, "samples": 0.0})
        values["occupancy_sum"] += _number(row.get("occupancy_sum"))
        values["samples"] += _number(row.get("samples"))
        summary = queue_summary.setdefault(kind, {"occupancy_sum": 0.0, "samples": 0.0,
                                                  "occupancy_peak": 0.0, "full_cycles": 0.0,
                                                  "capacities": set()})
        for field in ("occupancy_sum", "samples", "full_cycles"):
            summary[field] += _number(row.get(field))
        summary["occupancy_peak"] = max(summary["occupancy_peak"],
                                         _number(row.get("occupancy_peak")))
        if row.get("capacity") not in (None, ""):
            summary["capacities"].add(str(row["capacity"]))

    task_data = {task: dict(series) for task, series in from_core.items()}
    for task, series in from_task.items():
        task_data.setdefault(task, {}).update(series)
    task_order.extend(sorted((set(task_data) | {key[0] for key in by_population}) - set(task_order)))
    ticks = _ticks(manifest, metadata, observed)
    scenario = manifest.get("scenario")
    name = scenario.get("name") if isinstance(scenario, dict) else scenario
    name = str(name or "NoC 仿真")
    workload_svg = _workload_svg(name, task_order, task_data)
    (output_dir / "workload.svg").write_text(workload_svg, encoding="utf-8")

    status = str(manifest.get("status", "unknown"))
    status_label = {"completed": "正常完成",
                    "max_ticks": "达到最大物理 tick"}.get(status, status)
    status_class = "ok" if status == "completed" else "alert"
    elapsed = manifest.get("elapsed_seconds")
    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>{_escape(name)} · NoC 仿真报告</title>',
        '<style>body{font:15px/1.6 system-ui,sans-serif;color:#0f172a;background:#f8fafc;margin:0}'
        'main{max-width:1180px;margin:auto;padding:32px 20px}h1,h2,h3{line-height:1.3}'
        'section,.card{background:white;border:1px solid #e2e8f0;border-radius:12px;'
        'padding:20px;margin:20px 0}.muted{color:#64748b}.ok{color:#047857}.alert{color:#b91c1c}'
        '.scroll{overflow-x:auto}svg{max-width:none;height:auto}table{border-collapse:collapse;width:100%}'
        'th,td{border-bottom:1px solid #e2e8f0;text-align:left;padding:7px 9px}'
        'th{background:#f1f5f9}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
        'details{margin:14px 0}summary{cursor:pointer;font-weight:600}</style>',
        '</head><body><main>',
        f'<h1>{_escape(name)} · NoC 仿真报告</h1>',
        '<section><h2>运行概况</h2>',
        f'<p>状态：<strong class="{status_class}">{_escape(status_label)}</strong>；'
        f'物理 tick：1–{ticks[-1]}；任务数：{len(task_order)}；'
        f'通信延长：{_escape(manifest.get("timing", {}).get("communication_extension_ticks", 0))} Tick；'
        f'耗时：{_escape(_fmt(_number(elapsed)) if elapsed is not None else "未记录")} 秒。</p>',
        '<p class="muted">SOP 为计算工作量；产生 flit、源核注入 Tx 与目的核接收 Rx '
        '是不同事件。空白稀疏行按 0 补齐。SOP 与 flit 分别缩放。</p>',
        '<div class="scroll">', workload_svg, '</div></section>',
        '<section><h2>任务逐 tick 曲线</h2>',
    ]
    for task in task_order:
        meta = metadata.get(task, {})
        values = _totals(task_data.get(task, {}))
        parts.extend((
            '<article class="card">', f'<h3>任务 {_escape(task)}</h3>',
            f'<p class="muted">预设启动：{_escape(meta.get("start_tick", "未记录"))}；'
            f'实际启动：{_escape(meta.get("actual_start_tick", "未记录"))}；'
            f'完成：{_escape(meta.get("completion_tick", "未完成"))}；'
            f'总计算 {_fmt(values[0])} SOP；产生 {_fmt(values[1])} flit；'
            f'Tx {_fmt(values[2])}；Rx {_fmt(values[3])}。</p>',
            '<div class="scroll">', _line_svg(task, task_data.get(task, {}), ticks), '</div>',
            '</article>',
        ))
    if len(task_order) > 1:
        parts.append('<h3>任务计算与通信负载对比</h3><table><thead><tr><th>任务</th>'
                     '<th>计算 SOP</th><th>产生 flit</th><th>Tx</th><th>Rx</th></tr></thead><tbody>')
        for task in task_order:
            values = _totals(task_data.get(task, {}))
            parts.append(f'<tr><td>{_escape(task)}</td>' + ''.join(
                f'<td>{_fmt(value)}</td>' for value in values
            ) + '</tr>')
        parts.append('</tbody></table>')
    if not task_order:
        parts.append('<p>无任务记录。</p>')
    parts.append('</section><section><h2>通信延长与任务时间</h2>'
                 '<p class="muted">每物理 Tick 固定 K cycle；未完成 Rx 时保留网络队列，'
                 '同卡任务等待全卡屏障。SOP 和请求只在逻辑步开始时记一次。</p>'
                 '<table><thead><tr><th>任务</th><th>启动等待 Tick</th><th>执行 Tick</th>'
                 '<th>端到端 Tick</th><th>通信延长 Tick</th><th>减速比</th></tr></thead><tbody>')
    for task in task_order:
        meta = metadata.get(task, {})
        parts.append(f'<tr><td>{_escape(task)}</td>' + ''.join(
            f'<td>{_escape(_fmt(_number(meta[field])) if meta.get(field) is not None else "未完成")}</td>'
            for field in ('task_start_wait_ticks', 'task_execution_ticks', 'task_end_to_end_ticks',
                          'communication_extension_ticks', 'slowdown')) + '</tr>')
    parts.append('</tbody></table>')
    step_rows = list(_rows(output_dir / "step_timing.csv", ("task_id", "logical_tick")))
    if step_rows:
        step_series = {}
        for row in step_rows:
            start, end = int(row['step_start_tick']), int(row['step_end_tick'])
            for tick in range(start, end + 1):
                step_series.setdefault(str(row['task_id']), {})[tick] = {
                    'logical_step': int(row['logical_tick']) + 1,
                }
        parts.extend(('<div class="scroll">',
                      _heatmap('任务逻辑步进度（显示 logical_tick + 1）', task_order,
                               [step_series.get(task, {}) for task in task_order], ticks, 'logical_step', mean=True),
                      '</div><p class="muted">同一逻辑步占多列即跨物理 Tick 等待；颜色值为逻辑步编号加一。</p>'))
    parts.append('</section><section><h2>任务热图</h2><p class="muted">行是任务，列是物理 tick；'
                 '超过 96 tick 时合并连续区间，每格显示区间合计。</p>')
    task_rows = [task_data.get(task, {}) for task in task_order]
    for metric, label in zip(METRICS, LABELS):
        parts.extend(('<div class="scroll">',
                      _heatmap(f'任务 × tick · {label}', task_order, task_rows, ticks, metric),
                      '</div>'))
    parts.append('</section><section><h2>MicroPopulation 热图</h2>'
                 '<p class="muted">每个MicroPopulation按物理 tick 展开；未出现的已声明MicroPopulation按 0。'
                 '核号读取 manifest 映射。多个逻辑步的延迟事件按实际发生的物理 tick 合并。</p>')
    for task in task_order:
        meta = metadata.get(task, {})
        mapping = meta.get("mapping")
        mapping = mapping if isinstance(mapping, list) else []
        declared = meta.get("population_count", len(mapping))
        count = _int(declared, "population_count") if declared is not None else len(mapping)
        inferred = max((pop for tid, pop in by_population if tid == task), default=-1) + 1
        count = max(count, len(mapping), inferred)
        labels = [f'MicroPopulation {pop}（核 {mapping[pop]}）' if pop < len(mapping) else f'MicroPopulation {pop}'
                  for pop in range(count)]
        rows = [by_population.get((task, pop), {}) for pop in range(count)]
        parts.append(f'<details><summary>任务 {_escape(task)} · {count} 个MicroPopulation</summary>')
        for metric, label in zip(METRICS, LABELS):
            parts.extend(('<div class="scroll">',
                          _heatmap(f'{task} · MicroPopulation × tick · {label}', labels, rows, ticks, metric),
                          '</div>'))
        parts.extend(('<div class="scroll">',
                      _heatmap(f'{task} · MicroPopulation × tick · 路由器队头等待 flit-cycle',
                               labels, rows, ticks, 'router_wait_flit_cycles'),
                      '</div>'))
        parts.append('</details>')
    parts.append('</section><section><h2>卡级汇总</h2><p class="muted">数据读取 card_tick.csv；'
                 '若文件无数据行，从任务汇总推导。</p>')
    if not by_card:
        derived: dict[int, dict[str, float]] = {}
        for series in task_data.values():
            for tick, row in series.items():
                values = derived.setdefault(tick, {metric: 0.0 for metric in ALL_FIELDS})
                for metric in ALL_FIELDS:
                    values[metric] += row.get(metric, 0.0)
        by_card = {"0": derived}
    parts.append('<table><thead><tr><th>卡</th><th>计算 SOP</th><th>产生 flit</th>'
                 '<th>Tx</th><th>Rx</th></tr></thead><tbody>')
    for card in sorted(by_card):
        values = _totals(by_card[card])
        parts.append(f'<tr><td>{_escape(card)}</td>' + ''.join(f'<td>{_fmt(value)}</td>'
                                                          for value in values) + '</tr>')
        parts.extend(('<tr><td colspan="5"><div class="scroll">',
                      _line_svg(f'卡 {card}', by_card[card], ticks), '</div></td></tr>'))
    parts.append('</tbody></table></section><section><h2>队列与拥塞</h2>')
    if queue_summary:
        parts.append('<table><thead><tr><th>队列种类</th><th>平均占用</th><th>峰值占用</th>'
                     '<th>满队列周期</th><th>容量</th></tr></thead><tbody>')
        for kind in sorted(queue_summary):
            summary = queue_summary[kind]
            full_per_tick = _queue_samples_per_tick(manifest, kind)
            samples = max(summary["samples"], full_per_tick * len(ticks)) if full_per_tick else summary["samples"]
            cap = ", ".join(sorted(summary["capacities"])) or "无界/未记录"
            parts.append(f'<tr><td>{_escape(kind)}</td>'
                         f'<td>{_fmt(summary["occupancy_sum"] / samples if samples else 0.0)}</td>'
                         f'<td>{_fmt(summary["occupancy_peak"])}</td>'
                         f'<td>{_fmt(summary["full_cycles"])}</td><td>{_escape(cap)}</td></tr>')
        parts.append('</tbody></table>')
        kinds = sorted(queues)
        queue_rows = []
        for kind in kinds:
            full_per_tick = _queue_samples_per_tick(manifest, kind)
            queue_rows.append({
                tick: {"occupancy": (values["occupancy_sum"] /
                                     (max(values["samples"], full_per_tick) if full_per_tick
                                      else values["samples"]))
                       if values["samples"] or full_per_tick else 0.0}
                for tick, values in queues[kind].items()
            })
        parts.extend(('<div class="scroll">', _heatmap('队列 × tick · 平均占用', kinds,
                                                        queue_rows, ticks, 'occupancy', mean=True),
                      '</div>'))
    else:
        parts.append('<p>队列统计无活动行。</p>')
    diagnostics = {field: sum(_sum(series, field) for series in task_data.values())
                   for field in DIAGNOSTICS}
    parts.append('<h3>网络等待诊断</h3><p class="muted">路由器队头等待按每个等待中的输入队头'
                 '累计 flit-cycle；它不是物理核停滞比例，源核注入停滞可为 0。</p>'
                 '<table><thead><tr><th>指标</th><th>累计值</th></tr>'
                 '</thead><tbody>')
    for field, label in (("tx_stall_cycles", "源核注入停滞周期"),
                         ("rx_blocked_cycles", "目标核接收阻塞周期"),
                         ("router_wait_flit_cycles", "路由器队头等待 flit-cycle"),
                         ("source_wait_cycles_sum", "源端等待周期合计"),
                         ("rx_latency_cycles_sum", "接收延迟周期合计")):
        parts.append(f'<tr><td>{label}</td><td>{_fmt(diagnostics[field])}</td></tr>')
    parts.append('</tbody></table>')
    parts.append('</section><section><h2>运行元数据</h2><details><summary>manifest 原始内容</summary><pre>')
    parts.append(_escape(json.dumps(manifest, ensure_ascii=False, indent=2)))
    parts.append('</pre></details></section></main></body></html>')
    report_path = output_dir / "report.html"
    report_path.write_text("".join(parts), encoding="utf-8")
    return report_path


__all__ = ["write_report"]
