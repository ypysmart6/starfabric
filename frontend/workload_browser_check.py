#!/usr/bin/env python3
"""Browser rendering checks using recorded workload evidence in an isolated UI.

The real live runtime is only read. Rate writes go to a temporary fixture run,
so this check never changes the running constellation or its traffic.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from frontend.server import Dashboard, Handler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
from lab.live.workload_model import catalog, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--base', type=Path, help='saved dashboard snapshot while the live instance restarts')
    parser.add_argument('--global-fixture', action='store_true', help='synthetic 384-flow UI fixture; no traffic certification')
    args = parser.parse_args()
    actual = json.loads(args.base.read_text()) if args.base else Dashboard().live_snapshot()
    if actual.get('pending'):
        raise RuntimeError('this browser check needs an existing readable live snapshot')
    evidence = json.loads(args.evidence.read_text())
    if args.global_fixture:
        actual['physics'] = json.loads((ROOT/'scenarios/constellations/physical-defaults.json').read_text())
        config = json.loads((ROOT/'scenarios/constellations/live-workloads.json').read_text())
        templates = {f['carrier']:f for f in evidence['flows']}
        flows = catalog(config, [f'gw-{i:03d}' for i in range(1,25)])
        for flow in flows:
            template = templates[flow['carrier']]
            flow.update(metrics=copy.deepcopy(template['metrics']), evidence=copy.deepcopy(template['evidence']),
                        directions={flow['id']+'-'+direction:{'status':'ready'} for direction in ('forward','reverse')})
        evidence.update(flows=flows, summary=summary(flows))
    errors, checks = [], {}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run = 'sf-unified-uifixture'
        folder = root/'lab/unified/artifacts'/run/'workloads'
        folder.mkdir(parents=True)
        (folder/'catalog.json').write_text(json.dumps({'run_id':run,'token':'12'*16,'flows':evidence['flows']}))
        dashboard = Dashboard(root)
        dashboard.service.status = lambda:{'run_id':run,'active':True}
        payload = copy.deepcopy(actual)
        payload['run']['run_id'] = run
        payload['workloads'] = evidence
        payload['runtime'].update(active=True, phase='running', fresh=True)
        for flow in evidence['flows']:
            flow.update(fresh=True, evidence_fresh=True, rate_scale=1)
        dashboard.live_snapshot = lambda:payload
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.dashboard = dashboard
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width':1440,'height':1040})
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/?mode=live#business',wait_until='networkidle')
                expect(page.locator('#workload-rate')).to_be_visible(timeout=15000)
                expect(page.get_by_text('实包已验证',exact=True)).to_have_count(min(48,len(evidence['flows'])))
                checks['all_recorded_streams_render_with_packet_evidence'] = True
                if args.global_fixture:
                    expect(page.get_by_text('24 个网关 · 6 个洲 · 276 对网关',exact=False)).to_be_visible()
                    expect(page.locator('.gateway-matrix tbody tr')).to_have_count(24)
                    expect(page.locator('#workload-table tbody tr')).to_have_count(48)
                    page.locator('#workload-gateway').select_option('gw-001')
                    expect(page.locator('#workload-table tbody tr')).to_have_count(32)
                    page.locator('#workload-carrier').select_option('srv6')
                    expect(page.locator('#workload-table tbody tr')).to_have_count(4)
                    page.locator('[data-action="workload-reset"]').click()
                    page.locator('[data-workload-page="1"]').click()
                    expect(page.locator('#workload-table tbody tr')).to_have_count(48)
                    expect(page.locator('#workload-table')).to_contain_text('management-01')
                    page.locator('[data-workload-pair="gw-001|gw-024"]').click()
                    count=sum({f['source'],f['destination']}=={'gw-001','gw-024'} for f in evidence['flows'])
                    expect(page.locator('#workload-table tbody tr')).to_have_count(count)
                    page.locator('[data-action="workload-reset"]').click()
                    checks['global_matrix_filters_and_pagination'] = True
                    (ROOT/'reports/frontend').mkdir(parents=True,exist_ok=True)
                    page.evaluate('window.scrollTo(0,0)')
                    page.screenshot(path=str(ROOT/'reports/frontend/global-workloads.png'))
                page.locator('#workload-rate').select_option('0.5')
                page.wait_for_function('document.body.textContent.includes("调速请求已保存")')
                assert json.loads((folder/'rates.json').read_text())['scale'] == .5
                checks['rate_change_writes_only_fixture_session'] = True
                page.evaluate("location.hash='protocols'")
                expect(page.locator('.protocol-card')).to_have_count(8)
                copies=len(evidence['flows'])//8
                expect(page.get_by_text(f'{copies} / {copies} 条实包验证',exact=True)).to_have_count(8)
                checks['protocol_configuration_traffic_and_fault_scope_are_separate'] = True
                for flow in evidence['flows']:
                    flow['evidence_fresh'] = False
                page.reload(wait_until='networkidle')
                expect(page.get_by_text('实包已验证',exact=True)).to_have_count(0)
                expect(page.get_by_text('待封装证据',exact=True)).to_have_count(min(48,len(evidence['flows'])))
                checks['stale_capture_does_not_remain_verified'] = True
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                checks['mobile_layout_has_no_page_overflow'] = True
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    assert not errors, errors
    output = ROOT/'reports/frontend'/('global-workload-browser-check.json' if args.global_fixture else 'workload-browser-check.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'passed':True,'scope':'isolated browser fixture, no live traffic changes or traffic certification',
                                  'checks':checks,'errors':errors},indent=2))
    print(output)


if __name__ == '__main__':
    main()
