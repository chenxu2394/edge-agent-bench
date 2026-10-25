import json
import tempfile
import unittest
from pathlib import Path

import evaluate
from evaluate import DEFAULT_CAUSE_PATTERNS, score_diagnosis


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.private = self.root / "results" / "private"
        self.private.mkdir(parents=True)
        self.sessions = self.root / ".openclaw" / "agents"
        self.keyfile = self.root / "evaluation" / "answer-key.json"
        self.keyfile.parent.mkdir(parents=True, exist_ok=True)
        self.keyfile.write_text(
            json.dumps(
                {
                    "INC-001": {
                        "rootCauseCode": "DISK_EXHAUSTION",
                        "rootCauseDescription": "Disk exhaustion prevented telemetry persistence",
                        "requiredEvidenceIds": ["M-001", "L-002"],
                        "additionalSupportingEvidenceIds": ["L-003"],
                    }
                }
            )
        )
        self.rubrics = evaluate.load_rubrics(self.keyfile)

    def make_run(self, model, trial, answer, records, duration_ms, reported_calls):
        agent = {"qwen": "bench-qwen", "cloud": "bench-cloud"}[model]
        sid = f"session-{model}-{trial}"
        trans = self.sessions / agent / "sessions" / (sid + ".jsonl")
        trans.parent.mkdir(parents=True, exist_ok=True)
        trans.write_text("\n".join(json.dumps(x) for x in records) + "\n")
        path = self.private / f"{model}-inc001-{trial:03d}.json"
        path.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "result": {
                        "meta": {
                            "agentMeta": {"sessionId": sid},
                            "finalAssistantRawText": answer,
                            "durationMs": duration_ms,
                            "toolSummary": {"calls": reported_calls, "failures": 0},
                        }
                    },
                }
            )
        )
        return path

    def test_qwen_success_and_wrong_evidence_warning(self):
        answer = (
            "## Diagnosis\nDisk space exhaustion caused publishing to stop.\n"
            "### Supporting Evidence\nL-002 and M-001.\n"
            "The broker connection (M-004 evidence) was healthy."
        )
        records = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "name": "get_incident_logs",
                            "arguments": {"incidentId": "INC-001"},
                        },
                        {
                            "type": "toolCall",
                            "name": "get_incident_metrics",
                            "arguments": {"incidentId": "INC-001"},
                        },
                    ],
                }
            },
            {
                "message": {
                    "role": "toolResult",
                    "toolName": "get_incident_logs",
                    "isError": False,
                }
            },
            {
                "message": {
                    "role": "toolResult",
                    "toolName": "get_incident_metrics",
                    "isError": False,
                }
            },
        ]
        path = self.make_run("qwen", 2, answer, records, 46970, 2)
        row = evaluate.score_run(path, self.root, self.rubrics)
        self.assertTrue(row["root_cause_match"])
        self.assertTrue(row["required_evidence_present"])
        self.assertEqual(row["failed_results"], 0)
        self.assertEqual(row["missing_incident_id_args"], 0)
        self.assertEqual(row["parallel_tool_batches"], 1)
        self.assertIn("M-004", row["citation_warning"])

    def test_cloud_repeated_empty_arguments_then_recovers(self):
        records = []
        for _ in range(2):
            records.append(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "name": "get_incident_logs",
                                "arguments": {},
                            },
                            {
                                "type": "toolCall",
                                "name": "get_incident_metrics",
                                "arguments": {},
                            },
                        ],
                    }
                }
            )
            for tool in ("get_incident_logs", "get_incident_metrics"):
                records.append(
                    {
                        "message": {
                            "role": "toolResult",
                            "toolName": tool,
                            "isError": True,
                            "content": [
                                {
                                    "type": "text",
                                    "text": 'Validation failed for tool "'
                                    + tool
                                    + '": incidentId required',
                                }
                            ],
                        }
                    }
                )
        for tool in ("get_incident_logs", "get_incident_metrics"):
            records.append(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "name": tool,
                                "arguments": {"incidentId": "INC-001"},
                            },
                        ],
                    }
                }
            )
            records.append(
                {"message": {"role": "toolResult", "toolName": tool, "isError": False}}
            )
        answer = (
            "### Diagnosis\nLocal disk exhaustion caused persistence to fail.\n"
            "### Supporting Evidence\nL-002 and M-001."
        )
        path = self.make_run("cloud", 1, answer, records, 20932, 6)
        row = evaluate.score_run(path, self.root, self.rubrics)
        self.assertEqual(row["calls"], 6)
        self.assertEqual(row["failed_results"], 4)
        self.assertEqual(row["schema_validation_errors"], 4)
        self.assertEqual(row["missing_incident_id_args"], 4)
        self.assertTrue(row["recovered_after_errors"])
        self.assertEqual(row["parallel_tool_batches"], 2)
        self.assertTrue(row["call_count_matches_metadata"])

    def test_missing_transcript_is_failure_not_zero_errors(self):
        p = self.private / "qwen-inc001-001.json"
        p.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "result": {
                        "meta": {
                            "agentMeta": {"sessionId": "missing"},
                            "durationMs": 1000,
                            "finalAssistantRawText": "Disk exhaustion. L-002 M-001",
                        }
                    },
                }
            )
        )
        with self.assertRaises(FileNotFoundError):
            evaluate.score_run(p, self.root, self.rubrics)

    def test_wrong_root_cause_not_awarded_based_on_later_enospc(self):
        answer = "### Diagnosis\nThe broker disconnected.\n### Evidence\nLog said ENOSPC; L-002 and M-001."
        self.assertFalse(
            evaluate.score_diagnosis(
                answer, self.rubrics["INC-001"]["diagnosis_patterns"]
            )
        )

    def test_answer_key_mapping_and_required_evidence(self):
        rubric = self.rubrics["INC-001"]
        self.assertEqual(rubric["expected_cause"], "DISK_EXHAUSTION")
        self.assertEqual(rubric["required_evidence"], ("M-001", "L-002"))
        self.assertTrue(
            evaluate.score_diagnosis(
                "Root cause: disk space exhaustion", rubric["diagnosis_patterns"]
            )
        )

    def test_second_incident_uses_key_defined_patterns(self):
        key = json.loads(self.keyfile.read_text())
        key["INC-002"] = {
            "rootCauseCode": "BROKER_CONNECTION_FAILURE",
            "rootCauseDescription": "Telemetry failed after broker disconnect",
            "requiredEvidenceIds": ["L-020", "M-021"],
            "diagnosisPatterns": [
                r"broker\s+(?:connection\s+)?(?:failure|disconnection)",
                r"broker\s+disconnected",
            ],
        }
        self.keyfile.write_text(json.dumps(key))
        rubrics = evaluate.load_rubrics(self.keyfile)
        self.assertIn("INC-002", rubrics)
        self.assertTrue(
            evaluate.score_diagnosis(
                "Diagnosis: MQTT broker disconnected; publishing suspended.",
                rubrics["INC-002"]["diagnosis_patterns"],
            )
        )
        self.assertFalse(
            evaluate.score_diagnosis(
                "Diagnosis: disk exhaustion.\nEvidence: broker disconnected",
                rubrics["INC-002"]["diagnosis_patterns"],
            )
        )

    def test_missing_patterns_for_new_cause_fails_closed(self):
        key = {
            "INC-002": {
                "rootCauseCode": "BROKER_CONNECTION_FAILURE",
                "requiredEvidenceIds": ["L-020"],
            }
        }
        self.keyfile.write_text(json.dumps(key))
        with self.assertRaisesRegex(ValueError, "diagnosisPatterns"):
            evaluate.load_rubrics(self.keyfile)

    def test_bad_answer_key_evidence_rejected(self):
        key = {
            "INC-002": {
                "rootCauseCode": "BROKER_CONNECTION_FAILURE",
                "requiredEvidenceIds": ["L-020", "L-020"],
                "diagnosisPatterns": [r"broker disconnect"],
            }
        }
        self.keyfile.write_text(json.dumps(key))
        with self.assertRaisesRegex(ValueError, "requiredEvidenceIds"):
            evaluate.load_rubrics(self.keyfile)

    def test_disk_exhaustion_alternative_phrases(self):
        patterns = DEFAULT_CAUSE_PATTERNS["DISK_EXHAUSTION"]

        self.assertTrue(score_diagnosis("The device ran out of disk space.", patterns))
        self.assertTrue(
            score_diagnosis("The filesystem ran out of writable space.", patterns)
        )

    def test_aggregate_counts_for_synthetic_runs(self):
        rows = []
        for agent, durations in (
            ("bench-qwen", [39.0, 40.0, 41.0, 42.0]),
            ("bench-cloud", [10.0, 11.0, 12.0, 13.0]),
        ):
            for i in range(4):
                rows.append(
                    {
                        "incident": "INC-001",
                        "agent": agent,
                        "root_cause_match": True,
                        "required_evidence_present": True,
                        "calls": 2,
                        "failed_results": 0,
                        "schema_validation_errors": 0,
                        "missing_incident_id_args": 0,
                        "citation_warning": "review"
                        if agent == "bench-qwen" and i == 1
                        else "",
                        "duration_s": durations[i],
                    }
                )
        groups = {g["agent"]: g for g in evaluate.aggregate(rows)}
        self.assertEqual(groups["bench-qwen"]["total_calls"], 8)
        self.assertEqual(groups["bench-qwen"]["total_tool_errors"], 0)
        self.assertEqual(groups["bench-cloud"]["total_calls"], 8)
        self.assertEqual(groups["bench-cloud"]["total_tool_errors"], 0)
        self.assertEqual(groups["bench-qwen"]["trials"], 4)
        self.assertEqual(groups["bench-qwen"]["runs_with_citation_warnings"], 1)
        self.assertEqual(groups["bench-qwen"]["mean_duration_s"], 40.5)
        self.assertEqual(groups["bench-cloud"]["mean_duration_s"], 11.5)


if __name__ == "__main__":
    unittest.main()
