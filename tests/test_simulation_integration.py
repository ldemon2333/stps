from __future__ import annotations

import csv
import subprocess
import sys

import numpy as np

from fingerprint import make_synthetic_fingerprint, save_fingerprint
from simulation.engine import SimulationEngine, run_simulation


def test_list_schedulers_exposes_baselines_and_stps_family():
    result = subprocess.run(
        [sys.executable, "main.py", "--list-schedulers"],
        text=True,
        capture_output=True,
        check=True,
    )

    for name in ["bestfit", "drf", "p2c", "rr", "stps", "stps-spatial", "stps-temporal"]:
        assert f"- {name}" in result.stdout


def _make_fingerprint_dir(tmp_path):
    fp_dir = tmp_path / "fingerprints"
    save_fingerprint(
        fp_dir / "flat.npz",
        make_synthetic_fingerprint(beta_target=1.2, K=1, T=8, V=4, seed=1),
    )
    save_fingerprint(
        fp_dir / "bursty.npz",
        make_synthetic_fingerprint(beta_target=3.0, K=2, T=8, V=4, seed=2),
    )
    return fp_dir


def test_bestfit_smoke_run_writes_load_and_summary_csv(tmp_path):
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "log"
    fp_dir = _make_fingerprint_dir(tmp_path)

    metrics = run_simulation(
        scheduler="bestfit",
        cards=2,
        tasks=4,
        steps=4,
        seed=11,
        log_dir=str(log_dir),
        data_dir=str(data_dir),
        data_output="bestfit_smoke",
        arrival_mode="poisson",
        
        fingerprint_dir=str(fp_dir),
    )

    assert metrics.scheduler_name == "BestFit"
    assert metrics.task_count == 4
    assert metrics.load_snapshots
    load_files = list(data_dir.glob("bestfit_smoke_loads_*.csv"))
    summary_files = list(data_dir.glob("bestfit_smoke_summary_*.csv"))
    assert len(load_files) == 1
    assert len(summary_files) == 1

    with load_files[0].open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["time_step", "card_id", "load", "tasks"]
    assert len(rows) > 1


def test_stps_uses_fingerprint_directory_and_completes_small_run(tmp_path):
    fp_dir = _make_fingerprint_dir(tmp_path)

    metrics = run_simulation(
        scheduler="stps",
        cards=2,
        tasks=5,
        steps=5,
        seed=22,
        log_dir=str(tmp_path / "log"),
        data_dir=str(tmp_path / "data"),
        data_output="stps_smoke",
        arrival_mode="mixed",
        
        fingerprint_dir=str(fp_dir),
        horizon=8,
        d_max=3,
        bw_max=1e6,
    )

    assert metrics.scheduler_name == "stps"
    assert metrics.task_count == 5
    assert metrics.load_snapshots
    assert 0.0 <= metrics.completion_rate <= 1.0


def test_missing_fingerprint_dir_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        run_simulation(
            scheduler="bestfit",
            cards=1,
            tasks=1,
            steps=1,
            seed=1,
            log_dir=str(tmp_path / "log"),
            data_dir=str(tmp_path / "data"),
            fingerprint_dir=str(tmp_path / "does-not-exist"),
        )


