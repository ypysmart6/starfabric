#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../.." && pwd)
artifact_dir="$lab_dir/artifacts"
container_name=starfabric-p4-bmv2
p4c_image=p4lang/p4c@sha256:cce724714a077ed6b476a2a66ca6906fa19dce4d8b385be91e61dfa359477bd3
p4runtime_python="$repo_dir/.cache/p4runtime-venv/bin/python"

cleanup() {
  docker stop "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "$artifact_dir" "$repo_dir/reports"
if [[ ! -x "$p4runtime_python" ]]; then
  python3 -m venv "$repo_dir/.cache/p4runtime-venv"
  "$p4runtime_python" -m pip install -r "$lab_dir/requirements.txt"
fi

make -C "$lab_dir" build
python3 "$lab_dir/packet_probe.py" generate --directory "$artifact_dir"

docker run -d --rm --name "$container_name" -p 19559:9559 \
  -v "$lab_dir:/work" -w /work/artifacts "$p4c_image" \
  simple_switch_grpc --no-p4 --use-files 8 -i 1@port1 -i 2@port2 \
  --device-id 1 --log-console -- --grpc-server-addr 0.0.0.0:9559 >/dev/null

for _ in $(seq 1 80); do
  if "$p4runtime_python" -c 'import socket; s=socket.create_connection(("127.0.0.1",19559),.1); s.close()' \
      >/dev/null 2>&1; then break; fi
  sleep 0.1
done
"$p4runtime_python" "$lab_dir/program.py" --address 127.0.0.1:19559 \
  --p4info "$lab_dir/build/starfabric.p4info.txtpb" --pipeline "$lab_dir/build/starfabric.json" \
  --wait-seconds 10 --report "$artifact_dir/p4runtime.json"
docker stop "$container_name" >/dev/null

python3 "$lab_dir/packet_probe.py" verify --directory "$artifact_dir" \
  --p4runtime-report "$artifact_dir/p4runtime.json" --output "$repo_dir/reports/p4-closed-loop.json"
