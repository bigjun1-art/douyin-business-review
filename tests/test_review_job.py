from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_review_pipeline import config, evidence, MockRunner, metric
import review_job as job
import review_pipeline as rp


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.write("config.json", config())
        self.report = "<title>虚构测试商家复盘</title><p>2025-08-01 至 2025-08-31</p>"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        rp._atomic_json(self.directory / name, value)

    def ready(self):
        self.write("evidence.json", evidence())
        (self.directory / "report.xml").write_text(self.report)
        job.record_review(self.directory, "test-author")

    def test_no_capture_no_external_calls(self):
        with mock.patch.object(rp.LarkRunner, "run", side_effect=AssertionError("external call")):
            self.assertEqual(job.run(self.directory, publish=True)["phase"], "BLOCKED_BACKGROUND_CAPTURE")

    def test_stages_and_explicit_publish(self):
        self.write("evidence.json", evidence())
        self.assertEqual(job.run(self.directory)["phase"], "AWAITING_REPORT")
        (self.directory / "report.xml").write_text(self.report)
        self.assertEqual(job.run(self.directory)["phase"], "AWAITING_SEMANTIC_REVIEW")
        job.record_review(self.directory, "test-author")
        runner = MockRunner(self.report)
        self.assertEqual(job.run(self.directory, runner=runner)["phase"], "READY_TO_PUBLISH")
        self.assertEqual(runner.commands, [])
        self.assertEqual(job.run(self.directory, publish=True, runner=runner)["phase"], "VERIFIED")
        retry = MockRunner(self.report)
        job.run(self.directory, publish=True, runner=retry)
        self.assertFalse(any(c[1:3] == ["docs", "+create"] for c in retry.commands))

    def test_changed_evidence_or_report_invalidates_review(self):
        self.ready()
        data = evidence()
        metric(data, "gmv")["value"] = 11000
        metric(data, "gmv")["raw_value"] = "11000元"
        self.write("evidence.json", data)
        self.assertEqual(job.run(self.directory, publish=True)["phase"], "AWAITING_SEMANTIC_REVIEW")
        job.record_review(self.directory, "test-author")
        (self.directory / "report.xml").write_text(self.report + "<p>修改正文</p>")
        self.assertEqual(job.run(self.directory)["phase"], "AWAITING_SEMANTIC_REVIEW")

    def test_warning_and_period_mismatch_block(self):
        for change in ("warning", "period"):
            data = evidence()
            if change == "warning":
                data["video_groups"]["complete"] = False
            else:
                metric(data, "live_duration_seconds")["period_start"] = "2025-08-25"
            self.write("evidence.json", data)
            self.assertEqual(job.run(self.directory, publish=True)["phase"], "BLOCKED_EVIDENCE")

    def test_update_target_never_creates(self):
        cfg = config()
        cfg["delivery"]["document_id"] = "existing-test-doc"
        self.write("config.json", cfg)
        self.assertEqual(job.run(self.directory, publish=True)["phase"], "BLOCKED_UPDATE_REQUIRES_BOUNDED_EDIT")

    def test_legacy_publish_also_blocks_existing_target(self):
        self.ready()
        for target in ({"mode": "update"}, {"document_id": "existing-test-doc"}):
            cfg = config()
            cfg["delivery"].update(target)
            runner = MockRunner(self.report)
            with self.assertRaises(rp.PipelineError) as caught:
                rp.publish_review(cfg, evidence(), report_path=self.directory / "report.xml",
                                  state_path=self.directory / "delivery-state.json", runner=runner)
            self.assertEqual(caught.exception.code, "BLOCKED_UPDATE_REQUIRES_BOUNDED_EDIT")
            self.assertEqual(runner.commands, [])

    def test_unknown_create_outcome_never_recreates(self):
        self.ready()
        with self.assertRaises(rp.PipelineError):
            job.run(self.directory, publish=True, runner=MockRunner(self.report, timeout_create=True))
        self.assertEqual(rp._load_json(self.directory / "job-state.json")["phase"], "DELIVERY_REQUIRES_ATTENTION")
        retry = MockRunner(self.report)
        with self.assertRaises(rp.PipelineError):
            job.run(self.directory, publish=True, runner=retry)
        self.assertFalse(any(c[1:3] == ["docs", "+create"] for c in retry.commands))

    def test_lock_and_status_are_local(self):
        self.ready()
        with rp._StateLock(self.directory / "job-state.json"):
            with self.assertRaises(rp.PipelineError):
                job.run(self.directory)
        job.run(self.directory)
        with mock.patch.object(rp.LarkRunner, "run", side_effect=AssertionError("external call")):
            with mock.patch("builtins.print") as output:
                self.assertEqual(job.main(["status", "--job", str(self.directory)]), 0)
                self.assertFalse(json.loads(output.call_args.args[0])["live_verified"])


if __name__ == "__main__":
    unittest.main()
