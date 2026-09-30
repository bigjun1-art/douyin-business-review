from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_pipeline as rp  # noqa: E402


def config(start: str = "2025-08-01", end: str = "2025-08-31") -> dict:
    return {
        "schema_version": 2,
        "company": "虚构测试商家",
        "period": {"start": start, "end": end},
        "source": {"platform": "douyin_life_business", "browser_profile": "测试资料"},
        "execution": {"mode": "background_only", "allow_foreground": False},
        "delivery": {"profile": "test-profile", "identity": "user", "folder_token": "folder-1"},
        "required_metrics": list(rp.REQUIRED_METRICS),
    }


def evidence(start: str = "2025-08-01", end: str = "2025-08-31") -> dict:
    scopes = {
        "gmv": "全部门店", "redemption": "全部门店", "refund": "全部门店",
        "live_session_count": "全部账号；全部门店", "live_duration_seconds": "全部账号；全部门店",
        "live_viewers": "全部账号；全部门店", "live_gmv": "全部账号；全部门店",
        "live_coupon_count": "全部账号；全部门店", "video_count_total": "全部身份；全部锚点",
        "influencer_video_count": "全部身份；全部锚点", "video_play_count": "全部身份；全部锚点",
        "video_direct_gmv": "全部身份；全部锚点",
    }
    values = {
        "gmv": 10000, "redemption": 4000, "refund": 1500, "live_session_count": 10,
        "live_duration_seconds": 36000, "live_viewers": 1000, "live_gmv": 6000,
        "live_coupon_count": 100, "video_count_total": 20, "influencer_video_count": 5,
        "video_play_count": 10000, "video_direct_gmv": 1000,
    }
    units = {
        "gmv": "元", "redemption": "元", "refund": "元", "live_session_count": "场",
        "live_duration_seconds": "秒", "live_viewers": "人", "live_gmv": "元",
        "live_coupon_count": "张", "video_count_total": "条", "influencer_video_count": "条",
        "video_play_count": "次", "video_direct_gmv": "元",
    }
    metrics = []
    for name in rp.REQUIRED_METRICS:
        module = "经营总览" if name in rp.MODULE_GROUPS["business"] else ("直播分析" if name in rp.MODULE_GROUPS["live"] else "视频分析")
        metrics.append({
            "metric": name, "value": values[name], "raw_value": f"{values[name]}{units[name]}",
            "period_start": start, "period_end": end, "scope": scopes[name],
            "source_module": module, "source_label": name, "source_ref": f"synthetic://{name}",
        })
    return {
        "schema_version": 2, "company": "虚构测试商家", "period": {"start": start, "end": end},
        "captured_at": "2025-09-01T10:00:00+08:00",
        "capture": {"method": "platform_export", "foreground_used": False, "identity_verified": True, "identity_evidence": "虚构导出主体"},
        "metrics": metrics,
        "live_accounts": {"complete": True, "rows": [
            {"id": "001", "name": "虚构账号甲", "live_duration_seconds": 36000, "live_gmv": 6000,
             "live_coupon_count": 100, "live_session_count": 10, "period_start": start, "period_end": end,
             "scope": "全部账号；全部门店"},
        ]},
        "video_groups": {"complete": True, "mutually_exclusive": True, "rows": [
            {"name": "商家", "identity_type": "merchant", "new_video_count": 15, "video_play_count": 4000,
             "video_direct_gmv": 700, "period_start": start, "period_end": end, "scope": "全部身份；全部锚点"},
            {"name": "达人", "identity_type": "influencer", "new_video_count": 5, "video_play_count": 6000,
             "video_direct_gmv": 300, "period_start": start, "period_end": end, "scope": "全部身份；全部锚点"},
        ]},
        "influencer_in_total": True,
    }


def metric(data: dict, name: str) -> dict:
    return next(item for item in data["metrics"] if item["metric"] == name)


