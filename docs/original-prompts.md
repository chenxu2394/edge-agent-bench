# Frozen original-prompt protocol

These are the frozen original user prompts for evaluated incidents under the OpenClaw `2026.10.5` direct-tool condition. Add new incident prompts here before evaluating them.

## INC-001

> Investigate incident INC-001. The telemetry publisher stopped sending messages at 14:32 UTC. Use the available incident investigation tools to determine the most likely root cause. Report your diagnosis, supporting evidence IDs, and any uncertainty. Do not assume the cause without checking evidence.

## INC-002

> Investigate incident INC-002. The telemetry publisher stopped sending messages at 09:17 UTC. Use the available incident investigation tools to determine the most likely root cause. Report your diagnosis, supporting evidence IDs, and any uncertainty. Do not assume the cause without checking evidence.

## INC-003

> Investigate incident INC-003. Downstream telemetry consumers stopped receiving messages at 11:42 UTC. Use the available incident investigation tools to determine the most likely root cause. Report your diagnosis, supporting evidence IDs, and any uncertainty. Do not assume the cause without checking evidence.

## INC-004

> Investigate incident INC-004. Downstream telemetry processing stopped accepting records at 12:14 UTC. Use the available incident investigation tools to determine the most likely root cause. Report your diagnosis, supporting evidence IDs, and any uncertainty. Do not assume the cause without checking evidence.

## INC-005

> Investigate incident INC-005. Downstream analytics stopped accepting telemetry from line-b devices at 02:20 UTC, while line-a continues updating. Use the available incident investigation tools to determine the most likely root cause. Report your diagnosis, supporting evidence IDs, and any uncertainty. Do not assume the cause without checking evidence.

## Experimental setup

- Edge computer: NVIDIA Jetson AGX Orin Developer Kit, 32 GB RAM.
- Local candidate: OpenClaw agent `bench-qwen`, model `llamacpp/qwen3.5:9b`.
- Cloud candidate: OpenClaw agent `bench-cloud`, model `openai/gpt-5.6-sol`.
- Runtime condition for these scored runs: OpenClaw `2026.10.5` and direct
  incident tools for both candidates (Code Mode disabled for both).
- Incident tools: `get_incident_logs` and `get_incident_metrics` requiring
  `incidentId`; neither candidate is intentionally supplied the hidden
  diagnosis answer key.
- Distinct independent agent sessions, with trial counts recorded in the
  evaluation manifest for each snapshot.
- Root cause scored _after_ the run by separate `bench-judge` on GPT-6 Sol,
  blinded to source candidate identity. Tool/evidence checks are deterministic.

A fresh inference run is a new trial. Do not merge it into a previously frozen evaluation snapshot.
