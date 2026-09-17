#!/usr/bin/env python3
"""Compile, load, attach and packet-test the XDP counter in an isolated netns."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "lab/dataplane/xdp_probe.bpf.c"


def run(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command[:4])}: {(result.stderr or result.stdout).strip()}")
    return result


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "reports/xdp-runtime.json")
args = parser.parse_args()
clang = shutil.which("clang-14") or shutil.which("clang")
bpftool = shutil.which("bpftool")
if not clang or not bpftool:
    raise SystemExit("clang and bpftool are required")
versioned_bpftool = Path(f"/usr/lib/linux-tools/{os.uname().release}/bpftool")
bpftool_real = versioned_bpftool.resolve() if versioned_bpftool.exists() else Path(bpftool).resolve()
headers = Path(f"/usr/src/linux-headers-{os.uname().release}/tools/bpf/resolve_btfids/libbpf/include")
if not (headers / "bpf/bpf_helpers.h").exists():
    raise SystemExit(f"kernel libbpf headers not found under {headers}")

with tempfile.TemporaryDirectory(prefix="starfabric-xdp-") as directory:
    object_path = Path(directory) / "xdp_probe.bpf.o"
    run([
        clang, "-O2", "-g", "-target", "bpf", "-D__TARGET_ARCH_x86",
        "-I/usr/include/x86_64-linux-gnu", f"-I{headers}", "-c", str(SOURCE), "-o", str(object_path),
    ])
    pin_suffix = str(os.getpid())
    script = r'''
set -euo pipefail
program=/sys/fs/bpf/starfabric_probe_${PIN_SUFFIX}
maps=/sys/fs/bpf/starfabric_maps_${PIN_SUFFIX}
cleanup() {
  bpftool net detach xdp dev lo >/dev/null 2>&1 || true
  rm -f "$program" "$maps/packets"
  rmdir "$maps" >/dev/null 2>&1 || true
}
trap cleanup EXIT
bpftool prog load /work/xdp_probe.bpf.o "$program" type xdp pinmaps "$maps"
bpftool net attach xdp pinned "$program" dev lo
for ignored in $(seq 1 20); do echo packet > /dev/udp/127.0.0.1/9 || true; done
bpftool -j map dump pinned "$maps/packets"
'''
    result = run([
        "docker", "run", "--rm", "--privileged", "--network", "none",
        "--entrypoint", "/bin/bash", "-e", f"PIN_SUFFIX={pin_suffix}",
        "-v", f"{bpftool_real}:/usr/local/bin/bpftool:ro",
        "-v", "/lib/x86_64-linux-gnu/libelf.so.1:/lib/x86_64-linux-gnu/libelf.so.1:ro",
        "-v", f"{directory}:/work:ro", "mongo:6.0", "-lc", script,
    ])
    values = json.loads(result.stdout)[0]["values"]
    def counter(item):
        value = item["value"]
        if isinstance(value, list):
            octets = bytes(int(octet, 16) if isinstance(octet, str) else octet for octet in value)
            return int.from_bytes(octets, "little")
        return int(value)
    packets = sum(counter(item) for item in values)
    if packets < 20:
        raise SystemExit(f"XDP counter observed only {packets} packets")
    report = {
        "success": True,
        "evidence_level": "loaded Linux XDP/eBPF program in an isolated software network namespace",
        "kernel": os.uname().release,
        "clang": run([clang, "--version"]).stdout.splitlines()[0],
        "bpftool": run([bpftool, "version"]).stdout.splitlines()[0],
        "object_sha256": hashlib.sha256(object_path.read_bytes()).hexdigest(),
        "packets_observed": packets,
        "checks": {"compiled_elf_bpf": True, "kernel_verifier_accepted": True, "xdp_attached": True, "packets_counted": True},
    }
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
