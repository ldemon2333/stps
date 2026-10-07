"""Reproducible, external task arrival sequences for cluster experiments."""
from math import isfinite

import numpy as np


def _integer(value, name, *, positive=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer, excluding booleans")
    lower = 1 if positive else 0
    if value < lower:
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return int(value)


def make_arrival_ticks(mode, count, *, seed=0, poisson_rate=1.2,
                       burst_size=6, burst_interval=6):
    """Return exactly count sorted, 1-based physical arrival Ticks.

    Poisson arrivals use independent exponential gaps at poisson_rate
    tasks per physical Tick and bin continuous times with floor(time) + 1.
    Thus disjoint whole-Tick bins have Poisson counts before truncation at the
    requested task count. Sampling gaps avoids scanning potentially many empty
    Ticks. Bursty arrivals place fixed-size batches at 1, 1 + interval, ...;
    the last batch may be smaller. Both modes are independent of card progress.
    """
    if mode not in ("poisson", "bursty"):
        raise ValueError("mode must be 'poisson' or 'bursty'")
    count = _integer(count, "count")
    seed = _integer(seed, "seed")
    if mode == "bursty":
        burst_size = _integer(burst_size, "burst_size", positive=True)
        burst_interval = _integer(burst_interval, "burst_interval", positive=True)
        return [1 + (index // burst_size) * burst_interval for index in range(count)]

    if isinstance(poisson_rate, (bool, np.bool_)) or not isinstance(
            poisson_rate, (int, float, np.integer, np.floating)):
        raise ValueError("poisson_rate must be a finite positive number")
    rate = float(poisson_rate)
    if not isfinite(rate) or rate <= 0:
        raise ValueError("poisson_rate must be a finite positive number")
    if count == 0:
        return []
    scale = 1.0 / rate
    if not isfinite(scale):
        raise ValueError("poisson_rate is too small for finite arrival times")
    rng = np.random.default_rng(seed)
    with np.errstate(over="ignore"):
        times = np.cumsum(rng.exponential(scale=scale, size=count))
    # Beyond this boundary float64 cannot reliably distinguish adjacent Ticks.
    if not np.isfinite(times).all() or times[-1] >= 2 ** 53:
        raise ValueError("arrival times exceed exact float64 Tick range")
    return [int(time) + 1 for time in times]
