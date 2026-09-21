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
    def test_authenticated_keepalive_burst_and_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=SimpleNamespace(identifier='test',art=Path(directory),router=lambda n:'test-'+n,
                scenario={'topology':{'nodes':[{'id':'sat-1'}]}})
            with patch('frr_agent.subprocess.run',return_value=SimpleNamespace(stdout='FRRouting',returncode=0)) as execute:
                agent=Agent(runtime,'127.0.0.1','test-token')
                def send(connection,program='vtysh',authorized=True):
                    headers={'Content-Type':'application/json'}
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


if __name__=='__main__': unittest.main()