def test_card_epoch_load_equals_E_sum_when_one_task_runs_one_period(tmp_path):
    """Q1_TODO §9.4 acceptance: a single task running for T ticks accumulates E.sum() of traffic."""
    fp_dir = _make_fingerprint_dir(tmp_path)

    engine = SimulationEngine(
        scheduler_name="bestfit",
        card_count=1,
        task_count=0,
        steps=1,
        seed=1,
        log_dir=str(tmp_path / "log"),
        data_dir=str(tmp_path / "data"),
        
        fingerprint_dir=str(fp_dir),
    )
    # Boot only the parts we need (no full run()).
    from schedule.bestfit import BestFitScheduler
    from util.card import Card
    from util.task import Task

    engine.cards = [Card(card_id=0)]
    engine.scheduler = BestFitScheduler(engine.cards)
    engine._card_epoch_load = {card.card_id: 0.0 for card in engine.cards}
    engine._load_fingerprint_dir()

    task = Task(
        task_id=0,
        state_size_mb=8.0,
        neuron_count=64,
        complexity_ratio=1.0,
        arrival_step=1,
        duration_steps=99,
    )
    task.fingerprint_path, task.fingerprint = engine._pick_fingerprint(task.task_id)
    task.placement_step = 1
    engine.cards[0].put(task)
    engine.active_tasks = [task]

    T = task.fingerprint.T
    for _ in range(T):
        engine._tick(t=2)

    accumulated = engine._card_epoch_load[0]
    assert np.isclose(accumulated, float(task.fingerprint.traffic_sequence.sum()), atol=1e-4)


def test_task_traffic_does_not_wrap_after_fingerprint_window(tmp_path):
    fp_dir = _make_fingerprint_dir(tmp_path)

    engine = SimulationEngine(
        scheduler_name="bestfit",
        card_count=1,
        task_count=0,
        steps=1,
        seed=1,
        log_dir=str(tmp_path / "log"),
        data_dir=str(tmp_path / "data"),
        
        fingerprint_dir=str(fp_dir),
    )
    from schedule.bestfit import BestFitScheduler
    from util.card import Card
    from util.task import Task

    engine.cards = [Card(card_id=0)]
    engine.scheduler = BestFitScheduler(engine.cards)
    engine._card_epoch_load = {card.card_id: 0.0 for card in engine.cards}
    engine._load_fingerprint_dir()

    task = Task(
        task_id=0,
        state_size_mb=8.0,
        neuron_count=64,
        complexity_ratio=1.0,
        arrival_step=1,
        duration_steps=99,
    )
    task.fingerprint_path, task.fingerprint = engine._pick_fingerprint(task.task_id)
    task.placement_step = 1
    engine.cards[0].put(task)
    engine.active_tasks = [task]

    for _ in range(task.fingerprint.T):
        engine._tick(t=2)
    engine._reset_epoch_loads()

    engine._tick(t=2)

    assert task.current_traffic == 0.0
    assert engine._card_epoch_load[0] == 0.0


def test_phase_shift_delay_zeros_traffic_until_offset_expires(tmp_path):
    fp_dir = _make_fingerprint_dir(tmp_path)
    engine = SimulationEngine(
        scheduler_name="stps",
        card_count=1,
        task_count=0,
        steps=1,
        seed=1,
        log_dir=str(tmp_path / "log"),
        data_dir=str(tmp_path / "data"),
        
        fingerprint_dir=str(fp_dir),
    )

    from schedule.stps import STPSScheduler
    from util.card import Card
    from util.task import Task

    engine.cards = [Card(card_id=0)]
    engine.scheduler = STPSScheduler(engine.cards, horizon=8)
    engine._card_epoch_load = {card.card_id: 0.0 for card in engine.cards}
    engine._load_fingerprint_dir()

    task = Task(
        task_id=0,
        state_size_mb=8.0,
        neuron_count=256,
        complexity_ratio=1.0,
        arrival_step=1,
        duration_steps=99,
    )
    task.fingerprint_path, task.fingerprint = engine._pick_fingerprint(task.task_id)
    task.placement_step = 2
    task.start_offset = 3
    engine.active_tasks = [task]

    engine._tick(t=4)
    assert task.tick_index == 0
    assert task.current_traffic == 0.0

    engine._tick(t=5)
    assert task.tick_index == 1
    assert task.current_traffic == float(task.fingerprint.traffic_sequence[0])


def test_invalid_scheduler_returns_actionable_cli_error():
    result = subprocess.run(
        [sys.executable, "main.py", "--scheduler", "does-not-exist", "--steps", "1", "--tasks", "0"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Use --list-schedulers" in result.stderr
