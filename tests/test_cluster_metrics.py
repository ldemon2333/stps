import csv
import json
import math
from pathlib import Path

import pytest

from simulation.cluster_metrics import aggregate_cluster, balance_metrics
from util.metrics import MEASURES


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def card(path, card_id, updates, task_rows=()):
    directory = path / "cards" / f"card_{card_id}"
    fields = ["physical_tick", *MEASURES, "active_tasks", "waiting_tasks", "completed_tasks",
              "barrier_ready", "round_started", "communication_extension_tick"]
    rows = [{"physical_tick": tick, **{name: 0 for name in MEASURES},
             "active_tasks": 0, "waiting_tasks": 0, "completed_tasks": 0,
             "barrier_ready": True, "round_started": False,
             "communication_extension_tick": False, **update}
            for tick, update in enumerate(updates, 1)]
    write_csv(directory / "card_tick.csv", fields, rows)
    summary_fields = ["task_id", "status", "actual_start_tick", "completion_tick",
                      *MEASURES, "rx_latency_p95_cycles"]
    write_csv(directory / "task_summary.csv", summary_fields, task_rows)
    return directory


def task(task_id, card_id=0, status="completed", **updates):
    return {"task_id": task_id, "card_id": card_id, "status": status,
            "arrival_tick": 1, "placement_tick": 1, "actual_start_tick": 1,
            "completion_tick": 1, "task_execution_ticks": 1,
            "task_end_to_end_ticks": 1, "resource_wait_ticks": 0,
            "boundary_wait_ticks": 0, "communication_extension_ticks": 0,
            "slowdown": 1.0, **updates}


def test_idle_cards_included_and_gauges_use_last_snapshot(tmp_path):
    card(tmp_path, 0, [
        {"compute_sops": 12, "generated_tx": 3, "expected_tx": 3, "expected_rx": 3,
         "tx_injected": 2, "rx_ejected": 1, "rx_consumed": 1,
         "pending_tx": 1, "in_network": 1, "pending_rx": 2,
         "source_wait_cycles_sum": 6, "rx_latency_cycles_sum": 5,
         "rx_excess_latency_cycles_sum": 2, "router_wait_flit_cycles": 2,
         "active_tasks": 1, "barrier_ready": False, "round_started": True},
        {"tx_injected": 1, "rx_ejected": 2, "rx_consumed": 1,
         "sink_unconsumed": 1, "source_wait_cycles_sum": 4,
         "rx_latency_cycles_sum": 15, "rx_excess_latency_cycles_sum": 8,
         "router_wait_flit_cycles": 1, "active_tasks": 1, "completed_tasks": 1,
         "communication_extension_tick": True},
    ])
    for card_id in (1, 2, 3):
        card(tmp_path, card_id, [{}, {}])
    summary = aggregate_cluster(tmp_path, range(4), [task("a", completion_tick=2,
        task_execution_ticks=2, task_end_to_end_ticks=2, communication_extension_ticks=1)],
        2, (5, 40), 1)
    totals = summary["totals"]
    assert totals["generated_tx"] == totals["tx_injected"] == totals["rx_ejected"] == 3
    assert totals["pending_tx"] == totals["in_network"] == totals["pending_rx"] == 0
    assert totals["sink_unconsumed"] == 1
    assert summary["conservation"]["valid"]
    assert summary["derived"]["mean_source_wait_cycles"] == pytest.approx(10 / 3)
    assert summary["derived"]["mean_rx_latency_cycles"] == pytest.approx(20 / 3)
    assert summary["derived"]["mean_rx_excess_latency_cycles"] == pytest.approx(10 / 3)
    assert summary["derived"]["router_wait_per_generated_flit"] == 1
    full = summary["balance_windows"]["full"]
    assert len(full["cards"]) == 4
    assert full["cards"][0]["endpoint_events"] == 6
    balance = full["balance"]["compute_sops"]
    assert balance["cv"]["value"] == pytest.approx(math.sqrt(3))
    assert balance["jfi"]["value"] == 0.25
    assert balance["lif"]["value"] == 4
    for metric in ("cv", "jfi", "lif"):
        entry = balance[metric]
        assert entry["value"] == entry["numerator"] / entry["denominator"]
        assert not entry["zero_denominator"]
    rows = read_csv(tmp_path / "cluster_tick.csv")
    assert [int(row["endpoint_events"]) for row in rows] == [3, 3]
    assert rows[1]["sink_unconsumed"] == "1"
    assert summary["timing"]["card_barrier_blocked_ticks"] == 1
    assert summary["timing"]["card_communication_extension_ticks"] == 1
    assert summary["timing"]["card_rounds_started"] == 1


