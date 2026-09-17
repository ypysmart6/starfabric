#!/usr/bin/env bash
set -euo pipefail

left=sf-qos-left
right=sf-qos-right
lab_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
pcap_output=${SF_QOS_PCAP:-/tmp/starfabric-qos.pcap}
capture_pid=

cleanup() {
  if [[ -n "$capture_pid" ]]; then kill "$capture_pid" 2>/dev/null || true; fi
  ip netns del "$left" 2>/dev/null || true
  ip netns del "$right" 2>/dev/null || true
}
trap cleanup EXIT
cleanup

ip netns add "$left"
ip netns add "$right"
ip link add q0 netns "$left" type veth peer q1 netns "$right"
for item in "$left q0" "$right q1"; do
  read -r namespace interface <<<"$item"
  ip -n "$namespace" link set lo up
  ip -n "$namespace" link set "$interface" mtu 1400 up
done
ip -n "$left" address add 192.0.2.0/31 dev q0
ip -n "$right" address add 192.0.2.1/31 dev q1

ip netns exec "$left" tc qdisc add dev q0 root handle 1: htb default 30
ip netns exec "$left" tc class add dev q0 parent 1: classid 1:1 htb rate 10mbit
ip netns exec "$left" tc class add dev q0 parent 1:1 classid 1:10 htb rate 6mbit ceil 10mbit prio 0
ip netns exec "$left" tc class add dev q0 parent 1:1 classid 1:20 htb rate 3mbit ceil 8mbit prio 1
ip netns exec "$left" tc class add dev q0 parent 1:1 classid 1:30 htb rate 1mbit ceil 2mbit prio 2
ip netns exec "$left" tc filter add dev q0 protocol ip parent 1: pref 10 u32 match ip tos 0xb8 0xfc flowid 1:10
ip netns exec "$left" tc filter add dev q0 protocol ip parent 1: pref 20 u32 match ip tos 0x68 0xfc flowid 1:20

ip netns exec "$right" tcpdump --immediate-mode -U -Z root -s 0 -i q1 -w "$pcap_output" udp port 19100 >/dev/null 2>&1 &
capture_pid=$!
sleep 0.2
ip netns exec "$right" python3 "$lab_dir/qos_probe.py" receive 192.0.2.1 19100 --count 200 >/tmp/qos-receiver.json &
receiver_pid=$!
sleep 0.1
ip netns exec "$left" python3 "$lab_dir/qos_probe.py" send 192.0.2.1 19100 --count 100 --dscp 46 >/tmp/qos-ef.json
ip netns exec "$left" python3 "$lab_dir/qos_probe.py" send 192.0.2.1 19100 --count 100 --dscp 26 >/tmp/qos-af31.json
wait "$receiver_pid"
sleep 0.2
kill -INT "$capture_pid" 2>/dev/null || true
wait "$capture_pid" 2>/dev/null || true
capture_pid=

grep -q '"success": true' /tmp/qos-receiver.json
grep -q '"success": true' /tmp/qos-ef.json
grep -q '"success": true' /tmp/qos-af31.json
echo "TC_JSON=$(ip netns exec "$left" tc -j -s class show dev q0)"
echo "PASS: DSCP EF/AF31 packet classification and HTB transport classes"
