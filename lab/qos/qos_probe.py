#!/usr/bin/env python3
"""Small acknowledged UDP source/sink with an explicit six-bit DSCP."""

from __future__ import annotations

import argparse
import json
import socket


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["send", "receive"])
    parser.add_argument("address")
    parser.add_argument("port", type=int)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--dscp", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    if not 0 <= args.dscp <= 63 or args.count <= 0:
        raise SystemExit("invalid DSCP or count")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as value:
        value.settimeout(3)
        if args.mode == "receive":
            value.bind((args.address, args.port))
            labels: dict[str, int] = {}
            for _ in range(args.count):
                payload, peer = value.recvfrom(2048)
                label = payload.split(b":", 1)[0].decode()
                labels[label] = labels.get(label, 0) + 1
                value.sendto(b"ACK:" + payload, peer)
            print(json.dumps({"success": True, "received": sum(labels.values()), "labels": labels}))
            return
        value.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, args.dscp << 2)
        for sequence in range(args.count):
            payload = f"dscp-{args.dscp}:{sequence}".encode()
            value.sendto(payload, (args.address, args.port))
            reply, _ = value.recvfrom(2048)
            if reply != b"ACK:" + payload:
                raise SystemExit("invalid acknowledgement")
        print(json.dumps({"success": True, "sent": args.count, "dscp": args.dscp}))


if __name__ == "__main__":
    main()
