#!/usr/bin/env python3
"""Optional Playwright acceptance. Starts only the local display HTTP server."""
import argparse
import json
import threading
from pathlib import Path

from server import Dashboard, Handler, ROOT, ThreadingHTTPServer


def main():
    from playwright.sync_api import sync_playwright, expect
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'reports/frontend')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.dashboard = Dashboard()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors, checks = [], {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1040}, accept_downloads=True)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/?mode=history', wait_until='networkidle')
            expect(page.locator('h1')).to_have_text('任务总览')
            page.screenshot(path=str(args.output/'overview-desktop.png'), full_page=True, animations='disabled')
            for name in ('orbit', 'routing', 'business', 'autonomy', 'protocols', 'cloud', 'capabilities', 'evidence', 'learning', 'configuration', 'live'):
                page.evaluate('(name) => location.hash = name', name)
                expect(page.locator('h1')).to_be_visible()
                page.wait_for_timeout(120)
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), name
                checks[name+'_renders'] = True

            page.evaluate("location.hash='orbit'")
            expect(page.locator('#node-table tr')).to_have_count(124)
            page.locator('#node-search').fill('sat-0001')
            expect(page.locator('#node-table tr')).to_have_count(1)
            page.locator('#node-table tr').click()
            expect(page.locator('#drawer-title')).to_have_text('SAT-0001')
            expect(page.locator('#node-details')).to_contain_text('10.255.0.5')
            page.screenshot(path=str(args.output/'satellite-detail.png'), animations='disabled')
            page.locator('[data-action="node-frr"]').click()
            expect(page.locator('.drawer .code-view')).to_contain_text('hostname sat-0001')
            page.keyboard.press('Escape')
            checks['all_nodes_search_and_real_frr_detail'] = True

            page.locator('#frame-slider').evaluate("e => { e.value=30; e.dispatchEvent(new Event('input',{bubbles:true})); }")
            expect(page.locator('#frame-label')).to_have_text('帧 31 / 31')
            page.locator('[data-action="play"]').click()
            page.wait_for_timeout(1200)
            assert int(page.locator('#frame-slider').input_value()) >= 1
            page.locator('[data-action="play"]').click()
            checks['frame_slider_and_playback'] = True

            page.evaluate("location.hash='routing'")
            expect(page.locator('#intent-selector option')).to_have_count(10)
            page.locator('#intent-selector').select_option('flow-00001')
            expect(page.locator('tbody')).to_contain_text('10.240.0.1/32')
            checks['actual_intent_route_selection'] = True

            page.evaluate("location.hash='autonomy'")
            expect(page.locator('.fleet-tile')).to_have_count(120)
            expect(page.locator('.fleet-tile.amber')).to_have_count(120)
            page.locator('[data-stage="final"]').click()
            expect(page.locator('.fleet-tile.amber')).to_have_count(0)
            checks['autonomy_stage_comparison'] = True

            page.evaluate("location.hash='protocols'")
            expect(page.locator('.protocol-card')).to_have_count(6)
            expect(page.locator('.protocol-card .badge.amber')).to_have_count(6)
            page.locator('[data-protocol="pcep"]').click()
            expect(page.locator('.drawer')).to_contain_text('未执行该承载完整故障矩阵')
            page.keyboard.press('Escape')
            checks['protocol_scope_not_overstated'] = True

            page.evaluate("location.hash='evidence'")
            page.locator('#evidence-search').fill('physical-replay.json')
            expect(page.locator('#evidence-table tr')).to_have_count(1)
            with page.expect_download() as captured:
                page.locator('#evidence-table a.download-link').click()
            download = captured.value
            download.save_as(args.output/'download-physical-replay.json')
            assert len(json.loads((args.output/'download-physical-replay.json').read_text())['frames']) == 31
            checks['evidence_search_download'] = True

            page.evaluate("location.hash='learning'")
            page.locator('#source-search').fill('physics_runtime.py')
            expect(page.locator('#source-table')).to_contain_text('31 帧 1:1 回放')
            page.locator('#source-table [data-file="lab/platform/physics_runtime.py"]').click()
            expect(page.locator('.drawer .code-view')).to_contain_text('def ')
            page.locator('.drawer-scrim').click(position={'x': 8, 'y': 8})
            expect(page.locator('.drawer')).to_have_count(0)
            checks['source_search'] = True

            page.evaluate("location.hash='capabilities'")
            page.locator('#cap-search').fill('BFD')
            assert page.locator('.capability-list .capability').count() > 0
            checks['full_capability_search'] = True

            page.evaluate("location.hash='configuration'")
            field = page.locator('#cfg-constellation-satellites')
            field.fill('121')
            expect(page.locator('#config-error')).to_contain_text('整除')
            expect(page.locator('[data-action="download-constellation"]')).to_be_disabled()
            field.fill('120')
            expect(page.locator('[data-action="download-constellation"]')).to_be_enabled()
            with page.expect_download() as captured:
                page.locator('[data-action="download-constellation"]').click()
            captured.value.save_as(args.output/'download-configuration.json')
            assert json.loads((args.output/'download-configuration.json').read_text())['satellites'] == 120
            checks['configuration_validation_and_export'] = True

            page.locator('#global-search').fill('sat-0120')
            page.locator('#global-search').press('Enter')
            expect(page.locator('.drawer')).to_contain_text('sat-0120')
            page.locator('.drawer [data-node="sat-0120"]').click()
            expect(page.locator('#drawer-title')).to_have_text('SAT-0120')
            page.keyboard.press('Escape')
            checks['global_search_and_last_satellite'] = True

            page.evaluate("location.hash='evidence'")
            page.locator('#run-selector').select_option('sf-unified-0209fe1206')
            expect(page.locator('.run-banner strong')).to_contain_text('sf-unified-0209fe1206')
            expect(page.locator('.callout.amber')).to_contain_text('未成功部署 FRR 网络')
            expect(page.locator('.run-banner .badge')).to_have_text('本轮未通过')
            page.evaluate("location.hash='overview'")
            expect(page.locator('.stat-value').first).to_have_text('0/ 120')
            page.evaluate("location.hash='autonomy'")
            expect(page.locator('.fleet-tile.empty')).to_have_count(120)
            page.evaluate("location.hash='evidence'")
            page.locator('#run-selector').select_option('sf-unified-d180f84868')
            expect(page.locator('.run-banner strong')).to_contain_text('sf-unified-d180f84868')
            checks['historical_run_scope_and_deployment_counts'] = True

            page.evaluate("location.hash='live'")
            live_record = {'configured': True, 'connected': True, 'observed_at': '2026-09-15T14:00:00Z',
                'results': {'topology': {'data': {'version': 42, 'nodes': [{'id': 'sat-0001'}], 'links': []}},
                    'status': {'data': {'reconcile': {'phase': 'committed'}, 'committed_plan': {'id': 'test-plan'}}},
                    'intents': {'data': {'intents': [{'id': 'test-intent'}]}}}}
            page.route('**/api/live', lambda route: route.fulfill(json=live_record))
            page.locator('[data-action="poll-live"]').click()
            expect(page.locator('#live-content .stat-value').last).to_have_text('committed')
            page.locator('#live-content summary').filter(has_text='当前业务意图').click()
            expect(page.locator('#live-content details').first).to_contain_text('test-intent')
            page.unroute('**/api/live')
            checks['live_display_matches_controller_contract_with_mock'] = True

            page.set_viewport_size({'width': 390, 'height': 844})
            for name in ('overview', 'orbit', 'routing', 'business', 'autonomy', 'protocols', 'cloud', 'capabilities', 'evidence', 'learning', 'configuration', 'live'):
                page.evaluate('(name) => location.hash = name', name)
                page.wait_for_timeout(150)
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), 'mobile '+name
            page.evaluate("location.hash='overview'")
            page.wait_for_timeout(150)
            expect(page.locator('#toast')).not_to_have_class('show')
            page.screenshot(path=str(args.output/'overview-mobile.png'), full_page=True, animations='disabled')
            page.locator('[data-action="menu"]').click()
            expect(page.locator('#sidebar')).to_have_class('sidebar open')
            page.keyboard.press('Escape')
            checks['mobile_all_pages_and_navigation'] = True
            assert not errors, errors
            checks['no_browser_javascript_errors'] = True
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        result = {'success': bool(checks.get('no_browser_javascript_errors')), 'checks': checks, 'errors': errors}
        (args.output/'browser-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
