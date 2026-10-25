"""Offline regression tests for judge response validation and input handling."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from judge_semantic import (
    main,
    make_prompt,
    parse_judge_result,
    select_runs,
    source_answer,
)


def fake_judge(verdict=None, model="gpt-6-sol", tools=None, auth="auth-profile"):
    session = "judge-v1-test"
    verdict = verdict or {
        "root_cause_correct": True,
        "requires_review": False,
        "rationale": "Publisher switched topics but consumer remained on the old filter.",
    }
    result = {
        "status": "ok",
        "result": {
            "meta": {
                "agentMeta": {
                    "sessionId": session,
                    "provider": "openai",
                    "model": model,
                    "agentHarnessId": "codex",
                    "terminalReceipt": {
                        "effective": {
                            "provider": "openai",
                            "model": model,
                            "responseModel": model,
                        },
                        "rerouted": False,
                        "successfulToolNames": [] if tools is None else tools,
                    },
                },
                "executionTrace": {"fallbackUsed": False},
                "requestShaping": {"authMode": auth},
                "codeModeEngaged": False,
                "finalAssistantRawText": json.dumps(verdict),
            }
        },
    }
    return result, session


class JudgeSemanticTests(unittest.TestCase):
    def test_correct_judgment(self):
        payload, session = fake_judge()
        verdict, _ = parse_judge_result(payload, session)
        self.assertIs(verdict["root_cause_correct"], True)

    def test_wrong_cause_can_be_rejected(self):
        payload, session = fake_judge(
            {
                "root_cause_correct": False,
                "requires_review": False,
                "rationale": "Disk is not the causal mechanism.",
            }
        )
        verdict, _ = parse_judge_result(payload, session)
        self.assertIs(verdict["root_cause_correct"], False)

    def test_missing_code_mode_flag_with_zero_tool_schemas_accepted(self):
        payload, session = fake_judge()
        meta = payload["result"]["meta"]
        del meta["codeModeEngaged"]
        meta["systemPromptReport"] = {"tools": {"schemaChars": 0, "listChars": 0}}
        verdict, _ = parse_judge_result(payload, session)
        self.assertIs(verdict["root_cause_correct"], True)

    def test_missing_code_mode_flag_without_tool_report_rejected(self):
        payload, session = fake_judge()
        del payload["result"]["meta"]["codeModeEngaged"]
        with self.assertRaisesRegex(ValueError, "zero exposed tool schemas"):
            parse_judge_result(payload, session)

    def test_missing_code_mode_flag_with_nonzero_tool_schema_rejected(self):
        payload, session = fake_judge()
        meta = payload["result"]["meta"]
        del meta["codeModeEngaged"]
        meta["systemPromptReport"] = {"tools": {"schemaChars": 7, "listChars": 0}}
        with self.assertRaisesRegex(ValueError, "zero exposed tool schemas"):
            parse_judge_result(payload, session)

    def test_code_mode_true_rejected(self):
        payload, session = fake_judge()
        payload["result"]["meta"]["codeModeEngaged"] = True
        with self.assertRaisesRegex(ValueError, "Code Mode"):
            parse_judge_result(payload, session)

    def test_code_mode_null_rejected(self):
        payload, session = fake_judge()
        payload["result"]["meta"]["codeModeEngaged"] = None
        with self.assertRaisesRegex(ValueError, "Code Mode"):
            parse_judge_result(payload, session)

    def test_recover_saved_raw_without_invoking_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            source.mkdir()
            source_path = source / "bench-cloud-inc003-001.json"
            source_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "result": {
                            "meta": {
                                "finalAssistantRawText": "Publisher topic changed to v2 but consumer subscribed to v1"
                            }
                        },
                    }
                )
            )
            reference = (
                "Publisher changed topics to v2 but consumer stayed subscribed to v1"
            )
            key = root / "answer-key.json"
            key.write_text(json.dumps({"INC-003": {"rootCauseDescription": reference}}))
            output = root / "derived"
            output.mkdir()
            stem = source_path.stem
            candidate = "Publisher topic changed to v2 but consumer subscribed to v1"
            (output / f"{stem}.prompt.txt").write_text(
                make_prompt(reference, candidate)
            )
            payload, _ = fake_judge()
            meta = payload["result"]["meta"]
            del meta["codeModeEngaged"]
            meta["systemPromptReport"] = {"tools": {"schemaChars": 0, "listChars": 0}}
            session_id = "judge-v1-" + "a" * 24
            meta["agentMeta"]["sessionId"] = session_id
            meta["agentMeta"]["terminalReceipt"]["sessionId"] = session_id
            (output / f"{stem}.openclaw.json").write_text(json.dumps(payload))
            (output / f"{stem}.stderr.txt").write_text("")
            args = [
                "--results",
                str(source),
                "--answer-key",
                str(key),
                "--incident",
                "INC-003",
                "--output-dir",
                str(output),
                "--limit",
                "1",
                "--recover-only",
            ]
            with patch(
                "judge_semantic.subprocess.run",
                side_effect=AssertionError("Model called!"),
            ):
                self.assertEqual(main(args), 0)
            record = json.loads((output / f"{stem}.judgment.json").read_text())
            self.assertTrue(record["recovered_from_raw"])
            self.assertTrue(record["root_cause_correct"])
            self.assertEqual(record["session_id"], session_id)
            self.assertEqual(record["validator_version"], "judge-validator-v1.1")
            self.assertTrue((output / "summary.csv").exists())

    def test_recover_mismatched_prompt_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            source.mkdir()
            (source / "bench-cloud-inc003-001.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "result": {
                            "meta": {"finalAssistantRawText": "Correct diagnosis"}
                        },
                    }
                )
            )
            key = root / "key.json"
            key.write_text(
                json.dumps({"INC-003": {"rootCauseDescription": "Correct diagnosis"}})
            )
            output = root / "out"
            output.mkdir()
            (output / "bench-cloud-inc003-001.prompt.txt").write_text(
                "DIFFERENT PROMPT"
            )
            (output / "bench-cloud-inc003-001.openclaw.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "Saved prompt differs"):
                main(
                    [
                        "--results",
                        str(source),
                        "--answer-key",
                        str(key),
                        "--incident",
                        "INC-003",
                        "--output-dir",
                        str(output),
                        "--limit",
                        "1",
                        "--recover-only",
                    ]
                )

    def test_new_incident_can_be_dry_run_without_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "inputs"
            source.mkdir()
            (source / "bench-qwen-inc004-001.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "result": {
                            "meta": {
                                "finalAssistantRawText": "The consumer rejects messages due to a schema mismatch."
                            }
                        },
                    }
                )
            )
            key = root / "answer-key.json"
            key.write_text(
                json.dumps(
                    {
                        "INC-004": {
                            "rootCauseDescription": "Downstream payload schema mismatch"
                        }
                    }
                )
            )
            with patch(
                "judge_semantic.subprocess.run",
                side_effect=AssertionError("Model called"),
            ):
                self.assertEqual(
                    main(
                        [
                            "--results",
                            str(source),
                            "--answer-key",
                            str(key),
                            "--incident",
                            "INC-004",
                            "--output-dir",
                            str(root / "derived"),
                            "--limit",
                            "1",
                            "--dry-run",
                        ]
                    ),
                    0,
                )

    def test_wrong_model_rejected(self):
        payload, session = fake_judge(model="gpt-5.6-sol")
        with self.assertRaisesRegex(ValueError, "model/provider"):
            parse_judge_result(payload, session)

    def test_tools_rejected(self):
        payload, session = fake_judge(tools=["get_incident_logs"])
        with self.assertRaisesRegex(ValueError, "tool use"):
            parse_judge_result(payload, session)

    def test_non_oauth_rejected(self):
        payload, session = fake_judge(auth="api-key")
        with self.assertRaisesRegex(ValueError, "OAuth"):
            parse_judge_result(payload, session)

    def test_session_mismatch_rejected(self):
        payload, _ = fake_judge()
        with self.assertRaisesRegex(ValueError, "session ID"):
            parse_judge_result(payload, "another")

    def test_non_boolean_rejected(self):
        payload, session = fake_judge(
            {
                "root_cause_correct": "true",
                "requires_review": False,
                "rationale": "Invalid",
            }
        )
        with self.assertRaisesRegex(ValueError, "boolean"):
            parse_judge_result(payload, session)

    def test_fenced_json_rejected(self):
        payload, session = fake_judge()
        payload["result"]["meta"]["finalAssistantRawText"] = (
            '```json\n{"root_cause_correct":true}\n```'
        )
        with self.assertRaisesRegex(ValueError, "plain JSON"):
            parse_judge_result(payload, session)

    def test_prompt_blind_and_negative_guard(self):
        prompt = make_prompt(
            "Publisher v2 and consumer v1", "Disk exhaustion is root cause"
        )
        self.assertIn("incorrect primary cause", prompt)
        self.assertIn("Disk exhaustion", prompt)
        self.assertNotIn("bench-cloud", prompt)
        self.assertNotIn("bench-qwen", prompt)

    def test_input_and_balanced_first_two_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for model in ("cloud", "qwen"):
                for trial in (1, 2):
                    name = f"bench-{model}-inc003-{trial:03d}.json"
                    (directory / name).write_text(
                        json.dumps(
                            {
                                "status": "ok",
                                "result": {
                                    "meta": {
                                        "finalAssistantRawText": "A root cause diagnosis."
                                    }
                                },
                            }
                        )
                    )
            runs = select_runs(directory, "INC-003")
            self.assertEqual(
                [p.stem for p in runs[:2]],
                ["bench-cloud-inc003-001", "bench-qwen-inc003-001"],
            )
            self.assertEqual(source_answer(runs[0]), "A root cause diagnosis.")


if __name__ == "__main__":
    unittest.main()
