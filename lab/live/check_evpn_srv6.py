#!/usr/bin/env python3
"""Real 5-router test: selected IGP, EVPN L2/L3, SRH packets, withdrawal.

Creates only uniquely named owned Docker containers, always cleans them up.
No Kubernetes/5G/120-satellite stability claim is made by this test.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lab.live.test_launch import fixture
from lab.live.workloads import Workloads
from lab.live.workload_model import directions
from lab.live.storage import atomic
sys.path.insert(0, str(ROOT/'lab/unified'))

FRR = 'quay.io/frrouting/frr@sha256:65e5967b922572c0565d968388fb06af69d7e9b3b3eea40ad7e3810687667f68'
TOOLS = 'ghcr.io/herlesupreeth/docker_open5gs@sha256:985d89d67ad9c3e25ea8a98b0897c14a2420dc15909c69f3e0794a043327407c'


def command(*args, check=True, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=timeout)


def wait(label, predicate, seconds=75):
    until = time.monotonic()+seconds
    while time.monotonic() < until:
        value = predicate()
        if value: return value
        time.sleep(1)
    raise AssertionError(label+' timeout')


def run(igp, output):
    identifier = 'sf-evpn-check-'+uuid.uuid4().hex[:8]
    output = output.resolve(); output.mkdir(parents=True, exist_ok=True)
    f = fixture(igp,gateways=4)
    names, pids, result = [], {}, {'igp':igp,'run_id':identifier}
    w = agent = None
    def router(n): return identifier+'-'+n
    def docker(n,*args,check=True): return command('docker','exec',router(n),*args,check=check).stdout
    def jobs(operations):
        atomic(output/'wiring.json',operations)
        command('docker','run','--rm','--privileged','--pid=host','--network=none',
            '-v',f'{output}:/work','-v',f'{ROOT}/lab/platform/wire.py:/wire.py:ro',
            '--entrypoint','python3',TOOLS,'/wire.py','/work/wiring.json',timeout=120)
    try:
        for node in f.nodes:
            folder = output/node; folder.mkdir(exist_ok=True)
            (folder/'frr.conf').write_text(f.config(node))
            (folder/'daemons').write_text(''.join(d+'=yes\n' for d in f.daemons(node))+'vtysh_enable=yes\n')
            (folder/'vtysh.conf').write_text('service integrated-vtysh-config\n')
            names.append(router(node))
            command('docker','run','-d','--name',router(node),'--label','starfabric.run='+identifier,'--network=none',
                '--cap-add=NET_ADMIN','--cap-add=NET_RAW','--cap-add=SYS_ADMIN','--sysctl','net.ipv4.ip_forward=1',
                '--sysctl','net.ipv4.conf.all.rp_filter=0','--sysctl','net.ipv4.conf.default.rp_filter=0',
                '--sysctl','net.ipv6.conf.all.forwarding=1','--sysctl','net.ipv6.conf.all.seg6_enabled=1',
                '--sysctl','net.ipv6.conf.default.seg6_enabled=1',
                '-v',f'{folder}/frr.conf:/etc/frr/frr.conf:ro','-v',f'{folder}/daemons:/etc/frr/daemons:ro',
                '-v',f'{folder}/vtysh.conf:/etc/frr/vtysh.conf:ro', FRR)
            pids[node] = json.loads(command('docker','inspect',router(node)).stdout)[0]['State']['Pid']
        setup = f.wiring(pids,set(f.links))
        for node in f.nodes:
            for interface in ('sr0','svc0'):
                setup += [{'pid':pids[node],'args':['ip','link','add',interface,'type','dummy']},
                          {'pid':pids[node],'args':['ip','link','set',interface,'up']}]
            setup.append({'pid':pids[node],'args':['ip','-6','route','replace',f.locator(node)+'1/128',
                          'encap','seg6local','action','End','dev','sr0']})
        jobs(setup)
        for node in f.nodes:
            wait(node+' daemon',lambda:('isisd' if igp=='isis' else 'ospf6d') in docker(node,'vtysh','-c','show daemons',check=False))
        a,b=f.gateways[:2]
        wait('IPv6 IGP convergence',lambda:igp.replace('ospf','ospf6') in docker(a,'vtysh','-c',f'show ipv6 route {f.locator(b)}/64 json'))
        result['ipv6_routes'] = json.loads(docker(a,'vtysh','-c','show ipv6 route json'))

        # Exercise the runtime's actual endpoint/speaker setup and route builder.
        r = SimpleNamespace(identifier=identifier, fabric=f, art=output, pids=pids, router=router)
        # Exercise the actual persistent executor against real FRR namespaces.
        sys.path.append(str(ROOT/'lab/platform'))
        from frr_agent import Agent
        from frr_executor import request
        r.scenario=f.scenario; r.fabric_cpus=','.join(map(str,sorted(os.sched_getaffinity(0))))
        agent=Agent(r,'127.0.0.1',uuid.uuid4().hex)
        agent.start_executor()
        observed=request(agent.executor_socket,{'node':a,'program':'vtysh','args':['-c','show version']},10)
        if observed['exit_code'] or 'FRRouting' not in observed['output']: raise AssertionError(observed)
        rejected=request(agent.executor_socket,{'node':a,'program':'sh','args':['-c','id']},10)
        if rejected['exit_code']==0: raise AssertionError('executor accepted arbitrary command')
        result['persistent_frr_executor_namespace_and_boundary']=True
        r.event=lambda *args,**kwargs:None
        r.configure=lambda node,*args:docker(node,'vtysh','-c','configure terminal',*[v for arg in args for v in ('-c',arg)])
        config=json.loads((ROOT/'scenarios/constellations/live-workloads.json').read_text())
        config['copies_per_profile']=4; config['profiles']=config['profiles'][:1]
        config['evpn_l2_evis']=[201,202]
        config['profiles'][0].update(carrier='evpn-srv6',control='hybrid')
        w=Workloads(r,config); w.command=command
        capture=identifier+'-capture'; names.append(capture)
        command('docker','run','-d','--name',capture,'--network=container:'+router('sat-0001'),'--cap-add=NET_RAW',
                '-v',f'{output}:/work','--entrypoint','tcpdump',TOOLS,'-U','-ni','any','-w','/work/srv6.pcap','ip6')
        time.sleep(1)
        w.setup()
        names.extend(w.helpers)
        flow=w.flows[0]; src=flow['source_address']; dst=flow['destination_address']
        def learned():
            w.read_evpn_ribs()
            return (all(len(w.evpn_ribs.get(n,{}).get('sessions',{}))==3 and
                        all(v=='Established' for v in w.evpn_ribs[n]['sessions'].values()) for n in f.gateways)
                    and all(d['target']+'/32' in w.evpn_ribs.get(d['node'],{}).get('routes',{}) for fl in w.flows for d in directions(fl)))
        wait('EVPN Type-5 and Service SID',learned)
        result['received_evpn'] = w.evpn_ribs
        plan = {'id':'smoke','paths':{'work-'+d['id']:[{'nodes':[d['node'],'sat-0001',d['remote']]}]
                                    for fl in w.flows for d in directions(fl)}}
        r.protocols=type('Protocols',(),{'edges':lambda _:set()})()
        w.maybe_capture=lambda:None
        w.update(plan)
        w.start_l2()
        wait('all CE IPv4/IPv6 probes deliver',lambda:all(
            all(v.get('received',0)>0 for v in json.loads((w.folder/f"l2-metrics-{s['evi']}-{n}.json").read_text())['families'].values())
            for n,services in w.evpn_l2.items() for s in services))
        result['all_ce_probe_heartbeats_and_replies']=True
        result['gateway_count']=len(f.gateways)
        result['steering'] = w.states
        if not all(v['status']=='ready' for v in w.states.values()): raise AssertionError(w.states)
        result['ping'] = docker(a,'ping','-I',src,'-c','4','-W','2',dst)
        result['l2'] = {}
        for evi in (201,202):
            ce_a = next(v for v in w.evpn_l2[a] if v['evi']==evi)
            ce_b = next(v for v in w.evpn_l2[b] if v['evi']==evi)
            def ce_ping(family):
                dest=ce_b['ipv6' if family==6 else 'ipv4']
                return command('docker','exec',ce_a['container'],'ping','-6' if family==6 else '-4','-c','2','-W','2',dest,check=False)
            wait('L2 IPv4/ARP',lambda:ce_ping(4).returncode==0)
            wait('L2 IPv6/ND',lambda:ce_ping(6).returncode==0)
            result['l2'][str(evi)]={'ipv4':ce_ping(4).stdout,'ipv6':ce_ping(6).stdout}
        w.read_evpn_ribs(); result['l2_received_evpn']=w.evpn_ribs
        if not all({2,3,5} <= {r['route_type'] for r in rib['routes'].values()} for rib in w.evpn_ribs.values()):
            raise AssertionError('Type-2/3/5 not all learned')
        # The other EVI deliberately has the same IP, so a leak would answer.
        ce_a=w.evpn_l2[a][0]; ce_b=w.evpn_l2[b][0]
        command('docker','exec',ce_b['container'],'ip','address','del',ce_b['ipv4']+'/24','dev','eth0')
        isolated=command('docker','exec',ce_a['container'],'ping','-c','2','-W','1',ce_b['ipv4'],check=False)
        if isolated.returncode==0: raise AssertionError('duplicate-IP tenant leak')
        command('docker','exec',ce_b['container'],'ip','address','add',ce_b['ipv4']+'/24','dev','eth0')
        result['duplicate_ip_evi_isolation']=True
        command('docker','stop','-t','2',capture)
        trace=command('docker','run','--rm','--network=none','-v',f'{output}:/work:ro','--entrypoint','tcpdump',TOOLS,
                      '-nn','-vv','-r','/work/srv6.pcap').stdout
        (output/'packets.txt').write_text(trace)
        if 'RT6 (len=4, type=4,' not in trace or f'[0]{f.locator(b)}d' not in trace:
            raise AssertionError('no expected SRH and Service SID in packet capture')
        if '4 packets received' not in result['ping']: raise AssertionError(result['ping'])
        result['srv6_packets_verified']=True
        if '143' not in trace and 'Ethernet' not in trace: raise AssertionError('no Ethernet-in-SRv6 packet')
        result['ethernet_in_srv6_verified']=True
        # Stop the exporting BGP speaker. Peer detects TCP close and installs
        # unreachable, independently of the central path update loop.
        command('docker','stop','-t','3',r.router('evpn-'+b))
        wait('EVPN route withdrawn',lambda:dst+'/32' not in json.loads((w.folder/('evpn-'+a+'-rib.json')).read_text()).get('routes',{}))
        wait('withdrawal blocks forwarding', lambda:'unreachable' in docker(a,'ip','route','show','table',str(flow['table'])))
        failed=command('docker','exec',router(a),'ping','-I',src,'-c','1','-W','1',dst,check=False)
        if failed.returncode == 0: raise AssertionError('withdrawn EVPN route still forwards')
        result['withdrawal_blocks_native_fallback']=True
        failed=command('docker','exec',ce_a['container'],'ping','-c','1','-W','1',ce_b['ipv4'],check=False)
        if failed.returncode==0: raise AssertionError('L2 forwards after EVPN withdrawal')
        result['l2_withdrawal_blocks_forwarding']=True
        w.helper('evpn_speaker.py','evpn-'+b+'.json',detached=True,name=r.router('evpn-'+b))
        wait('EVPN reconnect',learned)
        w.update(plan)
        wait('L3 recovery',lambda:command('docker','exec',router(a),'ping','-I',src,'-c','1','-W','1',dst,check=False).returncode==0)
        wait('L2 recovery',lambda:command('docker','exec',ce_a['container'],'ping','-c','1','-W','1',ce_b['ipv4'],check=False).returncode==0)
        result['bgp_restart_l2_l3_recovery']=True
        result['success']=True
    except Exception as error:
        result.update(success=False,error=str(error),stderr=getattr(error,'stderr',None),stdout=getattr(error,'stdout',None))
        for node in f.nodes:
            (output/(node+'-log.txt')).write_text(command('docker','logs',router(node),check=False).stdout)
        raise
    finally:
        if agent: agent.close()
        if w: names.extend(n for n in w.helpers if n not in names)
        cleanup=command('docker','rm','-f',*names,check=False)
        result['cleanup_exit_code']=cleanup.returncode
        atomic(output/'result.json',result)
        print(json.dumps({'result':str(output/'result.json'),'success':result.get('success')},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--igp',choices=['isis','ospf'],default='isis')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/evpn-srv6-smoke')
    args=parser.parse_args(); run(args.igp,args.output)
