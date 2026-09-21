"""Execute generated wiring, preserving dependencies within each contact."""
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


def execute(jobs, timeout=60):
    for index, job in enumerate(jobs):
        result = subprocess.run(['nsenter', '-t', str(job['pid']), '-n', '--', *job['args']],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'wire operation {index}: {job}: {result.stdout} {result.stderr}')
    return len(jobs)


def execute_groups(groups, workers=8, timeout=60):
    # Each contact has unique interface names. Its veth creation/move/address
    # dependencies remain serial while unrelated contacts can be wired together.
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        counts = list(pool.map(lambda jobs: execute(jobs, timeout), groups))
    return {'executed': sum(counts), 'success': True}


if __name__ == '__main__':
    jobs = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps({'executed': execute(jobs), 'success': True}))
