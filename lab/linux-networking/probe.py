#!/usr/bin/env python3
"""Small TCP/UDP echo probe used inside disposable network namespaces."""

import argparse
import socket


def args():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("server", "client"))
    parser.add_argument("protocol", choices=("tcp", "udp"))
    parser.add_argument("address")
    parser.add_argument("port", type=int)
    return parser.parse_args()


def main():
    options = args()
    kind = socket.SOCK_STREAM if options.protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.settimeout(5)
        if options.mode == "server":
            sock.bind((options.address, options.port))
            if options.protocol == "tcp":
                sock.listen(1)
                connection, _ = sock.accept()
                with connection:
                    connection.sendall(connection.recv(64))
            else:
                payload, peer = sock.recvfrom(64)
                sock.sendto(payload, peer)
        else:
            payload = b"starfabric-linux-lab"
            if options.protocol == "tcp":
                sock.connect((options.address, options.port))
                sock.sendall(payload)
                received = sock.recv(64)
            else:
                sock.sendto(payload, (options.address, options.port))
                received, _ = sock.recvfrom(64)
            if received != payload:
                raise SystemExit("echo payload mismatch")


if __name__ == "__main__":
    main()
