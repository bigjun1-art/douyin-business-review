#!/usr/bin/env python3
"""Resumable local review job. No server, socket, browser driver or scheduler."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import review_pipeline as rp


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def inputs(job: Path) -> tuple[dict, dict, Path, str]:
    config = rp._load_json(job / "config.json")
    data = rp._load_json(job / "evidence.json")
    report = job / "report.xml"
    rp.validate_report_xml(report, config)
    fingerprint = digest({"config": config, "evidence": data,
                          "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()})
    return config, data, report, fingerprint


def checkpoint(job: Path, phase: str, **details) -> dict:
    result = {"phase": phase, **details}
    rp._atomic_json(job / "job-state.json", result)
    return result


def check_data(job: Path, config: dict, data: dict) -> dict:
    result = rp.validate_review(config, data)
    rp._atomic_json(job / "checks.json", result)
    return result


def run(job: Path, *, publish: bool = False, runner=None) -> dict:
    """Advance only available stages. Publishing is opt-in and requires reviewed inputs."""
    job = job.resolve()
    with rp._StateLock(job / "job-state.json"):
        config = rp._load_json(job / "config.json")
        errors = rp.validate_config(config)["failures"]
        if errors:
            raise rp.PipelineError("INVALID_CONFIG", "; ".join(errors))
        # An existing-document target must never be silently turned into a new document.
        if config["delivery"].get("document_id") or config["delivery"].get("mode", "create") != "create":
            return checkpoint(job, "BLOCKED_UPDATE_REQUIRES_BOUNDED_EDIT",
                              message="Use the documented revision-checked existing-document workflow; no document was created.")
        if not (job / "evidence.json").is_file():
            return checkpoint(job, "BLOCKED_BACKGROUND_CAPTURE", missing=list(config["required_metrics"]),
                              message="No live collector is bundled. Supply authorized isolated-connector evidence or platform exports; never operate the user's page.")
        data = rp._load_json(job / "evidence.json")
        checked = check_data(job, config, data)
        if checked["status"] != "pass":
            return checkpoint(job, "BLOCKED_EVIDENCE", checks="checks.json")
        rp._atomic_json(job / "analysis.json", rp.build_analysis(config, data))
        if not (job / "report.xml").is_file():
            return checkpoint(job, "AWAITING_REPORT", analysis="analysis.json")
        config, data, report, fingerprint = inputs(job)
        review_path = job / "review.json"
        if not review_path.is_file() or rp._load_json(review_path).get("input_hash") != fingerprint:
            return checkpoint(job, "AWAITING_SEMANTIC_REVIEW", input_hash=fingerprint)
        if not publish:
            return checkpoint(job, "READY_TO_PUBLISH", input_hash=fingerprint)
        try:
            result = rp.publish_review(config, data, report_path=report,
                                       state_path=job / "delivery-state.json", runner=runner)
        except rp.PipelineError as exc:
            checkpoint(job, "DELIVERY_REQUIRES_ATTENTION", input_hash=fingerprint,
                       error_code=exc.code, delivery_state="delivery-state.json")
            raise
        return checkpoint(job, "VERIFIED", input_hash=fingerprint, delivery=result)


def record_review(job: Path, reviewer: str) -> dict:
    """Record an already performed semantic review, not an automated claim of quality."""
    if not reviewer.strip():
        raise rp.PipelineError("INVALID_REVIEWER", "reviewer must not be empty")
    with rp._StateLock(job / "job-state.json"):
        config, data, _, fingerprint = inputs(job)
        if check_data(job, config, data)["status"] != "pass":
            raise rp.PipelineError("EVIDENCE_NOT_READY", "Resolve evidence failures and warnings before review")
        result = {"input_hash": fingerprint, "reviewer": reviewer,
                  "assertion": "Facts, scope, causal claims, plan versus results, and full-document consistency reviewed"}
        rp._atomic_json(job / "review.json", result)
        return {"phase": "REVIEW_RECORDED", "input_hash": fingerprint}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("run")
    start.add_argument("--job", required=True)
    start.add_argument("--publish", action="store_true")
    review = sub.add_parser("reviewed", help="Record semantic review already performed by the author/reviewer")
    review.add_argument("--job", required=True)
    review.add_argument("--reviewer", required=True)
    status = sub.add_parser("status", help="Read saved checkpoint only; does not verify remote state")
    status.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    job = Path(args.job).resolve()
    try:
        if args.command == "run":
            result = run(job, publish=args.publish)
        elif args.command == "reviewed":
            result = record_review(job, args.reviewer)
        else:
            result = {"saved_checkpoint": rp._load_json(job / "job-state.json"),
                      "live_verified": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("phase", "").startswith("BLOCKED_") else 0
    except (rp.PipelineError, OSError, ValueError) as exc:
        # Do not print arbitrary file contents, CLI output or credentials on errors.
        code = exc.code if isinstance(exc, rp.PipelineError) else "LOCAL_INPUT_ERROR"
        print(json.dumps({"status": "error", "error_code": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
