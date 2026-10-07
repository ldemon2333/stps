"""External arrival streams must not depend on a policy or card completion."""
import numpy as np
import pytest

from simulation.arrivals import make_arrival_ticks


def test_poisson_fixed_count_reproducibility_and_binning():
    actual = make_arrival_ticks("poisson", 24, seed=23, poisson_rate=1.2)
    times = np.cumsum(np.random.default_rng(23).exponential(1 / 1.2, size=24))
    assert actual == [int(time) + 1 for time in times]
    assert len(actual) == 24
    assert actual == sorted(actual)
    assert min(actual) >= 1
    assert len(set(actual)) < len(actual)
    assert actual == make_arrival_ticks("poisson", 24, seed=23, poisson_rate=1.2)
    assert actual != make_arrival_ticks("poisson", 24, seed=24, poisson_rate=1.2)


def test_bursty_partial_final_batch():
    assert make_arrival_ticks("bursty", 8, burst_size=3, burst_interval=5) == [
        1, 1, 1, 6, 6, 6, 11, 11]


def test_empty_stream_and_small_rate_do_not_scan_ticks():
    assert make_arrival_ticks("poisson", 0, seed=1) == []
    assert make_arrival_ticks("bursty", 0) == []
    assert len(make_arrival_ticks("poisson", 2, seed=1, poisson_rate=1e-8)) == 2


@pytest.mark.parametrize("kwargs", [
    {"mode": "possion", "count": 3},
    {"mode": "poisson", "count": -1},
    {"mode": "poisson", "count": 2.5},
    {"mode": "poisson", "count": True},
    {"mode": "poisson", "count": 3, "seed": -1},
    {"mode": "poisson", "count": 3, "seed": False},
    {"mode": "poisson", "count": 3, "poisson_rate": 0},
    {"mode": "poisson", "count": 3, "poisson_rate": -1},
    {"mode": "poisson", "count": 3, "poisson_rate": float("nan")},
    {"mode": "poisson", "count": 3, "poisson_rate": float("inf")},
    {"mode": "poisson", "count": 3, "poisson_rate": True},
    {"mode": "poisson", "count": 3, "poisson_rate": "1.2"},
    {"mode": "poisson", "count": 3, "poisson_rate": 1e-320},
    {"mode": "bursty", "count": 3, "burst_size": 0},
    {"mode": "bursty", "count": 3, "burst_interval": 0},
    {"mode": "bursty", "count": 3, "burst_interval": 1.5},
])
def test_invalid_arrival_parameters(kwargs):
    with pytest.raises(ValueError):
        make_arrival_ticks(**kwargs)


def test_cluster_examples_share_tasks_and_declared_calibration():
    import json
    from pathlib import Path
    from fingerprint.workload import load_workload

    folder = Path(__file__).resolve().parents[1] / "examples" / "cluster"
    poisson = json.loads((folder / "poisson.json").read_text(encoding="utf-8"))
    bursty = json.loads((folder / "bursty.json").read_text(encoding="utf-8"))
    assert poisson["cluster"] == bursty["cluster"]
    assert poisson["noc"] == bursty["noc"]
    assert len(poisson["tasks"]) == len(bursty["tasks"]) == 24
    for left, right in zip(poisson["tasks"], bursty["tasks"]):
        assert {key: value for key, value in left.items() if key != "arrival_tick"} == {
            key: value for key, value in right.items() if key != "arrival_tick"}
        workload = load_workload(folder / left["workload"])
        assert workload.population_count <= 16
        assert max(workload.pop_size) <= poisson["cluster"]["neurons_per_core"]
        assert left["mean_compute_sops"] == workload.compute_sops.sum() / workload.T
        remote = workload.edge_expected_flits[:, workload.edge_src != workload.edge_dst].sum()
        assert left["mean_noc_endpoint"] == 2 * remote / workload.T
        assert np.count_nonzero(workload.edge_expected_flits) < workload.edge_expected_flits.size / 2
        assert np.any(np.all(workload.compute_sops == 0, axis=1))
    assert [task["arrival_tick"] for task in poisson["tasks"]] == make_arrival_ticks(
        "poisson", 24, seed=23, poisson_rate=1.2)
    assert [task["arrival_tick"] for task in bursty["tasks"]] == make_arrival_ticks(
        "bursty", 24, seed=23, burst_size=6, burst_interval=6)
