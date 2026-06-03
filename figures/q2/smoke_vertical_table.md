# Q2 smoke vertical phase-shift ablation

Values compare `Algo(+phase-shift)` against the same base algorithm.

| Arrival | Base Algo | CV gain ↓ | JFI Δ ↑ | LIF gain ↓ | Throughput cost | p99 cost | mean offset | p95 offset |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| bursty | BestFit | -19.2% | -0.0685 | -7.8% | 13.7% | 22.5% | 2.25 | 6.00 |
| bursty | DRF | -19.2% | -0.0685 | -7.8% | 13.7% | 22.5% | 2.25 | 6.00 |
| bursty | P2C | 8.6% | 0.0414 | 4.0% | 15.2% | 25.6% | 2.27 | 6.21 |
| bursty | RR | 12.4% | 0.0438 | 4.9% | 10.0% | 15.4% | 1.65 | 3.65 |
| bursty | STPS-spatial | -18.7% | -0.0710 | -7.7% | 13.7% | 22.5% | 2.25 | 6.00 |
| poisson | BestFit | -12.3% | -0.0503 | -5.2% | 16.0% | 21.8% | 2.20 | 6.83 |
| poisson | DRF | -12.3% | -0.0503 | -5.2% | 16.0% | 21.8% | 2.20 | 6.83 |
| poisson | P2C | 5.5% | 0.0234 | 2.4% | 14.7% | 20.4% | 2.10 | 5.29 |
| poisson | RR | 6.4% | 0.0233 | 2.6% | 8.9% | 13.9% | 1.62 | 3.52 |
| poisson | STPS-spatial | -32.6% | -0.1136 | -12.9% | 16.0% | 21.8% | 2.20 | 6.83 |
