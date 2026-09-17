"""Authenticated, run-scoped FRR execution bridge for controller pods.

No shell, user-supplied container names or unrestricted executable dispatch.
Only the FRR adapter's route operations and fixed one-packet probes are accepted.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def validate(program: str, args: list[str]) -> None:
    if not isinstance(args, list) or not args or len(args) > 4096 or any(not isinstance(a, str) or len(a) > 512 for a in args):
        raise ValueError("invalid argument list")
    if program == "ping":
        if args[:-1] not in (["-n", "-c", "1", "-W", "1"], ["-n", "-c", "1", "-W", "1", "-6"]):
            raise ValueError("only one-packet probes are allowed")
        ipaddress.ip_address(args[-1])
    elif program == "vtysh":
        if len(args) % 2 or any(args[i] != "-c" for i in range(0, len(args), 2)):
            raise ValueError("only vtysh -c commands are allowed")
        fixed = {"show version", "show ip route static json", "show ipv6 route static json", "configure terminal", "end"}
        for value in args[1::2]:
            if value in fixed:
                continue
            if not re.fullmatch(r"(?:no )?(?:ip|ipv6) route [0-9a-fA-F:./]+ [0-9a-fA-F:.]+(?: [0-9]+)?", value):
                raise ValueError("unsupported FRR adapter command")
    else:
        raise ValueError("unsupported executable")


class Agent:
    def __init__(self, runtime, host: str, token: str):
        self.runtime, self.token = runtime, token
        self.nodes = {n["id"]: runtime.router(n["id"]) for n in runtime.scenario["topology"]["nodes"]}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path != "/v1/execute":
                    self.send_error(404)
                    return
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner.token):
                    self.send_error(401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 512 * 1024:
                        raise ValueError("invalid body length")
                    body = json.loads(self.rfile.read(length))
                    node, program, args = body["node"], body["program"], body["args"]
                    if node not in owner.nodes:
                        raise ValueError("node is not owned by this run")
                    validate(program, args)
                    result = subprocess.run(["docker", "exec", owner.nodes[node], program, *args],
                                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=getattr(runtime, "frr_operation_timeout", 1.5))
                    data = json.dumps({"output": result.stdout, "exit_code": result.returncode}).encode()
                    with owner.lock:
                        with (runtime.art / "frr-agent.jsonl").open("a") as stream:
                            stream.write(json.dumps({"run_id": runtime.identifier, "node": node, "program": program,
                                                     "args": args, "exit_code": result.returncode}) + "\n")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (ValueError, KeyError, TypeError):
                    self.send_error(400, "invalid execution request")
                except subprocess.TimeoutExpired:
                    self.send_error(504, "router operation timed out")

        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.server.daemon_threads = True
        self.endpoint = f"http://{host}:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
