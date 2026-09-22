"""Authenticated, run-scoped FRR execution bridge for controller pods.

No shell, user-supplied container names or unrestricted executable dispatch.
Only the FRR adapter's route operations and fixed one-packet probes are accepted.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import subprocess
import threading
import os
import time
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
        self.executor = None
        self.executor_socket = None
        self.nodes = {n["id"]: runtime.router(n["id"]) for n in runtime.scenario["topology"]["nodes"]}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            # Headers and the small JSON body are separate writes. With
            # persistent connections Nagle otherwise waits for a delayed ACK
            # on every route operation, multiplied across device batches.
            disable_nagle_algorithm = True

            def setup(self):
                super().setup()
                self.connection.settimeout(30)

            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path != "/v1/execute":
                    self.send_error(404)
                    return
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner.token):
                    self.send_error(401)
                    return
                started = time.monotonic()
                record = None
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 512 * 1024:
                        raise ValueError("invalid body length")
                    body = json.loads(self.rfile.read(length))
                    node, program, args = body["node"], body["program"], body["args"]
                    if node not in owner.nodes:
                        raise ValueError("node is not owned by this run")
                    validate(program, args)
                    record = {"run_id":runtime.identifier, "node":node, "program":program, "args":args,
                              "started_at":time.time(), "exit_code":None,
                              "correlation":{key:self.headers.get(header, '')[:128] for key,header in {
                                  "request_id":"X-Request-ID", "frame_id":"X-Starfabric-Frame-ID", "plan_id":"X-Starfabric-Plan-ID",
                                  "phase":"X-Starfabric-Phase", "attempt":"X-Starfabric-Attempt"}.items()}}
                    timeout = getattr(runtime, "frr_operation_timeout", 1.5)
                    if owner.executor_socket:
                        from frr_executor import request
                        result = request(owner.executor_socket, body, timeout)
                    else:
                        process = subprocess.run(["docker", "exec", owner.nodes[node], program, *args],
                                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
                        result = {"output": process.stdout, "exit_code": process.returncode}
                    data = json.dumps(result).encode()
                    record.update(exit_code=result['exit_code'], response_bytes=len(data), executor_timing=result.get('timing'))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (ValueError, KeyError, TypeError):
                    self.send_error(400, "invalid execution request")
                except (OSError, subprocess.TimeoutExpired):
                    self.send_error(504, "router operation timed out")
                finally:
                    if record is not None:
                        record.update(finished_at=time.time(), duration_seconds=time.monotonic()-started)
                        owner.log.info(json.dumps(record, separators=(',', ':')))

        # Rotate at the only writer, under the logging handler's lock. The
        # driver's old truncate-in-place could race with active requests and
        # discard early frames of a 600-second measurement.
        self.log = logging.Logger(str(runtime.art/'frr-agent.jsonl'))
        self.log_handler = RotatingFileHandler(runtime.art/'frr-agent.jsonl',
                                               maxBytes=8*1024*1024, backupCount=8)
        self.log_handler.setFormatter(logging.Formatter('%(message)s'))
        self.log.addHandler(self.log_handler)
        class Server(ThreadingHTTPServer):
            # The controller's device batches open connections together.
            # The default backlog of 5 causes TCP SYN retries (1..3 seconds)
            # before a cheap route read even reaches the execution handler.
            request_queue_size = 128
        self.server = Server((host, 0), Handler)
        self.server.daemon_threads = True
        self.endpoint = f"http://{host}:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def start_executor(self):
        from run import command, save, ROOT, TOOLS
        r = self.runtime
        folder = r.art/'frr-executor'
        folder.mkdir()
        # Unix socket names have a small fixed bound; keep the in-container
        # and host paths independent of the (possibly long) workspace path.
        path = folder/'rpc.sock'
        save(folder/'config.json', {'nodes': {n:r.pids[n] for n in self.nodes},
             'uid':os.getuid(), 'gid':os.getgid(), 'socket':'/work/rpc.sock',
             'timeout':getattr(r,'frr_operation_timeout',20)})
        self.executor = r.router('frr-executor')
        command('docker','run','--rm','-d','--name',self.executor,
                '--label','starfabric.run='+r.identifier,'--network=none','--pid=host',
                '--privileged','--cpuset-cpus',r.fabric_cpus,
                '-v',f'{folder}:/work','-v',f'{ROOT}/lab/platform:/scripts:ro',
                '--entrypoint','python3',TOOLS,'/scripts/frr_executor.py','/work/config.json')
        until = time.monotonic()+15
        while not path.exists():
            if time.monotonic() >= until:
                raise RuntimeError('FRR executor did not create its socket')
            time.sleep(.1)
        self.executor_socket = path

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.log_handler.close()
        if self.executor:
            from run import command
            command('docker','rm','-f',self.executor,check=False)
