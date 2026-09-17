"""Protocol carriers on the same config-driven FRR constellation and N3 session."""
from __future__ import annotations
import json,time
from run import GNB,UPF,TOOLS,command,save,wait_for,now
from pce import PCE
from packets import proof


class Protocols:
    def __init__(self,runtime):
        self.r=runtime;self.sequence=100;self.profile='native';self.captures=[];self.proofs={};self.pce=None;self.phase_windows={}
    @property
    def f(self):return self.r.fabric
    def ip(self,node,*args,check=True):return self.r.exec(self.r.router(node),'ip',*args,check=check)
    def path(self,intent):
        plan=self.r.request('/api/v1/status')['committed_plan']
        return plan,plan['paths'][intent][0]['nodes']
    def setup(self):
        a,b=self.f.pair
        wait_for('OSPFv3 remote SID locator',lambda:'ospf6' in self.r.vty(a,f'show ipv6 route {self.f.locator(b)}/64 json'),180)
        wait_for('BGP-LS active physical links',lambda:len(self.edges())==len(self.f.active_pairs())*2,180)
        missing={}
        def evpn_ready():
            nonlocal missing
            missing=self.r.parallel(self.f.gateways,self.evpn_missing,8)
            save(self.r.art/'evpn-convergence.json',{'at':now(),'missing':missing})
            return not any(missing.values())
        try:
            wait_for('all EVPN gateway SVIs',evpn_ready,180)
        except AssertionError as error:
            nodes=[n for n,peers in missing.items() if peers]
            detail=self.r.parallel(nodes,lambda n:{'summary':self.r.vty(n,'show bgp summary json'),
                'evpn':self.r.vty(n,'show bgp l2vpn evpn route type 2')},8)
            save(self.r.art/'evpn-convergence-failed.json',detail)
            raise AssertionError('EVPN gateway addresses missing: '+json.dumps(missing)) from error
        self.snapshot('protocol-initial')
        self.r.checks.update(ospfv3_routes_generated_sid_locators=True,evpn_all_configured_gateways=True)
        self.r.event('shared_constellation_protocols_configured',nodes=len(self.f.nodes),protocols=['OSPFv2','OSPFv3','LDP','SR-MPLS','SRv6','BGP-LS','EVPN/VXLAN'])
    def evpn_missing(self,node):
        # Read once per gateway, rather than once per remote SVI. Exact
        # bracketed addresses avoid matching .1 inside .10 or .11.
        table=self.r.vty(node,'show bgp l2vpn evpn route type 2')
        valid='\n'.join(line for line in table.splitlines() if line.lstrip().startswith('*'))
        return [peer for peer in self.f.gateways if peer!=node and '[32]:['+self.f.svi(peer)+']' not in valid]
    def edges(self):
        source,collector=self.f.pair
        rib=json.loads(self.r.vty(collector,'show bgp link-state link-state json'));edges=set()
        for paths in rib.get('routes',{}).values():
            for path in paths:
                nlri=path.get('nlri',{})
                if nlri.get('nlriType')!='link' or not path.get('valid') or path.get('peerId')!=self.f.loopback(source):continue
                ids=[nlri[k].get('igpRouterId','').lower() for k in ['localNodeDescriptors','remoteNodeDescriptors']]
                nodes=[self.f.by_system_id.get('.'.join(v.split('.')[:3])) for v in ids]
                if all(nodes):edges.add(tuple(nodes))
        return edges
    def sync_bgpls(self,down):
        pair={self.r.fault_pair,self.r.fault_pair[::-1]};expected=len(self.f.active_pairs())*2-(2 if down else 0)
        def matches():
            edges=self.edges()
            return len(edges)>=expected and (not(pair&edges) if down else pair<=edges)
        try:wait_for('BGP-LS contact update',matches,3)
        except AssertionError:
            source,collector=self.f.pair
            save(self.r.art/f'bgpls-stale-{self.sequence}.json',{'rib':json.loads(self.r.vty(collector,'show bgp link-state link-state json')),
                 'ted':self.r.vty(source,'show isis mpls-te database detail')})
            self.r.event('bgpls_exporter_resynchronizing',carrier=self.profile,contact_down=down)
            self.r.configure(source,'no router bgp 65000',*self.f.bgp_config(source))
            wait_for('BGP-LS exporter recovered',matches,120)
            self.r.event('bgpls_exporter_resynchronized',carrier=self.profile,contact_down=down)
    def snapshot(self,phase):
        queries=[('ospfv2','show ip ospf neighbor'),('ospfv3','show ipv6 ospf6 neighbor'),('ipv4-rib','show ip route json'),
                 ('ipv6-rib','show ipv6 route json'),('ldp','show mpls ldp neighbor'),('lfib','show mpls table json')]
        def node_state(node):
            values={name:self.r.vty(node,query) for name,query in queries}
            if node in self.f.gateways:values.update(bgpls=self.r.vty(node,'show bgp link-state link-state json'),evpn=self.r.vty(node,'show bgp l2vpn evpn route type 2'))
            return values
        # Whole-fleet initial evidence; selected service routers on each fault.
        nodes=list(self.f.nodes) if phase=='protocol-initial' else sorted(set(self.path('n3-forward')[1]+self.path('n3-reverse')[1]))
        save(self.r.art/(phase+'-protocol-state.json'),self.r.parallel(nodes,node_state))
    def setup_pce(self):
        r=self.r;self.pce=PCE(self,r.cloud.gateway);relay=r.router('pce-management')
        command('docker','run','-d','--name',relay,'--label','starfabric.run='+r.identifier,'--network=kind','--cap-add=NET_ADMIN',
                '--sysctl','net.ipv4.ip_forward=1','--entrypoint','sleep',TOOLS,'86400');r.containers.append(relay)
        r.exec(relay,'iptables','-t','nat','-A','POSTROUTING','-s','10.233.0.0/16','-o','eth0','-j','MASQUERADE')
        pid=json.loads(command('docker','inspect',relay).stdout)[0]['State']['Pid']
        for i,node in enumerate(self.f.pair):
            helper=['docker','run','--rm','--privileged','--pid=host','--network=none','--entrypoint','nsenter',TOOLS,'-t',str(pid),'-n','--','ip','link']
            command(*helper,'add',f'pce{i}','type','veth','peer','name',f'pce-peer{i}')
            command(*helper,'set',f'pce-peer{i}','netns',str(r.pids[node]));self.ip(node,'link','set',f'pce-peer{i}','name','pce-mgmt')
            for container,interface,host in [(relay,f'pce{i}',1),(r.router(node),'pce-mgmt',2)]:
                r.exec(container,'ip','address','add',f'10.233.{i}.{host}/30','dev',interface);r.exec(container,'ip','link','set',interface,'up')
            self.ip(node,'route','add',r.cloud.gateway+'/32','via',f'10.233.{i}.1','dev','pce-mgmt')
            command('docker','exec','-d',r.router(node),'/usr/lib/frr/pathd','-F','traditional','-M','pathd_pcep','--log','file:/tmp/platform-pathd.log')
            wait_for(node+' pathd',lambda:'pathd' in r.vty(node,'show daemons'),30)
            r.configure(node,'segment-routing','traffic-eng','pcep','pce-config MGMT',f'source-address ip 10.233.{i}.2','exit',
                        'pce PLATFORM','config MGMT','address ip '+r.cloud.gateway,'exit','pcc','msd 32','peer PLATFORM precedence 10')
            wait_for(node+' PCEP session',lambda:'UP' in r.vty(node,'show sr-te pcep session json'),60)
        self.refresh_pce()
    def refresh_pce(self):
        r=self.r;start=len(self.pce.responses)
        for node,target,_,_ in self.f.service():
            r.configure(node,'segment-routing','traffic-eng',f'no policy color 100 endpoint {self.f.loopback(target)}')
            r.configure(node,'segment-routing','traffic-eng',f'policy color 100 endpoint {self.f.loopback(target)}','name PLATFORM-N3',
                        'binding-sid 24001','candidate-path preference 200 name CLOUD dynamic')
        wait_for('positive current-plan PCEP paths',lambda:len(self.pce.responses)>=start+2,90)
        for node,target,_,intent in self.f.service():
            plan,path=self.path(intent);expected=[self.f.sid(n) for n in path[2:]]
            def installed():
                table=json.loads(r.vty(node,'show mpls table json')).get('24001',{})
                return table.get('installed') and any(h.get('installed') and h.get('type')=='SR-TE' and h.get('outLabelStack')==expected for h in table.get('nexthops',[])) and any(v['intent']==intent and v['plan_id']==plan['id'] for v in self.pce.responses[start:])
            wait_for(node+' PCEP binding SID LFIB',installed,90)
            save(r.art/f'pcep-{node}-{len(self.pce.responses)}.json',{'policy':r.vty(node,'show sr-te policy detail'),'lfib':r.vty(node,'show mpls table json'),'responses':self.pce.responses})
    def qualify_sr(self):
        r=self.r;topology=r.request('/api/v1/topology')
        down=any(not l['operational_up'] and {l['source'],l['target']}==set(r.fault_pair) for l in topology['links'])
        targets=sorted({n for intent in ['n3-forward','n3-reverse'] for n in self.path(intent)[1][1:]})
        excluded=r.fault_pair if down else ()
        def tables():return r.parallel(list(self.f.nodes),lambda n:json.loads(r.vty(n,'show mpls table json')))
        observations=[]
        def ready():
            bad=self.f.sr_mismatches(tables(),targets,excluded)
            observations.append({'at':now(),'mismatches':bad})
            return not bad
        try:
            try:wait_for('SR LFIB matches current shortest paths',ready,12)
            except AssertionError:
                repair=sorted({v['target'] for v in observations[-1]['mismatches']})
                r.event('sr_prefixes_resynchronizing',carrier=self.profile,targets=repair)
                # Withdraw/reannounce the actual IS-IS Prefix-SID. Do not write
                # synthetic MPLS entries to make the packet test pass.
                r.parallel(repair,lambda n:r.configure(n,'router isis SF',f'no segment-routing prefix {self.f.loopback(n)}/32'))
                def withdrawn():
                    return all(str(self.f.sid(target)) not in table for node,table in tables().items() for target in repair if node!=target)
                wait_for('stale SR labels withdrawn throughout fleet',withdrawn,60)
                r.parallel(repair,lambda n:r.configure(n,'router isis SF',f'segment-routing prefix {self.f.loopback(n)}/32 index {self.f.ordinal[n]}'))
                wait_for('reannounced SR labels have valid next hops',ready,60)
                r.event('sr_prefixes_resynchronized',carrier=self.profile,targets=repair)
            r.checks['sr_lfib_matches_current_constellation_paths']=True
        finally:save(r.art/f'sr-lfib-qualification-{self.profile}-{self.sequence}.json',observations)
    def select(self,profile):
        self.profile=profile;r=self.r
        if profile in ['sr-mpls','pcep']:self.qualify_sr()
        for node,remote,dest,intent in self.f.service():
            flushed=command('docker','exec',r.router(node),'ip','route','flush','table','300',check=False)
            if flushed.returncode and 'FIB table does not exist' not in flushed.stderr:raise RuntimeError(flushed.stderr)
            if profile=='native':continue
            plan,path=self.path(intent);interface=self.f.interface(node,path[1]);hop=self.f.hop(node,path[1])
            args=['route','replace','table','300',dest+'/32']
            if profile=='ospf':args+=['dev','sf-ospf']
            elif profile=='evpn':args+=['via',self.f.svi(remote),'dev','br100']
            elif profile=='srv6':
                segments=[self.f.locator(p)+'1' for p in path[1:-1]]+[self.f.locator(remote)+'4']
                args+=['encap','seg6','mode','encap','segs',','.join(segments),'dev',interface]
            elif profile=='pcep':args+=['encap','mpls','24001','dev','lo']
            elif profile in ['ldp','sr-mpls']:
                if profile=='ldp':
                    def binding():
                        for line in r.vty(node,f'show mpls ldp ipv4 binding {self.f.loopback(remote)}/32').splitlines():
                            fields=line.split()
                            if len(fields)>=5 and fields[0]=='ipv4' and fields[2]==self.f.loopback(path[1]) and fields[4].isdigit():return fields[4]
                        return None
                    labels=wait_for(node+' LDP binding',binding,90)
                else:labels='/'.join(str(self.f.sid(p)) for p in path[2:])
                args+=['encap','mpls',labels,'via','inet',hop,'dev',interface]
            else:raise ValueError(profile)
            self.ip(node,*args)
            save(r.art/f'{profile}-{node}-{plan["id"]}-policy.json',{'run_id':r.identifier,'plan_id':plan['id'],'nodes':path,'route':json.loads(self.ip(node,'-j','route','show','table','300'))})
        r.event('business_carrier_selected',carrier=profile)
    def restored_underlay(self):
        observations=[];consecutive=0;started=time.monotonic();a,b=self.r.fault_pair
        def ready():
            nonlocal consecutive
            neighbors={n:self.r.vty(n,'show mpls ldp neighbor') for n in [a,b]}
            ldp=all(any(self.f.loopback(peer) in line and 'OPERATIONAL' in line for line in neighbors[node].splitlines()) for node,peer in [(a,b),(b,a)])
            probes={node:command('docker','exec',self.r.router(node),'ping','-n','-c','1','-W','1',self.f.loopback(peer),check=False) for node,peer,_,_ in self.f.service()}
            observations.append({'at':now(),'ldp_neighbors':neighbors,'probes':{n:{'exit_code':p.returncode,'output':p.stdout+p.stderr} for n,p in probes.items()}})
            consecutive=consecutive+1 if ldp and all(v.returncode==0 for v in probes.values()) else 0
            return consecutive>=3
        try:wait_for('restored LDP and gateway forwarding',ready,90)
        finally:save(self.r.art/f'{self.profile}-{self.sequence}-underlay-recovery.json',observations)
        self.r.event('protocol_contact_forwarding_ready',carrier=self.profile,elapsed_seconds=time.monotonic()-started)
        self.r.checks['restored_contact_qualified_by_real_forwarding']=True
    def fault(self,down):
        r=self.r;self.sequence+=1;r.event('protocol_contact_down' if down else 'protocol_contact_up',carrier=self.profile,sequence=self.sequence)
        for a,b in [r.fault_pair,r.fault_pair[::-1]]:self.ip(a,'link','set',self.f.interface(a,b),'down' if down else 'up')
        if not down:self.restored_underlay()
        topology=r.request('/api/v1/topology')
        for link in topology['links']:
            if {link['source'],link['target']}!=set(r.fault_pair):continue
            link['operational_up']=not down
            r.request('/api/v1/topology/events',{'event_id':f"{r.identifier}-{self.sequence}-{link['id']}",'type':'link_update','subject':link['id'],
                'sequence':self.sequence,'observed_at':now(),'effective_at':now(),'link':link})
        r.reconcile(self.profile+('-fault' if down else '-restore')+'-commit');self.sync_bgpls(down)
        if self.profile=='pcep':self.refresh_pce()
        self.select(self.profile);time.sleep(3)
    def capture_start(self,profile):
        name=self.r.router(profile+'-fleet-capture')
        save(self.r.art/'capture-namespaces.json',{n:self.r.pids[n] for n in self.f.satellites})
        command('docker','run','-d','--name',name,'--label','starfabric.run='+self.r.identifier,'--privileged','--pid=host','--network=none',
                '--cpuset-cpus',self.r.fabric_cpus,
                '-v',f'{self.r.art}:/evidence','-v',f'{self.r.art.parents[3]}/lab/platform/capture.py:/capture.py:ro',
                '--entrypoint','python3',TOOLS,'/capture.py','/evidence/capture-namespaces.json',profile)
        self.r.containers.append(name);self.captures.append((profile,name))
        wait_for(profile+' all satellite captures',lambda:(self.r.art/f'{profile}-capture-ready.json').exists(),90)
    def capture_finish(self,profile):
        name=next(name for carrier,name in self.captures if carrier==profile)
        command('docker','kill','--signal=INT',name)
        result=command('docker','wait',name)
        assert result.stdout.strip()=='0',command('docker','logs',name).stdout
        records={node:proof(self.r.art/f'{profile}-{node}.pcap',profile,GNB,UPF,False) for node in self.f.satellites}
        used={node:v for node,v in records.items() if v['uplink'] or v['downlink']}
        assert sum(v['uplink'] for v in used.values())>0 and sum(v['downlink'] for v in used.values())>0,profile
        phases={}
        for phase,window in self.phase_windows.get(profile,{}).items():
            values={node:proof(self.r.art/f'{profile}-{node}.pcap',profile,GNB,UPF,False,window) for node in self.f.satellites}
            uplink=sum(v['uplink'] for v in values.values());downlink=sum(v['downlink'] for v in values.values())
            assert uplink>0 and downlink>0,(profile,phase,'encapsulated N3 missing during phase')
            phases[phase]={'window':window,'uplink':uplink,'downlink':downlink,
                           'forwarding_satellites':[n for n,v in values.items() if v['uplink'] or v['downlink']]}
        self.proofs[profile]={'captured_satellites':len(records),'forwarding_satellites':used,'phases':phases}
        save(self.r.art/'protocol-packet-proofs.json',self.proofs)
        if profile!='native':self.r.checks[profile+'_same_gtpu_business_and_fault_recovery']=True
        else:self.r.checks['satellite_bidirectional_gtpu_capture']=True
    def business(self,profile,phase):
        started=time.time()
        self.r.ping(profile+'-'+phase,20)
        self.phase_windows.setdefault(profile,{})[phase]=[started,time.time()]
    def exercise(self):
        self.setup_pce()
        for profile in ['ospf','ldp','sr-mpls','pcep','srv6','evpn']:
            if profile=='pcep':self.refresh_pce()
            self.select(profile);self.capture_start(profile);self.business(profile,'initial')
            self.fault(True);self.business(profile,'fault-recovered');self.snapshot(profile+'-fault')
            self.r.gateway_probe(profile+'-fault');self.fault(False);self.business(profile,'restored');self.capture_finish(profile)
        self.select('native');self.r.checks['bgpls_live_contact_withdrawal_and_restoration']=True
    def cleanup(self):
        if self.pce:self.pce.close()
