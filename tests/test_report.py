"""Focused checks for portable report rendering and sparse, zero-valued runs."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET

from simulation.report import write_report


MEASURES = ("compute_sops", "generated_tx", "tx_injected", "rx_ejected",
            "tx_stall_cycles", "router_wait_flit_cycles")


def _csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)


def _artifacts(tmp_path, manifest, *, core=(), task=(), card=(), queues=()):
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _csv(tmp_path / "core_tick.csv",
         ("task_id", "physical_tick", "logical_tick", "population_id", "core_id", *MEASURES), core)
    _csv(tmp_path / "task_tick.csv", ("task_id", "physical_tick", "logical_tick", *MEASURES), task)
    _csv(tmp_path / "card_tick.csv", ("physical_tick", *MEASURES), card)
    _csv(tmp_path / "step_timing.csv", ("task_id", "logical_tick"), [])
    _csv(tmp_path / "queue_stats.csv",
         ("physical_tick", "kind", "router", "port", "occupancy_peak", "occupancy_sum",
          "samples", "full_cycles", "capacity"), queues)


def test_sparse_report_renders_per_task_populations_and_escapes_user_text(tmp_path):
    task_id = '<script>alert("x")</script>'
    scenario = '<img src=x onerror="bad()">'
    _artifacts(
        tmp_path,
        {"status": "completed", "scenario": scenario,
         "elapsed_seconds": 0.125, "ticks_executed": 4, "totals": {},
         "tasks": [{"task_id": task_id, "start_tick": 2, "completion_tick": 4,
                    "population_count": 3, "mapping": [0, 1, 2]}]},
        core=[
            {"task_id": task_id, "physical_tick": 2, "logical_tick": 0,
             "population_id": 0, "core_id": 0, "compute_sops": 10,
             "generated_tx": 2, "tx_injected": 2, "rx_ejected": 0},
            {"task_id": task_id, "physical_tick": 3, "logical_tick": 0,
             "population_id": 1, "core_id": 1, "compute_sops": 0,
             "generated_tx": 0, "tx_injected": 0, "rx_ejected": 2},
        ],
        task=[
            {"task_id": task_id, "physical_tick": 2, "logical_tick": 0,
             "compute_sops": 10, "generated_tx": 2, "tx_injected": 2, "rx_ejected": 0},
            {"task_id": task_id, "physical_tick": 3, "logical_tick": 1,
             "compute_sops": 0, "generated_tx": 0, "tx_injected": 0, "rx_ejected": 2},
        ],
        card=[
            {"physical_tick": 2, "compute_sops": 10, "generated_tx": 2,
             "tx_injected": 2, "rx_ejected": 0},
            {"physical_tick": 3, "compute_sops": 0, "generated_tx": 0,
             "tx_injected": 0, "rx_ejected": 2},
        ],
        queues=[
            {"physical_tick": 2, "kind": '<svg onload="bad()">', "router": 0,
             "port": "Local", "occupancy_peak": 2, "occupancy_sum": 4,
             "samples": 2, "full_cycles": 1, "capacity": 2},
        ],
    )
    path = write_report(tmp_path)
    document = path.read_text(encoding="utf-8")
    artwork = (tmp_path / "workload.svg").read_text(encoding="utf-8")

    assert path == tmp_path / "report.html"
    assert "<!doctype html>" in document
    assert "任务逐 tick 曲线" in document
    assert "MicroPopulation 热图" in document
    assert "总计算 10 SOP；产生 2 flit；Tx 2；Rx 2" in document
    assert "MicroPopulation 2（核 2）" in document  # sparse zero population still shown
    assert "物理 tick 1；" in document  # no core row at tick 1, filled with zero
    assert "物理 tick 4；" in document
    assert "平均占用" in document and "满队列周期" in document
    assert "源核注入停滞周期" in document and "目标核接收阻塞周期" in document
    assert "路由器队头等待 flit-cycle" in document
    assert '&lt;script&gt;alert(&quot;' in document
    assert '&lt;img src=x onerror=&quot;' in document
    assert '&lt;svg onload=&quot;' in document
    assert "<script>" not in document
    assert "<img src=x" not in document
    assert "<svg onload=" not in document
    assert "<script>" not in artwork and "<img src=x" not in artwork
    assert ET.fromstring(artwork).tag.endswith("svg")
    assert "http://" not in document.replace("http://www.w3.org/2000/svg", "")


def test_all_zero_silent_task_renders_without_division_by_zero(tmp_path):
    _artifacts(
        tmp_path,
        {"status": "completed", "scenario": "silent", "ticks_executed": 3,
         "tasks": [{"task_id": "zero", "start_tick": 1, "completion_tick": 3,
                    "population_count": 2, "mapping": [0, 1]}]},
        task=[{"task_id": "zero", "physical_tick": tick, "logical_tick": tick - 1,
               **{metric: 0 for metric in MEASURES}} for tick in range(1, 4)],
        card=[{"physical_tick": tick, **{metric: 0 for metric in MEASURES}}
              for tick in range(1, 4)],
    )
    document = write_report(tmp_path).read_text(encoding="utf-8")
    artwork = (tmp_path / "workload.svg").read_text(encoding="utf-8")
    assert "总计算 0 SOP；产生 0 flit；Tx 0；Rx 0" in document
    assert "MicroPopulation 0（核 0）" in document and "MicroPopulation 1（核 1）" in document
    assert "0 → 0" in document
    assert "队列统计无活动行" in document
    assert ET.fromstring(artwork).tag.endswith("svg")


def test_truncated_run_limits_zero_fill_to_executed_window(tmp_path):
    _artifacts(
        tmp_path,
        {"status": "max_ticks", "max_ticks": 1, "ticks_executed": 1,
         "scenario": "deadline", "tasks": [{"task_id": "late", "start_tick": 1,
                                           "completion_tick": 20, "population_count": 1,
                                           "mapping": [0]}]},
    )
    document = write_report(tmp_path).read_text(encoding="utf-8")
    assert "物理 tick：1–1" in document
    assert "通信延长：0 Tick" in document
    assert "达到最大物理 tick" in document
    assert "物理 tick 20；" not in document


def test_task_aggregates_keep_delayed_rx_at_physical_tick_and_queue_mean_includes_zeros(tmp_path):
    _artifacts(
        tmp_path,
        {"status": "completed", "scenario": "aggregation", "ticks_executed": 2,
         "physical_tick_period_cycles": 2, "config": {"mesh_x": 2, "mesh_y": 1},
         "tasks": [{"task_id": "a", "start_tick": 1, "completion_tick": 2,
                    "population_count": 2, "mapping": [0, 1]},
                   {"task_id": "b", "start_tick": 1, "completion_tick": 2,
                    "population_count": 2, "mapping": [0, 1]}]},
        core=[
            {"task_id": "a", "physical_tick": 1, "logical_tick": 0,
             "population_id": 0, "core_id": 0, "compute_sops": 3,
             "generated_tx": 1, "tx_injected": 1, "rx_ejected": 0},
            {"task_id": "a", "physical_tick": 2, "logical_tick": 0,
             "population_id": 1, "core_id": 1, "compute_sops": 0,
             "generated_tx": 0, "tx_injected": 0, "rx_ejected": 1},
            {"task_id": "b", "physical_tick": 2, "logical_tick": 1,
             "population_id": 1, "core_id": 1, "compute_sops": 7,
             "generated_tx": 0, "tx_injected": 0, "rx_ejected": 0},
        ],
        task=[
            {"task_id": "a", "physical_tick": 1, "logical_tick": 0,
             "compute_sops": 3, "generated_tx": 1, "tx_injected": 1, "rx_ejected": 0},
            {"task_id": "a", "physical_tick": 2, "logical_tick": 0,
             "compute_sops": 0, "generated_tx": 0, "tx_injected": 0, "rx_ejected": 1},
            {"task_id": "b", "physical_tick": 2, "logical_tick": 1,
             "compute_sops": 7, "generated_tx": 0, "tx_injected": 0, "rx_ejected": 0},
        ],
        queues=[{"physical_tick": 1, "kind": "source_ni", "router": 0,
                 "port": "Local", "occupancy_peak": 2, "occupancy_sum": 4,
                 "samples": 2, "full_cycles": 1, "capacity": 2}],
    )
    document = write_report(tmp_path).read_text(encoding="utf-8")
    assert "任务计算与通信负载对比" in document
    assert "任务 a" in document and "任务 b" in document
    assert "总计算 3 SOP；产生 1 flit；Tx 1；Rx 1" in document
    assert "总计算 7 SOP；产生 0 flit；Tx 0；Rx 0" in document
    assert "平均占用</th>" in document
    assert "<td>0.5</td>" in document  # 4 occupied slots / (2 cores * 2 cycles * 2 ticks)
    assert "物理 tick 2；rx_ejected 1" in document


def test_router_wait_is_visible_with_zero_source_stall(tmp_path):
    _artifacts(
        tmp_path,
        {"status": "completed", "scenario": "shared-link", "ticks_executed": 1,
         "tasks": [{"task_id": "a", "start_tick": 1, "completion_tick": 1,
                    "population_count": 2, "mapping": [0, 1]}]},
        core=[{"task_id": "a", "physical_tick": 1, "logical_tick": 0,
               "population_id": 0, "core_id": 0, "router_wait_flit_cycles": 7,
               "tx_stall_cycles": 0}],
        task=[{"task_id": "a", "physical_tick": 1, "logical_tick": 0,
               "router_wait_flit_cycles": 7, "tx_stall_cycles": 0}],
        card=[{"physical_tick": 1, "router_wait_flit_cycles": 7,
               "tx_stall_cycles": 0}],
    )
    document = write_report(tmp_path).read_text(encoding="utf-8")
    assert '<tr><td>路由器队头等待 flit-cycle</td><td>7</td></tr>' in document
    assert '<tr><td>源核注入停滞周期</td><td>0</td></tr>' in document
    assert '路由器队头等待 flit-cycle：7' in document
    assert '物理 tick 1；router_wait_flit_cycles 7' in document