def test_natural_end_pads_steady_window_zero_and_keeps_numerators(tmp_path):
    for card_id in range(2):
        card(tmp_path, card_id, [{"compute_sops": card_id + 1}, {}])
    summary = aggregate_cluster(tmp_path, [0, 1], [task("a")], 2, (5, 40), 1)
    steady = summary["balance_windows"]["steady"]
    assert steady["complete"] and steady["run_complete"]
    assert steady["observed_end_tick"] is None
    assert steady["observed_ticks"] == 0 and steady["padded_zero_ticks"] == 36
    for name in ("compute_sops", "tx_injected", "rx_ejected", "endpoint_events"):
        balance = steady["balance"][name]
        assert balance["zero_load"] and balance["normalization_scale"] == 0
        for metric in ("cv", "jfi", "lif"):
            assert balance[metric] == {"value": 0.0, "numerator": 0.0,
                                        "denominator": 0.0, "zero_denominator": True}
        assert all(stat["samples"] == 36 and stat["max"] == 0
                   for stat in steady["temporal_hotspots"][name].values())
        assert steady["time_card_distribution"][name]["samples"] == 72
    saved = json.loads((tmp_path / "balance_windows.json").read_text())
    assert saved == summary["balance_windows"]


@pytest.mark.parametrize("window,observed_end,observed_ticks", [((2, 4), 3, 2), ((5, 40), None, 0)])
def test_truncated_future_window_is_incomplete_without_padding(tmp_path, window, observed_end, observed_ticks):
    card(tmp_path, 0, [{}, {}, {}])
    record = task("a", status="unfinished", completion_tick=None, task_execution_ticks=None,
                  task_end_to_end_ticks=None, slowdown=None)
    summary = aggregate_cluster(tmp_path, [0], [record], 3, window, 1)
    full = summary["balance_windows"]["full"]
    assert full["complete"] and not full["run_complete"]
    steady = summary["balance_windows"]["steady"]
    assert not steady["complete"] and not steady["run_complete"]
    assert steady["observed_end_tick"] == observed_end
    assert steady["observed_ticks"] == observed_ticks
    assert steady["padded_zero_ticks"] == 0
    assert steady["temporal_hotspots"]["compute_sops"]["lif"]["samples"] == observed_ticks
    assert steady["time_card_distribution"]["compute_sops"]["samples"] == observed_ticks
    assert summary["timing"]["total_completion_span_ticks"] is None
    assert summary["timing"]["tasks_unfinished"] == 1


def test_future_not_arrived_tasks_prevent_zero_padding(tmp_path):
    card(tmp_path, 0, [{}, {}])
    records = [task("a"), task("future", None, "not_arrived", arrival_tick=10,
               placement_tick=None, actual_start_tick=None, completion_tick=None,
               task_execution_ticks=None, task_end_to_end_ticks=None, slowdown=None)]
    summary = aggregate_cluster(tmp_path, [0], records, 2, (5, 40), 2)
    assert not summary["balance_windows"]["steady"]["complete"]
    assert summary["timing"]["tasks_not_arrived"] == 1
    assert summary["timing"]["tasks_unfinished"] == 0


