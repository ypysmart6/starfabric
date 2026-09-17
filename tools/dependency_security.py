#!/usr/bin/env python3
"""Run the pinned Go call-graph vulnerability and dependency-policy gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/dependency-security.json"
PINNED_SCANNER = "v1.8.0"
MINIMUM_GO = (1, 26, 6)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GOCACHE"] = str(ROOT / ".cache/go-build")
    return subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def decode_json_stream(payload: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    values: list[dict[str, Any]] = []
    offset = 0
    while offset < len(payload):
        while offset < len(payload) and payload[offset].isspace():
            offset += 1
        if offset == len(payload):
            break
        value, offset = decoder.raw_decode(payload, offset)
        if not isinstance(value, dict):
            raise AssertionError("go list returned a non-object JSON value")
        values.append(value)
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--govulncheck", default=str(ROOT / ".cache/tools/bin/govulncheck"))
    args = parser.parse_args()
    scanner = Path(args.govulncheck).resolve()
    if not scanner.is_file() or not os.access(scanner, os.X_OK):
        raise SystemExit("pinned govulncheck is missing; run `make bootstrap-security-tools`")

    version_output = run([str(scanner), "-version"]).stdout
    scanner_match = re.search(r"Scanner: govulncheck@(v\S+)", version_output)
    go_match = re.search(r"Go: go(\d+)\.(\d+)\.(\d+)", version_output)
    if not scanner_match or not go_match:
        raise SystemExit(f"unrecognized govulncheck version output: {version_output!r}")
    scanner_version = scanner_match.group(1)
    go_version = tuple(int(part) for part in go_match.groups())

    module_verification = run(["go", "mod", "verify"])
    modules = decode_json_stream(run(["go", "list", "-m", "-json", "all"]).stdout)
    if not modules or modules[0].get("Main") is not True:
        raise SystemExit("dependency inventory does not begin with the main module")

    dependabot = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {entry["package-ecosystem"] for entry in dependabot.get("updates", [])}
    required_ecosystems = {"gomod", "cargo", "pip", "docker", "github-actions"}
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    scan = run([str(scanner), "-test", "./..."], check=False)
    scan_output = scan.stdout + scan.stderr
    checks = {
        "go_toolchain_patched": go_version >= MINIMUM_GO,
        "go_modules_verified": module_verification.returncode == 0
        and "all modules verified" in module_verification.stdout,
        "dependency_inventory": len(modules),
        "dependency_update_automation": required_ecosystems <= ecosystems,
        "official_go_vulnerability_database": "DB: https://vuln.go.dev" in version_output,
        "govulncheck_pinned": scanner_version == PINNED_SCANNER,
        "govulncheck_reachable_vulnerabilities_zero": scan.returncode == 0
        and "No vulnerabilities found." in scan_output
        and "affected by 0 vulnerabilities" in scan_output,
        "ci_vulnerability_gate": "golang/govulncheck-action@v1" in ci,
    }
    report = {
        "schema_version": 1,
        "success": all(bool(value) for value in checks.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "scanner": {
            "name": "govulncheck",
            "version": scanner_version,
            "go_version": ".".join(str(part) for part in go_version),
            "database": "https://vuln.go.dev",
            "mode": "source-with-tests",
        },
        "artifacts": {
            "go_sum_sha256": hashlib.sha256((ROOT / "go.sum").read_bytes()).hexdigest(),
            "scan_output_sha256": hashlib.sha256(scan_output.encode()).hexdigest(),
        },
        "summary": scan_output.strip().splitlines()[-3:],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["success"]:
        raise SystemExit("dependency security gate failed")


if __name__ == "__main__":
    main()
