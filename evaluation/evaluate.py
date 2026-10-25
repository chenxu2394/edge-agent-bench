#!/usr/bin/env python3
"""Score saved original-prompt EdgeAgentBench runs using answer-key.json.

Python stdlib only.

Expected file names:
  results/private/{qwen,cloud}-inc001-001.json
  ~/.openclaw/agents/{bench-qwen,bench-cloud}/sessions/{sessionId}.jsonl

Diagnostic prompts (serial/parallel) are excluded intentionally.
"""

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

RUN_RE = re.compile(r"^(qwen|cloud)-inc(\d{3})-(\d{3})\.json$")
AGENT_BY_MODEL = {"qwen": "bench-qwen", "cloud": "bench-cloud"}
TOOL_NAMES = {"get_incident_logs", "get_incident_metrics"}

# Default auxiliary lexical check for DISK_EXHAUSTION. Other root-cause
# entries may supply diagnosisPatterns in answer-key.json.
DEFAULT_CAUSE_PATTERNS = {
    "DISK_EXHAUSTION": [
        r"\bdisk(?:\s+space)?\s+exhaustion\b",
        r"\bdisk\b.{0,110}\b(?:exhausted|nearly\s+exhausted|full|out\s+of\s+space)\b",
        r"\bfilesystem\b.{0,110}\b(?:out\s+of\s+(?:usable\s+)?(?:disk\s+)?space|full)\b",
        # Recognize equivalent disk exhaustion diagnoses.
        r"\b(?:ran|run|running)\s+out\s+of\s+(?:writable\s+)?disk\s+space\b",
        r"\bfilesystem\b.{0,110}\b(?:ran|run|running)\s+out\s+of\s+(?:writable\s+)?space\b",
    ],
}


