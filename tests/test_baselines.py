from dataclasses import replace

import pytest

from schedule.baselines import POLICIES, BaselinePolicy, CardSnapshot


def card(card_id, *, used_cores=0, used_memory=0, memory=16,
         compute=0, noc=0, compute_budget=100, noc_budget=200):
    return CardSnapshot(card_id, 16, used_cores, memory, used_memory,
                        compute, noc, compute_budget, noc_budget)


def choose(policy, candidates, *, cores=2, memory=1, compute=10, noc=20):
    return policy.select(candidates, cores, memory, compute, noc)


def test_rr_full_cycle_sorted_independent_of_input_order():
    policy = BaselinePolicy("RR", card_count=4)
    cards = [card(i) for i in (3, 1, 2, 0)]
    assert [choose(policy, cards) for _ in range(9)] == [0, 1, 2, 3, 0, 1, 2, 3, 0]


def test_rr_skips_missing_cards_and_failure_does_not_advance():
    policy = BaselinePolicy("RR", card_count=4)
    assert choose(policy, [card(0), card(2)]) == 0
    assert choose(policy, []) is None
    assert policy.last_decision["selected_card_id"] is None
    assert choose(policy, [card(0), card(2)]) == 2
    assert choose(policy, [card(0), card(2)]) == 0
    assert choose(policy, [card(3)]) == 3
    assert choose(policy, [card(1), card(0)]) == 0


def test_rr_without_card_count_handles_sparse_ids():
    policy = BaselinePolicy("RR")
    cards = [card(7), card(2)]
    assert [choose(policy, cards) for _ in range(4)] == [2, 7, 2, 7]


def test_worstfit_and_bestfit_remaining_cores_take_priority_over_memory():
    cards = [card(0, used_cores=4, memory=10, used_memory=9),
             card(1, used_cores=5, memory=100)]
    worst = BaselinePolicy("WorstFit")
    best = BaselinePolicy("BestFit")
    assert choose(worst, cards) == 0
    assert choose(best, cards) == 1
    assert worst.last_decision["scores"] == {0: (10, 0), 1: (9, 99)}


def test_remaining_memory_is_secondary_capacity_score():
    cards = [card(4, used_cores=4, used_memory=8), card(2, used_cores=4, used_memory=5)]
    assert choose(BaselinePolicy("WorstFit"), cards) == 2
    assert choose(BaselinePolicy("BestFit"), cards) == 4


def test_dru_normalizes_both_resources_and_uses_post_placement_load():
    cards = [card(0, used_memory=9, memory=10), card(1, used_cores=4, memory=10)]
    policy = BaselinePolicy("DRU")
    assert choose(policy, cards) == 1
    assert policy.last_decision["scores"] == {0: 1.0, 1: 0.375}


@pytest.mark.parametrize("name", POLICIES)
def test_every_policy_breaks_exact_score_ties_by_smallest_card_id(name):
    policy = BaselinePolicy(name, card_count=4)
    assert choose(policy, [card(3), card(1)]) == 1


def test_p2c_compute_and_communication_are_independent_normalized_pressures():
    policy = BaselinePolicy("P2C-Mean", seed=21)
    cards = [card(0, compute=70, noc=0), card(1, compute=0, noc=150)]
    assert choose(policy, cards, compute=10, noc=30) == 0
    assert policy.last_decision["scores"] == {0: 0.8, 1: 0.9}
    assert choose(policy, cards, compute=30, noc=0) == 1
    assert policy.last_decision["scores"] == {0: 1.0, 1: 0.75}


def test_p2c_uses_supplied_card_budgets():
    policy = BaselinePolicy("P2C-Mean")
    cards = [card(0, compute=50, compute_budget=50),
             card(1, compute=70, compute_budget=200)]
    assert choose(policy, cards) == 1
    assert policy.last_decision["scores"] == {0: 1.2, 1: 0.4}


def test_p2c_reproducible_isolated_rng_and_no_replacement():
    policies = [BaselinePolicy("P2C-Mean", seed=17) for _ in range(2)]
    cards = [card(i, compute=i * 10, noc=(3 - i) * 20) for i in range(4)]
    sequences = []
    for policy in policies:
        sequence = []
        for _ in range(20):
            choose(policy, list(reversed(cards)))
            sample = policy.last_decision["sampled_card_ids"]
            assert len(sample) == len(set(sample)) == 2
            sequence.append((sample[:], policy.last_decision["selected_card_id"]))
        sequences.append(sequence)
    assert sequences[0] == sequences[1]


def test_p2c_no_candidates_or_single_candidate_does_not_consume_rng():
    a = BaselinePolicy("P2C-Mean", seed=11)
    b = BaselinePolicy("P2C-Mean", seed=11)
    assert choose(a, []) is None
    assert a.last_decision["scores"] == {}
    assert choose(a, [card(9)]) == 9
    cards = [card(i) for i in range(4)]
    assert choose(a, cards) == choose(b, cards)
    assert a.last_decision == b.last_decision


@pytest.mark.parametrize("name", ["RR", "WorstFit", "BestFit", "DRU"])
def test_capacity_policies_are_independent_of_compute_and_noc_mean(name):
    cards = [card(0, used_cores=8), card(1, used_cores=2)]
    changed = [replace(c, mean_compute_sops=10000, mean_noc_endpoint=100000)
               if c.card_id == 1 else c for c in cards]
    assert choose(BaselinePolicy(name), cards) == choose(BaselinePolicy(name), changed)


def test_p2c_keeps_planned_load_even_without_current_successful_throughput():
    # Snapshots have no successful-Tx/Rx field: a blocked card's retained mean
    # workload is still pressure, even when it cannot inject anything now.
    cards = [card(0, compute=10, noc=190), card(1, compute=40, noc=30)]
    policy = BaselinePolicy("P2C-Mean")
    assert choose(policy, cards) == 1
    assert policy.last_decision["scores"] == {0: 1.05, 1: 0.5}


@pytest.mark.parametrize("name", POLICIES)
def test_empty_candidates_returns_none_and_logs_empty_decision(name):
    policy = BaselinePolicy(name)
    assert choose(policy, []) is None
    assert policy.last_decision == {
        "policy": name, "eligible_card_ids": [], "sampled_card_ids": [],
        "scores": {}, "selected_card_id": None,
    }


def test_invalid_policy_snapshot_and_ineligible_candidate_rejected():
    with pytest.raises(ValueError, match="Unknown baseline"):
        BaselinePolicy("legacy-DRF")
    with pytest.raises(ValueError, match="budget"):
        card(0, compute_budget=0)
    with pytest.raises(ValueError, match="used_cores"):
        card(0, used_cores=17)
    with pytest.raises(ValueError, match="finite"):
        card(0, noc=float("nan"))
    policy = BaselinePolicy("DRU", card_count=4)
    with pytest.raises(ValueError, match="unique"):
        choose(policy, [card(1), card(1)])
    with pytest.raises(ValueError, match="exceeds card_count"):
        choose(policy, [card(4)])
    with pytest.raises(ValueError, match="not resource-feasible"):
        choose(policy, [card(0, used_cores=15)])
    with pytest.raises(ValueError, match="not resource-feasible"):
        choose(policy, [card(0, used_memory=16)])
