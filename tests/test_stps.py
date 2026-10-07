"""Hand-counted aggregate prediction and joint card/offset contracts."""
from dataclasses import FrozenInstanceError, replace
import math

import numpy as np
import pytest

from fingerprint.scheduling import SchedulingFingerprint
from schedule.stps import (
    CardForecastState, ForecastTask, STPSConfig, choose_stps,
    demand_cycles, residual_demand_cycles,
)
from simulation.noc import NoCConfig


def profile(traffic, compute=None):
    traffic = np.asarray(traffic, dtype=float)
    return SchedulingFingerprint(
        np.array([1, 1]), np.array([0]), np.array([1]), traffic.reshape(-1, 1),
        np.zeros(len(traffic)) if compute is None else np.asarray(compute, dtype=float),
        1.0, "independent-test-calibration",
    )


def state(*, card_id=0, tasks=(), mapping=(1, 2), k=6, tick=1,
          round_open=False, outstanding=(), noc=None,
          assigned_compute=0.0, assigned_noc=0.0):
    return CardForecastState(
        card_id=card_id, tasks=tuple(tasks),
        network_config=noc or NoCConfig(4, 2), cycles_per_tick=k,
        current_tick=tick, round_open=round_open,
        outstanding=tuple(outstanding), compute_budget_sops=100.0,
        noc_budget_endpoint=100.0,
        cumulative_assigned_compute_sops=assigned_compute,
        cumulative_assigned_noc_endpoint=assigned_noc, mapping=tuple(mapping),
    )


def row(kind, src=0, dst=7, router=0, count=1):
    return {"kind": kind, "src_core": src, "dst_core": dst,
            "router": router, "count": count, "task_id": "old"}


def test_demand_uses_row_major_xy_and_directed_shared_links():
    noc = NoCConfig(4, 2)
    # Cores 1 and 2 are neighboring top-row routers, not opposite row ends.
    assert demand_cycles(noc, [(1, 2, 1)]) == 3
    assert demand_cycles(noc, [(0, 7, 1)]) == 6
    # Independent sources/receivers can still share a directed link.
    assert demand_cycles(noc, [(0, 3, 6), (1, 2, 6)]) == 12
    assert demand_cycles(noc, [(0, 3, 6), (4, 7, 6)]) == 6
    assert demand_cycles(noc, [(0, 3, 6), (3, 0, 6)]) == 6


def test_demand_keeps_fractional_means_and_local_traffic_off_network():
    noc = NoCConfig(4, 2)
    assert demand_cycles(noc, [(0, 0, 1e12)]) == 0
    assert demand_cycles(noc, [(0, 1, 3.5)]) == 3.5
    assert demand_cycles(noc, []) == 0


def test_sink_inventory_affects_delivery_pressure_but_is_not_new_traffic():
    noc = NoCConfig(4, 2, sink_buffer_depth=2, sink_service_period=3)
    assert demand_cycles(noc, [(0, 1, 6)]) == 12
    assert demand_cycles(noc, [(0, 1, 6)], {1: 2}) == 18
    assert demand_cycles(noc, [], {1: 2}) == 0
    assert residual_demand_cycles(noc, [row("sink_ni", dst=1, router=1, count=2)]) == 0


def test_residual_routes_from_current_router_without_reinjecting():
    noc = NoCConfig(4, 2)
    assert residual_demand_cycles(noc, [row("source_ni")]) == 6
    assert residual_demand_cycles(noc, [row("router", router=3)]) == 2
    assert residual_demand_cycles(noc, [row("router", router=7)]) == 1
    # A compressed source batch is arithmetic, not a trillion forecast objects.
    assert residual_demand_cycles(noc, [row("source_pending", count=10**12)]) == 10**12


def test_joint_search_can_choose_delayed_card_that_loses_immediate_comparison():
    first = state(tasks=(ForecastTask("old0", profile([6, 0]), (0, 3), 0, 1, False),))
    second = state(card_id=1, tasks=(
        ForecastTask("old1", profile([1, 6]), (0, 3), 0, 1, False),))
    decision = choose_stps([first, second], "new", profile([6, 0]), STPSConfig(d_max=3))
    immediate = min((r for r in decision.candidates if r["delay"] == 0),
                    key=lambda r: (r["J"], r["pressure"], r["card_id"]))
    assert immediate["card_id"] == 1
    assert (decision.card_id, decision.delay, decision.J) == (0, 1, 3)
    assert (decision.predicted_actual_start, decision.predicted_completion) == (2, 3)
    chosen = next(r for r in decision.candidates if (r["card_id"], r["delay"]) == (0, 1))
    assert chosen["externality_ticks"] == 0
    assert chosen["predicted_existing_completions"] == {"old0": 2}
    assert decision.candidate_count == 8


