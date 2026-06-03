from __future__ import annotations

import numpy as np
import pytest

from fingerprint import Fingerprint
from schedule.hotspot_split import split_population
from schedule.phase_shift import find_optimal_offset
from schedule.stps import STPSScheduler, STPSSpatialScheduler, STPSTemporalScheduler
from util.card import Card
from util.task import Task


def make_task(task_id: int = 0, *, cores: int | None = None) -> Task:
    task = Task(
        task_id=task_id,
        state_size_mb=8.0,
        neuron_count=256,
        complexity_ratio=1.0,
        arrival_step=1,
        duration_steps=4,
    )
    if cores is not None:
        task.cores_required = cores
    return task


def make_fp(
    E: np.ndarray | None = None,
    beta: float = 4.0,
    K_mean: float = 2.0,
) -> Fingerprint:
    E = np.asarray(E if E is not None else [0.0, 10.0, 0.0, 0.0], dtype=np.float32)
    return Fingerprint(
        mean_injection_trace=E,
        global_burstiness=beta,
        max_centrality=np.array([0.1, 0.7, 0.2], dtype=np.float32),
        mean_components=K_mean,
        T=int(E.shape[0]),
        neuron_count=64,
        state_size_mb=4.0,
        complexity_ratio=1.0,
        compute_sequence=np.zeros(E.shape[0], dtype=np.float32),
        centrality_var=np.full(E.shape[0], 0.05, dtype=np.float32),
        meta={"source": "test"},
    )


def test_phase_shift_interleaves_pulses_under_bandwidth_limit():
    E_m = np.zeros(8, dtype=np.float32)
    E_new = np.zeros(8, dtype=np.float32)
    E_m[2] = 10.0
    E_new[2] = 10.0

    offset, peak = find_optimal_offset(E_m, E_new, D_max=4, BW_max=15.0)

    assert offset != 0
    assert peak <= 10.0


def test_phase_shift_returns_min_peak_fallback_when_no_offset_is_feasible():
    E_m = np.full(4, 50.0, dtype=np.float32)
    E_new = np.array([10.0, 0.0, 0.0, 0.0], dtype=np.float32)

    offset, peak = find_optimal_offset(E_m, E_new, D_max=2, BW_max=40.0)

    assert offset in {0, 1, 2}
    assert peak > 40.0


def test_hotspot_split_flags_threshold_and_handles_empty_vector():
    assert split_population(np.array([0.1, 0.2, 0.6], dtype=np.float32), 0.2) == [1, 2]
    assert split_population(np.array([], dtype=np.float32), 0.2) == []


def test_stps_selects_temporal_offset_updates_forecast_and_split_plan():
    cards = [Card(card_id=0), Card(card_id=1)]
    scheduler = STPSScheduler(cards, horizon=8, d_max=4, bw_max=15.0)
    cards[0].add_forecast(np.array([0, 0, 10, 0], dtype=np.float32), 0)
    task = make_task()
    task.fingerprint = make_fp(np.array([0, 0, 10, 0], dtype=np.float32))

    chosen = scheduler.select_card_for_task(task)

    assert chosen is not None
    assert task.start_offset >= 1
    assert task.split_plan == [1, 2]
    assert chosen.peak_forecast() <= 10.0
    assert chosen.beta_card > 1.0


def test_stps_admits_task_with_min_peak_offset_when_bw_max_infeasible():
    """When no offset keeps the forecast peak under bw_max, STPS still places
    the task using the min-peak offset and lets the NoC pending_traffic queue
    absorb the overflow. The previous reject-and-drop semantics was removed in
    the traffic_optim refactor — see docs/traffic_result.md §3."""
    cards = [Card(card_id=0)]
    scheduler = STPSScheduler(cards, horizon=4, d_max=1, bw_max=5.0)
    task = make_task()
    task.fingerprint = make_fp(np.array([10, 10, 10, 10], dtype=np.float32))

    chosen = scheduler.select_card_for_task(task)
    assert chosen is cards[0]
    assert task.start_offset in (0, 1)
    assert cards[0].forecast is not None
    assert float(cards[0].forecast.sum()) > 0.0


def test_stps_falls_back_to_resource_capacity_when_card_cannot_host_task():
    cards = [Card(card_id=0, cores=2)]
    scheduler = STPSScheduler(cards, horizon=4)
    task = make_task(cores=3)
    task.fingerprint = make_fp()

    assert scheduler.select_card_for_task(task) is None


def test_stps_spatial_ablation_prefers_fragmentation_match_and_sets_split_plan():
    cards = [Card(card_id=0), Card(card_id=1)]
    cards[0].put(make_task(task_id=10, cores=256))
    scheduler = STPSSpatialScheduler(cards, horizon=6, d_max=4, bw_max=1.0)
    task = make_task()
    task.fingerprint = make_fp(K_mean=1.0)

    chosen = scheduler.select_card_for_task(task)

    assert chosen is cards[1]
    assert task.start_offset == 0
    assert task.split_plan == [1, 2]

    fragmented_task = make_task(task_id=2)
    fragmented_task.fingerprint = make_fp(K_mean=4.0)
    fragmented_chosen = scheduler.select_card_for_task(fragmented_task)

    assert fragmented_chosen is cards[0]


def test_stps_temporal_ablation_sets_offset_without_split_plan():
    cards = [Card(card_id=0)]
    scheduler = STPSTemporalScheduler(cards, horizon=8, d_max=4, bw_max=100.0)
    task = make_task()
    task.fingerprint = make_fp(np.array([0, 0, 10, 0], dtype=np.float32))

    chosen = scheduler.select_card_for_task(task)

    assert chosen is cards[0]
    assert task.start_offset == 0
    assert task.split_plan == []


def test_card_forecast_rolls_forward_and_truncates_over_horizon():
    card = Card(card_id=0)
    card.ensure_forecast(4)
    card.add_forecast(np.array([1, 2, 3, 4, 5], dtype=np.float32), offset=2)

    np.testing.assert_array_equal(card.forecast, np.array([0, 0, 1, 2], dtype=np.float32))
    card.advance_forecast()
    np.testing.assert_array_equal(card.forecast, np.array([0, 1, 2, 0], dtype=np.float32))
