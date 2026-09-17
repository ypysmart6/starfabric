#!/usr/bin/env python3
"""Build and run a pinned srsRAN Project Rel-17 NTN gNB without RF hardware."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".cache/srsran-project"
SOURCE_URL = "https://github.com/srsran/srsRAN_Project.git"
COMMIT = "d2f4b70dda8e2c557d5b05a0ac5f92dbddda19bc"
IMAGE = os.environ.get("STARFABRIC_SRSRAN_IMAGE", "starfabric/srsran-project:release-25.10")
CONFIG = ROOT / "lab/srsran/geo_ntn_testmode.yml"
DOCKERFILE = ROOT / "lab/srsran/Dockerfile"
EVIDENCE = ROOT / "reports/srsran"
REPORT = ROOT / "reports/srsran-rel17-ntn.json"


def run(command: list[str], timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def require(result: subprocess.CompletedProcess[str], action: str) -> str:
    output = result.stdout + result.stderr
    if result.returncode:
        raise AssertionError(f"{action} failed ({result.returncode}):\n{output[-12000:]}")
    return output


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_source() -> None:
    if not (SOURCE / ".git").is_dir():
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        require(
            run(["git", "clone", "--filter=blob:none", "--no-checkout", SOURCE_URL, str(SOURCE)]),
            "clone official srsRAN source",
        )
    origin = require(run(["git", "-C", str(SOURCE), "remote", "get-url", "origin"]), "read origin").strip()
    if origin != SOURCE_URL:
        raise AssertionError(f"refusing unexpected srsRAN origin {origin!r}")
    present = run(["git", "-C", str(SOURCE), "cat-file", "-e", f"{COMMIT}^{{commit}}"])
    if present.returncode:
        require(run(["git", "-C", str(SOURCE), "fetch", "--depth=1", "origin", COMMIT]), "fetch pinned commit")
    current = require(run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"]), "read source commit").strip()
    if current != COMMIT:
        dirty = require(run(["git", "-C", str(SOURCE), "status", "--porcelain"]), "check source tree")
        if dirty.strip():
            raise AssertionError("refusing to overwrite a modified srsRAN cache")
        require(run(["git", "-C", str(SOURCE), "checkout", "--detach", COMMIT]), "checkout pinned commit")


def image_metadata() -> dict[str, object] | None:
    inspected = run(["docker", "image", "inspect", IMAGE], timeout=30)
    if inspected.returncode:
        return None
    return json.loads(inspected.stdout)[0]


def prepare_image() -> dict[str, object]:
    metadata = image_metadata()
    labels = ((metadata or {}).get("Config") or {}).get("Labels") or {}  # type: ignore[union-attr]
    if labels.get("org.opencontainers.image.revision") != COMMIT:
        require(
            run(
                [
                    "docker", "build", "--pull=false", "--file", str(DOCKERFILE),
                    "--build-arg", f"SRSRAN_COMMIT={COMMIT}", "--tag", IMAGE, str(SOURCE),
                ],
                timeout=3600,
            ),
            "build pinned srsRAN image",
        )
        metadata = image_metadata()
    if metadata is None:
        raise AssertionError("srsRAN image missing after build")
    labels = (metadata.get("Config") or {}).get("Labels") or {}  # type: ignore[union-attr]
    if labels.get("org.opencontainers.image.revision") != COMMIT:
        raise AssertionError("srsRAN image revision label does not match pinned source")
    return metadata


def main() -> None:
    prepare_source()
    metadata = prepare_image()
    version_output = require(run(["docker", "run", "--rm", IMAGE, "--version"], timeout=30), "read gNB version")
    version_match = re.search(r"25\.10\.0", version_output)
    if not version_match:
        raise AssertionError(f"unexpected srsRAN version:\n{version_output}")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    pcap = EVIDENCE / "srsran_mac.pcap"
    log_path = EVIDENCE / "gnb.log"
    pcap.unlink(missing_ok=True)
    container = f"starfabric-srsran-{uuid.uuid4().hex[:10]}"
    created = False
    started_at = time.monotonic()
    early_exit = False
    ready_at: float | None = None
    try:
        require(
            run(
                [
                    "docker", "create", "--name", container, "--network", "none",
                    "--volume", f"{CONFIG}:/config/gnb.yml:ro",
                    "--volume", f"{EVIDENCE}:/evidence",
                    IMAGE, "-c", "/config/gnb.yml",
                ],
                timeout=30,
            ),
            "create srsRAN gNB container",
        )
        created = True
        require(run(["docker", "start", container], timeout=30), "start srsRAN gNB")
        # Container running is not gNB readiness: initialization can take tens
        # of seconds on a shared single-PC testbed.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = require(
                run(["docker", "inspect", "--format", "{{.State.Running}}", container], timeout=10),
                "inspect srsRAN gNB",
            ).strip()
            if state != "true":
                early_exit = True
                break
            current_log = require(run(["docker", "logs", container], timeout=10), "read gNB readiness")
            if ready_at is None and "==== gNB started ===" in current_log:
                ready_at = time.monotonic()
            if ready_at is not None and time.monotonic() - ready_at >= 15:
                break
            time.sleep(0.5)
        if not early_exit:
            require(run(["docker", "stop", "--signal", "SIGINT", "--time", "15", container], timeout=30), "stop gNB")
        log = require(run(["docker", "logs", container], timeout=30), "collect gNB logs")
        exit_code = int(require(
            run(["docker", "inspect", "--format", "{{.State.ExitCode}}", container], timeout=10),
            "read gNB exit code",
        ).strip())
    finally:
        if created:
            run(["docker", "rm", "--force", container], timeout=30)

    elapsed = time.monotonic() - started_at
    log_path.write_text(log, encoding="utf-8")
    lowered = log.lower()
    fatal_config = "invalid configuration" in lowered or "configuration error" in lowered
    scheduler_lines = [
        line for line in log.splitlines()
        if ('[METRICS' in line or '"timestamp"' in line)
        and (re.search(r"\b0x0*44\b", line, re.IGNORECASE) or '"pci"' in line or "dl_brate" in line)
    ]
    config_text = CONFIG.read_text(encoding="utf-8")
    checks: dict[str, bool | int] = {
        "official_source_commit_pinned": True,
        "image_revision_matches_source": True,
        "srsran_project_25_10_binary": bool(version_match),
        "real_gnb_process_sustained": not early_exit and ready_at is not None and time.monotonic() - ready_at >= 15,
        "release_17_ntn_config_accepted": exit_code == 0 and not fatal_config,
        "band_256_runtime_config": "band: 256" in config_text and "dl_arfcn: 437000" in config_text,
        "sib19_runtime_config": "sib_mapping: 19" in config_text,
        "dummy_ru_test_mode": "ru_dummy:" in config_text and "test_mode:" in config_text,
        "test_ue_scheduler_metrics": len(scheduler_lines),
        "mac_pcap_written": pcap.is_file() and pcap.stat().st_size > 24,
    }
    report = {
        "schema_version": 1,
        "success": all(bool(value) for value in checks.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "runtime": {
            "seconds": elapsed,
            "exit_code": exit_code,
            "scheduler_evidence_lines": scheduler_lines[-20:],
            "mac_pcap_bytes": pcap.stat().st_size if pcap.is_file() else 0,
            "network": "none",
            "radio_unit": "srsRAN ru_dummy",
            "test_ue_rnti": "0x44",
        },
        "versions": {
            "srsran": version_output.strip(),
            "source_commit": COMMIT,
            "image": IMAGE,
            "image_id": metadata.get("Id"),
        },
        "artifacts": {
            "config_sha256": sha256(CONFIG),
            "dockerfile_sha256": sha256(DOCKERFILE),
            "log": str(log_path.relative_to(ROOT)),
            "log_sha256": sha256(log_path),
            "pcap": str(pcap.relative_to(ROOT)) if pcap.is_file() else None,
            "pcap_sha256": sha256(pcap) if pcap.is_file() else None,
        },
        "boundary": "The real srsRAN CU/DU/L1-L3 process uses its software dummy RU/test UE. Commercial UE and OTA/RF conformance remain hardware/vendor gates.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["success"]:
        raise SystemExit("srsRAN Rel-17 NTN runtime checks failed")


if __name__ == "__main__":
    main()
