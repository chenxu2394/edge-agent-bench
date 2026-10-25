#!/usr/bin/env python3
"""Assemble archived runs and produce a GPT-6-judged EdgeAgentBench report.

Python 3.10+ and the existing evaluation/judge_semantic.py (v1.1) required.
Only stage copies archived results; report never invokes a model or reruns agents.
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import judge_semantic as judge

SCHEMA = "edgeagentbench-consolidated-gpt6-v1"
RUN_RE = re.compile(r"^(?:bench-)?(cloud|qwen)-inc(\d{3})-(\d{3})$")
AGENT_MAP = {"cloud": "bench-cloud", "qwen": "bench-qwen"}
RUN_FIELDS = [
    "run_id",
    "incident",
    "agent",
    "candidate_model",
    "source_sha256",
    "candidate_session_id",
    "candidate_code_mode",
    "root_cause_correct_gpt6",
    "requires_review",
    "required_evidence_found",
    "required_evidence_total",
    "full_required_evidence",
    "tool_calls",
    "tool_results",
    "tool_errors",
    "missing_incident_id_args",
    "candidate_duration_s",
    "judge_duration_ms",
    "judge_session_id",
    "rubric_version",
    "validator_version",
    "recovered_from_raw",
    "judge_prompt_sha256",
    "citation_warning",
]


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return sha_bytes(path.read_bytes())


def canon(name):
    stem = Path(str(name)).stem
    m = RUN_RE.fullmatch(stem)
    if not m:
        raise ValueError(f"Not a scored original-prompt run ID: {name}")
    return f"{m.group(1)}-inc{m.group(2)}-{m.group(3)}"


def incident_of(run_id):
    m = RUN_RE.fullmatch(canon(run_id))
    return f"INC-{m.group(2)}"


def agent_of(run_id):
    m = RUN_RE.fullmatch(canon(run_id))
    return AGENT_MAP[m.group(1)]


def read_csv(path):
    if not path.is_file():
        raise ValueError(f"Missing CSV: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            raise ValueError(f"Missing CSV header: {path}")
        return list(r)


def parse_bool(value, where):
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in ("true", "1"):
        return True
    if str(value).strip().lower() in ("false", "0"):
        return False
    raise ValueError(f"Invalid boolean {value!r}: {where}")


def mechanical_for_incident(csv_path, incident, per_agent):
    selected = {}
    for row in read_csv(csv_path):
        rid = canon(row.get("run_id", ""))
        if incident_of(rid) != incident:
            continue
        if rid in selected:
            raise ValueError(f"Duplicate mechanical run: {rid}")
        if row.get("incident", incident) != incident:
            raise ValueError(f"Mechanical incident mismatch: {rid}")
        if row.get("agent", agent_of(rid)) != agent_of(rid):
            raise ValueError(f"Mechanical agent mismatch: {rid}")
        selected[rid] = row
    expected = {
        f"{model}-inc{incident[-3:]}-{trial:03d}"
        for model in ("cloud", "qwen")
        for trial in range(1, per_agent + 1)
    }
    if set(selected) != expected:
        raise ValueError(
            f"Mechanical selection {incident}: missing={sorted(expected - set(selected))} extra={sorted(set(selected) - expected)}"
        )
    return selected


def resolve_mechanical_source(row, search_root, canonical):
    raw = row.get("source_json") or ""
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_file():
        return None
    if canon(p.name) != canonical:
        return None
    if p.resolve().is_relative_to(search_root.resolve()):
        return p.resolve()
    return None


def stage(args):
    root = args.search_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Search directory missing: {root}")
    dest = args.output_dir.resolve()
    if dest == root:
        raise ValueError("Staging destination must not equal search root")
    selected = mechanical_for_incident(
        args.mechanical_csv, args.incident, args.per_agent
    )
    wanted = set(selected)
    matches = defaultdict(list)
    for p in root.rglob("*.json"):
        if p.resolve().is_relative_to(dest):
            continue
        try:
            rid = canon(p.name)
        except ValueError:
            continue
        if rid in wanted:
            matches[rid].append(p.resolve())
    plan = []
    for rid in sorted(wanted):
        preferred = resolve_mechanical_source(selected[rid], root, rid)
        possible = sorted(set(matches[rid]))
        if preferred in possible:
            chosen = preferred
        elif len(possible) == 1:
            chosen = possible[0]
        else:
            raise ValueError(
                f"Expected one matching source for {rid}, found {len(possible)} under {root}: "
                + ", ".join(map(str, possible[:12]))
                + ". Narrow --search-root to the Oct 20 direct-tool batch."
            )
        raw = json.loads(chosen.read_text(encoding="utf-8"))
        if raw.get("status") != "ok":
            raise ValueError(f"Source run is not status=ok: {chosen}")
        judge.source_answer(chosen)
        target = dest / f"bench-{rid}.json"
        if target.exists() and sha_file(target) != sha_file(chosen):
            raise ValueError(f"Conflicting staged source, refusing overwrite: {target}")
        plan.append(
            {
                "run_id": rid,
                "incident": args.incident,
                "source": str(chosen),
                "staged_path": str(target),
                "sha256": sha_file(chosen),
            }
        )
    for item in plan:
        print(
            f"{'DRY RUN' if args.dry_run else 'STAGE'} {item['run_id']}: {item['source']} sha256={item['sha256'][:16]}"
        )
    if args.dry_run:
        print(f"Source preflight complete: {len(plan)} files; NO files written")
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in plan:
        path = Path(item["staged_path"])
        if not path.exists():
            tmp = path.with_suffix(".json.tmp")
            shutil.copyfile(item["source"], tmp)
            if sha_file(tmp) != item["sha256"]:
                tmp.unlink(missing_ok=True)
                raise ValueError(f"Copy hash mismatch: {path}")
            tmp.replace(path)
    manifest = dest / f"source-manifest-{args.incident.lower()}.json"
    manifest.write_text(
        json.dumps({"schema": SCHEMA, "mode": "stage", "files": plan}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Archived {len(plan)} unchanged results under {dest}")


def load_settings(manifest_path):
    obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    if obj.get("schema") != SCHEMA:
        raise ValueError(f"Unexpected manifest schema: {obj.get('schema')}")
    if (
        obj.get("judge_model") != "gpt-6-sol"
        or obj.get("rubric") != judge.RUBRIC_VERSION
    ):
        raise ValueError(
            "Report requires the same GPT-6 Sol semantic rubric as the judge runner"
        )
    if obj.get("validator") != judge.VALIDATOR_VERSION:
        raise ValueError(
            "Report requires the installed, tested judge validator version"
        )
    if obj.get("experiment", {}).get("runtime") != "OpenClaw 2026.10.5":
        raise ValueError("Expected the direct-tool OpenClaw 2026.10.5 condition")
    groups = obj.get("incident_groups")
    if (
        not isinstance(groups, dict)
        or not groups
        or any(not re.fullmatch(r"INC-\d{3}", incident) for incident in groups)
    ):
        raise ValueError(
            "A nonempty incident_groups mapping with INC-NNN IDs is required"
        )
    per_agent = obj.get("trials_per_agent_per_incident")
    if type(per_agent) is not int or per_agent < 1:
        raise ValueError("Invalid trials_per_agent_per_incident")
    return obj


def load_frozen_prompts(path=Path("docs/original-prompts.md")):
    """Read the exact, published original prompts; fail on missing/duplicate entries."""
    prompts = {}
    headings = set()
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.fullmatch(r"## (INC-\d{3})", line.strip())
        if heading:
            current = heading.group(1)
            if current in headings:
                raise ValueError(f"Duplicate frozen prompt for {current}")
            headings.add(current)
        elif current and line.startswith("> "):
            if current in prompts:
                raise ValueError(f"Duplicate prompt paragraph for {current}")
            prompts[current] = line[2:]
    if not prompts or set(prompts) != headings:
        raise ValueError("Every declared incident must have exactly one frozen prompt")
    return prompts


def frozen_prompt_for(incident):
    return load_frozen_prompts()[incident]


def filename_map(directory, incident):
    if not directory.is_dir():
        raise ValueError(f"Source dir missing: {directory}")
    mapped = {}
    for p in directory.iterdir():
        if not p.name.endswith(".json"):
            continue
        try:
            key = canon(p.name)
        except ValueError:
            continue
        if incident_of(key) != incident:
            continue
        if key in mapped:
            raise ValueError(
                f"Duplicate original source files for {key} under {directory}"
            )
        mapped[key] = p
    return mapped


def checked_int(value, name):
    try:
        n = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"Non-integer {name}: {value!r}") from None
    if n < 0:
        raise ValueError(f"Negative {name}: {value}")
    return n


def checked_float(value, name):
    try:
        n = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Non-numeric {name}: {value!r}") from None
    if n < 0 or not (n < float("inf")):
        raise ValueError(f"Invalid {name}: {value}")
    return n


def validate_one(
    rid,
    incident,
    source,
    mechanical,
    judge_dir,
    answer_key,
    expected_model,
    seen_sessions,
    seen_candidate_sessions,
):
    raw_bytes = source.read_bytes()
    candidate = json.loads(raw_bytes)
    if candidate.get("status") != "ok" or mechanical.get("status") != "ok":
        raise ValueError(f"Agent execution not ok: {rid}")
    ans = judge.source_answer(source)
    meta = (candidate.get("result") or {}).get("meta") or {}
    agent_meta = meta.get("agentMeta") or {}
    candidate_session = agent_meta.get("sessionId")
    if not isinstance(candidate_session, str) or not candidate_session.strip():
        raise ValueError(f"Missing candidate session ID for {rid}")
    if candidate_session in seen_candidate_sessions:
        raise ValueError(
            f"Duplicate candidate session ID for {rid}: {candidate_session}"
        )
    seen_candidate_sessions.add(candidate_session)
    # OpenClaw 2026.10.5 records candidate execution mode in agentMeta.
    # Require explicit False rather than interpreting missing metadata as False.
    if agent_meta.get("codeModeEngaged") is not False:
        raise ValueError(f"Candidate Code Mode was not explicitly disabled for {rid}")
    model = agent_meta.get("model")
    provider_expected, sep, model_expected = expected_model.partition("/")
    if not sep or model not in (expected_model, model_expected):
        raise ValueError(
            f"Candidate model does not match manifest for {rid}: {model!r}"
        )
    provider_actual = agent_meta.get("provider")
    if provider_actual is not None and provider_actual != provider_expected:
        raise ValueError(
            f"Candidate provider does not match manifest for {rid}: {provider_actual!r}"
        )
    measured_ms = checked_float(meta.get("durationMs"), f"source duration {rid}")
    measured_s = round(measured_ms / 1000.0, 3)
    reported_s = checked_float(
        mechanical.get("duration_s"), f"mechanical duration {rid}"
    )
    if abs(measured_s - reported_s) > 0.005:
        raise ValueError(
            f"Candidate/mechanical timing mismatch for {rid}: {measured_s} vs {reported_s}"
        )
    required = answer_key[incident]["requiredEvidenceIds"]
    actual_cited = sum(
        bool(re.search(r"\b" + re.escape(ev) + r"\b", ans)) for ev in required
    )
    n = len(required)
    mechanical_evidence = mechanical.get("evidence_coverage", "")
    if mechanical_evidence != f"{actual_cited}/{n}":
        raise ValueError(
            f"Evidence coverage mismatch for {rid}: source {actual_cited}/{n}, evaluator {mechanical_evidence}"
        )
    if parse_bool(
        mechanical.get("required_evidence_present"), f"required evidence {rid}"
    ) != (actual_cited == n):
        raise ValueError(f"Evidence present status mismatch for {rid}")
    prefix = f"bench-{rid}"
    derived = judge_dir / (prefix + ".judgment.json")
    prompt_path = judge_dir / (prefix + ".prompt.txt")
    judge_raw_path = judge_dir / (prefix + ".openclaw.json")
    if not all(p.is_file() for p in (derived, prompt_path, judge_raw_path)):
        raise ValueError(
            f"Missing saved GPT-6 judgment artifacts for {rid}; run judge_semantic.py first"
        )
    verdict_record = json.loads(derived.read_text(encoding="utf-8"))
    session = verdict_record.get("session_id")
    if not isinstance(session, str) or session in seen_sessions:
        raise ValueError(f"Missing/duplicate judge session for {rid}: {session!r}")
    seen_sessions.add(session)
    expected_prompt = judge.make_prompt(
        answer_key[incident]["rootCauseDescription"], ans
    )
    if prompt_path.read_text(encoding="utf-8") != expected_prompt:
        raise ValueError(f"Rubric/source prompt mismatch for {rid}")
    if verdict_record.get("prompt_sha256") != judge.digest(expected_prompt):
        raise ValueError(f"Judge prompt hash mismatch for {rid}")
    if verdict_record.get("source_sha256") != sha_bytes(raw_bytes):
        raise ValueError(f"Original benchmark source hash mismatch for {rid}")
    if (
        verdict_record.get("run_id") != prefix
        or verdict_record.get("incident") != incident
    ):
        raise ValueError(f"Judgment run identity mismatch for {rid}")
    if (
        verdict_record.get("rubric_version") != judge.RUBRIC_VERSION
        or verdict_record.get("judge_model") != "gpt-6-sol"
        or verdict_record.get("validator_version") != judge.VALIDATOR_VERSION
    ):
        raise ValueError(f"Judgment version/model mismatch for {rid}")
    raw_judge = json.loads(judge_raw_path.read_text(encoding="utf-8"))
    verified, official_meta = judge.parse_judge_result(raw_judge, session)
    if any(
        verdict_record.get(k) != verified[k]
        for k in ("root_cause_correct", "requires_review", "rationale")
    ):
        raise ValueError(f"Saved verdict differs from saved raw judge output for {rid}")
    if type(verdict_record.get("recovered_from_raw")) is not bool:
        raise ValueError(f"Missing recovered-from-raw provenance for {rid}")
    jj = (raw_judge.get("result") or {}).get("meta") or {}
    measured_judge = official_meta.get("durationMs", jj.get("durationMs"))
    if str(verdict_record.get("judge_duration_ms")) != str(measured_judge):
        raise ValueError(f"Judge duration metadata mismatch for {rid}")
    calls = checked_int(mechanical.get("calls"), f"calls {rid}")
    summary_calls = (meta.get("toolSummary") or {}).get("calls")
    if type(summary_calls) is not int or summary_calls != calls:
        raise ValueError(
            f"Candidate tool metadata/count mismatch for {rid}: {summary_calls!r} vs {calls}"
        )
    errors = checked_int(mechanical.get("failed_results"), f"errors {rid}")
    missing_id = checked_int(
        mechanical.get("missing_incident_id_args"), f"missing ID {rid}"
    )
    results = checked_int(mechanical.get("tool_results"), f"tool results {rid}")
    if errors > results or missing_id > calls:
        raise ValueError(f"Inconsistent tool counts for {rid}")
    return {
        "run_id": rid,
        "incident": incident,
        "agent": agent_of(rid),
        "candidate_model": model,
        "source_sha256": sha_bytes(raw_bytes),
        "candidate_session_id": candidate_session,
        "candidate_code_mode": False,
        "root_cause_correct_gpt6": verified["root_cause_correct"],
        "requires_review": verified["requires_review"],
        "required_evidence_found": actual_cited,
        "required_evidence_total": n,
        "full_required_evidence": actual_cited == n,
        "tool_calls": calls,
        "tool_results": results,
        "tool_errors": errors,
        "missing_incident_id_args": missing_id,
        "candidate_duration_s": measured_s,
        "judge_duration_ms": checked_int(measured_judge, f"judge duration {rid}"),
        "judge_session_id": session,
        "rubric_version": judge.RUBRIC_VERSION,
        "validator_version": judge.VALIDATOR_VERSION,
        "recovered_from_raw": verdict_record["recovered_from_raw"],
        "judge_prompt_sha256": judge.digest(expected_prompt),
        "citation_warning": mechanical.get("citation_warning", ""),
    }


def summarize(rows):
    by = defaultdict(list)
    for row in rows:
        by[(row["incident"], row["agent"])].append(row)
    groups = []
    for (incident, agent), items in sorted(by.items()):
        groups.append(
            {
                "incident": incident,
                "agent": agent,
                "trials": len(items),
                "root_correct": sum(r["root_cause_correct_gpt6"] for r in items),
                "judge_review": sum(r["requires_review"] for r in items),
                "full_evidence": sum(r["full_required_evidence"] for r in items),
                "evidence_found": sum(r["required_evidence_found"] for r in items),
                "evidence_total": sum(r["required_evidence_total"] for r in items),
                "calls": sum(r["tool_calls"] for r in items),
                "tool_errors": sum(r["tool_errors"] for r in items),
                "missing_id": sum(r["missing_incident_id_args"] for r in items),
                "mean_duration_s": round(
                    statistics.mean(r["candidate_duration_s"] for r in items), 3
                ),
                "median_duration_s": round(
                    statistics.median(r["candidate_duration_s"] for r in items), 3
                ),
            }
        )
    return groups


def render_markdown(rows, manifest, lock):
    groups = summarize(rows)
    model_labels = manifest["experiment"]["candidate_models"]
    total = len(rows)
    trial_count = manifest["trials_per_agent_per_incident"]
    trial_noun = "trial" if trial_count == 1 else "trials"
    correct = sum(r["root_cause_correct_gpt6"] for r in rows)
    flagged = sum(r["requires_review"] for r in rows)
    full_ev = sum(r["full_required_evidence"] for r in rows)
    evidences = sum(r["required_evidence_found"] for r in rows)
    evidences_needed = sum(r["required_evidence_total"] for r in rows)
    tool_calls = sum(r["tool_calls"] for r in rows)
    errors = sum(r["tool_errors"] for r in rows)
    missing_ids = sum(r["missing_incident_id_args"] for r in rows)

    def group_label(agent):
        return (
            "Cloud (GPT-5.6 Sol)" if agent == "bench-cloud" else "Jetson (Qwen 3.5 9B)"
        )

    lines = [
        "# EdgeAgentBench: reproducible incident-diagnosis evaluation",
        "",
        f"**Experiment:** OpenClaw {manifest['experiment']['runtime'].removeprefix('OpenClaw ')}; direct incident tools for both candidate agents.",
        f"**Primary judge:** GPT-6 Sol via the isolated `bench-judge` OpenClaw agent; `{judge.RUBRIC_VERSION}`; `{judge.VALIDATOR_VERSION}`.",
        f"**Dataset:** {len(manifest['incident_groups'])} synthetic incidents, {trial_count} independent saved {trial_noun} per candidate model and incident, {total} total.",
        "",
        "## Results at a glance",
        "",
        f"- **Root-cause accuracy (GPT-6 semantic judge): {correct}/{total}.** This is an LLM-judged outcome, not independently established ground truth for every response.",
        f"- **Judge review flags:** {flagged}/{total}.",
        f"- **Required evidence-ID coverage:** {evidences}/{evidences_needed} IDs; {full_ev}/{total} answers cite all required IDs. Citation presence is not proof of correct evidence interpretation.",
        f"- **Incident tool calls:** {tool_calls} attempts; {errors} errors; {missing_ids} missing/wrong `incidentId` arguments, as reported by the existing transcript-backed deterministic evaluator.",
        "",
        "| Incident | Candidate | GPT-6 correct | Full required evidence | Tool calls | Errors | Mean answer time |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for group in groups:
        lines.append(
            f"| {group['incident']} | {group_label(group['agent'])} | {group['root_correct']}/{group['trials']} | {group['full_evidence']}/{group['trials']} | {group['calls']} | {group['tool_errors']} | {group['mean_duration_s']:.3f} s |"
        )
    lines += [
        "",
        "**Latency interpretation:** Durations above belong to the *candidate diagnosis runs*, not GPT-6 grading calls. Cloud-vs-Jetson latencies reflect differing compute, model size, inference infrastructure and network paths; do not attribute the difference solely to model reasoning ability.",
        "",
        "## Scope and causal interpretation",
        "",
        "- Evaluated incident IDs: "
        + ", ".join(sorted(manifest["incident_groups"]))
        + ". Reference diagnoses remained hidden from candidate agents and were supplied to the judge after candidate responses were recorded.",
        "- Both candidates used the same incident investigation interfaces, `get_incident_logs` and `get_incident_metrics`. Code Mode was disabled for both candidate agents. Each row is an original-prompt run, not an explicit-tool diagnostic or smoke test.",
        "- Only the **saved semantic GPT-6 verdict** determines root-cause correctness in this report. Deterministic evidence and tool checks are scored separately.",
        "- Evidence-ID matching and incident tool-call accounting remain deterministic, using the previously audited evaluation CSVs and OpenClaw transcript export. The report checks IDs against the archived answer text and confirms source hashes, but does not independently re-extract SQLite tool logs.",
        "- Candidate session IDs must be distinct, Code Mode explicitly disabled, and metadata tool-call counts consistent with the transcript-backed CSV.",
        (
            "- **Original-prompt verification:** A separate frozen-prompt/SQLite transcript audit was checked against all source hashes and report rows."
            if lock.get("candidate_audit_sha256")
            else "- **Original-prompt verification limitation:** This consolidation did not independently reread the original user-message events. Run `scripts/audit_frozen_candidates.py` and supply its private audit manifest for an additional check when the archived SQLite sessions remain available."
        ),
        "",
        "## Judge and audit procedure",
        "",
        "1. Save the original candidate OpenClaw JSON output and transcript/evaluator CSV; retain unmodified files under `results/private/`.",
        "2. Grade each archived answer using the isolated `bench-judge` (`openai/gpt-6-sol`) with the fixed `semantic-root-v1` prompt. The judge prompt omits a candidate-model label.",
        "3. Preserve the judge's entire raw OpenClaw response, prompt and schema-constrained JSON verdict. `judge-validator-v1.1` verifies OAuth Codex use, actual model and session, no fallback/rerouting, and no successful tools.",
        "4. Consolidation revalidates every judge raw response, compares original source and prompt SHA-256 values, checks mechanical tool/evidence records, and fails if any expected run is missing, duplicated or inconsistent.",
        "5. Run `python3 evaluation/consolidate_semantic.py report --manifest evaluation/REPORT_MANIFEST.json --output-dir results/private/NEW_REPORT_DIR --check-only`, then repeat without `--check-only` to render the snapshot. Choose a fresh private output directory when the report template changes; neither operation invokes a model.",
        "",
        "## Reproducibility scope",
        "",
        "- **Private offline verification:** With the original candidate JSON, transcript-backed deterministic CSVs, saved GPT-6 responses and prompts, reference key, and manifest, this generator validates hashes and reconstructs the same aggregate results without any candidate or judge model calls. This is a replay of a frozen snapshot, not a fresh benchmark run.",
        "- **Independent public reproduction:** An aggregate-only Markdown report and summary table are insufficient for third parties to independently rerun or audit the experiment. A reproducible public release would also need suitable non-sensitive synthetic fixtures, incident-tool implementations, input prompts, versioned evaluation code, execution instructions, and sanitized trace examples. Keep secrets, private session data, and unreleased answer keys out of public artifacts.",
        "",
        "## Human review and limitations",
        "",
        "- This automatic report does not establish an independent human review of all answers; record any human-reviewed subsets separately for each snapshot.",
        "- A single GPT-6 judge is not a human gold standard. The two positive/negative pilot controls establish a minimal sanity check, not broad calibration. Flagged or disputed grades should be independently examined.",
        f"- {trial_count} {trial_noun} per candidate per incident, {len(manifest['incident_groups'])} synthetic incidents, and one platform condition are insufficient for broad production-reliability claims.",
        "- A fresh future judge invocation may produce different wording or verdicts. Offline replay of this snapshot uses immutable saved judge outputs and source hashes; a new scoring run should be reported as a separate version.",
        "",
        "## Provenance",
        "",
        f"- Manifest schema: `{SCHEMA}`; judge version: `{judge.RUBRIC_VERSION}`, `{judge.VALIDATOR_VERSION}`.",
        f"- Expected candidate models: `{model_labels['bench-cloud']}`, `{model_labels['bench-qwen']}`.",
        "- **Judge context limitation:** The initial archived grading batches used different OpenClaw `bench-judge.contextInjection` settings for INC-003 versus INC-001/002. Confirm the effective judge context for any later batches; a fixed rubric alone does not establish byte-identical instructions.",
        f"- Verified candidate responses: {len(lock['sources'])}; verified judge responses: {len(lock['judges'])}.",
        "- Full relative paths, per-run source/prompt SHA-256 values, archived candidate/judge-session IDs, and input file SHA-256 checksums are recorded in private `provenance.json` and `runs.csv`.",
        "- Do not publish raw JSON transcripts, secret-bearing OpenClaw session metadata, credentials or private answer keys. Publish only a separately reviewed aggregate report.",
        "",
    ]
    return "\n".join(lines)


def report(args):
    m = load_settings(args.manifest)
    key_path = Path(m["answer_key"])
    if not key_path.is_file():
        raise ValueError(f"Missing answer key: {key_path}")
    key = json.loads(key_path.read_text(encoding="utf-8"))
    expected_key = set(m["incident_groups"])
    if not expected_key.issubset(key):
        raise ValueError("Answer key is missing an incident referenced by the manifest")
    all_rows = []
    seen = set()
    sessions = set()
    candidate_sessions = set()
    lock = {
        "schema": SCHEMA,
        "manifest_sha256": sha_file(args.manifest),
        "answer_key_sha256": sha_file(key_path),
        "judge_runner_sha256": sha_file(Path(judge.__file__)),
        "report_runner_sha256": sha_file(Path(__file__)),
        "sources": {},
        "judges": {},
        "deterministic_csv": {},
    }
    for inc in sorted(expected_key):
        setting = m["incident_groups"][inc]
        originals = Path(setting["sources_dir"])
        mechanical_path = Path(setting["deterministic_runs_csv"])
        judge_dir = Path(setting["judge_dir"])
        mechanics = mechanical_for_incident(
            mechanical_path, inc, m["trials_per_agent_per_incident"]
        )
        files = filename_map(originals, inc)
        if set(files) != set(mechanics):
            raise ValueError(
                f"Original source selection differs from recorded benchmark condition for {inc}: {sorted(set(files) ^ set(mechanics))}"
            )
        lock["deterministic_csv"][inc] = sha_file(mechanical_path)
        for rid in sorted(files):
            if rid in seen:
                raise ValueError(f"Duplicate original run across incidents: {rid}")
            seen.add(rid)
            row = validate_one(
                rid,
                inc,
                files[rid],
                mechanics[rid],
                judge_dir,
                key,
                m["experiment"]["candidate_models"][agent_of(rid)],
                sessions,
                candidate_sessions,
            )
            all_rows.append(row)
            lock["sources"][rid] = row["source_sha256"]
            prefix = f"bench-{rid}"
            lock["judges"][rid] = {
                "verdict_sha256": sha_file(judge_dir / (prefix + ".judgment.json")),
                "raw_sha256": sha_file(judge_dir / (prefix + ".openclaw.json")),
                "prompt_sha256": sha_file(judge_dir / (prefix + ".prompt.txt")),
                "session_id": row["judge_session_id"],
            }
    audit_path = m.get("candidate_audit_file")
    if audit_path:
        audit_file = Path(audit_path)
        audit_data = json.loads(audit_file.read_text(encoding="utf-8"))
        if audit_data.get("schema") != "edgeagentbench-frozen-candidate-audit-v1":
            raise ValueError("Candidate audit has an unexpected schema")
        audited_runs = audit_data.get("runs")
        if not isinstance(audited_runs, dict) or set(audited_runs) != seen:
            raise ValueError(
                "Candidate prompt audit does not cover exactly the selected runs"
            )
        for row in all_rows:
            rid = row["run_id"]
            item = audited_runs[rid]
            frozen_prompt_hash = sha_bytes(
                frozen_prompt_for(row["incident"]).encode("utf-8")
            )
            if (
                item.get("source_sha256") != row["source_sha256"]
                or item.get("candidate_session_id") != row["candidate_session_id"]
                or item.get("prompt_sha256") != frozen_prompt_hash
                or item.get("code_mode_engaged") is not False
                or item.get("tool_calls") != row["tool_calls"]
                or item.get("tool_results") != row["tool_results"]
                or item.get("tool_errors") != row["tool_errors"]
            ):
                raise ValueError(f"Candidate prompt/audit mismatch for {rid}")
            if not isinstance(item.get("transcript_sha256"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", item["transcript_sha256"]
            ):
                raise ValueError(f"Invalid transcript provenance for {rid}")
        lock["candidate_audit_sha256"] = sha_file(audit_file)
    expected_total = (
        len(expected_key) * len(AGENT_MAP) * m["trials_per_agent_per_incident"]
    )
    if len(all_rows) != expected_total:
        raise ValueError(
            f"Expected {expected_total} verified runs, got {len(all_rows)}"
        )
    all_rows.sort(key=lambda x: (x["incident"], x["agent"], x["run_id"]))
    md = render_markdown(all_rows, m, lock)
    result = args.output_dir
    print(
        f"VALIDATED {len(all_rows)} source runs, GPT-6 raw responses and deterministic records"
    )
    for g in summarize(all_rows):
        print(
            f"  {g['incident']} {g['agent']}: root={g['root_correct']}/{g['trials']} review={g['judge_review']} evidence={g['evidence_found']}/{g['evidence_total']} errors={g['tool_errors']} mean_s={g['mean_duration_s']}"
        )
    if args.check_only:
        print("CHECK ONLY: no output files written")
        return
    result.mkdir(parents=True, exist_ok=True)
    from io import StringIO

    sio = StringIO()
    writer = csv.DictWriter(sio, fieldnames=RUN_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(all_rows)
    outputs = {
        "report.md": md.encode("utf-8"),
        "runs.csv": sio.getvalue().encode("utf-8"),
        "provenance.json": (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
        "summary.json": (
            json.dumps(
                {
                    "schema": SCHEMA,
                    "groups": summarize(all_rows),
                    "trials": len(all_rows),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
    }
    for name, content in outputs.items():
        p = result / name
        if p.exists() and p.read_bytes() != content:
            raise ValueError(
                f"Report output differs from existing snapshot; do not overwrite: {p}. Use a new output dir for another report version"
            )
    for name, content in outputs.items():
        p = result / name
        if not p.exists():
            t = result / (name + ".tmp")
            t.write_bytes(content)
            t.replace(p)
    print(f"Report written: {result / 'report.md'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    s = sub.add_parser(
        "stage",
        help="Select/verify and copy already existing original-run JSONs; does not invoke any agent",
    )
    s.add_argument(
        "--incident", required=True, help="Incident identifier, e.g. INC-004"
    )
    s.add_argument("--mechanical-csv", type=Path, required=True)
    s.add_argument("--search-root", type=Path, required=True)
    s.add_argument("--output-dir", type=Path, required=True)
    s.add_argument("--per-agent", type=int, default=4)
    s.add_argument("--dry-run", action="store_true")
    r = sub.add_parser(
        "report",
        help="Audit manifest-selected judged runs and render Markdown without invoking a model",
    )
    r.add_argument("--manifest", type=Path, required=True)
    r.add_argument("--output-dir", type=Path, required=True)
    r.add_argument("--check-only", action="store_true")
    args = p.parse_args(argv)
    if args.mode == "stage":
        if not re.fullmatch(r"INC-\d{3}", args.incident):
            p.error("--incident must match INC-NNN")
        if args.per_agent < 1:
            p.error("--per-agent must be positive")
        stage(args)
    else:
        report(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
