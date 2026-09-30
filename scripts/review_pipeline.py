#!/usr/bin/env python3
"""Deterministic, background-only review evidence pipeline.

This module validates evidence produced by an authorized background connector or
by a platform export.  It deliberately contains no browser automation or data
collection implementation.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 2
CAPTURE_METHODS = {"authorized_background_connector", "platform_export"}
REQUIRED_METRICS = [
    "gmv",
    "redemption",
    "refund",
    "live_session_count",
    "live_duration_seconds",
    "live_viewers",
    "live_gmv",
    "live_coupon_count",
    "video_count_total",
    "influencer_video_count",
    "video_play_count",
    "video_direct_gmv",
]
INTEGER_METRICS = {
    "live_session_count",
    "live_viewers",
    "live_coupon_count",
    "video_count_total",
    "influencer_video_count",
    "video_play_count",
}
MODULE_GROUPS = {
    "business": {"gmv", "redemption", "refund"},
    "live": {
        "live_session_count",
        "live_duration_seconds",
        "live_viewers",
        "live_gmv",
        "live_coupon_count",
    },
    "video": {
        "video_count_total",
        "influencer_video_count",
        "video_play_count",
        "video_direct_gmv",
    },
}
SECURITY_STOP_MARKERS = ("operation not permitted", "20064", "20073")
MAX_PAGES = 5
DEFAULT_TIMEOUT = 30


class PipelineError(RuntimeError):
    """A safe, user-facing pipeline failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError("FILE_NOT_FOUND", f"file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError("INVALID_JSON", f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PipelineError("INVALID_JSON", f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _date(value: Any, label: str, failures: list[str]) -> dt.date | None:
    if not isinstance(value, str):
        failures.append(f"{label} must be YYYY-MM-DD")
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        failures.append(f"{label} must be YYYY-MM-DD")
        return None


def _period(value: Any, label: str, failures: list[str]) -> tuple[dt.date | None, dt.date | None]:
    if not isinstance(value, dict):
        failures.append(f"{label} must be an object")
        return None, None
    start = _date(value.get("start"), f"{label}.start", failures)
    end = _date(value.get("end"), f"{label}.end", failures)
    if start and end and end < start:
        failures.append(f"{label}.end must be on or after start")
    return start, end


def _number(value: Any, label: str, failures: list[str], *, integer: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        failures.append(f"{label} must be a number, not bool")
        return None
    converted = float(value)
    if not math.isfinite(converted):
        failures.append(f"{label} must be finite")
        return None
    if converted < 0:
        failures.append(f"{label} must not be negative")
        return None
    if integer and not converted.is_integer():
        failures.append(f"{label} must be an integer")
        return None
    return converted


def _nonempty_string(value: Any, label: str, failures: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        failures.append(f"{label} must be a non-empty string")
        return ""
    return value.strip()


def validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if config.get("schema_version") != SCHEMA_VERSION:
        failures.append("config.schema_version must be 2")
    company = _nonempty_string(config.get("company"), "config.company", failures)
    start, end = _period(config.get("period"), "config.period", failures)
    execution = config.get("execution")
    if not isinstance(execution, dict):
        failures.append("config.execution must be an object")
    else:
        if execution.get("mode") != "background_only":
            failures.append("config.execution.mode must be background_only")
        if execution.get("allow_foreground") is not False:
            failures.append("config.execution.allow_foreground must be false")
    delivery = config.get("delivery")
    if not isinstance(delivery, dict):
        failures.append("config.delivery must be an object")
    else:
        _nonempty_string(delivery.get("profile"), "config.delivery.profile", failures)
        if delivery.get("identity") != "user":
            failures.append("config.delivery.identity must be user")
        _nonempty_string(delivery.get("folder_token"), "config.delivery.folder_token", failures)
    source = config.get("source")
    if not isinstance(source, dict) or source.get("platform") != "douyin_life_business":
        failures.append("config.source.platform must be douyin_life_business")
    elif not isinstance(source.get("browser_profile", ""), str):
        failures.append("config.source.browser_profile must be a string")
    required = config.get("required_metrics")
    if not isinstance(required, list) or not required:
        failures.append("config.required_metrics must be a non-empty list")
    elif any(not isinstance(item, str) or not item.strip() for item in required):
        failures.append("config.required_metrics must contain non-empty strings")
    elif len(required) != len(set(required)):
        failures.append("config.required_metrics must not contain duplicates")
    return {
        "status": "fail" if failures else "pass",
        "failures": failures,
        "company": company,
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
    }


def _validate_metric_rows(
    rows: Any,
    *,
    label: str,
    expected_period: Mapping[str, Any],
    failures: list[str],
) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list) or not rows:
        failures.append(f"{label} must be a non-empty list")
        return by_name
    expected_start = expected_period.get("start")
    expected_end = expected_period.get("end")
    for index, row in enumerate(rows):
        prefix = f"{label}[{index}]"
        if not isinstance(row, dict):
            failures.append(f"{prefix} must be an object")
            continue
        name = _nonempty_string(row.get("metric"), f"{prefix}.metric", failures)
        if not name:
            continue
        if name in by_name:
            failures.append(f"duplicate metric: {name}")
            continue
        by_name[name] = row
        _number(row.get("value"), f"{prefix}.value", failures, integer=name in INTEGER_METRICS)
        if "raw_value" not in row or row.get("raw_value") is None or row.get("raw_value") == "":
            failures.append(f"{prefix}.raw_value is required")
        for field in ("scope", "source_module", "source_label", "source_ref"):
            _nonempty_string(row.get(field), f"{prefix}.{field}", failures)
        row_start = _date(row.get("period_start"), f"{prefix}.period_start", failures)
        row_end = _date(row.get("period_end"), f"{prefix}.period_end", failures)
        if row_start and row_end and row_end < row_start:
            failures.append(f"{prefix}.period_end must be on or after period_start")
        if row.get("period_start") != expected_start or row.get("period_end") != expected_end:
            failures.append(f"{name} period must match {label} period")
        lowered = name.lower()
        if lowered in {"net_income", "net_revenue"} or (
            "net" in lowered and "refund" in str(row.get("source_label", "")).lower()
        ):
            failures.append(f"{name} must not describe revenue minus refund as net income")
    return by_name


def _scope_consistency(by_name: Mapping[str, Mapping[str, Any]], failures: list[str]) -> None:
    for module_name, names in MODULE_GROUPS.items():
        scopes = {str(by_name[name].get("scope", "")).strip() for name in names if name in by_name}
        if len(scopes) > 1:
            failures.append(f"{module_name} core metrics must use the same scope")


def _reconcile(
    detail: float,
    overview: float,
    *,
    label: str,
    kind: str,
    failures: list[str],
    warnings: list[str],
) -> None:
    delta = abs(detail - overview)
    if kind == "integer":
        if delta != 0:
            failures.append(f"{label} total mismatch: detail={detail:g}, overview={overview:g}")
    elif kind == "money":
        if delta > 0.01:
            failures.append(f"{label} total mismatch: detail={detail:g}, overview={overview:g}")
    elif kind == "duration":
        if delta > 60:
            failures.append(f"{label} total mismatch exceeds 60 seconds: detail={detail:g}, overview={overview:g}")
        elif delta > 0:
            warnings.append(f"{label} differs by {delta:g} seconds within the 60-second tolerance")


def _validate_live_accounts(
    value: Any,
    by_name: Mapping[str, Mapping[str, Any]],
    expected_period: Mapping[str, Any],
    failures: list[str],
    warnings: list[str],
) -> None:
    if not isinstance(value, dict):
        warnings.append("live_accounts detail is missing; additive totals cannot be reconciled")
        return
    if value.get("complete") is not True:
        warnings.append("live_accounts.complete is not true; additive totals cannot be reconciled")
        return
    rows = value.get("rows")
    if not isinstance(rows, list):
        failures.append("live_accounts.rows must be a list when complete is true")
        return
    parent_scope = str(by_name.get("live_duration_seconds", {}).get("scope", ""))
    seen_ids: set[str] = set()
    sums = {"live_duration_seconds": 0.0, "live_gmv": 0.0, "live_coupon_count": 0.0}
    session_sum = 0.0
    all_sessions = True
    for index, row in enumerate(rows):
        prefix = f"live_accounts.rows[{index}]"
        if not isinstance(row, dict):
            failures.append(f"{prefix} must be an object")
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            failures.append(f"{prefix}.id must be a non-empty string")
        elif identifier in seen_ids:
            failures.append(f"duplicate live account id: {identifier}")
        else:
            seen_ids.add(identifier)
        _nonempty_string(row.get("name"), f"{prefix}.name", failures)
        if row.get("period_start") != expected_period.get("start") or row.get("period_end") != expected_period.get("end"):
            failures.append(f"{prefix} period must match config period")
        if row.get("scope") != parent_scope:
            failures.append(f"{prefix}.scope must match live metric scope")
        for field in sums:
            number = _number(row.get(field), f"{prefix}.{field}", failures, integer=field == "live_coupon_count")
            if number is not None:
                sums[field] += number
        if "live_session_count" in row:
            number = _number(row.get("live_session_count"), f"{prefix}.live_session_count", failures, integer=True)
            if number is not None:
                session_sum += number
        else:
            all_sessions = False
    comparisons = (
        ("live_duration_seconds", "duration"),
        ("live_gmv", "money"),
        ("live_coupon_count", "integer"),
    )
    for metric, kind in comparisons:
        if metric in by_name and _is_valid_number(by_name[metric].get("value")):
            _reconcile(sums[metric], float(by_name[metric]["value"]), label=metric, kind=kind, failures=failures, warnings=warnings)
    if all_sessions and "live_session_count" in by_name and _is_valid_number(by_name["live_session_count"].get("value")):
        _reconcile(session_sum, float(by_name["live_session_count"]["value"]), label="live_session_count", kind="integer", failures=failures, warnings=warnings)
    elif "live_session_count" in by_name:
        warnings.append("some live account rows omit live_session_count; session totals cannot be reconciled")


def _validate_video_groups(
    value: Any,
    by_name: Mapping[str, Mapping[str, Any]],
    expected_period: Mapping[str, Any],
    failures: list[str],
    warnings: list[str],
) -> None:
    if not isinstance(value, dict):
        warnings.append("video_groups detail is missing; additive totals cannot be reconciled")
        return
    if value.get("complete") is not True:
        warnings.append("video_groups.complete is not true; additive totals cannot be reconciled")
        return
    if value.get("mutually_exclusive") is not True:
        failures.append("video_groups.mutually_exclusive must be true when complete is true")
    rows = value.get("rows")
    if not isinstance(rows, list):
        failures.append("video_groups.rows must be a list when complete is true")
        return
    parent_scope = str(by_name.get("video_count_total", {}).get("scope", ""))
    sums = {"new_video_count": 0.0, "video_play_count": 0.0, "video_direct_gmv": 0.0}
    influencer_count = 0.0
    seen_names: set[str] = set()
    for index, row in enumerate(rows):
        prefix = f"video_groups.rows[{index}]"
        if not isinstance(row, dict):
            failures.append(f"{prefix} must be an object")
            continue
        name = _nonempty_string(row.get("name"), f"{prefix}.name", failures)
        if name in seen_names:
            failures.append(f"duplicate video group name: {name}")
        seen_names.add(name)
        identity_type = row.get("identity_type")
        if identity_type not in {"merchant", "influencer", "staff", "ugc", "other"}:
            failures.append(f"{prefix}.identity_type must be merchant, influencer, staff, ugc, or other")
        if row.get("period_start") != expected_period.get("start") or row.get("period_end") != expected_period.get("end"):
            failures.append(f"{prefix} period must match config period")
        if row.get("scope") != parent_scope:
            failures.append(f"{prefix}.scope must match video metric scope")
        for field in sums:
            number = _number(row.get(field), f"{prefix}.{field}", failures, integer=field != "video_direct_gmv")
            if number is not None:
                sums[field] += number
                if field == "new_video_count" and identity_type == "influencer":
                    influencer_count += number
    mapping = (
        ("new_video_count", "video_count_total", "integer"),
        ("video_play_count", "video_play_count", "integer"),
        ("video_direct_gmv", "video_direct_gmv", "money"),
    )
    for detail_name, overview_name, kind in mapping:
        if overview_name in by_name and _is_valid_number(by_name[overview_name].get("value")):
            _reconcile(sums[detail_name], float(by_name[overview_name]["value"]), label=overview_name, kind=kind, failures=failures, warnings=warnings)
    if "influencer_video_count" in by_name and _is_valid_number(by_name["influencer_video_count"].get("value")):
        _reconcile(
            influencer_count, float(by_name["influencer_video_count"]["value"]),
            label="influencer_video_count", kind="integer", failures=failures, warnings=warnings,
        )


def _is_valid_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _is_full_month(start: dt.date, end: dt.date) -> bool:
    if start.day != 1:
        return False
    next_month = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return end == next_month - dt.timedelta(days=1)


def _captured_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _validate_comparison(
    comparison: Any,
    current: Mapping[str, Mapping[str, Any]],
    target_period: tuple[dt.date | None, dt.date | None],
    failures: list[str],
    limitations: list[str],
) -> dict[str, dict[str, Any]]:
    if comparison is None:
        limitations.append("comparison evidence is absent; no period-over-period claim was calculated")
        return {}
    if not isinstance(comparison, dict):
        failures.append("comparison must be an object")
        return {}
    comp_period = comparison.get("period")
    comp_start, comp_end = _period(comp_period, "comparison.period", failures)
    mode = comparison.get("comparison_mode")
    if mode not in {"full_month", "same_length"}:
        failures.append("comparison.comparison_mode must be full_month or same_length")
    elif comp_start and comp_end:
        if mode == "full_month" and not _is_full_month(comp_start, comp_end):
            failures.append("comparison period must be a complete natural month for full_month mode")
        if mode == "full_month" and all(target_period) and not _is_full_month(target_period[0], target_period[1]):  # type: ignore[arg-type]
            failures.append("current period must be a complete natural month for full_month mode")
        if mode == "same_length" and all(target_period):
            target_days = (target_period[1] - target_period[0]).days + 1  # type: ignore[operator]
            comp_days = (comp_end - comp_start).days + 1
            if target_days != comp_days:
                failures.append("comparison period must have the same inclusive day count")
    comp_rows = _validate_metric_rows(
        comparison.get("metrics"), label="comparison.metrics", expected_period=comp_period if isinstance(comp_period, dict) else {}, failures=failures
    )
    for name, row in comp_rows.items():
        if name not in current:
            failures.append(f"comparison metric has no matching current metric: {name}")
            continue
        for field in ("scope", "source_module", "source_label"):
            if row.get(field) != current[name].get(field):
                failures.append(f"comparison metric {name} must match current {field}")
        current_unit = _unit_hint(current[name].get("raw_value"))
        comparison_unit = _unit_hint(row.get("raw_value"))
        if current_unit != comparison_unit:
            failures.append(f"comparison metric {name} must use the same unit")
    return comp_rows


def _unit_hint(raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        return ""
    compact = raw_value.replace(",", "").replace(" ", "")
    return re.sub(r"[-+]?\d+(?:\.\d+)?", "", compact)


def validate_review(config: Mapping[str, Any], data: Mapping[str, Any]) -> dict[str, Any]:
    config_result = validate_config(config)
    failures = list(config_result["failures"])
    warnings: list[str] = []
    limitations: list[str] = []
    blocked = False
    if data.get("schema_version") != SCHEMA_VERSION:
        failures.append("data.schema_version must be 2")
    if data.get("company") != config.get("company"):
        failures.append("data.company must exactly match config.company")
    data_period = data.get("period")
    data_start, data_end = _period(data_period, "data.period", failures)
    if data_period != config.get("period"):
        failures.append("data.period must exactly match config.period")
    captured_at = data.get("captured_at")
    captured = _captured_datetime(captured_at)
    if captured is None:
        failures.append("data.captured_at must be an ISO datetime with timezone")
    capture = data.get("capture")
    if not isinstance(capture, dict):
        failures.append("data.capture must be an object")
        blocked = True
    else:
        if capture.get("method") not in CAPTURE_METHODS:
            failures.append("capture.method must be authorized_background_connector or platform_export")
            blocked = True
        if capture.get("foreground_used") is not False:
            failures.append("capture.foreground_used must be false")
            blocked = True
        if capture.get("identity_verified") is not True:
            failures.append("capture.identity_verified must be true")
            blocked = True
        _nonempty_string(capture.get("identity_evidence"), "capture.identity_evidence", failures)
    config_period = config.get("period") if isinstance(config.get("period"), dict) else {}
    by_name = _validate_metric_rows(
        data.get("metrics"), label="metrics", expected_period=config_period, failures=failures
    )
    configured_required = config.get("required_metrics")
    required = (
        [name for name in configured_required if isinstance(name, str)]
        if isinstance(configured_required, list)
        else REQUIRED_METRICS
    )
    missing = [name for name in required if name not in by_name]
    if missing:
        failures.append("missing required metrics: " + ", ".join(missing))
    _scope_consistency(by_name, failures)
    total = by_name.get("video_count_total")
    influencer = by_name.get("influencer_video_count")
    if data.get("influencer_in_total") is not True:
        failures.append("influencer_in_total must be true; total and influencer counts must never be added")
    if total and influencer and _is_valid_number(total.get("value")) and _is_valid_number(influencer.get("value")):
        if float(influencer["value"]) > float(total["value"]):
            failures.append("influencer_video_count must not exceed video_count_total")
    _validate_live_accounts(data.get("live_accounts"), by_name, config_period, failures, warnings)
    _validate_video_groups(data.get("video_groups"), by_name, config_period, failures, warnings)
    comparison = _validate_comparison(data.get("comparison"), by_name, (data_start, data_end), failures, limitations)
    if data_end and captured and captured.date() <= data_end:
        limitations.append("target period had not ended when evidence was captured; completeness is not assumed")
        if isinstance(data.get("comparison"), dict) and data["comparison"].get("comparison_mode") == "full_month":
            failures.append("current period was not complete at captured_at for full_month comparison")
    status = "fail" if failures else ("warning" if warnings else "pass")
    result: dict[str, Any] = {
        "status": status,
        "company": str(config.get("company", "")),
        "period": config.get("period"),
        "failures": failures,
        "warnings": warnings,
        "limitations": limitations,
        "metric_count": len(by_name),
        "comparison_metric_count": len(comparison),
    }
    if blocked:
        result["error_code"] = "BLOCKED_BACKGROUND_CAPTURE"
    return result


def build_analysis(config: Mapping[str, Any], data: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_review(config, data)
    if checked["failures"]:
        raise PipelineError("CHECK_FAILED", "analysis requires evidence with no validation failures")
    metrics = {row["metric"]: row for row in data["metrics"]}
    facts = {
        name: {
            "value": row["value"],
            "raw_value": row["raw_value"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "scope": row["scope"],
            "source_module": row["source_module"],
            "source_label": row["source_label"],
            "source_ref": row["source_ref"],
        }
        for name, row in sorted(metrics.items())
    }
    derived: dict[str, Any] = {}
    if "video_count_total" in metrics and "influencer_video_count" in metrics and metrics["video_count_total"]["value"]:
        derived["influencer_video_share"] = {
            "value": metrics["influencer_video_count"]["value"] / metrics["video_count_total"]["value"],
            "formula": "influencer_video_count / video_count_total",
        }
    elif "video_count_total" in metrics and "influencer_video_count" in metrics:
        checked["limitations"].append("video_count_total is zero; influencer video share is not calculated")
    comparison = data.get("comparison")
    if isinstance(comparison, dict):
        current = metrics
        for previous in comparison.get("metrics", []):
            name = previous["metric"]
            if name not in current:
                continue
            denominator = previous["value"]
            item: dict[str, Any] = {
                "absolute_change": current[name]["value"] - denominator,
                "formula": "current - comparison",
                "comparison_mode": comparison["comparison_mode"],
            }
            if denominator == 0:
                item["percentage_change"] = None
                item["limitation"] = "comparison denominator is zero; percentage change is undefined"
            else:
                item["percentage_change"] = (current[name]["value"] - denominator) / denominator
            derived[f"comparison.{name}"] = item
    sources = sorted(
        {
            (row["source_module"], row["source_label"], row["source_ref"])
            for row in data["metrics"]
        }
    )
    period_start = dt.date.fromisoformat(config["period"]["start"])
    period_end = dt.date.fromisoformat(config["period"]["end"])
    captured = _captured_datetime(data.get("captured_at"))
    period_is_full = _is_full_month(period_start, period_end) and captured is not None and captured.date() > period_end
    if not period_is_full:
        checked["limitations"].append("target period is not a complete natural month")
    return {
        "schema_version": SCHEMA_VERSION,
        "company": config["company"],
        "period": config["period"],
        "period_is_full_natural_month": period_is_full,
        "facts": facts,
        "derived": derived,
        "limitations": checked["limitations"] + checked["warnings"],
        "interpretation_gaps": [
            "The evidence supports measurement and reconciliation, not causal attribution.",
            "live_viewers is non-additive and is not summed across accounts.",
            "video_play_count may include historical works; no per-new-video average is calculated.",
            "refund is reported separately and is not relabeled as net income.",
        ],
        "sources": [
            {"source_module": module, "source_label": label, "source_ref": ref}
            for module, label, ref in sources
        ],
    }


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


class LarkRunner:
    """The only external-process boundary; argv is always a fixed lark-cli list."""

    def run(self, argv: Sequence[str], timeout: int = DEFAULT_TIMEOUT) -> RunResult:
        if not argv or argv[0] != "lark-cli":
            raise PipelineError("UNSAFE_COMMAND", "only lark-cli commands are allowed")
        try:
            result = subprocess.run(
                list(argv),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PipelineError("COMMAND_TIMEOUT", "lark-cli command timed out") from exc
        return RunResult(result.returncode, result.stdout, result.stderr)


def _run_lark(runner: LarkRunner, argv: list[str], *, uncertain_on_timeout: bool = False) -> dict[str, Any]:
    is_auth_status = argv[1:3] == ["auth", "status"]
    if "--as" not in argv and not is_auth_status:
        raise PipelineError("UNSAFE_COMMAND", "lark-cli argv must include --as user")
    try:
        result = runner.run(argv)
    except PipelineError as exc:
        if uncertain_on_timeout and exc.code == "COMMAND_TIMEOUT":
            raise PipelineError("CREATE_RESULT_UNCERTAIN", "create timed out; do not retry automatically") from exc
        raise
    if result.returncode != 0:
        error_output = f"{result.stderr}\n{result.stdout}".lower()
        if any(marker in error_output for marker in SECURITY_STOP_MARKERS):
            raise PipelineError("LARK_SECURITY_STOP", "lark security or permission stop detected")
        raise PipelineError("LARK_COMMAND_FAILED", f"lark-cli command failed with exit code {result.returncode}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError("LARK_INVALID_OUTPUT", "lark-cli returned non-JSON output") from exc
    if not isinstance(value, dict):
        raise PipelineError("LARK_INVALID_OUTPUT", "lark-cli JSON output must be an object")
    error_code = str(value.get("code", ""))
    error_text = " ".join(
        str(value.get(field, "")) for field in ("error", "message", "msg")
    ).lower()
    if error_code in {"20064", "20073"} or "operation not permitted" in error_text:
        raise PipelineError("LARK_SECURITY_STOP", "lark security or permission stop detected")
    if value.get("ok") is False or (isinstance(value.get("code"), int) and value.get("code") != 0):
        raise PipelineError("LARK_COMMAND_FAILED", "lark-cli JSON result reported failure")
    return value


def _parse_xml_fragment(xml_text: str) -> ET.Element:
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml_text, flags=re.IGNORECASE):
        raise ET.ParseError("DOCTYPE and ENTITY declarations are not allowed")
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return ET.fromstring(f"<codex_fragment>{xml_text}</codex_fragment>")


def _visible_text(xml_text: str) -> str:
    root = _parse_xml_fragment(xml_text)
    return " ".join(" ".join(root.itertext()).split())


def _report_title(root: ET.Element) -> str:
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"title", "h1"}:
            return " ".join(" ".join(element.itertext()).split())
    return ""


def validate_report_xml(report_path: Path, config: Mapping[str, Any]) -> tuple[str, str]:
    try:
        raw = report_path.read_text(encoding="utf-8")
        root = _parse_xml_fragment(raw)
    except FileNotFoundError as exc:
        raise PipelineError("REPORT_NOT_FOUND", f"report not found: {report_path}") from exc
    except (OSError, ET.ParseError) as exc:
        raise PipelineError("INVALID_REPORT_XML", "report must be safe, well-formed XML") from exc
    visible = " ".join(" ".join(root.itertext()).split())
    if not visible:
        raise PipelineError("EMPTY_REPORT", "report XML has no human-visible text")
    title = _report_title(root)
    if not title or config["company"] not in title:
        raise PipelineError("INVALID_REPORT_TITLE", "report title must contain company")
    if any(item not in visible for item in (config["period"]["start"], config["period"]["end"])):
        raise PipelineError("INVALID_REPORT_PERIOD", "report body must contain both period dates")
    return raw, visible


class _StateLock:
    def __init__(self, state_path: Path):
        self.path = state_path.with_name(state_path.name + ".lock")
        self.fd: int | None = None

    def __enter__(self) -> "_StateLock":
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PipelineError("STATE_LOCKED", "another publish process owns this state") from exc
        os.write(self.fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class _CredentialLock:
    """Serialize this script's real user-credential CLI access on one host."""

    def __init__(self) -> None:
        self.path = Path(tempfile.gettempdir()) / f"douyin-business-review-lark-{os.getuid()}.lock"
        self.fd: int | None = None

    def __enter__(self) -> "_CredentialLock":
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PipelineError("LARK_CREDENTIALS_LOCKED", "another review publish is using Lark user credentials") from exc
        os.write(self.fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _fingerprints(config: Mapping[str, Any], data: Mapping[str, Any], report_bytes: bytes) -> tuple[str, str]:
    content_hash = hashlib.sha256(report_bytes).hexdigest()
    payload = {
        "config": config,
        "data": data,
        "content_hash": content_hash,
    }
    job_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return content_hash, job_hash


def _state_for(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _load_json(path)


def _state_write(path: Path, *, phase: str, content_hash: str, job_hash: str, document_id: str = "", resource_folder_id: str = "") -> None:
    _atomic_json(
        path,
        {
            "phase": phase,
            "content_hash": content_hash,
            "job_hash": job_hash,
            "document_id": document_id,
            "resource_folder_id": resource_folder_id,
        },
    )


def _auth_verified(value: Mapping[str, Any]) -> bool:
    identities = value.get("identities")
    if not isinstance(identities, dict):
        data = value.get("data")
        if isinstance(data, dict):
            identities = data.get("identities")
    if isinstance(identities, dict) and isinstance(identities.get("user"), dict):
        user = identities["user"]
        return user.get("verified") is True and user.get("tokenStatus", user.get("token_status")) == "valid"
    return False


def _extract_fetch_text(value: Mapping[str, Any], target_id: str) -> str:
    data = value.get("data")
    document = data.get("document") if isinstance(data, dict) else None
    if not isinstance(document, dict):
        raise PipelineError("READBACK_MISSING", "fetch response has no data.document object")
    fetched_id = document.get("document_id")
    if str(fetched_id) != target_id:
        raise PipelineError("READBACK_ID_MISMATCH", "fetch response document id does not match target")
    candidate = document.get("content")
    if not isinstance(candidate, str):
        raise PipelineError("READBACK_MISSING", "fetch response has no document body")
    try:
        return _visible_text(candidate)
    except ET.ParseError:
        return " ".join(candidate.split())


def _folder_contains(
    runner: LarkRunner,
    *,
    profile: str,
    folder_token: str,
    document_id: str,
) -> bool:
    page_token = ""
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"folder_token": folder_token, "page_size": 50}
        if page_token:
            params["page_token"] = page_token
        argv = [
            "lark-cli", "drive", "files", "list", "--params",
            json.dumps(params, ensure_ascii=False, separators=(",", ":")),
            "--as", "user", "--profile", profile,
        ]
        value = _run_lark(runner, argv)
        data = value.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise PipelineError("DRIVE_INVALID_OUTPUT", "drive list response must contain data.files")
        for item in data["files"]:
            if not isinstance(item, dict):
                raise PipelineError("DRIVE_INVALID_OUTPUT", "drive data.files entries must be objects")
            identity = item.get("token")
            parent = item.get("parent_token")
            parent_matches = parent is None or str(parent) == folder_token
            if str(identity) == document_id and parent_matches:
                return True
        next_token = data.get("next_page_token")
        has_more = data.get("has_more")
        if has_more not in {True, False, None}:
            raise PipelineError("DRIVE_INVALID_OUTPUT", "drive data.has_more must be boolean")
        if not has_more or not isinstance(next_token, str) or not next_token or next_token == page_token:
            break
        page_token = next_token
    return False


def publish_review(
    config: Mapping[str, Any],
    data: Mapping[str, Any],
    *,
    report_path: Path,
    state_path: Path,
    document_id: str | None = None,
    runner: LarkRunner | None = None,
) -> dict[str, Any]:
    checked = validate_review(config, data)
    if checked["failures"]:
        raise PipelineError("CHECK_FAILED", "publish requires evidence with no validation failures")
    if checked["warnings"]:
        raise PipelineError("WARNINGS_BLOCK_PUBLISH", "publish requires all reconciliation warnings to be resolved")
    report_raw, expected_visible = validate_report_xml(report_path, config)
    content_hash, job_hash = _fingerprints(config, data, report_raw.encode("utf-8"))
    profile = config["delivery"]["profile"]
    folder_token = config["delivery"]["folder_token"]
    active_runner = runner or LarkRunner()
    credential_lock = _CredentialLock() if runner is None else contextlib.nullcontext()
    with _StateLock(state_path), credential_lock:
        state = _state_for(state_path)
        if state and (state.get("job_hash") != job_hash or state.get("content_hash") != content_hash):
            raise PipelineError("STATE_FINGERPRINT_MISMATCH", "state belongs to different company, period, or report content")
        known_id = str(state.get("document_id") or "")
        if document_id and known_id and document_id != known_id:
            raise PipelineError("DOCUMENT_ID_MISMATCH", "provided document id differs from saved state")
        if not document_id and state.get("phase") in {"create_started", "uncertain"} and not known_id:
            raise PipelineError("CREATE_RESULT_UNCERTAIN", "create result is uncertain; recover with --document-id")
        if state.get("phase") == "create_warning":
            raise PipelineError("CREATE_WARNINGS", "create returned warnings; document remains unverified")
        auth = _run_lark(
            active_runner,
            ["lark-cli", "auth", "status", "--json", "--verify", "--profile", profile],
        )
        if not _auth_verified(auth):
            raise PipelineError("LARK_AUTH_INVALID", "verified user identity with valid token is required")
        target_id = document_id or known_id
        if not target_id:
            parsed = _run_lark(
                active_runner,
                [
                    "lark-cli", "docs", "+script", "--command", "parse", "--content",
                    "@" + str(report_path.resolve()), "--as", "user", "--profile", profile,
                ],
            )
            parsed_data = parsed.get("data")
            assessment = parsed_data.get("assessment") if isinstance(parsed_data, dict) else None
            assessment_status = assessment.get("status") if isinstance(assessment, dict) else None
            if assessment_status != "passed":
                raise PipelineError("REPORT_PARSE_FAILED", "lark document script assessment did not pass")
            _state_write(state_path, phase="create_started", content_hash=content_hash, job_hash=job_hash, resource_folder_id=folder_token)
            try:
                created = _run_lark(
                    active_runner,
                    [
                        "lark-cli", "docs", "+create", "--doc-format", "xml", "--content",
                        "@" + str(report_path.resolve()), "--parent-token", folder_token,
                        "--as", "user", "--profile", profile,
                    ],
                    uncertain_on_timeout=True,
                )
                created_data = created.get("data")
                created_document = created_data.get("document") if isinstance(created_data, dict) else None
                extracted = created_document.get("document_id") if isinstance(created_document, dict) else None
                if not isinstance(extracted, str) or not extracted:
                    raise PipelineError("CREATE_RESULT_UNCERTAIN", "create returned no document id; do not retry automatically")
                target_id = extracted
                _state_write(
                    state_path, phase="created", content_hash=content_hash, job_hash=job_hash,
                    document_id=target_id, resource_folder_id=folder_token,
                )
                create_warnings = created_data.get("warnings") if isinstance(created_data, dict) else None
                if create_warnings is not None and not isinstance(create_warnings, list):
                    raise PipelineError("CREATE_INVALID_OUTPUT", "create data.warnings must be a list")
                if isinstance(create_warnings, list) and create_warnings:
                    _state_write(
                        state_path, phase="create_warning", content_hash=content_hash, job_hash=job_hash,
                        document_id=target_id, resource_folder_id=folder_token,
                    )
                    raise PipelineError("CREATE_WARNINGS", "create returned warnings; document remains unverified")
            except PipelineError as exc:
                if exc.code in {"CREATE_RESULT_UNCERTAIN", "LARK_INVALID_OUTPUT"}:
                    _state_write(
                        state_path, phase="uncertain", content_hash=content_hash, job_hash=job_hash,
                        resource_folder_id=folder_token,
                    )
                    raise PipelineError("CREATE_RESULT_UNCERTAIN", "create result is uncertain; recover with --document-id") from exc
                raise
        elif document_id and not known_id:
            _state_write(
                state_path, phase="created", content_hash=content_hash, job_hash=job_hash,
                document_id=target_id, resource_folder_id=folder_token,
            )
        fetched = _run_lark(
            active_runner,
            [
                "lark-cli", "docs", "+fetch", "--doc", target_id, "--scope", "full",
                "--doc-format", "xml", "--detail", "full", "--as", "user", "--profile", profile,
            ],
        )
        actual_visible = _extract_fetch_text(fetched, target_id)
        if actual_visible != expected_visible:
            raise PipelineError("READBACK_MISMATCH", "fetched human-visible document text does not fully match report XML")
        if not _folder_contains(
            active_runner, profile=profile, folder_token=folder_token, document_id=target_id
        ):
            raise PipelineError("FOLDER_MISMATCH", "created document was not found in the configured folder")
        _state_write(
            state_path, phase="verified", content_hash=content_hash, job_hash=job_hash,
            document_id=target_id, resource_folder_id=folder_token,
        )
        return {
            "status": "verified",
            "document_id": target_id,
            "resource_folder_id": folder_token,
            "content_hash": content_hash,
        }


def _cmd_init(args: argparse.Namespace) -> int:
    output = Path(args.out)
    if output.exists():
        raise PipelineError("OUTPUT_EXISTS", f"refusing to overwrite existing config: {output}")
    failures: list[str] = []
    start = _date(args.start, "start", failures)
    end = _date(args.end, "end", failures)
    if start and end and end < start:
        failures.append("end must be on or after start")
    if failures:
        raise PipelineError("INVALID_PERIOD", "; ".join(failures))
    config = {
        "schema_version": SCHEMA_VERSION,
        "company": args.company,
        "period": {"start": args.start, "end": args.end},
        "source": {"platform": "douyin_life_business", "browser_profile": args.browser_profile or ""},
        "execution": {"mode": "background_only", "allow_foreground": False},
        "delivery": {"profile": args.profile, "identity": "user", "folder_token": args.folder_token},
        "required_metrics": REQUIRED_METRICS,
    }
    checked = validate_config(config)
    if checked["failures"]:
        raise PipelineError("INVALID_CONFIG", "; ".join(checked["failures"]))
    _atomic_json(output, config)
    print(json.dumps({"status": "created", "config": str(output)}, ensure_ascii=False))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    result = validate_review(_load_json(Path(args.config)), _load_json(Path(args.data)))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["failures"] else 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    analysis = build_analysis(_load_json(Path(args.config)), _load_json(Path(args.data)))
    output = Path(args.out)
    if output.exists():
        raise PipelineError("OUTPUT_EXISTS", f"refusing to overwrite existing analysis: {output}")
    _atomic_json(output, analysis)
    print(json.dumps({"status": "created", "analysis": str(output)}, ensure_ascii=False))
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    result = publish_review(
        _load_json(Path(args.config)),
        _load_json(Path(args.data)),
        report_path=Path(args.report),
        state_path=Path(args.state),
        document_id=args.document_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--company", required=True)
    init.add_argument("--start", required=True)
    init.add_argument("--end", required=True)
    init.add_argument("--profile", required=True)
    init.add_argument("--folder-token", required=True)
    init.add_argument("--browser-profile")
    init.add_argument("--out", required=True)
    init.set_defaults(func=_cmd_init)
    check = subparsers.add_parser("check")
    check.add_argument("--config", required=True)
    check.add_argument("--data", required=True)
    check.set_defaults(func=_cmd_check)
    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--config", required=True)
    analyze.add_argument("--data", required=True)
    analyze.add_argument("--out", required=True)
    analyze.set_defaults(func=_cmd_analyze)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--config", required=True)
    publish.add_argument("--data", required=True)
    publish.add_argument("--report", required=True)
    publish.add_argument("--state", required=True)
    publish.add_argument("--document-id")
    publish.set_defaults(func=_cmd_publish)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except PipelineError as exc:
        print(json.dumps({"status": "error", "error_code": exc.code, "message": exc.message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
