#!/usr/bin/env python3
"""Exercise actual Rust fallback updates against changing kernel routes."""
import json
import subprocess
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
TOOLS='ghcr.io/herlesupreeth/docker_open5gs@sha256:985d89d67ad9c3e25ea8a98b0897c14a2420dc15909c69f3e0794a043327407c'


def main():
    name='sf-live-autonomy-'+uuid.uuid4().hex[:8]
    folder=ROOT/'reports/live-autonomy-check'/name
    folder.mkdir(parents=True)
    config=json.loads((ROOT/'onboard/config.example.json').read_text())
    config.update(node_id=name,fallback_routes=[],fallback_targets=[
        {'prefix':'172.22.0.8/32','gateway_loopback':'10.255.0.2','metric':42760}])
    (folder/'config.json').write_text(json.dumps(config))
    def command(*args,check=True):
        return subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=30,check=check)
    def execute(*args,check=True):
        return command('docker','exec',name,*args,check=check)
    def request(path,body=None):
        args=['curl','-sf','--max-time','2','-H','Content-Type: application/json']
        if body is not None: args+=['--data-binary',json.dumps(body)]
        return json.loads(execute(*args,'http://127.0.0.1:19080'+path).stdout)
    def wait(predicate):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                result=predicate()
                if result:return result
            except (subprocess.CalledProcessError,KeyError,ValueError):pass
            time.sleep(.2)
        raise AssertionError('dynamic onboard condition did not converge')
    checks={}
    try:
        command('docker','run','-d','--name',name,'--label','starfabric.run='+name,'--network=none',
            '--cap-add=NET_ADMIN','-v',f'{folder}:/data','-v',f'{ROOT}/onboard/target/release/satellite-node-runtime:/sf:ro',
            '--entrypoint','sleep',TOOLS,'infinity')
        for interface,address in [('up1','10.10.0.1/30'),('up2','10.10.0.5/30')]:
            execute('ip','link','add',interface,'type','dummy')
            execute('ip','link','set',interface,'up')
            execute('ip','address','add',address,'dev',interface)
        execute('ip','route','replace','10.255.0.2/32','via','10.10.0.2','dev','up1')
        command('docker','exec','-d',name,'/sf','--config','/data/config.json','--state-dir','/data/state',
            '--listen','127.0.0.1:19080','--heartbeat-timeout-seconds','2','--disconnect-hold-seconds','1')
        wait(lambda:request('/v1/status'))
        request('/v1/connectivity',{'connected':True,'generation':2})
        wait(lambda:request('/v1/status')['fallback_active'])
        def gateway():
            response=execute('ip','-j','route','get','172.22.0.8',check=False)
            return json.loads(response.stdout)[0].get('gateway') if response.returncode==0 else None
        wait(lambda:gateway()=='10.10.0.2')
        checks['heartbeat_loss_installs_real_route']=True
        execute('ip','route','replace','10.255.0.2/32','via','10.10.0.6','dev','up2')
        wait(lambda:gateway()=='10.10.0.6')
        checks['autonomous_next_hop_changes_without_ground_heartbeat']=True
        execute('ip','route','del','10.255.0.2/32')
        wait(lambda:not request('/v1/status')['fallback_routes'])
        assert gateway() is None
        checks['unreachable_gateway_withdraws_stale_fallback']=True
        execute('ip','route','replace','10.255.0.2/32','via','10.10.0.2','dev','up1')
        wait(lambda:gateway()=='10.10.0.2')
        request('/v1/connectivity',{'connected':True,'generation':3})
        assert not request('/v1/status')['fallback_active']
        assert gateway() is None
        checks['ground_recovery_withdraws_current_fallback']=True
        result={'success':True,'checks':checks,'artifacts':str(folder.relative_to(ROOT))}
        (ROOT/'reports/live-onboard-dynamic-check.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:
        command('docker','rm','-f',name,check=False)


if __name__=='__main__':main()
