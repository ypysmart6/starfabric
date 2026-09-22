"""Run-scoped local FRR executor, without a Docker exec for every operation.

The private Unix socket accepts the same restricted operations as the HTTP
bridge. Target PIDs and process identities are frozen at startup; requests
cannot supply a PID, container, executable path or arbitrary shell command.
"""
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import threading
import time

from frr_agent import validate


def identity(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def execute(nodes, request, timeout):
    node, program, args = request['node'], request['program'], request['args']
    if node not in nodes:
        raise ValueError('node is not owned by this run')
    validate(program, args)
    target = nodes[node]
    if identity(target['pid']) != target['identity']:
        raise ValueError('router process identity changed')
    pid = str(target['pid'])
    # Enter both mount and network namespaces, so vtysh uses this router's
    # actual binaries and daemon sockets. No Docker socket is mounted here.
    command = ['nsenter', '--target', pid, '--mount', '--net',
               '--root=/proc/'+pid+'/root', '--wd=/', program, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return {'output': result.stdout+result.stderr, 'exit_code': result.returncode}


def serve(config_path):
    config = json.loads(Path(config_path).read_text())
    nodes = {n: {'pid': pid, 'identity': identity(pid)} for n, pid in config['nodes'].items()}
    slots = threading.BoundedSemaphore(32)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(config['timeout']+5)
            started = time.monotonic()
            acquired = None
            executing = None
            timing = {}
            try:
                raw = self.rfile.readline(512*1024+1)
                if len(raw) > 512*1024 or not raw.endswith(b'\n'):
                    raise ValueError('invalid request size')
                waiting = time.monotonic()
                with slots:
                    acquired = time.monotonic()
                    timing['slot_wait_seconds'] = acquired-waiting
                    executing = time.monotonic()
                    try:
                        result = execute(nodes, json.loads(raw), config['timeout'])
                    finally:
                        timing['execution_seconds'] = time.monotonic()-executing
            except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
                result = {'output': str(error), 'exit_code': 125}
            timing["executor_seconds"] = time.monotonic()-started
            result["timing"] = timing
            self.wfile.write(json.dumps(result).encode()+b'\n')
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
        # A canary batch opens up to 32 connections together. The stdlib
        # default backlog of 5 returns EAGAIN before requests reach workers.
        request_queue_size = 128
    with Server(config['socket'], Handler) as server:
        os.chown(config['socket'], config['uid'], config['gid'])
        os.chmod(config['socket'], 0o600)
        server.serve_forever()


def request(path, body, timeout):
    path = Path(path)
    folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout+5)
            client.connect(f'/proc/self/fd/{folder}/{path.name}')
            client.sendall(json.dumps(body).encode()+b'\n')
            with client.makefile('rb') as stream:
                return json.loads(stream.readline(4*1024*1024))
    finally:
        os.close(folder)


if __name__ == '__main__':
    serve(sys.argv[1])
