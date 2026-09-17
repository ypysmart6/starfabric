#!/bin/sh
set -eu

ip link show sr0 >/dev/null 2>&1 || ip link add sr0 type dummy
ip link set sr0 up
sysctl -qw net.ipv6.conf.all.seg6_enabled=1
sysctl -qw net.ipv6.conf.default.seg6_enabled=1
for interface in lo sr0 eth1 eth2 eth3; do
  if ip link show "$interface" >/dev/null 2>&1; then
    sysctl -qw "net.ipv6.conf.${interface}.seg6_enabled=1"
  fi
done

