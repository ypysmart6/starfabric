#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../../.." && pwd)
keep=${SF_LAB_KEEP:-0}
control_topology="$lab_dir/control.clab.yml"
evpn_topology="$lab_dir/evpn.clab.yml"
control_report="$repo_dir/reports/advanced-control-closed-loop.json"
evpn_report="$repo_dir/reports/evpn-vxlan-closed-loop.json"
aggregate_report="$repo_dir/reports/advanced-protocols-closed-loop.json"

cleanup() {
  if [[ "$keep" != 1 ]]; then
    containerlab destroy --topo "$control_topology" --cleanup >/dev/null 2>&1 || true
    containerlab destroy --topo "$evpn_topology" --cleanup >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

command -v docker >/dev/null
command -v containerlab >/dev/null
if [[ ! -e /proc/sys/net/ipv6/conf/all/seg6_enabled ]]; then
  echo "Linux SRv6 support is unavailable in this kernel." >&2
  exit 2
fi

mkdir -p "$repo_dir/reports" "$lab_dir/artifacts"

# A failed first component must not leave an older successful aggregate or
# unexecuted component report looking like evidence from this invocation.
python3 - "$control_report" "$evpn_report" "$aggregate_report" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

for name in sys.argv[1:]:
    Path(name).write_text(json.dumps({
        "schema": 1, "success": False, "status": "not-completed",
        "measured_at": datetime.now(timezone.utc).isoformat(), "checks": {},
    }, indent=2) + "\n")
PY

containerlab deploy --reconfigure --topo "$control_topology"
python3 "$lab_dir/control_loop.py" \
  --output "$control_report" \
  --pcep-events "$lab_dir/artifacts/pcep-events.jsonl"
containerlab destroy --topo "$control_topology" --cleanup

containerlab deploy --reconfigure --topo "$evpn_topology"
python3 "$lab_dir/evpn_loop.py" --output "$evpn_report"
containerlab destroy --topo "$evpn_topology" --cleanup

python3 "$lab_dir/aggregate.py" \
  --control "$control_report" \
  --evpn "$evpn_report" \
  --output "$aggregate_report"
