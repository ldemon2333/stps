"""Stream per-card output into a shared physical-time cluster observation.

NoC counters retain their original endpoint meaning. Inventory columns are
end-boundary snapshots, and balance ratios include every configured card.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import csv
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Sequence

from util.metrics import GAUGES, MEASURES, SimulationMetrics


MERGED_TABLES = (
    "core_tick", "task_tick", "card_tick", "queue_stats", "link_stats",
    "step_timing", "outstanding", "events",
)
TASK_LIFECYCLE_FIELDS = (
    "task_id", "status", "arrival_tick", "placement_tick", "requested_start_tick",
    "actual_start_tick", "completion_tick", "task_execution_ticks",
    "task_end_to_end_ticks", "resource_wait_ticks", "boundary_wait_ticks",
    "communication_extension_ticks", "slowdown", "phase_offset_ticks",
    "task_start_wait_ticks", "observed_execution_ticks", "steps_started",
    "steps_completed", "population_count", "logical_ticks", "rejection_reason",
    "mean_compute_sops", "mean_noc_endpoint", "phase_wait_ticks",
    "predicted_actual_start", "predicted_completion", "start_prediction_error_ticks",
    "completion_prediction_error_ticks",
)
TASK_STAT_FIELDS = (
    "task_execution_ticks", "task_end_to_end_ticks", "resource_wait_ticks",
    "boundary_wait_ticks", "communication_extension_ticks", "slowdown", "phase_wait_ticks",
)
LOAD_FIELDS = (
    "compute_sops", "tx_injected", "rx_ejected", "endpoint_events",
    "offered_endpoint_events",
)
SLIDING_LOAD_FIELDS = ("compute_sops", "offered_endpoint_events", "endpoint_events")
RATIO_FIELDS = ("cv", "jfi", "lif", "p99_to_mean", "max_to_mean")
TERMINAL_STATUSES = frozenset(("completed", "unschedulable"))


@contextmanager
def _atomic_text(path: Path):
    """Publish each complete output file; never leave a partial destination."""
    handle = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                         dir=path.parent, prefix=f".{path.name}.", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            yield handle
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _number(value, name: str = "value") -> int | float:
    if value is None or value == "":
        return 0
    try:
        number = value if isinstance(value, int) else int(value) if isinstance(value, str) and "." not in value and "e" not in value.lower() else float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number: {value!r}")
    return number


def _offered_endpoint_events(generated_tx) -> int | float:
    return _number(2 * _number(generated_tx, "generated_tx"),
                   "offered_endpoint_events")


def _tick(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    number = _number(value, name)
    if number < 1 or int(number) != number:
        raise ValueError(f"{name} must be a positive integer")
    return int(number)


def _nearest_rank(values: Sequence[int | float], quantile: float) -> int | float:
    """Return a nearest-rank quantile (ceil(q*n), with ranks starting at one)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def balance_metrics(loads: Sequence[int | float]) -> dict:
    """CV/JFI and tail imbalance with overflow-safe ratio arithmetic.

    Ratios use y_m=x_m/normalization_scale. This leaves all three ratios
    unchanged and avoids squaring raw flit/SOP totals. The original values are
    retained by the calling window alongside the common normalization scale.
    """
    if not loads:
        raise ValueError("balance requires at least one card")
    values = [_number(value, "card load") for value in loads]
    if any(value < 0 for value in values):
        raise ValueError("card load must be nonnegative")
    scale = max(values)
    normalized = [value / scale for value in values] if scale else [0.0] * len(values)
    count = len(values)
    total = math.fsum(normalized)
    mean = total / count
    variance = math.fsum((value - mean) ** 2 for value in normalized) / count
    square_sum = math.fsum(value * value for value in normalized)
    p99_load = _nearest_rank(values, 0.99)
    normalized_p99 = p99_load / scale if scale else 0.0

    def ratio(numerator, denominator):
        return {"value": numerator / denominator if denominator else 0.0,
                "numerator": numerator, "denominator": denominator,
                "zero_denominator": denominator == 0}

    lif = ratio(max(normalized), mean)
    max_to_mean = ratio(max(normalized), mean)
    return {
        "card_count": count, "normalization_scale": scale,
        "mean_load": scale * mean, "p99_load": p99_load,
        "zero_load": scale == 0,
        "numerator_denominator_units": "normalized card load; y=x/normalization_scale",
        "cv": ratio(math.sqrt(variance), mean),
        "jfi": ratio(total * total, count * square_sum),
        "lif": lif,
        "p99_to_mean": ratio(normalized_p99, mean),
        "max_to_mean": max_to_mean,
    }


