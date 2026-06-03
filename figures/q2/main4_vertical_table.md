# Q2 main4 vertical phase-shift ablation

Values compare `Algo(+phase-shift)` against the same base algorithm.

| Arrival | Base Algo | CV gain ↓ | JFI Δ ↑ | LIF gain ↓ | Throughput cost | p99 cost | mean offset | p95 offset |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| bursty | BestFit | 12.5% | 0.0460 | 7.7% | 10.8% | 574.7% | 5.81 | 16.00 |
| bursty | DRF | 12.5% | 0.0460 | 7.7% | 10.8% | 574.7% | 5.81 | 16.00 |
| bursty | P2C | 25.1% | 0.1092 | 14.1% | 8.7% | 506.4% | 5.86 | 16.00 |
| bursty | RR | 22.0% | 0.0885 | 12.7% | 10.5% | 546.9% | 5.81 | 16.00 |
| bursty | STPS-spatial | 17.2% | 0.0669 | 10.6% | 7.3% | 557.0% | 6.47 | 16.00 |
| poisson | BestFit | 12.7% | 0.0513 | 10.1% | 5.4% | 625.4% | 5.85 | 15.20 |
| poisson | DRF | 12.7% | 0.0513 | 10.1% | 5.4% | 625.4% | 5.85 | 15.20 |
| poisson | P2C | 27.2% | 0.1248 | 16.4% | 6.3% | 445.4% | 5.67 | 16.00 |
| poisson | RR | 25.9% | 0.1151 | 16.8% | 6.2% | 444.1% | 5.71 | 16.00 |
| poisson | STPS-spatial | 18.7% | 0.0777 | 13.4% | 4.2% | 368.1% | 6.33 | 16.00 |
