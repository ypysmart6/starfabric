#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../../.." && pwd)
keep=${SF_LAB_KEEP:-0}

cleanup() {
  if [[ "$keep" != 1 ]]; then
    containerlab destroy --topo "$lab_dir/topology.clab.yml" --cleanup >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

command -v docker >/dev/null
command -v containerlab >/dev/null
if [[ ! -e /proc/sys/net/mpls/platform_labels ]]; then
  echo "Linux MPLS kernel support is not loaded." >&2
  echo "After reviewing the host impact, load it once with:" >&2
  echo "  sudo modprobe mpls_router mpls_iptunnel mpls_gso" >&2
  echo "Then rerun: make test-protocol-live" >&2
  exit 2
fi

containerlab deploy --reconfigure --topo "$lab_dir/topology.clab.yml"
python3 "$lab_dir/closed_loop.py" --output "$repo_dir/reports/protocol-matrix-closed-loop.json"
