from __future__ import annotations

import argparse
import sys
from pathlib import Path
import random
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from util.task import Task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot spike intensity over a task's duration")
    parser.add_argument("--steps", type=int, default=50, help="Number of ticks to simulate")
    parser.add_argument("--neuron-count", type=int, default=800, help="Neuron count for the task")
    parser.add_argument("--complexity", type=float, default=1.0, help="Complexity ratio for the task")
    parser.add_argument("--state-mb", type=float, default=12.0, help="State size in MB")
    parser.add_argument("--seed", type=int, default=21, help="Random seed")
    parser.add_argument("--output", type=Path, default=Path("plot/task_spikes.pdf"), help="Output figure path (pdf)")
    return parser.parse_args()


def simulate_task(steps: int, neuron_count: int, complexity: float, state_mb: float, seed: int):
    random.seed(seed)
    task = Task(
        task_id=0,
        state_size_mb=state_mb,
        neuron_count=neuron_count,
        complexity_ratio=complexity,
        arrival_step=0,
        duration_steps=steps,
    )

    spikes: list[int] = []
    ops: list[int] = []
    for _ in range(steps):
        task.simulate_tick()
        spikes.append(task.current_spike_count)
        ops.append(task.current_synaptic_ops)
    return spikes, ops


def plot_series(spikes: list[int], ops: list[int], output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    x = list(range(1, len(spikes) + 1))

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(x, spikes, label="Spikes")
    ax.set_title("Task Spike Intensity Over Time")
    ax.set_xlabel("Time Step")
    ax.set_ylabel("Spike Count")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend()
    fig.tight_layout()
    fig.savefig(output)
    plt.close(fig)

    ops_out = output.with_name(output.stem + "_ops" + output.suffix)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(x, ops, color="tab:red", label="Synaptic Ops")
    ax.set_title("Task Synaptic Ops Over Time")
    ax.set_xlabel("Time Step")
    ax.set_ylabel("Ops")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend()
    fig.tight_layout()
    fig.savefig(ops_out)
    plt.close(fig)

    return output, ops_out


def main() -> None:
    args = parse_args()
    spikes, ops = simulate_task(args.steps, args.neuron_count, args.complexity, args.state_mb, args.seed)
    out_spikes, out_ops = plot_series(spikes, ops, args.output)
    print(f"Saved spike plot to {out_spikes}")
    print(f"Saved ops plot to {out_ops}")


if __name__ == "__main__":
    main()
