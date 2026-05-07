"""Unit tests for Q1 spatial-balance metrics on LoadSnapshot."""
from __future__ import annotations

import math

from util.metrics import LoadSnapshot, SimulationMetrics


def _snap(loads):
    return LoadSnapshot(
        time_step=0,
        card_loads={i: v for i, v in enumerate(loads)},
        card_task_counts={i: 1 for i, _ in enumerate(loads)},
    )


def test_cv_uniform_is_zero():
    s = _snap([10.0, 10.0, 10.0, 10.0])
    assert s.cv == 0.0


def test_cv_known_value():
    # mean=2.5, var=1.25, std=sqrt(1.25), cv = sqrt(1.25)/2.5
    s = _snap([1.0, 2.0, 3.0, 4.0])
    assert math.isclose(s.cv, math.sqrt(1.25) / 2.5, rel_tol=1e-9)


def test_jfi_perfect_fairness():
    s = _snap([5.0, 5.0, 5.0, 5.0])
    assert math.isclose(s.jfi, 1.0, rel_tol=1e-9)


def test_jfi_worst_case_one_active():
    s = _snap([10.0, 0.0, 0.0, 0.0])
    # JFI = (10)^2 / (4 * 100) = 0.25 = 1/N
    assert math.isclose(s.jfi, 0.25, rel_tol=1e-9)


def test_jfi_known_value():
    s = _snap([1.0, 2.0, 3.0, 4.0])
    # JFI = 100 / (4 * 30) = 0.8333..
    assert math.isclose(s.jfi, 100.0 / 120.0, rel_tol=1e-9)


def test_max_min_ratio_skips_zeros():
    s = _snap([0.0, 2.0, 8.0, 0.0])
    assert math.isclose(s.max_min_ratio, 4.0, rel_tol=1e-9)


def test_max_min_ratio_all_zero_returns_zero():
    s = _snap([0.0, 0.0, 0.0])
    assert s.max_min_ratio == 0.0


def test_simulation_metrics_aggregates_steady_window():
    m = SimulationMetrics(
        scheduler_name="x", arrival_mode="poisson",
        card_count=4, task_count=10, steps=4, seed=0,
    )
    # 4 snapshots; small N → no trim, all included.
    for t, loads in enumerate([
        [1.0, 1.0, 1.0, 1.0],
        [1.0, 2.0, 3.0, 4.0],
        [2.0, 2.0, 2.0, 2.0],
        [0.0, 4.0, 4.0, 0.0],
    ]):
        snap = LoadSnapshot(time_step=t,
                            card_loads={i: v for i, v in enumerate(loads)},
                            card_task_counts={i: 1 for i in range(4)})
        m.load_snapshots.append(snap)
    # avg_card_cv > 0; avg_card_jfi in (0, 1]
    assert 0 < m.avg_card_cv < 2
    assert 0 < m.avg_card_jfi <= 1
    assert m.avg_max_min_ratio > 0


def test_simulation_metrics_steady_window_trims_when_long():
    m = SimulationMetrics(
        scheduler_name="x", arrival_mode="poisson",
        card_count=2, task_count=1, steps=200, seed=0,
    )
    for t in range(200):
        # Hot warmup, calm steady, hot teardown.
        if t < 64 or t >= 200 - 64:
            loads = [10.0, 0.0]
        else:
            loads = [1.0, 1.0]
        m.load_snapshots.append(LoadSnapshot(
            time_step=t,
            card_loads={i: v for i, v in enumerate(loads)},
            card_task_counts={i: 1 for i in range(2)},
        ))
    # Steady region is uniform → cv ~ 0.
    assert math.isclose(m.avg_card_cv, 0.0, abs_tol=1e-9)
    assert math.isclose(m.avg_card_jfi, 1.0, rel_tol=1e-9)
