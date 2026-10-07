#!/usr/bin/env python3
"""Optional real LIF-forward capture with controlled currents and explicit topology.

This is an untrained tiny SNN with synthetic currents, NOT a pretrained model
benchmark or a hardware SOP measurement. Requires torch and spikingjelly.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/phase1_lif")
    args = parser.parse_args(argv)
    import torch
    from spikingjelly.activation_based import functional, neuron
    from fingerprint.dtdg import collect_spike_traces
    from fingerprint.extractor import workload_from_spike_traces
    from fingerprint import EdgeSpec, mask_linear, save_workload, split_layer
    from simulation.engine import run_simulation

    class TinySNN(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.a = neuron.LIFNode(tau=2.0, step_mode="m")
            self.fc = torch.nn.Linear(4, 4, bias=False)
            self.b = neuron.LIFNode(tau=2.0, step_mode="m")
            with torch.no_grad():
                self.fc.weight.fill_(0.7)

        def forward(self, inputs):
            return self.b(self.fc(self.a(inputs.transpose(0, 1))))

    torch.manual_seed(17)
    net = TinySNN()
    currents = torch.zeros(2, 8, 4)
    currents[0, 1, :] = 3.0
    currents[0, 5, :2] = 4.0
    currents[1, 3, 2:] = 4.0
    traces = collect_spike_traces(net, {"a": net.a, "b": net.b}, [currents], 8,
                                  reset_fn=functional.reset_net)
    pops = split_layer("a", "vec", (4,)) + split_layer("b", "vec", (4,))
    edges = [EdgeSpec("a", "b", "linear", lambda src, dst: mask_linear(dst.size), compute_per_flit=1)]
    out = args.output_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    out.mkdir(parents=True, exist_ok=False)
    tasks = []
    for sample in range(2):
        workload = workload_from_spike_traces(pops, edges, traces, state_size_mb=0.001,
            source="model-forward:tiny-untrained-LIF/controlled-synthetic-currents",
            sample_index=sample, metadata={"seed": 17, "model": "two-LIF+Linear4x4",
                "compute_scope": "Linear downstream synaptic work only; input LIF updates excluded"})
        save_workload(out / f"sample{sample}.json", workload)
        tasks.append({"task_id": f"sample{sample}", "workload": f"sample{sample}.json",
                      "start_tick": 1, "mapping": [0, 3] if sample == 0 else [1, 2]})
    scene = {"schema_version": 1, "name": "same-source LIF captures, independent two tasks",
             "card": {"mesh_x": 2, "mesh_y": 2, "neurons_per_core": 16, "memory_mb": 1},
             "noc": {"cycles_per_tick": 64}, "tasks": tasks}
    path = out / "scenario.json"
    path.write_text(json.dumps(scene, indent=2) + "\n")
    tracemalloc.start()
    try:
        result = run_simulation(path, out / "run", trace=True)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    (out / "scale.json").write_text(json.dumps({
        "scope": "NoC replay/report after model capture; timings include tracemalloc overhead",
        "python_peak_traced_bytes": peak_bytes,
        "elapsed_seconds": result.summary["elapsed_seconds"],
        "cpu_seconds": result.summary["cpu_seconds"],
        "peak_inventory_flits": result.summary["peak_inventory_flits"],
        "output_bytes": sum(p.stat().st_size for p in (out / "run").iterdir()),
        "tasks": [{"task_id": t["task_id"], "active_edges": t["active_edges"],
                   "input_totals": t["input_totals"]} for t in result.summary["tasks"]],
    }, indent=2) + "\n")
    if result.status != "completed":
        raise RuntimeError(f"tiny model fixture failed: {result.status}")
    print(out / "run/report.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
