# Architecture and scope

EdgeAgentBench is a controlled incident-investigation benchmark built on OpenClaw. It compares an on-device language model with a cloud model while holding the incident data, instructions, and tools constant.

## Components

```text
                    Incident prompt
                          |
                  OpenClaw 2026.10.5
                          |
               +----------+----------+
               |                     |
          bench-qwen            bench-cloud
      llamacpp/qwen3.5:9b     openai/gpt-5.6-sol
       Jetson AGX Orin           Cloud API
               |                     |
               +----------+----------+
                          |
                  Two read-only tools
               /                      \
      get_incident_logs       get_incident_metrics
               \                      /
                data/incidents.json
                          |
                 Answers + session traces
                          |
           Deterministic tool/evidence scoring
            + GPT-6 Sol semantic root judge
                          |
                Frozen report + CSV
```

**Code Mode was disabled for both agents in the published experiment.** Every run reported `codeModeEngaged: false`, and the session traces showed only the two expected incident-tool calls. The agents did not use OpenClaw's Code Mode `exec` orchestration bridge.

## Responsibilities

| Provided by this repository | Provided by OpenClaw / model providers |
| --- | --- |
| Synthetic incident fixtures (`data/incidents.json`) | Agent runtime and session lifecycle |
| `get_incident_logs` and `get_incident_metrics` plugin tools (`src/index.ts`) | Model/provider integrations |
| Plugin tests and fixture setup utilities | Tool invocation and delivery to models |
| Deterministic evidence/tool scorer and GPT-6 verdict validation | CLI execution and session tracing |
| Published benchmark protocol and aggregated data | Inference runtime / cloud API |

The incident tools retrieve synthetic data. They do **not** investigate, repair, or change live Jetson services. This is not an automated incident-remediation system. Root-cause correctness is scored by a separate GPT-6 Sol semantic judgment.

## Runtime configuration used for the benchmark

| Setting | Value |
| --- | --- |
| OpenClaw | `2026.10.5` |
| Local hardware | NVIDIA Jetson AGX Orin Developer Kit, 32 GB |
| Local model | `llamacpp/qwen3.5:9b` |
| Cloud model | `openai/gpt-5.6-sol` |
| Agent IDs | `bench-qwen`, `bench-cloud` |
| Tool surface | `get_incident_logs`, `get_incident_metrics` |
| Code Mode | Disabled for both agents |
| Tool access | Read-only synthetic incidents |

Each tool requires an `incidentId` argument. Tool-call arguments and errors were audited from recorded session events, not inferred solely from the final response. The archived report includes a separate judge agent (`bench-judge`) and optional checks of original user prompts from archived SQLite events. A judge configuration variation across the incident batches limits claims of identical effective judge context; see [results](results.md).

Agent credentials, personal OpenClaw configuration, and session databases are not part of the published repository. For the precise test procedure, see [Protocol](protocol.md); for setup limitations, see [Reproducibility](reproducibility.md).
