import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest

from fingerprint.workload import Workload, save_workload
from simulation.engine import run_simulation
from simulation.scenario import load_scenario
from util.metrics import MEASURES

ROOT = Path(__file__).resolve().parents[1]


def scene(tmp_path, traffic=((1,), (0,)), compute=((10, 0), (0, 20)), cycles=3,
          sink_period=1, mapping=(0, 1)):
    w = Workload([4, 4], [0], [1], traffic, compute, 1.0, "test:explicit")
    save_workload(tmp_path / "w.json", w)
    doc = {"schema_version": 1, "name": "test", "card": {
        "mesh_x": 2, "mesh_y": 1, "neurons_per_core": 4, "memory_mb": 4},
        "noc": {"cycles_per_tick": cycles, "sink_service_period": sink_period},
        "tasks": [{"task_id": "a", "workload": "w.json", "start_tick": 1, "mapping": list(mapping)}]}
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(doc))
    return path, doc


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def test_exact_boundary_rx_not_consumption(tmp_path):
    path, _ = scene(tmp_path, cycles=3, traffic=((1,),), compute=((10, 0),), sink_period=99)
    result = run_simulation(path, tmp_path / "out", trace=True, report=False)
    assert result.status == "completed"
    assert result.summary["totals"]["rx_ejected"] == 1
    assert result.summary["totals"]["rx_consumed"] == 0
    assert result.summary["totals"]["sink_unconsumed"] == 1
    assert result.summary["cycles_executed"] == 3
    assert result.summary["peak_inventory_flits"]["sink_ni"] == 1
    sink = [r for r in rows(tmp_path / "out/queue_stats.csv") if r["kind"] == "sink_ni"]
    assert len(sink) == 1 and int(sink[0]["occupancy_peak"]) == 1
    assert int(sink[0]["samples"]) == 3 and int(sink[0]["occupancy_sum"]) == 0
    events = rows(tmp_path / "out/events.csv")
    assert [(x["kind"], int(x["cycle"])) for x in events if x["kind"] in ("tx", "link", "rx")] == [
        ("tx", 1), ("link", 2), ("rx", 3)]


def test_cross_tick_delivery_extends_step_without_repeating_work(tmp_path):
    path, _ = scene(tmp_path, cycles=2)
    result = run_simulation(path, tmp_path / "out", trace=True, report=False)
    assert result.status == "completed" and result.ticks_executed == 3
    assert result.summary["totals"]["compute_sops"] == 30
    assert result.summary["totals"]["generated_tx"] == 1
    task = result.summary["tasks"][0]
    assert task["task_execution_ticks"] == 3 and task["slowdown"] == 1.5
    assert task["communication_extension_ticks"] == 1
    ticks = rows(tmp_path / "out/task_tick.csv")
    assert [int(r["logical_tick"]) for r in ticks] == [0, 0, 1]
    assert [float(r["compute_sops"]) for r in ticks] == [10, 0, 20]
    assert [int(r["generated_tx"]) for r in ticks] == [1, 0, 0]
    assert [int(r["rx_ejected"]) for r in ticks] == [0, 1, 0]
    assert [int(r["logical_step_duration_ticks"]) for r in rows(tmp_path / "out/step_timing.csv")] == [2, 1]
    events = rows(tmp_path / "out/events.csv")
    assert [(r["kind"], r["physical_tick"], r["logical_tick"]) for r in events if r["kind"] in ("generate", "rx")] == [
        ("generate", "1", "0"), ("rx", "2", "0")]


def test_zero_traffic_steps_keep_nonzero_compute_and_logical_time(tmp_path):
    path, _ = scene(tmp_path, traffic=((0,), (0,)), compute=((50, 0), (0, 70)), cycles=1)
    result = run_simulation(path, tmp_path / "out", report=False)
    assert result.status == "completed" and result.ticks_executed == 2
    assert result.summary["totals"]["compute_sops"] == 120
    assert result.summary["totals"]["generated_tx"] == 0
    assert [r["logical_tick"] for r in rows(tmp_path / "out/task_tick.csv")] == ["0", "1"]


def test_multitask_examples_aggregate_and_reproduce(tmp_path):
    results = [run_simulation(ROOT / "examples/single_card/positive.json", tmp_path / name,
                              trace=True, report=False) for name in ("a", "b")]
    assert all(r.status == "completed" for r in results)
    assert results[0].summary["totals"]["generated_tx"] == 22
    assert results[0].summary["totals"]["local_flits"] == 2
    assert results[0].summary["totals"]["router_wait_flit_cycles"] > 0
    for file in (tmp_path / "a").glob("*.csv"):
        assert file.read_bytes() == (tmp_path / "b" / file.name).read_bytes()
    core, task, card = (rows(tmp_path / "a" / (name + ".csv"))
                        for name in ("core_tick", "task_tick", "card_tick"))
    for c in card:
        tick = c["physical_tick"]
        for field in MEASURES:
            assert float(c[field]) == sum(float(r[field]) for r in core if r["physical_tick"] == tick)
            assert float(c[field]) == sum(float(r[field]) for r in task if r["physical_tick"] == tick)


