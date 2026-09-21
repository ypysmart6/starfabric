"""Actual browser launch-form contract, with a fake lifecycle (no platform start).

CHROME may point to a Chromium binary. Requires the websocket-client package.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen

from frontend.server import Dashboard, Handler, ThreadingHTTPServer


class BrowserLaunchTests(unittest.TestCase):
    def test_selection_survives_polling_and_is_submitted_exactly(self):
        import websocket
        chrome = os.environ.get('CHROME')
        if not chrome:
            candidates = sorted((Path.home()/'.cache/ms-playwright').glob('chromium-*/chrome-linux*/chrome'))
            if not candidates: self.skipTest('set CHROME to run the browser test')
            chrome = str(candidates[-1])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dashboard = Dashboard(root)
            calls = []
            class Lifecycle:
                def status(self):
                    return {'active':bool(calls),'phase':'starting_5g' if calls else 'stopped',
                            'launch_config': calls[-1] if calls else None}
                def start(self, configuration):
                    calls.append(configuration)
                    return self.status()
            dashboard.service = Lifecycle()
            server = ThreadingHTTPServer(('127.0.0.1',0),Handler); server.dashboard=dashboard
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            process = subprocess.Popen([chrome,'--headless','--no-sandbox','--disable-gpu','--remote-debugging-port=0',
                '--remote-allow-origins=*','--user-data-dir='+str(root/'chrome'),'about:blank'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            connection=None
            try:
                active=root/'chrome/DevToolsActivePort'
                for _ in range(100):
                    if active.exists(): break
                    time.sleep(.1)
                port=active.read_text().splitlines()[0]
                targets=[]
                for _ in range(100):
                    targets=json.load(urlopen('http://127.0.0.1:'+port+'/json'))
                    targets=[t for t in targets if t.get('type')=='page' and t.get('webSocketDebuggerUrl')]
                    if targets: break
                    time.sleep(.1)
                self.assertTrue(targets,'Chromium did not create a page target')
                connection=websocket.create_connection(targets[0]['webSocketDebuggerUrl'],timeout=15)
                sequence=0; errors=[]
                def cdp(method,params=None):
                    nonlocal sequence
                    sequence+=1
                    connection.send(json.dumps({'id':sequence,'method':method,'params':params or {}}))
                    while True:
                        value=json.loads(connection.recv())
                        if value.get('method')=='Runtime.exceptionThrown': errors.append(value)
                        if value.get('id')==sequence:
                            self.assertNotIn('error',value)
                            return value.get('result',{})
                def js(expression):
                    result=cdp('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
                    self.assertNotIn('exceptionDetails',result)
                    return result.get('result',{}).get('value')
                cdp('Runtime.enable')
                cdp('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/?mode=live'})
                for _ in range(100):
                    if js("!!document.querySelector('[data-action=runtime-start]')"): break
                    time.sleep(.1)
                js("document.querySelector('[data-action=runtime-start]').click()")
                for _ in range(50):
                    if js("!!document.querySelector('#launch-form')"): break
                    time.sleep(.1)
                js("""(() => {
                  const form=document.querySelector('#launch-form');
                  const set=(name,value)=>{form.elements[name].value=value;form.elements[name].dispatchEvent(new Event('change',{bubbles:true}));};
                  set('preset','2'); set('algorithm','hops'); set('igp','ospf');
                  form.querySelector('[value=ldp]').click();
                  set('copies_per_profile','3');
                })()""")
                time.sleep(4)  # Includes a dashboard refresh while the drawer remains open.
                self.assertEqual(js("document.querySelector('#launch-form').elements.satellites.value"),'60')
                js("document.querySelector('#launch-form [value=pcep]').click(); document.querySelector('[data-action=launch-submit]').click()")
                for _ in range(50):
                    if js("!document.querySelector('[data-action=launch-submit]').disabled"): break
                    time.sleep(.1)
                self.assertEqual(calls,[])
                self.assertIn('IS-IS',js("document.querySelector('#launch-error').textContent"))
                js("document.querySelector('#launch-form [value=pcep]').click()")
                js("document.querySelector('[data-action=launch-submit]').click()")
                for _ in range(100):
                    if calls: break
                    time.sleep(.1)
                self.assertEqual(calls,[{'schema_version':1,'igp':'ospf','algorithm':'hops','carriers':['evpn-srv6','ldp'],
                    'satellites':60,'gateways':4,'planes':6,'copies_per_profile':3,'step_seconds':10}])
                for _ in range(50):
                    if js("!!document.querySelector('[data-action=runtime-stop]')"): break
                    time.sleep(.1)
                js("location.hash='configuration'")
                for _ in range(50):
                    if js("!!document.querySelector('#main #launch-form')"): break
                    time.sleep(.1)
                self.assertTrue(js("document.querySelector('#launch-form fieldset').disabled"))
                self.assertEqual(js("document.querySelector('#launch-form').elements.gateways.value"),'4')
                self.assertEqual(errors,[])
            finally:
                if connection: connection.close()
                process.terminate(); process.wait(timeout=15)
                server.shutdown();server.server_close();thread.join()


if __name__ == '__main__': unittest.main()
