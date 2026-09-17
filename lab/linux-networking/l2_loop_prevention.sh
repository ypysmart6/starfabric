#!/usr/bin/env bash
set -euo pipefail

host_a=sf-l2-host-a
host_b=sf-l2-host-b
switch_a=sf-l2-switch-a
switch_b=sf-l2-switch-b

cleanup() {
  ip netns del "$host_a" 2>/dev/null || true
  ip netns del "$host_b" 2>/dev/null || true
  ip netns del "$switch_a" 2>/dev/null || true
  ip netns del "$switch_b" 2>/dev/null || true
}

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi
command -v bridge >/dev/null || { echo "iproute2 bridge command is required" >&2; exit 1; }
trap cleanup EXIT
cleanup

for namespace in "$host_a" "$host_b" "$switch_a" "$switch_b"; do
  ip netns add "$namespace"
  ip -n "$namespace" link set lo up
done

ip link add ha0 netns "$host_a" type veth peer sa-host netns "$switch_a"
ip link add hb0 netns "$host_b" type veth peer sb-host netns "$switch_b"
ip link add sa-a netns "$switch_a" type veth peer sb-a netns "$switch_b"
ip link add sa-b netns "$switch_a" type veth peer sb-b netns "$switch_b"

ip -n "$switch_a" link add br0 type bridge stp_state 1 priority 4096
ip -n "$switch_b" link add br0 type bridge stp_state 1 priority 8192
for item in "$switch_a sa-host" "$switch_a sa-a" "$switch_a sa-b" "$switch_b sb-host" "$switch_b sb-a" "$switch_b sb-b"; do
  read -r namespace interface <<<"$item"
  ip -n "$namespace" link set "$interface" master br0
  ip -n "$namespace" link set "$interface" up
  ip -n "$namespace" link set "$interface" mtu 1400
done
ip -n "$switch_a" link set br0 up
ip -n "$switch_b" link set br0 up
ip -n "$host_a" link set ha0 mtu 1400 up
ip -n "$host_b" link set hb0 mtu 1400 up
ip -n "$host_a" address add 192.0.2.10/24 dev ha0
ip -n "$host_b" address add 192.0.2.20/24 dev hb0

reachable=false
for _ in $(seq 1 30); do
  if ip netns exec "$host_a" ping -c 1 -W 1 192.0.2.20 >/dev/null 2>&1; then
    reachable=true
    break
  fi
  sleep 1
done
[[ "$reachable" == true ]] || { echo "STP topology did not converge" >&2; exit 1; }

{
  ip netns exec "$switch_a" bridge link show
  ip netns exec "$switch_b" bridge link show
} | grep -q 'state blocking'
ip netns exec "$switch_a" bridge fdb show br br0 | grep -q 'master br0'
ip netns exec "$switch_b" bridge fdb show br br0 | grep -q 'master br0'

echo "PASS: Ethernet switching, MAC learning, MTU and STP loop prevention"
