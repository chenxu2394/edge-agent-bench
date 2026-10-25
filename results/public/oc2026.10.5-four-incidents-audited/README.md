# EdgeAgentBench: GPT-6 semantic evaluation

**Experiment:** OpenClaw 2026.10.5, two direct read-only incident tools, Code Mode disabled.
**Candidates:** Cloud GPT-5.6 Sol and Jetson Qwen 3.5 9B. **Judge:** GPT-6 Sol (`semantic-root-v1`).
**Dataset:** 4 synthetic incidents, 4 independent trials per candidate and incident, 32 saved runs.

- GPT-6-judged root-cause matches: **32/32** (LLM-judged, not independent human ground truth).
- Full required evidence-ID coverage: **29/32**.
- Tool calls: **64**; tool errors: **0**; missing/wrong `incidentId`: **0**.

| Incident | Candidate | GPT-6 root-cause matches | Full evidence | Tool calls | Mean diagnosis time |
|---|---|---:|---:|---:|---:|
| INC-001 | Cloud (GPT-5.6 Sol) | 4/4 | 4/4 | 8 | 10.159 s |
| INC-001 | Jetson (Qwen 3.5 9B) | 4/4 | 4/4 | 8 | 39.203 s |
| INC-002 | Cloud (GPT-5.6 Sol) | 4/4 | 4/4 | 8 | 11.189 s |
| INC-002 | Jetson (Qwen 3.5 9B) | 4/4 | 4/4 | 8 | 48.928 s |
| INC-003 | Cloud (GPT-5.6 Sol) | 4/4 | 4/4 | 8 | 13.307 s |
| INC-003 | Jetson (Qwen 3.5 9B) | 4/4 | 4/4 | 8 | 41.831 s |
| INC-004 | Cloud (GPT-5.6 Sol) | 4/4 | 4/4 | 8 | 23.301 s |
| INC-004 | Jetson (Qwen 3.5 9B) | 4/4 | 1/4 | 8 | 43.551 s |

## Interpretation and limits

The evaluated synthetic incident IDs are INC-001, INC-002, INC-003, INC-004. All scores refer to archived original-prompt candidate runs; GPT-6 grading time is excluded from candidate latency.

The primary root-cause metric uses saved GPT-6 semantic judgments. Required evidence IDs and incident tool calls are scored separately by deterministic checks. Citation presence does not prove a citation is interpreted correctly.

Each candidate was evaluated in 4 independent sessions per incident. Different model sizes and compute infrastructure prevent hardware-normalized speed conclusions. This controlled synthetic benchmark does not establish production incident reliability.

Effective judge-context equivalence must be verified for each evaluation snapshot. The initial archived experiment disclosed a `contextInjection` variation between grading batches; see `docs/results.md`.

See `docs/reproducibility.md` for the private provenance and validation workflow. Raw traces, credentials, session IDs, full judge receipts, and private file paths are intentionally excluded from these public artifacts.
