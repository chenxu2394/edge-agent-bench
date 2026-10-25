"""Tests for the OpenClaw SQLite-to-JSONL compatibility exporter."""

import ctypes
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from export_transcripts import (
    ExportError,
    audit_run,
    decode_event,
    export_results,
    zstd_library,
)


def events_for(incident_id="INC-001", missing_id=False):
    arguments = {} if missing_id else {"incidentId": incident_id}
    return [
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "name": "get_incident_logs",
                        "arguments": arguments,
                    },
                    {
                        "type": "toolCall",
                        "name": "get_incident_metrics",
                        "arguments": {"incidentId": incident_id},
                    },
                ],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolName": "get_incident_logs",
                "isError": False,
            },
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolName": "get_incident_metrics",
                "isError": False,
            },
        },
    ]


def run_json(session_id="test-session"):
    return {
        "status": "ok",
        "result": {
            "meta": {
                "agentMeta": {
                    "sessionId": session_id,
                    "sessionFile": "agent:bench-cloud:unresolvable-key",
                    "codeModeEngaged": False,
                },
                "toolSummary": {"calls": 2},
            },
            "payloads": [{"text": "Disk exhaustion: L-002 M-001"}],
        },
    }


def compress_zstd(payload):
    lib = zstd_library()
    lib.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
    lib.ZSTD_compressBound.restype = ctypes.c_size_t
    lib.ZSTD_compress.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
    ]
    lib.ZSTD_compress.restype = ctypes.c_size_t
    output = ctypes.create_string_buffer(lib.ZSTD_compressBound(len(payload)))
    source = ctypes.create_string_buffer(payload, len(payload))
    size = lib.ZSTD_compress(output, len(output), source, len(payload), 1)
    if lib.ZSTD_isError(size):
        raise RuntimeError("Fixture compression failed")
    return output.raw[:size]


class ExporterTests(unittest.TestCase):
    def test_plain_json_event(self):
        event = events_for()[0]
        self.assertEqual(decode_event(json.dumps(event), None, None), event)

    def test_zstd_json_event(self):
        event = events_for()[0]
        payload = json.dumps(event).encode("utf-8")
        self.assertEqual(
            decode_event(None, compress_zstd(payload), len(payload)), event
        )

    def test_reject_incomplete_payload(self):
        with self.assertRaises(ExportError):
            decode_event(None, None, None)

    def test_reject_oversized_payload(self):
        with self.assertRaisesRegex(ExportError, "outside safe range"):
            decode_event(None, b"irrelevant", 100_000_000)

    def test_audit_valid_calls(self):
        self.assertEqual(audit_run(run_json(), events_for(), "INC-001"), 2)

    def test_audit_missing_incident_id(self):
        with self.assertRaisesRegex(ExportError, "incidentId"):
            audit_run(run_json(), events_for(missing_id=True), "INC-001")

    def test_audit_wrong_incident_id(self):
        with self.assertRaisesRegex(ExportError, "incidentId"):
            audit_run(run_json(), events_for("INC-002"), "INC-001")

    def test_audit_extra_tool(self):
        events = events_for()
        events[0]["message"]["content"].append(
            {"type": "toolCall", "name": "exec", "arguments": {}}
        )
        with self.assertRaisesRegex(ExportError, "Unexpected tool calls"):
            audit_run(run_json(), events, "INC-001")

    def test_audit_failed_tool(self):
        events = events_for()
        events[1]["message"]["isError"] = True
        with self.assertRaisesRegex(ExportError, "failed tool result"):
            audit_run(run_json(), events, "INC-001")

    def test_audit_code_mode(self):
        data = run_json()
        data["result"]["meta"]["agentMeta"]["codeModeEngaged"] = True
        with self.assertRaisesRegex(ExportError, "Code Mode"):
            audit_run(data, events_for(), "INC-001")

    def test_export_read_only_sqlite_to_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            openclaw_home = base / "openclaw-home"
            db = openclaw_home / "agents/bench-cloud/agent/openclaw-agent.sqlite"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            try:
                conn.execute("""CREATE TABLE transcript_events (
                    session_id TEXT, seq INTEGER, event_json TEXT,
                    event_zstd BLOB, event_utf8_bytes INTEGER
                )""")
                for seq, event in enumerate(events_for()):
                    payload = json.dumps(event).encode("utf-8")
                    if seq == 0:
                        conn.execute(
                            "INSERT INTO transcript_events VALUES (?, ?, NULL, ?, ?)",
                            ("test-session", seq, compress_zstd(payload), len(payload)),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO transcript_events VALUES (?, ?, ?, NULL, NULL)",
                            ("test-session", seq, payload.decode("utf-8")),
                        )
                conn.commit()
            finally:
                conn.close()

            source = base / "source"
            source.mkdir()
            filename = "bench-cloud-inc001-001.json"
            original = run_json()
            (source / filename).write_text(json.dumps(original), encoding="utf-8")
            stage = export_results(
                source, base / "results/private", openclaw_home, expect_runs=1
            )
            exported = json.loads((stage / "cloud-inc001-001.json").read_text())
            transcript_path = Path(
                exported["result"]["meta"]["agentMeta"]["sessionFile"]
            )
            self.assertTrue(transcript_path.exists())
            self.assertEqual(len(transcript_path.read_text().splitlines()), 3)
            self.assertEqual(
                original,
                json.loads((source / filename).read_text()),
                "The source result must not be modified",
            )
            self.assertTrue(db.is_file(), "The database must not be removed")

    def test_missing_session_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            openclaw_home = base / "openclaw-home"
            db = openclaw_home / "agents/bench-cloud/agent/openclaw-agent.sqlite"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            try:
                conn.execute("""CREATE TABLE transcript_events (
                    session_id TEXT, seq INTEGER, event_json TEXT,
                    event_zstd BLOB, event_utf8_bytes INTEGER
                )""")
                conn.commit()
            finally:
                conn.close()
            source = base / "source"
            source.mkdir()
            (source / "bench-cloud-inc001-001.json").write_text(json.dumps(run_json()))
            with self.assertRaisesRegex(ExportError, "No transcript events"):
                export_results(source, base / "results/private", openclaw_home)


if __name__ == "__main__":
    unittest.main()
