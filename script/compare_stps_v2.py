#!/usr/bin/env python3
"""Tune STPS-Balance on frozen validation traces, then extend the frozen v1 test.

The source artifact is immutable and identified by fixed hashes.  This driver
does not regenerate profiles, workloads, gamma, baselines, or STPS-v1.  It
selects ``balance_slack`` only on the source validation split, runs one new
STPS-Balance replay for each frozen test scene, and rebuilds the seven-policy
analysis from the 120 historical rows plus 20 new rows.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path, PurePosixPath
import statistics
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fingerprint.scheduling import load_fingerprint
from schedule.baselines import POLICIES as TRADITIONAL_BASELINES
from script.compare_stps import (
    COMPARISON_METRICS, KEY_METRICS, higher_is_better, run_row, sha256,
    write_csv, write_json,
)
from simulation.arrivals import make_arrival_ticks
from simulation.cluster_engine import run_cluster_simulation
from simulation.cluster_scenario import load_cluster_scenario


SOURCE_ARTIFACT = ROOT / "data/stps_comparison/20261007T083540_810669Z"
FROZEN_SOURCE_SHA256 = {
    "plan.json": "2fa6e4a329aac278bb117d783ab0f54d8e95ffe2bfbc74fa1533dcf44d3841e5",
    "input_hashes.json": "ad746e23449761010dc610e74cb4a9bb78fec29aea0332250768e11807ddaf84",
    "raw.csv": "0fef0c071a7c919d67f5f761def93a51e7359bf0a52e485d3f372391e3e6d9bf",
    "summary.json": "e4d3807b56e50f485238bc44af300ea4ae8059b41906f660967d444b876e8637",
}
FROZEN_INPUT_COUNT = 435
VALIDATION_SEEDS = (9001, 9002)
ARRIVAL_MODES = ("poisson", "bursty")
VALIDATION_TASK_COUNT = 24
BALANCE_SLACK_GRID = (0.0, 0.05, 0.10, 0.20)
E2E_LIMIT_FACTOR = 1.05
TEMPLATES = ("light_sparse", "compute_heavy", "fan_in", "wide_burst")
V1_POLICY = "STPS-v1"
TARGET_POLICY = "STPS-Balance"
ALL_POLICIES = (*TRADITIONAL_BASELINES, V1_POLICY, TARGET_POLICY)
IMPLEMENTATION_FILES = (
    "script/compare_stps_v2.py", "schedule/stps.py",
    "simulation/cluster_engine.py", "simulation/cluster_scenario.py",
    "simulation/card_runtime.py", "simulation/noc.py",
    "simulation/cluster_metrics.py", "fingerprint/scheduling.py",
)
TEXT_FIELDS = {
    "arrival_mode", "policy", "status", "scenario_sha256",
    "source_scenario_sha256", "replay_input_sha256", "manifest",
    "report", "row_origin", "source_artifact", "source_policy",
    "objective",
}
BOOL_FIELDS = {"valid", "full_complete", "steady_complete"}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_relative_path(value: str, *, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"{label} must be a safe artifact-relative path: {value!r}")
    return path


def verify_source_artifact(
    source: Path, *, expected_sha256: dict[str, str] = FROZEN_SOURCE_SHA256,
    expected_input_count: int | None = FROZEN_INPUT_COUNT,
) -> dict:
    """Fail before execution if any frozen top-level or input byte changed."""
    source = source.resolve()
    actual = {}
    for name, expected in expected_sha256.items():
        path = source / name
        if not path.is_file():
            raise ValueError(f"frozen source file is missing: {path}")
        actual[name] = sha256(path)
        if actual[name] != expected:
            raise ValueError(f"frozen source hash mismatch for {name}")
    hashes = _json(source / "input_hashes.json")
    if not isinstance(hashes, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in hashes.items()):
        raise ValueError("frozen input_hashes.json must be a string-to-string object")
    if expected_input_count is not None and len(hashes) != expected_input_count:
        raise ValueError(
            f"frozen input count mismatch: expected {expected_input_count}, got {len(hashes)}")
    for name, expected in hashes.items():
        relative = _safe_relative_path(name, label="frozen input path")
        path = (source / Path(*relative.parts)).resolve()
        try:
            path.relative_to(source)
        except ValueError as exc:
            raise ValueError(f"frozen input escapes source artifact: {name}") from exc
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"frozen input missing or changed: {name}")
    return {
        "path": str(source), "required_sha256": dict(expected_sha256),
        "actual_sha256": actual, "input_hash_entries": len(hashes),
        "all_input_hashes_verified": True, "input_hashes": hashes,
    }


def implementation_hashes() -> dict[str, str]:
    result = {}
    for name in IMPLEMENTATION_FILES:
        path = ROOT / name
        if not path.is_file():
            raise ValueError(f"required implementation file is missing: {name}")
        result[name] = sha256(path)
    return result


def verify_implementation_hashes(expected: dict[str, str]) -> None:
    actual = implementation_hashes()
    if actual != expected:
        changed = sorted(set(actual) | set(expected), key=str)
        changed = [name for name in changed if actual.get(name) != expected.get(name)]
        raise RuntimeError(f"implementation changed during experiment: {changed}")


def _parse_csv_value(key: str, value: str):
    if key in TEXT_FIELDS:
        return value
    if key in BOOL_FIELDS:
        if value not in ("True", "False"):
            raise ValueError(f"invalid boolean {key}={value!r}")
        return value == "True"
    if value == "":
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"unexpected nonnumeric raw field {key}={value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite raw field {key}={value!r}")
    return int(number) if number.is_integer() and not any(c in value.lower() for c in (".", "e")) else number


def _rewritten_artifact_path(source: Path, output: Path, value: str) -> str:
    if not value:
        return ""
    relative = _safe_relative_path(value, label="legacy run path")
    path = (source / Path(*relative.parts)).resolve()
    try:
        path.relative_to(source)
    except ValueError as exc:
        raise ValueError(f"legacy run path escapes source artifact: {value}") from exc
    if not path.is_file():
        raise ValueError(f"legacy run path is missing: {path}")
    return Path(os.path.relpath(path, output)).as_posix()


def import_legacy_rows(source: Path, output: Path, plan: dict,
                       replay_hashes: dict[tuple[str, int, int], str] | None = None) -> list[dict]:
    """Import exactly the frozen 120-row matrix and make links v2-relative."""
    source, output = source.resolve(), output.resolve()
    with (source / "raw.csv").open(newline="", encoding="utf-8") as handle:
        raw = list(csv.DictReader(handle))
    expected = {
        (mode, int(count), int(seed), policy)
        for mode in plan["arrival_modes"] for count in plan["task_counts"]
        for seed in plan["seeds"] for policy in plan["policies"]
    }
    keys = [(row["arrival_mode"], int(row["task_count"]), int(row["seed"]), row["policy"])
            for row in raw]
    if len(keys) != len(expected) or len(set(keys)) != len(keys) or set(keys) != expected:
        raise ValueError("legacy raw.csv is not the exact frozen policy/scene matrix")
    imported = []
    relative_source = Path(os.path.relpath(source, output)).as_posix()
    for source_row in raw:
        row = {key: _parse_csv_value(key, value) for key, value in source_row.items()}
        source_policy = row["policy"]
        row["policy"] = V1_POLICY if source_policy == "STPS" else source_policy
        row["manifest"] = _rewritten_artifact_path(source, output, row["manifest"])
        row["report"] = _rewritten_artifact_path(source, output, row["report"])
        row.update({
            "row_origin": "reused-frozen-v1", "source_artifact": relative_source,
            "source_policy": source_policy,
            "source_scenario_sha256": row["scenario_sha256"],
            "objective": "completion" if source_policy == "STPS" else "baseline",
        })
        scene_key = (row["arrival_mode"], row["task_count"], row["seed"])
        if replay_hashes is not None:
            row["replay_input_sha256"] = replay_hashes[scene_key]
        imported.append(row)
    return imported


def _resolved_input(scene_path: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (scene_path.parent / path).resolve()


def replay_identity(scene_path: Path) -> str:
    """Hash replay-affecting values and dependency bytes, excluding STPS knobs."""
    scene_path = scene_path.resolve()
    document = _json(scene_path)
    tasks = []
    for item in document["tasks"]:
        task = {key: item[key] for key in (
            "task_id", "arrival_tick", "mean_compute_sops", "mean_noc_endpoint")}
        task["workload_sha256"] = sha256(_resolved_input(scene_path, item["workload"]))
        task["profile_sha256"] = (
            sha256(_resolved_input(scene_path, item["profile"])) if "profile" in item else None)
        tasks.append(task)
    replay = {
        key: document[key] for key in (
            "schema_version", "cluster", "noc", "max_ticks",
            "steady_window", "scheduler_seed")
    }
    replay["tasks"] = tasks
    encoded = json.dumps(replay, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def verify_scene_dependencies(scene_path: Path, source: Path, frozen_hashes: dict[str, str]) -> None:
    source = source.resolve()
    for item in _json(scene_path)["tasks"]:
        for field in ("workload", "profile"):
            if field not in item:
                continue
            path = _resolved_input(scene_path.resolve(), item[field])
            try:
                relative = path.relative_to(source).as_posix()
            except ValueError as exc:
                raise ValueError(f"{field} does not resolve into frozen artifact: {path}") from exc
            if frozen_hashes.get(relative) != sha256(path):
                raise ValueError(f"{field} is absent from or differs from frozen inputs: {relative}")


def source_scene_paths(source: Path, plan: dict) -> list[tuple[str, int, int, Path]]:
    result = []
    for count in plan["task_counts"]:
        for seed in plan["seeds"]:
            for mode in plan["arrival_modes"]:
                path = source / "inputs" / f"tasks_{count}" / f"seed_{seed}" / f"{mode}.json"
                if not path.is_file():
                    raise ValueError(f"frozen test scene is missing: {path}")
                result.append((mode, int(count), int(seed), path))
    return result


def prepare_test_scene(source_scene: Path, source: Path, output: Path, slack: float) -> Path:
    """Copy one scene with only the STPS objective fields changed."""
    source_scene, source = source_scene.resolve(), source.resolve()
    relative = source_scene.relative_to(source)
    target = output / "test_inputs" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    workload_link = target.parent / "workloads"
    expected_workloads = source_scene.parent / "workloads"
    if workload_link.is_symlink():
        if workload_link.resolve() != expected_workloads.resolve():
            raise ValueError(f"test workload link targets the wrong source: {workload_link}")
    elif workload_link.exists():
        raise ValueError(f"test workload link path already exists: {workload_link}")
    else:
        workload_link.symlink_to(expected_workloads, target_is_directory=True)
    before = _json(source_scene)
    after = json.loads(json.dumps(before))
    after["stps"] = dict(after.get("stps", {}))
    after["stps"].update({"objective": "balance", "balance_slack": slack})
    write_json(target, after)
    left, right = dict(before), dict(after)
    left.pop("stps", None)
    right.pop("stps", None)
    if left != right:
        raise AssertionError("v2 test scene changed fields outside stps")
    if replay_identity(source_scene) != replay_identity(target):
        raise AssertionError("v2 test scene changed replay inputs")
    return target


def validation_scene_document(source: Path, source_plan: dict, *, seed: int, mode: str,
                              objective: str, slack: float | None, gamma: float) -> dict:
    kinds = [TEMPLATES[index % len(TEMPLATES)] for index in range(VALIDATION_TASK_COUNT)]
    np.random.default_rng(np.random.SeedSequence([5000, seed])).shuffle(kinds)
    arrival_parameters = source_plan["arrival_parameters"]["normal_task_count_lte_24"]
    arrivals = make_arrival_ticks(mode, VALIDATION_TASK_COUNT, seed=seed, **arrival_parameters)
    profiles = {name: load_fingerprint(source / "profiles" / f"{name}.json")
                for name in TEMPLATES}
    occurrences = defaultdict(int)
    requests, sample_indices = [], []
    for index, (kind, arrival) in enumerate(zip(kinds, arrivals)):
        sample = occurrences[kind] % 4
        occurrences[kind] += 1
        workload = source / "samples/validation" / kind / f"{sample:02}.json"
        profile_path = source / "profiles" / f"{kind}.json"
        profile = profiles[kind]
        requests.append({
            "task_id": f"task-{index:04}", "arrival_tick": arrival,
            "workload": str(workload.resolve()), "profile": str(profile_path.resolve()),
            "mean_compute_sops": profile.mean_compute_sops,
            "mean_noc_endpoint": profile.mean_noc_endpoint,
        })
        sample_indices.append(sample)
    base_scene = _json(source / "inputs/tasks_24/seed_11/poisson.json")
    stps = {"d_max": 4, "gamma": gamma, "max_rounds": 10000,
            "objective": objective}
    if objective == "balance":
        stps["balance_slack"] = slack
    return {
        "schema_version": 1, "name": f"stps-v2-validation-{mode}-seed{seed}-{objective}",
        "cluster": base_scene["cluster"], "noc": base_scene["noc"],
        "max_ticks": base_scene["max_ticks"],
        "steady_window": base_scene["steady_window"],
        "scheduler_seed": seed, "tasks": requests, "stps": stps,
        "metadata": {
            "provenance": "frozen STPS v1 validation workloads; no test traces",
            "split": "validation", "arrival_mode": mode, "arrival_seed": seed,
            "arrival_parameters": arrival_parameters,
            "task_order_seed": [5000, seed], "task_order": kinds,
            "validation_sample_rule": "per-template occurrence modulo four",
            "validation_sample_indices": sample_indices,
            "profile_source": "frozen calibration profiles",
        },
    }


def prepare_validation_scenes(output: Path, source: Path, source_plan: dict,
                              gamma: float) -> list[tuple[str, float | None, str, int, Path]]:
    scenes = []
    objectives = [("completion", None), *(
        ("balance", slack) for slack in BALANCE_SLACK_GRID)]
    for objective, slack in objectives:
        label = "completion" if objective == "completion" else f"balance_{slack:.2f}"
        for seed in VALIDATION_SEEDS:
            for mode in ARRIVAL_MODES:
                path = output / "validation/inputs" / label / f"seed_{seed}" / f"{mode}.json"
                write_json(path, validation_scene_document(
                    source, source_plan, seed=seed, mode=mode, objective=objective,
                    slack=slack, gamma=gamma))
                scenes.append((objective, slack, mode, seed, path))
    return scenes


def _expected_totals(scenario):
    remote = sum(sum(int(value) for value in request.workload.flits[
        :, request.workload.edge_src != request.workload.edge_dst].flat)
        for request in scenario.tasks)
    sops = math.fsum(request.workload.totals["compute_sops"] for request in scenario.tasks)
    return remote, sops


def execute_stps_scene(scene_path: Path, folder: Path, output: Path, *, mode: str,
                       task_count: int, seed: int, label: str, report: bool) -> dict:
    scenario = load_cluster_scenario(scene_path)
    expected_remote, expected_sops = _expected_totals(scenario)
    result = run_cluster_simulation(
        scenario, folder, policy="STPS", seed=seed, trace=False, report=report)
    summary = result.summary
    if not summary["conservation"]["valid"]:
        raise AssertionError(f"{mode}/{task_count}/{seed}/{label}: flit conservation failed")
    if result.status == "completed":
        if not all(summary["totals"][field] == expected_remote
                   for field in ("generated_tx", "tx_injected", "rx_ejected")):
            raise AssertionError("policy changed or lost replay traffic")
        if not math.isclose(summary["totals"]["compute_sops"], expected_sops, rel_tol=1e-12):
            raise AssertionError("policy repeated or lost SOP work")
    return run_row(summary, mode=mode, task_count=task_count, seed=seed,
                   policy=label, folder=folder, output=output)


def select_balance_slack(completion_rows: list[dict], candidate_rows: list[dict]) -> dict:
    """Apply the predeclared per-scene latency gate and balance objective."""
    keys = {(mode, seed) for seed in VALIDATION_SEEDS for mode in ARRIVAL_MODES}
    completion = {(row["arrival_mode"], row["seed"]): row for row in completion_rows}
    if set(completion) != keys:
        raise ValueError("completion validation must contain exactly four scenes")
    entries = []
    for slack in BALANCE_SLACK_GRID:
        rows = [row for row in candidate_rows if row["balance_slack"] == slack]
        by_scene = {(row["arrival_mode"], row["seed"]): row for row in rows}
        if set(by_scene) != keys or len(rows) != len(keys):
            raise ValueError(f"balance_slack={slack} must contain exactly four scenes")
        checks, valid = [], True
        for key in sorted(keys):
            reference, row = completion[key], by_scene[key]
            complete = (row.get("valid") is True and row.get("status") == "completed"
                        and row.get("tasks_completed") == row.get("tasks_total"))
            limit = reference["mean_task_end_to_end_ticks"] * E2E_LIMIT_FACTOR
            e2e_ok = complete and row["mean_task_end_to_end_ticks"] <= limit
            valid = valid and e2e_ok
            checks.append({
                "arrival_mode": key[0], "seed": key[1], "completed": complete,
                "completion_e2e": reference["mean_task_end_to_end_ticks"],
                "balance_e2e": row["mean_task_end_to_end_ticks"],
                "e2e_limit": limit, "e2e_within_limit": e2e_ok,
                "balance_max_cv": max(row["full_compute_sops_cv"],
                                          row["full_endpoint_events_cv"]),
            })
        score = statistics.fmean(check["balance_max_cv"] for check in checks)
        entries.append({
            "balance_slack": slack, "feasible": valid,
            "completed_scenes": sum(check["completed"] for check in checks),
            "latency_gate_scenes": sum(check["e2e_within_limit"] for check in checks),
            "balance_score": score, "scenes": checks,
        })
    feasible = [entry for entry in entries if entry["feasible"]]
    if not feasible:
        raise RuntimeError("no balance_slack candidate passed all four validation gates")
    selected = min(feasible, key=lambda entry: (entry["balance_score"], entry["balance_slack"]))
    for entry in entries:
        entry["selected"] = entry is selected
    return {
        "selected_balance_slack": selected["balance_slack"],
        "selected_balance_score": selected["balance_score"],
        "rule": ("require 4/4 valid completed and each scene mean E2E <= "
                 "same-scene completion objective * 1.05; minimize four-scene mean of "
                 "max(full compute CV, full endpoint CV); tie chooses smaller slack"),
        "candidates": entries,
    }


def paired_target(target_rows: dict, comparator_rows: dict, metric: str) -> dict:
    pairs = [(target_rows[seed][metric], comparator_rows[seed][metric])
             for seed in sorted(target_rows.keys() & comparator_rows.keys())
             if target_rows[seed]["valid"] and comparator_rows[seed]["valid"]
             and target_rows[seed].get(metric) is not None
             and comparator_rows[seed].get(metric) is not None]
    direction = 1 if higher_is_better(metric) else -1
    deltas = [target - comparator for target, comparator in pairs]
    improvements = [direction * delta for delta in deltas]
    relative = [100 * direction * (target - comparator) / abs(comparator)
                for target, comparator in pairs if comparator != 0]
    return {
        "paired_samples": len(pairs),
        "mean_target_minus_comparator": statistics.fmean(deltas) if pairs else None,
        "mean_improvement": statistics.fmean(improvements) if pairs else None,
        "mean_relative_improvement_percent": statistics.fmean(relative) if relative else None,
        "relative_samples": len(relative),
        "wins": sum(value > 1e-12 for value in improvements),
        "ties": sum(abs(value) <= 1e-12 for value in improvements),
        "losses": sum(value < -1e-12 for value in improvements),
    }


def summarize_v2(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["arrival_mode"], row["task_count"], row["policy"])].append(row)
    numeric_metrics = sorted({
        key for row in rows for key, value in row.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        and key not in ("seed", "task_count", "balance_slack")
    })
    summaries = []
    for (mode, count, policy), members in sorted(groups.items()):
        complete = [row for row in members if row["valid"]]
        entry = {"arrival_mode": mode, "task_count": count, "policy": policy,
                 "runs": len(members), "completed_runs": len(complete), "metrics": {}}
        for metric in numeric_metrics:
            values = [row[metric] for row in complete if row.get(metric) is not None]
            if values:
                entry["metrics"][metric] = {
                    "mean": statistics.fmean(values),
                    "std": statistics.stdev(values) if len(values) > 1 else 0.0,
                    "min": min(values), "max": max(values), "samples": len(values),
                }
        summaries.append(entry)
    paired, best, versus_v1 = [], [], []
    scenarios = sorted({(row["arrival_mode"], row["task_count"]) for row in rows})
    for mode, count in scenarios:
        target = {row["seed"]: row for row in groups[(mode, count, TARGET_POLICY)]}
        selected = [entry for entry in summaries
                    if entry["arrival_mode"] == mode and entry["task_count"] == count]
        for comparator in (*TRADITIONAL_BASELINES, V1_POLICY):
            other = {row["seed"]: row for row in groups[(mode, count, comparator)]}
            comparator_type = "stps_v1" if comparator == V1_POLICY else "traditional_baseline"
            for metric in COMPARISON_METRICS:
                result = paired_target(target, other, metric)
                if result["paired_samples"]:
                    entry = {
                        "arrival_mode": mode, "task_count": count,
                        "target": TARGET_POLICY, "comparator": comparator,
                        "comparator_type": comparator_type, "metric": metric, **result,
                    }
                    paired.append(entry)
                    if comparator == V1_POLICY:
                        versus_v1.append(entry)
        for metric in COMPARISON_METRICS:
            options = [entry for entry in selected if entry["policy"] in TRADITIONAL_BASELINES
                       and metric in entry["metrics"]]
            if not options:
                continue
            winner = min(options, key=lambda entry: (
                (-1 if higher_is_better(metric) else 1) * entry["metrics"][metric]["mean"],
                entry["policy"]))
            other = {row["seed"]: row
                     for row in groups[(mode, count, winner["policy"])]}
            result = paired_target(target, other, metric)
            best.append({
                "arrival_mode": mode, "task_count": count, "target": TARGET_POLICY,
                "metric": metric, "best_baseline": winner["policy"],
                "baseline_mean": winner["metrics"][metric]["mean"],
                "selection": "best frozen traditional-baseline cross-seed mean for this metric",
                **result,
            })
    return {
        "runs": len(rows), "completed_runs": sum(row["valid"] for row in rows),
        "policies": list(ALL_POLICIES), "target_policy": TARGET_POLICY,
        "groups": summaries, "paired_vs_comparators": paired,
        "versus_best_traditional_baseline": best, "versus_stps_v1": versus_v1,
        "statistical_scope": ("descriptive paired synthetic experiment; five test seeds; "
                              "traditional baselines and STPS-v1 are reused historical rows "
                              "from the frozen v1 source artifact"),
    }


def _flat_summary(summary: dict) -> list[dict]:
    rows = []
    for group in summary["groups"]:
        for metric, stats in group["metrics"].items():
            rows.append({key: group[key] for key in (
                "arrival_mode", "task_count", "policy", "runs", "completed_runs")}
                | {"metric": metric, **stats})
    return rows


def report_html(output: Path, summary: dict, selection: dict, plan: dict, rows: list[dict]) -> None:
    escape = html.escape
    body = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<title>STPS-Balance 冻结验证与七策略比较</title>',
        '<style>body{font:15px/1.55 system-ui;margin:32px;color:#17202a}'
        'table{border-collapse:collapse;margin:14px 0}th,td{border:1px solid #ccd;padding:7px;text-align:right}'
        'th:first-child,td:first-child{text-align:left}.good{color:#176037}.bad{color:#a32626}'
        'a{color:#155d99}</style>',
        '<h1>STPS-Balance：独立验证选参与冻结测试扩展</h1>',
        '<p>balance_slack 仅在冻结 validation workload 上选择。测试沿用 20 个冻结场景，'
        '只新增 STPS-Balance 运行；五种传统基线和 STPS-v1 的 120 行结果来自冻结 v1 artifact。'
        '由于当前调度器源码已经迭代，这是带完整来源哈希的历史对照，不宣称同一执行器版本重跑。</p>',
        f'<p>选择 balance_slack={selection["selected_balance_slack"]:.2f}；'
        f'七策略完成 {summary["completed_runs"]}/{summary["runs"]} 次测试运行。</p>',
        '<p><a href="plan_v2.json">预冻结计划</a> · '
        '<a href="source_artifact.json">源 artifact 校验</a> · '
        '<a href="validation/selection.json">validation 选择</a> · '
        '<a href="validation/raw.csv">validation 原始结果</a> · '
        '<a href="raw_v2.csv">七策略逐次结果</a> · '
        '<a href="summary_v2.csv">汇总</a> · '
        '<a href="paired_v2.csv">全部配对</a> · '
        '<a href="best_baseline_v2.csv">最佳传统基线</a> · '
        '<a href="versus_v1_v2.csv">STPS-v1 对照</a></p>',
        '<h2>Validation 候选</h2><table><tr><th>slack</th><th>完成</th><th>逐场延迟门</th>'
        '<th>mean max(compute CV, NoC CV)</th><th>可行</th><th>选中</th></tr>',
    ]
    for entry in selection["candidates"]:
        body.append(
            f'<tr><td>{entry["balance_slack"]:.2f}</td>'
            f'<td>{entry["completed_scenes"]}/4</td>'
            f'<td>{entry["latency_gate_scenes"]}/4</td>'
            f'<td>{entry["balance_score"]:.6f}</td>'
            f'<td>{"是" if entry["feasible"] else "否"}</td>'
            f'<td>{"是" if entry["selected"] else ""}</td></tr>')
    body.append('</table><h2>测试：相对最佳传统基线与 STPS-v1</h2>')
    best_map = {(x["arrival_mode"], x["task_count"], x["metric"]): x
                for x in summary["versus_best_traditional_baseline"]}
    v1_map = {(x["arrival_mode"], x["task_count"], x["metric"]): x
              for x in summary["versus_stps_v1"]}
    body.append('<table><tr><th>场景</th><th>指标</th><th>最佳基线</th>'
                '<th>相对基线改进</th><th>胜/平/负</th><th>相对 v1 改进</th><th>胜/平/负</th></tr>')
    for mode, count in sorted({(x["arrival_mode"], x["task_count"])
                               for x in summary["groups"]}):
        for metric in KEY_METRICS:
            best = best_map.get((mode, count, metric))
            v1 = v1_map.get((mode, count, metric))
            if not best or not v1:
                continue
            bimp, vimp = best["mean_improvement"], v1["mean_improvement"]
            body.append(
                f'<tr><td>{escape(mode)}/{count}</td><td>{escape(metric)}</td>'
                f'<td>{escape(best["best_baseline"])}</td>'
                f'<td class="{"good" if bimp > 0 else "bad" if bimp < 0 else ""}">{bimp:+.6f}</td>'
                f'<td>{best["wins"]}/{best["ties"]}/{best["losses"]}</td>'
                f'<td class="{"good" if vimp > 0 else "bad" if vimp < 0 else ""}">{vimp:+.6f}</td>'
                f'<td>{v1["wins"]}/{v1["ties"]}/{v1["losses"]}</td></tr>')
    body.append('</table><h2>运行产物</h2><ul>')
    for row in rows:
        if row.get("manifest"):
            label = f'{row["arrival_mode"]}/{row["task_count"]}/seed{row["seed"]}/{row["policy"]}'
            body.append(f'<li><a href="{escape(row["manifest"], quote=True)}">{escape(label)}</a></li>')
    body.append('</ul><p>所有结论限于四类小型合成拓扑、4 卡 × 每卡 4×4、两个到达模式和五个测试种子。'
                'SOP 只做负载记账，没有计算服务时延或硬件验证。</p></html>')
    (output / "index_v2.html").write_text("".join(body), encoding="utf-8")


def frozen_v2_plan(source: Path, source_plan: dict, source_identity: dict,
                   current_hashes: dict[str, str], gamma: float) -> dict:
    return {
        "schema_version": 2,
        "written_before": "validation scene generation, validation execution, selection, and test execution",
        "test_outcome_tuning": False,
        "source_artifact": {
            "path": str(source.resolve()),
            "required_sha256": source_identity["required_sha256"],
            "verified_input_hash_entries": source_identity["input_hash_entries"],
            "all_input_hashes_verified": True,
        },
        "implementation_sha256": current_hashes,
        "gamma": {
            "value": gamma, "source": "frozen source calibration/summary.json",
            "regenerated_or_tuned": False,
        },
        "validation": {
            "split": "frozen samples/validation workload replay with frozen calibration profiles",
            "seeds": list(VALIDATION_SEEDS), "task_count": VALIDATION_TASK_COUNT,
            "arrival_modes": list(ARRIVAL_MODES),
            "task_order_seed": "[5000, validation_seed]",
            "task_mix": "six tasks per template; deterministic shuffle",
            "sample_rule": "per-template occurrence modulo four; no new workload generation",
            "balance_slack_grid": list(BALANCE_SLACK_GRID),
            "completion_reference_runs": 4, "candidate_runs": 16,
            "feasibility": "4/4 valid completed and per-scene mean E2E <= completion objective * 1.05",
            "score": "mean across four scenes of max(full_compute_sops_cv, full_endpoint_events_cv)",
            "tie_break": "smaller balance_slack",
        },
        "test": {
            "source_scenes": 20, "new_runs": 20,
            "seeds": source_plan["seeds"], "task_counts": source_plan["task_counts"],
            "arrival_modes": source_plan["arrival_modes"],
            "scene_change": "only stps.objective and stps.balance_slack; dependency paths/bytes unchanged",
            "reused_rows": 120, "combined_rows": 140,
            "policies": list(ALL_POLICIES),
        },
        "analysis": {
            "best_baseline_pool": list(TRADITIONAL_BASELINES),
            "v1_comparator": V1_POLICY, "target": TARGET_POLICY,
            "paired_by": "arrival mode, task count, seed",
            "direction": "positive improvement always favors STPS-Balance",
            "scope": "descriptive; reused v1 rows are historical cross-version comparators",
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-artifact", type=Path, default=SOURCE_ARTIFACT)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/stps_comparison_v2")
    parser.add_argument("--no-reports", action="store_true")
    args = parser.parse_args(argv)

    source = args.source_artifact.resolve()
    source_identity = verify_source_artifact(source)
    source_plan = _json(source / "plan.json")
    gamma = float(_json(source / "calibration/summary.json")["gamma"])
    output = args.output_root.resolve() / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output.mkdir(parents=True, exist_ok=False)
    hashes = implementation_hashes()
    plan = frozen_v2_plan(source, source_plan, source_identity, hashes, gamma)
    write_json(output / "plan_v2.json", plan)
    write_json(output / "source_artifact.json", {
        key: value for key, value in source_identity.items() if key != "input_hashes"})
    print(f'Frozen v2 plan: {output / "plan_v2.json"}', flush=True)

    frozen_hashes = source_identity["input_hashes"]
    validation_scenes = prepare_validation_scenes(output, source, source_plan, gamma)
    for *_, scene in validation_scenes:
        verify_scene_dependencies(scene, source, frozen_hashes)
    write_json(output / "validation/input_hashes.json", {
        str(path.relative_to(output)): sha256(path) for *_, path in validation_scenes})

    completion_rows, candidate_rows, validation_rows = [], [], []
    for index, (objective, slack, mode, seed, scene) in enumerate(validation_scenes, 1):
        label = V1_POLICY if objective == "completion" else f"STPS-Balance[{slack:.2f}]"
        folder_name = "completion" if objective == "completion" else f"balance_{slack:.2f}"
        folder = output / "validation/runs" / folder_name / mode / f"seed_{seed}"
        row = execute_stps_scene(
            scene, folder, output, mode=mode, task_count=VALIDATION_TASK_COUNT,
            seed=seed, label=label, report=False)
        row.update({"objective": objective, "balance_slack": slack,
                    "row_origin": "executed-v2-validation"})
        validation_rows.append(row)
        (completion_rows if objective == "completion" else candidate_rows).append(row)
        write_csv(output / "validation/raw.csv", validation_rows)
        print(f'validation {index}/{len(validation_scenes)} {label}/{mode}/seed{seed}: '
              f'{row["status"]}, E2E={row["mean_task_end_to_end_ticks"]:.3f}, '
              f'maxCV={max(row["full_compute_sops_cv"], row["full_endpoint_events_cv"]):.4f}',
              flush=True)
    selection = select_balance_slack(completion_rows, candidate_rows)
    write_json(output / "validation/selection.json", selection)
    write_csv(output / "validation/selection.csv", [
        {key: entry[key] for key in ("balance_slack", "feasible", "completed_scenes",
                                     "latency_gate_scenes", "balance_score", "selected")}
        for entry in selection["candidates"]])
    selected_slack = selection["selected_balance_slack"]
    print(f'Selected balance_slack={selected_slack:.2f}', flush=True)

    source_scenes = source_scene_paths(source, source_plan)
    source_replay, prepared = {}, []
    for mode, count, seed, source_scene in source_scenes:
        verify_scene_dependencies(source_scene, source, frozen_hashes)
        source_hash = sha256(source_scene)
        relative = source_scene.relative_to(source).as_posix()
        if frozen_hashes.get(relative) != source_hash:
            raise ValueError(f"source scene is absent from frozen input hashes: {relative}")
        replay_hash = replay_identity(source_scene)
        source_replay[(mode, count, seed)] = replay_hash
        target = prepare_test_scene(source_scene, source, output, selected_slack)
        verify_scene_dependencies(target, source, frozen_hashes)
        prepared.append((mode, count, seed, source_scene, target, source_hash, replay_hash))
    write_json(output / "input_hashes_v2.json", {
        "plan_v2.json": sha256(output / "plan_v2.json"),
        "source_artifact.json": sha256(output / "source_artifact.json"),
        **{str(target.relative_to(output)): sha256(target) for *_, target, __, ___ in prepared},
    })

    legacy_rows = import_legacy_rows(source, output, source_plan, source_replay)
    target_rows = []
    for index, (mode, count, seed, source_scene, target, source_hash, replay_hash) in enumerate(prepared, 1):
        folder = output / "runs" / mode / f"tasks_{count}" / f"seed_{seed}" / TARGET_POLICY
        make_report = (not args.no_reports and seed == source_plan["seeds"][0]
                       and count == min(source_plan["task_counts"]))
        row = execute_stps_scene(
            target, folder, output, mode=mode, task_count=count, seed=seed,
            label=TARGET_POLICY, report=make_report)
        row.update({
            "row_origin": "executed-v2-test", "source_artifact": ".",
            "source_policy": "STPS", "source_scenario_sha256": source_hash,
            "replay_input_sha256": replay_hash, "objective": "balance",
            "balance_slack": selected_slack,
        })
        target_rows.append(row)
        write_csv(output / "raw_v2.csv", [*legacy_rows, *target_rows])
        print(f'test {index}/{len(prepared)} {mode}/{count}/seed{seed}/{TARGET_POLICY}: '
              f'{row["status"]}, E2E={row["mean_task_end_to_end_ticks"]:.3f}, '
              f'NoC CV={row["full_endpoint_events_cv"]:.4f}', flush=True)

    rows = [*legacy_rows, *target_rows]
    if len(rows) != 140 or {row["policy"] for row in rows} != set(ALL_POLICIES):
        raise AssertionError("combined result must contain 140 rows and exactly seven policies")
    summary = summarize_v2(rows)
    write_json(output / "summary_v2.json", summary)
    write_csv(output / "summary_v2.csv", _flat_summary(summary))
    write_csv(output / "paired_v2.csv", summary["paired_vs_comparators"])
    write_csv(output / "best_baseline_v2.csv", summary["versus_best_traditional_baseline"])
    write_csv(output / "versus_v1_v2.csv", summary["versus_stps_v1"])
    report_html(output, summary, selection, plan, rows)

    verify_implementation_hashes(plan["implementation_sha256"])
    verify_source_artifact(source)
    print(output / "index_v2.html", flush=True)
    return 0 if summary["completed_runs"] == summary["runs"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
