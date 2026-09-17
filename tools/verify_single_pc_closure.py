#!/usr/bin/env python3
"""Audit every roadmap capability against successful, check-level evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .single_pc import local_path, sha256, source_digest
except ImportError:
    from single_pc import local_path, sha256, source_digest


ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "docs/single-pc-closure.json"
OUTPUT = ROOT / "reports/single-pc-closure.json"


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return math.isfinite(value) and value > 0
    return False


def value_at(document: Any, path: str) -> Any:
    value = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def validate_gate(identifier: str, gate: dict[str, Any], max_age_hours: float | None,
                  run_dir: Path | None = None, run: dict[str, Any] | None = None) -> list[str]:
    failures: list[str] = []
    if not isinstance(gate, dict):
        return [f"{identifier}: gate must be an object"]
    relative = gate.get("report")
    if not isinstance(relative, str) or not relative:
        return [f"{identifier}: gate has no report"]
    try:
        report_path = local_path(ROOT, relative)
        if run_dir is not None:
            candidates = [stage for stage in (run or {}).get("stages", [])
                          if stage.get("status") == "passed" and relative in stage.get("reports", {})]
            if not candidates:
                return [f"{identifier}: no successful current-run producer for {relative}"]
            evidence = candidates[-1]["reports"][relative]
            report_path = local_path(run_dir, evidence["path"])
            if sha256(report_path) != evidence.get("sha256"):
                return [f"{identifier}: current-run evidence hash mismatch: {relative}"]
            producer = candidates[-1]
            if relative in producer.get("artifact_reports", {}):
                artifacts = producer.get("artifacts", {}).get(relative, {})
                if not artifacts:
                    return [f"{identifier}: current-run raw artifacts are absent: {relative}"]
                for original, archived in artifacts.items():
                    path = local_path(run_dir, archived["path"])
                    if sha256(path) != archived.get("sha256"):
                        return [f"{identifier}: raw artifact hash mismatch: {original}"]
    except (OSError, ValueError, KeyError) as error:
        return [f"{identifier}: invalid evidence {relative}: {error}"]
    if not report_path.is_file():
        return [f"{identifier}: missing report {relative}"]
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"{identifier}: invalid report {relative}: {error}"]
    if not isinstance(report, dict):
        return [f"{identifier}: report {relative} must be an object"]
    if report.get("success") is not True:
        failures.append(f"{identifier}: {relative} success is not true")
    for field in ("checks", "paths"):
        values = gate.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            return [f"{identifier}: {field} must be a list of nonempty strings"]
    for check in gate.get("checks", []):
        try:
            value = value_at(report, f"checks.{check}")
        except KeyError:
            failures.append(f"{identifier}: {relative} lacks checks.{check}")
            continue
        if not truthy(value):
            failures.append(f"{identifier}: {relative} checks.{check} is not truthy")
    for path in gate.get("paths", []):
        try:
            value = value_at(report, path)
        except KeyError:
            failures.append(f"{identifier}: {relative} lacks {path}")
            continue
        if not truthy(value):
            failures.append(f"{identifier}: {relative} {path} is not truthy")
    if max_age_hours is not None:
        generated = report.get("generated_at")
        if not isinstance(generated, str):
            failures.append(f"{identifier}: {relative} lacks generated_at for freshness gate")
        else:
            try:
                stamp = datetime.fromisoformat(generated.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("timestamp must include timezone")
                age = datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
                if age.total_seconds() < -300:
                    failures.append(f"{identifier}: {relative} generated_at is in the future")
                if age.total_seconds() > max_age_hours * 3600:
                    failures.append(f"{identifier}: {relative} is {age.total_seconds()/3600:.1f}h old")
            except ValueError:
                failures.append(f"{identifier}: {relative} has invalid generated_at")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--max-age-hours", type=float)
    parser.add_argument("--run-dir", type=Path, help="validate immutable evidence from one suite execution")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.max_age_hours is not None and (not math.isfinite(args.max_age_hours) or args.max_age_hours <= 0):
        raise SystemExit("--max-age-hours must be positive and finite")
    run = None
    if args.run_dir:
        try:
            run = json.loads((args.run_dir / "run.json").read_text())
        except (OSError, ValueError) as error:
            raise SystemExit(f"invalid current run: {error}") from error

    closure = json.loads(CLOSURE.read_text(encoding="utf-8"))
    if closure.get("schema_version") != 1:
        raise SystemExit("closure schema_version must be 1")
    manifest_path = ROOT / closure.get("roadmap_manifest", "")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    roadmap = {entry["id"]: entry for entry in manifest["entries"]}
    entries = closure.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("closure entries must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    if run is not None:
        if run.get("profile") != "full" or run.get("status") != "finished":
            failures.append("current run must be a finished full-suite execution")
        if run.get("source_unchanged") is not True or run.get("source_sha256") != source_digest():
            failures.append("current-run source hash does not match this workspace")
        if not run.get("stages") or any(stage.get("status") != "passed" for stage in run["stages"]):
            failures.append("current run has failed, blocked, or absent stages")
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or identifier in indexed:
            failures.append(f"invalid or duplicate closure id {identifier!r}")
            continue
        indexed[identifier] = entry
    missing_ids = sorted(set(roadmap) - set(indexed))
    extra_ids = sorted(set(indexed) - set(roadmap))
    if missing_ids:
        failures.append(f"unclassified roadmap ids: {missing_ids}")
    if extra_ids:
        failures.append(f"unknown closure ids: {extra_ids}")

    counts = {"closed": 0, "partial": 0, "hardware-excluded": 0}
    open_items: list[dict[str, Any]] = []
    for identifier in sorted(set(roadmap) & set(indexed)):
        source, entry = roadmap[identifier], indexed[identifier]
        status = entry.get("status")
        if status not in counts:
            failures.append(f"{identifier}: invalid status {status!r}")
            continue
        counts[status] += 1
        technologies = set(source["technologies"])
        missing = entry.get("missing", [])
        excluded = entry.get("hardware_excluded", [])
        if not isinstance(missing, list) or not isinstance(excluded, list):
            failures.append(f"{identifier}: missing/hardware_excluded must be lists")
            continue
        unknown = (set(missing) | set(excluded)) - technologies
        overlap = set(missing) & set(excluded)
        if unknown:
            failures.append(f"{identifier}: classifications not in roadmap technologies: {sorted(unknown)}")
        if overlap:
            failures.append(f"{identifier}: technologies both missing and excluded: {sorted(overlap)}")
        gates = entry.get("gates", [])
        if not isinstance(gates, list):
            failures.append(f"{identifier}: gates must be a list")
            continue
        for gate in gates:
            failures.extend(validate_gate(identifier, gate, args.max_age_hours, args.run_dir, run))
        if status == "closed" and missing:
            failures.append(f"{identifier}: closed entry still lists missing technologies")
        if status == "partial" and not missing:
            failures.append(f"{identifier}: partial entry must name missing technologies")
        if status == "hardware-excluded":
            if missing or set(excluded) != technologies or not entry.get("reason"):
                failures.append(f"{identifier}: hardware exclusion must cover every technology and give a reason")
        if status == "closed" and not gates:
            failures.append(f"{identifier}: closed entry requires at least one evidence gate")
        if status == "partial":
            open_items.append({"id": identifier, "missing": missing})

    complete = not open_items and not failures
    report = {
        "schema_version": 1,
        "success": complete,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_scope": "current-run" if run is not None else "historical-reports",
        "run_id": run.get("run_id") if run is not None else None,
        "roadmap_entries": len(roadmap),
        "counts": counts,
        "open_software_items": open_items,
        "validation_failures": failures,
        "ai_excluded": closure.get("ai_excluded", []),
        "non_satellite_excluded": closure.get("non_satellite_excluded", []),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    evidence_state = "valid" if not failures else f"invalid ({len(failures)} errors)"
    print(
        f"AUDIT: roadmap={len(roadmap)} closed={counts['closed']} "
        f"partial={counts['partial']} hardware-excluded={counts['hardware-excluded']} evidence={evidence_state}"
    )
    for item in open_items:
        print(f"OPEN: {item['id']}: {', '.join(item['missing'])}")
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures or (args.require_complete and not complete):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
