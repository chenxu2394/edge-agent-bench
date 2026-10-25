# EdgeAgentBench benchmark protocol

## Experimental condition

Each evaluation snapshot records its selected incident IDs and number of trials per candidate in its manifest. The archived direct-tool experiment used OpenClaw `2026.10.5` with the same read-only plugin tools, `get_incident_logs` and `get_incident_metrics`, for both candidates. Code Mode was disabled and the incident datasets were shared. Published denominators belong to the individual result snapshot, not to the project as a whole.

- **Local candidate:** `bench-qwen` (`llamacpp/qwen3.5:9b` on a 32 GB NVIDIA Jetson AGX Orin).
- **Cloud candidate:** `bench-cloud` (`openai/gpt-5.6-sol`).
- **Semantic judge:** isolated `bench-judge` (`openai/gpt-6-sol`), run **after** candidate outputs were recorded, with no candidate identity label in the judge prompt.
- **Trial isolation:** one fresh candidate session per trial, with the same prompt and trial count for both candidates. The trial count is configured in the evaluation manifest.

## Scenarios

| Incident | Symptom | Expected mechanism | Required evidence IDs |
|---|---|---|---|
| INC-001 | Publisher stops at 14:32 UTC | Disk exhaustion blocks queue persistence | `L-002`, `M-001` |
| INC-002 | Publisher stops at 09:17 UTC | Expired MQTT mTLS client certificate blocks authentication | `L-013`, `M-013` |
| INC-003 | Downstream consumers stop at 11:42 UTC | Publisher topic v2 does not match consumer's v1 subscription | `L-022`, `L-024`, `M-025` |
| INC-004 | Downstream processing stops at 12:14 UTC | Unsynchronized publisher clock produces event timestamps outside consumer freshness window | `L-033`, `L-035`, `M-038` |
| INC-005 | line-b analytics stops at 02:20 UTC while line-a continues | Sequence-only deduplication key collides across devices and suppresses distinct samples | `L-041`, `L-043`, `L-045`, `M-050` |

The **verbatim original prompts** for evaluated incidents are recorded in [`docs/original-prompts.md`](original-prompts.md). They ask the agent to investigate using available tools and report a root cause, evidence IDs, and uncertainty; they do not include the reference diagnosis or required evidence IDs.

The reference incident key is in [`evaluation/answer-key.json`](../evaluation/answer-key.json). It is committed for transparent offline evaluation, but candidate agents were not intentionally given access to that file, the repository filesystem, or a hidden answer key during scored runs.

## Scoring and audit

1. **Primary root-cause metric:** blinded GPT-6 Sol judge using the fixed `semantic-root-v1` rubric. The stored verdict is the reported LLM-judged outcome, and any `requires_review` flags remain visible.
2. **Evidence coverage:** a deterministic check for presence of each incident's required evidence IDs in the candidate's final answer. This does not guarantee correct interpretation of a cited record.
3. **Tool correctness:** the archived SQLite event exporter audits allowed tool names, required `incidentId`, complete results, tool errors, and execution-mode metadata. The deterministic evaluator preserves per-run counts. Consolidation checks the saved source hashes against the judge and mechanical records.
4. **Timing:** candidate end-to-end OpenClaw duration, excluding the later GPT-6 grading calls.

[`evaluation/consolidate_semantic.py`](../evaluation/consolidate_semantic.py) checks distinct candidate sessions, explicit Code Mode state, and tool-call metadata. When original SQLite sessions remain available, [`scripts/audit_frozen_candidates.py`](../scripts/audit_frozen_candidates.py) can independently verify the archived user-message bytes against the frozen prompts and bind those checks to a new private report manifest. The saved candidate/judge records can still be validated offline without database access; in that case the report discloses the original-prompt audit limitation.

## Adding another incident

Define the new incident and evidence IDs in [`data/incidents.json`](../data/incidents.json), add its trusted diagnosis and required evidence in [`evaluation/answer-key.json`](../evaluation/answer-key.json), and freeze its exact prompt in [`docs/original-prompts.md`](original-prompts.md). Record the run sources, deterministic scoring CSV, and judge directory under the new incident ID in a **new manifest**. Use a separate versioned results directory so existing snapshots remain unchanged.

## Limitations

The two models run on different hardware and through different inference paths. The current synthetic scenarios and limited independent trials are insufficient to estimate production incident reliability or general model quality. A single LLM judge has its own limitations. The judge's `contextInjection` setting changed between the INC-003 and INC-001/002 grading batches; the report must preserve this disclosure and may not claim byte-identical effective judge instructions.
