#!/usr/bin/env python3
"""Start/status/stop the owned persistent constellation, independently of the UI."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lab.live.storage import atomic, rotating
from lab.live.progress import Progress, monitor, stamp


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


class Service:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.directory = self.root/'reports/live'

    def status(self):
        owner = read(self.directory/'owner.json')
        status = read(self.directory/'status.json')
        alive = bool(owner.get('pid') and owner.get('identity') == identity(owner['pid']))
        if status.get('session') != owner.get('session') and not status.get('run_id'):
            status = {}
        if not alive and status.get('phase') not in ('stopped','failed'):
            status['phase'] = 'interrupted' if status else 'stopped'
        if alive and status.get('phase') == 'stopped':
            status['phase'] = 'stopping'
        if alive and owner.get('stop_requested_at'):
            status['phase'] = 'stopping'
        progress = read(self.directory/'progress.json')
        diagnostics = read(self.directory/'diagnostics.json')
        if progress.get('session') == owner.get('session') and progress.get('session'):
            status['startup'] = progress
        if diagnostics.get('session') == owner.get('session') and diagnostics.get('session'):
            status['diagnostics'] = diagnostics
        return {**status, 'active': alive, 'supervisor_pid': owner.get('pid'),
                'phase': status.get('phase','stopped')}

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = (self.directory/'service.lock').open('a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return self.status()
        env = dict(os.environ, SF_LIVE_LOCK_FD=str(lock.fileno()), SF_5G_KEEP='0')
        try:
            # The child inherits the lock; a second UI or CLI cannot start a
            # second constellation sharing the same 5G endpoints.
            subprocess.Popen([sys.executable, str(self.root/'lab/live/service.py'), 'supervise'],
                cwd=self.root, env=env, pass_fds=(lock.fileno(),), start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            lock.close()
        for _ in range(20):
            if self.status()['active']:
                break
            time.sleep(.05)
        return self.status()

    def stop(self):
        status = self.status()
        if status['active']:
            os.kill(status['supervisor_pid'], signal.SIGTERM)
            return {**status, 'phase':'stopping'}
        return status


def supervise():
    service = Service()
    directory = service.directory
    directory.mkdir(parents=True, exist_ok=True)
    inherited = os.environ.get('SF_LIVE_LOCK_FD')
    lock = os.fdopen(int(inherited),'a+') if inherited else (directory/'service.lock').open('a+')
    if not inherited:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    session = str(time.time_ns())
    atomic(directory/'owner.json', {'pid':os.getpid(),'identity':identity(os.getpid()),'session':session})
    atomic(directory/'status.json', {'phase':'starting_5g','session':session,'started_at':stamp(),'updated_at':stamp()})
    (directory/'snapshot.json').unlink(missing_ok=True)
    (directory/'diagnostics.json').unlink(missing_ok=True)
    progress = Progress(directory/'progress.json', session)
    progress.begin('starting_5g')
    monitor_stop = threading.Event()
    monitor_thread = threading.Thread(target=monitor, args=(ROOT, session, monitor_stop), daemon=True)
    monitor_thread.start()
    logger = rotating(directory/'service.log', backups=2)
    child = subprocess.Popen(['bash',str(ROOT/'ntn/single-pc/run.sh'),'--live'],cwd=ROOT,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True,
        env=dict(os.environ, SF_5G_KEEP='0', SF_LIVE_SESSION=session))
    stopping = False
    def stop(*_):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        owner = read(directory/'owner.json')
        owner['stop_requested_at'] = stamp()
        atomic(directory/'owner.json', owner)
        state = read(directory/'status.json')
        runtime_pid = state.get('pid')
        cmdline = Path(f'/proc/{runtime_pid}/cmdline')
        if runtime_pid and identity(runtime_pid) and cmdline.exists() and str(ROOT/'lab/live/runtime.py').encode() in cmdline.read_bytes():
            # Let runtime tear down its own routes/containers first. Once it
            # returns, the shell's EXIT handler removes the owned 5G stack.
            os.kill(runtime_pid, signal.SIGTERM)
        else:
            os.killpg(child.pid, signal.SIGTERM)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for line in child.stdout:
        logger.info(line.rstrip())
    result = child.wait()
    status = read(directory/'status.json')
    if status.get('phase') not in ('stopped','failed'):
        status.update(phase='stopped' if stopping else 'failed', error=None if stopping else f'5G/runtime launcher exited {result}; see reports/live/service.log')
    if result not in (0,143) and status.get('phase') == 'stopped':
        status.update(phase='failed',error=f'5G cleanup failed with exit {result}; see reports/live/service.log')
    status['wrapper_exit_code'] = result
    atomic(directory/'status.json', status)
    progress = Progress(directory/'progress.json', session, resume=True)
    for row in progress.value['stages']:
        if row['state'] == 'running':
            progress.finish(row['id'], error=status.get('error') or '启动过程已结束', cancelled=stopping)
    monitor_stop.set()
    monitor_thread.join(timeout=10)
    lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start','stop','status','supervise'))
    args = parser.parse_args()
    if args.action == 'supervise':
        supervise()
    else:
        print(json.dumps(getattr(Service(),args.action)(), ensure_ascii=False, indent=2))
