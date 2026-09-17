#!/usr/bin/env python3
"""Produce machine-readable evidence for the repository's core quality gates."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/core-quality.json"
FORBIDDEN = ("tensorflow", "pytorch", "torch", "onnx", "openai", "langchain", "llama", "transformers")


def execute(name: str, command: list[str], *, cwd: Path = ROOT) -> tuple[bool, str]:
    environment = os.environ.copy()
    environment.setdefault("GOCACHE", str(ROOT / ".cache/go-build"))
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout[-12000:]
    if result.returncode:
        print(f"FAIL: {name}\n{output}")
    else:
        print(f"PASS: {name}")
    return result.returncode == 0, output


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dependency_scan() -> tuple[bool, list[str]]:
    candidates = [
        ROOT / "go.mod",
        ROOT / "go.sum",
        ROOT / "onboard/Cargo.toml",
        ROOT / "onboard/Cargo.lock",
        ROOT / "tools/requirements-orbit.txt",
        ROOT / "lab/batfish/requirements.txt",
        ROOT / "lab/p4/requirements.txt",
    ]
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in candidates)
    found = [dependency for dependency in FORBIDDEN if dependency in text]
    return not found, found


def main() -> None:
    checks: dict[str, bool] = {}
    diagnostics: dict[str, str] = {}

    formatted, formatted_output = execute("gofmt", ["gofmt", "-l", "cmd", "internal", "tests"])
    checks["go_sources_formatted"] = formatted and not formatted_output.strip()
    checks["go_vet"], diagnostics["go_vet"] = execute("go vet", ["go", "vet", "./..."])
    checks["go_unit_tests"], diagnostics["go_unit_tests"] = execute(
        "go unit tests", ["go", "test", "./..."]
    )
    checks["go_race_tests"], diagnostics["go_race_tests"] = execute(
        "go race tests", ["go", "test", "-race", "./..."]
    )
    checks["go_modules_verified"], diagnostics["go_modules_verified"] = execute(
        "go module verification", ["go", "mod", "verify"]
    )
    checks["python_unit_tests"], diagnostics["python_unit_tests"] = execute(
        "Python NTN and acceptance tests", ["python3", "-m", "unittest", "ntn.test_experiment", "ntn.test_lab_lifecycle", "tools.test_single_pc"]
    )
    orbit_ok, orbit_output = execute(
        "Python orbit tests", ["python3", "-m", "unittest", "tools.test_ephemeris_contacts"]
    )
    checks["python_unit_tests"] = checks["python_unit_tests"] and orbit_ok
    diagnostics["python_unit_tests"] += orbit_output
    checks["coverage_manifest_valid"], diagnostics["coverage_manifest_valid"] = execute(
        "roadmap manifest", ["python3", "tools/verify_coverage.py"]
    )

    with tempfile.TemporaryDirectory(prefix="starfabric-core-", dir="/tmp") as temporary:
        directory = Path(temporary)
        binaries = []
        for index in range(2):
            path = directory / f"sf-controller-{index}"
            ok, output = execute(
                f"reproducible Go build {index + 1}",
                [
                    "go", "build", "-buildvcs=false", "-trimpath",
                    "-ldflags=-buildid=", "-o", str(path), "./cmd/sf-controller",
                ],
            )
            checks[f"go_build_{index + 1}"] = ok
            diagnostics[f"go_build_{index + 1}"] = output
            if ok:
                binaries.append(path)
        reproducible_hash = sha256(binaries[0]) if len(binaries) == 2 else ""
        checks["reproducible_go_build"] = (
            len(binaries) == 2 and sha256(binaries[0]) == sha256(binaries[1])
        )
        ondatra_binary = directory / "ondatra.test"
        checks["ondatra_test_binary_compiles"], diagnostics["ondatra_test_binary_compiles"] = execute(
            "Ondatra test binary compilation",
            ["go", "test", "-c", "-o", str(ondatra_binary)],
            cwd=ROOT / "tests/ondatra",
        )

    checks["forbidden_ai_dependencies_absent"], found = dependency_scan()
    checks["locked_go_dependencies"] = (ROOT / "go.sum").stat().st_size > 0
    checks["locked_rust_dependencies"] = (ROOT / "onboard/Cargo.lock").stat().st_size > 0
    report = {
        "schema_version": 1,
        "success": all(checks.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "offline core build, test, race, dependency and contract gates",
        "checks": checks,
        "reproducible_controller_sha256": reproducible_hash,
        "forbidden_dependencies_found": found,
        "notes": {
            "ondatra": "compiled only; live KNE DUT/ATE execution is a separate closure gate",
            "vulnerability_database": "online govulncheck remains a CI/release gate",
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary_report = REPORT.with_suffix(".json.tmp")
    temporary_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_report, REPORT)
    if not report["success"]:
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit(f"core acceptance failed: {failed}")
    print(f"PASS: {len(checks)} core quality gates; build sha256={reproducible_hash}")


if __name__ == "__main__":
    main()
