"""Startup checkpoints and bounded, read-only diagnostics for the owned session."""
from __future__ import annotations

import json
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from lab.live.storage import atomic

STAGES = [
    ('starting_5g', '构建程序与建立 5G 会话'),
    ('preparing_host', '准备主机资源'),
    ('computing_initial_orbits', '计算初始轨道'),
    ('checking_resources', '检查资源容量'),
    ('starting_cloud', '部署 Kubernetes 云控制平面'),
    ('creating_124_routers', '创建本轮全部 FRR 路由器'),
    ('checking_protocol_neighbors', '检查协议邻居'),
    ('starting_observability', '启动监控服务'),
    ('starting_controller', '启动地面控制器'),
    ('committing_initial_routes', '提交初始路由'),
    ('starting_5g_business', '启动持续 5G 业务'),
    ('starting_workloads', '启动并行业务与协议承载'),
    ('starting_120_onboard', '启动 120 个星上实例'),
]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


class Progress:
    def __init__(self, path, session, resume=False):
        self.path = Path(path)
        old = read(path) if resume else {}
        self.value = old if old.get('session') == session else {
            'session': session, 'started_at': stamp(), 'stages': [
                {'id': key, 'title': title, 'state': 'pending'} for key, title in STAGES],
            'tasks': [], 'events': [], 'context': {}, 'current_stage': None}

    def save(self, message=None):
        self.value['last_progress_at'] = stamp()
        if message:
            self.value['events'].append({'at': stamp(), 'message': str(message)[-2500:]})
            self.value['events'] = self.value['events'][-100:]
        atomic(self.path, self.value)

    def context(self, **values):
        self.value['context'].update(values)
        self.save()

    def begin(self, key):
        self.value['current_stage'] = key
        row = next(s for s in self.value['stages'] if s['id'] == key)
        row.update(state='running', started_at=stamp())
        self.save('开始：' + row['title'])

    def finish(self, key, error=None, cancelled=False):
        row = next(s for s in self.value['stages'] if s['id'] == key)
        row.update(state='cancelled' if cancelled else 'failed' if error else 'done', finished_at=stamp())
        if error:
            row['error'] = str(error)[-2500:]
        self.save(('中止：' if cancelled else '失败：' if error else '完成：') + row['title'])

    @contextmanager
    def operation(self, key, title, detail='', timeout=None):
        row = {'id': key, 'stage': self.value['current_stage'], 'title': title,
               'detail': detail, 'timeout_seconds': timeout, 'state': 'running', 'started_at': stamp()}
        self.value['tasks'].append(row)
        self.save('开始：' + title + (' · ' + detail if detail else ''))
        try:
            yield
        except Exception as error:
            row.update(state='failed', error=str(error)[-2500:], finished_at=stamp())
            self.save('失败：' + title + ' · ' + str(error)[-1500:])
            raise
        else:
            row.update(state='done', finished_at=stamp())
            self.save('完成：' + title)


def kubernetes_summary(value):
    nodes, pods, warnings = [], [], []
    for item in value.get('items', []):
        meta, status = item.get('metadata', {}), item.get('status', {})
        if item.get('kind') == 'Node':
            conditions = {c['type']: c for c in status.get('conditions', [])}
            nodes.append({'name': meta['name'], 'ready': conditions.get('Ready', {}).get('status') == 'True',
                          'reason': conditions.get('Ready', {}).get('reason', '等待节点上报'),
                          'message': conditions.get('Ready', {}).get('message', '')[:500]})
        elif item.get('kind') == 'Pod':
            containers = status.get('containerStatuses', [])
            states = status.get('initContainerStatuses', []) + containers
            reasons = [s['state']['waiting'].get('reason', '') for s in states if s.get('state', {}).get('waiting')]
            if not reasons:
                reasons = [c.get('reason', '') for c in status.get('conditions', []) if c.get('status') == 'False' and c.get('reason')]
            pods.append({'name': meta['name'], 'namespace': meta.get('namespace'), 'phase': status.get('phase', 'Pending'),
                         'ready': sum(bool(s.get('ready')) for s in containers), 'total': len(item.get('spec', {}).get('containers', [])),
                         'restarts': sum(s.get('restartCount', 0) for s in states), 'reason': ', '.join(dict.fromkeys(reasons)),
                         'node': item.get('spec', {}).get('nodeName', '尚未调度')})
        elif item.get('kind') == 'Event' and item.get('type') == 'Warning':
            warnings.append({'at': item.get('lastTimestamp') or item.get('eventTime') or meta.get('creationTimestamp'),
                             'object': item.get('involvedObject', {}).get('name'), 'reason': item.get('reason'),
                             'message': item.get('message', '')[:1000], 'count': item.get('count', 1)})
    return {'nodes': sorted(nodes, key=lambda n: n['name']),
            'pods': sorted(pods, key=lambda p: (p['namespace'] or '', p['name'])),
            'warnings': sorted(warnings, key=lambda e: e['at'] or '')[-8:]}


