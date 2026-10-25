# Reproduce and verify EdgeAgentBench

This repository exposes synthetic incidents, two read-only tools, frozen prompts, reference diagnoses, evaluation code, and sanitized **versioned result snapshots**. The actual candidate responses, OpenClaw session transcripts, judge receipts, and source hashes remain local and are not distributed. Public artifacts therefore support inspection of the method and reported aggregates, **not independent verification of the original model executions**.

## 1. Build and test (offline)

From the repository root, with Node/npm and Python 3.10+:

```bash
npm ci
npm run build
npm test
python3 -m unittest discover -s scripts -p 'test_*.py' -v
python3 -m unittest discover -s evaluation -p 'test_*.py' -v
```

The Python tests use the standard library. Transcript decompression through `scripts/export_transcripts.py` additionally requires system `libzstd`. None of these tests invoke candidate or judge models.

## 2. Verify an archived evaluation (offline)

The latest publication selects five incidents and 40 saved runs through [`evaluation/gpt6-report-manifest-five-incidents.json`](../evaluation/gpt6-report-manifest-five-incidents.json). Its required `candidate_audit_file` remains private. On the archive machine, validate it without writing report files or invoking models:

```bash
python3 evaluation/consolidate_semantic.py report \
  --manifest evaluation/gpt6-report-manifest-five-incidents.json \
  --output-dir results/private/consolidated-gpt6-five-incidents-audited-v1 \
  --check-only

python3 evaluation/consolidate_semantic.py stage \
  --incident INC-005 \
  --mechanical-csv results/private/inc005-scored-QzYjemiU/evaluation/runs.csv \
  --search-root results/private/inc005-scored-QzYjemiU \
  --output-dir /tmp/edgeagentbench-inc005-preflight \
  --dry-run
```

The second command checks selection of the eight archived INC-005 sources without copying them. The publication's separate frozen-prompt audit read the original SQLite events for all 40 candidate sessions, verified exact prompts and tool records, and saved `results/private/frozen-candidate-audit-five-incidents-v1.json`. To independently repeat that read-only database audit, write its outputs to **new private paths**:

```bash
python3 scripts/audit_frozen_candidates.py \
  --manifest evaluation/gpt6-report-manifest-five-incidents.json \
  --output results/private/NEW-five-incident-audit.json \
  --updated-manifest results/private/NEW-five-incident-manifest-audited.json
```

The audit writes only those new private artifacts, never the databases. The tracked manifest binds consolidation to the original private audit; a new audit can be used through its newly generated manifest. Missing provenance must be investigated rather than filled with guessed values or bypassed. The INC-005 source hashes also matched the saved checksum list, and its earlier transcript audit passed all eight runs. See [INC-005 results](INC-005.md#published-results) for the primary semantic metric, evidence shortfall, and separately reported auxiliary pattern scores.

## 3. Validate the sanitized publication

The latest sanitized files are in [`results/public/oc2026.10.5-five-incidents-audited/`](../results/public/oc2026.10.5-five-incidents-audited/README.md). They were generated using the existing consolidator and allowlist publisher:

```bash
python3 evaluation/consolidate_semantic.py report \
  --manifest evaluation/gpt6-report-manifest-five-incidents.json \
  --output-dir results/private/consolidated-gpt6-five-incidents-audited-v1

python3 scripts/publish_results.py \
  --input-dir results/private/consolidated-gpt6-five-incidents-audited-v1 \
  --output-dir results/public/oc2026.10.5-five-incidents-audited
```

Both generators refuse to replace differing saved outputs. To review a regenerated publication without touching it, use a fresh temporary output directory and compare all three sanitized files. The latest snapshot preserves the earlier 32-run publication. Public files contain semantic outcomes, evidence counts, tool counts, and timing; the supplementary [INC-005 interpretation](INC-005.md#published-results) discloses the saved auxiliary deterministic root-cause pattern scores. Audit receipts and execution provenance remain private, so the public release alone cannot verify the original executions.

For a future versioned publication from a reviewed private report:

```bash
python3 scripts/publish_results.py \
  --input-dir results/private/NEW-REPORT-DIR \
  --output-dir results/public/NEW-SNAPSHOT-ID
```

The publisher validates the private `runs.csv`, `summary.json`, and `provenance.json`, then produces the allowlisted public `README.md`, `runs.csv`, and `summary.csv`. It computes incident counts, trial counts, and denominators from the archived records. The command above deliberately targets a **new snapshot directory**: report templates can evolve, but previously published outputs must not be silently overwritten. Do not delete mismatching outputs without investigating the source of the difference.

For a new evaluation snapshot, stage **only** the reviewed sanitized files (replace `NEW-SNAPSHOT-ID` with the directory name chosen above):

```bash
git add \
  results/public/NEW-SNAPSHOT-ID/README.md \
  results/public/NEW-SNAPSHOT-ID/runs.csv \
  results/public/NEW-SNAPSHOT-ID/summary.csv
git diff --cached --check
git status --short
```

Keep `.openclaw/`, OAuth profiles, raw JSON/JSONL, SQLite databases, session IDs, judge receipts, and private file paths out of published artifacts. The reference answer key is public for auditability but was not accessible to candidate agents during the benchmark.

## 4. New inference is a new experiment

Running new candidate trials requires OpenClaw `2026.10.5`, matching tool permissions and prompts, the local Qwen inference endpoint, and cloud GPT-5.6 Sol access. Invoking `evaluation/judge_semantic.py` runs GPT-6 grading, whereas `--check-only` on `evaluation/consolidate_semantic.py` strictly validates **already saved** judgments. A fresh inference run or judgment is not a byte-for-byte replay of the previously archived outputs.

The semantic judge validates its effective model, authentication mode, session identity, lack of fallback/reroute/tool use, and JSON schema. The saved experiment also has a material limitation: OpenClaw `bench-judge.contextInjection` changed between grading INC-003 and INC-001/002. The `semantic-root-v1` rubric was unchanged, but byte-identical effective judge context cannot be asserted. See [protocol](protocol.md) and [results](results.md).
