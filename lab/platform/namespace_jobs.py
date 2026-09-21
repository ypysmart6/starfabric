"""Persistent run-owned namespace worker for contact and channel updates.

Only manifests inside this run's evidence directory and the frozen router PIDs
are accepted. The socket is local, mode 0600, and no Docker socket is mounted.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import sys
import threading
import time

from frr_executor import identity, request
import physics_jobs
import wire


def contact_commands(commands):
    fixed = {'exit', 'mpls ldp', 'address-family ipv4', 'exit-address-family',
             'ip router isis SF', 'ipv6 router isis SF', 'isis network point-to-point',
             'ip ospf area 0.0.0.0', 'ip ospf network point-to-point',
             'ipv6 ospf6 area 0.0.0.0', 'ipv6 ospf6 network point-to-point',
             'link-params', 'exit-link-params'}
    numeric = r'(?:isis (?:hello-interval|hello-multiplier|metric level-2)|(?:ip ospf|ipv6 ospf6) (?:hello-interval|dead-interval|cost)) [0-9]+'
    for command in commands:
        if not isinstance(command, str) or not (command in fixed or
                re.fullmatch(r'(?:no )?interface sf[0-9]+', command) or
                re.fullmatch(numeric, command) or
                re.fullmatch(r'max-bw [0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?', command)):
            raise ValueError('unsupported contact configuration')


class Dispatcher:
    def __init__(self, config):
        self.folder = Path(config['folder']).resolve()
        self.nodes = {name: {'pid': pid, 'identity': identity(pid)} for name, pid in config['nodes'].items()}
        self.pids = {record['pid']: record for record in self.nodes.values()}

    def target(self, pid):
        if type(pid) is not int or pid not in self.pids:
            raise ValueError('namespace is not owned by this run')
        if identity(pid) != self.pids[pid]['identity']:
            raise ValueError('router process identity changed')

    def execute(self, body):
        filename = body['manifest']
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError('manifest must be a local evidence filename')
        path = (self.folder / filename).resolve()
        if path.parent != self.folder:
            raise ValueError('manifest escapes evidence directory')
        payload = json.loads(path.read_text())
        mode = body['mode']
        if mode not in ('configure', 'channels', 'wire'):
            raise ValueError('unsupported manifest mode')
        workers = body.get('workers', 8)
        if type(workers) is not int or not 1 <= workers <= 32:
            raise ValueError('invalid worker count')
        if mode == 'configure':
            for node, commands in payload.items():
                self.target(self.nodes[node]['pid'])
                contact_commands(commands)
            def configure(item):
                node, commands = item
                pid = str(self.nodes[node]['pid'])
                args = [part for value in ['configure terminal', *commands, 'end'] for part in ('-c', value)]
                result = subprocess.run(['nsenter', '--target', pid, '--mount', '--net',
                    '--root=/proc/'+pid+'/root', '--wd=/', 'vtysh', *args],
                    capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise RuntimeError(f'{node}: {result.stdout} {result.stderr}')
                return node
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                return {'configured': list(pool.map(configure, payload.items()))}
        jobs = payload if mode == 'channels' else [job for group in payload for job in group]
        for job in jobs:
            self.target(job['pid'])
            commands = job['commands'] if mode == 'channels' else [job['args']]
            for args in commands:
                if not isinstance(args, list) or not args or args[0] not in ('ip', 'tc', 'sysctl') or any(not isinstance(a, str) or '\n' in a for a in args):
                    raise ValueError('unsupported namespace command')
                if 'netns' in args:
                    self.target(int(args[args.index('netns')+1]))
        if mode == 'channels':
            return physics_jobs.execute(payload, workers)
        if mode == 'wire':
            return wire.execute_groups(payload, workers)
        raise ValueError('unsupported manifest mode')


def serve(config_path):
    config = json.loads(Path(config_path).read_text())
    dispatcher = Dispatcher(config)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(305)
            try:
                raw = self.rfile.readline(65537)
                if len(raw) > 65536 or not raw.endswith(b'\n'):
                    raise ValueError('invalid request')
                result = {'ok': True, 'result': dispatcher.execute(json.loads(raw))}
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as error:
                result = {'ok': False, 'error': str(error)}
            self.wfile.write(json.dumps(result).encode()+b'\n')
    # Serialize manifests; each manifest manages its own bounded parallelism.
    with socketserver.UnixStreamServer(config['socket'], Handler) as server:
        os.chown(config['socket'], config['uid'], config['gid'])
        os.chmod(config['socket'], 0o600)
        server.serve_forever()


class NamespaceJobs:
    def __init__(self, runtime):
        self.runtime = runtime
        self.socket = runtime.art / 'namespace-jobs' / 'rpc.sock'
        self.lock = threading.Lock()
        self.started = False

    def start(self):
        from run import command, save, ROOT, TOOLS
        r = self.runtime
        with self.lock:
            if self.started:
                return
            folder = self.socket.parent
            folder.mkdir(exist_ok=True)
            save(folder/'config.json', {'folder': '/evidence', 'nodes': {n:r.pids[n] for n in r.fabric.nodes},
                'socket': '/evidence/namespace-jobs/rpc.sock', 'uid': os.getuid(), 'gid': os.getgid()})
            name = r.router('namespace-jobs')
            command('docker', 'run', '--rm', '-d', '--name', name, '--privileged', '--pid=host',
                '--network=none', '--label', 'starfabric.run='+r.identifier, '--cpuset-cpus', r.fabric_cpus,
                '-v', f'{r.art}:/evidence', '-v', f'{ROOT}/lab/platform:/scripts:ro', '--entrypoint', 'python3',
                TOOLS, '/scripts/namespace_jobs.py', '/evidence/namespace-jobs/config.json')
            # The existing run cleanup removes every registered helper, also
            # when startup fails before the socket becomes ready.
            with r.resource_lock:
                r.containers.append(name)
            until = time.monotonic()+15
            while not self.socket.exists():
                if time.monotonic() >= until:
                    raise RuntimeError('namespace worker did not create its socket')
                time.sleep(.05)
            self.started = True

    def run(self, mode, filename, workers=8, timeout=180):
        self.start()
        result = request(self.socket, {'mode': mode, 'manifest': filename, 'workers': workers}, timeout)
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'namespace worker failed'))
        return result['result']


def get_worker(runtime):
    with runtime.resource_lock:
        if not hasattr(runtime, 'namespace_jobs'):
            runtime.namespace_jobs = NamespaceJobs(runtime)
        return runtime.namespace_jobs


if __name__ == '__main__':
    serve(sys.argv[1])
