# Evaluation harness

This folder contains the offline evaluation tooling for PyKnora. The public snapshot intentionally excludes:

- private evaluation corpora,
- generated evaluation datasets,
- baseline/candidate configuration snapshots,
- raw evaluation results,
- internal metric reports.

You can recreate these artifacts from your own private corpus.

## Layout

| Path | Purpose |
| --- | --- |
| `build_eval_set.py` | Build a fixed evaluation set from a private corpus |
| `make_configs.py` | Generate baseline and candidate experiment configs |
| `run_eval.py` | Run evaluation against the live HTTP API |
| `gate.py` | Compare baseline and candidate runs |
| `report.py` | Render evaluation reports |
| `selfcheck.py` | Validate the evaluation toolchain |
| `corpus.py` | Prepare and verify the private corpus |

## Metrics

The harness measures:

- `recall_at_5`
- `precision_at_5`
- `citation_accuracy`
- `abstain_accuracy`
- privilege interception
- `retrieval_p95_ms`
- `chat_ttft_p95_ms`
- `chat_total_p95_ms`

See [`../EVALUATION.md`](../EVALUATION.md) for interpretation and limitations.

## General usage

```bash
# 1. Prepare and verify your private corpus
uv run python eval/corpus.py --prepare
uv run python eval/corpus.py --verify-only

# 2. Build an evaluation set
uv run python eval/build_eval_set.py --build

# 3. Generate baseline and candidate configs
uv run python eval/make_configs.py

# 4. Run evaluation
uv run python eval/run_eval.py --config eval/baseline_config.yaml --out eval/results/baseline

# 5. Compare runs
uv run python eval/gate.py --baseline eval/results/baseline --candidate eval/results/candidate
```

The exact arguments may vary depending on your corpus and workspace setup. The harness intentionally does not include private credentials or corpora.
