#!/usr/bin/env python3
"""Optional read-only audit of original candidate user prompts and SQLite events.

This complements, but does not replace, the archived semantic report.
It reads archived OpenClaw sessions and writes a private audit manifest only
when every selected candidate run matches the original prompt and tool protocol.
It never invokes an agent or writes to OpenClaw's databases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

EVALUATION = Path(__file__).resolve().parent.parent / "evaluation"
sys.path.insert(0, str(EVALUATION))

from consolidate_semantic import (  # noqa: E402
    agent_of,
    canon,
    filename_map,
    load_frozen_prompts,
    mechanical_for_incident,
)
from export_transcripts import (  # noqa: E402
    audit_run,
    incident_calls_and_results,
    read_events,
)

SCHEMA = "edgeagentbench-frozen-candidate-audit-v1"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def text_messages(events: list[dict]) -> list[str]:
    """Extract user messages without interpreting tool or assistant content."""
    messages = []
    for event in events:
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            messages.append(content)
        elif isinstance(content, list):
            parts = [
                piece["text"]
                for piece in content
                if isinstance(piece, dict)
                and piece.get("type") == "text"
                and isinstance(piece.get("text"), str)
            ]
            if parts:
                messages.append("".join(parts))
    return messages


def audit(manifest: Path, openclaw_home: Path, original_prompts: Path) -> dict:
    config = json.loads(manifest.read_text(encoding="utf-8"))
    if config.get("experiment", {}).get("runtime") != "OpenClaw 2026.10.5":
        raise ValueError("Unexpected benchmark runtime")
    per_agent = config.get("trials_per_agent_per_incident")
    if type(per_agent) is not int or per_agent < 1:
        raise ValueError("Invalid trials_per_agent_per_incident")
    prompts = load_frozen_prompts(original_prompts)
    groups = config.get("incident_groups")
    if not isinstance(groups, dict) or not groups or not set(groups).issubset(prompts):
        raise ValueError("Manifest references missing frozen incident prompts")

    records = {}
    seen_sessions = set()
    for incident in sorted(groups):
        info = groups[incident]
        sources = filename_map(Path(info["sources_dir"]), incident)
        mechanical = mechanical_for_incident(
            Path(info["deterministic_runs_csv"]), incident, per_agent
        )
        if set(sources) != set(mechanical):
            raise ValueError(
                f"Original and mechanical selections differ for {incident}"
            )
        for rid, path in sorted(sources.items()):
            source_bytes = path.read_bytes()
            data = json.loads(source_bytes)
            meta = (data.get("result") or {}).get("meta") or {}
            agent_meta = meta.get("agentMeta") or {}
            session = agent_meta.get("sessionId")
            if (
                not isinstance(session, str)
                or not session.strip()
                or session in seen_sessions
            ):
                raise ValueError(f"Missing or duplicate candidate session for {rid}")
            seen_sessions.add(session)
            if agent_meta.get("codeModeEngaged") is not False:
                raise ValueError(f"Candidate Code Mode was not disabled for {rid}")
            agent_id = agent_of(rid)
            events = read_events(openclaw_home, agent_id, session)
            users = text_messages(events)
            if not users or users[-1] != prompts[incident]:
                raise ValueError(f"Original prompt mismatch or unavailable for {rid}")
            calls = audit_run(data, events, incident)
            tool_calls, results = incident_calls_and_results(events)
            row = mechanical[rid]
            if (
                row.get("status") != "ok"
                or int(row["calls"]) != calls
                or int(row["tool_results"]) != len(results)
                or int(row["failed_results"]) != 0
                or int(row["missing_incident_id_args"]) != 0
            ):
                raise ValueError(f"Transcript and mechanical CSV mismatch for {rid}")
            records[rid] = {
                "source_sha256": sha_bytes(source_bytes),
                "candidate_session_id": session,
                "prompt_sha256": sha_bytes(prompts[incident].encode("utf-8")),
                "code_mode_engaged": False,
                "tool_calls": len(tool_calls),
                "tool_results": len(results),
                "tool_errors": 0,
                "transcript_sha256": sha_bytes(
                    json.dumps(
                        events,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ),
            }
    expected_count = len(groups) * 2 * per_agent
    if len(records) != expected_count:
        raise ValueError(
            f"Expected {expected_count} manifest-selected candidate runs, found {len(records)}"
        )
    return {"schema": SCHEMA, "runs": records}


def write_new_or_identical(path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() != content:
        raise ValueError(f"Refusing to replace a different saved artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--openclaw-home", type=Path, default=Path.home() / ".openclaw")
    parser.add_argument(
        "--original-prompts", type=Path, default=Path("docs/original-prompts.md")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--updated-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() == args.updated_manifest.resolve():
        parser.error("Audit and updated manifest must have distinct destinations")
    result = audit(args.manifest, args.openclaw_home, args.original_prompts)
    content = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode("utf-8")
    amended = json.loads(args.manifest.read_text(encoding="utf-8"))
    amended["candidate_audit_file"] = str(args.output)
    manifest_bytes = (json.dumps(amended, indent=2) + "\n").encode("utf-8")
    # Validate both destinations before writing either one.
    for path, data in ((args.output, content), (args.updated_manifest, manifest_bytes)):
        if path.exists() and path.read_bytes() != data:
            raise ValueError(f"Refusing to replace a different saved artifact: {path}")
    write_new_or_identical(args.output, content)
    write_new_or_identical(args.updated_manifest, manifest_bytes)
    print(
        f"Verified {len(result['runs'])} candidate sessions and frozen prompts: {args.output}"
    )
    print(f"Audited report manifest: {args.updated_manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
