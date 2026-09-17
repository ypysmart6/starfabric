"""Temporary, recorded host limits required by a combined kind/FRR SIL run."""
from pathlib import Path
import json
import os

from run import command, save, TOOLS


class HostBudget:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.changed = {}
        self.radio = []
        self.affinity = None

    def prepare(self):
        self.affinity = sorted(os.sched_getaffinity(0))
        if len(self.affinity) < 2:
            raise RuntimeError('live constellation and software radio require at least two available CPUs')
        self.radio_cpu = str(self.affinity[0])
        self.data_cpu_set = ','.join(map(str,self.affinity[1:]))
        # UERANSIM RLS timers must not be starved while hundreds of FRR/Rust
        # processes start. Reserve one CPU for the owned radio pair and keep
        # the driver, its children, cloud and constellation on the remainder.
        for name in ['nr_gnb','nr_ue']:
            info=json.loads(command('docker','inspect',name).stdout)[0]
            assert info['Config']['Labels']['com.docker.compose.project']=='starfabric-5g'
            record={'name':name,'id':info['Id'],'before_cpu_set':info['HostConfig']['CpusetCpus'],
                    'before_cpu_shares':info['HostConfig']['CpuShares']}
            self.radio.append(record)
            self.save_radio()
            command('docker','update','--cpuset-cpus',self.radio_cpu,'--cpu-shares','8192',name)
        os.sched_setaffinity(0,self.affinity[1:])
        for name, required in (("max_user_instances", 4096), ("max_user_watches", 524288)):
            path = Path("/proc/sys/fs/inotify") / name
            old = int(path.read_text())
            if old >= required:
                continue
            self.changed[name] = {"before": old, "during": required}
            save(self.artifacts / "host-budget.json", self.changed)
            command("docker", "run", "--rm", "--privileged", "--network=none", "--entrypoint", "sysctl", TOOLS,
                    "-w", f"fs.inotify.{name}={required}")

    def save_radio(self):
        save(self.artifacts/'radio-cpu-budget.json',{'available_cpus':self.affinity,'radio_cpu':self.radio_cpu,
             'fabric_cpus':self.data_cpu_set,'radio_containers':self.radio})

    def limit_containers(self,names, cpu_shares=None):
        if names:
            args=['docker','update','--cpuset-cpus',self.data_cpu_set]
            if cpu_shares is not None:args+=['--cpu-shares',str(cpu_shares)]
            command(*args,*names)

    def cleanup(self):
        for record in self.radio:
            result=command('docker','inspect',record['name'],check=False)
            if result.returncode:
                record['already_removed']=True
                continue
            info=json.loads(result.stdout)[0]
            if info['Id']==record['id'] and info['HostConfig']['CpusetCpus']==self.radio_cpu and info['HostConfig']['CpuShares']==8192:
                command('docker','update','--cpuset-cpus',record['before_cpu_set'],'--cpu-shares',str(record['before_cpu_shares']),record['name'])
                record['restored']=True
            else:record['skipped_external_change']=True
        if self.affinity:os.sched_setaffinity(0,self.affinity)
        if self.radio:self.save_radio()
        for name, values in self.changed.items():
            current = int((Path("/proc/sys/fs/inotify") / name).read_text())
            # Do not undo another operator's change made while the run was active.
            if current == values["during"]:
                command("docker", "run", "--rm", "--privileged", "--network=none", "--entrypoint", "sysctl", TOOLS,
                        "-w", f"fs.inotify.{name}={values['before']}")
                values["restored"] = True
            else:
                values["skipped_external_change"] = current
        save(self.artifacts / "host-budget.json", self.changed)
