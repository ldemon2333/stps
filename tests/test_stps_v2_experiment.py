"""Protect frozen-source reuse, validation selection, and seven-policy analysis."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil

import pytest

from script.compare_stps_v2 import (
    ALL_POLICIES, BALANCE_SLACK_GRID, E2E_LIMIT_FACTOR, SOURCE_ARTIFACT,
    TARGET_POLICY, TRADITIONAL_BASELINES, V1_POLICY, import_legacy_rows,
    prepare_test_scene, replay_identity, select_balance_slack, sha256,
    summarize_v2, verify_source_artifact,
)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_source_artifact_verification_rejects_mutated_or_missing_input(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "plan.json", {"plan": 1})
    _write_json(source / "raw.csv", {"raw": 1})
    _write_json(source / "summary.json", {"summary": 1})
    payload = source / "inputs/scene.json"
    _write_json(payload, {"scene": 1})
    _write_json(source / "input_hashes.json", {"inputs/scene.json": sha256(payload)})
    expected = {name: sha256(source / name) for name in (
        "plan.json", "input_hashes.json", "raw.csv", "summary.json")}

    result = verify_source_artifact(source, expected_sha256=expected, expected_input_count=1)
    assert result["all_input_hashes_verified"] is True
    payload.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or changed"):
        verify_source_artifact(source, expected_sha256=expected, expected_input_count=1)
    payload.unlink()
    with pytest.raises(ValueError, match="missing or changed"):
        verify_source_artifact(source, expected_sha256=expected, expected_input_count=1)


def _validation_row(mode, seed, *, slack=None, e2e=10.0, compute=.2, noc=.3, valid=True):
    return {
        "arrival_mode": mode, "seed": seed, "balance_slack": slack,
        "status": "completed" if valid else "max_ticks", "valid": valid,
        "tasks_total": 24, "tasks_completed": 24 if valid else 23,
        "mean_task_end_to_end_ticks": e2e,
        "full_compute_sops_cv": compute, "full_endpoint_events_cv": noc,
    }


def test_slack_selection_uses_each_scene_gate_balance_score_and_smaller_tie():
    completion = [_validation_row(mode, seed, e2e=10.0)
                  for seed in (9001, 9002) for mode in ("poisson", "bursty")]
    candidates = []
    scores = {0.0: .30, .05: .20, .10: .20, .20: .10}
    for slack in BALANCE_SLACK_GRID:
        for seed in (9001, 9002):
            for mode in ("poisson", "bursty"):
                # The globally best balance candidate violates one per-scene
                # latency gate and is therefore infeasible.
                e2e = 10 * E2E_LIMIT_FACTOR
                if slack == .20 and seed == 9002 and mode == "bursty":
                    e2e += .001
                candidates.append(_validation_row(
                    mode, seed, slack=slack, e2e=e2e,
                    compute=scores[slack], noc=scores[slack] - .01))
    result = select_balance_slack(completion, candidates)
    assert result["selected_balance_slack"] == .05
    entries = {entry["balance_slack"]: entry for entry in result["candidates"]}
    assert entries[.20]["feasible"] is False
    assert entries[.20]["latency_gate_scenes"] == 3
    assert entries[.05]["selected"] is True
    assert entries[.10]["selected"] is False


def test_test_scene_changes_only_stps_and_keeps_replay_identity(tmp_path):
    source = tmp_path / "source"
    scene_dir = source / "inputs/tasks_1/seed_1"
    workloads = scene_dir / "workloads"
    workloads.mkdir(parents=True)
    profile = source / "profiles/unit.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("profile bytes", encoding="utf-8")
    workload = workloads / "task_0000.json"
    workload.write_text("workload bytes", encoding="utf-8")
    scene = {
        "schema_version": 1, "name": "unit",
        "cluster": {"cards": 4}, "noc": {"cycles_per_tick": 8},
        "max_ticks": 20, "steady_window": [1, 5], "scheduler_seed": 1,
        "tasks": [{
            "task_id": "task-0000", "arrival_tick": 1,
            "workload": "workloads/task_0000.json",
            "profile": str(profile.resolve()),
            "mean_compute_sops": 1.0, "mean_noc_endpoint": 2.0,
        }],
        "stps": {"d_max": 4, "gamma": 1.4, "max_rounds": 10000},
        "metadata": {"source": "unit"},
    }
    source_scene = scene_dir / "poisson.json"
    _write_json(source_scene, scene)

    output = tmp_path / "output"
    target = prepare_test_scene(source_scene, source, output, .10)
    before, after = json.loads(source_scene.read_text()), json.loads(target.read_text())
    before_stps, after_stps = before.pop("stps"), after.pop("stps")
    assert before == after
    assert after_stps == {**before_stps, "objective": "balance", "balance_slack": .10}
    assert replay_identity(source_scene) == replay_identity(target)
    assert (target.parent / "workloads").resolve() == workloads.resolve()

    after = json.loads(target.read_text())
    after["tasks"][0]["arrival_tick"] = 2
    _write_json(target, after)
    assert replay_identity(source_scene) != replay_identity(target)


def test_imported_frozen_matrix_renames_only_stps_and_rewrites_paths(tmp_path):
    source = tmp_path / "v1"
    output = tmp_path / "v2/run"
    source.mkdir()
    output.mkdir(parents=True)
    policies = [*TRADITIONAL_BASELINES, "STPS"]
    rows = []
    for policy in policies:
        folder = source / "runs" / policy
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text("{}", encoding="utf-8")
        report = ""
        if policy == "STPS":
            (folder / "report.html").write_text("report", encoding="utf-8")
            report = f"runs/{policy}/report.html"
        rows.append({
            "arrival_mode": "poisson", "task_count": 24, "seed": 11,
            "policy": policy, "status": "completed", "valid": "True",
            "scenario_sha256": "digest",
            "manifest": f"runs/{policy}/manifest.json", "report": report,
            "mean_task_end_to_end_ticks": "10.0",
        })
    with (source / "raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    plan = {
        "arrival_modes": ["poisson"], "task_counts": [24], "seeds": [11],
        "policies": policies,
    }
    imported = import_legacy_rows(source, output, plan, {("poisson", 24, 11): "replay"})
    assert [row["policy"] for row in imported] == [*TRADITIONAL_BASELINES, V1_POLICY]
    assert all((output / row["manifest"]).resolve().is_file() for row in imported)
    assert imported[-1]["report"] and (output / imported[-1]["report"]).resolve().is_file()
    assert all(row["report"] == "" for row in imported[:-1])
    assert all(row["replay_input_sha256"] == "replay" for row in imported)


def _analysis_row(policy, seed, cv, e2e):
    return {
        "arrival_mode": "poisson", "task_count": 24, "seed": seed,
        "policy": policy, "valid": True, "full_endpoint_events_cv": cv,
        "full_compute_sops_cv": cv, "mean_task_end_to_end_ticks": e2e,
    }


def test_summary_has_seven_policies_and_excludes_v1_from_best_baseline():
    rows = []
    for seed in (11, 23):
        values = {
            "RR": .30, "WorstFit": .40, "DRU": .35,
            "BestFit": .25, "P2C-Mean": .32, V1_POLICY: .05, TARGET_POLICY: .20,
        }
        rows.extend(_analysis_row(policy, seed, cv, 10 + cv) for policy, cv in values.items())
    summary = summarize_v2(rows)
    assert summary["runs"] == 14
    assert summary["policies"] == list(ALL_POLICIES)
    assert {group["policy"] for group in summary["groups"]} == set(ALL_POLICIES)
    best = next(entry for entry in summary["versus_best_traditional_baseline"]
                if entry["metric"] == "full_endpoint_events_cv")
    assert best["best_baseline"] == "BestFit"
    assert best["mean_improvement"] > 0
    v1 = next(entry for entry in summary["versus_stps_v1"]
              if entry["metric"] == "full_endpoint_events_cv")
    assert v1["comparator"] == V1_POLICY
    assert v1["mean_improvement"] < 0


def test_real_frozen_source_identity_is_current():
    result = verify_source_artifact(SOURCE_ARTIFACT)
    assert result["input_hash_entries"] == 435
    assert result["all_input_hashes_verified"] is True