def test_empty_cards_choose_zero_delay_and_lowest_id_with_no_work():
    task = profile([0, 0, 0])
    decision = choose_stps([state(card_id=5), state(card_id=2)], "new", task)
    assert (decision.card_id, decision.delay, decision.J) == (2, 0, 3)
    assert (decision.predicted_actual_start, decision.predicted_completion) == (1, 3)
    assert decision.peak_comp == decision.peak_noc == decision.pressure == 0
    assert choose_stps([], "new", task) is None


def test_dmax_zero_still_searches_every_card_and_respects_fixed_mapping():
    # Same demand sees different fixed paths; no mapping optimization occurs.
    close = state(card_id=3, mapping=(0, 1), k=3)
    far = state(card_id=0, mapping=(0, 7), k=3)
    decision = choose_stps([far, close], "new", profile([1]), STPSConfig(d_max=0))
    assert decision.card_id == 3 and decision.mapping == (0, 1)
    assert decision.predicted_completion == 1
    assert decision.candidate_count == 2
    assert {tuple(r["mapping"]) for r in decision.candidates} == {(0, 7), (0, 1)}


def test_future_reservation_does_not_block_existing_running_task():
    running = ForecastTask("running", profile([0, 0, 0]), (0, 3), 1, 1, True)
    reserved = ForecastTask("reserved", profile([0]), (4, 5), 0, 1000000, False)
    card = state(tasks=(running, reserved), tick=2)
    decision = choose_stps([card], "new", profile([0]), STPSConfig(d_max=0, max_rounds=4))
    record = decision.candidates[0]
    assert decision.predicted_completion == 2
    assert record["baseline_existing_completions"] == {"running": 3, "reserved": 1000000}
    assert record["predicted_existing_completions"] == record["baseline_existing_completions"]
    assert record["predicted_rounds"] == 3


def test_open_round_uses_only_observed_residual_and_merges_equal_join_times():
    # Already-generated profile step had huge traffic/SOP. Only one flit at
    # its destination router remains; the current step is never reissued.
    running = ForecastTask("old", profile([1000000], [1000000]), (0, 7), 1, 1, True)
    card = state(tasks=(running,), tick=5, k=1, round_open=True,
                 outstanding=(row("router", router=3),))
    decision = choose_stps([card], "new", profile([0]), STPSConfig(d_max=3))
    assert (decision.delay, decision.predicted_actual_start, decision.predicted_completion) == (0, 7, 7)
    assert decision.peak_comp == 0
    assert decision.peak_noc == 1  # router stock only still needs its Rx event
    assert [r["delay"] for r in decision.candidates] == [0, 3]
    assert decision.candidates[0]["baseline_existing_completions"] == {"old": 6}
    assert decision.candidates[0]["predicted_residual_ticks"] == 2


def test_open_round_completes_current_step_then_forecasts_only_unissued_tail():
    running = ForecastTask("old", profile([1000000, 0], [1000000, 10]), (0, 7), 1, 1, True)
    card = state(tasks=(running,), tick=4, k=3, round_open=True,
                 outstanding=(row("router", router=7),))
    decision = choose_stps([card], "new", profile([0]), STPSConfig(d_max=0))
    assert decision.predicted_actual_start == 5
    assert decision.predicted_completion == 5
    assert decision.peak_comp == 10
    assert decision.candidates[0]["predicted_existing_completions"] == {"old": 5}


def test_compute_demand_is_independent_and_only_breaks_equal_completion_cost():
    hot = ForecastTask("hot", profile([0], [100]), (0, 3), 0, 1, False)
    cool = ForecastTask("cool", profile([0], [1]), (0, 3), 0, 1, False)
    decision = choose_stps([state(tasks=(hot,)), state(card_id=1, tasks=(cool,))],
                           "new", profile([0], [10]), STPSConfig(d_max=0))
    assert decision.card_id == 1
    assert decision.J == 1 and decision.peak_comp == 11
    assert decision.peak_noc == 0


