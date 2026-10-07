"""Hand-counted data-path checks for the phase-one NoC model."""

from collections import Counter, defaultdict

import pytest

from simulation.noc import NoCConfig, NoCNetwork


def offer(network, task, src, dst, count=1, cycle=0, tick=0, edge=0):
    network.offer(task, tick, edge, src, dst, count, cycle)


def totals(events):
    result = Counter()
    for event in events:
        result[event["kind"]] += event["count"]
    return result


def assert_conserved(network, generated, events):
    counts = totals(events)
    inventory = Counter()
    for row in network.outstanding():
        inventory[row["kind"]] += row["count"]
    assert generated == counts["tx"] + inventory["source_pending"] + inventory["source_ni"]
    assert counts["tx"] == counts["rx"] + inventory["router"]
    assert counts["rx"] == counts["consume"] + inventory["sink_ni"]
    assert generated == counts["consume"] + sum(inventory.values())
    assert network.pending_delivery() == generated - counts["rx"]
    for row in network.occupancy():
        assert row["occupancy"] >= 0
        if row["capacity"] is not None:
            assert row["occupancy"] <= row["capacity"]


@pytest.mark.parametrize("src,dst,path", [
    (0, 1, [(0, "E", 1)]),
    (0, 5, [(0, "E", 1), (1, "E", 2), (2, "S", 5)]),
    (5, 0, [(5, "W", 4), (4, "W", 3), (3, "N", 0)]),
])
def test_xy_latency_and_no_same_cycle_multi_hop(src, dst, path):
    network = NoCNetwork(NoCConfig(3, 2))
    offer(network, "task", src, dst)
    events = []
    for cycle in range(len(path) + 3):
        step = network.advance_cycle(cycle)
        events.extend(step)
        assert_conserved(network, 1, events)
    links = [e for e in events if e["kind"] == "link"]
    assert [(e["router"], e["port"], e["next_router"]) for e in links] == path
    assert [e["cycle"] for e in links] == list(range(2, len(path) + 2))
    tx = next(e for e in events if e["kind"] == "tx")
    enqueued = next(e for e in events if e["kind"] == "source_enqueue")
    rx = next(e for e in events if e["kind"] == "rx")
    consumed = next(e for e in events if e["kind"] == "consume")
    assert tx["cycle"] == tx["wait_cycles"] == 1
    assert enqueued["cycle"] == enqueued["generated_cycle"] == 0
    assert rx["cycle"] == rx["wait_cycles"] == len(path) + 2
    assert consumed["cycle"] == len(path) + 3
    assert totals(events)["router_wait"] == 0
    assert network.outstanding() == []


def test_round_robin_contested_output_and_transfer_limits():
    network = NoCNetwork(NoCConfig(3, 3, router_buffer_depth=8))
    offer(network, "north", 1, 7, count=4)
    offer(network, "west", 3, 7, count=4)
    events = []
    for cycle in range(20):
        step = network.advance_cycle(cycle)
        events.extend(step)
        outputs = [(e["router"], e["port"]) for e in step if e["kind"] in {"link", "rx"}]
        assert len(outputs) == len(set(outputs))
        assert_conserved(network, 8, events)
    arbitration = [e for e in events if e["kind"] == "link" and e["router"] == 4]
    assert [e["task_id"] for e in arbitration] == ["north", "west"] * 4
    assert [e["cycle"] for e in arbitration] == list(range(3, 11))
    waits = [e for e in events if e["kind"] == "router_wait"]
    assert waits
    assert totals(events)["tx_stall"] == 0
    assert all(e["reason"] == "arbitration" for e in waits)
    assert all(e["router"] == 4 and e["requested_output"] == "S" for e in waits)
    assert all(e["input_port"] == e["port"] for e in waits)
    assert (waits[0]["task_id"], waits[0]["cycle"], waits[0]["port"]) == ("west", 3, "W")
    keys = [(e["cycle"], e["router"], e["input_port"]) for e in waits]
    assert len(keys) == len(set(keys))
    assert totals(events)["consume"] == 8