def collect_diagnostics(root, session):
    """No user-supplied commands or kubeconfig; inspect only this owned cluster."""
    root = Path(root)
    progress = read(root/'reports/live/progress.json')
    if progress.get('session') != session:
        return {'session': session, 'observed_at': stamp(), 'error': '等待本轮启动记录'}
    context = progress.get('context', {})
    result = {'session': session, 'observed_at': stamp(), 'containers': {}, 'kubernetes': None}
    try:
        raw = subprocess.run(['docker', 'ps', '-a', '--format', '{{json .}}'], capture_output=True,
                             text=True, timeout=4, check=True)
        rows = [json.loads(line) for line in raw.stdout.splitlines()]
        run = context.get('run_id', '')
        cluster = context.get('cluster', '')
        def count(pattern):
            selected = [r for r in rows if re.fullmatch(pattern, r['Names'])]
            return {'running': sum(r['State'] == 'running' for r in selected), 'created': len(selected)}
        if re.fullmatch(r'sf-unified-[a-z0-9]+', run):
            result['containers']['frr'] = count(re.escape(run) + r'-(sat-\d{4}|gw-\d{3})')
            result['containers']['onboard'] = count(re.escape(run) + r'-sat-\d{4}-onboard')
        if re.fullmatch(r'sf-cloud-[a-z0-9]+', cluster):
            result['containers']['kubernetes'] = count(re.escape(cluster) + r'-(control-plane|worker\d*)')
        result['docker_ok'] = True
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        result.update(docker_ok=False, docker_error=str(error)[-500:])
    cluster = context.get('cluster', '')
    if re.fullmatch(r'sf-cloud-[a-z0-9]+', cluster):
        directory = root/'.cache/cloud-runtime'/cluster
        if (directory/'kubectl').is_file() and (directory/'kubeconfig').is_file():
            try:
                raw = subprocess.run([str(directory/'kubectl'), '--kubeconfig', str(directory/'kubeconfig'),
                    '--request-timeout=2s', 'get', 'nodes,pods,events', '-A', '-o', 'json'],
                    capture_output=True, text=True, timeout=4)
                if raw.returncode:
                    raise RuntimeError(raw.stderr[-800:])
                result['kubernetes'] = {**kubernetes_summary(json.loads(raw.stdout)), 'api_ok': True}
            except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as error:
                result['kubernetes'] = {'api_ok': False, 'error': str(error)[-800:]}
        else:
            result['kubernetes'] = {'api_ok': False, 'error': '集群创建中，等待本轮 kubectl 与 kubeconfig 就绪'}
    result['observed_at'] = stamp()
    return result


def monitor(root, session, stop):
    root = Path(root)
    previous = None
    while not stop.is_set():
        phase = read(root/'reports/live/status.json').get('phase')
        if phase not in ('running', 'degraded') or previous not in ('running', 'degraded'):
            try:
                atomic(root/'reports/live/diagnostics.json', collect_diagnostics(root, session))
            except Exception as error:
                atomic(root/'reports/live/diagnostics.json', {'session': session, 'observed_at': stamp(), 'error': str(error)[-500:]})
        previous = phase
        stop.wait(5)


if __name__ == '__main__':
    import os
    import sys
    path = Path(__file__).resolve().parents[2]/'reports/live/progress.json'
    session = os.environ.get('SF_LIVE_SESSION')
    if session and len(sys.argv) > 1:
        progress = Progress(path, session, resume=True)
        progress.save('5G：' + sys.argv[1])