def _aggregate_samples(values: Sequence[int | float]) -> dict:
    """Summarize one metric over time without interpolating tail quantiles."""
    samples = [_number(value) for value in values]
    count = len(samples)
    return {
        "samples": count,
        "mean": math.fsum(samples) / count if count else 0.0,
        "p95": _nearest_rank(samples, 0.95),
        "p99": _nearest_rank(samples, 0.99),
        "max": max(samples, default=0.0),
    }


def _time_card_distribution(tick_loads: Sequence[Sequence[int | float]],
                            card_count: int, padded_zero_ticks: int) -> dict:
    values = [value for tick in tick_loads for value in tick]
    values.extend([0] * (card_count * padded_zero_ticks))
    count = len(values)
    if not count:
        return {"samples": 0, "mean_load": 0.0, "p99_load": 0.0,
                "max_load": 0.0, "p99_to_mean": 0.0, "max_to_mean": 0.0}
    scale = max(values)
    normalized = [value / scale for value in values] if scale else [0.0] * count
    mean = math.fsum(normalized) / count
    normalized_p99 = _nearest_rank(normalized, 0.99)
    return {
        "samples": count,
        "mean_load": scale * mean,
        "p99_load": _nearest_rank(values, 0.99),
        "max_load": scale,
        "p99_to_mean": normalized_p99 / mean if mean else 0.0,
        "max_to_mean": max(normalized) / mean if mean else 0.0,
    }


def _finalize_window(window: dict) -> None:
    """Add cumulative and temporal balance views, then discard raw tick storage."""
    tick_loads = window.pop("_tick_loads")
    padded = window["padded_zero_ticks"]
    window["balance"] = {
        name: balance_metrics([card[name] for card in window["cards"]])
        for name in LOAD_FIELDS
    }
    temporal = {}
    distributions = {}
    for name in LOAD_FIELDS:
        ratio_values = {ratio: [] for ratio in RATIO_FIELDS}
        for loads in tick_loads[name]:
            metrics = balance_metrics(loads)
            for ratio in RATIO_FIELDS:
                ratio_values[ratio].append(metrics[ratio]["value"])
        if padded:
            for values in ratio_values.values():
                values.extend([0.0] * padded)
        temporal[name] = {ratio: _aggregate_samples(values)
                          for ratio, values in ratio_values.items()}
        distributions[name] = _time_card_distribution(
            tick_loads[name], len(window["cards"]), padded)
    window["temporal_hotspots"] = temporal
    window["time_card_distribution"] = distributions


def _sources(output_dir: Path, card_ids: Sequence[int], table: str):
    return [(card_id, output_dir / "cards" / f"card_{card_id}" / f"{table}.csv")
            for card_id in card_ids]


def _common_header(sources, table: str) -> list[str] | None:
    columns = None
    for _, path in sources:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            header = csv.DictReader(handle).fieldnames
        if not header or len(set(header)) != len(header) or "card_id" in header:
            raise ValueError(f"invalid {table} header: {path}")
        if columns is None:
            columns = header
        elif columns != header:
            raise ValueError(f"inconsistent {table} headers: {path}")
    return columns


def _merge_table(output_dir, card_ids, table):
    sources = _sources(output_dir, card_ids, table)
    columns = _common_header(sources, table)
    if columns is None:
        return
    with _atomic_text(output_dir / f"{table}.csv") as handle:
        writer = csv.DictWriter(handle, fieldnames=["card_id", *columns])
        writer.writeheader()
        for card_id, path in sources:
            if not path.exists():
                continue
            with path.open(newline="", encoding="utf-8") as source:
                for row in csv.DictReader(source):
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f"malformed {table} row: {path}")
                    writer.writerow({"card_id": card_id, **row})


def _merge_task_summary(output_dir, card_ids, records):
    sources = _sources(output_dir, card_ids, "task_summary")
    base_fields = _common_header(sources, "task_summary") or ["task_id", "status", *MEASURES]
    fields = ["card_id", *base_fields]
    for name in TASK_LIFECYCLE_FIELDS:
        if name not in fields:
            fields.append(name)
    for name in MEASURES:
        if name not in fields:
            fields.append(name)
    by_task = {}
    for card_id, path in sources:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                task_id = row["task_id"]
                if task_id in by_task:
                    raise ValueError(f"duplicate task_summary task_id: {task_id}")
                by_task[task_id] = {"card_id": card_id, **row}
    if set(by_task).difference(record["task_id"] for record in records):
        raise ValueError("per-card task_summary includes a task absent from task_records")
    with _atomic_text(output_dir / "task_summary.csv") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = {name: 0 for name in MEASURES}
            row.update(by_task.get(record["task_id"], {}))
            if row.get("card_id") is not None and row.get("card_id") != record.get("card_id"):
                raise ValueError(f"task_summary card does not match task_records: {record['task_id']}")
            row.update({name: record[name] for name in TASK_LIFECYCLE_FIELDS if name in record})
            row["card_id"] = record.get("card_id")
            writer.writerow(row)


