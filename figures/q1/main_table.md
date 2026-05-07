# Q1 Main Table — Spatial Load Balancing via Step A

Setup: cards=4 × 512 cores = 2048 cores, tasks=800, steps=512, arrival=poisson, capacity=4500, seeds=[21, 42, 99, 123, 2024]

Values are `mean ± 95% CI half-width` across seeds.

| Scheduler | card-CV ↓ | card-JFI ↑ | Max/Min ↓ | SLA viol ↓ | avg load var |
|---|---|---|---|---|---|
| RR | 0.3632 ± 0.0140 | 0.8719 ± 0.0078 | 3.397 | 0.1973 | 1316245.6 |
| BestFit | 0.2921 ± 0.0078 | 0.9105 ± 0.0043 | 2.504 | 0.1636 | 892809.6 |
| DRF | 0.2932 ± 0.0094 | 0.9099 ± 0.0046 | 2.461 | 0.1596 | 881852.4 |
| P2C | 0.4070 ± 0.0193 | 0.8461 ± 0.0104 | 3.735 | 0.2219 | 1599698.8 |
| STPS (Step A) | 0.3856 ± 0.0183 | 0.8587 ± 0.0105 | 3.362 | 0.2028 | 1402528.4 |

## Headline Numbers

- STPS card-CV vs RR:      **-6.2%** (paper claim: −20.1%)
- STPS card-CV vs BestFit: **-32.0%** (paper claim: −11.2%)
- P2C card-JFI:            **0.846** (paper claim: ≈0.67)
