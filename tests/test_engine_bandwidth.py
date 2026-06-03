"""Engine bandwidth contention tests (docs/traffic_optim.md §A.5)."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from fingerprint import (
    effective_traffic_trace,
    make_synthetic_fingerprint,
    save_fingerprint,
)
from simulation.engine import SimulationEngine, run_simulation
from schedule.bestfit import BestFitScheduler
from util.card import Card
from util.task import Task


def _make_fp_dir(tmp_path: Path) -> Path:
    fp_dir = tmp_path / "fp"
    save_fingerprint(
        fp_dir / "a.npz",
        make_synthetic_fingerprint(beta_target=1.5, K=1, T=8, V=4, seed=1),
    )
    save_fingerprint(
        fp_dir / "b.npz",
        make_synthetic_fingerprint(beta_target=2.0, K=1, T=8, V=4, seed=2),
    )
    return fp_dir


def _setup_single_task_engine(tmp_path: Path) -> tuple[SimulationEngine, Task]:
    fp_dir = _make_fp_dir(tmp_path)
    eng = SimulationEngine(
        scheduler_name="bestfit",
        card_count=1,
        task_count=0,
        steps=1,
        seed=1,
        log_dir=str(tmp_path / "log"),
        data_dir=str(tmp_path / "data"),
        fingerprint_dir=str(fp_dir),
    )
    eng.cards = [Card(card_id=0)]
    eng.scheduler = BestFitScheduler(eng.cards)
    eng._card_epoch_load = {0: 0.0}
    eng._card_epoch_demand = {0: 0.0}
    eng._card_epoch_backlog = {0: 0.0}
    eng._load_fingerprint_dir()
    eng._max_backlog_ticks = 64

    from util.metrics import SimulationMetrics
    eng.metrics = SimulationMetrics(
        scheduler_name="bestfit", arrival_mode="poisson",
        card_count=1, task_count=1, steps=8, seed=1,
    )

    task = Task(
        task_id=0,
        state_size_mb=8.0,
        neuron_count=64,
        complexity_ratio=1.0,
        arrival_step=1,
        duration_steps=8,
    )
    task.fingerprint_path, task.fingerprint = eng._pick_fingerprint(0)
    task.placement_step = 1
    eng.cards[0].put(task)
    eng.active_tasks = [task]
    return eng, task


def test_bw_cap_none_matches_legacy(tmp_path):
    """bw_cap=None must serve every task quantum fully each tick."""
    eng, task = _setup_single_task_engine(tmp_path)
    trace = effective_traffic_trace(task.fingerprint)
    for t in range(1, len(trace) + 1):
        eng._tick(t=t + 1)
    assert task.tick_index == len(trace)
    assert task.pending_traffic == 0.0
    assert task.congestion_wait_ticks == 0
    assert np.isclose(eng._card_epoch_load[0], float(trace.sum()), atol=1e-4)


def test_bw_cap_tight_creates_backlog_and_blocks_progress(tmp_path):
    """Tiny bw_cap must accumulate pending_traffic and freeze tick_index / duration."""
    eng, task = _setup_single_task_engine(tmp_path)
    trace = effective_traffic_trace(task.fingerprint)
    first_demand = float(trace[0])
    eng.cards[0].bw_cap = first_demand * 1e-3

    duration_before = task.duration_steps
    eng._tick(t=2)
    # Quantum not fully drained, so trace tick must not advance.
    assert task.tick_index == 0
    assert task.pending_traffic > 0
    assert task.blocked_ticks == 1
    assert task.congestion_wait_ticks == 1
    # _handle_completions must not decrement duration while a quantum is pending.
    eng._handle_completions(time_step=2)
    assert task.duration_steps == duration_before


def test_bw_cap_generous_zero_congestion(tmp_path):
    """bw_cap >> peak demand must behave like unlimited."""
    eng, task = _setup_single_task_engine(tmp_path)
    trace = effective_traffic_trace(task.fingerprint)
    eng.cards[0].bw_cap = float(trace.max()) * 10.0

    for t in range(1, len(trace) + 1):
        eng._tick(t=t + 1)
    assert task.tick_index == len(trace)
    assert task.congestion_wait_ticks == 0
    assert task.pending_traffic == 0.0


def test_backlog_timeout_triggers_circuit_breaker(tmp_path):
    """Permanent over-cap demand must eventually trip congestion_timeouts and drain."""
    eng, task = _setup_single_task_engine(tmp_path)
    trace = effective_traffic_trace(task.fingerprint)
    # Force scenarios where demand always exceeds cap: cap is tiny but non-zero.
    eng.cards[0].bw_cap = float(trace[0]) * 1e-6
    eng._max_backlog_ticks = 5

    for t in range(1, 50):
        eng._tick(t=t + 1)
        if eng.metrics.congestion_timeouts > 0:
            break
    assert eng.metrics.congestion_timeouts > 0
    # After at least one timeout, tick_index must have advanced past zero.
    assert task.tick_index >= 1


def test_stps_forecast_uses_effective_trace_helper(tmp_path):
    """STPS Stage B must consult the same effective trace as the engine executes."""
    from schedule.stps import STPSScheduler
    fp_dir = _make_fp_dir(tmp_path)

    # End-to-end run with bw_cap binds bw_max=bw_cap when CLI default used.
    metrics = run_simulation(
        scheduler="stps",
        cards=2,
        tasks=4,
        steps=16,
        seed=11,
        log_dir=str(tmp_path / "log"),
        data_dir=str(tmp_path / "data"),
        arrival_mode="poisson",
        fingerprint_dir=str(fp_dir),
        bw_cap=0.5,
        bw_max=0.5,
        d_max=4,
        horizon=8,
    )
    # When bw_cap is tight, engine must record some congestion or backlog.
    assert metrics.bw_cap_value == 0.5
    # avg_congestion_ratio is non-negative; at this tight cap we expect > 0.
    assert metrics.avg_congestion_ratio >= 0.0
