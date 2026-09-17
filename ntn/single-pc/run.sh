#!/usr/bin/env bash
set -Eeuo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../.." && pwd)
# shellcheck source=versions.env
source "$lab_dir/versions.env"
mode=${1:-component}
if [[ "$mode" != component && "$mode" != --unified && "$mode" != --platform && "$mode" != --live ]]; then
  echo "usage: $0 [--unified|--platform|--live]" >&2
  exit 2
fi

stack_dir=${SF_5G_CACHE_DIR:-$repo_dir/.cache/ntn/docker_open5gs}
keep=${SF_5G_KEEP:-0}
project=starfabric-5g
live_progress() {
  if [[ "$mode" == --live ]]; then
    (cd "$repo_dir" && python3 -m lab.live.progress "$1")
  fi
}
core_services=(mongo webui nrf scp ausf udr udm pcf bsf amf smf upf)
owned_containers=(mongo webui nrf scp ausf udr udm pcf bsf amf smf upf nr_gnb nr_ue)
started=0
controller_pid=""
controller_state=$(mktemp -d /tmp/starfabric-5g-controller.XXXXXX)

core_compose=(docker compose -p "$project" -f "$stack_dir/sa-deploy.yaml" -f "$lab_dir/core-images.yaml")
gnb_compose=(docker compose -p "$project" -f "$stack_dir/nr-gnb.yaml" -f "$lab_dir/gnb-image.yaml")
ue_compose=(docker compose -p "$project" -f "$stack_dir/nr-ue.yaml" -f "$lab_dir/ue-image.yaml")

down_stack() {
  local cleanup_log="$repo_dir/reports/5g-cleanup.log"
  local remaining
  : > "$cleanup_log"
  "${ue_compose[@]}" down >>"$cleanup_log" 2>&1 || true
  "${gnb_compose[@]}" down >>"$cleanup_log" 2>&1 || true
  "${core_compose[@]}" down >>"$cleanup_log" 2>&1 || true
  # Intermediate Compose files share a network, so judge the final owned
  # resources after all three components have stopped. Keep the Mongo volume.
  remaining=$(docker ps -a -q --filter "label=com.docker.compose.project=$project") || return 1
  if [[ -n "$remaining" ]]; then
    cat "$cleanup_log" >&2
    echo "5G cleanup left owned containers: $remaining" >&2
    return 1
  fi
  remaining=$(docker network ls -q --filter "label=com.docker.compose.project=$project") || return 1
  if [[ -n "$remaining" ]]; then
    cat "$cleanup_log" >&2
    echo "5G cleanup left owned networks: $remaining" >&2
    return 1
  fi
}

