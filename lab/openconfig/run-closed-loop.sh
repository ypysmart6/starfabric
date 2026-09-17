#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../.." && pwd)
artifact_dir="$lab_dir/artifacts"
state_dir=$(mktemp -d /tmp/starfabric-openconfig-state.XXXXXX)
endpoint=http://127.0.0.1:19380
controller_pid=""
target_pids=()

stop_process() {
  local pid=$1
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  if [[ -n "$controller_pid" ]]; then stop_process "$controller_pid"; fi
  for pid in "${target_pids[@]}"; do stop_process "$pid"; done
  rm -rf "$state_dir"
}
trap cleanup EXIT

mkdir -p "$artifact_dir" "$repo_dir/reports"
for specification in "edge:19339" "sat-primary:19340" "sat-backup:19341" "gateway:19342"; do
  node=${specification%%:*}
  port=${specification##*:}
  "$repo_dir/bin/sf-openconfig-emulator" --node "$node" --listen "127.0.0.1:$port" \
    >"$artifact_dir/$node.log" 2>&1 &
  target_pids+=("$!")
done

probe_ready=false
for _ in $(seq 1 50); do
  if "$repo_dir/bin/sf-openconfig-probe" --endpoint 127.0.0.1:19339 \
    >"$artifact_dir/management-probe.json" 2>"$artifact_dir/management-probe.err"; then
    probe_ready=true
    break
  fi
  sleep 0.1
done
[[ "$probe_ready" == true ]] || { cat "$artifact_dir/management-probe.err" >&2; exit 1; }

start_controller() {
  "$repo_dir/bin/sf-controller" --scenario "$lab_dir/scenario.json" \
    --adapter openconfig --openconfig-insecure --openconfig-require-gnoi \
    --state-dir "$state_dir" --listen 127.0.0.1:19380 --reconcile-interval 0 \
    >"$artifact_dir/controller.log" 2>&1 &
  controller_pid=$!
  for _ in $(seq 1 80); do
    if curl --silent --fail "$endpoint/readyz" >/dev/null; then return; fi
    sleep 0.1
  done
  curl --silent --fail "$endpoint/readyz" >/dev/null
}

start_controller
"$repo_dir/bin/sfctl" reconcile --endpoint "$endpoint" >"$artifact_dir/initial.json"
"$repo_dir/bin/sfctl" event --endpoint "$endpoint" --file "$lab_dir/event-primary-degrade.json" \
  --reconcile >"$artifact_dir/switched.json"
"$repo_dir/bin/sfctl" get devices --endpoint "$endpoint" >"$artifact_dir/devices.json"

stop_process "$controller_pid"
controller_pid=""
start_controller
"$repo_dir/bin/sfctl" get devices --endpoint "$endpoint" >"$artifact_dir/restarted-devices.json"
"$repo_dir/bin/sfctl" get status --endpoint "$endpoint" >"$artifact_dir/restarted-status.json"

python3 "$lab_dir/assert_closed_loop.py" \
  --initial "$artifact_dir/initial.json" --switched "$artifact_dir/switched.json" \
  --devices "$artifact_dir/devices.json" --restarted-devices "$artifact_dir/restarted-devices.json" \
  --restarted-status "$artifact_dir/restarted-status.json" \
  --management-probe "$artifact_dir/management-probe.json" \
  --output "$repo_dir/reports/openconfig-closed-loop.json"