class MockRunner:
    def __init__(self, report: str, *, auth_valid: bool = True, fetch: str | None = None,
                 folder_ok: bool = True, security_stop: bool = False, timeout_create: bool = False,
                 assessment: str = "passed", fetch_id: str | None = "doc-1",
                 create_warnings: list | None = None, omit_create_id: bool = False):
        self.report = report
        self.auth_valid = auth_valid
        self.fetch = fetch if fetch is not None else report
        self.folder_ok = folder_ok
        self.security_stop = security_stop
        self.timeout_create = timeout_create
        self.assessment = assessment
        self.fetch_id = fetch_id
        self.create_warnings = create_warnings or []
        self.omit_create_id = omit_create_id
        self.commands: list[list[str]] = []

    def run(self, argv, timeout=30):
        argv = list(argv)
        self.commands.append(argv)
        if self.security_stop:
            return rp.RunResult(1, "", "error 20064")
        if argv[1:3] == ["auth", "status"]:
            return rp.RunResult(0, json.dumps({"identities": {"user": {"verified": self.auth_valid, "tokenStatus": "valid" if self.auth_valid else "invalid"}}}), "")
        if argv[1:3] == ["docs", "+script"]:
            return rp.RunResult(0, json.dumps({"status": "success", "data": {"assessment": {"status": self.assessment}}}), "")
        if argv[1:3] == ["docs", "+create"]:
            if self.timeout_create:
                raise rp.PipelineError("COMMAND_TIMEOUT", "timeout")
            document = {} if self.omit_create_id else {"document_id": "doc-1"}
            return rp.RunResult(0, json.dumps({"data": {"document": document, "warnings": self.create_warnings}}), "")
        if argv[1:3] == ["docs", "+fetch"]:
            document = {"content": self.fetch}
            if self.fetch_id is not None:
                document["document_id"] = self.fetch_id
            return rp.RunResult(0, json.dumps({"data": {"document": document}}), "")
        if argv[1:4] == ["drive", "files", "list"]:
            files = [{"token": "doc-1", "parent_token": "folder-1"}] if self.folder_ok else []
            return rp.RunResult(0, json.dumps({"data": {"files": files, "has_more": False}}), "")
        raise AssertionError(argv)


