"""Actual HTTP connection reuse and burst handling at the FRR boundary."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from frr_agent import Agent


class AgentHTTPTests(unittest.TestCase):
    def test_rotation_preserves_complete_concurrent_records(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=SimpleNamespace(identifier='test',art=Path(directory),router=lambda n:'test-'+n,
                scenario={'topology':{'nodes':[{'id':'sat-1'}]}})
            agent=Agent(runtime,'127.0.0.1','test-token')
            agent.log_handler.maxBytes=200
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda i:agent.log.info(json.dumps({'sequence':i})),range(64)))
            finally:
                agent.close()
            records=[json.loads(line)['sequence'] for path in Path(directory).glob('frr-agent.jsonl*')
                     for line in path.read_text().splitlines()]
            self.assertEqual(sorted(records),list(range(64)))

    def test_authenticated_keepalive_burst_and_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=SimpleNamespace(identifier='test',art=Path(directory),router=lambda n:'test-'+n,
                scenario={'topology':{'nodes':[{'id':'sat-1'}]}})
            with patch('frr_agent.subprocess.run',return_value=SimpleNamespace(stdout='FRRouting',returncode=0)) as execute:
                agent=Agent(runtime,'127.0.0.1','test-token')
                def send(connection,program='vtysh',authorized=True):
                    headers={'Content-Type':'application/json', 'X-Request-ID':'request-test',
                             'X-Starfabric-Frame-ID':'42', 'X-Starfabric-Phase':'final_verify'}
                    if authorized: headers['Authorization']='Bearer test-token'
                    connection.request('POST','/v1/execute',json.dumps({'node':'sat-1','program':program,'args':['-c','show version']}),headers)
                    response=connection.getresponse();response.read();return response.status
                def client(): return closing(http.client.HTTPConnection('127.0.0.1',agent.server.server_port,timeout=2))
                try:
                    with client() as connection:
                        self.assertEqual(send(connection),200)
                        original=connection.sock
                        self.assertIsNotNone(original)
                        self.assertEqual(send(connection),200)
                        self.assertIs(connection.sock,original)
                    def burst(_):
                        with client() as connection: return send(connection)
                    with ThreadPoolExecutor(max_workers=32) as pool:
                        self.assertEqual(list(pool.map(burst,range(64))),[200]*64)
                    count=execute.call_count
                    with client() as connection: self.assertEqual(send(connection,authorized=False),401)
                    with client() as connection: self.assertEqual(send(connection,program='sh'),400)
                    self.assertEqual(execute.call_count,count)
                finally:
                    agent.close()
                records=[json.loads(line) for line in (Path(directory)/'frr-agent.jsonl').read_text().splitlines()]
                self.assertEqual(len(records),66)
                self.assertTrue(all(r['correlation']['frame_id']=='42' and r['correlation']['phase']=='final_verify'
                                    and r['duration_seconds']>=0 and r['response_bytes']>0 for r in records))
                self.assertNotIn('test-token',json.dumps(records))


if __name__=='__main__': unittest.main()
