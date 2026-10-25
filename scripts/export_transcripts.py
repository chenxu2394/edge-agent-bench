#!/usr/bin/env python3
"""Audit and stage OpenClaw 2026.10.5 SQLite benchmark transcripts.

The script reads OpenClaw SQLite databases in read-only mode, audits all incident
calls and results, and writes staged JSON/JSONL *copies* under results/private/.
It never contacts models, changes agent configuration, or edits saved runs.

Run from the repository root:

    python3 scripts/export_transcripts.py \
        --results results/private/<run-batch>

Use --expect-runs N to require an exact matched run count.

The last output line is STAGED_RESULTS_DIR=<absolute_path>. Pass that directory
to evaluation/evaluate.py --results. Never commit staged files or raw traces.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import re
import sqlite3
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

RUN_NAME = re.compile(r"^bench-(cloud|qwen)-inc(\d{3})-(\d{3})\.json$")
INCIDENT_TOOLS = frozenset({"get_incident_logs", "get_incident_metrics"})
MAX_EVENT_BYTES = 64 * 1024 * 1024


class ExportError(RuntimeError):
    """Cannot safely reconstruct or validate a benchmark transcript."""


@lru_cache(maxsize=1)
def zstd_library():
    path = ctypes.util.find_library("zstd") or "libzstd.so.1"
    try:
        lib = ctypes.CDLL(path)
    except OSError as exc:
        raise ExportError(
            "libzstd is required to decode compressed OpenClaw events"
        ) from exc
    lib.ZSTD_decompress.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    lib.ZSTD_decompress.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
    lib.ZSTD_getErrorName.restype = ctypes.c_char_p
    return lib


def decode_event(event_json, event_zstd, event_utf8_bytes):
    """Decode one SQLite event, including the optional Zstandard storage form."""
    if event_json is not None:
        payload = event_json
    else:
        if event_zstd is None or event_utf8_bytes is None:
            raise ExportError(
                "Transcript event has neither plain JSON nor complete Zstd data"
            )
        try:
            expected_bytes = int(event_utf8_bytes)
        except (TypeError, ValueError) as exc:
            raise ExportError("Invalid uncompressed event size") from exc
        if not 0 < expected_bytes <= MAX_EVENT_BYTES:
            raise ExportError(
                f"Uncompressed event size outside safe range: {expected_bytes}"
            )
        compressed = bytes(event_zstd)
        if not compressed:
            raise ExportError("Compressed event has an empty payload")
        lib = zstd_library()
        dest = ctypes.create_string_buffer(expected_bytes)
        src = ctypes.create_string_buffer(compressed, len(compressed))
        actual_bytes = lib.ZSTD_decompress(dest, expected_bytes, src, len(compressed))
        if lib.ZSTD_isError(actual_bytes):
            error_name = lib.ZSTD_getErrorName(actual_bytes).decode("utf-8", "replace")
            raise ExportError(f"Zstd decompression failed: {error_name}")
        if actual_bytes != expected_bytes:
            raise ExportError(
                f"Zstd event size mismatch: expected {expected_bytes}, got {actual_bytes}"
            )
        payload = dest.raw[:actual_bytes]
    try:
        event = json.loads(payload)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ExportError("Transcript event is not valid JSON") from exc
    if not isinstance(event, dict):
        raise ExportError("Transcript event is not a JSON object")
    return event


def read_events(openclaw_home: Path, agent_id: str, session_id: str):
    """Read a session's events without opening the database for writing."""
    db = openclaw_home / "agents" / agent_id / "agent" / "openclaw-agent.sqlite"
    if not db.is_file():
        raise ExportError(f"OpenClaw session database not found: {db}")
    try:
        connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """
                SELECT event_json, event_zstd, event_utf8_bytes
                  FROM transcript_events
                 WHERE session_id = ?
                 ORDER BY seq
                """,
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ExportError(
            f"Unable to read OpenClaw transcript database {db}: {exc}"
        ) from exc
    if not rows:
        raise ExportError(f"No transcript events for {agent_id} session {session_id}")
    return [decode_event(*row) for row in rows]


def incident_calls_and_results(events):
    """Find *all* tool activity; unrelated calls must not be silently excluded."""
    calls, results = [], []
    for event in events:
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, list):
                calls.extend(
                    part
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "toolCall"
                )
        elif message.get("role") == "toolResult":
            results.append(message)
    return calls, results


