#!/usr/bin/env python3
"""Check each physical frame using the same Go planner and live service binding."""
import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lab/platform'))
from fabric import Fabric


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenario',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    f=Fabric(json.loads(args.scenario.read_text()),'physical-preflight')
    records=[]
    with tempfile.TemporaryDirectory(prefix='sf-physical-preflight-') as directory:
        path=Path(directory)/'scenario.json'
        for frame in f.physical_model['frames']:
            scenario=copy.deepcopy(f.scenario)
            current={l['id']:l for l in frame['links']}
            scenario['topology']['links']=[current.get(l['id'],dict(l,operational_up=False,acquisition_state='unavailable')) for l in scenario['topology']['links']]
            path.write_text(json.dumps(scenario))
            result=subprocess.run([str(ROOT/'bin/sfctl'),'scenario','validate','--check-paths','--file',str(path)],capture_output=True,text=True,timeout=60)
            records.append({'offset_seconds':frame['offset_seconds'],'success':result.returncode==0,'output':result.stdout+result.stderr})
    report={'success':all(r['success'] for r in records),'scope':'offline physical-frame path feasibility; no packet or live deployment claim','frames':records}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'success':report['success'],'frames':len(records),'failed_frames':[r['offset_seconds'] for r in records if not r['success']]}))
    raise SystemExit(0 if report['success'] else 1)


if __name__=='__main__':main()
