#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../../.." && pwd)
artifact_dir="$lab_dir/artifacts/closed-loop"
state_dir=$(mktemp -d /tmp/starfabric-phase1-state.XXXXXX)
endpoint=http://127.0.0.1:18080
controller_pid=""
otg_pid=""
keep=${SF_LAB_KEEP:-0}

cleanup() {
  if [[ -n "$otg_pid" ]]; then kill "$otg_pid" >/dev/null 2>&1 || true; fi
  if [[ -n "$controller_pid" ]]; then kill "$controller_pid" >/dev/null 2>&1 || true; fi
  docker exec clab-sf-phase1-r1 vtysh -c "configure terminal" -c "interface eth2" -c "no shutdown" -c "end" >/dev/null 2>&1 || true
  docker exec clab-sf-phase1-r2 vtysh -c "configure terminal" -c "interface eth1" -c "no shutdown" -c "end" >/dev/null 2>&1 || true
  if [[ "$keep" != 1 ]]; then
    containerlab destroy --topo "$lab_dir/topology.clab.yml" --cleanup >/dev/null 2>&1 || true
  fi
  rm -rf "$state_dir"
}
trap cleanup EXIT

mkdir -p "$artifact_dir"
command -v containerlab >/dev/null
command -v docker >/dev/null
command -v otgen >/dev/null
command -v curl >/dev/null

containerlab deploy --reconfigure --topo "$lab_dir/topology.clab.yml"
python3 "$lab_dir/wait_ready.py"
python3 "$lab_dir/render_otg.py" "$lab_dir/otg.yaml" "$artifact_dir/otg.rendered.yaml"

make -C "$repo_dir" build >/dev/null
"$repo_dir/bin/sf-controller" \
  --scenario "$repo_dir/scenarios/frr-phase1-controller.json" \
  --adapter frr --state-dir "$state_dir" --listen 127.0.0.1:18080 \
  --otg-api https://127.0.0.1:8443 --otg-insecure \
  --otg-flow-names leo-service-forward,leo-service-reverse --otg-sample-window 250ms \
  --reconcile-interval 0 >"$artifact_dir/controller.log" 2>&1 &
controller_pid=$!
for _ in $(seq 1 50); do
  if curl --silent --fail "$endpoint/readyz" >/dev/null; then break; fi
  sleep 0.1
done
curl --silent --fail "$endpoint/readyz" >/dev/null

OTG_API=https://127.0.0.1:8443 otgen run --insecure \
  --protocols ignore --timeout 45s --xeta 2 \
  --file "$artifact_dir/otg.rendered.yaml" --yaml --metrics flow >"$artifact_dir/metrics.json" &
otg_pid=$!
"$repo_dir/bin/sfctl" reconcile --endpoint "$endpoint" >"$artifact_dir/initial-reconcile.json"
docker exec clab-sf-phase1-r1 vtysh -c "show ip route 198.51.100.0/24 json" >"$artifact_dir/fib-before.json"
sleep 3
failure_ns=$(date +%s%N)
docker exec clab-sf-phase1-r1 vtysh -c "configure terminal" -c "interface eth2" -c "shutdown" -c "end"
docker exec clab-sf-phase1-r2 vtysh -c "configure terminal" -c "interface eth1" -c "shutdown" -c "end"
"$repo_dir/bin/sfctl" event --endpoint "$endpoint" --file "$lab_dir/events/r1-r2-down.json" >"$artifact_dir/event-r1-r2.json"
"$repo_dir/bin/sfctl" event --endpoint "$endpoint" --file "$lab_dir/events/r2-r1-down.json" --reconcile >"$artifact_dir/recovered-reconcile.json"
convergence_ms=$(python3 "$lab_dir/wait_converged.py" "$failure_ns")
wait "$otg_pid"
otg_pid=""

python3 "$lab_dir/assert_metrics.py" "$artifact_dir/metrics.json" \
  --convergence-ms "$convergence_ms" --max-convergence-ms 3000 \
  --summary "$artifact_dir/traffic-summary.json"
docker exec clab-sf-phase1-r1 vtysh -c "show ip route 198.51.100.0/24 json" >"$artifact_dir/fib-after.json"
"$repo_dir/bin/sfctl" get devices --endpoint "$endpoint" >"$artifact_dir/device-state.json"
"$repo_dir/bin/sfctl" get status --endpoint "$endpoint" >"$artifact_dir/controller-status.json"

python3 "$lab_dir/assert_closed_loop.py" \
  --initial "$artifact_dir/initial-reconcile.json" \
  --recovered "$artifact_dir/recovered-reconcile.json" \
  --fib-before "$artifact_dir/fib-before.json" \
  --fib-after "$artifact_dir/fib-after.json" \
  --traffic "$artifact_dir/traffic-summary.json" \
  --convergence-ms "$convergence_ms" \
  --max-convergence-ms 3000 \
  --output "$artifact_dir/summary.json"
