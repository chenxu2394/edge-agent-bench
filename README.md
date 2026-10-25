# EdgeAgentBench

**Can a local AI agent on a Jetson investigate telemetry failures with tools, and how does it compare with a cloud agent doing the same work?**

Remote sites may have limited or no internet access, and repeated cloud inference can be costly. A local model could reduce that dependence. I built EdgeAgentBench to test a concrete edge-agent task: investigate telemetry failures by calling tools, reading the returned evidence, and explaining the cause. The project compares that workflow on a Jetson-hosted model and a cloud model under the same incident prompts and tool interface.

A longer-term goal is **data sovereignty**: giving organizations more control over where operational data is processed and who can access it. The [European Commission's Data Union Strategy](https://digital-strategy.ec.europa.eu/en/policies/data-union) names data sovereignty as a policy goal; the [European Data Protection Board's transfer guide](https://www.edpb.europa.eu/sme/be-compliant/international-data-transfers_en) explains the conditions for sending personal data outside the EEA. Local inference may support greater control, but this synthetic benchmark does not assess data governance, privacy, or regulatory compliance.

## How the comparison works

The local candidate is Qwen 3.5 9B on a 32 GB NVIDIA Jetson AGX Orin; the cloud baseline is GPT-5.6 Sol. OpenClaw runs both agents and records their sessions. The repository provides the synthetic incidents, two read-only tools, and evaluation code.

Each prompt describes a failure in a synthetic edge-to-cloud telemetry pipeline. The agent can call `get_incident_logs` and `get_incident_metrics`, then must explain the root cause, cite evidence IDs, and state any uncertainty. Every tool call requires the correct `incidentId`; the agents do not receive the reference answer key.

For example, in [INC-005](docs/INC-005.md), telemetry from one production line disappears even though messages still arrive. The underlying problem is a deduplication change that uses a sequence number without the device ID, causing distinct devices' samples to collide. The incident tests whether the agent can connect the configuration change, the accepted and suppressed records, and the resulting metrics into a causal explanation.

Code Mode was disabled for both candidates in the published experiment; the tools only read synthetic data and do not change live services. See [architecture](docs/architecture.md) and the [protocol](docs/protocol.md) for the exact division of responsibilities and trial conditions.

## How answers are evaluated

| Question | Check |
|---|---|
| Did the answer identify the root cause? | Saved, candidate-blinded GPT-6 Sol judgment using the `semantic-root-v1` rubric; review flags are retained. |
| Did it cite the required observations? | Deterministic check of required evidence IDs in the final answer. |
| Did it use the tools correctly? | Audit of recorded calls, results, errors, and `incidentId` arguments. |
| How long did the candidate take? | OpenClaw candidate duration, excluding later judging time. |

The original prompts, answer key, scoring code, and sanitized result snapshots are in this repository. Private candidate responses, session transcripts, and judge receipts are needed to audit the original executions and are not published. The [reproducibility guide](docs/reproducibility.md) explains what can be checked offline and where that boundary lies.

## What the latest snapshot shows

The [five-incident audited snapshot](results/public/oc2026.10.5-five-incidents-audited/README.md) contains four saved trials per candidate and incident: 40 runs in total. The local candidate matched the cloud candidate on GPT-6-judged root-cause correctness in these cases, while taking longer on average and citing the complete required evidence less often.

| Measure | Local Jetson Qwen | Cloud GPT-5.6 Sol |
|---|---:|---:|
| GPT-6-judged correct root causes | 20/20 | 20/20 |
| Answers citing every required evidence ID | 14/20 | 16/20 |
| Mean candidate duration, excluding judging | 45.591 s | 13.892 s |
| Incident-tool errors | 0/40 calls | 0/40 calls |

Across both agents, there were no judge review flags or missing/wrong `incidentId` arguments. [INC-005](docs/INC-005.md#published-results) shows why diagnosis and evidence should be read separately: GPT-6 judged all 8/8 root causes correct, but only one answer cited every required evidence ID. Its auxiliary text-pattern scores are reported separately from semantic correctness.

These synthetic, LLM-judged results come from a small number of trials. They do not measure cloud costs or test operation without internet, and they do not establish production reliability. Different hardware and network paths limit direct speed comparisons. A judge configuration setting varied between earlier grading batches, so identical effective judge context across all batches has not been established. The [snapshot overview](results/public/oc2026.10.5-five-incidents-audited/README.md) gives the per-incident results and limitations.

Each result release has its own versioned snapshot. The [per-run CSV](results/public/oc2026.10.5-five-incidents-audited/runs.csv), [group summary](results/public/oc2026.10.5-five-incidents-audited/summary.csv), and [five-incident manifest](evaluation/gpt6-report-manifest-five-incidents.json) describe the latest release. The earlier [32-run snapshot](results/public/oc2026.10.5-four-incidents-audited/README.md) remains available as a frozen, superseded release.

## Explore the project

- [Incident fixtures](data/incidents.json) and [read-only tools](src/index.ts) define what an agent can observe.
- [Frozen prompts](docs/original-prompts.md) and the [protocol](docs/protocol.md) define the candidate task and controls.
- [Answer key](evaluation/answer-key.json), [deterministic evaluator](evaluation/evaluate.py), and [semantic judge validator](evaluation/judge_semantic.py) define the scoring boundary.
- [Transcript audit](scripts/audit_frozen_candidates.py), [consolidator](evaluation/consolidate_semantic.py), and [publisher](scripts/publish_results.py) check and package saved runs.

To build the plugin and run the offline tests:

```bash
npm ci
npm run build
npm test
python3 -m unittest discover -s scripts -p 'test_*.py' -v
python3 -m unittest discover -s evaluation -p 'test_*.py' -v
```

The tests do not invoke candidate or judge models. Replaying the archived evaluation also requires the private run and judge artifacts described in the [reproducibility guide](docs/reproducibility.md).

## License

EdgeAgentBench is available under the [MIT License](LICENSE).
