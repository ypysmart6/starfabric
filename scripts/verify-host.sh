#!/usr/bin/env bash

set -Eeuo pipefail

readonly EXPECTED_GO_VERSION="go1.26.5"
readonly EXPECTED_CLAB_VERSION="0.79.0"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

command -v go >/dev/null || fail "go is not in PATH"
command -v docker >/dev/null || fail "docker is not in PATH"
command -v containerlab >/dev/null || fail "containerlab is not in PATH"

go version | grep -Fq "${EXPECTED_GO_VERSION}" \
  || fail "expected Go ${EXPECTED_GO_VERSION}"
# containerlab writes its banner/version to stderr in recent releases.
containerlab version 2>&1 | grep -Fq "${EXPECTED_CLAB_VERSION}" \
  || fail "expected containerlab ${EXPECTED_CLAB_VERSION}"
docker info >/dev/null \
  || fail "Docker daemon is unavailable or this login lacks Docker access"
docker compose version >/dev/null \
  || fail "Docker Compose plugin is unavailable"

if [[ "$(sysctl -n net.ipv6.conf.all.disable_ipv6)" == "1" ]]; then
  fail "IPv6 is disabled; later IS-IS/SRv6 labs require it"
fi

echo "PASS: Go ${EXPECTED_GO_VERSION}"
echo "PASS: Docker daemon is reachable"
echo "PASS: Docker Compose plugin is available"
echo "PASS: containerlab ${EXPECTED_CLAB_VERSION}"
echo "PASS: IPv6 is enabled"
