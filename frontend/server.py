#!/usr/bin/env python3
"""Local StarFabric evidence dashboard with bounded, same-origin runtime controls."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import mimetypes
import math
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from tools.ephemeris_contacts import ground_teme, greenwich_mean_sidereal_time
from lab.live.service import Service
from lab.live.storage import atomic
from lab.live.workload_snapshot import observe as observe_workloads


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def within(root: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def public_report(report):
    return {k: v for k, v in report.items() if k not in ('source_sha256', 'source_sha256_after')}


class Dashboard:
    def __init__(self, root=ROOT, controller='', token=''):
        self.root = Path(root)
        self.controller = controller.rstrip('/')
        self.token = token
        self.lock = threading.Lock()
        self.cache = {}
        self.service = Service(self.root)

    def live_snapshot(self):
        runtime = self.service.status()
        value = read_json(self.root/'reports/live/snapshot.json')
        if not value.get('frames') or value.get('run_id') != runtime.get('run_id'):
            return {'mode':'live', 'pending':True, 'runtime':runtime}
        config = value['physics']
        frames = []
        for frame in value['frames']:
            at = datetime.fromisoformat(frame['at'].replace('Z','+00:00'))
            frames.append({'at':frame['at'], 'offset':frame['offset_seconds'],
                'positions':{n:[round(x,3) for x in s['position_km']] for n,s in frame['states'].items()},
                'ground':{s['id']:[round(x,3) for x in ground_teme(s,at)[0]] for s in config['ground_stations']},
                'gmst':greenwich_mean_sidereal_time(at), 'links':frame['links'],
                'active_pairs':frame['active_pairs'],'selected_pairs':frame['selected_pairs']})
        art = self.artifact_path({'run_id':value['run_id']})
        evidence = [{'name':str(p.relative_to(art)), 'path':str(p.relative_to(self.root)), 'bytes':p.stat().st_size}
                    for p in sorted(art.rglob('*')) if p.is_file() and within(art,p)]
        stamp = runtime.get('applied_at')
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds() if stamp else None
        runtime['sample_age_seconds'] = age
        runtime['fresh'] = bool(runtime['active'] and runtime.get('phase')=='running' and age is not None and age < max(30,3*runtime['step_seconds']))
        deployment = read_json(art/'cloud/platform-controller-deployment.json')
        lease = read_json(art/'cloud/platform-lease.json')
        return {'mode':'live', 'runtime':runtime, 'preview_only':False,
            'run':{'run_id':value['run_id'],'focus':'live','success':None,'generated_at':runtime.get('updated_at'),
                'physical_model':True,'physical_runtime_integration':False,'full_protocol_integration':False,
                'artifacts':str(art.relative_to(self.root)), 'checks':value['checks'],
                'deployed_counts':value['deployed_counts'],'timeline':value.get('timeline',[]),'traffic':[value['traffic']]},
            'runs':self.runs(), 'frames':frames, 'physics':config,
            'replay':{'frames':value['records']}, 'plans':value['plans'],
            'nodes':[{'id':n['id'],'kind':n['kind'],'loopback':n.get('loopback'),
                'plane':int(n.get('labels',{}).get('logical_plane',0)), 'slot':int(n.get('labels',{}).get('logical_slot',0)),
                'orbit':value['orbits'].get(n['id'],{})} for n in value['nodes']],
            'intents':value['intents'], 'onboard':{n:{'live':s,'final':s,'autonomous':None,
                'routes':s.get('fallback_routes',[]) if s else []} for n,s in value['onboard'].items()},
            'proofs':{}, 'evidence':evidence, 'pings':value['pings'], 'sources':self.source_files(),
            'workloads':observe_workloads(value.get('workloads', {'enabled':False}), art/'workloads'), 'workload_error':value.get('workload_error'),
            'capabilities':self.capability_catalog(), 'pdu':{'initial':value['pdu'],'final':{}},
            'cloud':{'deployment':{'replicas':deployment.get('spec',{}).get('replicas'),
                'name':deployment.get('metadata',{}).get('name'),'status':deployment.get('status',{})}, 'lease':lease.get('spec',{})},
            'configuration':value.get('configuration', {
                'constellation':read_json(self.root/'scenarios/constellations/leo-120.config.json'),
                'physical':config}),
            'controller_configured':True, 'controller_status':value.get('controller_status'),
            'noc_urls':value.get('noc_urls',{}),'loaded_at':datetime.now(timezone.utc).isoformat()}

    def set_workload_rate(self, body):
        if not isinstance(body, dict) or set(body) != {'run_id', 'scale'}:
            raise ValueError('请求必须包含 run_id 和 scale。')
        scale = body['scale']
        if type(scale) not in (int, float) or not math.isfinite(scale) or not .1 <= scale <= 1:
            raise ValueError('在线速率比例应为 0.1–1；增加上限需要重新配置业务与容量预留。')
        with self.lock:
            runtime = self.service.status()
            run = body['run_id']
            if not isinstance(run, str) or not re.fullmatch(r'sf-unified-[a-z0-9]+', run) or run != runtime.get('run_id') or not runtime.get('active'):
                raise ValueError('只能调节当前正在运行的会话。')
            folder = self.root/'lab/unified/artifacts'/run/'workloads'
            if not within(self.root, folder):
                raise ValueError('业务目录不在当前工程中。')
            catalog = read_json(folder/'catalog.json')
            if catalog.get('run_id') != run or not catalog.get('flows'):
                raise ValueError('当前运行尚未启动并行业务。')
            value = {'run_id':run, 'token':catalog['token'], 'scale':scale, 'updated_at':time.time()}
            atomic(folder/'rates.json', value)
            return {'run_id':run, 'scale':scale, 'state':'requested'}

    def runs(self):
        candidates = list((self.root / 'lab/unified/artifacts').glob('*/platform-report.json'))
        candidates += list((self.root / 'reports/physical-platforms').glob('leo-120-*/latest.json'))
        candidates += list((self.root / 'reports/platforms').glob('leo-120-*/latest.json'))
        found = {}
        for path in candidates:
            value = read_json(path)
            if value.get('satellites') != 120 or not re.fullmatch(r'sf-unified-[a-z0-9]+', value.get('run_id', '')):
                continue
            if not within(self.root, path):
                continue
            key = value['run_id']
            found[key] = dict(run_id=key, success=value.get('success', False),
                physical=bool(value.get('physical_model')), focus=value.get('focus', 'full'),
                generated_at=value.get('generated_at', ''), deployed=value.get('deployed_counts', {}),
                full_protocol_integration=value.get('full_protocol_integration', False),
                path=str(path.relative_to(self.root)), artifacts=value.get('artifacts', ''))
        return sorted(found.values(), key=lambda item: item['generated_at'], reverse=True)

    def source_files(self):
        path = self.root / 'docs/120-satellite-file-index.md'
        result = []
        category = ''
        for line in path.read_text().splitlines() if path.exists() else []:
            if line.startswith('## '):
                category = line[3:]
            match = re.match(r'\| \[([^]]+)\]\(\.\./([^)]+)\) \| (.*?) \|', line)
            if match:
                name, target, role = match.groups()
                result.append({'path': unquote(target), 'category': category, 'role': role})
        for name in ('120-satellite-learning-guide.md', '120-satellite-file-index.md', '120-satellite-quickstart.md', 'remaining-systems.md'):
            relative = 'docs/' + name
            if not any(item['path'] == relative for item in result):
                result.append({'path': relative, 'category': '学习资料', 'role': '完整流程、证据和学习指引'})
        for relative in ('lab/live/README.md','lab/live/model.py','lab/live/runtime.py','lab/live/service.py','lab/live/storage.py','lab/live/heartbeat.py',
                         'lab/live/workloads.py','lab/live/workload_model.py','lab/live/workload_traffic.py','lab/live/workload_capture.py',
                         'lab/live/workload_snapshot.py',
                         'lab/live/validate_workloads.py','scenarios/constellations/live-workloads.json',
                         'scenarios/constellations/leo-120-global.config.json'):
            if (self.root/relative).is_file() and not any(item['path'] == relative for item in result):
                result.append({'path':relative,'category':'实时运行主线','role':'常驻运行、动态轨道与链路、实时状态和生命周期'})
        return result

    def capability_catalog(self):
        manifest = read_json(self.root / 'docs/coverage-manifest.json')
        closure = read_json(self.root / 'docs/single-pc-closure.json')
        states = {entry['id']: entry for entry in closure.get('entries', [])}
        result = []
        for entry in manifest.get('entries', []):
            declared = states.get(entry['id'], {})
            gates = []
            for gate in declared.get('gates', []):
                path = gate.get('report', '')
                data = read_json(self.root / path) if path and within(self.root, self.root/path) else {}
                checks = data.get('checks', {})
                requested = gate.get('checks', [])
                supported = isinstance(checks, dict) and all(bool(checks.get(k)) for k in requested)
                gates.append({'path': path, 'present': bool(data), 'passed': data.get('success') is True and supported,
                              'generated_at': data.get('generated_at'), 'checks': requested})
            result.append({**entry, 'declared_status': declared.get('status', 'unknown'), 'gates': gates,
                           'hardware_excluded': declared.get('hardware_excluded', [])})
        return result

    def artifact_path(self, report):
        expected = self.root / 'lab/unified/artifacts' / report['run_id']
        provided = self.root / report.get('artifacts', str(expected.relative_to(self.root)))
        if provided.resolve() != expected.resolve() or not within(self.root, expected):
            raise ValueError('Invalid run artifact location')
        return expected

    def snapshot(self, identifier=None):
        runs = self.runs()
        if not runs:
            raise FileNotFoundError('没有找到 120 星平台报告，请先生成或运行 120 星场景。')
        if identifier:
            chosen = next((item for item in runs if item['run_id'] == identifier), None)
            if chosen is None:
                raise FileNotFoundError('没有找到指定运行。')
        else:
            chosen = next((item for item in runs if item['success'] and item['physical']), runs[0])
        report_path = self.root / chosen['path']
        signature = (chosen['run_id'], report_path.stat().st_mtime_ns)
        with self.lock:
            if signature in self.cache:
                return {**self.cache[signature], 'runs': runs}
        report = read_json(report_path)
        art = self.artifact_path(report)
        model = read_json(art / 'physical-model.json')
        scenario = read_json(art / 'scenario.json')
        preview_only = not bool(model)
        if not model:
            generated = read_json(self.root / 'reports/physical-config/120-4/scenario.json')
            model = generated.get('physical_model', {})
            if not scenario:
                scenario = generated
        if not model.get('frames'):
            raise FileNotFoundError('本轮缺少物理轨道文件，且没有可用的 120 星配置预览。')
        config = model['config']
        frames = []
        for frame in model['frames']:
            at = datetime.fromisoformat(frame['at'].replace('Z', '+00:00'))
            angle = greenwich_mean_sidereal_time(at)
            positions = {name: [round(v, 3) for v in state['position_km']] for name, state in frame['states'].items()}
            ground = {station['id']: [round(v, 3) for v in ground_teme(station, at)[0]] for station in config['ground_stations']}
            links = [{key: link[key] for key in ('id', 'source', 'target', 'operational_up', 'link_type', 'latency_us', 'capacity_bps', 'loss_ppm', 'range_km', 'doppler_hz', 'acquisition_state') if key in link} for link in frame['links']]
            frames.append({'at': frame['at'], 'offset': frame['offset_seconds'], 'positions': positions,
                           'ground': ground, 'gmst': angle, 'links': links,
                           'active_pairs': frame['active_pairs'], 'selected_pairs': frame['selected_pairs']})
        replay = read_json(art / 'physical-replay.json')
        initial = read_json(art / 'initial-commit.json').get('plan')
        plans = []
        for index, _ in enumerate(frames):
            value = read_json(art / f'physical-{index}.json').get('plan')
            if value:
                initial = value
            plans.append(initial)
        nodes = []
        for node in scenario.get('topology', {}).get('nodes', []):
            labels = node.get('labels', {})
            nodes.append({'id': node['id'], 'kind': node['kind'], 'loopback': node.get('loopback'),
                          'plane': int(labels.get('logical_plane', 0)), 'slot': int(labels.get('logical_slot', 0)),
                          'orbit': model.get('satellite_orbits', {}).get(node['id'], {})})
        autonomous = read_json(art / 'fleet-autonomous.json')
        onboard = {}
        for node in nodes:
            if node['kind'] == 'satellite':
                folder = art / (node['id'] + '-onboard')
                local_config = read_json(folder / 'config.json')
                onboard[node['id']] = {'routes': local_config.get('fallback_routes', []),
                    'autonomous': autonomous.get(node['id']), 'final': read_json(folder / 'state/state.json') or None}
        proofs = read_json(art / 'protocol-packet-proofs.json')
        # Keep summary counts; raw packet details remain available in the evidence viewer.
        for profile in proofs.values():
            for node in profile.get('forwarding_satellites', {}).values():
                node.pop('examples', None)
        evidence = []
        if art.exists():
            for file in sorted(art.rglob('*')):
                if file.is_file() and within(art, file):
                    evidence.append({'name': str(file.relative_to(art)), 'path': str(file.relative_to(self.root)), 'bytes': file.stat().st_size})
        pings = []
        ping_file = art / 'continuous-ping.log'
        if ping_file.exists():
            for line in ping_file.read_text(errors='replace').splitlines():
                match = re.search(r'\[([\d.]+)\].*icmp_seq=(\d+).*time=([\d.]+)', line)
                if match:
                    stamp, sequence, latency = match.groups()
                    pings.append([float(stamp), int(sequence), float(latency)])
        cloud = read_json(art / 'cloud/platform-controller-deployment.json')
        lease = read_json(art / 'cloud/platform-lease.json')
        docs = self.source_files()
        payload = {'run': public_report(report), 'runs': runs, 'preview_only': preview_only,
            'frames': frames, 'replay': replay, 'plans': plans, 'nodes': nodes, 'physics': config,
            'intents': scenario.get('intents', []), 'onboard': onboard, 'proofs': proofs,
            'evidence': evidence, 'pings': pings, 'sources': docs, 'capabilities': self.capability_catalog(),
            'pdu': {'initial': read_json(art / 'pdu-identity-initial.json'), 'final': read_json(art / 'pdu-identity-final.json')},
            'cloud': {'deployment': {'replicas': cloud.get('spec', {}).get('replicas'), 'name': cloud.get('metadata', {}).get('name'),
                       'status': cloud.get('status', {})}, 'lease': lease.get('spec', {})},
            'configuration': {'constellation': read_json(self.root / 'scenarios/constellations/leo-120.config.json'),
                              'physical': read_json(self.root / 'scenarios/constellations/physical-defaults.json')},
            'controller_configured': bool(self.controller), 'loaded_at': datetime.now(timezone.utc).isoformat()}
        with self.lock:
            if len(self.cache) >= 4:
                self.cache.clear()
            self.cache[signature] = payload
        return payload

    def allowed_file(self, relative):
        path = self.root / relative
        if not within(self.root, path) or not path.is_file():
            raise FileNotFoundError('文件不存在或不在可查看范围。')
        allowed = {item['path'] for item in self.source_files()}
        for entry in self.capability_catalog():
            allowed.update(entry.get('evidence', []))
            allowed.update(gate['path'] for gate in entry['gates'])
        for run in self.runs():
            allowed.add(run['path'])
            art = self.root/'lab/unified/artifacts'/run['run_id']
            if path.resolve().is_relative_to(art.resolve()):
                return path
        live = self.service.status()
        if re.fullmatch(r'sf-unified-[a-z0-9]+',live.get('run_id','')):
            art = self.root/'lab/unified/artifacts'/live['run_id']
            if path.resolve().is_relative_to(art.resolve()):
                return path
        if relative not in allowed:
            raise FileNotFoundError('文件不在工程学习资料或验收证据中。')
        return path

    def live(self):
        if not self.controller:
            return {'configured': False, 'connected': False, 'message': '尚未配置实时控制器地址。历史回放仍可使用。'}
        def request(path):
            req = Request(self.controller + path, headers={'Authorization': 'Bearer ' + self.token} if self.token else {})
            try:
                with urlopen(req, timeout=4) as response:
                    return {'ok': True, 'data': json.load(response)}
            except (HTTPError, URLError, OSError, ValueError):
                return {'ok': False, 'message': '控制器不可达、未就绪或认证失败'}
        names = {'health': '/readyz', 'topology': '/api/v1/topology', 'status': '/api/v1/status', 'intents': '/api/v1/intents'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            values = dict(zip(names, pool.map(request, names.values())))
        return {'configured': True, 'connected': all(value['ok'] for value in values.values()),
                'observed_at': datetime.now(timezone.utc).isoformat(), 'results': values}


class Handler(BaseHTTPRequestHandler):
    server_version = 'StarFabricUI/1.0'

    def send_bytes(self, data, content_type, status=200, filename=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            from urllib.parse import quote
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def json_response(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(), 'application/json; charset=utf-8', status)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == '/api/dashboard':
                self.json_response(self.server.dashboard.live_snapshot() if query.get('mode')==['live']
                    else self.server.dashboard.snapshot(query.get('run', [None])[0]))
            elif parsed.path == '/api/runtime':
                self.json_response(self.server.dashboard.service.status())
            elif parsed.path == '/api/live':
                self.json_response(self.server.dashboard.live())
            elif parsed.path == '/api/file':
                file = self.server.dashboard.allowed_file(query.get('path', [''])[0])
                if file.stat().st_size > 32 * 1024 * 1024:
                    self.json_response({'error': '文件超过 32 MiB，请在本机直接打开。'}, 413)
                    return
                is_binary = file.suffix in ('.pcap', '.pcapng')
                self.send_bytes(file.read_bytes(), 'application/octet-stream' if is_binary else 'text/plain; charset=utf-8',
                                filename=file.name if is_binary or query.get('download') == ['1'] else None)
            elif parsed.path == '/healthz':
                self.json_response({'status': 'ok'})
            else:
                static = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js',
                          '/globe.js': 'globe.js', '/earth-surface.js': 'earth-surface.js', '/solar.js': 'solar.js',
                          '/style.css': 'style.css', '/favicon.svg': 'favicon.svg',
                          '/assets/earth-day.png': 'assets/earth-day.png',
                          '/assets/earth-night.png': 'assets/earth-night.png'}
                name = static.get(parsed.path)
                if not name:
                    raise FileNotFoundError('页面不存在。')
                mime = mimetypes.guess_type(name)[0] or 'text/plain'
                self.send_bytes((STATIC/name).read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
        except (FileNotFoundError, ValueError) as error:
            self.json_response({'error': str(error)}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.json_response({'error': '读取数据失败，请检查本地报告和文件权限。'}, 500)

    def do_POST(self):
        if self.path not in ('/api/runtime/start','/api/runtime/stop','/api/runtime/workload-rate'):
            self.json_response({'error':'该接口不接受写入请求。'},405)
            return
        origin = self.headers.get('Origin')
        if (origin and origin != 'http://'+self.headers.get('Host','')) or self.headers.get('Content-Type') != 'application/json':
            self.json_response({'error':'运行操作只接受同源 JSON 请求。'},403)
            return
        if self.path == '/api/runtime/workload-rate':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 512:
                    raise ValueError('请求体大小应为 1–512 字节。')
                body = json.loads(self.rfile.read(length))
                self.json_response(self.server.dashboard.set_workload_rate(body), 202)
            except (ValueError, UnicodeError) as error:
                self.json_response({'error':str(error)}, 400)
            except OSError:
                self.json_response({'error':'无法保存业务速率，请检查本轮文件权限。'}, 500)
            return
        if self.headers.get('Content-Length') not in ('0','2'):
            self.json_response({'error':'运行入口使用本地配置；请求体应为 {}。'},400)
            return
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        try:
            action = self.path.rsplit('/',1)[-1]
            self.json_response(getattr(self.server.dashboard.service,action)(),202)
        except (OSError,ValueError) as error:
            self.json_response({'error':str(error)},500)


def serve(port=8090, controller='', token=''):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.dashboard = Dashboard(controller=controller, token=token)
    print(f'StarFabric mission control → http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--controller', default='', help='Optional read-only controller HTTP(S) origin')
    parser.add_argument('--controller-token-file', type=Path)
    args = parser.parse_args()
    if args.controller:
        url = urlparse(args.controller)
        if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            parser.error('--controller must be an HTTP(S) origin without credentials or a path')
    token = args.controller_token_file.read_text().strip() if args.controller_token_file else ''
    try:
        serve(args.port, args.controller, token)
    except KeyboardInterrupt:
        pass
