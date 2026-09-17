import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lab.live.progress import Progress, collect_diagnostics, kubernetes_summary, monitor
from lab.live.service import Service, identity


class StartupProgressTests(unittest.TestCase):
    def test_begin_is_not_completion_and_failure_keeps_waiting_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'progress.json'
            p = Progress(path, 'one')
            p.begin('starting_cloud')
            with self.assertRaisesRegex(RuntimeError, 'probe timeout'):
                with p.operation('cilium-ready', '等待 Cilium', timeout=600):
                    raise RuntimeError('probe timeout')
            p.finish('starting_cloud', error='probe timeout')
            v = json.loads(path.read_text())
            self.assertEqual(v['tasks'][0]['state'], 'failed')
            self.assertEqual(next(s for s in v['stages'] if s['id']=='starting_cloud')['state'], 'failed')
            self.assertEqual(v['stages'][-1]['state'], 'pending')
            self.assertFalse(any(s['state']=='done' for s in v['stages']))

    def test_success_preserves_start_end_and_diagnostic_reads_do_not_advance_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            p = Progress(root/'reports/live/progress.json', 'one')
            p.begin('starting_cloud')
            with p.operation('nodes-ready', '等待节点', timeout=180):
                pass
            p.finish('starting_cloud')
            before = p.path.read_bytes()
            with patch('lab.live.progress.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
                d = collect_diagnostics(root, 'one')
            self.assertEqual(p.path.read_bytes(), before)
            self.assertTrue(d['docker_ok'])
            self.assertEqual(p.value['tasks'][0]['state'], 'done')
            self.assertIn('finished_at', p.value['tasks'][0])

    def test_another_session_never_leaks_previous_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root/'reports/live'
            p = Progress(directory/'progress.json', 'old')
            p.begin('starting_cloud')
            (directory/'owner.json').write_text(json.dumps({'pid':os.getpid(),'identity':identity(os.getpid()),'session':'new'}))
            (directory/'status.json').write_text(json.dumps({'phase':'starting_5g','session':'new'}))
            self.assertNotIn('startup', Service(root).status())
            with patch('lab.live.progress.subprocess.run') as run:
                collect_diagnostics(root, 'new')
                run.assert_not_called()

    def test_kubernetes_states_and_warnings_remain_distinct(self):
        result = kubernetes_summary({'items':[
            {'kind':'Node','metadata':{'name':'worker'},'status':{'conditions':[{'type':'Ready','status':'False','reason':'NetworkPluginNotReady'}]}},
            {'kind':'Pod','metadata':{'name':'cilium-x','namespace':'kube-system'},'spec':{'containers':[{}]},
             'status':{'phase':'Running','containerStatuses':[{'ready':False,'restartCount':3,'state':{'waiting':{'reason':'CrashLoopBackOff'}}}]}},
            {'kind':'Event','type':'Warning','metadata':{},'reason':'FailedScheduling','message':'waiting for network','count':2},
            {'kind':'Event','type':'Normal','metadata':{},'reason':'Scheduled'},
        ]})
        self.assertFalse(result['nodes'][0]['ready'])
        self.assertEqual(result['pods'][0]['ready'], 0)
        self.assertEqual(result['pods'][0]['reason'], 'CrashLoopBackOff')
        self.assertEqual(len(result['warnings']), 1)

    def test_diagnostic_command_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            p = Progress(root/'reports/live/progress.json', 'one')
            p.begin('starting_cloud')
            with patch('lab.live.progress.subprocess.run', side_effect=subprocess.TimeoutExpired('docker',4)):
                result = collect_diagnostics(root,'one')
            self.assertFalse(result['docker_ok'])
            self.assertIn('timed out',result['docker_error'])

    def test_monitor_takes_final_startup_sample_then_stops_polling_running_cluster(self):
        with tempfile.TemporaryDirectory() as folder:
            stop=Mock()
            stop.is_set.side_effect=[False,False,False,True]
            with patch('lab.live.progress.read',side_effect=[{'phase':'starting_cloud'},{'phase':'running'},{'phase':'running'}]), \
                    patch('lab.live.progress.collect_diagnostics',return_value={'session':'one','observed_at':'sample'}) as collect:
                monitor(folder,'one',stop)
            self.assertEqual(collect.call_count,2)
            self.assertEqual(json.loads((Path(folder)/'reports/live/diagnostics.json').read_text())['session'],'one')


if __name__ == '__main__':
    unittest.main()