def test_finite_queues_backpressure_source_stalls_and_conservation():
    network = NoCNetwork(NoCConfig(2, 1, router_buffer_depth=1,
                                  source_buffer_depth=1, sink_buffer_depth=1,
                                  sink_service_period=5))
    offer(network, "burst", 0, 1, count=8)
    assert {row["kind"]: row["count"] for row in network.outstanding()} == {
        "source_pending": 7, "source_ni": 1,
    }
    events = []
    for cycle in range(60):
        events.extend(network.advance_cycle(cycle))
        assert_conserved(network, 8, events)
        if not network.outstanding():
            break
    counts = totals(events)
    assert counts["source_enqueue"] == counts["tx"] == counts["rx"] == counts["consume"] == 8
    assert counts["tx_stall"] > 0
    assert counts["rx_blocked"] > 0
    waits = [e for e in events if e["kind"] == "router_wait"]
    assert waits
    assert all(e["reason"] == "downstream_full" for e in waits)
    keys = [(e["cycle"], e["router"], e["input_port"]) for e in waits]
    assert len(keys) == len(set(keys))
    assert all(e["cycle"] % 5 == 0 for e in events if e["kind"] == "consume")
    assert network.outstanding() == []


def test_rx_satisfies_delivery_before_sink_consumption_and_retains_task_identity():
    network = NoCNetwork(NoCConfig(2, 1, sink_buffer_depth=1, sink_service_period=10))
    offer(network, "finished_task", 0, 1, tick=3, edge=9)
    events = []
    for cycle in range(3):
        events.extend(network.advance_cycle(cycle))
    assert totals(events)["rx"] == 1
    assert totals(events)["consume"] == 0
    assert network.pending_delivery() == 0
    pending = network.outstanding()
    assert len(pending) == 1
    assert pending[0]["kind"] == "sink_ni"
    assert pending[0]["task_id"] == "finished_task"
    assert pending[0]["logical_tick"] == 3
    assert pending[0]["edge_id"] == 9
    # Reusing the endpoint never changes ownership of its queued old flit.
    offer(network, "new_task", 0, 1, cycle=3)
    for cycle in range(3, 21):
        events.extend(network.advance_cycle(cycle))
        assert_conserved(network, 2, events)
    assert [e["task_id"] for e in events if e["kind"] == "consume"] == ["finished_task", "new_task"]


def test_full_sink_cannot_use_a_slot_freed_in_the_same_cycle():
    network = NoCNetwork(NoCConfig(2, 1, sink_buffer_depth=1, sink_service_period=4))
    offer(network, "task", 0, 1, count=2)
    events = []
    for cycle in range(9):
        events.extend(network.advance_cycle(cycle))
    assert [e["cycle"] for e in events if e["kind"] == "rx"] == [3, 5]
    assert [e["cycle"] for e in events if e["kind"] == "consume"] == [4, 8]
    assert [e["cycle"] for e in events if e["kind"] == "rx_blocked"] == [4]
    waits = [e for e in events if e["kind"] == "router_wait"]
    assert len(waits) == 1
    assert (waits[0]["cycle"], waits[0]["router"], waits[0]["input_port"],
            waits[0]["requested_output"], waits[0]["reason"]) == (4, 1, "W", "Local", "downstream_full")


def test_full_downstream_cannot_use_a_router_slot_freed_in_same_cycle():
    network = NoCNetwork(NoCConfig(2, 1, router_buffer_depth=1))
    offer(network, "task", 0, 1, count=2)
    events = []
    for cycle in range(7):
        events.extend(network.advance_cycle(cycle))
    assert [e["cycle"] for e in events if e["kind"] == "tx"] == [1, 3]
    assert [e["cycle"] for e in events if e["kind"] == "rx"] == [3, 5]
    assert [e["cycle"] for e in events if e["kind"] == "tx_stall"] == [2]


