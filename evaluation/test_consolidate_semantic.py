import csv
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import consolidate_semantic as c
import judge_semantic as judge


class TestConsolidate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.base = Path(self.td.name)
        self.sources = self.base / "orig"
        self.sources.mkdir()
        self.staged = self.base / "stage"
        self.staged.mkdir()
        self.judges = self.base / "judges"
        self.judges.mkdir()
        self.answer = self.base / "answer-key.json"
        self.key = {
            "INC-001": {
                "rootCauseDescription": "Disk exhaustion stopped persistence.",
                "requiredEvidenceIds": ["L-002", "M-001"],
            },
            "INC-002": {
                "rootCauseDescription": "Expired client TLS certificate blocked MQTT.",
                "requiredEvidenceIds": ["L-013", "M-013"],
            },
            "INC-003": {
                "rootCauseDescription": "Publisher v2 and consumer v1 topic mismatch.",
                "requiredEvidenceIds": ["L-022", "L-024", "M-025"],
            },
        }
        self.answer.write_text(json.dumps(self.key))
        self.rows = []
        self.items = {}

    def add_run(
        self, incident="INC-003", agent="cloud", trial=1, root=True, archived=True
    ):
        rid = f"{agent}-inc{incident[-3:]}-{trial:03d}"
        evidence = self.key[incident]["requiredEvidenceIds"]
        answer = (
            "Primary cause is "
            + self.key[incident]["rootCauseDescription"]
            + " Evidence: "
            + ", ".join(evidence)
        )
        provider, model = (
            ("openai", "gpt-5.6-sol")
            if agent == "cloud"
            else ("llamacpp", "qwen3.5:9b")
        )
        source = self.sources / f"bench-{rid}.json"
        run_data = {
            "status": "ok",
            "result": {
                "payloads": [{"text": answer}],
                "meta": {
                    "durationMs": 12345,
                    "agentMeta": {
                        "provider": provider,
                        "model": model,
                        "sessionId": f"candidate-{rid}",
                        "codeModeEngaged": False,
                    },
                    "toolSummary": {"calls": 2},
                    "finalAssistantRawText": answer,
                },
            },
        }
        source.write_text(json.dumps(run_data))
        staged = self.staged / source.name
        if archived:
            staged.write_bytes(source.read_bytes())
        run_row = {
            "run_id": rid,
            "incident": incident,
            "agent": f"bench-{agent}",
            "status": "ok",
            "root_cause_match": "False"
            if root
            else "True",  # Intentionally contradict semantic grade.
            "required_evidence_present": "True",
            "evidence_coverage": f"{len(evidence)}/{len(evidence)}",
            "calls": "2",
            "tool_results": "2",
            "failed_results": "0",
            "missing_incident_id_args": "0",
            "duration_s": "12.345",
            "source_json": str(source),
            "citation_warning": "",
        }
        self.rows.append(run_row)
        session = "judge-v1-" + hashlib.sha256(rid.encode()).hexdigest()[:24]
        prompt = judge.make_prompt(self.key[incident]["rootCauseDescription"], answer)
        verdict = {
            "root_cause_correct": root,
            "requires_review": False,
            "rationale": "Primary cause matches.",
        }
        reply = {
            "status": "ok",
            "result": {
                "meta": {
                    "durationMs": 3500,
                    "agentMeta": {
                        "sessionId": session,
                        "provider": "openai",
                        "model": "gpt-6-sol",
                        "agentHarnessId": "codex",
                        "terminalReceipt": {
                            "sessionId": session,
                            "effective": {
                                "provider": "openai",
                                "model": "gpt-6-sol",
                                "responseModel": "gpt-6-sol",
                            },
                            "rerouted": False,
                            "successfulToolNames": [],
                        },
                    },
                    "executionTrace": {"fallbackUsed": False},
                    "requestShaping": {"authMode": "auth-profile"},
                    "systemPromptReport": {"tools": {"schemaChars": 0, "listChars": 0}},
                    "finalAssistantRawText": json.dumps(verdict),
                }
            },
        }
        pref = self.judges / f"bench-{rid}"
        (self.judges / (pref.name + ".prompt.txt")).write_text(prompt)
        (self.judges / (pref.name + ".openclaw.json")).write_text(json.dumps(reply))
        (self.judges / (pref.name + ".judgment.json")).write_text(
            json.dumps(
                {
                    "run_id": f"bench-{rid}",
                    "incident": incident,
                    "rubric_version": judge.RUBRIC_VERSION,
                    "judge_model": "gpt-6-sol",
                    "validator_version": judge.VALIDATOR_VERSION,
                    "root_cause_correct": root,
                    "requires_review": False,
                    "rationale": "Primary cause matches.",
                    "session_id": session,
                    "source_sha256": c.sha_file(staged)
                    if archived
                    else c.sha_file(source),
                    "prompt_sha256": judge.digest(prompt),
                    "judge_duration_ms": 3500,
                    "recovered_from_raw": False,
                }
            )
        )
        self.items[rid] = source
        return source

    def write_mechanical(self):
        path = self.base / "runs.csv"
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.rows[0]))
            w.writeheader()
            w.writerows(self.rows)
        return path

    def config_for(self, per_agent=1):
        groups = {
            inc: {
                "sources_dir": str(self.staged),
                "deterministic_runs_csv": str(self.write_mechanical()),
                "judge_dir": str(self.judges),
            }
            for inc in self.key
        }
        cfg = {
            "schema": c.SCHEMA,
            "experiment": {
                "runtime": "OpenClaw 2026.10.5",
                "candidate_models": {
                    "bench-cloud": "openai/gpt-5.6-sol",
                    "bench-qwen": "llamacpp/qwen3.5:9b",
                },
            },
            "judge_model": "gpt-6-sol",
            "rubric": judge.RUBRIC_VERSION,
            "validator": judge.VALIDATOR_VERSION,
            "answer_key": str(self.answer),
            "trials_per_agent_per_incident": per_agent,
            "incident_groups": groups,
        }
        cfg_path = self.base / "manifest.json"
        cfg_path.write_text(json.dumps(cfg))
        return cfg_path

    def make_complete(self):
        for inc in ("INC-001", "INC-002", "INC-003"):
            self.add_run(inc, "cloud", root=True)
            self.add_run(inc, "qwen", root=(inc != "INC-002"))
        return self.config_for()

    def test_canon_and_safety(self):
        self.assertEqual(c.canon("bench-cloud-inc003-001.json"), "cloud-inc003-001")
        self.assertEqual(c.canon("qwen-inc002-004"), "qwen-inc002-004")
        with self.assertRaises(ValueError):
            c.canon("cloud-inc001-serial-005.json")

    def test_stage_dry_run_no_mutations_and_copy(self):
        self.add_run("INC-001", "cloud", archived=False)
        self.add_run("INC-001", "qwen", archived=False)
        baseline = self.write_mechanical()
        out = self.base / "stage-copy"
        p = type(
            "X",
            (),
            dict(
                incident="INC-001",
                mechanical_csv=baseline,
                search_root=self.sources,
                output_dir=out,
                per_agent=1,
                dry_run=True,
            ),
        )()
        with redirect_stdout(StringIO()):
            c.stage(p)
        self.assertFalse(out.exists())
        p.dry_run = False
        with redirect_stdout(StringIO()):
            c.stage(p)
        self.assertEqual(len(list(out.glob("bench-*.json"))), 2)
        for rid, source in self.items.items():
            self.assertEqual(c.sha_file(source), c.sha_file(out / f"bench-{rid}.json"))

    def test_stage_rejects_ambiguous_archive(self):
        self.add_run("INC-001", "cloud", archived=False)
        self.add_run("INC-001", "qwen", archived=False)
        duplicate = self.sources / "second"
        duplicate.mkdir()
        p = self.items["cloud-inc001-001"]
        (duplicate / p.name).write_bytes(p.read_bytes())
        baseline = self.write_mechanical()
        # The source path in the CSV takes precedence when still present.
        args = type(
            "X",
            (),
            dict(
                incident="INC-001",
                mechanical_csv=baseline,
                search_root=self.sources,
                output_dir=self.base / "dest",
                per_agent=1,
                dry_run=True,
            ),
        )()
        with redirect_stdout(StringIO()):
            c.stage(args)
        # Without a matching explicit source path, duplicates fail.
        self.rows[0]["source_json"] = "/tmp/missing-source.json"
        args.mechanical_csv = self.write_mechanical()
        with self.assertRaisesRegex(ValueError, "Expected one matching source"):
            c.stage(args)

    def test_report_primary_accuracy_is_not_regex(self):
        manifest = self.make_complete()
        output = self.base / "report"
        args = type(
            "X", (), dict(manifest=manifest, output_dir=output, check_only=True)
        )()
        with redirect_stdout(StringIO()):
            c.report(args)
        self.assertFalse(output.exists())
        args.check_only = False
        with redirect_stdout(StringIO()):
            c.report(args)
        report = (output / "report.md").read_text()
        self.assertIn("Root-cause accuracy (GPT-6 semantic judge): 5/6", report)
        self.assertIn(
            "Deterministic evidence and tool checks are scored separately", report
        )
        self.assertIn("INC-003", report)
        rows = c.read_csv(output / "runs.csv")
        self.assertEqual(len(rows), 6)
        self.assertEqual(set(r["rubric_version"] for r in rows), {"semantic-root-v1"})
        self.assertTrue((output / "provenance.json").is_file())
        # Same verified input is idempotent.
        with redirect_stdout(StringIO()):
            c.report(args)

    def test_report_discloses_judge_drift_and_public_reproduction_boundary(self):
        manifest = self.make_complete()
        out = self.base / "report-docrev1"
        args = type(
            "X", (), dict(manifest=manifest, output_dir=out, check_only=False)
        )()
        with redirect_stdout(StringIO()):
            c.report(args)
        md = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Reproducibility scope", md)
        self.assertIn("Private offline verification", md)
        self.assertIn("without any candidate or judge model calls", md)
        self.assertIn("Independent public reproduction", md)
        self.assertIn("aggregate-only Markdown report", md)
        self.assertIn("synthetic fixtures", md)
        self.assertIn("bench-judge.contextInjection", md)
        self.assertIn("for INC-003 versus INC-001/002", md)
        self.assertIn(
            "a fixed rubric alone does not establish byte-identical instructions", md
        )
        # Narrative-only change: primary grading remains the saved semantic verdict.
        self.assertIn("Root-cause accuracy (GPT-6 semantic judge): 5/6", md)
        self.assertEqual(len(c.read_csv(out / "runs.csv")), 6)
        self.assertTrue((out / "provenance.json").is_file())

    def test_missing_judge_fails_closed(self):
        manifest = self.make_complete()
        (self.judges / "bench-qwen-inc003-001.judgment.json").unlink()
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "report", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "Missing saved GPT-6"):
            c.report(args)

    def test_tampered_original_fails_hash(self):
        manifest = self.make_complete()
        path = self.staged / "bench-cloud-inc001-001.json"
        data = json.loads(path.read_text())
        data["new_key"] = 1
        path.write_text(json.dumps(data))
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "report", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            c.report(args)

    def test_evidence_mismatch_fails(self):
        manifest = self.make_complete()
        self.rows[0]["evidence_coverage"] = "1/2"
        # Replace mechanical evidence and ensure this is caught independently.
        self.write_mechanical()
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "report", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "Evidence coverage mismatch"):
            c.report(args)

    def test_model_mismatch_fails(self):
        manifest = self.make_complete()
        path = self.staged / "bench-cloud-inc001-001.json"
        data = json.loads(path.read_text())
        data["result"]["meta"]["agentMeta"]["model"] = "gpt-5.5-sol"
        path.write_text(json.dumps(data))
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "report", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "Candidate model does not match"):
            c.report(args)

    def test_duplicate_candidate_session_is_rejected(self):
        manifest = self.make_complete()
        path = self.staged / "bench-qwen-inc001-001.json"
        data = json.loads(path.read_text())
        data["result"]["meta"]["agentMeta"]["sessionId"] = "candidate-cloud-inc001-001"
        path.write_text(json.dumps(data))
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "out", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "Duplicate candidate session ID"):
            c.report(args)

    def test_candidate_code_mode_true_is_rejected(self):
        manifest = self.make_complete()
        path = self.staged / "bench-cloud-inc001-001.json"
        data = json.loads(path.read_text())
        data["result"]["meta"]["agentMeta"]["codeModeEngaged"] = True
        path.write_text(json.dumps(data))
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "out", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "Code Mode"):
            c.report(args)

    def test_candidate_tool_summary_mismatch_is_rejected(self):
        manifest = self.make_complete()
        self.rows[0]["calls"] = "9"
        self.write_mechanical()
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "out", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "tool metadata/count mismatch"):
            c.report(args)

    def test_external_frozen_prompt_audit_is_verified(self):
        manifest = self.make_complete()
        audit_path = self.base / "candidate-audit.json"
        frozen = c.load_frozen_prompts(
            Path(__file__).resolve().parent.parent / "docs/original-prompts.md"
        )
        rows = {}
        for rid, original in self.items.items():
            raw = json.loads(original.read_text())
            incident = c.incident_of(rid)
            rows[rid] = {
                "source_sha256": c.sha_file(original),
                "candidate_session_id": raw["result"]["meta"]["agentMeta"]["sessionId"],
                "prompt_sha256": c.sha_bytes(frozen[incident].encode("utf-8")),
                "code_mode_engaged": False,
                "tool_calls": 2,
                "tool_results": 2,
                "tool_errors": 0,
                "transcript_sha256": "0" * 64,
            }
        audit_path.write_text(
            json.dumps(
                {
                    "schema": "edgeagentbench-frozen-candidate-audit-v1",
                    "runs": rows,
                }
            )
        )
        config = json.loads(manifest.read_text())
        config["candidate_audit_file"] = str(audit_path)
        manifest.write_text(json.dumps(config))
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "audited", check_only=False),
        )()
        with redirect_stdout(StringIO()):
            c.report(args)
        self.assertIn(
            "candidate_audit_sha256", (args.output_dir / "provenance.json").read_text()
        )
        self.assertIn(
            "A separate frozen-prompt/SQLite transcript audit",
            (args.output_dir / "report.md").read_text(),
        )
        # A switched source hash must be rejected before any report is published.
        rows["cloud-inc001-001"]["source_sha256"] = "0" * 64
        audit_path.write_text(
            json.dumps(
                {"schema": "edgeagentbench-frozen-candidate-audit-v1", "runs": rows}
            )
        )
        args.output_dir = self.base / "tampered"
        with self.assertRaisesRegex(ValueError, "Candidate prompt/audit mismatch"):
            c.report(args)

    def test_new_incident_is_supported_by_manifest(self):
        self.key["INC-004"] = {
            "rootCauseDescription": "Schema incompatibility rejects downstream telemetry.",
            "requiredEvidenceIds": ["L-031", "M-031"],
        }
        self.answer.write_text(json.dumps(self.key))
        for inc in ("INC-001", "INC-002", "INC-003", "INC-004"):
            self.add_run(inc, "cloud")
            self.add_run(inc, "qwen")
        manifest = self.config_for(per_agent=1)
        output = self.base / "four-incidents"
        args = type(
            "X", (), dict(manifest=manifest, output_dir=output, check_only=False)
        )()
        with redirect_stdout(StringIO()):
            c.report(args)
        self.assertEqual(len(c.read_csv(output / "runs.csv")), 8)
        text = (output / "report.md").read_text()
        self.assertIn("4 synthetic incidents", text)
        self.assertIn("INC-004", text)

    def test_frozen_prompt_loader_allows_added_incident(self):
        prompts_file = self.base / "prompts.md"
        prompts_file.write_text(
            "\n".join(
                f"## INC-{i:03d}\n\n> Investigate incident INC-{i:03d}."
                for i in range(1, 5)
            )
        )
        prompts = c.load_frozen_prompts(prompts_file)
        self.assertEqual(len(prompts), 4)
        self.assertEqual(prompts["INC-004"], "Investigate incident INC-004.")

    def test_bad_manifest_and_partial_not_reported(self):
        manifest = self.make_complete()
        p = json.loads(manifest.read_text())
        p["rubric"] = "regex-v1"
        manifest.write_text(json.dumps(p))
        args = type(
            "X",
            (),
            dict(manifest=manifest, output_dir=self.base / "report", check_only=True),
        )()
        with self.assertRaisesRegex(ValueError, "same GPT-6 Sol semantic rubric"):
            c.report(args)


if __name__ == "__main__":
    unittest.main()
