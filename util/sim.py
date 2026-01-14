from __future__ import annotations

import logging
import os
import random
from datetime import datetime

from util.task import Task


def setup_logging(log_dir: str) -> str:
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"simulation_{timestamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )
    return log_path


def create_task(task_id: int, arrival_step: int) -> Task:
    neuron_count = random.randint(300, 1050)
    complexity_ratio = random.uniform(0.6, 1.4)
    base_state_mb = random.uniform(8, 20)
    state_size_mb = base_state_mb * (0.5 + 0.5 * complexity_ratio)
    duration_steps = random.randint(3, 12)
    return Task(
        task_id=task_id,
        state_size_mb=state_size_mb,
        neuron_count=neuron_count,
        complexity_ratio=complexity_ratio,
        arrival_step=arrival_step,
        duration_steps=duration_steps,
    )


def build_arrival_plan(mode: str, total_tasks: int, steps: int) -> list[int]:
    plan = [0] * max(steps, 0)
    if steps <= 0:
        return plan

    if mode == "poisson":
        rate = max(0.1, total_tasks / float(steps))
        produced = 0
        t = 0.0
        while produced < total_tasks:
            t += random.expovariate(rate)
            if t >= steps:
                break
            plan[int(t)] += 1
            produced += 1
        while produced < total_tasks:
            plan[produced % steps] += 1
            produced += 1

    elif mode == "bursty":
        remaining = total_tasks
        if steps == 1:
            plan[0] = total_tasks
        else:
            burst_points = [0, max(1, steps // 2)]
            burst_weights = [0.6, 0.3]
            for step_idx, weight in zip(burst_points, burst_weights):
                alloc = min(remaining, int(total_tasks * weight))
                plan[step_idx] += alloc
                remaining -= alloc
            idx = 0
            while remaining > 0:
                plan[idx % steps] += 1
                remaining -= 1
                idx += 1

    elif mode == "mixed":
        base = int(total_tasks * 0.4)
        for i in range(base):
            plan[i % steps] += 1
        remaining = total_tasks - base
        rate = max(0.1, remaining / float(steps))
        produced = 0
        t = 0.0
        while produced < remaining:
            t += random.expovariate(rate)
            if t >= steps:
                break
            plan[int(t)] += 1
            produced += 1
        while produced < remaining:
            plan[produced % steps] += 1
            produced += 1

    else:
        raise ValueError(f"Unsupported arrival mode: {mode}")

    return plan