def _window(start, end, ticks_executed, run_complete, card_ids):
    observed_end = min(end, ticks_executed) if start <= ticks_executed and start <= end else None
    observed_ticks = max(0, observed_end - start + 1) if observed_end is not None else 0
    requested_ticks = max(0, end - start + 1)
    complete = end <= ticks_executed or run_complete
    return {
        "start_tick": start, "end_tick": end, "requested_ticks": requested_ticks,
        "observed_start_tick": start if observed_ticks else None,
        "observed_end_tick": observed_end, "observed_ticks": observed_ticks,
        "complete": complete, "run_complete": run_complete,
        "padded_zero_ticks": requested_ticks - observed_ticks if complete else 0,
        "cards": [{"card_id": card_id, **{field: 0 for field in LOAD_FIELDS}}
                  for card_id in card_ids],
        "_tick_loads": {field: [] for field in LOAD_FIELDS},
    }


def _validate_sliding_widths(widths: Sequence[int]) -> tuple[int, ...]:
    try:
        values = tuple(widths)
    except TypeError as exc:
        raise ValueError("sliding_window_ticks must contain positive integers") from exc
    if not values or any(isinstance(width, bool) or not isinstance(width, int) or width < 1
                         for width in values):
        raise ValueError("sliding_window_ticks must contain positive integers")
    return tuple(sorted(set(values)))


def _write_sliding_balance(output_dir: Path, tick_loads: dict[str, list[list]],
                           widths: Sequence[int]) -> dict:
    """Write full rolling windows and summarize their cross-card imbalance."""
    fields = ("window_ticks", "observed_start_tick", "end_tick", "load",
              *RATIO_FIELDS, "card_loads_json")
    rows = []
    by_width = {}
    ticks = len(tick_loads[SLIDING_LOAD_FIELDS[0]])
    for width in widths:
        samples = max(0, ticks - width + 1)
        by_width[str(width)] = {
            "window_ticks": width, "windows": samples,
            "loads": {name: {ratio: [] for ratio in RATIO_FIELDS}
                      for name in SLIDING_LOAD_FIELDS},
        }
        for name in SLIDING_LOAD_FIELDS:
            per_tick = tick_loads[name]
            card_count = len(per_tick[0]) if per_tick else 0
            rolling = [0] * card_count
            for index, current in enumerate(per_tick):
                for card_index, value in enumerate(current):
                    rolling[card_index] += value
                if index >= width:
                    expired = per_tick[index - width]
                    for card_index, value in enumerate(expired):
                        rolling[card_index] -= value
                        if -1e-9 < rolling[card_index] < 0:
                            rolling[card_index] = 0.0
                if index + 1 < width:
                    continue
                metrics = balance_metrics(rolling)
                row = {
                    "window_ticks": width, "observed_start_tick": index - width + 2,
                    "end_tick": index + 1, "load": name,
                    "card_loads_json": json.dumps(rolling, allow_nan=False,
                                                    separators=(",", ":")),
                }
                for ratio in RATIO_FIELDS:
                    value = metrics[ratio]["value"]
                    row[ratio] = value
                    by_width[str(width)]["loads"][name][ratio].append(value)
                rows.append(row)
        for load in by_width[str(width)]["loads"].values():
            for ratio, values in tuple(load.items()):
                load[ratio] = _aggregate_samples(values)
    with _atomic_text(output_dir / "sliding_balance.csv") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "widths_ticks": list(widths), "full_windows_only": True,
        "csv": "sliding_balance.csv", "rows": len(rows), "by_width": by_width,
    }


def _waiting_deltas(records, ticks_executed):
    pending, placed = {}, {}

    def interval(changes, begin, end):
        begin, end = max(1, begin), min(ticks_executed + 1, end)
        if begin < end:
            changes[begin] = changes.get(begin, 0) + 1
            changes[end] = changes.get(end, 0) - 1

    for record in records:
        if record.get("status") == "unschedulable":
            continue
        arrival = _tick(record.get("arrival_tick"), "arrival_tick") or 1
        placement = _tick(record.get("placement_tick"), "placement_tick")
        start = _tick(record.get("actual_start_tick"), "actual_start_tick")
        interval(pending, arrival, placement or ticks_executed + 1)
        if placement is not None:
            interval(placed, placement, start or ticks_executed + 1)
    return pending, placed


