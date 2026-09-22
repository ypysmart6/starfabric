"""Performance evidence independent of forwarding and controller availability."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time


def append_record(path, value):
    with Path(path).open('a') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')


def source_manifest(root):
    root = Path(root)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True, timeout=15)
    files = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
    selected = [root/name for name in files if name and (
        name.endswith(('.go', '.py', '.rs', '.sh', '.patch', '.proto', '.toml', '.lock', '.Dockerfile')) or
        Path(name).name in ('Dockerfile', '.dockerignore', 'requirements.txt') or
        name.startswith(('deploy/', 'scenarios/')) or
        name in ('go.mod', 'go.sum', 'Dockerfile', '.dockerignore', 'Makefile'))]
    hashes = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(set(selected)) if p.is_file()}
    status = git('status', '--porcelain')
    return {'captured_at':time.time(), 'commit':git('rev-parse','HEAD').strip(),
            'dirty':bool(status.strip()), 'git_status':status, 'source_sha256':hashes}


def cpu_snapshot(pids=None, proc=Path('/proc'), cgroup=Path('/sys/fs/cgroup')):
    """Read current and parent v2 quotas/counters. Missing files stay explicit."""
    result = {'observed_at':time.time(), 'processes':{}, 'cgroups':{}}
    for label, pid in (pids or {'driver':os.getpid()}).items():
        process = {'pid':pid}
        try:
            process['cpus_allowed_list'] = next(line.split(':',1)[1].strip()
                for line in (proc/str(pid)/'status').read_text().splitlines() if line.startswith('Cpus_allowed_list:'))
            membership = (proc/str(pid)/'cgroup').read_text()
            process['membership'] = membership
            for line in membership.splitlines():
                if not line.startswith('0::/'):
                    continue
                relative = Path(line[3:].lstrip('/'))
                if '..' in relative.parts:
                    continue
                path = cgroup/relative
                while path == cgroup or cgroup in path.parents:
                    values = {}
                    for name in ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective'):
                        try: values[name] = (path/name).read_text().strip()
                        except OSError: pass
                    if values: result['cgroups'][str(path)] = values
                    if path == cgroup: break
                    path = path.parent
        except (OSError, StopIteration) as error:
            process['unavailable'] = str(error)
        result['processes'][label] = process
    return result


def next_slot(slot, elapsed, step):
    """Slots at/before completion are expired; never replay stale model time."""
    following = max(slot+1, math.floor(elapsed/step)+1)
    return following, following-slot-1


def workloads_ready(workloads):
    """Desired service updates completed; this is not per-packet evidence."""
    if workloads is None:
        return True
    states = workloads.states
    if len(states) != 2*len(workloads.flows) or any(s.get('status') != 'ready' for s in states.values()):
        return False
    if workloads.n3_evpn:
        return len(workloads.n3_states) == len(workloads.f.service()) and all(
            s.get('status') == 'ready' for s in workloads.n3_states.values())
    return True


def realtime_assessment(frames, schedule, started, finished, target=10):
    """Count due slots, including unobserved and skipped slots, in the window.

    A slot is due once its full target deadline falls inside the observation
    window. Boundary slots whose deadline has not arrived remain pending.
    """
    if started is None or not schedule or not schedule.get('step_seconds'):
        return {'available':False, 'passed':False, 'reason':'scheduler evidence missing'}
    base, step = schedule['base_epoch'], schedule['step_seconds']
    first = max(0, math.ceil((started-base)/step))
    last = math.floor((finished-target-base)/step)
    expected = max(0, last-first+1)
    eligible = {r['slot']:r for r in frames if 'slot' in r and first<=r['slot']<=last}
    completed = len(eligible)
    good = sum(r.get('scheduled_to_forwarding_seconds', math.inf)<=target and
               r.get('scheduled_to_completion_seconds', math.inf)<=target and
               r.get('forwarding_verified', False) for r in eligible.values())
    return {'available':True, 'first_due_slot':first, 'last_due_slot':last,
            'expected_slots':expected, 'completed_slots':completed,
            'skipped_or_unfinished_slots':expected-completed, 'on_time_slots':good,
            'pending_slots':max(0, math.floor((finished-base)/step)-max(last,first-1)),
            'passing_fraction':good/max(1,expected), 'passed':expected>=3 and good/max(1,expected)>=.95,
            'target_seconds':target, 'boundary_policy':'scheduled in window and deadline <= observed_at'}
