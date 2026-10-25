"""Regression tests: private report data must yield only allowlisted public fields."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from publish_results import build_public


class PublicResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        rows = []
        groups = []
        sources = {}
        judges = {}
        for incident in ("INC-001", "INC-002", "INC-003"):
            for agent in ("bench-cloud", "bench-qwen"):
                records = []
                for trial in range(1, 5):
                    model = agent.removeprefix("bench-")
                    rid = f"{model}-inc{incident[-3:]}-{trial:03d}"
                    row = {
                        "run_id": rid,
                        "incident": incident,
                        "agent": agent,
                        "candidate_model": "gpt-5.6-sol"
                        if model == "cloud"
                        else "qwen3.5:9b",
                        "source_sha256": f"sha-{rid}",
                        "root_cause_correct_gpt6": "True",
                        "requires_review": "False",
                        "full_required_evidence": "True",
                        "required_evidence_found": 2,
                        "required_evidence_total": 2,
                        "tool_calls": 2,
                        "tool_results": 2,
                        "tool_errors": 0,
                        "missing_incident_id_args": 0,
                        "candidate_duration_s": 12.345,
                        "judge_session_id": f"private-{rid}",
                    }
                    rows.append(row)
                    records.append(row)
                    sources[rid] = row["source_sha256"]
                    judges[rid] = {"session_id": row["judge_session_id"]}
                groups.append(
                    {
                        "incident": incident,
                        "agent": agent,
                        "trials": 4,
                        "root_correct": 4,
                        "full_evidence": 4,
                        "calls": 8,
                        "tool_errors": 0,
                        "missing_id": 0,
                        "mean_duration_s": 12.345,
                    }
                )
        with (self.root / "runs.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
        (self.root / "summary.json").write_text(
            json.dumps(
                {
                    "schema": "edgeagentbench-consolidated-gpt6-v1",
                    "trials": 24,
                    "groups": groups,
                }
            )
        )
        (self.root / "provenance.json").write_text(
            json.dumps(
                {
                    "schema": "edgeagentbench-consolidated-gpt6-v1",
                    "sources": sources,
                    "judges": judges,
                    "private_directory": "/home/private/secret",
                }
            )
        )

    def test_public_results_redact_private_metadata(self):
        public = build_public(self.root)
        self.assertEqual(set(public), {"runs.csv", "summary.csv", "README.md"})
        for content in public.values():
            self.assertNotIn(b"private-", content)
            self.assertNotIn(b"judge_session_id", content)
            self.assertNotIn(b"/home/private/secret", content)
            self.assertNotIn(b"sha-", content)
        self.assertIn(b"24/24", public["README.md"])

    def _rewrite_fixture(self, keep):
        path = self.root / "runs.csv"
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = [row for row in reader if keep(row)]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        config = json.loads((self.root / "summary.json").read_text())
        groups = [
            g
            for g in config["groups"]
            if any(
                r["incident"] == g["incident"] and r["agent"] == g["agent"]
                for r in rows
            )
        ]
        for g in groups:
            matching = [
                r
                for r in rows
                if r["incident"] == g["incident"] and r["agent"] == g["agent"]
            ]
            g["trials"] = len(matching)
            g["root_correct"] = len(matching)
            g["full_evidence"] = len(matching)
            g["calls"] = 2 * len(matching)
        config["groups"] = groups
        config["trials"] = len(rows)
        (self.root / "summary.json").write_text(json.dumps(config))
        provenance_path = self.root / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        ids = {r["run_id"] for r in rows}
        provenance["sources"] = {
            rid: value for rid, value in provenance["sources"].items() if rid in ids
        }
        provenance["judges"] = {
            rid: value for rid, value in provenance["judges"].items() if rid in ids
        }
        provenance_path.write_text(json.dumps(provenance))

    def test_different_trial_count_is_derived_from_summary(self):
        self._rewrite_fixture(lambda r: int(r["run_id"][-3:]) <= 2)
        public = build_public(self.root)
        self.assertIn(b"12/12", public["README.md"])
        self.assertIn(b"2 independent trials per candidate", public["README.md"])
        self.assertEqual(len(public["runs.csv"].splitlines()) - 1, 12)

    def test_new_incident_is_supported_by_summary(self):
        runs_path = self.root / "runs.csv"
        with runs_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            rows = list(reader)
        new_rows = []
        for previous in rows:
            if previous["incident"] != "INC-003":
                continue
            new = dict(previous)
            rid = previous["run_id"].replace("inc003", "inc004")
            new.update(
                run_id=rid,
                incident="INC-004",
                source_sha256=f"sha-{rid}",
                judge_session_id=f"private-{rid}",
            )
            new_rows.append(new)
        with runs_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows + new_rows)
        private_summary = self.root / "summary.json"
        cfg = json.loads(private_summary.read_text())
        cfg["groups"] += [
            {**g, "incident": "INC-004"}
            for g in cfg["groups"]
            if g["incident"] == "INC-003"
        ]
        cfg["trials"] += len(new_rows)
        private_summary.write_text(json.dumps(cfg))
        private_provenance = self.root / "provenance.json"
        prov = json.loads(private_provenance.read_text())
        for row in new_rows:
            prov["sources"][row["run_id"]] = row["source_sha256"]
            prov["judges"][row["run_id"]] = {"session_id": row["judge_session_id"]}
        private_provenance.write_text(json.dumps(prov))
        public = build_public(self.root)
        self.assertIn(b"32/32", public["README.md"])
        self.assertIn(b"4 synthetic incidents", public["README.md"])
        self.assertIn(b"INC-004", public["summary.csv"])
        self.assertEqual(len(public["runs.csv"].splitlines()) - 1, 32)

    def test_missing_group_or_incomplete_archive_is_rejected(self):
        private_summary = self.root / "summary.json"
        config = json.loads(private_summary.read_text())
        config["groups"] = config["groups"][:-1]
        private_summary.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "Missing candidate group"):
            build_public(self.root)

    def _change_first_run(self, **changes):
        path = self.root / "runs.csv"
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            rows = list(reader)
        rows[0].update(changes)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_full_evidence_flag_must_match_coverage(self):
        self._change_first_run(
            required_evidence_found="1",
            required_evidence_total="2",
            full_required_evidence="True",
        )
        with self.assertRaisesRegex(ValueError, "Inconsistent evidence coverage"):
            build_public(self.root)

    def test_missing_tool_result_is_rejected(self):
        self._change_first_run(tool_calls="2", tool_results="1")
        with self.assertRaisesRegex(ValueError, "Inconsistent tool counts"):
            build_public(self.root)

    def test_missing_id_count_cannot_exceed_calls(self):
        self._change_first_run(tool_calls="2", missing_incident_id_args="3")
        with self.assertRaisesRegex(ValueError, "Inconsistent tool counts"):
            build_public(self.root)

    def test_bad_provenance_is_rejected(self):
        path = self.root / "provenance.json"
        value = json.loads(path.read_text())
        value["sources"]["cloud-inc001-001"] = "tampered"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Source hash provenance mismatch"):
            build_public(self.root)


if __name__ == "__main__":
    unittest.main()