def _task_statistics(records, tasks_total, ticks_executed, run_complete):
    completed = [record for record in records if record.get("status") == "completed"]
    rejected = sum(record.get("status") == "unschedulable" for record in records)
    not_arrived = sum(record.get("status") == "not_arrived" or
                      (_tick(record.get("arrival_tick"), "arrival_tick") or 1) > ticks_executed
                      for record in records)
    completion_ticks = [_tick(record.get("completion_tick"), "completion_tick") for record in completed]
    makespan = max((tick for tick in completion_ticks if tick is not None), default=0)
    timing = {
        "tasks_total": tasks_total, "tasks_completed": len(completed),
        "tasks_rejected": rejected, "tasks_not_arrived": not_arrived,
        "tasks_arrived": tasks_total - not_arrived,
        "tasks_unfinished": tasks_total - len(completed) - rejected - not_arrived,
        "task_throughput_per_tick": len(completed) / ticks_executed if ticks_executed else 0.0,
        "makespan_ticks": makespan, "ticks_executed": ticks_executed,
        "run_complete": run_complete,
        "total_completion_span_ticks": makespan if run_complete and not rejected else None,
        "completed_task_statistics": {},
    }
    for field in TASK_STAT_FIELDS:
        values = sorted(_number(record[field], field) for record in completed
                        if record.get(field) not in (None, ""))
        count = len(values)
        mean = math.fsum(values) / count if count else 0.0
        p95 = values[math.ceil(0.95 * count) - 1] if count else 0.0
        timing["completed_task_statistics"][field] = {
            "samples": count, "mean": mean, "p95": p95,
            "sum": math.fsum(values), "mean_denominator": count,
        }
        timing[f"mean_{field}"] = mean
        timing[f"p95_{field}"] = p95
    return timing