def test_completion_objective_is_the_unchanged_default():
    cards = [
        state(card_id=3, mapping=(0, 1), assigned_compute=100, assigned_noc=100),
        state(card_id=1, mapping=(0, 7)),
    ]
    task = profile([1], [2])
    assert choose_stps(cards, "new", task, STPSConfig(d_max=0)) == choose_stps(
        cards, "new", task, STPSConfig(d_max=0, objective="completion"))


def test_balance_can_accept_a_slightly_slower_card_within_j_slack():
    fast_loaded = state(card_id=0, mapping=(0, 1), k=3,
                        assigned_compute=10, assigned_noc=2)
    slow_empty = state(card_id=1, mapping=(0, 7), k=3)
    task = profile([1] + [0] * 9, [1] * 10)
    completion = choose_stps([fast_loaded, slow_empty], "new", task,
                             STPSConfig(d_max=0))
    balanced = choose_stps([fast_loaded, slow_empty], "new", task,
                           STPSConfig(d_max=0, objective="balance",
                                      balance_slack=0.10))
    assert (completion.card_id, completion.J) == (0, 10)
    assert (balanced.card_id, balanced.J) == (1, 11)
    assert balanced.balance_primary == 0
    assert all(math.isclose(row["balance_j_limit"], 11)
               for row in balanced.candidates)


def test_balance_rejects_better_balance_beyond_j_slack():
    noc = NoCConfig(4, 4)
    fast_loaded = state(card_id=0, mapping=(0, 1), k=3, noc=noc,
                        assigned_compute=10, assigned_noc=2)
    too_slow_empty = state(card_id=1, mapping=(0, 15), k=3, noc=noc)
    decision = choose_stps(
        [fast_loaded, too_slow_empty], "new",
        profile([1] + [0] * 9, [1] * 10),
        STPSConfig(d_max=0, objective="balance", balance_slack=0.10),
    )
    assert (decision.card_id, decision.J) == (0, 10)
    rejected = next(row for row in decision.candidates if row["card_id"] == 1)
    assert rejected["J"] == 12
    assert rejected["balance_primary"] == 0
    assert rejected["balance_admissible"] is False
    assert math.isclose(rejected["balance_j_limit"], 11)


def test_balance_still_searches_card_and_delay_jointly():
    first = state(
        tasks=(ForecastTask("old0", profile([6, 0]), (0, 3), 0, 1, False),),
        assigned_compute=20, assigned_noc=24,
    )
    second = state(
        card_id=1,
        tasks=(ForecastTask("old1", profile([1, 6]), (0, 3), 0, 1, False),),
    )
    decision = choose_stps(
        [first, second], "new", profile([6, 0], [10, 0]),
        STPSConfig(d_max=3, objective="balance", balance_slack=1.0),
    )
    assert (decision.card_id, decision.delay, decision.J) == (1, 2, 4)
    assert min(row["J"] for row in decision.candidates) == 3
    assert decision.predicted_actual_start == 3


def test_projected_cv_is_safe_for_zero_loads_and_includes_empty_cards():
    decision = choose_stps(
        [state(card_id=4), state(card_id=2)], "zero", profile([0], [0]),
        STPSConfig(d_max=0, objective="balance"),
    )
    assert decision.card_id == 2
    assert decision.projected_compute_cv == decision.projected_noc_cv == 0

    nonzero = choose_stps(
        [state(card_id=0), state(card_id=1)], "work", profile([5], [10]),
        STPSConfig(d_max=0, objective="balance"),
    )
    assert nonzero.projected_compute_cv == nonzero.projected_noc_cv == 1


def test_compute_and_noc_balance_weights_are_independent():
    cards = [
        state(card_id=0, assigned_compute=10, assigned_noc=0),
        state(card_id=1, assigned_compute=0, assigned_noc=10),
    ]
    task = profile([5], [10])
    favor_noc = choose_stps(
        cards, "new", task,
        STPSConfig(d_max=0, objective="balance", compute_weight=0.1),
    )
    favor_compute = choose_stps(
        cards, "new", task,
        STPSConfig(d_max=0, objective="balance", noc_weight=0.1),
    )
    assert favor_noc.card_id == 0
    assert (favor_noc.projected_compute_cv, favor_noc.projected_noc_cv) == (1, 0)
    assert favor_compute.card_id == 1
    assert (favor_compute.projected_compute_cv, favor_compute.projected_noc_cv) == (0, 1)


