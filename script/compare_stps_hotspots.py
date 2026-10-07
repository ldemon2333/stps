#!/usr/bin/env python3
"""Re-run five baselines and final STPS under one binary with hotspot metrics."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import html
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from schedule.baselines import POLICIES as BASELINES
from script.compare_stps import sha256, write_csv, write_json
from script.compare_stps_v2 import FROZEN_SOURCE_SHA256, FROZEN_INPUT_COUNT, verify_source_artifact, source_scene_paths
from simulation.cluster_engine import run_cluster_simulation
from simulation.cluster_scenario import load_cluster_scenario

SOURCE = ROOT / "data/stps_comparison/20261007T083540_810669Z"
POLICIES = (*BASELINES, "STPS")
LOADS = ("compute_sops", "offered_endpoint_events", "endpoint_events")
RATIOS = ("cv", "jfi", "lif", "p99_to_mean", "max_to_mean")
IMPLEMENTATION_FILES = ("script/compare_stps_hotspots.py", "schedule/stps.py",
                        "simulation/cluster_engine.py", "simulation/card_runtime.py",
                        "simulation/cluster_metrics.py", "simulation/noc.py")


def implementation_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in IMPLEMENTATION_FILES}


def flatten(summary, mode, count, seed, policy, folder, output):
    row = {"arrival_mode": mode, "task_count": count, "seed": seed, "policy": policy,
           "status": summary["status"], "valid": summary["valid"],
           "ticks_executed": summary["ticks_executed"],
           "manifest": str((folder / "manifest.json").relative_to(output))}
    row.update({k: v for k, v in summary["timing"].items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)})
    for window_name, window in summary["balance_windows"].items():
        for load in LOADS:
            metrics = window["balance"][load]
            for ratio in RATIOS:
                row[f"{window_name}_{load}_{ratio}"] = metrics[ratio]["value"]
            temporal = window["temporal_hotspots"][load]
            for ratio in RATIOS:
                for stat in ("mean", "p95", "p99", "max"):
                    row[f"{window_name}_tick_{load}_{ratio}_{stat}"] = temporal[ratio][stat]
    for width, block in summary["sliding_windows"]["by_width"].items():
        for load in LOADS:
            for ratio in RATIOS:
                for stat in ("mean", "p95", "p99", "max"):
                    row[f"slide{width}_{load}_{ratio}_{stat}"] = block["loads"][load][ratio][stat]
    return row


def summarize(rows):
    numeric = sorted({k for row in rows for k, v in row.items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool)
                      and k not in ("seed", "task_count")})
    groups = []
    for mode in ("poisson", "bursty"):
        for count in (24, 48):
            for policy in POLICIES:
                selected = [r for r in rows if r["arrival_mode"] == mode and
                            r["task_count"] == count and r["policy"] == policy and r["valid"]]
                metrics = {k: sum(r[k] for r in selected) / len(selected)
                           for k in numeric if selected and all(k in r for r in selected)}
                groups.append({"arrival_mode": mode, "task_count": count,
                               "policy": policy, "samples": len(selected), "metrics": metrics})
    return {"runs": len(rows), "completed_runs": sum(r["valid"] for r in rows), "groups": groups}


def report(output, summary):
    keys = ("full_compute_sops_cv", "full_offered_endpoint_events_cv",
            "full_offered_endpoint_events_jfi", "full_offered_endpoint_events_lif",
            "full_tick_offered_endpoint_events_lif_p99",
            "slide4_offered_endpoint_events_cv_p99", "slide8_offered_endpoint_events_lif_p99")
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>STPS热点重跑</title>',
            '<style>body{font:15px/1.6 system-ui;margin:32px}table{border-collapse:collapse}'
            'td,th{border:1px solid #ccc;padding:7px;text-align:right}td:first-child{text-align:left}</style>',
            '<h1>同一二进制热点/straggler重跑</h1><p>五基线和最终STPS全部重跑；4卡时nearest-rank P99=Max，LIF=Max/Mean。'
            'offered=2×generated，served=Tx+Rx；滑窗只用完整1/4/8 Tick样本。</p>',
            '<p><a href="raw_hotspot.csv">raw</a> · <a href="summary_hotspot.json">summary</a></p>']
    for mode in ("poisson", "bursty"):
        for count in (24, 48):
            body.append(f'<h2>{mode}/{count}</h2><table><tr><th>策略</th>' +
                        ''.join(f'<th>{html.escape(k)}</th>' for k in keys) + '</tr>')
            for g in summary["groups"]:
                if g["arrival_mode"] == mode and g["task_count"] == count:
                    body.append('<tr><td>' + html.escape(g["policy"]) + '</td>' + ''.join(
                        f'<td>{g["metrics"].get(k, 0):.4f}</td>' for k in keys) + '</tr>')
            body.append('</table>')
    (output / "index.html").write_text(''.join(body) + '</html>', encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-artifact", type=Path, default=SOURCE)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/stps_hotspots")
    args = parser.parse_args(argv)
    source = args.source_artifact.resolve()
    identity = verify_source_artifact(source, expected_sha256=FROZEN_SOURCE_SHA256,
                                      expected_input_count=FROZEN_INPUT_COUNT)
    plan = json.loads((source / "plan.json").read_text())
    output = args.output_root.resolve() / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan_hotspot.json", {"source": str(source), "source_identity": {
        k: v for k, v in identity.items() if k != "input_hashes"}, "policies": list(POLICIES),
        "implementation_sha256": implementation_hashes(),
        "metrics": {"loads": LOADS, "ratios": RATIOS, "sliding_window_ticks": [1, 4, 8]},
        "stps": {"objective": "balance", "balance_slack": 0.0, "adaptive_ledger": True}})
    rows = []
    scenes = source_scene_paths(source, plan)
    for mode, count, seed, path in scenes:
        original = json.loads(path.read_text())
        original["sliding_window_ticks"] = [1, 4, 8]
        original["stps"] = dict(
            original.get("stps", {}), objective="balance", balance_slack=0.0,
            adaptive_ledger=True)
        scene = output / "inputs" / mode / f"tasks_{count}" / f"seed_{seed}.json"
        scene.parent.mkdir(parents=True, exist_ok=True)
        for task in original["tasks"]:
            task["workload"] = str((path.parent / task["workload"]).resolve())
            task["profile"] = str((path.parent / task["profile"]).resolve())
        write_json(scene, original)
        for policy in POLICIES:
            folder = output / "runs" / mode / f"tasks_{count}" / f"seed_{seed}" / policy
            result = run_cluster_simulation(load_cluster_scenario(scene), folder, policy=policy,
                                            seed=seed, trace=False, report=False)
            if not result.summary["conservation"]["valid"]:
                raise AssertionError("conservation failed")
            rows.append(flatten(result.summary, mode, count, seed, policy, folder, output))
            write_csv(output / "raw_hotspot.csv", rows)
            print(f'{len(rows)}/{len(scenes)*len(POLICIES)} {mode}/{count}/{seed}/{policy}', flush=True)
    summary = summarize(rows)
    if json.loads((output / "plan_hotspot.json").read_text())["implementation_sha256"] != implementation_hashes():
        raise RuntimeError("implementation changed during hotspot experiment")
    write_json(output / "summary_hotspot.json", summary)
    report(output, summary)
    print(output / "index.html")
    return 0 if summary["runs"] == summary["completed_runs"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
