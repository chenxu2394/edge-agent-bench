#!/usr/bin/env python3
"""Create sanitized, deterministic public CSV/Markdown from a private frozen report.

Only allowlisted fields are published. Private source paths, agent/judge session
IDs, transcript receipts, judge responses, and hashes remain local. This tool
never calls a model or changes the original consolidated snapshot.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from io import StringIO
from pathlib import Path

PUBLIC_RUN_FIELDS = (
    "run_id",
    "incident",
    "agent",
    "candidate_model",
    "root_cause_correct_gpt6",
    "requires_review",
    "full_required_evidence",
    "required_evidence_found",
    "required_evidence_total",
    "tool_calls",
    "tool_results",
    "tool_errors",
    "missing_incident_id_args",
    "candidate_duration_s",
)
PUBLIC_SUMMARY_FIELDS = (
    "incident",
    "agent",
    "trials",
    "gpt6_root_correct",
    "full_required_evidence",
    "tool_calls",
    "tool_errors",
    "missing_incident_id_args",
    "mean_duration_s",
)
SCHEMA = "edgeagentbench-consolidated-gpt6-v1"
RUN_RE = re.compile(r"^(cloud|qwen)-inc(\d{3})-(\d{3})$")
AGENTS = {"bench-cloud", "bench-qwen"}


def parse_bool(value, field):
    if value in ("True", "true", "1", True):
        return True
    if value in ("False", "false", "0", False):
        return False
    raise ValueError(f"Invalid boolean {field}: {value!r}")


def csv_bytes(rows, fields):
    buf = StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=fields, lineterminator="\n", extrasaction="raise"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def build_public(input_dir: Path) -> dict[str, bytes]:
    with (input_dir / "runs.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not (set(PUBLIC_RUN_FIELDS) | {"source_sha256"}).issubset(
            reader.fieldnames or []
        ):
            raise ValueError("Consolidated runs.csv is missing required columns")
        original = list(reader)
    summary = json.loads((input_dir / "summary.json").read_text(encoding="utf-8"))
    provenance = json.loads((input_dir / "provenance.json").read_text(encoding="utf-8"))
    if summary.get("schema") != SCHEMA or provenance.get("schema") != SCHEMA:
        raise ValueError("Unsupported consolidated report schema")
    if type(summary.get("trials")) is not int or summary["trials"] < 1:
        raise ValueError("Invalid total trial count in private summary")
    declared = summary.get("groups")
    if not isinstance(declared, list) or not declared:
        raise ValueError("Private summary must declare nonempty incident/agent groups")
    expected_groups = {}
    for group in declared:
        if not isinstance(group, dict):
            raise ValueError("Invalid private summary group")
        incident, agent, count = (
            group.get("incident"),
            group.get("agent"),
            group.get("trials"),
        )
        if (
            not isinstance(incident, str)
            or not re.fullmatch(r"INC-\d{3}", incident)
            or agent not in AGENTS
            or type(count) is not int
            or not 1 <= count <= 999
        ):
            raise ValueError(f"Invalid incident/agent/trial group: {group!r}")
        key = (incident, agent)
        if key in expected_groups:
            raise ValueError(f"Duplicate private summary group: {key}")
        expected_groups[key] = group
    incidents = {incident for incident, _ in expected_groups}
    for incident in incidents:
        if {agent for inc, agent in expected_groups if inc == incident} != AGENTS:
            raise ValueError(f"Missing candidate group for {incident}")
    trial_counts = {group["trials"] for group in expected_groups.values()}
    if len(trial_counts) != 1:
        raise ValueError("Candidate/incident groups must use a consistent trial count")
    trials_per_group = trial_counts.pop()
    expected_ids = {
        f"{agent.removeprefix('bench-')}-inc{incident[-3:]}-{trial:03d}"
        for incident, agent in expected_groups
        for trial in range(1, trials_per_group + 1)
    }
    total = len(expected_ids)
    if summary["trials"] != total or len(original) != total:
        raise ValueError(f"Incomplete report: expected {total} run records")
    by_id = {}
    grouped = defaultdict(list)
    for entry in original:
        rid = entry["run_id"]
        match = RUN_RE.fullmatch(rid)
        if not match or rid in by_id:
            raise ValueError(f"Invalid or duplicate run ID: {rid!r}")
        agent = f"bench-{match.group(1)}"
        incident = f"INC-{match.group(2)}"
        expected_model = "gpt-5.6-sol" if agent == "bench-cloud" else "qwen3.5:9b"
        if (
            entry["incident"] != incident
            or entry["agent"] != agent
            or entry["candidate_model"]
            not in (
                expected_model,
                "openai/gpt-5.6-sol"
                if agent == "bench-cloud"
                else "llamacpp/qwen3.5:9b",
            )
        ):
            raise ValueError(f"Candidate identity does not match run ID: {rid}")
        for field in (
            "root_cause_correct_gpt6",
            "requires_review",
            "full_required_evidence",
        ):
            parse_bool(entry[field], field)
        for field in (
            "required_evidence_found",
            "required_evidence_total",
            "tool_calls",
            "tool_results",
            "tool_errors",
            "missing_incident_id_args",
        ):
            if not entry[field].isdigit():
                raise ValueError(f"Invalid numeric {field} for {rid}")
        found = int(entry["required_evidence_found"])
        required = int(entry["required_evidence_total"])
        fully_cited = parse_bool(
            entry["full_required_evidence"], "full_required_evidence"
        )
        if required == 0 or found > required or fully_cited != (found == required):
            raise ValueError(f"Inconsistent evidence coverage for {rid}")
        calls = int(entry["tool_calls"])
        results = int(entry["tool_results"])
        errors = int(entry["tool_errors"])
        missing_id = int(entry["missing_incident_id_args"])
        if results != calls or errors > results or missing_id > calls:
            raise ValueError(f"Inconsistent tool counts for {rid}")
        try:
            latency = float(entry["candidate_duration_s"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid latency for {rid}") from exc
        if not 0 <= latency < float("inf"):
            raise ValueError(f"Invalid latency for {rid}")
        if entry["source_sha256"] != provenance.get("sources", {}).get(rid):
            raise ValueError(f"Source hash provenance mismatch for {rid}")
        if rid not in provenance.get("judges", {}):
            raise ValueError(f"Missing judge provenance for {rid}")
        by_id[rid] = entry
        grouped[(incident, agent)].append(entry)
    if set(by_id) != expected_ids or set(grouped) != set(expected_groups):
        raise ValueError("Run IDs do not match the declared incident/agent groups")
    if (
        set(provenance.get("sources", {})) != expected_ids
        or set(provenance.get("judges", {})) != expected_ids
    ):
        raise ValueError("Extra or missing source/judge provenance records")

    public_runs = [
        {key: by_id[rid][key] for key in PUBLIC_RUN_FIELDS} for rid in sorted(by_id)
    ]
    public_groups = []
    for (incident, agent), records in sorted(grouped.items()):
        result = {
            "incident": incident,
            "agent": agent,
            "trials": len(records),
            "gpt6_root_correct": sum(
                parse_bool(r["root_cause_correct_gpt6"], "root") for r in records
            ),
            "full_required_evidence": sum(
                parse_bool(r["full_required_evidence"], "evidence") for r in records
            ),
            "tool_calls": sum(int(r["tool_calls"]) for r in records),
            "tool_errors": sum(int(r["tool_errors"]) for r in records),
            "missing_incident_id_args": sum(
                int(r["missing_incident_id_args"]) for r in records
            ),
            "mean_duration_s": round(
                statistics.mean(float(r["candidate_duration_s"]) for r in records), 3
            ),
        }
        original_group = expected_groups[(incident, agent)]
        if result["trials"] != original_group["trials"]:
            raise ValueError(f"Incomplete group: {(incident, agent)}")
        if not original_group or any(
            original_group.get(src) != result[dst]
            for src, dst in (
                ("trials", "trials"),
                ("root_correct", "gpt6_root_correct"),
                ("full_evidence", "full_required_evidence"),
                ("calls", "tool_calls"),
                ("tool_errors", "tool_errors"),
                ("missing_id", "missing_incident_id_args"),
                ("mean_duration_s", "mean_duration_s"),
            )
        ):
            raise ValueError(
                f"Private summary and run records disagree for {(incident, agent)}"
            )
        public_groups.append(result)
    correct = sum(row["gpt6_root_correct"] for row in public_groups)
    full = sum(row["full_required_evidence"] for row in public_groups)
    calls = sum(row["tool_calls"] for row in public_groups)
    errors = sum(row["tool_errors"] for row in public_groups)
    missing = sum(row["missing_incident_id_args"] for row in public_groups)
    lines = [
        "# EdgeAgentBench: GPT-6 semantic evaluation",
        "",
        "**Experiment:** OpenClaw 2026.10.5, two direct read-only incident tools, Code Mode disabled.",
        "**Candidates:** Cloud GPT-5.6 Sol and Jetson Qwen 3.5 9B. **Judge:** GPT-6 Sol (`semantic-root-v1`).",
        f"**Dataset:** {len(incidents)} synthetic incidents, {trials_per_group} independent trials per candidate and incident, {total} saved runs.",
        "",
        f"- GPT-6-judged root-cause matches: **{correct}/{total}** (LLM-judged, not independent human ground truth).",
        f"- Full required evidence-ID coverage: **{full}/{total}**.",
        f"- Tool calls: **{calls}**; tool errors: **{errors}**; missing/wrong `incidentId`: **{missing}**.",
        "",
        "| Incident | Candidate | GPT-6 root-cause matches | Full evidence | Tool calls | Mean diagnosis time |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in public_groups:
        model = (
            "Cloud (GPT-5.6 Sol)"
            if row["agent"] == "bench-cloud"
            else "Jetson (Qwen 3.5 9B)"
        )
        lines.append(
            f"| {row['incident']} | {model} | {row['gpt6_root_correct']}/{row['trials']} | "
            f"{row['full_required_evidence']}/{row['trials']} | {row['tool_calls']} | "
            f"{row['mean_duration_s']:.3f} s |"
        )
    lines.extend(
        [
            "",
            "## Interpretation and limits",
            "",
            "The evaluated synthetic incident IDs are "
            + ", ".join(sorted(incidents))
            + ". All scores refer to archived original-prompt candidate runs; GPT-6 grading time is excluded from candidate latency.",
            "",
            "The primary root-cause metric uses saved GPT-6 semantic judgments. Required evidence IDs and incident tool calls are scored separately by deterministic checks. Citation presence does not prove a citation is interpreted correctly.",
            "",
            f"Each candidate was evaluated in {trials_per_group} independent sessions per incident. Different model sizes and compute infrastructure prevent hardware-normalized speed conclusions. This controlled synthetic benchmark does not establish production incident reliability.",
            "",
            "Effective judge-context equivalence must be verified for each evaluation snapshot. The initial archived experiment disclosed a `contextInjection` variation between grading batches; see `docs/results.md`.",
            "",
            "See `docs/reproducibility.md` for the private provenance and validation workflow. Raw traces, credentials, session IDs, full judge receipts, and private file paths are intentionally excluded from these public artifacts.",
            "",
        ]
    )
    return {
        "runs.csv": csv_bytes(public_runs, PUBLIC_RUN_FIELDS),
        "summary.csv": csv_bytes(public_groups, PUBLIC_SUMMARY_FIELDS),
        "README.md": "\n".join(lines).encode("utf-8"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    outputs = build_public(args.input_dir)
    if args.output_dir.resolve().is_relative_to(args.input_dir.resolve()):
        parser.error("Output directory must not be inside the private snapshot")
    for name, data in outputs.items():
        dest = args.output_dir / name
        if dest.exists() and dest.read_bytes() != data:
            raise ValueError(
                f"Refusing to overwrite different existing public artifact: {dest}"
            )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        dest = args.output_dir / name
        if not dest.exists():
            dest.write_bytes(data)
    print(f"Prepared {len(outputs)} sanitized public files in {args.output_dir}")
    print("Review before staging or pushing to Git.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