def test_merge_preserves_header_fields_and_lifecycle_including_unplaced(tmp_path):
    card0 = card(tmp_path, 0, [{}, {}, {}], [{"task_id": "a", "status": "completed",
        "actual_start_tick": 2, "completion_tick": 3, "compute_sops": 123,
        "rx_latency_p95_cycles": 77}])
    card1 = card(tmp_path, 1, [{}, {}, {}])
    fields = ["kind", "task_id", "count", "reason"]
    write_csv(card0 / "events.csv", fields, [{"kind": "rx", "task_id": "a", "count": 4,
                                               "reason": ""}])
    write_csv(card1 / "events.csv", fields, [{"kind": "link", "task_id": "b", "count": 2,
                                               "reason": "arbitration"}])
    records = [task("a", arrival_tick=1, placement_tick=2, actual_start_tick=3,
        completion_tick=3, resource_wait_ticks=1, boundary_wait_ticks=1,
        task_end_to_end_ticks=3),
        task("pending", None, "pending", arrival_tick=2, placement_tick=None,
             actual_start_tick=None, completion_tick=None, task_execution_ticks=None,
             task_end_to_end_ticks=None, population_count=2, logical_ticks=5),
        task("bad", None, "unschedulable", placement_tick=None, actual_start_tick=None,
             completion_tick=None, task_execution_ticks=None, task_end_to_end_ticks=None,
             rejection_reason="too large")]
    summary = aggregate_cluster(tmp_path, [0, 1], records, 3, None, 3)
    with (tmp_path / "events.csv").open() as handle:
        assert next(csv.reader(handle)) == ["card_id", *fields]
    events = read_csv(tmp_path / "events.csv")
    assert [row["card_id"] for row in events] == ["0", "1"]
    assert events[1]["reason"] == "arbitration"
    tasks = {row["task_id"]: row for row in read_csv(tmp_path / "task_summary.csv")}
    assert tasks["a"]["actual_start_tick"] == "3"
    assert tasks["a"]["compute_sops"] == "123"
    assert tasks["a"]["rx_latency_p95_cycles"] == "77"
    assert tasks["a"]["resource_wait_ticks"] == "1"
    assert tasks["pending"]["card_id"] == tasks["pending"]["actual_start_tick"] == ""
    assert tasks["pending"]["population_count"] == "2"
    assert tasks["pending"]["logical_ticks"] == "5"
    assert tasks["bad"]["status"] == "unschedulable"
    assert tasks["bad"]["rejection_reason"] == "too large"
    assert all(field in tasks["bad"] and tasks["bad"][field] == "0" for field in MEASURES)
    rows = read_csv(tmp_path / "cluster_tick.csv")
    assert [row["pending_tasks"] for row in rows] == ["1", "1", "1"]
    assert [row["placed_waiting_tasks"] for row in rows] == ["0", "1", "0"]
    assert summary["timing"]["tasks_rejected"] == 1
    assert summary["timing"]["tasks_unfinished"] == 1


def test_completed_task_statistics_use_samples_not_card_percentiles(tmp_path):
    card(tmp_path, 0, [{}] * 20, [{"task_id": "a", "status": "completed",
                                   "rx_latency_p95_cycles": 900}])
    records = [task("a", task_execution_ticks=1, task_end_to_end_ticks=1)]
    records.extend(task(f"task_{i}", task_execution_ticks=i, task_end_to_end_ticks=i,
                        completion_tick=i) for i in range(2, 21))
    records.append(task("notdone", None, "pending", placement_tick=None, actual_start_tick=None,
                        completion_tick=None, task_execution_ticks=None, task_end_to_end_ticks=None))
    summary = aggregate_cluster(tmp_path, [0], records, 20, None, len(records))
    timing = summary["timing"]
    assert timing["tasks_completed"] == 20 and timing["tasks_unfinished"] == 1
    assert timing["mean_task_execution_ticks"] == 10.5
    assert timing["p95_task_execution_ticks"] == 19
    assert timing["completed_task_statistics"]["task_execution_ticks"]["samples"] == 20
    assert timing["makespan_ticks"] == 20
    assert timing["total_completion_span_ticks"] is None
    assert "rx_latency_p95_cycles" not in summary["derived"]


