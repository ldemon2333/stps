"""Unit tests for LoadSnapshot.lif (Load Imbalance Factor) and avg_card_lif."""
from __future__ import annotations

from util.metrics import LoadSnapshot, SimulationMetrics


def _snap(loads):
    return LoadSnapshot(
        time_step=0,
        card_loads={i: v for i, v in enumerate(loads)},
        card_task_counts={i: 0 for i, _ in enumerate(loads)},
    )


def test_lif_perfect_balance():
    assert _snap([1.0, 1.0, 1.0, 1.0]).lif == 1.0


def test_lif_single_hotspot():
    # Only card 0 active: max=4, mean=1, LIF=4=M
    assert _snap([4.0, 0.0, 0.0, 0.0]).lif == 4.0


def test_lif_all_zero():
    assert _snap([0.0, 0.0, 0.0, 0.0]).lif == 0.0


def test_lif_mixed():
    # max=6, mean=(6+3+2+1)/4=3, LIF=2.0
    assert _snap([6.0, 3.0, 2.0, 1.0]).lif == 2.0


def test_avg_card_lif_skips_zero_snapshots():
    m = SimulationMetrics(
        scheduler_name="t", arrival_mode="bursty",
        card_count=4, task_count=0, steps=0, seed=None,
    )
    m.load_snapshots = [_snap([0, 0, 0, 0]), _snap([4, 0, 0, 0]), _snap([2, 2, 2, 2])]
    # only non-zero LIF snapshots: 4.0 and 1.0 → mean 2.5
    assert m.avg_card_lif == 2.5
