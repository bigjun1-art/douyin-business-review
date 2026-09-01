#!/usr/bin/env python3
"""Validate natural-month Douyin Business Analytics review data."""

from __future__ import annotations

import calendar
import datetime as dt
import json
import math
import sys
from pathlib import Path


CORE_METRICS = {
    "live_session_count", "live_duration_seconds", "live_viewers",
    "live_gmv", "live_coupon_count", "video_count_total",
    "influencer_video_count", "video_play_count",
}


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def close_enough(a: float, b: float, abs_tol: float = 1.0, rel_tol: float = 0.005) -> bool:
    return math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_review_data.py review-data.json", file=sys.stderr)
        return 2
    data = load(sys.argv[1])
    failures: list[str] = []
    warnings: list[str] = []
    company = str(data.get("company", "")).strip()
    month = str(data.get("review_month", "")).strip()
    if not company:
        failures.append("missing company")
    try:
        year, mon = map(int, month.split("-"))
        last_day = calendar.monthrange(year, mon)[1]
        expected_start = dt.date(year, mon, 1).isoformat()
        expected_end = dt.date(year, mon, last_day).isoformat()
    except Exception:
        failures.append("review_month must be YYYY-MM")
        expected_start = expected_end = ""
    metrics = data.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        failures.append("metrics must be a non-empty list")
        metrics = []
    by_name: dict[str, dict] = {}
    for i, item in enumerate(metrics):
        if not isinstance(item, dict):
            failures.append(f"metrics[{i}] must be an object")
            continue
        name = str(item.get("metric", "")).strip()
        if not name:
            failures.append(f"metrics[{i}] missing metric")
            continue
        if name in by_name:
            warnings.append(f"duplicate metric: {name}")
        by_name[name] = item
        for field in ("value", "raw_value", "period_start", "period_end", "scope", "source_module", "source_label"):
            if field not in item or item[field] in (None, ""):
                failures.append(f"{name} missing {field}")
        if name in CORE_METRICS and expected_start:
            if item.get("period_start") != expected_start:
                failures.append(f"{name} period_start is not natural-month start")
            if not data.get("partial_period") and item.get("period_end") != expected_end:
                failures.append(f"{name} period_end is not natural-month end")
    total = by_name.get("video_count_total")
    influencer = by_name.get("influencer_video_count")
    relation = data.get("influencer_in_total")
    if total and influencer and relation is not False and float(influencer["value"]) > float(total["value"]):
        failures.append("influencer_video_count exceeds video_count_total without independent-scope declaration")
    if total and influencer and relation is None:
        warnings.append("influencer_in_total is unknown; do not add total and influencer counts")
    live_accounts = data.get("live_accounts") or []
    if live_accounts and by_name.get("live_duration_seconds"):
        values = [x.get("live_duration_seconds") for x in live_accounts if x.get("live_duration_seconds") is not None]
        if len(values) == len(live_accounts):
            detail_sum = sum(float(x) for x in values)
            overview = float(by_name["live_duration_seconds"]["value"])
            if not close_enough(detail_sum, overview, abs_tol=60.0):
                failures.append(f"live duration mismatch: detail={detail_sum}, overview={overview}")
        else:
            warnings.append("some live accounts lack duration; total cannot be reconciled")
    status = "fail" if failures else ("warning" if warnings else "pass")
    print(json.dumps({"status": status, "company": company, "review_month": month, "failures": failures, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
