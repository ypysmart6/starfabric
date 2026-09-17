#!/usr/bin/env python3
"""Run owned-namespace channel operations, preserving ordering within each node."""
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


def observation(args):
    return args[:3] == ['tc', '-j', '-s'] or args[:4] == ['ip', '-j', 'link', 'show'] or args[:2] == ['sysctl', '-n']


def apply(node):
    records = {}
    commands = node['commands']
    index = 0
    while index < len(commands):
        args = commands[index]
        index += 1
        payload = None
        if args[:2] == ['tc', 'qdisc'] or args[:3] == ['ip', 'link', 'set']:
            program = args[0]
            batch = [args[1:]]
            prefix = ['tc', 'qdisc'] if program == 'tc' else ['ip', 'link', 'set']
            while index < len(commands) and commands[index][:len(prefix)] == prefix:
                batch.append(commands[index][1:]); index += 1
            # Generated numeric parameters/interface names contain no tc batch
            # syntax. Keep IP down/up operations in their original positions.
            payload = '\n'.join(' '.join(command) for command in batch) + '\n'
            args = [program, '-batch', '-']
        command = ['nsenter', '-t', str(node['pid']), '-n', '--', *args]
        result = subprocess.run(command, input=payload, capture_output=True, text=True, timeout=node.get('timeout_seconds',15))
        if result.returncode:
            raise RuntimeError(f"{node['name']}: {args}: {result.stderr}")
        if args[:3] == ['tc', '-j', '-s']:
            records.setdefault(args[-1], {'interface': args[-1]})['qdisc'] = json.loads(result.stdout)
        if args[:4] == ['ip', '-j', 'link', 'show']:
            records.setdefault(args[-1], {'interface': args[-1]})['link'] = json.loads(result.stdout)[0]
        if args[:2] == ['sysctl', '-n']:
            interface=args[-1].split('.')[-2]
            records.setdefault(interface, {'interface':interface})['rp_filter']=int(result.stdout.strip())
    return node['name'], list(records.values())


def execute(jobs, workers=16):
    def phase(args):
        if observation(args):
            return 'read'
        if args[:3] == ['ip','link','set'] and args[-1] in ('down','up'):
            return args[-1]
        return 'shape'
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        # Fleet-wide barriers keep route convergence caused by withdrawals
        # away from queue updates, and shape every new link before enabling it.
        for step in ('down','shape','up'):
            changes = [dict(job, commands=[args for args in job['commands'] if phase(args)==step]) for job in jobs]
            list(pool.map(apply, [job for job in changes if job['commands']]))
        reads = [dict(job, commands=[args for args in job['commands'] if observation(args)]) for job in jobs]
        records = dict(pool.map(apply, reads))
    return records


if __name__ == '__main__':
    jobs = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(json.dumps(execute(jobs,int(sys.argv[3]) if len(sys.argv)>3 else 16)))
