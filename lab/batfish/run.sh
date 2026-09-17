#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../.." && pwd)
container_name=starfabric-batfish
image=batfish/allinone@sha256:54cb0ed94fd9a3c1ca0985f73e5be479e955cee9b9f6a6799b66be3364fd8e5c
python="$repo_dir/.cache/batfish-venv/bin/python"

cleanup() {
  docker stop "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -x "$python" ]]; then
  python3 -m venv "$repo_dir/.cache/batfish-venv"
  "$python" -m pip install -r "$lab_dir/requirements.txt"
fi
bash "$lab_dir/prepare_snapshot.sh"
docker run -d --rm --name "$container_name" -p 9996:9996 "$image" >/dev/null
for _ in $(seq 1 120); do
  if curl --silent --fail http://127.0.0.1:9996/v2/version >/dev/null; then break; fi
  sleep 0.5
done
curl --silent --fail http://127.0.0.1:9996/v2/version >/dev/null
"$python" "$lab_dir/validate.py" --output "$repo_dir/reports/batfish.json"
