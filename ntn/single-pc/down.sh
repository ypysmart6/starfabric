#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../.." && pwd)
stack_dir=${SF_5G_CACHE_DIR:-$repo_dir/.cache/ntn/docker_open5gs}
project=starfabric-5g

if [[ ! -f "$stack_dir/sa-deploy.yaml" ]]; then
  echo "single-PC 5G cache not found at $stack_dir" >&2
  exit 1
fi

docker compose -p "$project" -f "$stack_dir/nr-ue.yaml" -f "$lab_dir/ue-image.yaml" down
docker compose -p "$project" -f "$stack_dir/nr-gnb.yaml" -f "$lab_dir/gnb-image.yaml" down
docker compose -p "$project" -f "$stack_dir/sa-deploy.yaml" -f "$lab_dir/core-images.yaml" down
