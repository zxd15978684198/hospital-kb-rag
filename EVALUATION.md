# Evaluation

PyKnora has an offline RAG evaluation harness. Its goal is to measure whether the system can retrieve the right evidence, cite it correctly, abstain when evidence is insufficient, and reject unauthorized access.

The public repository includes the evaluation scripts, but not the private corpus or raw evaluation results. This avoids publishing third-party or company-specific material while keeping the methodology reviewable.

## What is measured

| Metric | Question it answers |
| --- | --- |
| Recall@5 | Did retrieval find the gold chunk? |
| Precision@5 | How much of the top-5 result set was relevant? |
| Citation accuracy | Did cited evidence actually support the answer? |
| Abstention accuracy | Did the system refuse when no evidence existed? |
| Privilege interception | Did unauthorized workspace access return 404 without leaking snippets? |
| Retrieval P95 | How fast was the retrieval endpoint? |
| Chat total P95 | How long did the complete streamed answer take? |

## Dataset categories

The evaluation set is grouped by behaviour:

- **fact**: direct factual questions.
- **exact_term**: questions requiring a fixed phrase.
- **synonym**: questions whose wording differs from the source document.
- **version**: questions that must read the current active document version.
- **no_evidence**: questions with no supporting corpus evidence; expected answer is abstention.
- **injection**: prompt-instruction attempts embedded in or around documents.
- **poison**: retired, forged or unapproved sources that must not be treated as evidence.
- **privilege**: cross-workspace access attempts.

## Representative internal run

A recent 30-question baseline used three repeats:

| Metric | Value |
| --- | --- |
| Recall@5 | 1.000 |
| Precision@5 | 0.200 |
| Citation accuracy | 0.750 |
| Abstention accuracy | 0.636 |
| Privilege interception | 1.000 |
| Retrieval P95 | 81.38 ms |
| Chat total P95 | 9054.36 ms |

Interpretation:

- `Precision@5` is bounded by the dataset design. Each answer-style question has one gold chunk and retrieval returns five items, so the maximum value is `0.200`.
- Retrieval latency is measured against the local stack and is not a production benchmark.
- Chat latency is dominated by the external model provider and should not be read as application overhead.
- The dataset is small. These numbers are regression signals, not marketing claims.

## Reproducing the harness

The evaluation scripts are under [`eval/`](./eval/). They call the running HTTP service rather than importing application internals, so the measured path matches the real client path.

General flow:

1. Start PostgreSQL, run migrations and seed a private evaluation workspace.
2. Start the API and Worker.
3. Build or load a private evaluation set.
4. Run the baseline and candidate configurations.
5. Generate metrics and compare runs.

Because the corpus is intentionally excluded from this public snapshot, you need to provide your own document set before running a complete evaluation.
