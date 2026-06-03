# Q0 Main Table — End-to-End Card-Level Load Balance

Setup: cards=4, tasks=800, steps=512, arrival=bursty, seeds=[21, 42, 99, 123, 2024]

Fingerprint dir: synthetic-only (flat / pulse_t8 / pulse_t16 / bursty), to avoid scale-mixing with real-model spike counts.

Values are `mean ± 95% CI half-width` across seeds.

| Scheduler | card-CV ↓ | card-JFI ↑ | card-LIF ↓ | Max/Min ↓ |
|---|---|---|---|---|
| RR | 0.2865 ± 0.0131 | 0.9060 ± 0.0065 | 1.2724 ± 0.0197 | 6.115 ± 2.010 |
| BestFit | 0.2864 ± 0.0166 | 0.9043 ± 0.0084 | 1.2748 ± 0.0212 | 6.060 ± 1.125 |
| DRF | 0.2864 ± 0.0166 | 0.9043 ± 0.0084 | 1.2748 ± 0.0212 | 6.060 ± 1.125 |
| P2C | 0.2940 ± 0.0109 | 0.9014 ± 0.0058 | 1.2819 ± 0.0175 | 5.803 ± 1.031 |
| STPS (full) | 0.3074 ± 0.0042 | 0.8960 ± 0.0025 | 1.2934 ± 0.0100 | 6.227 ± 2.371 |

## Headline numbers (STPS vs baselines)

- vs RR     : CV -7.3%, JFI -0.0100, LIF -1.6%
- vs BestFit: CV -7.3%, JFI -0.0083, LIF -1.5%
- vs DRF    : CV -7.3%, JFI -0.0083, LIF -1.5%
- vs P2C    : CV -4.6%, JFI -0.0054, LIF -0.9%