def load_rubrics(path):
    """Load reference diagnoses, required evidence, and optional lexical checks.

    Supported answer-key format:
      {"INC-001": {"rootCauseCode": "DISK_EXHAUSTION",
                   "requiredEvidenceIds": ["L-002", "M-001"],
                   "diagnosisPatterns": ["regex optional for default codes"]}}

    New cause codes must define diagnosisPatterns (case-insensitive regular
    expressions). These are compared to the diagnosis section of the answer,
    not to later quotes from the supporting evidence section.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"Answer key must be a nonempty object: {path}")
    rubrics = {}
    for incident, entry in raw.items():
        if not re.fullmatch(r"INC-\d{3}", incident):
            raise ValueError(f"Invalid incident ID in answer key: {incident!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"Answer key entry for {incident} must be an object")
        cause = entry.get("rootCauseCode")
        evidence = entry.get("requiredEvidenceIds")
        if not isinstance(cause, str) or not cause.strip():
            raise ValueError(f"Missing rootCauseCode for {incident}")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(
                not isinstance(i, str) or not re.fullmatch(r"[LM]-\d{3}", i)
                for i in evidence
            )
            or len(set(evidence)) != len(evidence)
        ):
            raise ValueError(f"Invalid requiredEvidenceIds for {incident}")
        patterns = entry.get("diagnosisPatterns")
        if patterns is None:
            patterns = DEFAULT_CAUSE_PATTERNS.get(cause)
        if (
            not isinstance(patterns, list)
            or not patterns
            or any(
                not isinstance(pattern, str) or not pattern.strip()
                for pattern in patterns
            )
        ):
            raise ValueError(
                f"No diagnosisPatterns for {incident} ({cause}). "
                "Add a list of diagnosis regexes to evaluation/answer-key.json."
            )
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"Invalid diagnosisPattern for {incident}: {exc}"
                ) from exc
        rubrics[incident] = {
            "expected_cause": cause,
            "required_evidence": tuple(evidence),
            "diagnosis_patterns": tuple(patterns),
        }
    return rubrics


FIELDS = [
    "run_id",
    "incident",
    "agent",
    "status",
    "root_cause_match",
    "required_evidence_present",
    "evidence_coverage",
    "citation_warning",
    "calls",
    "tool_results",
    "successful_results",
    "failed_results",
    "schema_validation_errors",
    "other_tool_errors",
    "missing_incident_id_args",
    "recovered_after_errors",
    "parallel_tool_batches",
    "duration_s",
    "tool_summary_calls",
    "call_count_matches_metadata",
    "source_json",
]
SUMMARY_FIELDS = [
    "incident",
    "agent",
    "trials",
    "root_cause_matches",
    "full_required_evidence",
    "total_calls",
    "total_tool_errors",
    "schema_validation_errors",
    "missing_incident_id_args",
    "runs_with_citation_warnings",
    "mean_duration_s",
]


def get_final_answer(data):
    result = data.get("result") or {}
    meta = result.get("meta") or {}
    text = meta.get("finalAssistantRawText")
    if isinstance(text, str):
        return text
    # Useful fallback if a version of OpenClaw omits finalAssistantRawText.
    return "\n".join(
        p.get("text", "")
        for p in result.get("payloads", [])
        if isinstance(p, dict) and isinstance(p.get("text"), str)
    )


def transcript_path(run, meta, home):
    info = meta.get("agentMeta") or {}
    candidates = []
    if info.get("sessionFile"):
        candidates.append(Path(info["sessionFile"]).expanduser())
    if info.get("sessionId"):
        candidates.append(
            home
            / ".openclaw"
            / "agents"
            / run["agent"]
            / "sessions"
            / (str(info["sessionId"]) + ".jsonl")
        )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Transcript not found for {run['run_id']}. Checked: {candidates}. "
        "Do not interpret missing transcript data as zero tool errors."
    )


def normalize_args(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def error_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def read_trace(path, incident):
    calls = 0
    results = 0
    errors = 0
    validations = 0
    missing_id = 0
    parallel_batches = 0
    failed_tools = set()
    recovered_tools = set()

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid transcript JSON {path}:{lineno}: {exc}") from exc
        message = record.get("message") or {}
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content") or []

        if role == "assistant":
            if not isinstance(content, list):
                continue
            batch = []
            for item in content:
                if not isinstance(item, dict) or item.get("type") not in (
                    "toolCall",
                    "tool_use",
                    "function_call",
                ):
                    continue
                fn = item.get("function") or {}
                name = item.get("name") or (
                    fn.get("name") if isinstance(fn, dict) else None
                )
                args = normalize_args(item.get("arguments", item.get("input")))
                if not args and isinstance(fn, dict):
                    args = normalize_args(fn.get("arguments"))
                calls += 1
                batch.append(name)
                if name in TOOL_NAMES and args.get("incidentId") != incident:
                    missing_id += 1
            if len(batch) > 1:
                parallel_batches += 1

        elif role in ("toolResult", "tool"):
            results += 1
            name = message.get("toolName") or message.get("name") or "unknown"
            is_error = message.get("isError") is True
            if is_error:
                errors += 1
                failed_tools.add(name)
                if "Validation failed for tool" in error_text(content):
                    validations += 1
            elif name in failed_tools:
                recovered_tools.add(name)

    return {
        "calls": calls,
        "tool_results": results,
        "successful_results": results - errors,
        "failed_results": errors,
        "schema_validation_errors": validations,
        "other_tool_errors": errors - validations,
        "missing_incident_id_args": missing_id,
        "recovered_after_errors": bool(
            failed_tools and failed_tools <= recovered_tools
        ),
        "parallel_tool_batches": parallel_batches,
    }


def score_diagnosis(answer, patterns):
    """Conservative lexical diagnosis check, not semantic grading.

    Only inspect the beginning of the answer before an evidence section.
    Prevent later quotes of an error message from making a wrong diagnosis
    appear correct. The run rubric supplies the accepted regex patterns.
    """
    first_part = re.split(
        r"(?im)^\s*(?:#{1,5}\s*)?(?:\*\*)?(?:key |supporting )?evidence\b",
        answer,
        maxsplit=1,
    )[0][:600]
    return any(
        re.search(pattern, first_part, flags=re.I | re.S) for pattern in patterns
    )


def citation_warnings(answer):
    # Conservative indicator for manual review, NOT a semantic citation judge.
    warnings = []
    for line in answer.splitlines():
        if (
            re.search(r"\bM-004\b", line, re.I)
            and re.search(r"\b(?:broker|mqtt)\b", line, re.I)
            and not re.search(r"\bL-004\b", line, re.I)
            and re.search(r"\b(?:broker|mqtt)\b.{0,45}\bM-004\b", line, re.I)
        ):
            warnings.append("Possible incorrect broker attribution to M-004")
            break
    return "; ".join(warnings)


def score_run(result_path, home, rubrics=None):
    match = RUN_RE.fullmatch(result_path.name)
    if not match:
        raise ValueError(f"Not an original-prompt run filename: {result_path.name}")
    model, incident_no, _trial = match.groups()
    incident = f"INC-{incident_no}"
    if rubrics is None:
        rubrics = load_rubrics(Path(__file__).with_name("answer-key.json"))
    if incident not in rubrics:
        raise ValueError(f"No rubric for {incident}")
    data = json.loads(result_path.read_text(encoding="utf-8"))
    meta = (data.get("result") or {}).get("meta") or {}
    run = {
        "run_id": result_path.stem,
        "incident": incident,
        "agent": AGENT_BY_MODEL[model],
        "status": data.get("status", "unknown"),
    }
    answer = get_final_answer(data)
    if not answer.strip():
        raise ValueError(f"No final answer text in {result_path}")
    rubric = rubrics[incident]
    run["root_cause_match"] = score_diagnosis(answer, rubric["diagnosis_patterns"])
    required = rubric["required_evidence"]
    cited = [eid for eid in required if re.search(rf"\b{re.escape(eid)}\b", answer)]
    run["required_evidence_present"] = len(cited) == len(required)
    run["evidence_coverage"] = f"{len(cited)}/{len(required)}"
    run["citation_warning"] = citation_warnings(answer)
    run.update(read_trace(transcript_path(run, meta, home), incident))
    run["duration_s"] = round(float(meta["durationMs"]) / 1000.0, 3)
    run["tool_summary_calls"] = (meta.get("toolSummary") or {}).get("calls")
    run["call_count_matches_metadata"] = run["tool_summary_calls"] == run["calls"]
    run["source_json"] = str(result_path)
    return run


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def aggregate(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["incident"], r["agent"])].append(r)
    summaries = []
    for (incident, agent), group in sorted(groups.items()):
        summaries.append(
            {
                "incident": incident,
                "agent": agent,
                "trials": len(group),
                "root_cause_matches": sum(x["root_cause_match"] for x in group),
                "full_required_evidence": sum(
                    x["required_evidence_present"] for x in group
                ),
                "total_calls": sum(x["calls"] for x in group),
                "total_tool_errors": sum(x["failed_results"] for x in group),
                "schema_validation_errors": sum(
                    x["schema_validation_errors"] for x in group
                ),
                "missing_incident_id_args": sum(
                    x["missing_incident_id_args"] for x in group
                ),
                "runs_with_citation_warnings": sum(
                    bool(x["citation_warning"]) for x in group
                ),
                "mean_duration_s": round(
                    statistics.mean(x["duration_s"] for x in group), 3
                ),
            }
        )
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results/private"))
    parser.add_argument("--output", type=Path, default=Path("results/evaluation"))
    parser.add_argument(
        "--answer-key",
        type=Path,
        default=Path("evaluation/answer-key.json"),
        help="Ground truth, never exposed to benchmark agents",
    )
    args = parser.parse_args()
    rubrics = load_rubrics(args.answer_key)
    files = sorted(p for p in args.results.glob("*.json") if RUN_RE.fullmatch(p.name))
    if not files:
        parser.error(f"No original-prompt JSON results found in {args.results}")
    runs = [score_run(p, Path.home(), rubrics) for p in files]
    summaries = aggregate(runs)
    write_csv(args.output / "runs.csv", FIELDS, runs)
    write_csv(args.output / "summary.csv", SUMMARY_FIELDS, summaries)
    print(f"Loaded answer key: {args.answer_key} ({len(rubrics)} incidents)")
    print("Scored original-prompt runs (diagnostic runs excluded):")
    for r in runs:
        print(
            f"  {r['run_id']}: root={r['root_cause_match']}, "
            f"evidence={r['evidence_coverage']}, "
            f"calls={r['calls']}, errors={r['failed_results']}, "
            f"missing_id={r['missing_incident_id_args']}, "
            f"duration={r['duration_s']:.3f}s"
            + (f" [REVIEW: {r['citation_warning']}]" if r["citation_warning"] else "")
        )
    print("\nAggregate:")
    for s in summaries:
        print(
            f"  {s['agent']}: {s['root_cause_matches']}/{s['trials']} root matches, "
            f"{s['total_calls']} calls, {s['total_tool_errors']} errors, "
            f"mean {s['mean_duration_s']:.2f}s"
        )
    print(f"\nWrote {args.output / 'runs.csv'} and {args.output / 'summary.csv'}")


if __name__ == "__main__":
    main()