def test_balance_is_stable_for_large_finite_load_and_includes_zeros():
    metrics = balance_metrics([1e300, 1e300, 0, 0])
    assert metrics["cv"]["value"] == 1
    assert metrics["jfi"]["value"] == 0.5
    assert metrics["lif"]["value"] == 2
    assert metrics["p99_load"] == 1e300
    assert metrics["p99_to_mean"]["value"] == 2
    assert metrics["max_to_mean"] == metrics["lif"]
    assert metrics["max_to_mean"] is not metrics["lif"]
    assert metrics["mean_load"] == 5e299
    json.dumps(metrics, allow_nan=False)
    with pytest.raises(ValueError, match="finite"):
        balance_metrics([math.inf])
    with pytest.raises(ValueError, match="nonnegative"):
        balance_metrics([-1, 2])


def test_temporal_hotspot_detects_rotating_straggler_hidden_by_cumulative_balance(tmp_path):
    peak = 8
    for card_id in range(4):
        updates = [{"compute_sops": peak if tick == card_id else 0}
                   for tick in range(4)]
        updates.append({})
        card(tmp_path, card_id, updates)
    summary = aggregate_cluster(tmp_path, range(4), [task("a", completion_tick=5)],
                                5, None, 1, sliding_window_ticks=(1, 2))
    full = summary["balance_windows"]["full"]
    cumulative = full["balance"]["compute_sops"]
    assert cumulative["cv"]["value"] == 0
    assert cumulative["jfi"]["value"] == 1
    assert cumulative["lif"]["value"] == 1
    assert cumulative["p99_load"] == peak

    temporal = full["temporal_hotspots"]["compute_sops"]
    for ratio in ("lif", "p99_to_mean", "max_to_mean"):
        assert temporal[ratio] == {"samples": 5, "mean": 3.2,
                                   "p95": 4, "p99": 4, "max": 4}
    assert temporal["cv"]["samples"] == 5
    assert temporal["cv"]["mean"] == pytest.approx(4 * math.sqrt(3) / 5)
    assert temporal["jfi"]["mean"] == pytest.approx(0.2)

    distribution = full["time_card_distribution"]["compute_sops"]
    assert distribution == {"samples": 20, "mean_load": pytest.approx(1.6),
                            "p99_load": peak, "max_load": peak,
                            "p99_to_mean": pytest.approx(5),
                            "max_to_mean": pytest.approx(5)}


def test_offered_endpoint_load_is_distinct_from_served_endpoint_load(tmp_path):
    card(tmp_path, 0, [{"generated_tx": 5, "pending_tx": 5, "pending_rx": 5}])
    for card_id in range(1, 4):
        card(tmp_path, card_id, [{}])
    summary = aggregate_cluster(tmp_path, range(4), [task("a")], 1, None, 1,
                                sliding_window_ticks=(1,))
    full = summary["balance_windows"]["full"]
    assert full["cards"][0]["offered_endpoint_events"] == 10
    assert full["cards"][0]["endpoint_events"] == 0
    assert full["balance"]["offered_endpoint_events"]["lif"]["value"] == 4
    assert full["balance"]["endpoint_events"]["zero_load"]
    assert full["temporal_hotspots"]["offered_endpoint_events"]["p99_to_mean"]["max"] == 4
    cluster_row = read_csv(tmp_path / "cluster_tick.csv")[0]
    assert cluster_row["offered_endpoint_events"] == "10"
    assert cluster_row["endpoint_events"] == "0"
    assert summary["conservation"]["valid"]