def audit_run(data, events, incident_id):
    """Fail closed on unknown tools, missing IDs, errors, or unexpected call counts."""
    if data.get("status") != "ok":
        raise ExportError(f"Run status is {data.get('status')!r}, not 'ok'")
    try:
        meta = data["result"]["meta"]
        agent_meta = meta["agentMeta"]
    except (TypeError, KeyError) as exc:
        raise ExportError("Run lacks result.meta.agentMeta") from exc
    if agent_meta.get("codeModeEngaged") is not False:
        raise ExportError("Code Mode was not explicitly disabled in this run")

    calls, results = incident_calls_and_results(events)
    required = Counter({name: 1 for name in INCIDENT_TOOLS})
    if Counter(call.get("name") for call in calls) != required:
        raise ExportError(
            f"Unexpected tool calls: {[call.get('name') for call in calls]}"
        )
    if Counter(result.get("toolName") for result in results) != required:
        raise ExportError(
            f"Unexpected tool results: {[result.get('toolName') for result in results]}"
        )
    for call in calls:
        arguments = call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError) as exc:
                raise ExportError(
                    f"Invalid JSON arguments for {call.get('name')}"
                ) from exc
        if (
            not isinstance(arguments, dict)
            or arguments.get("incidentId") != incident_id
        ):
            raise ExportError(f"{call.get('name')} has missing/incorrect incidentId")
    if any(result.get("isError") is True for result in results):
        raise ExportError("Transcript contains a failed tool result")

    reported = (meta.get("toolSummary") or {}).get("calls")
    if reported != len(calls):
        raise ExportError(
            f"Tool count disagrees with metadata: {len(calls)} vs {reported}"
        )
    return len(calls)


def export_results(
    results_dir: Path,
    stage_root: Path,
    openclaw_home: Path,
    expect_runs: int | None = None,
):
    """Create a new private stage directory of evaluator-compatible copies."""
    if not results_dir.is_dir():
        raise ExportError(f"Results directory not found: {results_dir}")
    run_files = sorted(
        file
        for file in results_dir.iterdir()
        if file.is_file() and RUN_NAME.fullmatch(file.name)
    )
    if not run_files:
        raise ExportError(f"No bench-<model>-incNNN-NNN.json files in {results_dir}")
    if expect_runs is not None and len(run_files) != expect_runs:
        raise ExportError(f"Expected {expect_runs} runs, found {len(run_files)}")

    stage_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="eval-stage-", dir=stage_root)).resolve()
    checked_calls = 0
    for run_file in run_files:
        try:
            match = RUN_NAME.fullmatch(run_file.name)
            assert match is not None
            model, num, _trial = match.groups()
            agent_id = f"bench-{model}"
            incident_id = f"INC-{num}"
            data = json.loads(run_file.read_text(encoding="utf-8"))
            meta = data["result"]["meta"]["agentMeta"]
            session_id = meta["sessionId"]
            if not isinstance(session_id, str) or not session_id:
                raise ExportError("Missing sessionId")
            events = read_events(openclaw_home, agent_id, session_id)
            call_count = audit_run(data, events, incident_id)

            transcript_path = (stage / f"{run_file.stem}.jsonl").resolve()
            transcript_path.write_text(
                "".join(
                    json.dumps(event, ensure_ascii=False) + "\n" for event in events
                ),
                encoding="utf-8",
            )
            # The scorer expects a filesystem path. Only the staged copy is changed.
            meta["sessionFile"] = str(transcript_path)
            staged_name = run_file.name.removeprefix("bench-")
            (stage / staged_name).write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            checked_calls += call_count
            print(f"{run_file.stem}: PASS ({call_count} calls)")
        except (OSError, ValueError, KeyError, TypeError, ExportError) as exc:
            raise ExportError(
                f"{run_file.name}: {exc} (partial stage: {stage})"
            ) from exc
    print(f"Verified runs: {len(run_files)}; incident tool calls: {checked_calls}")
    print("Missing/incorrect incidentId: 0; tool errors: 0")
    print(f"STAGED_RESULTS_DIR={stage}")
    return stage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        type=Path,
        required=True,
        help="Directory containing bench-cloud/qwen run JSON files",
    )
    parser.add_argument(
        "--stage-root",
        type=Path,
        default=Path("results/private"),
        help="Root for fresh private staging directories",
    )
    parser.add_argument(
        "--openclaw-home",
        type=Path,
        default=Path.home() / ".openclaw",
        help="OpenClaw state directory (default: ~/.openclaw)",
    )
    parser.add_argument(
        "--expect-runs",
        type=int,
        default=None,
        help="Fail if the matched benchmark run count differs",
    )
    args = parser.parse_args()
    try:
        export_results(
            args.results, args.stage_root, args.openclaw_home, args.expect_runs
        )
    except ExportError as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
