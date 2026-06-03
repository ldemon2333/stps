# Q0 Main Table — End-to-End Card-Level Load Balance

Setup: cards=4, tasks=8, steps=32, arrival=bursty, seeds=[21]

Fingerprint dir: synthetic-only (flat / pulse_t8 / pulse_t16 / bursty), to avoid scale-mixing with real-model spike counts.

Values are `mean ± 95% CI half-width` across seeds.

| Scheduler | card-CV ↓ | card-JFI ↑ | card-LIF ↓ | Max/Min ↓ |
|---|---|---|---|---|
| RR | 1.0719 ± 0.0000 | 0.5127 ± 0.0000 | 3.0769 ± 0.0000 | 3.196 ± 0.000 |
| BestFit | 0.9257 ± 0.0000 | 0.5854 ± 0.0000 | 2.6475 ± 0.0000 | 2.992 ± 0.000 |
| DRF | 0.9257 ± 0.0000 | 0.5854 ± 0.0000 | 2.6475 ± 0.0000 | 2.992 ± 0.000 |
| P2C | 0.9926 ± 0.0000 | 0.5501 ± 0.0000 | 2.8133 ± 0.0000 | 1.428 ± 0.000 |
| STPS (full) | 0.9257 ± 0.0000 | 0.5854 ± 0.0000 | 2.6475 ± 0.0000 | 2.992 ± 0.000 |

## Headline numbers (STPS vs baselines)

- vs RR     : CV +13.6%, JFI +0.0727, LIF +14.0%
- vs BestFit: CV +0.0%, JFI +0.0000, LIF +0.0%
- vs DRF    : CV +0.0%, JFI +0.0000, LIF +0.0%
- vs P2C    : CV +6.7%, JFI +0.0353, LIF +5.9%
