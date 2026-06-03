from __future__ import annotations

from pathlib import Path

import numpy as np

from fingerprint import make_synthetic_fingerprint, save_fingerprint
from schedule import list_schedulers
from schedule.bestfit import BestFitScheduler
from schedule.phase_wrappers import BestFitPhaseScheduler
from util.card import Card
from util.sim import create_task
from simulation.engine import run_simulation


def _make_fp_dir(tmp_path: Path) -> Path:
    fp_dir = tmp_path / "fp"
    save_fingerprint(
        fp_dir / "flat.npz",
        make_synthetic_fingerprint(beta_target=1.05, K=1, T=8, V=4, seed=1),
    )
    save_fingerprint(
        fp_dir / "bursty.npz",
        make_synthetic_fingerprint(beta_target=4.0, K=1, T=8, V=4, seed=2),
    )
    return fp_dir


def test_q2_phase_schedulers_are_registered():
    schedulers = set(list_schedulers())

    assert {"rr-phase", "bestfit-phase", "drf-phase", "p2c-phase"} <= schedulers


def test_bestfit_phase_preserves_base_card_choice_and_sets_offset():
    cards = [Card(card_id=0), Card(card_id=1)]
    cards[0].ensure_forecast(8)
    cards[0].add_forecast(np.array([10, 0, 0, 0], dtype=np.float32), 0)
    fp = make_synthetic_fingerprint(beta_target=1.0, K=1, T=4, V=4, seed=3)
    object.__setattr__(fp, "mean_injection_trace", np.array([10, 0, 0, 0], dtype=np.float32))
    object.__setattr__(fp, "T", 4)
    task = create_task(0, 1, fp)

    base_choice = BestFitScheduler(cards).select_card_for_task(task)
    phase_choice = BestFitPhaseScheduler(cards, horizon=8, d_max=3, bw_max=100.0).select_card_for_task(task)

    assert phase_choice is base_choice
    assert phase_choice is cards[0]
    assert task.start_offset == 1


def test_dmax_zero_phase_run_records_zero_offsets(tmp_path):
    fp_dir = _make_fp_dir(tmp_path)

    metrics = run_simulation(
        scheduler="bestfit-phase",
        cards=2,
        tasks=8,
        steps=8,
        seed=7,
        log_dir=str(tmp_path / "log"),
        data_dir=str(tmp_path / "data"),
        arrival_mode="poisson",
        fingerprint_dir=str(fp_dir),
        horizon=8,
        d_max=0,
        bw_max=1e9,
    )

    assert metrics.mean_start_offset == 0.0
    assert metrics.p95_start_offset == 0.0


def test_q2_checkpoint_resume_skips_completed_runs_and_rebuilds_outputs(tmp_path, monkeypatch):
    import csv
    import script.q2_run as q2_run

    out_dir = tmp_path / "q2"
    raw_path = out_dir / "resume_raw.csv"
    existing = {
        "scheduler": "rr",
        "seed": 21,
        "cards": 2,
        "tasks": 4,
        "arrival_mode": "poisson",
        "fingerprint_set": "mixed",
        "card_cv": 1.0,
        "card_jfi": 0.5,
        "card_lif": 2.0,
        "max_min_ratio": 3.0,
        "completion_rate": 1.0,
        "throughput": 0.5,
        "p99_delay": 4.0,
        "mean_start_offset": 0.0,
        "p95_start_offset": 0.0,
        "reject_rate_bw": 0.0,
        "base_algo": "RR",
        "phase_enabled": False,
        "pair_base_scheduler": "rr",
        "pair_phase_scheduler": "rr-phase",
    }
    q2_run._write_csv(raw_path, [existing])

    monkeypatch.setattr(q2_run, "ARRIVALS", ["poisson"])
    monkeypatch.setattr(q2_run, "PAIRS", [("rr", "rr-phase", "RR")])
    monkeypatch.setattr(q2_run, "SEEDS", [21])
    calls = []

    def fake_run_one(scheduler, seed, cards, tasks, arrival, fp_dir):
        calls.append((scheduler, seed, cards, tasks, arrival, fp_dir))
        return {
            "scheduler": scheduler,
            "seed": seed,
            "cards": cards,
            "tasks": tasks,
            "arrival_mode": arrival,
            "fingerprint_set": "mixed",
            "card_cv": 0.5,
            "card_jfi": 0.8,
            "card_lif": 1.5,
            "max_min_ratio": 2.0,
            "completion_rate": 1.0,
            "throughput": 0.4,
            "p99_delay": 8.0,
            "mean_start_offset": 1.0,
            "p95_start_offset": 2.0,
            "reject_rate_bw": 0.0,
        }

    monkeypatch.setattr(q2_run, "_run_one", fake_run_one)

    q2_run.run_matrix(out_dir, "resume", cards=2, tasks=4, fp_dir="fp", resume=True)

    assert calls == [("rr-phase", 21, 2, 4, "poisson", "fp")]
    rows = list(csv.DictReader(raw_path.open()))
    assert len(rows) == 2
    assert (out_dir / "resume_summary.csv").exists()
    assert (out_dir / "resume_vertical.csv").exists()
