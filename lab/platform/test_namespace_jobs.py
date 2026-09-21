"""Owned namespace boundaries, contact ordering and helper lifecycle."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import namespace_jobs
import wire


class NamespaceJobsTests(unittest.TestCase):
    def test_parallel_contacts_keep_each_contact_in_order(self):
        other_started = threading.Event()
        completed = []
        def run(command, **kwargs):
            pid, operation = command[2], command[-1]
            if pid == '1' and operation == 'first':
                self.assertTrue(other_started.wait(2))
            if pid == '2' and operation == 'first':
                other_started.set()
            completed.append((pid, operation))
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        groups = [[{'pid':pid, 'args':['ip', operation]} for operation in ('first', 'second')] for pid in (1, 2)]
        with patch.object(wire.subprocess, 'run', side_effect=run):
            self.assertEqual(wire.execute_groups(groups, 2)['executed'], 4)
        for pid in ('1', '2'):
            self.assertLess(completed.index((pid, 'first')), completed.index((pid, 'second')))

    def test_manifest_cannot_escape_run_or_target_unowned_namespace(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(namespace_jobs, 'identity', return_value='original'):
            dispatcher = namespace_jobs.Dispatcher({'folder':folder, 'nodes':{'a':11}})
            path = Path(folder)/'jobs.json'
            body = {'mode':'wire', 'manifest':path.name}
            path.write_text(json.dumps([[{'pid':11, 'args':['ip','link','set','sf1','down']}]]))
            with patch.object(wire, 'execute_groups', return_value={'executed':1}) as execute:
                self.assertEqual(dispatcher.execute(body), {'executed':1})
                execute.assert_called_once()
            for args, pid in [(['sh','-c','true'],11), (['ip','link','show'],12),
                              (['ip','link','set','sf1','netns','12'],11),
                              (['tc','qdisc','change','dev','sf1\nqdisc del'],11)]:
                path.write_text(json.dumps([[{'pid':pid,'args':args}]]))
                with self.subTest(args=args), self.assertRaises(ValueError):
                    dispatcher.execute(body)
            with self.assertRaises(ValueError):
                dispatcher.execute(dict(body, manifest='../jobs.json'))
            path.write_text(json.dumps([[{'pid':11,'args':['ip','link','show']}]]))
            with patch.object(namespace_jobs, 'identity', return_value='reused'):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    dispatcher.execute(body)

    def test_only_contact_configuration_runs_in_router_mount_namespace(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(namespace_jobs, 'identity', return_value='original'):
            dispatcher = namespace_jobs.Dispatcher({'folder':folder, 'nodes':{'a':11}})
            path = Path(folder)/'contact.json'
            path.write_text(json.dumps({'a':['interface sf1','ip router isis SF','isis metric level-2 1','exit']}))
            with patch.object(namespace_jobs.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(dispatcher.execute({'mode':'configure','manifest':path.name}), {'configured':['a']})
                args=run.call_args.args[0]
                self.assertIn('--mount',args)
                self.assertIn('--net',args)
                self.assertIn('--root=/proc/11/root',args)
                self.assertEqual(args[-2:],['-c','end'])
            path.write_text(json.dumps({'a':['shell touch /tmp/no']}))
            with self.assertRaises(ValueError):
                dispatcher.execute({'mode':'configure','manifest':path.name})

    def test_worker_is_reused_and_registered_for_normal_run_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            r=SimpleNamespace(art=Path(folder), pids={'a':11}, fabric=SimpleNamespace(nodes=['a']),
                router=lambda n:'owned-'+n, identifier='owned', fabric_cpus='2,3',
                resource_lock=threading.Lock(), containers=[])
            worker=namespace_jobs.NamespaceJobs(r)
            # The client imports the platform command module lazily. A small
            # stand-in avoids pulling in Docker or unrelated runtime modules.
            calls=[]
            def command(*args):
                calls.append(args)
                worker.socket.touch()
            module=SimpleNamespace(command=command, save=lambda p,v:p.write_text(json.dumps(v)),
                                   ROOT=Path(folder), TOOLS='test-tools')
            with patch.dict(sys.modules, {'run':module}), patch.object(namespace_jobs, 'request', return_value={'ok':True,'result':{}}):
                worker.run('wire','a.json')
                worker.run('channels','b.json')
            self.assertEqual(len(calls),1)
            self.assertEqual(r.containers,['owned-namespace-jobs'])
            self.assertIn('--network=none',calls[0])
            self.assertFalse(any('docker.sock' in arg for arg in calls[0]))


if __name__ == '__main__':
    unittest.main()
