# Results and interpretation

The latest audited release evaluates five synthetic incidents with four independent trials per candidate and incident: 40 saved runs in total. Both candidates used the same two read-only incident tools with Code Mode disabled. Root-cause correctness comes from saved, candidate-blinded GPT-6 Sol judgments using the `semantic-root-v1` rubric; evidence citation, tool correctness, and candidate duration are reported separately.

| Candidate | GPT-6 correct | Complete evidence | Tool errors | Mean diagnosis time |
| --- | ---: | ---: | ---: | ---: |
| Cloud GPT-5.6 Sol | 20/20 | 16/20 | 0/40 calls | 13.892 s |
| Jetson Qwen 3.5 9B | 20/20 | 14/20 | 0/40 calls | 45.591 s |

Across both candidates, the saved judge marked 40/40 root causes correct with no review flags. Thirty of 40 answers cited every required evidence ID. All 80 incident-tool calls returned results, with no tool errors or missing/wrong `incidentId` arguments. Judge latency is excluded from the duration figures.

Read the [five-incident results overview](../results/public/oc2026.10.5-five-incidents-audited/README.md), download the [per-run CSV](../results/public/oc2026.10.5-five-incidents-audited/runs.csv) or [group CSV](../results/public/oc2026.10.5-five-incidents-audited/summary.csv), and see the [INC-005 interpretation](INC-005.md#published-results) for the largest evidence-citation gap.

The earlier [32-run snapshot](../results/public/oc2026.10.5-four-incidents-audited/README.md) is retained as a frozen, superseded release. Result denominators belong to each snapshot and must not be combined as independent trials.

## Limits on interpretation

- These are saved **LLM-judged** outcomes on five explicit synthetic fixtures, not human-verified accuracy over real incidents.
- Checking an evidence ID proves its presence in the final response, not the correctness of the associated explanation.
- Different hardware, model sizes, serving paths, and networks prevent direct hardware-normalized inference-speed comparisons.
- The judge's OpenClaw `contextInjection` setting differed between the INC-003 and INC-001/002 grading batches. The judge rubric stayed fixed, but identical effective model instructions were **not** established.
- Only private archives contain the raw candidate replies, judge receipts, full tool traces, and hashes needed to audit the saved executions. The public CSVs intentionally omit these potentially sensitive records, so the public release alone cannot independently verify the original executions.

For fixed prompts and trial controls see [protocol](protocol.md); for validation and publication instructions see [reproducibility](reproducibility.md).
