"""Execute a generated, owned-namespace wiring manifest in one short-lived helper."""
import json,subprocess,sys
from pathlib import Path
jobs=json.loads(Path(sys.argv[1]).read_text())
for index,job in enumerate(jobs):
    result=subprocess.run(['nsenter','-t',str(job['pid']),'-n','--',*job['args']],capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError(f'wire operation {index}: {job}: {result.stdout} {result.stderr}')
print(json.dumps({'executed':len(jobs),'success':True}))
