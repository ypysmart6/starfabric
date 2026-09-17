#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$root/snapshot/configs"
cp "$root/model-configs/"r{1,2,3,4}.conf "$root/snapshot/configs/"