@pytest.mark.parametrize("mutation, match", [
    (lambda d: d["tasks"][0].update(mapping=[0, 0]), "mapping"),
    (lambda d: d["tasks"][0].update(mapping=[0, 2]), "mapping"),
    (lambda d: d["tasks"][0].update(start_tick=0), "start_tick"),
    (lambda d: d["card"].update(neurons_per_core=3), "MicroPopulation"),
    (lambda d: d["card"].update(memory_mb=0.5), "memory"),
    (lambda d: d.update(cards=2), "unknown"),
])
def test_invalid_scene_before_output(tmp_path, mutation, match):
    path, doc = scene(tmp_path)
    mutation(doc)
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=match):
        run_simulation(path, tmp_path / "out", report=False)
    assert not (tmp_path / "out").exists()


def test_reuse_core_after_delivery_preserves_unconsumed_task_identity(tmp_path):
    path, doc = scene(tmp_path, traffic=((1,),), compute=((1, 2),), cycles=4, sink_period=8)
    doc["noc"]["sink_buffer_depth"] = 4
    doc["tasks"].append(dict(doc["tasks"][0], task_id="b", start_tick=2))
    path.write_text(json.dumps(doc))
    result = run_simulation(path, tmp_path / "out", trace=True, report=False)
    assert result.status == "completed"
    assert result.summary["totals"]["rx_ejected"] == 2
    events = rows(tmp_path / "out/events.csv")
    consumed = [r for r in events if r["kind"] == "consume"]
    assert consumed[0]["task_id"] == "a" and consumed[0]["physical_tick"] == "2"
    assert all(x["status"] == "completed" for x in result.summary["tasks"])


@pytest.mark.parametrize("name,code,status", [("carry_over", 0, "completed"), ("truncated", 2, "max_ticks")])
def test_cli_completion_and_truncation_exit_codes(tmp_path, name, code, status):
    completed = subprocess.run([sys.executable, str(ROOT / "main.py"), "--scenario",
        str(ROOT / f"examples/single_card/{name}.json"), "--output-dir", str(tmp_path / "out"),
        "--no-report"], capture_output=True, text=True)
    assert completed.returncode == code, completed.stderr
    doc = json.loads(completed.stdout)
    assert doc["status"] == status
    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert manifest["valid"] is (status == "completed")


def test_output_safety_and_explicit_max_ticks(tmp_path):
    path, doc = scene(tmp_path, traffic=((0,), (0,)))
    doc["max_ticks"] = 1
    path.write_text(json.dumps(doc))
    out = tmp_path / "out"
    result = run_simulation(path, out, report=False)
    assert result.status == "max_ticks" and result.summary["valid"] is False
    before = (out / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="empty"):
        run_simulation(path, out, report=False)
    assert (out / "manifest.json").read_bytes() == before


def test_saved_scenario_replays_from_output_directory(tmp_path):
    path, _ = scene(tmp_path)
    first = run_simulation(path, tmp_path / "first", report=False)
    saved = tmp_path / "first/scenario.json"
    assert Path(json.loads(saved.read_text())["tasks"][0]["workload"]).is_absolute()
    replay = run_simulation(saved, tmp_path / "replay", report=False)
    assert first.summary["totals"] == replay.summary["totals"]
    assert (tmp_path / "first/core_tick.csv").read_bytes() == (tmp_path / "replay/core_tick.csv").read_bytes()


def test_shared_barrier_holds_silent_task_and_delays_new_admission(tmp_path):
    path, doc = scene(tmp_path, cycles=2)
    save_workload(tmp_path / "quiet.json", Workload([4], [], [], [[], []], [[5], [7]], 1, "test:quiet"))
    doc["card"]["mesh_x"] = 3
    doc["tasks"].append({"task_id": "b", "workload": "quiet.json", "start_tick": 1, "mapping": [2]})
    doc["tasks"].append({"task_id": "c", "workload": "quiet.json", "start_tick": 2, "mapping": [0]})
    path.write_text(json.dumps(doc))
    result = run_simulation(path, tmp_path / "out", report=False)
    tasks = {t["task_id"]: t for t in result.summary["tasks"]}
    assert tasks["a"]["completion_tick"] == tasks["b"]["completion_tick"] == 3
    assert tasks["b"]["communication_extension_ticks"] == 1
    assert tasks["c"]["actual_start_tick"] == 4 and tasks["c"]["task_start_wait_ticks"] == 2
    ticks = [r for r in rows(tmp_path / "out/task_tick.csv") if r["task_id"] == "b"]
    assert [r["logical_tick"] for r in ticks] == ["0", "0", "1"]
    assert ticks[0]["waiting_for_card_barrier"] == "True"
    assert float(ticks[1]["compute_sops"]) == 0


