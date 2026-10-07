#!/usr/bin/env python3
"""Run the fixed single-card acceptance matrix, write reports and comparisons."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulation.engine import run_simulation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/phase1")
    args = parser.parse_args(argv)
    output = args.output_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output.mkdir(parents=True, exist_ok=False)
    base_path = ROOT / "examples/single_card/positive.json"
    base = json.loads(base_path.read_text())
    for task in base["tasks"]:
        task["workload"] = str((base_path.parent / task["workload"]).resolve())
    results = []
    cases = (("positive", 40, 2, False, "completed"),
             ("carry_over", 6, 2, False, "completed"),
             ("carry_over_buffer1", 6, 1, False, "completed"),
             ("carry_over_stagger", 6, 2, True, "completed"),
             ("buffer1", 40, 1, False, "completed"),
             ("manual_stagger", 40, 2, True, "completed"),
             ("truncated", 6, 2, False, "max_ticks"))
    for name, cycles, depth, stagger, expected in cases:
        document = json.loads(json.dumps(base))
        document["name"] = name
        document["noc"].update(cycles_per_tick=cycles, router_buffer_depth=depth,
                                source_buffer_depth=depth, sink_buffer_depth=depth)
        if stagger:
            document["tasks"][1]["start_tick"] = 3
            document["tasks"][2]["start_tick"] = 2
        if name == "truncated":
            document["max_ticks"] = 2
        path = output / (name + ".json")
        path.write_text(json.dumps(document, indent=2) + "\n")
        tracemalloc.start()
        try:
            result = run_simulation(path, output / name, trace=True)
            _, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        if result.status != expected:
            raise RuntimeError(f"{name}: expected {expected}, got {result.status}")
        totals = result.summary["totals"]
        results.append({"case": name, "status": result.status,
                        "cycles_per_tick": cycles,
                        "buffer_depth": depth, "ticks_executed": result.ticks_executed,
                        **result.summary["timing"], **result.summary["derived"],
                        **totals, "elapsed_seconds": result.summary["elapsed_seconds"],
                        "cpu_seconds": result.summary["cpu_seconds"],
                        "python_peak_traced_bytes": peak_bytes,
                        "peak_in_network_flits": result.summary["peak_inventory_flits"]["router"],
                        "peak_undelivered_flits": result.summary["peak_inventory_flits"]["undelivered"],
                        "output_bytes": sum(p.stat().st_size for p in (output / name).iterdir()),
                        "report": f"{name}/report.html"})
    with (output / "comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>单卡 NoC 验收</title>'
        '<h1>单卡多任务 NoC 验收</h1><p>固定场景：跨 Tick 排队使逻辑步变长；truncated 只运行两个 Tick。</p><ul>'
        + ''.join(f'<li><a href="{r["report"]}">{r["case"]}</a>: {r["status"]}</li>' for r in results)
        + '</ul><p><a href="comparison.csv">完整计数与规模测量</a></p>'
        + '<p>CPU/耗时含 tracemalloc 开销；内存是各次运行含报告的 Python 分配峰值，不是进程 RSS。</p>',
        encoding="utf-8")
    print(output / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
