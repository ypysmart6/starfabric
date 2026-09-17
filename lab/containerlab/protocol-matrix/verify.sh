#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$lab_dir/../../.." && pwd)
python3 "$lab_dir/closed_loop.py" --output "$repo_dir/reports/protocol-matrix-closed-loop.json"
