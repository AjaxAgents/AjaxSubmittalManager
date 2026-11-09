# Evaluation cases

Put each past submittal in its own folder here (this folder is git-ignored except this file):

```
eval/cases/door-hardware-003/
  case.yaml
  Project Manual.pdf
  Drawings.pdf
  Door Hardware PD.pdf
```

See `eval/run_eval.py` for the `case.yaml` format. Each A/E comment that marked a deviation becomes one
`expected` entry. Target (spec V2 section 16): >= 90% of A/E deviations caught as Non-compliant or Human Review.

Run: `make eval` (real API, costs money) or `python eval/run_eval.py --mock` (offline smoke test).
