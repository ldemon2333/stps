from __future__ import annotations

from util.card import Card
from util.task import Task


def test_default_card_hosts_task_up_to_neuron_capacity_with_128gb_memory():
    card = Card(card_id=0)
    task = Task(
        task_id=1,
        state_size_mb=32.0,
        neuron_count=16_777_216,
        complexity_ratio=1.0,
        arrival_step=1,
    )

    assert card.neuron_capacity == 16_777_216
    assert card.memory_gb == 128.0
    assert card.can_host(task)


def test_default_card_rejects_task_above_neuron_capacity():
    card = Card(card_id=0)
    task = Task(
        task_id=1,
        state_size_mb=32.0,
        neuron_count=16_777_217,
        complexity_ratio=1.0,
        arrival_step=1,
    )

    assert not card.can_host(task)
