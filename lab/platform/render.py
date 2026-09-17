#!/usr/bin/env python3
"""Preview the exact graph/address/config binding consumed by the live platform."""
import argparse,hashlib,json
from pathlib import Path
from fabric import Fabric
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--scenario',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();raw=a.scenario.read_bytes();f=Fabric(json.loads(raw),'sf-preview')
a.output.mkdir(parents=True,exist_ok=True)
for node in f.nodes:
    directory=a.output/'routers'/node;directory.mkdir(parents=True,exist_ok=True)
    (directory/'frr.conf').write_text(f.config(node))
manifest={'action':'preview_only','instantiated':False,'input_sha256':hashlib.sha256(raw).hexdigest(),
          'satellites':len(f.satellites),'gateways':len(f.gateways),'frr_nodes':len(f.nodes),
          'nodes':f.nodes,'n3_gateways':f.pair,'links':[{'nodes':list(k),**v} for k,v in f.links.items()],
          'gateway_service_addresses':f.service_addresses,'bound_scenario':f.scenario,
          'estimated_additional_memory_gib':len(f.nodes)*40/1024+4,
          'boundary':'This is the same Fabric binding used by make test-unified. Rendering does not assert live deployment or forwarding.'}
manifest['physical_model']=bool(f.physical_model)
manifest['initial_active_pairs']=len(f.active_pairs())
if f.physical_model:
    manifest['physics']={'epoch':f.physical_model['config']['epoch'],'frames':len(f.physical_model['frames']),
        'satellite_orbits':f.physical_model['satellite_orbits'],'ground_stations':f.physical_model['config']['ground_stations'],
        'boundary':f.physical_model['boundary']}
(a.output/'deployment-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(f"LIVE DEPLOYMENT CONFIG: {len(f.satellites)} satellites + {len(f.gateways)} gateways, {len(f.links)} physical link pairs; {a.output}")