def aggregate_cluster(output_dir: Path, card_ids: Sequence[int], task_records: Sequence[dict],
                      ticks_executed: int, steady_window: tuple[int, int] | None,
                      tasks_total: int, *,
                      sliding_window_ticks: Sequence[int] = (1, 4, 8)) -> dict:
    """Merge finalized card logs and return totals, task timing and balance.

    All cards must provide one card_tick row for every physical Tick, including
    idle time. A full observation window is complete even for a truncated run;
    run_complete independently identifies whether the whole workload ended.
    Future steady-window ticks are padded with zero only after natural end.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    card_ids, records = tuple(card_ids), list(task_records)
    sliding_window_ticks = _validate_sliding_widths(sliding_window_ticks)
    if not card_ids or len(set(card_ids)) != len(card_ids) or any(
            isinstance(card_id, bool) or not isinstance(card_id, int) or card_id < 0 for card_id in card_ids):
        raise ValueError("card_ids must contain distinct nonnegative integer IDs")
    if isinstance(ticks_executed, bool) or not isinstance(ticks_executed, int) or ticks_executed < 0:
        raise ValueError("ticks_executed must be a nonnegative integer")
    if tasks_total != len(records) or len({record["task_id"] for record in records}) != len(records):
        raise ValueError("task_records must contain every task exactly once")
    if steady_window is not None:
        if len(steady_window) != 2 or any(isinstance(tick, bool) or not isinstance(tick, int)
                                            for tick in steady_window) or not 1 <= steady_window[0] <= steady_window[1]:
            raise ValueError("steady_window must be a pre-fixed inclusive positive Tick interval")
    run_complete = all(record.get("status") in TERMINAL_STATUSES for record in records)
    windows = {"full": _window(1, ticks_executed, ticks_executed, run_complete, card_ids)}
    if steady_window is not None:
        windows["steady"] = _window(*steady_window, ticks_executed, run_complete, card_ids)
    card_sources = _sources(output_dir, card_ids, "card_tick")
    if any(not path.exists() for _, path in card_sources):
        raise ValueError("every card must provide card_tick.csv including idle cards")
    header = _common_header(card_sources, "card_tick")
    if not set(("physical_tick", *MEASURES)).issubset(header):
        raise ValueError("card_tick header must include physical_tick and all MEASURES")
    pending_delta, placed_delta = _waiting_deltas(records, ticks_executed)
    totals = {name: 0 for name in MEASURES}
    timing = _task_statistics(records, tasks_total, ticks_executed, run_complete)
    timing.update(card_communication_extension_ticks=0, card_barrier_blocked_ticks=0,
                  card_rounds_started=0, active_card_ticks=0)
    cluster_fields = ("physical_tick", *MEASURES, "endpoint_events",
                      "offered_endpoint_events", "active_tasks",
                      "waiting_tasks", "placed_waiting_tasks", "pending_tasks", "completed_tasks")
    full_tick_loads = {field: [] for field in LOAD_FIELDS}
    pending_count = placed_count = 0
    with ExitStack() as stack:
        readers = [csv.DictReader(stack.enter_context(path.open(newline="", encoding="utf-8")))
                   for _, path in card_sources]
        handle = stack.enter_context(_atomic_text(output_dir / "cluster_tick.csv"))
        writer = csv.DictWriter(handle, fieldnames=cluster_fields)
        writer.writeheader()
        for tick in range(1, ticks_executed + 1):
            pending_count += pending_delta.get(tick, 0)
            placed_count += placed_delta.get(tick, 0)
            combined = {"physical_tick": tick, **{name: 0 for name in MEASURES},
                        "active_tasks": 0, "waiting_tasks": 0, "completed_tasks": 0,
                        "placed_waiting_tasks": placed_count, "pending_tasks": pending_count}
            tick_by_field = {field: [] for field in LOAD_FIELDS}
            for index, reader in enumerate(readers):
                row = next(reader, None)
                if row is None or _number(row.get("physical_tick"), "physical_tick") != tick:
                    raise ValueError(f"card {card_ids[index]} must provide exactly one row for Tick {tick}")
                for name in MEASURES:
                    combined[name] += _number(row[name], name)
                for name in ("active_tasks", "waiting_tasks", "completed_tasks"):
                    combined[name] += _number(row.get(name), name)
                for field, column in (("card_communication_extension_ticks", "communication_extension_tick"),
                                      ("card_rounds_started", "round_started")):
                    timing[field] += int(str(row.get(column, "")).lower() in ("true", "1"))
                timing["card_barrier_blocked_ticks"] += int(str(row.get("barrier_ready", "")).lower() in ("false", "0"))
                timing["active_card_ticks"] += int(_number(row.get("active_tasks")) > 0)
                row_loads = {
                    "compute_sops": _number(row["compute_sops"], "compute_sops"),
                    "tx_injected": _number(row["tx_injected"], "tx_injected"),
                    "rx_ejected": _number(row["rx_ejected"], "rx_ejected"),
                    "endpoint_events": (_number(row["tx_injected"], "tx_injected")
                                        + _number(row["rx_ejected"], "rx_ejected")),
                    "offered_endpoint_events": _offered_endpoint_events(row["generated_tx"]),
                }
                for name, value in row_loads.items():
                    tick_by_field[name].append(value)
                for window in windows.values():
                    if window["start_tick"] <= tick <= window["end_tick"]:
                        loads = window["cards"][index]
                        for name, value in row_loads.items():
                            loads[name] += value
            combined["endpoint_events"] = combined["tx_injected"] + combined["rx_ejected"]
            combined["offered_endpoint_events"] = _offered_endpoint_events(
                combined["generated_tx"])
            for name, loads in tick_by_field.items():
                full_tick_loads[name].append(loads)
                for window in windows.values():
                    if window["start_tick"] <= tick <= window["end_tick"]:
                        window["_tick_loads"][name].append(loads)
            for name in MEASURES:
                totals[name] = combined[name] if name in GAUGES else totals[name] + combined[name]
            writer.writerow(combined)
        if any(next(reader, None) is not None for reader in readers):
            raise ValueError("card_tick contains rows after ticks_executed")
    for window in windows.values():
        _finalize_window(window)
    sliding = _write_sliding_balance(output_dir, full_tick_loads, sliding_window_ticks)
    for table in MERGED_TABLES:
        _merge_table(output_dir, card_ids, table)
    _merge_task_summary(output_dir, card_ids, records)
    with _atomic_text(output_dir / "balance_windows.json") as handle:
        json.dump(windows, handle, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")
    conservation = {
        "generated_minus_tx_minus_source": totals["generated_tx"] - totals["tx_injected"] - totals["pending_tx"],
        "tx_minus_rx_minus_network": totals["tx_injected"] - totals["rx_ejected"] - totals["in_network"],
        "rx_minus_consumed_minus_sink": totals["rx_ejected"] - totals["rx_consumed"] - totals["sink_unconsumed"],
        "pending_rx_minus_source_minus_network": totals["pending_rx"] - totals["pending_tx"] - totals["in_network"],
    }
    conservation["valid"] = all(value == 0 for value in conservation.values())
    return {"totals": totals, "derived": SimulationMetrics.derived(totals),
            "timing": timing, "conservation": conservation, "balance_windows": windows,
            "sliding_windows": sliding}
