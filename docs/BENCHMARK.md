# Three-mode local benchmark

The benchmark measures local ledger, ticket, receipt, verification, and report overhead. It does not call models and does not measure credit cost or model quality.

| Mode | Mean seconds | CLI calls | Stages | Simulated dispatches | Report bytes | Verification |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| fast | 5.639 | 13 | 3 | 3 | 7306 | passed |
| standard | 13.096 | 30 | 8 | 9 | 8829 | passed |
| strict | 14.842 | 34 | 8 | 11 | 9371 | passed |

## Recommendation

Use `standard` as the default. It covers all eight stages and two execution experts with moderate overhead. Use `fast` for simple, well-defined tasks and `strict` for high-risk or highly constrained work.

Run:

```bash
python runtime/benchmark.py --iterations 5
```
