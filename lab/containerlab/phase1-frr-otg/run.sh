#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
artifact_dir="$lab_dir/artifacts"
mkdir -p "$artifact_dir"
command -v containerlab >/dev/null
command -v docker >/dev/null
command -v otgen >/dev/null

containerlab deploy --reconfigure --topo "$lab_dir/topology.clab.yml"
python3 "$lab_dir/wait_ready.py"
python3 "$lab_dir/render_otg.py" "$lab_dir/otg.yaml" "$artifact_dir/otg.rendered.yaml"

pcap_pid=""
if command -v tcpdump >/dev/null && [[ ${EUID:-1} -eq 0 ]]; then
  host_if=$(python3 "$lab_dir/render_otg.py" --host-interface)
  tcpdump -U -i "$host_if" -w "$artifact_dir/failover.pcap" >/dev/null 2>&1 &
  pcap_pid=$!
fi

OTG_API=https://127.0.0.1:8443 otgen run --insecure --protocols ignore --timeout 45s --xeta 2 --file "$artifact_dir/otg.rendered.yaml" --yaml --metrics flow >"$artifact_dir/metrics.json" &
otg_pid=$!
sleep 3
failure_ns=$(date +%s%N)
# Drive the failure through FRR so zebra/isisd originate a new LSP. A raw
# one-sided netns `ip link down` can leave a stale peer adjacency in this
# container topology and create an artificial reverse-path loop.
docker exec clab-sf-phase1-r1 vtysh -c "configure terminal" -c "interface eth2" -c "shutdown" -c "end"
docker exec clab-sf-phase1-r2 vtysh -c "configure terminal" -c "interface eth1" -c "shutdown" -c "end"
convergence_ms=$(python3 "$lab_dir/wait_converged.py" "$failure_ns")
wait "$otg_pid"
if [[ -n "$pcap_pid" ]]; then
  kill -INT "$pcap_pid" || true
  wait "$pcap_pid" || true
fi
python3 "$lab_dir/assert_metrics.py" "$artifact_dir/metrics.json" --convergence-ms "$convergence_ms" --summary "$artifact_dir/summary.json"
