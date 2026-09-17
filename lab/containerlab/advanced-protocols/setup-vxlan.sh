#!/bin/sh
set -eu

case "$(hostname)" in
  vtep-a) vtep_ip=10.255.1.1 ;;
  vtep-b) vtep_ip=10.255.1.2 ;;
  *) echo "unsupported VTEP hostname" >&2; exit 2 ;;
esac

ip address replace "${vtep_ip}/32" dev lo
ip link show br100 >/dev/null 2>&1 || ip link add br100 type bridge
ip link set br100 up
ip link show vni100 >/dev/null 2>&1 || \
  ip link add vni100 type vxlan id 100 local "$vtep_ip" dstport 4789 nolearning
ip link set vni100 master br100
ip link set vni100 up
ip link set eth3 master br100
ip link set eth3 up