cleanup() {
  status=$?
  trap - EXIT
  if [[ "$status" != 0 && "$started" == 1 ]]; then
    local diagnostics="$repo_dir/reports/5g-startup-failure-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$diagnostics"
    for container in "${owned_containers[@]}"; do
      docker logs --tail 150 "$container" >"$diagnostics/$container.log" 2>&1 || true
    done
    echo "5G failure logs: $diagnostics" >&2
  fi
  if [[ -n "$controller_pid" ]]; then kill "$controller_pid" >/dev/null 2>&1 || true; fi
  if [[ "$started" == 1 && "$keep" != 1 ]]; then
    if ! down_stack; then status=1; fi
  fi
  rm -rf "$controller_state"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'echo "5G acceptance failed at line $LINENO (exit $?)" >&2' ERR

command -v docker >/dev/null
command -v git >/dev/null
command -v curl >/dev/null
mkdir -p "$repo_dir/reports"
docker info >/dev/null
live_progress "编译 Go 控制器与检查 Rust 程序"
make -C "$repo_dir" build >/dev/null
if [[ "$mode" == --live ]]; then
  if ! python3 - "$repo_dir" <<'PY'
import sys
from pathlib import Path
root=Path(sys.argv[1])/'onboard'
binary=root/'target/release/satellite-node-runtime'
sources=[*root.joinpath('src').glob('*.rs'),root/'Cargo.toml',root/'Cargo.lock']
raise SystemExit(0 if binary.exists() and binary.stat().st_mtime >= max(p.stat().st_mtime for p in sources) else 1)
PY
  then
    make -C "$repo_dir" onboard-build
  fi
fi
if [[ "$mode" == component ]]; then
"$repo_dir/bin/sf-controller" --scenario "$repo_dir/ntn/starfabric-transport.json" \
  --state-dir "$controller_state" --listen 127.0.0.1:18081 --reconcile-interval 0 \
  >"$repo_dir/reports/5g-starfabric-controller.log" 2>&1 &
controller_pid=$!
for _ in $(seq 1 50); do
  if curl --silent --fail http://127.0.0.1:18081/readyz >/dev/null; then break; fi
  sleep 0.1
done
curl --silent --fail http://127.0.0.1:18081/readyz >/dev/null
"$repo_dir/bin/sfctl" reconcile --endpoint http://127.0.0.1:18081 >"$repo_dir/reports/5g-starfabric-initial.json"
fi

live_progress "检查 5G 实验资源与镜像缓存"
python3 "$lab_dir/prepare_lab.py" "${owned_containers[@]}"

if [[ ! -d "$stack_dir/.git" ]]; then
  mkdir -p "$(dirname "$stack_dir")"
  git clone --filter=blob:none "$UPSTREAM_REPOSITORY" "$stack_dir"
fi
if [[ $(git -C "$stack_dir" remote get-url origin) != "$UPSTREAM_REPOSITORY" ]]; then
  echo "refusing cache with unexpected origin: $stack_dir" >&2
  exit 1
fi
if ! git -C "$stack_dir" cat-file -e "$UPSTREAM_COMMIT^{commit}" 2>/dev/null; then
  git -C "$stack_dir" fetch --depth=1 origin "$UPSTREAM_COMMIT"
fi
git -C "$stack_dir" checkout --detach "$UPSTREAM_COMMIT"

for image in "$OPEN5GS_IMAGE" "$UERANSIM_IMAGE" "$MONGO_IMAGE"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then docker pull "$image"; fi
done

started=1
live_progress "启动 MongoDB 与用户配置服务"
# UDR and PCF exit if Mongo is still recovering. Compose's depends_on only
# orders container starts, so establish DB readiness before starting them.
"${core_compose[@]}" up -d mongo webui
for _ in $(seq 1 90); do
  if docker exec webui test -x /open5gs/misc/db/open5gs-dbctl >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec webui test -x /open5gs/misc/db/open5gs-dbctl

# The webui image can expose dbctl before Mongo finishes recovery/startup.
# Check the same network endpoint used by subscriber provisioning.
mongo_deadline=$((SECONDS + 90))
live_progress "等待 MongoDB 就绪，最长 90 秒"
until docker exec webui mongosh --quiet 'mongodb://172.22.0.2/open5gs?serverSelectionTimeoutMS=2000' \
  --eval 'quit(db.adminCommand({ping: 1}).ok === 1 ? 0 : 1)' >/dev/null 2>&1; do
  if (( SECONDS >= mongo_deadline )); then
    docker logs --tail 100 mongo >&2
    echo "MongoDB did not become ready for subscriber provisioning" >&2
    exit 1
  fi
  sleep 1
done

live_progress "数据库已就绪，启动 Open5GS 核心网容器"
"${core_compose[@]}" up -d "${core_services[@]}"

docker exec webui /open5gs/misc/db/open5gs-dbctl --db_uri=mongodb://172.22.0.2/open5gs remove 001011234567895 >/dev/null 2>&1 || true
docker exec webui /open5gs/misc/db/open5gs-dbctl --db_uri=mongodb://172.22.0.2/open5gs add_ue_with_apn \
  001011234567895 8baf473f2f8fd09487cccbd7097c6862 8E27B6AF0E692E750F32667A3B14605D internet

live_progress "配置测试用户并启动 gNB，等待 NG Setup"
"${gnb_compose[@]}" up -d
for _ in $(seq 1 60); do
  if docker logs nr_gnb 2>&1 | grep -q "NG Setup procedure is successful"; then break; fi
  sleep 1
done
docker logs nr_gnb 2>&1 | grep -q "NG Setup procedure is successful"

live_progress "启动 UE，等待 PDU 会话建立"
"${ue_compose[@]}" up -d
for _ in $(seq 1 60); do
  if docker logs nr_ue 2>&1 | grep -q "PDU Session establishment is successful"; then break; fi
  sleep 1
done
docker logs nr_ue 2>&1 | grep -q "PDU Session establishment is successful"

live_progress "验证 5G 基线连通性"
python3 "$lab_dir/verify.py" --output "$repo_dir/reports/5g-sa-baseline.json"
if [[ "$mode" == --live ]]; then
  python3 "$repo_dir/lab/live/runtime.py"
elif [[ "$mode" == --platform ]]; then
  python3 "$repo_dir/lab/platform/run.py"
elif [[ "$mode" == --unified ]]; then
  python3 "$repo_dir/lab/unified/run.py"
else
  python3 "$repo_dir/ntn/experiment.py" \
    --manifest "$lab_dir/ntn-transport.json" \
    --output "$repo_dir/reports/5g-ntn-transport.json"
fi

if [[ "$keep" == 1 ]]; then
  echo "5G lab left running; use ntn/single-pc/down.sh when finished"
else
  echo "5G lab passed; containers stopped, Mongo test volume retained"
fi