@pytest.mark.parametrize("resource", ["cores", "memory"])
def test_runtime_resource_admission_waits_until_actual_completion(tmp_path, resource):
    path, doc = scene(tmp_path, traffic=((1,),), compute=((10, 0),), cycles=2)
    doc["card"]["mesh_x"] = 4
    doc["card"]["memory_mb"] = 1 if resource == "memory" else 4
    doc["tasks"].append(dict(doc["tasks"][0], task_id="b", start_tick=1,
                             mapping=[2, 3] if resource == "memory" else [0, 1]))
    path.write_text(json.dumps(doc))
    result = run_simulation(path, tmp_path / "out", report=False)
    a, b = result.summary["tasks"]
    assert a["completion_tick"] == 2
    assert b["actual_start_tick"] == 3 and b["completion_tick"] == 4
    assert b["task_execution_ticks"] == 2 and b["task_end_to_end_ticks"] == 4


def test_truncated_wait_preserves_inventory_and_incomplete_step(tmp_path):
    path, doc = scene(tmp_path, cycles=1)
    doc["max_ticks"] = 2
    path.write_text(json.dumps(doc))
    result = run_simulation(path, tmp_path / "out", report=False)
    assert result.status == "max_ticks"
    task = result.summary["tasks"][0]
    assert task["steps_started"] == 1 and task["steps_completed"] == 0
    assert task["task_execution_ticks"] is None and task["slowdown"] is None
    assert result.summary["totals"]["in_network"] == 1
    step = rows(tmp_path / "out/step_timing.csv")[0]
    assert step["step_completed"] == "False" and step["logical_step_duration_ticks"] == "2"


def test_actual_cross_tick_report_renders_progress_and_slowdown(tmp_path):
    path, _ = scene(tmp_path, cycles=2)
    result = run_simulation(path, tmp_path / "out")
    assert result.status == "completed"
    report = (tmp_path / "out/report.html").read_text()
    assert "通信延长与任务时间" in report and "逻辑步进度" in report
    assert "<td>1.5</td>" in report


def test_slow_sink_backpressure_can_span_ticks_without_losing_old_identity(tmp_path):
    path, doc = scene(tmp_path, traffic=((1,),), compute=((1, 0),), cycles=3, sink_period=10)
    doc["noc"]["sink_buffer_depth"] = 1
    doc["tasks"].append(dict(doc["tasks"][0], task_id="b", start_tick=2))
    path.write_text(json.dumps(doc))
    result = run_simulation(path, tmp_path / "out", trace=True, report=False)
    a, b = result.summary["tasks"]
    assert a["completion_tick"] == 1 and b["actual_start_tick"] == 2
    assert b["completion_tick"] == 4 and b["communication_extension_ticks"] == 2
    assert result.summary["totals"]["rx_blocked_cycles"] > 0
    events = rows(tmp_path / "out/events.csv")
    assert [(r["task_id"],r["cycle"]) for r in events if r["kind"] == "consume"] == [("a","10")]
    assert result.summary["totals"]["rx_consumed"] + result.summary["totals"]["sink_unconsumed"] == 2


def test_disjoint_new_task_waits_for_round_boundary(tmp_path):
    path, doc = scene(tmp_path, traffic=((1,),), compute=((1, 0),), cycles=2)
    doc["card"]["mesh_x"] = 3
    save_workload(tmp_path / "quiet.json", Workload([4], [], [], [[]], [[7]], 0, "test:quiet"))
    doc["tasks"].append({"task_id": "b", "workload": "quiet.json", "start_tick": 2, "mapping": [2]})
    path.write_text(json.dumps(doc))
    result = run_simulation(path, tmp_path / "out", report=False)
    assert result.summary["tasks"][1]["actual_start_tick"] == 3
    assert result.summary["tasks"][1]["task_start_wait_ticks"] == 1


def test_cross_tick_core_task_card_gauges_and_counts_match(tmp_path):
    result = run_simulation(ROOT / "examples/single_card/carry_over.json", tmp_path / "out", report=False)
    core, task, card = (rows(tmp_path / "out" / (name + ".csv"))
                        for name in ("core_tick", "task_tick", "card_tick"))
    for c in card:
        tick = c["physical_tick"]
        for field in MEASURES:
            assert float(c[field]) == sum(float(r[field]) for r in core if r["physical_tick"] == tick)
            assert float(c[field]) == sum(float(r[field]) for r in task if r["physical_tick"] == tick)
    for t in result.summary["tasks"]:
        assert t["task_execution_ticks"] == t["logical_ticks"] + t["communication_extension_ticks"]
        steps = [r for r in rows(tmp_path / "out/step_timing.csv") if r["task_id"] == t["task_id"]]
        assert sum(int(r["logical_step_duration_ticks"]) for r in steps) == t["task_execution_ticks"]
    totals = result.summary["totals"]
    assert totals["generated_tx"] == totals["tx_injected"] == totals["rx_ejected"] == 22
    assert totals["rx_excess_latency_cycles_sum"] == 89
