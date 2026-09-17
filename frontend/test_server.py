"""Data provenance, file boundaries, and read-only HTTP contract checks."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from frontend.server import Dashboard, Handler, ROOT, ThreadingHTTPServer


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = Dashboard()
        cls.snapshot = cls.dashboard.snapshot('sf-unified-d180f84868')
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.http.dashboard = cls.dashboard
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def test_all_satellites_and_all_frames_come_from_physical_evidence(self):
        d = self.snapshot
        self.assertEqual(len(d['nodes']), 124)
        self.assertEqual(len([n for n in d['nodes'] if n['kind'] == 'satellite']), 120)
        self.assertEqual(len(d['frames']), 31)
        self.assertEqual(d['frames'][0]['active_pairs'], 250)
        self.assertEqual(d['frames'][-1]['active_pairs'], 249)
        for frame in d['frames']:
            self.assertEqual(len(frame['positions']), 120)
            self.assertEqual(len(frame['ground']), 4)

    def test_route_replay_uses_commits_and_holds_unchanged_plans(self):
        d = self.snapshot
        self.assertEqual(d['plans'][0]['id'], d['plans'][1]['id'])
        self.assertNotEqual(d['plans'][1]['id'], d['plans'][2]['id'])
        self.assertEqual(sum(f['routes_reprogrammed'] for f in d['replay']['frames']), 10)
        self.assertEqual(len(d['intents']), 10)
        self.assertEqual(d['plans'][0]['paths']['n3-forward'][0]['nodes'],
                         ['gw-001', 'sat-0063', 'sat-0044', 'sat-0072', 'sat-0053', 'gw-002'])

    def test_archived_states_and_proofs_preserve_scope(self):
        d = self.snapshot
        self.assertTrue(d['run']['physical_runtime_integration'])
        self.assertFalse(d['run']['full_protocol_integration'])
        self.assertEqual(set(d['proofs']), {'native'})
        self.assertEqual(len(d['pings']), 3853)
        self.assertEqual(len(d['evidence']), 872)
        self.assertTrue(all(n['autonomous']['fallback_active'] for n in d['onboard'].values()))
        self.assertTrue(all(not n['final']['fallback_active'] for n in d['onboard'].values()))

    def test_unknown_run_does_not_silently_fall_back(self):
        for run in ('nonexistent', '../../etc/passwd'):
            with self.assertRaises(FileNotFoundError):
                self.dashboard.snapshot(run)

    def test_failed_run_has_no_fabric_or_invented_onboard_state(self):
        d = self.dashboard.snapshot('sf-unified-0209fe1206')
        self.assertFalse(d['run']['success'])
        self.assertEqual(d['run']['deployed_counts']['frr_nodes'], 0)
        self.assertTrue(all(n['autonomous'] is None and n['final'] is None for n in d['onboard'].values()))

    def test_only_allowed_sources_and_run_artifacts_can_be_read(self):
        valid = self.dashboard.allowed_file('onboard/src/main.rs')
        self.assertTrue(valid.is_file())
        self.assertTrue(self.dashboard.allowed_file('frontend/server.py').is_file())
        for path in ('../.bashrc', '/etc/passwd', 'go.sum/../.gitignore-not-real', 'reports/live/owner.json'):
            with self.assertRaises(FileNotFoundError):
                self.dashboard.allowed_file(path)

    def test_symlink_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            root = Path(folder)
            (root / 'docs').mkdir()
            (root / 'docs/120-satellite-file-index.md').write_text('| [allowed.txt](../allowed.txt) | test |\n')
            target = Path(outside) / 'private.txt'
            target.write_text('not exposed')
            (root / 'allowed.txt').symlink_to(target)
            with self.assertRaises(FileNotFoundError):
                Dashboard(root).allowed_file('allowed.txt')

    def test_live_mode_is_explicitly_unconfigured(self):
        result = self.dashboard.live()
        self.assertFalse(result['configured'])
        self.assertFalse(result['connected'])

    def test_http_read_only_and_static_traversal(self):
        for path in ('/../server.py', '/%2e%2e/server.py', '/api/file?path=/etc/passwd'):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.origin + path)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.origin + '/api/reconcile', data=b'{}', method='POST'))
        self.assertEqual(caught.exception.code, 405)

    def test_globe_modules_and_fixed_image_assets(self):
        for path in ('/solar.js', '/earth-surface.js', '/assets/earth-day.png', '/assets/earth-night.png'):
            with urlopen(self.origin + path) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), (ROOT/'frontend'/path.lstrip('/')).read_bytes())
                if path.endswith('.png'):
                    self.assertEqual(response.headers['Content-Type'], 'image/png')
        for path in ('/assets/../server.py', '/assets/CREDITS.md', '/assets/%2e%2e/server.py'):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.origin + path)
            self.assertEqual(caught.exception.code, 404)

    def test_runtime_actions_reject_cross_origin_and_form_requests(self):
        from unittest.mock import patch
        for headers in ({'Content-Type':'application/json','Origin':'https://untrusted.example'},
                        {'Content-Type':'application/x-www-form-urlencoded'}):
            with patch.object(self.dashboard.service,'start') as start:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(self.origin+'/api/runtime/start',data=b'{}',headers=headers,method='POST'))
                self.assertEqual(caught.exception.code,403)
                start.assert_not_called()

    def test_runtime_action_uses_fixed_local_service(self):
        from unittest.mock import patch
        with patch.object(self.dashboard.service,'stop',return_value={'active':True,'phase':'stopping'}) as stop:
            with urlopen(Request(self.origin+'/api/runtime/stop',data=b'{}',headers={'Content-Type':'application/json'},method='POST')) as response:
                self.assertEqual(response.status,202)
                self.assertEqual(json.load(response)['phase'],'stopping')
            stop.assert_called_once_with()

    def test_empty_live_workspace_is_pending_not_historical(self):
        with tempfile.TemporaryDirectory() as folder:
            result=Dashboard(folder).live_snapshot()
            self.assertTrue(result['pending'])
            self.assertEqual(result['mode'],'live')
            self.assertFalse(result['runtime']['active'])
            self.assertNotIn('frames',result)

    def test_download_headers_and_data(self):
        with urlopen(self.origin + '/api/file?path=onboard/src/main.rs&download=1') as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.read(), (ROOT/'onboard/src/main.rs').read_bytes())

    def test_http_live_controller_reads_only_known_endpoints(self):
        from http.server import BaseHTTPRequestHandler
        seen = []
        class MockController(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.command, self.path))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ready"}')
            def log_message(self, *_):
                pass
        mock = ThreadingHTTPServer(('127.0.0.1', 0), MockController)
        worker = threading.Thread(target=mock.serve_forever, daemon=True)
        worker.start()
        try:
            result = Dashboard(controller=f'http://127.0.0.1:{mock.server_port}').live()
            self.assertTrue(result['connected'])
            self.assertEqual(set(seen), {('GET', p) for p in (
                '/readyz', '/api/v1/topology', '/api/v1/status', '/api/v1/intents')})
        finally:
            mock.shutdown()
            mock.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
