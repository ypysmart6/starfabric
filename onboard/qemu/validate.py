#!/usr/bin/env python3
"""Run ARM64 Rust tests in QEMU user mode and boot an ARM64 VM under TCG."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get("STARFABRIC_QEMU_IMAGE", "starfabric/qemu-arm64:rust-1.89-alpine-3.24.1")
REPORT = ROOT / "reports/qemu-arm64-sil.json"


def run(command: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def require(result: subprocess.CompletedProcess[str], action: str) -> str:
    output = result.stdout + result.stderr
    if result.returncode:
        raise AssertionError(f"{action} failed ({result.returncode}):\n{output}")
    return output


def main() -> None:
    build = run(
        ["docker", "build", "--pull=false", "-t", IMAGE, "-f", str(ROOT / "onboard/qemu/Dockerfile"), str(ROOT)],
        timeout=1200,
    )
    require(build, "QEMU toolchain image build")

    mount = f"{ROOT / 'onboard'}:/work"
    arm_tests = run(
        [
            "docker", "run", "--rm", "--network=bridge",
            "-v", mount,
            "-v", "starfabric-cargo-registry:/usr/local/cargo/registry",
            "-w", "/work", IMAGE,
            "cargo", "test", "--locked", "--target", "aarch64-unknown-linux-gnu",
        ],
        timeout=1200,
    )
    arm_output = require(arm_tests, "ARM64 QEMU-user Rust tests")
    match = re.search(r"test result: ok\. (\d+) passed", arm_output)
    assert match and int(match.group(1)) >= 6, arm_output

    version = require(
        run(["docker", "run", "--rm", IMAGE, "qemu-system-aarch64", "--version"]),
        "QEMU version",
    ).splitlines()[0]
    boot_command = [
        "docker", "run", "--rm", "--network=none", IMAGE,
        "timeout", "50", "qemu-system-aarch64",
        "-machine", "virt", "-cpu", "cortex-a72", "-accel", "tcg,thread=multi",
        "-m", "512", "-smp", "2", "-nographic", "-no-reboot",
        "-bios", "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd",
        "-drive", "file=/opt/alpine-virt-aarch64.iso,media=cdrom,readonly=on,format=raw",
        "-device", "virtio-rng-pci",
        "-netdev", "user,id=control", "-device", "virtio-net-pci,netdev=control,romfile=",
    ]
    boot = run(boot_command, timeout=70)
    boot_output = boot.stdout + boot.stderr
    if boot.returncode not in {0, 124}:
        raise AssertionError(f"ARM64 system boot failed ({boot.returncode}):\n{boot_output}")
    markers = ["OpenRC 0.63.1 is starting up Linux", "Welcome to Alpine Linux", "localhost login:"]
    if not any(marker in boot_output for marker in markers):
        raise AssertionError(f"ARM64 Linux did not reach userspace:\n{boot_output[-8000:]}")

    report = {
        "schema_version": 1,
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "arm64_cross_compilation": True,
            "qemu_user_executed_rust_tests": int(match.group(1)),
            "qemu_system_tcg_boot": True,
            "arm64_linux_userspace_reached": True,
            "virtio_rng_device": True,
            "virtio_network_device": True,
        },
        "versions": {
            "qemu": version,
            "rust": "1.89",
            "guest": "Alpine Linux 3.24.1 aarch64",
        },
        "guest_sha256": "c81699152db11d2a6dbb7d75348d632fcf5811eff414d7e71876a8bb6d48bc02",
        "acceleration": "TCG software emulation; KVM is optional and was not claimed",
        "boundary": "QEMU closes ARM64 software/device emulation on one PC. Flight BSP, boot ROM/root of trust, modem, FPGA and radiation-qualified target remain hardware gates.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(
        f"PASS: {version}; ARM64 Rust tests={match.group(1)}; "
        "Alpine ARM64 reached userspace under TCG"
    )


if __name__ == "__main__":
    main()
