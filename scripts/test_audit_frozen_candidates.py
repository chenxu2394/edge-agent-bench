"""Offline tests for the optional frozen-prompt/SQLite audit."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_frozen_candidates import audit, text_messages


class FrozenCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "orig"
        self.directory.mkdir()
        self.prompts = self.root / "prompts.md"
        self.prompts.write_text(
            "\n".join(
                f"## INC-{i:03d}\n\n> Investigate incident INC-{i:03d}."
                for i in range(1, 4)
            )
        )
        self.recorded = {}
        source_groups = {}
        for i in range(1, 4):
            incident = f"INC-{i:03d}"
            folder = self.directory / incident
            folder.mkdir()
            rows = []
            for model in ("cloud", "qwen"):
                for trial in range(1, 5):
                    rid = f"{model}-inc{i:03d}-{trial:03d}"
                    session = f"session-{rid}"
                    raw = {
                        "status": "ok",
                        "result": {
                            "meta": {
                                "agentMeta": {
                                    "sessionId": session,
                                    "codeModeEngaged": False,
                                },
                                "toolSummary": {"calls": 2},
                            }
                        },
                    }
                    (folder / f"bench-{rid}.json").write_text(json.dumps(raw))
                    rows.append(
                        {
                            "run_id": rid,
                            "incident": incident,
                            "agent": f"bench-{model}",
                            "status": "ok",
                            "calls": 2,
                            "tool_results": 2,
                            "failed_results": 0,
                            "missing_incident_id_args": 0,
                        }
                    )
                    self.recorded[session] = self.events(incident)
            mechanical_file = self.root / f"rows-{incident}.csv"
            with mechanical_file.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            source_groups[incident] = {
                "sources_dir": str(folder),
                "deterministic_runs_csv": str(mechanical_file),
                "judge_dir": str(self.root / "not-used"),
            }
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "experiment": {"runtime": "OpenClaw 2026.10.5"},
                    "trials_per_agent_per_incident": 4,
                    "incident_groups": source_groups,
                }
            )
        )

    @staticmethod
    def events(incident):
        return [
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Investigate incident {incident}."}
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "name": "get_incident_logs",
                            "arguments": {"incidentId": incident},
                        },
                        {
                            "type": "toolCall",
                            "name": "get_incident_metrics",
                            "arguments": {"incidentId": incident},
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

    def fake_read(self, _home, _agent, session):
        return self.recorded[session]

    def test_text_messages_ignores_assistant(self):
        events = self.events("INC-001")
        self.assertEqual(text_messages(events), ["Investigate incident INC-001."])

    def test_audit_declared_fixture_runs(self):
        with patch("audit_frozen_candidates.read_events", side_effect=self.fake_read):
            record = audit(self.manifest, self.root, self.prompts)
        config = json.loads(self.manifest.read_text())
        expected = (
            len(config["incident_groups"]) * 2 * config["trials_per_agent_per_incident"]
        )
        self.assertEqual(len(record["runs"]), expected)
        self.assertEqual(
            set(record["runs"]),
            {
                f"{model}-inc{i:03d}-{trial:03d}"
                for model in ("cloud", "qwen")
                for i in range(1, 4)
                for trial in range(1, 5)
            },
        )

    def test_added_incident_is_included_in_audit(self):
        incident = "INC-004"
        self.prompts.write_text(
            self.prompts.read_text()
            + f"\n## {incident}\n\n> Investigate incident {incident}.\n"
        )
        folder = self.directory / incident
        folder.mkdir()
        rows = []
        for model in ("cloud", "qwen"):
            for trial in range(1, 5):
                rid = f"{model}-inc004-{trial:03d}"
                session = f"session-{rid}"
                raw = {
                    "status": "ok",
                    "result": {
                        "meta": {
                            "agentMeta": {
                                "sessionId": session,
                                "codeModeEngaged": False,
                            },
                            "toolSummary": {"calls": 2},
                        }
                    },
                }
                (folder / f"bench-{rid}.json").write_text(json.dumps(raw))
                rows.append(
                    {
                        "run_id": rid,
                        "incident": incident,
                        "agent": f"bench-{model}",
                        "status": "ok",
                        "calls": 2,
                        "tool_results": 2,
                        "failed_results": 0,
                        "missing_incident_id_args": 0,
                    }
                )
                self.recorded[session] = self.events(incident)
        mechanical_file = self.root / f"rows-{incident}.csv"
        with mechanical_file.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
        config = json.loads(self.manifest.read_text())
        config["incident_groups"][incident] = {
            "sources_dir": str(folder),
            "deterministic_runs_csv": str(mechanical_file),
            "judge_dir": str(self.root / "not-used"),
        }
        self.manifest.write_text(json.dumps(config))
        with patch("audit_frozen_candidates.read_events", side_effect=self.fake_read):
            report = audit(self.manifest, self.root, self.prompts)
        self.assertEqual(len(report["runs"]), 32)
        self.assertIn("qwen-inc004-004", report["runs"])

    def test_wrong_original_prompt_rejected(self):
        self.recorded["session-cloud-inc001-001"][0]["message"]["content"][0][
            "text"
        ] = "Different prompt"
        with patch("audit_frozen_candidates.read_events", side_effect=self.fake_read):
            with self.assertRaisesRegex(ValueError, "Original prompt mismatch"):
                audit(self.manifest, self.root, self.prompts)

    def test_duplicate_session_rejected(self):
        path = self.directory / "INC-001/bench-qwen-inc001-001.json"
        raw = json.loads(path.read_text())
        raw["result"]["meta"]["agentMeta"]["sessionId"] = "session-cloud-inc001-001"
        path.write_text(json.dumps(raw))
        with patch("audit_frozen_candidates.read_events", side_effect=self.fake_read):
            with self.assertRaisesRegex(ValueError, "duplicate candidate session"):
                audit(self.manifest, self.root, self.prompts)


if __name__ == "__main__":
    unittest.main()