class ValidationTests(unittest.TestCase):
    def test_valid_and_mixed_month_seven_days(self):
        self.assertEqual(rp.validate_review(config(), evidence())["status"], "pass")
        cfg = config("2025-01-29", "2025-02-04")
        data = evidence("2025-01-29", "2025-02-04")
        checked = rp.validate_review(cfg, data)
        self.assertFalse(checked["failures"])
        analysis = rp.build_analysis(cfg, data)
        self.assertFalse(analysis["period_is_full_natural_month"])
        self.assertIn("target period is not a complete natural month", analysis["limitations"])

    def test_scope_missing_value_and_addition_errors(self):
        data = evidence()
        metric(data, "live_gmv")["scope"] = "单个账号"
        self.assertTrue(any("same scope" in item for item in rp.validate_review(config(), data)["failures"]))
        data = evidence()
        del metric(data, "gmv")["value"]
        self.assertTrue(rp.validate_review(config(), data)["failures"])
        data = evidence()
        data["live_accounts"]["rows"][0]["live_gmv"] = 5900
        self.assertTrue(any("live_gmv total mismatch" in item for item in rp.validate_review(config(), data)["failures"]))
        data = evidence()
        metric(data, "video_play_count")["period_start"] = "2025-08-25"
        self.assertTrue(any("video_play_count period" in item for item in rp.validate_review(config(), data)["failures"]))
        data = evidence()
        data["video_groups"]["rows"][0]["video_play_count"] -= 1
        self.assertTrue(any("video_play_count total mismatch" in item for item in rp.validate_review(config(), data)["failures"]))

    def test_numeric_rejections_and_duplicate(self):
        for bad in (0.5, float("nan"), -1, True):
            with self.subTest(bad=bad):
                data = evidence()
                metric(data, "video_count_total")["value"] = bad
                self.assertTrue(rp.validate_review(config(), data)["failures"])
        data = evidence()
        data["metrics"].append(copy.deepcopy(data["metrics"][0]))
        self.assertTrue(any("duplicate metric" in item for item in rp.validate_review(config(), data)["failures"]))

    def test_incomplete_groups_warning_blocks_publish(self):
        data = evidence()
        data["video_groups"]["complete"] = False
        checked = rp.validate_review(config(), data)
        self.assertEqual(checked["status"], "warning")
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "r.xml"
            report.write_text("<title>虚构测试商家复盘</title><p>2025-08-01 至 2025-08-31</p>", encoding="utf-8")
            with self.assertRaisesRegex(rp.PipelineError, "warnings"):
                rp.publish_review(config(), data, report_path=report, state_path=Path(directory) / "s.json", runner=MockRunner(report.read_text()))

    def test_foreground_and_identity_type_and_influencer_sum(self):
        data = evidence()
        data["capture"]["foreground_used"] = True
        result = rp.validate_review(config(), data)
        self.assertEqual(result["error_code"], "BLOCKED_BACKGROUND_CAPTURE")
        data = evidence()
        del data["video_groups"]["rows"][0]["identity_type"]
        self.assertTrue(any("identity_type" in item for item in rp.validate_review(config(), data)["failures"]))
        data = evidence()
        data["video_groups"]["rows"][1]["new_video_count"] = 4
        data["video_groups"]["rows"][0]["new_video_count"] = 16
        self.assertTrue(any("influencer_video_count total mismatch" in item for item in rp.validate_review(config(), data)["failures"]))

    def test_zero_denominator_and_no_invalid_averages(self):
        data = evidence()
        previous = copy.deepcopy(data["metrics"])
        metric({"metrics": previous}, "gmv")["value"] = 0
        for row in previous:
            row["period_start"] = "2025-07-01"
            row["period_end"] = "2025-07-31"
            row["source_ref"] = "synthetic://previous/" + row["metric"]
        data["comparison"] = {"period": {"start": "2025-07-01", "end": "2025-07-31"}, "comparison_mode": "full_month", "metrics": previous}
        analysis = rp.build_analysis(config(), data)
        self.assertIsNone(analysis["derived"]["comparison.gmv"]["percentage_change"])
        self.assertNotIn("average", " ".join(analysis["derived"]))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.report_text = "<title>虚构测试商家复盘</title><p>统计期 2025-08-01 至 2025-08-31</p><p>普通业务编号 20064 与 20073</p>"
        self.report = self.directory / "report.xml"
        self.report.write_text(self.report_text, encoding="utf-8")
        self.state = self.directory / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_normal_publish_and_verified_retry_does_not_create(self):
        runner = MockRunner(self.report_text)
        result = rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=runner)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(json.loads(self.state.read_text())["phase"], "verified")
        self.assertTrue(any(command[1:3] == ["docs", "+create"] for command in runner.commands))
        retry = MockRunner(self.report_text)
        rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=retry)
        self.assertFalse(any(command[1:3] == ["docs", "+create"] for command in retry.commands))

    def test_timeout_uncertain_and_no_automatic_retry(self):
        runner = MockRunner(self.report_text, timeout_create=True)
        with self.assertRaisesRegex(rp.PipelineError, "uncertain"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=runner)
        self.assertEqual(json.loads(self.state.read_text())["phase"], "uncertain")
        retry = MockRunner(self.report_text)
        with self.assertRaisesRegex(rp.PipelineError, "recover"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=retry)
        self.assertEqual(retry.commands, [])

    def test_fingerprint_folder_body_and_lock_failures(self):
        rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text))
        changed = evidence()
        metric(changed, "gmv")["value"] = 9999
        with self.assertRaisesRegex(rp.PipelineError, "different company"):
            rp.publish_review(config(), changed, report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text))
        self.state.unlink()
        with self.assertRaisesRegex(rp.PipelineError, "configured folder"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text, folder_ok=False))
        self.state.unlink()
        with self.assertRaisesRegex(rp.PipelineError, "does not fully match"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text, fetch="<p>wrong</p>"))
        Path(str(self.state) + ".lock").write_text("busy", encoding="utf-8")
        with self.assertRaisesRegex(rp.PipelineError, "another publish"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text))

    def test_auth_security_and_arbitrary_command_stop(self):
        with self.assertRaisesRegex(rp.PipelineError, "verified user"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text, auth_valid=False))
        with self.assertRaisesRegex(rp.PipelineError, "security"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text, security_stop=True))
        with self.assertRaisesRegex(rp.PipelineError, "only lark-cli"):
            rp.LarkRunner().run(["sh", "-c", "echo forbidden"])

    def test_strict_parse_fetch_and_create_warning_gates(self):
        with self.assertRaisesRegex(rp.PipelineError, "assessment"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text, assessment="failed"))
        self.assertFalse(self.state.exists())
        with self.assertRaisesRegex(rp.PipelineError, "document id"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text, fetch_id=None))
        self.state.unlink()
        warning_runner = MockRunner(self.report_text, create_warnings=["synthetic warning"])
        with self.assertRaisesRegex(rp.PipelineError, "warnings"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=warning_runner)
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["phase"], "create_warning")
        self.assertEqual(saved["document_id"], "doc-1")
        retry = MockRunner(self.report_text)
        with self.assertRaisesRegex(rp.PipelineError, "warnings"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=retry)
        self.assertEqual(retry.commands, [])

    def test_unknown_create_recovery_and_config_fingerprint(self):
        with self.assertRaisesRegex(rp.PipelineError, "uncertain"):
            rp.publish_review(config(), evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text, omit_create_id=True))
        recovery = MockRunner(self.report_text)
        result = rp.publish_review(
            config(), evidence(), report_path=self.report, state_path=self.state,
            document_id="doc-1", runner=recovery,
        )
        self.assertEqual(result["status"], "verified")
        self.assertFalse(any(command[1:3] == ["docs", "+create"] for command in recovery.commands))
        changed_config = config()
        changed_config["delivery"]["folder_token"] = "folder-2"
        with self.assertRaisesRegex(rp.PipelineError, "different company"):
            rp.publish_review(changed_config, evidence(), report_path=self.report, state_path=self.state, runner=MockRunner(self.report_text))

    def test_credential_lock_conflict(self):
        with mock.patch.object(rp.tempfile, "gettempdir", return_value=str(self.directory)):
            lock = rp._CredentialLock()
            lock.path.write_text("busy", encoding="utf-8")
            with self.assertRaisesRegex(rp.PipelineError, "credentials"):
                lock.__enter__()


if __name__ == "__main__":
    unittest.main()
