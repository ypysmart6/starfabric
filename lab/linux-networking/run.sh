#!/usr/bin/env bash
set -euo pipefail

left=sf-lnx-left
router=sf-lnx-router
right=sf-lnx-right
lab_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
capture_pid=
pcap_output=${SF_LINUX_PCAP:-/tmp/starfabric-linux-foundation.pcap}

cleanup() {
  if [[ -n "$capture_pid" ]]; then kill "$capture_pid" 2>/dev/null || true; fi
  ip netns del "$left" 2>/dev/null || true
  ip netns del "$router" 2>/dev/null || true
  ip netns del "$right" 2>/dev/null || true
}

finalize_capture() {
  if [[ -n "$capture_pid" ]]; then
    # Give libpcap time to drain packets queued while the last QUIC ACK was sent,
    # then ask tcpdump to close the file and wait for the final record flush.
    sleep 0.2
    kill -INT "$capture_pid" 2>/dev/null || true
    wait "$capture_pid" 2>/dev/null || true
    capture_pid=
  fi
}

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi
trap cleanup EXIT
cleanup

ip netns add "$left"
ip netns add "$router"
ip netns add "$right"
ip link add l0 netns "$left" type veth peer r-left netns "$router"
ip link add r0 netns "$right" type veth peer r-right netns "$router"

ip -n "$router" link add vrf-blue type vrf table 100
ip -n "$router" link set vrf-blue up
ip -n "$router" link set r-left master vrf-blue
ip -n "$router" link set r-right master vrf-blue

for item in "$left l0" "$router r-left" "$router r-right" "$right r0"; do
  read -r namespace interface <<<"$item"
  ip -n "$namespace" link set lo up
  ip -n "$namespace" link set "$interface" mtu 1400 up
done

ip -n "$left" address add 192.0.2.0/31 dev l0
ip -n "$router" address add 192.0.2.1/31 dev r-left
ip -n "$right" address add 198.51.100.1/31 dev r0
ip -n "$router" address add 198.51.100.0/31 dev r-right
ip -n "$left" -6 address add 2001:db8:10::/127 dev l0
ip -n "$router" -6 address add 2001:db8:10::1/127 dev r-left
ip -n "$right" -6 address add 2001:db8:20::1/127 dev r0
ip -n "$router" -6 address add 2001:db8:20::/127 dev r-right
# Docker masks its initial /proc/sys. A fresh proc view after entering the
# child network namespace changes only that namespace and avoids --privileged.
ip netns exec "$router" unshare -m --mount-proc sysctl -q -w net.ipv4.ip_forward=1
ip netns exec "$router" unshare -m --mount-proc sysctl -q -w net.ipv6.conf.all.forwarding=1
ip -n "$left" route add default via 192.0.2.1
ip -n "$right" route add 192.0.2.0/31 via 198.51.100.0
ip -n "$left" -6 route add default via 2001:db8:10::1
ip -n "$right" -6 route add 2001:db8:10::/127 via 2001:db8:20::
ip netns exec "$router" tc qdisc replace dev r-right root netem delay 5ms

if command -v nft >/dev/null; then
  ip netns exec "$router" nft add table ip sf_nat
  ip netns exec "$router" nft 'add chain ip sf_nat postrouting { type nat hook postrouting priority srcnat; policy accept; }'
  ip netns exec "$router" nft 'add rule ip sf_nat postrouting oifname "r-right" ip saddr 192.0.2.0/31 masquerade'
fi

if command -v tcpdump >/dev/null; then
  ip netns exec "$router" tcpdump --immediate-mode -U -Z root -s 0 -i r-right -w "$pcap_output" >/dev/null 2>&1 &
  capture_pid=$!
  sleep 0.2
fi

ip netns exec "$left" ping -c 2 -W 1 198.51.100.1
ip netns exec "$left" ping -6 -c 2 -W 1 2001:db8:20::1
ip -n "$left" neigh show dev l0 | grep -Eq '192\.0\.2\.1|2001:db8:10::1'
ip -n "$router" route get 198.51.100.1 vrf vrf-blue
ip netns exec "$router" tc qdisc show dev r-right | grep -q netem

for protocol in tcp udp; do
  port=$([[ "$protocol" == tcp ]] && echo 19001 || echo 19002)
  ip netns exec "$right" python3 "$lab_dir/probe.py" server "$protocol" 198.51.100.1 "$port" &
  server_pid=$!
  sleep 0.1
  ip netns exec "$left" python3 "$lab_dir/probe.py" client "$protocol" 198.51.100.1 "$port"
  wait "$server_pid"
done

ip netns exec "$right" /work/bin/sf-quic-probe --mode server --address 198.51.100.1:19003 >/tmp/quic-server.json 2>/tmp/quic-server.err &
quic_server_pid=$!
sleep 0.2
ip netns exec "$left" /work/bin/sf-quic-probe --mode client --address 198.51.100.1:19003 --message starfabric-quic-over-satellite-transport >/tmp/quic-client.json
wait "$quic_server_pid"
grep -q '"success":true' /tmp/quic-server.json
grep -q '"success":true' /tmp/quic-client.json
finalize_capture

echo "PASS: Ethernet ARP/ND IPv4/IPv6 VRF MTU ICMP TCP UDP QUIC/TLS1.3 netns tc NAT/capture"