def test_pending_requests_are_lazy_even_for_huge_bursts():
    network = NoCNetwork(NoCConfig(2, 1, source_buffer_depth=2))
    offer(network, "huge", 0, 1, count=10**12)
    rows = network.outstanding()
    assert len(rows) == 2
    assert sum(row["count"] for row in rows) == 10**12
    assert next(row["count"] for row in rows if row["kind"] == "source_pending") == 10**12 - 2
    events = network.advance_cycle(0)
    assert_conserved(network, 10**12, events)
    assert network.pending_delivery() == 10**12


def test_source_enqueue_uses_actual_boundary_for_offer_and_refill():
    network = NoCNetwork(NoCConfig(2, 1, source_buffer_depth=1))
    offer(network, "task", 0, 1, count=3)
    events = []
    for cycle in range(2):
        events.extend(network.advance_cycle(cycle))
    enqueues = [e for e in events if e["kind"] == "source_enqueue"]
    assert [e["cycle"] for e in enqueues] == [0, 1, 2]
    assert all(e["generated_cycle"] == 0 for e in enqueues)
    assert all(e["count"] == 1 for e in enqueues)


def test_fifo_at_source_and_per_task_per_tick_conservation():
    network = NoCNetwork(NoCConfig(3, 1, source_buffer_depth=1))
    offer(network, "a", 0, 2, count=3, tick=0)
    offer(network, "b", 0, 1, count=2, tick=1, edge=3)
    events = []
    for cycle in range(20):
        events.extend(network.advance_cycle(cycle))
        by_identity = defaultdict(Counter)
        for event in events:
            key = (event["task_id"], event["logical_tick"], event["edge_id"])
            by_identity[key][event["kind"]] += event["count"]
        inventory = Counter()
        for row in network.outstanding():
            inventory[(row["task_id"], row["logical_tick"], row["edge_id"])] += row["count"]
        for key, count in [( ("a", 0, 0), 3), (("b", 1, 3), 2)]:
            assert count == by_identity[key]["consume"] + inventory[key]
    assert [e["task_id"] for e in events if e["kind"] == "tx"] == ["a"] * 3 + ["b"] * 2


def test_determinism_for_competing_multi_task_traffic():
    def run():
        network = NoCNetwork(NoCConfig(3, 3, router_buffer_depth=2,
                                      source_buffer_depth=2, sink_buffer_depth=2))
        offer(network, "a", 0, 8, count=9)
        offer(network, "b", 2, 6, count=5)
        offer(network, "c", 3, 8, count=7)
        events = []
        snapshots = []
        for cycle in range(80):
            events.extend(network.advance_cycle(cycle))
            assert_conserved(network, 21, events)
            snapshots.append(network.outstanding())
        assert network.outstanding() == []
        return events, snapshots
    assert run() == run()


@pytest.mark.parametrize("field", ["mesh_x", "mesh_y", "router_buffer_depth",
                                    "source_buffer_depth", "sink_buffer_depth", "sink_service_period"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_invalid_config(field, value):
    kwargs = {"mesh_x": 2, "mesh_y": 2, field: value}
    with pytest.raises(ValueError):
        NoCConfig(**kwargs)


def test_invalid_edges_counts_and_cycle_order():
    network = NoCNetwork(NoCConfig(2, 1))
    for src, dst, count in [(0, 0, 1), (-1, 1, 1), (0, 2, 1), (0, 1, -1), (0, 1, 1.2)]:
        with pytest.raises(ValueError):
            offer(network, "task", src, dst, count)
    offer(network, "zero", 0, 1, 0)
    assert network.outstanding() == []
    assert network.advance_cycle(0) == []
    with pytest.raises(ValueError):
        network.advance_cycle(0)
    with pytest.raises(ValueError):
        offer(network, "past", 0, 1, cycle=0)
    with pytest.raises(ValueError):
        offer(network, "future", 0, 1, cycle=2)
