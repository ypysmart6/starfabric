#!/usr/bin/env python3
"""Execute and attest deterministic chaos, recovery, sharding and scale tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports/reliability-scale.json"
PACKAGES = ["./tests/chaos", "./tests/scale", "./internal/reconcile", "./internal/sharding"]
EXPECTED = {
    "TestControllerRestartRecoversTopologyAndCommittedPlan",
    "TestDelayedAndOutOfOrderTelemetryCannotOverwriteNewState",
    "TestNodeFailureReroutesAndRecoveryRestoresPreferredPath",
    "TestLinkFlapConvergesToLatestSequence",
    "TestTrafficVerificationFailureRollsBack",
    "TestObservabilityFailureDoesNotStopControl",
    "TestUnresponsiveDeviceHonorsDeadline",
    "TestPlannerThousandSatelliteSmoke",
    "TestPartialCommitRollsBackEveryDevice",
    "TestStableAndFailureDomainDiverseAssignment",
    "TestUnhealthyControllerRemoved",
}


def environment() -> dict[str, str]:
    value = os.environ.copy()
    value.setdefault("GOCACHE", str(ROOT / ".cache/go-build"))
    return value


def main() -> None:
    result = subprocess.run(
        ["go", "test", "-count=1", "-json", *PACKAGES],
        cwd=ROOT,
        env=environment(),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    passed: set[str] = set()
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("Action") == "pass" and event.get("Test"):
            passed.add(event["Test"])
    missing = EXPECTED - passed
    assert not missing, f"missing passing reliability tests: {sorted(missing)}"

    benchmark = subprocess.run(
        ["go", "test", "-run", "^$", "-bench", "^BenchmarkPlannerThousandSatellite$", "-benchtime=1x", "-count=1", "./tests/scale"],
        cwd=ROOT,
        env=environment(),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert benchmark.returncode == 0, benchmark.stdout + benchmark.stderr
    match = re.search(r"BenchmarkPlannerThousandSatellite-\d+\s+\d+\s+(\d+) ns/op", benchmark.stdout)
    assert match, benchmark.stdout
    nanoseconds = int(match.group(1))

    report = {
        "schema_version": 1,
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {name: True for name in sorted(EXPECTED)},
        "passed_tests": len(passed),
        "required_tests": len(EXPECTED),
        "thousand_satellite_one_plan_ns": nanoseconds,
        "packages": PACKAGES,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(f"PASS: {len(EXPECTED)} reliability gates and 1000-satellite plan in {nanoseconds / 1e6:.3f} ms")


if __name__ == "__main__":
    main()
