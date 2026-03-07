from __future__ import annotations

import logging
import os
import random
from datetime import datetime
import numpy as np

from util.task import Task


def setup_logging(log_dir: str) -> str:
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"simulation_{timestamp}.log")

    root = logging.getLogger()
    # Prevent duplicate handlers when called multiple times
    if not root.handlers:
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(log_path)
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(sh)
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
        # 使用多项分布模拟固定总数的随机到达
        # 需要 import numpy as np
        plan = np.random.multinomial(total_tasks, [1/steps]*steps).tolist()

    elif mode == "bursty":
        """
        使用帕累托分布生成突发流量。
        shape (alpha) 越小，突发性越强（长尾效应越明显）。
        通常取值 1.0 - 3.0 之间。
        """
        # 1. 生成基础概率分布
        # 使用 Pareto 分布生成权重，然后归一化
        # numpy.random.pareto accepts `a` (shape) and `size` parameters
        raw_weights = np.random.pareto(a=3.0, size=steps)
        probs = raw_weights / raw_weights.sum() 
        
        # 2. 根据概率分配任务 (Multinomial Distribution)
        # 这比 while 循环快得多，且能保证总数为 total_tasks
        plan = np.random.multinomial(total_tasks, probs).tolist()


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
