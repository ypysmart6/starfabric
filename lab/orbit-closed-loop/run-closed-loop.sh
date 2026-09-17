#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../.." && pwd)
phase1_dir="$repo_dir/lab/containerlab/phase1-frr-otg"
artifact_dir="$lab_dir/artifacts"
oem_dir="$artifact_dir/oem"
state_dir=$(mktemp -d /tmp/starfabric-orbit-state.XXXXXX)
endpoint=http://127.0.0.1:18080
controller_pid=""
otg_pid=""
keep=${SF_LAB_KEEP:-0}

cleanup() {
  if [[ -n "$otg_pid" ]]; then kill "$otg_pid" >/dev/null 2>&1 || true; fi
  if [[ -n "$controller_pid" ]]; then kill "$controller_pid" >/dev/null 2>&1 || true; fi
  docker exec clab-sf-phase1-r2 vtysh -c "configure terminal" -c "interface eth2" -c "no shutdown" -c "end" >/dev/null 2>&1 || true
  docker exec clab-sf-phase1-r4 vtysh -c "configure terminal" -c "interface eth1" -c "no shutdown" -c "end" >/dev/null 2>&1 || true
  if [[ "$keep" != 1 ]]; then
    containerlab destroy --topo "$phase1_dir/topology.clab.yml" --cleanup >/dev/null 2>&1 || true
  fi
  rm -rf "$state_dir"
}
trap cleanup EXIT

mkdir -p "$artifact_dir" "$oem_dir" "$repo_dir/reports"
command -v containerlab >/dev/null
command -v docker >/dev/null
command -v otgen >/dev/null
command -v curl >/dev/null
python3 -c 'import sgp4'

epoch=2026-09-04T00:00:00Z
python3 "$repo_dir/tools/tle_to_oem.py" \
  --catalog "$lab_dir/inputs/tle-catalog.json" --start "$epoch" \
  --duration-seconds 86400 --step-seconds 10 --output-dir "$oem_dir"
python3 "$repo_dir/tools/ephemeris_contacts.py" \
  --oem "orbit-a=$oem_dir/orbit-a.oem" --oem "orbit-b=$oem_dir/orbit-b.oem" \
  --ground-stations "$lab_dir/inputs/ground-stations.json" \
  --minimum-elevation-deg 10 --carrier-hz 20000000000 \
  --output "$artifact_dir/contacts.csv"
python3 "$repo_dir/tools/contactplan.py" \
  --base "$lab_dir/inputs/contact-plan-base.json" --contacts "$artifact_dir/contacts.csv" \
  --output "$artifact_dir/full-contact-scenario.json"
python3 "$lab_dir/compile_replay.py" \
  --contacts "$artifact_dir/contacts.csv" --catalog "$lab_dir/inputs/tle-catalog.json" \
  --contact-scenario "$artifact_dir/full-contact-scenario.json" \
  --oem "$oem_dir/orbit-a.oem" --oem "$oem_dir/orbit-b.oem" --epoch "$epoch" \
  --scenario "$artifact_dir/live-scenario.json" \
  --event-forward "$artifact_dir/contact-end-forward.json" \
  --event-reverse "$artifact_dir/contact-end-reverse.json" \
  --selection "$artifact_dir/orbit-selection.json"

containerlab deploy --reconfigure --topo "$phase1_dir/topology.clab.yml"
python3 "$phase1_dir/wait_ready.py"
python3 "$phase1_dir/render_otg.py" "$phase1_dir/otg.yaml" "$artifact_dir/otg.rendered.yaml"

make -C "$repo_dir" build >/dev/null
"$repo_dir/bin/sf-controller" \
  --scenario "$artifact_dir/live-scenario.json" --adapter frr \
  --state-dir "$state_dir" --listen 127.0.0.1:18080 --reconcile-interval 0 \
  --otg-api https://127.0.0.1:8443 --otg-insecure \
  --otg-flow-names leo-service-forward,leo-service-reverse --otg-sample-window 250ms \
  >"$artifact_dir/controller.log" 2>&1 &
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
docker exec clab-sf-phase1-r2 vtysh -c "show ip route 198.51.100.0/24 json" >"$artifact_dir/fib-before.json"

python3 "$lab_dir/render_prediction.py" \
  --scenario "$artifact_dir/live-scenario.json" \
  --output "$artifact_dir/predictive-request.json" --timing "$artifact_dir/replay-timing.json"
curl --silent --fail --header 'Content-Type: application/json' \
  --data-binary "@$artifact_dir/predictive-request.json" \
  "$endpoint/api/v1/predictive/schedules" >"$artifact_dir/predictive-created.json"
python3 "$lab_dir/wait_prediction.py" --endpoint "$endpoint" \
  --created "$artifact_dir/predictive-created.json" \
  --schedule-output "$artifact_dir/predictive-final.json" \
  --reconcile-output "$artifact_dir/recovered-reconcile.json"
python3 "$lab_dir/wait_contact_end.py" "$artifact_dir/replay-timing.json"
failure_ns=$(date +%s%N)
docker exec clab-sf-phase1-r2 vtysh -c "configure terminal" -c "interface eth2" -c "shutdown" -c "end"
docker exec clab-sf-phase1-r4 vtysh -c "configure terminal" -c "interface eth1" -c "shutdown" -c "end"
convergence_ms=$(python3 "$lab_dir/wait_converged.py" "$failure_ns")
wait "$otg_pid"
otg_pid=""

python3 "$phase1_dir/assert_metrics.py" "$artifact_dir/metrics.json" \
  --convergence-ms "$convergence_ms" --max-convergence-ms 3000 \
  --summary "$artifact_dir/traffic-summary.json"
docker exec clab-sf-phase1-r2 vtysh -c "show ip route 198.51.100.0/24 json" >"$artifact_dir/fib-after.json"
python3 "$lab_dir/assert_closed_loop.py" \
  --initial "$artifact_dir/initial-reconcile.json" \
  --predicted "$artifact_dir/recovered-reconcile.json" \
  --schedule "$artifact_dir/predictive-final.json" \
  --scenario "$artifact_dir/live-scenario.json" \
  --timing "$artifact_dir/replay-timing.json" \
  --fib-before "$artifact_dir/fib-before.json" --fib-after "$artifact_dir/fib-after.json" \
  --traffic "$artifact_dir/traffic-summary.json" --convergence-ms "$convergence_ms" \
  --max-convergence-ms 500 --output "$artifact_dir/network-summary.json"
python3 "$lab_dir/assemble_report.py" \
  --selection "$artifact_dir/orbit-selection.json" \
  --network "$artifact_dir/network-summary.json" \
  --scenario "$artifact_dir/live-scenario.json" \
  --output "$repo_dir/reports/orbit-network-closed-loop.json"
