# Q2 scale16 vertical phase-shift ablation

Values compare `Algo(+phase-shift)` against the same base algorithm.

| Arrival | Base Algo | CV gain ↓ | JFI Δ ↑ | LIF gain ↓ | Throughput cost | p99 cost | mean offset | p95 offset |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| bursty | BestFit | 16.7% | 0.0679 | 21.0% | 7.6% | 464.5% | 5.94 | 16.00 |
| bursty | DRF | 16.7% | 0.0679 | 21.0% | 7.6% | 464.5% | 5.94 | 16.00 |
| bursty | P2C | 25.1% | 0.1145 | 26.3% | 9.4% | 419.5% | 5.87 | 16.00 |
| bursty | RR | 20.8% | 0.0912 | 23.1% | 8.6% | 451.3% | 5.96 | 16.00 |
| bursty | STPS-spatial | 19.5% | 0.0835 | 21.9% | 7.3% | 460.6% | 6.95 | 16.00 |
| poisson | BestFit | 11.2% | 0.0531 | 6.1% | 4.8% | 708.0% | 5.91 | 15.60 |
| poisson | DRF | 11.2% | 0.0531 | 6.1% | 4.8% | 708.0% | 5.91 | 15.60 |
| poisson | P2C | 22.3% | 0.1156 | 13.9% | 5.0% | 473.4% | 5.91 | 16.00 |
| poisson | RR | 18.8% | 0.0947 | 13.7% | 4.3% | 476.0% | 5.91 | 16.00 |
| poisson | STPS-spatial | 16.1% | 0.0775 | 10.6% | 3.2% | 429.3% | 7.63 | 16.00 |