def test_infeasible_card_is_not_a_candidate_but_contributes_to_cluster_cv():
    feasible = state(card_id=0)
    unavailable = state(card_id=1, mapping=(), assigned_compute=20, assigned_noc=20)
    unavailable = replace(unavailable, candidate_feasible=False)
    decision = choose_stps(
        [feasible, unavailable], "new", profile([5], [10]),
        STPSConfig(d_max=0, objective="balance"),
    )
    assert decision.card_id == 0 and decision.candidate_count == 1
    assert {row["card_id"] for row in decision.candidates} == {0}
    assert math.isclose(decision.projected_compute_cv, 1 / 3)
    assert math.isclose(decision.projected_noc_cv, 1 / 3)


def test_gamma_is_used_for_new_round_and_observed_residual():
    decision = choose_stps([state(k=3)], "new", profile([1]), STPSConfig(d_max=0, gamma=2))
    assert decision.predicted_completion == 2  # near pair: ceil(2*3 / K=3)
    running = ForecastTask("old", profile([100]), (0, 7), 1, 1, True)
    card = state(tasks=(running,), k=1, tick=2, round_open=True,
                 outstanding=(row("router", router=7),))
    decision = choose_stps([card], "new", profile([0]), STPSConfig(d_max=0, gamma=2))
    assert decision.predicted_actual_start == 4


def test_sink_service_uses_global_phase_and_does_not_loop_on_slow_service():
    noc = NoCConfig(4, 2, sink_buffer_depth=1, sink_service_period=100)
    card = state(k=1, tick=100, noc=noc,
                 outstanding=(row("sink_ni", src=6, dst=2, router=2),))
    decision = choose_stps([card], "new", profile([2]), STPSConfig(d_max=0, max_rounds=1))
    # L includes P*(2 arrivals - 0 free) = 200. Even very slow reception is
    # computed in one aggregate round, rather than cycling/expanding flits.
    assert decision.predicted_completion == 299
    assert decision.peak_noc == 4


def test_forecast_guard_fails_instead_of_returning_partial_candidate():
    with pytest.raises(ValueError, match="max_rounds"):
        choose_stps([state()], "new", profile([0, 0]), STPSConfig(max_rounds=1))


def test_forecasting_does_not_mutate_observed_inventory_or_profiles():
    observed = row("sink_ni", dst=2, router=2)
    card = state(outstanding=(observed,))
    task = profile([2, 0], [7, 0])
    before = task.expected_edge_flits.copy()
    first = choose_stps([card], "new", task)
    second = choose_stps([card], "new", task)
    assert first == second
    assert observed["count"] == card.outstanding[0]["count"] == 1
    np.testing.assert_array_equal(task.expected_edge_flits, before)


@pytest.mark.parametrize("kwargs", [
    {"d_max": -1}, {"d_max": True}, {"gamma": 0.9}, {"gamma": float("nan")},
    {"max_rounds": 0}, {"objective": "unknown"}, {"objective": 1},
    {"balance_slack": -0.1}, {"balance_slack": float("inf")},
    {"compute_weight": 0}, {"compute_weight": True},
    {"noc_weight": -1}, {"noc_weight": float("nan")},
])
def test_invalid_predictor_parameters_fail_early(kwargs):
    with pytest.raises(ValueError):
        STPSConfig(**kwargs)


def test_config_and_forecast_snapshots_are_frozen_and_validate_balance_fields():
    config = STPSConfig(objective="balance")
    with pytest.raises(FrozenInstanceError):
        config.balance_slack = 1
    with pytest.raises(ValueError, match="cumulative_assigned_compute_sops"):
        state(assigned_compute=-1)
    with pytest.raises(ValueError, match="candidate_feasible"):
        replace(state(), candidate_feasible=1)
    with pytest.raises(ValueError, match="empty mapping"):
        replace(state(), candidate_feasible=False)


def test_invalid_snapshot_and_inventory_are_rejected():
    with pytest.raises(ValueError, match="currently free"):
        state(tasks=(ForecastTask("old", profile([1]), (0, 1), 0, 1, False),))
    with pytest.raises(ValueError, match="open card round"):
        choose_stps([state(outstanding=(row("source_ni"),))], "new", profile([0]))
    with pytest.raises(ValueError, match="same current physical Tick|one current physical Tick"):
        choose_stps([state(), state(card_id=1, tick=2)], "new", profile([0]))
    with pytest.raises(ValueError, match="unique"):
        choose_stps([state(), state()], "new", profile([0]))
    with pytest.raises(ValueError, match="one distinct core"):
        choose_stps([replace(state(), mapping=(0,))], "new", profile([0]))
