"""Validated, immutable inputs for a live launch (never a hot reconfiguration)."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARRIERS = {'native': '原生 IPv4', 'srv6': 'SRv6', 'evpn-srv6': 'EVPN + SRv6（二层 / 三层 / N3）',
            'ldp': 'LDP / MPLS', 'sr-mpls': 'SR-MPLS', 'pcep': 'PCEP / SR-MPLS',
            'evpn': 'EVPN + VXLAN'}
ALGORITHMS = {'latency': '时延优先', 'hops': '最少跳数', 'capacity': '容量优先', 'reliability': '可靠性优先'}
DEFAULT = {'schema_version': 1, 'igp': 'isis', 'carriers': ['evpn-srv6'], 'algorithm': 'latency',
           'satellites': 120, 'gateways': 24, 'planes': 12, 'copies_per_profile': 2, 'step_seconds': 10}


def options():
    return {'defaults': copy.deepcopy(DEFAULT), 'carriers': CARRIERS, 'algorithms': ALGORITHMS,
            'presets': [{'label': '120 星 / 24 网关', 'satellites': 120, 'gateways': 24, 'planes': 12},
                        {'label': '120 星 / 4 网关', 'satellites': 120, 'gateways': 4, 'planes': 12},
                        {'label': '60 星 / 4 网关', 'satellites': 60, 'gateways': 4, 'planes': 6}],
            'limits': {'total_nodes': 255, 'gateways': 24},
            'notes': ['运行中不可更换启动配置；停止后重新选择并启动。',
                      '规模选项是部署配置，不代表已通过稳定性验收；当前实时地址池上限为 255 个节点。',
                      '算法作用于集中式业务路径；IGP 使用自身的 SPF 算法。',
                      '选择 EVPN + SRv6 时，启用二层以太网、三层业务及 5G N3 承载。']}


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('启动配置字段必须完整，且不能包含未知字段')
    result = copy.deepcopy(value)
    for key, minimum, maximum in [('schema_version', 1, 1), ('satellites', 4, 253), ('gateways', 2, 24),
                                  ('planes', 1, 253), ('copies_per_profile', 1, 64), ('step_seconds', 5, 60)]:
        if type(value[key]) is not int or not minimum <= value[key] <= maximum:
            raise ValueError(f'{key} 必须为 {minimum}..{maximum} 的整数')
    if value['satellites'] + value['gateways'] > 255:
        raise ValueError('当前实时地址池仅支持卫星与网关合计不超过 255 个节点')
    if value['satellites'] % value['planes']:
        raise ValueError('卫星数必须能被轨道面数整除')
    if value['igp'] not in ('isis', 'ospf') or not isinstance(value['algorithm'], str) or value['algorithm'] not in ALGORITHMS:
        raise ValueError('不支持的 IGP 或路径算法')
    carriers = value['carriers']
    if not isinstance(carriers, list) or not carriers or any(not isinstance(c, str) or c not in CARRIERS for c in carriers):
        raise ValueError('请选择至少一种已实现的业务承载')
    if len(set(carriers)) != len(carriers):
        raise ValueError('业务承载不能重复')
    if value['igp'] != 'isis' and {'sr-mpls', 'pcep'} & set(carriers):
        raise ValueError('本平台 SR-MPLS / PCEP 的 Prefix-SID 分发需要 IS-IS')
    return result


def materialize(value, root=ROOT):
    config = validate(value)
    root = Path(root)
    inputs = root/'scenarios/constellations'
    constellation = json.loads((inputs/'leo-120-global.config.json').read_text())
    constellation.update({key: config[key] for key in ('satellites', 'gateways', 'planes')})
    constellation['flows'] = 2 * config['gateways']
    physical = json.loads((inputs/'physical-defaults.json').read_text())
    physical['step_seconds'] = config['step_seconds']
    workload = json.loads((inputs/'live-workloads.json').read_text())
    workload['copies_per_profile'] = config['copies_per_profile']
    from lab.live.workload_model import CONTROLS
    for i, profile in enumerate(workload['profiles']):
        carrier = config['carriers'][i % len(config['carriers'])]
        profile.update(carrier=carrier, control=CONTROLS[carrier])
        if profile['id'] == 'management': profile['name'] = '运维'
    folder = root/'reports/live/launches'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    paths = {}
    for key, data in [('launch', config), ('constellation', constellation), ('physical', physical), ('workloads', workload)]:
        path = folder/(key+'.json')
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
        paths[key] = str(path)
    return paths
