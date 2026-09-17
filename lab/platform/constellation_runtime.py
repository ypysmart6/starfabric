"""Actual deployment and business checks driven by a generated constellation."""
from __future__ import annotations
import concurrent.futures
import heapq
import json
import os
import re
import subprocess
import threading
import time
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from pathlib import Path
from run import Runtime,ROOT,FRR,TOOLS,GNB,UPF,command,save,wait_for,now
from fabric import Fabric


class ConstellationRuntime(Runtime):
    request_timeout=240
    frr_operation_timeout=8
    def operation(self, key, title, detail='', timeout=None):
        progress = getattr(self, 'progress', None)
        return progress.operation(key, title, detail, timeout) if progress else nullcontext()

    def parallel(self,items,fn,workers=8):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(zip(items,pool.map(fn,items)))

    def compile_orbit(self):
        definition=json.loads(Path(self.input_scenario).read_text())
        save(self.art/'constellation-input.json',definition)
        self.fabric=Fabric(definition,self.identifier)
        self.scenario=self.fabric.scenario
        if self.fabric.physical_model:
            save(self.art/'physical-model.json',self.fabric.physical_model)
        self.scenario['topology']['generated_at']=now()
        self.scenario['topology']['valid_from']=now()
        save(self.art/'scenario.json',self.scenario)
        preflight=command(str(ROOT/'bin/sfctl'),'scenario','validate','--check-paths','--file',str(self.art/'scenario.json'))
        (self.art/'physical-path-preflight.log').write_text(preflight.stdout+preflight.stderr)
        save(self.art/'deployment-manifest.json',{'nodes':self.fabric.nodes,'links':[{'nodes':list(k),**v} for k,v in self.fabric.links.items()],
             'n3_gateways':self.fabric.pair,'orbital_scope':'per-satellite physical propagation' if self.fabric.physical_model else 'logical regression; no physical visibility'})
        self.event('constellation_configuration_bound',satellites=len(self.fabric.satellites),gateways=len(self.fabric.gateways),
                   nodes=len(self.fabric.nodes),directed_links=len(self.scenario['topology']['links']))

    def check_resources(self):
        f=self.fabric
        available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
        estimate=len(f.nodes)*40*1024**2+4*1024**3
        save(self.art/'resource-estimate.json',{'available_bytes':available,'estimated_additional_bytes':estimate,
            'per_router_and_onboard_capture_budget_mib':40,'fixed_cloud_5g_noc_budget_gib':4,'nodes':len(f.nodes),
            'calibration':'8-satellite pilot: 34.3–34.6 MiB per FRR satellite and 43.3 MiB per gateway before onboard/captures'})
        if estimate>available:
            raise RuntimeError(f'live {len(f.nodes)}-node deployment estimates {estimate/1024**3:.1f} GiB; available {available/1024**3:.1f} GiB; no memory-adapter fallback')

    def network(self):
        f=self.fabric
        self.event('create_frr_forwarding_network',nodes=len(f.nodes),links=len(f.links))
        def create(node):
            directory=self.art/'routers'/node; directory.mkdir(parents=True)
            (directory/'frr.conf').write_text(f.config(node))
            daemons='zebra=yes\nstaticd=yes\nisisd=yes\nospfd=yes\nospf6d=yes\nldpd=yes\nvtysh_enable=yes\n'
            if node in f.gateways: daemons+='bgpd=yes\n'
            (directory/'daemons').write_text(daemons)
            name=self.router(node)
            command('docker','run','-d','--name',name,'--label','starfabric.run='+self.identifier,'--network=none',
                    *getattr(self,'container_extra_args',[]),
                    '--cpuset-cpus',self.fabric_cpus,
                    '--cap-add=NET_ADMIN','--cap-add=NET_RAW','--cap-add=SYS_ADMIN',
                    '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv4.conf.all.rp_filter=0',
                    '--sysctl','net.ipv4.conf.default.rp_filter=0',
                    '--sysctl','net.ipv6.conf.all.forwarding=1','--sysctl','net.ipv6.conf.all.seg6_enabled=1',
                    '--sysctl','net.ipv6.conf.default.seg6_enabled=1','--sysctl','net.mpls.platform_labels=32768',
                    '--sysctl','net.mpls.conf.lo.input=1','-v',f'{directory}/daemons:/etc/frr/daemons:ro',
                    '-v',f'{directory}/frr.conf:/etc/frr/frr.conf:ro','-v',f'{self.frr_config_dir}/vtysh.conf:/etc/frr/vtysh.conf:ro',FRR)
            with self.resource_lock: self.containers.append(name)
            wait_for(node+' FRR startup',lambda:'isisd' in self.vty(node,'show daemons'),90)
            return json.loads(command('docker','inspect',name).stdout)[0]['State']['Pid']
        with self.operation('frr-create', '创建 FRR 容器并等待路由进程', '8 路并行创建；每节点路由进程就绪检查最长 90 秒'):
            self.pids=self.parallel(list(f.nodes),create,8)
        for node in ['nr_gnb','upf','nr_ue']:
            inspected=json.loads(command('docker','inspect',node).stdout)[0]
            assert inspected['Config']['Labels']['com.docker.compose.project']=='starfabric-5g' and inspected['State']['Running']
            self.pids[node]=inspected['State']['Pid']
        jobs=f.wiring(self.pids); save(self.art/'wiring.json',jobs)
        with self.operation('frr-wire', '连接卫星链路并配置接口与队列', f'{len(f.links)} 对初始链路；容器创建完成后继续连线', 900):
            result=command('docker','run','--rm','--privileged','--pid=host','--network=none',
                '--cpuset-cpus',self.fabric_cpus,
                '-v',f'{self.art}:/evidence:ro','-v',f'{ROOT}/lab/platform/wire.py:/wire.py:ro','--entrypoint','python3',TOOLS,
                '/wire.py','/evidence/wiring.json',timeout=900)
        (self.art/'wiring.log').write_text(result.stdout+result.stderr)
        self.interfaces={(a,b):v['interface'] for a,neighbors in f.adjacency.items() for b,v in neighbors.items()}
        def ready(node):
            def neighbors_ready():
                raw=self.vty(node,'show ip ospf neighbor')
                return sum('Full' in line for line in raw.splitlines())==sum(f.active(node,p) for p in f.adjacency[node])
            wait_for(node+' all OSPF neighbors',neighbors_ready,180)
            return {'pid':self.pids[node],'container':self.router(node),'daemons':self.vty(node,'show daemons'),
                    'ospf_neighbors':self.vty(node,'show ip ospf neighbor'),'isis_neighbors':self.vty(node,'show isis neighbor'),
                    'running_config':self.vty(node,'show running-config')}
        with self.operation('frr-neighbors', '等待全部 OSPF 邻居 Full', '逐节点检查实际邻居数量；每节点最长 180 秒'):
            save(self.art/'fleet-inventory.json',self.parallel(list(f.nodes),ready))
        self.checks['real_frr_isis_network']=True
        self.checks['all_configured_nodes_deployed']=True
        self.checks['all_configured_links_have_live_ospf_neighbors']=True

    def fleet_probe(self,phase):
        f=self.fabric
        def probe(node):
            target=f.pair[1] if node!=f.pair[1] else f.pair[0]
            result=command('docker','exec',self.router(node),'ping','-n','-c','2','-W','1',f.loopback(target),check=False)
            assert result.returncode==0 and '2 packets transmitted, 2 packets received' in result.stdout, (node,result.stdout,result.stderr)
            return result.stdout
        records=self.parallel(list(f.nodes),probe)
        save(self.art/f'fleet-probes-{phase}.json',records)
        self.checks['every_configured_node_passes_real_packet_probe']=len(records)
        self.event('fleet_packets_verified',phase=phase,nodes=len(records))

    def gateway_probe(self,phase):
        def probe(intent):
            dest=self.fabric.service_addresses[intent]
            src=next(v['source'] for v in self.fabric.gateway_intents if v['id']==intent)
            result=command('docker','exec',self.router(src),'ping','-n','-c','3','-W','1',dest,check=False)
            assert result.returncode==0 and '3 packets transmitted, 3 packets received' in result.stdout,(intent,result.stdout)
            return {'source':src,'destination':dest,'ping':result.stdout}
        save(self.art/f'gateway-business-{phase}.json',self.parallel(list(self.fabric.service_addresses),probe))
        self.checks['configured_gateway_intents_carry_real_packets']=len(self.fabric.service_addresses)

    def route_n3(self):
        for endpoint,dest,via,source in [('nr_gnb',UPF,'10.230.0.1',GNB),('upf',GNB,'10.230.0.5',UPF)]:
            self.exec(endpoint,'ip','route','add',dest+'/32','via',via,'src',source)
            self.routes_added.append((endpoint,dest,via))
        self.checks['gtpu_endpoints_routed_over_satellite_graph']=True
        self.continuous_log=(self.art/'continuous-ping.log').open('w')
        self.continuous=subprocess.Popen(['docker','exec','nr_ue','ping','-n','-D','-I','uesimtun0','-i','0.1','-w',str(self.business_duration),'-W','1','192.168.100.1'],stdout=self.continuous_log,stderr=subprocess.STDOUT)
        self.event('continuous_5g_business_started')

    def prepare_paths(self,initial):
        f=self.fabric; path=initial['plan']['paths']['n3-forward'][0]['nodes']
        self.fault_pair=tuple(path[-2:])
        self.capture_nodes=sorted({n for intent in ['n3-forward','n3-reverse'] for n in initial['plan']['paths'][intent][0]['nodes'] if n in f.satellites})
        assert self.capture_nodes
        save(self.art/'service-path-selection.json',{'fault_pair':self.fault_pair,'capture_nodes':self.capture_nodes,'plan':initial['plan']})
        self.protocols.capture_start('native')

    def fallback_hops(self,destination):
        f=self.fabric; distances={destination:0}; hops={}; queue=[(0,destination)]
        while queue:
            dist,node=heapq.heappop(queue)
            if dist!=distances[node]: continue
            for peer,link in f.adjacency[node].items():
                if not f.active(node,peer) or (not f.physical_model and {node,peer}==set(self.fault_pair)): continue
                weight=f.directed[peer,node]['latency_us'] if f.physical_model else link['cost']
                candidate=dist+weight
                if candidate<distances.get(peer,float('inf')):
                    distances[peer]=candidate; hops[peer]=node; heapq.heappush(queue,(candidate,peer))
        assert set(f.satellites)<=set(hops)
        return hops

    def start_onboard(self):
        f=self.fabric; forward=self.fallback_hops(f.pair[1]); reverse=self.fallback_hops(f.pair[0])
        self.fallback_forward=forward
        self.event('start_real_onboard_runtimes',satellites=len(f.satellites))
        def create(node):
            directory=self.art/(node+'-onboard'); directory.mkdir()
            config=json.loads((ROOT/'onboard/config.example.json').read_text()); config['node_id']=self.identifier+'/'+node
            config['fallback_routes']=[{'prefix':dest+'/32','via':f.hop(node,hops[node]),'interface':f.interface(node,hops[node]),'metric':42760}
                                       for dest,hops in [(UPF,forward),(GNB,reverse)]]
            save(directory/'config.json',config); name=self.router(node)+'-onboard'
            command('docker','run','-d','--name',name,'--label','starfabric.run='+self.identifier,'--network','container:'+self.router(node),
                    '--cpuset-cpus',self.fabric_cpus,
                    '--cap-add=NET_ADMIN','-e','TOKIO_WORKER_THREADS=1','-v',f'{directory}:/data','-v',f'{ROOT}/onboard/target/release/satellite-node-runtime:/sf-onboard:ro',
                    '--entrypoint','/sf-onboard',TOOLS,'--config','/data/config.json','--state-dir','/data/state','--listen','127.0.0.1:19080',
                    '--disconnect-hold-seconds','2','--heartbeat-timeout-seconds','20')
            with self.resource_lock: self.containers.append(name)
            return self.onboard_request(node,'/v1/status')
        save(self.art/'onboard-inventory.json',self.parallel(f.satellites,create))
        self.heartbeat_enabled.set()
        def heartbeat():
            while not self.stop_heartbeat.is_set():
                if self.heartbeat_enabled.is_set():
                    try:
                        self.request('/readyz'); self.generation+=1
                        self.parallel(f.satellites,lambda n:self.onboard_request(n,'/v1/connectivity',{'connected':True,'generation':self.generation}),16)
                    except (RuntimeError,OSError,ValueError): pass
                self.stop_heartbeat.wait(3)
        self.heartbeat_thread=threading.Thread(target=heartbeat,daemon=True);self.heartbeat_thread.start()
        wait_for('all satellites connected',lambda:all(s['connected'] for s in self.parallel(f.satellites,lambda n:self.onboard_request(n,'/v1/status'),16).values()),60)
        self.checks['onboard_runtime_on_every_configured_satellite']=len(f.satellites)

    def orbit_handover(self):
        if self.fabric.physical_model:
            from physics_runtime import PhysicalReplay
            self.physical_replay=PhysicalReplay(self)
            self.physical_replay.run()
            path=self.request('/api/v1/status')['committed_plan']['paths']['n3-forward'][0]['nodes']
            self.autonomy_node=next(n for n in path if n in self.fabric.satellites)
            return
        self.event('schedule_orbital_handover',mapped_contact=self.fault_pair)
        current=self.request('/api/v1/topology'); start=datetime.now(timezone.utc)
        end=start+timedelta(seconds=60); windows=[]
        for link in current['links']:
            if {link['source'],link['target']}==set(self.fault_pair):
                windows.append({'link':link,'start':(start-timedelta(minutes=1)).isoformat(),'end':end.isoformat()})
        request={'windows':windows,'horizon_seconds':80,'lead_seconds':45}
        save(self.art/'prediction.json',request)
        created=self.request('/api/v1/predictive/schedules',request); save(self.art/'predictive-created.json',created)
        result=wait_for('constellation predictive activation',lambda:(lambda s:s if s['state']=='completed' else None)(self.request('/api/v1/predictive/schedules?id='+created['id'])),180)
        save(self.art/'predictive-completed.json',result)
        status=self.request('/api/v1/status'); save(self.art/'predicted-status.json',status)
        path=status['committed_plan']['paths']['n3-forward'][0]['nodes']
        assert not any({a,b}==set(self.fault_pair) for a,b in zip(path,path[1:]))
        while datetime.now(timezone.utc)<end: time.sleep(0.1)
        for a,b in [self.fault_pair,self.fault_pair[::-1]]:self.exec(self.router(a),'ip','link','set',self.fabric.interface(a,b),'down')
        self.event('orbital_contact_physically_removed',plan_id=status['committed_plan']['id'],topology_version=status['committed_plan']['topology_version'])
        self.ping('orbital-handover');self.checks['orbit_prediction_switches_real_gtpu_path']=True
        self.autonomy_node=next(n for n in path if n in self.fabric.satellites)

    def autonomy(self):
        nodes=self.fabric.satellites;self.event('ground_controller_stopped');self.stop_controller()
        def states_ready():
            states=self.parallel(nodes,lambda n:self.onboard_request(n,'/v1/status'),16)
            return states if all(s['fallback_active'] and not s['connected'] for s in states.values()) else None
        states=wait_for('whole constellation heartbeat timeout',states_ready,90);save(self.art/'fleet-autonomous.json',states)
        self.event('onboard_heartbeat_timeout_installed_routes',satellites=len(nodes))
        node=self.autonomy_node;config=self.vty(node,'show running-config')
        routes=[line.strip() for line in config.splitlines() if line.strip().startswith(f'ip route {UPF}/32 ')]
        assert routes
        self.configure(node,*('no '+route for route in routes));self.event('ground_owned_route_withdrawn',node=node)
        active=json.loads(self.exec(self.router(node),'ip','-j','route','get',UPF))
        assert active[0].get('gateway')==self.fabric.hop(node,self.fallback_forward[node]),active
        save(self.art/'autonomous-kernel-fib.json',{'node':node,'lookup':active})
        self.ping('onboard-autonomy',20);self.heartbeat_enabled.clear();self.start_controller()
        if self.fabric.physical_model:
            self.physical_replay.refresh_held_snapshot()
        self.reconcile('ground-restored-commit');self.heartbeat_enabled.set()
        wait_for('all satellite fallbacks withdrawn',lambda:all(not s['fallback_active'] for s in self.parallel(nodes,lambda n:self.onboard_request(n,'/v1/status'),16).values()),90)
        self.ping('ground-recovered',20)
        self.checks.update(missing_ground_heartbeat_triggers_autonomy=True,onboard_installs_real_kernel_routes=True,
                           gtpu_survives_ground_route_withdrawal=True,ground_reconnect_withdraws_fallback=True)

    def capture_evidence(self):
        self.continuous.wait(timeout=75);self.continuous_log.close();self.continuous_log=None
        raw=(self.art/'continuous-ping.log').read_text()
        match=re.search(r'(\d+) packets transmitted, (\d+) received',raw)
        assert match,raw[-1000:]
        tx,rx=map(int,match.groups());loss=(tx-rx)*100/tx
        self.traffic.append({'phase':'continuous_across_all_faults','transmitted':tx,'received':rx,'loss_percent':loss})
        assert tx>=300 and loss<5,{'tx':tx,'rx':rx,'loss_percent':loss}
        self.protocols.capture_finish('native')
        self.checks.update(continuous_gtpu_business_across_faults=True,shared_scenario_and_fault_timeline=True)

    def cleanup(self):
        self.stop_heartbeat.set()
        if self.heartbeat_thread:self.heartbeat_thread.join(timeout=60)
        self.stop_controller()
        if self.continuous and self.continuous.poll() is None:
            command('docker','exec','nr_ue','pkill','-INT','-f',f'^ping -n -D -I uesimtun0 -i 0.1 -w {self.business_duration} ',check=False)
            self.continuous.wait(timeout=10)
        if self.continuous_log and not self.continuous_log.closed:self.continuous_log.close()
        for name,destination,gateway in self.routes_added:command('docker','exec',name,'ip','route','del',destination+'/32','via',gateway,check=False)
        def remove(name):
            result=command('docker','rm','-f',name,check=False)
            return result.stderr if result.returncode else None
        errors=[v for v in self.parallel(list(reversed(self.containers)),remove).values() if v]
        return errors
