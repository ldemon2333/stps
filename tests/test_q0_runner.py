from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from fingerprint import load_fingerprint
from util.card import Card
from util.sim import create_task

import script.q0_run as q0_run


def test_q0_staged_synthetic_fingerprints_fit_default_card(tmp_path):
    fp_dir = Path(q0_run._build_synthetic_fp_dir(tmp_path / "q0_fp"))
    card = Card(card_id=0)

    paths = sorted(fp_dir.glob("*.npz"))
    assert paths

    for task_id, path in enumerate(paths):
        fp = load_fingerprint(path)
        task = create_task(task_id, arrival_step=1, fingerprint=fp)

        assert fp.neuron_count == 2_000_000
        assert card.can_host(task), (
            path.name,
            task.neuron_count,
            card.neuron_capacity,
            task.memory_gb_required,
        )


def test_q0_scale16_mixed_fingerprints_include_real_models(tmp_path):
    fp_dir = Path(q0_run._build_mixed_fp_dir(tmp_path / "q0_mixed"))
    names = {p.name for p in fp_dir.glob("*.npz")}

    assert "synthetic_flat.npz" in names
    assert "spikformer_cifar10.npz" in names
    assert "qkformer_cifar10.npz" in names
    assert "spikingresformer_ti_imagenet.npz" in names

    card = Card(card_id=0)
    for task_id, path in enumerate(sorted(fp_dir.glob("*.npz"))):
        fp = load_fingerprint(path)
        task = create_task(task_id, arrival_step=1, fingerprint=fp)
        assert card.can_host(task), (path.name, task.neuron_count, card.neuron_capacity)


def test_q0_runner_reports_end_to_end_guardrail_metrics():
    assert "completion_rate" in q0_run.METRIC_KEYS
    assert "throughput" in q0_run.METRIC_KEYS
    assert "p99_delay" in q0_run.METRIC_KEYS


def test_q0_runner_cli_imports_from_repo_root():
    result = subprocess.run(
        [sys.executable, "script/q0_run.py", "--help"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "{main,arrival,sweep,scale16,all}" in result.stdout