def test_sliding_windows_use_full_width_and_smooth_swapped_hotspots(tmp_path):
    card(tmp_path, 0, [{"compute_sops": 8}, {}])
    card(tmp_path, 1, [{}, {"compute_sops": 8}])
    for card_id in (2, 3):
        card(tmp_path, card_id, [{}, {}])
    summary = aggregate_cluster(tmp_path, range(4), [task("a", completion_tick=2)],
                                2, None, 1, sliding_window_ticks=(2, 1, 2))
    sliding = summary["sliding_windows"]
    assert sliding["widths_ticks"] == [1, 2]
    assert sliding["full_windows_only"]
    assert sliding["by_width"]["1"]["loads"]["compute_sops"]["lif"]["mean"] == 4
    width_two = sliding["by_width"]["2"]["loads"]["compute_sops"]
    assert width_two["lif"] == {"samples": 1, "mean": 2.0,
                                   "p95": 2.0, "p99": 2.0, "max": 2.0}
    rows = read_csv(tmp_path / "sliding_balance.csv")
    compute_two = [row for row in rows if row["load"] == "compute_sops"
                   and row["window_ticks"] == "2"]
    assert len(compute_two) == 1
    assert compute_two[0]["observed_start_tick"] == "1"
    assert compute_two[0]["end_tick"] == "2"
    assert json.loads(compute_two[0]["card_loads_json"]) == [8, 8, 0, 0]
    assert float(compute_two[0]["max_to_mean"]) == 2


def test_sliding_float_roundoff_does_not_create_negative_load(tmp_path):
    card(tmp_path, 0, [{"compute_sops": 0.1}, {"compute_sops": 0.2},
                       {"compute_sops": 0.3}, {"compute_sops": 0.0}])
    card(tmp_path, 1, [{}, {}, {}, {}])
    summary = aggregate_cluster(tmp_path, [0, 1], [task("a")], 4, None, 1,
                                sliding_window_ticks=(1, 3))
    assert summary["sliding_windows"]["by_width"]["1"]["windows"] == 4
    rows = read_csv(tmp_path / "sliding_balance.csv")
    assert all(float(row["cv"]) >= 0 for row in rows)


def test_header_mismatch_does_not_publish_partial_merged_table(tmp_path):
    first = card(tmp_path, 0, [{}])
    second = card(tmp_path, 1, [{}])
    write_csv(first / "events.csv", ["kind", "count"], [{"kind": "rx", "count": 1}])
    write_csv(second / "events.csv", ["count", "kind"], [{"kind": "rx", "count": 2}])
    with pytest.raises(ValueError, match="inconsistent events headers"):
        aggregate_cluster(tmp_path, [0, 1], [task("a")], 1, None, 1)
    assert not (tmp_path / "events.csv").exists()
    assert not list(tmp_path.glob(".events.csv.*"))


@pytest.mark.parametrize("updates,ticks", [([{"physical_tick": 2}], 1), ([{}], 2), ([{}, {}], 1)])
def test_card_tick_requires_every_card_on_same_contiguous_clock(tmp_path, updates, ticks):
    card(tmp_path, 0, updates)
    with pytest.raises(ValueError, match="Tick|after ticks_executed"):
        aggregate_cluster(tmp_path, [0], [task("a")], ticks, None, 1)
    assert not (tmp_path / "cluster_tick.csv").exists()


def test_natural_end_with_rejections_is_terminal_but_no_whole_completion_span(tmp_path):
    card(tmp_path, 0, [{}])
    records = [task("bad", None, "unschedulable", placement_tick=None, actual_start_tick=None,
                    completion_tick=None, task_execution_ticks=None, task_end_to_end_ticks=None)]
    summary = aggregate_cluster(tmp_path, [0], records, 1, (1, 3), 1)
    assert summary["balance_windows"]["steady"]["complete"]
    assert summary["balance_windows"]["steady"]["padded_zero_ticks"] == 2
    assert summary["timing"]["tasks_rejected"] == 1
    assert summary["timing"]["total_completion_span_ticks"] is None
