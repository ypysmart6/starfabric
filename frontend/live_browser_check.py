#!/usr/bin/env python3
"""Browser acceptance against an already running real constellation; read-only."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'reports/frontend'


def main():
    checks,errors={},[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1040})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8090/?mode=live',wait_until='networkidle')
        snapshot=page.request.get('http://127.0.0.1:8090/api/dashboard?mode=live').json()
        node_count=len(snapshot['nodes'])
        expect(page.locator('h1')).to_have_text('任务总览',timeout=15000)
        expect(page.locator('#frame-slider')).to_have_count(0)
        expect(page.locator('.stat-value').first).to_have_text('120/ 120')
        page.wait_for_timeout(300)
        page.evaluate('''() => {
            window.refreshReferences = {page:document.querySelector('.page-enter'),
                canvas:document.querySelector('#orbit-canvas'),sidebar:document.querySelector('#sidebar').firstElementChild,
                search:document.querySelector('#global-search'),animations:0};
            document.addEventListener('animationstart',e=>{
                if(e.animationName==='fade' && e.target.closest('#main')) refreshReferences.animations++;
            });
        }''')
        page.locator('#global-search').fill('sat-0120')
        page.locator('#global-search').evaluate('e=>e.setSelectionRange(4,8)')
        before=page.locator('#frame-label').inner_text()
        page.wait_for_function('(before) => document.querySelector("#frame-label")?.textContent !== before',arg=before,timeout=240000)
        checks['actual_live_frame_advances_without_playback']=True
        assert page.evaluate('''() => refreshReferences.page===document.querySelector('.page-enter') &&
            refreshReferences.canvas===document.querySelector('#orbit-canvas') &&
            refreshReferences.sidebar===document.querySelector('#sidebar').firstElementChild &&
            refreshReferences.search===document.activeElement && refreshReferences.animations===0''')
        expect(page.locator('#global-search')).to_have_value('sat-0120')
        assert page.locator('#global-search').evaluate('e=>[e.selectionStart,e.selectionEnd]')==[4,8]
        checks['live_refresh_keeps_dom_canvas_focus_and_has_no_fade']=True
        canvas=page.locator('#orbit-canvas')
        canvas.scroll_into_view_if_needed()
        canvas.evaluate('e=>e.addEventListener("pointerdown",event=>window.dragPointer=event.pointerId)')
        box=canvas.bounding_box()
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
        page.mouse.down()
        page.mouse.move(box['x']+box['width']/2+35,box['y']+box['height']/2+10)
        page.wait_for_timeout(6500)
        assert canvas.evaluate('e=>e===refreshReferences.canvas && e.hasPointerCapture(dragPointer)')
        page.mouse.up()
        checks['drag_continues_across_multiple_refreshes']=True
        page.screenshot(path=str(OUTPUT/'live-overview.png'),full_page=True,animations='disabled')
        for name in ('orbit','routing','business','autonomy','protocols','cloud','capabilities','evidence','learning','configuration','live'):
            page.evaluate('(name)=>location.hash=name',name)
            page.wait_for_timeout(250)
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),name
        checks['all_live_pages_render']=True
        if snapshot.get('workloads',{}).get('enabled'):
            flows=snapshot['workloads']['flows']
            gateways=sorted({n for f in flows for n in (f['source'],f['destination'])})
            page.evaluate("location.hash='business'")
            expect(page.locator('.gateway-matrix tbody tr')).to_have_count(len(gateways))
            expect(page.locator('#workload-table tbody tr')).to_have_count(min(48,len(flows)))
            page.locator('#workload-gateway').select_option(gateways[0])
            selected=[f for f in flows if gateways[0] in (f['source'],f['destination'])]
            expect(page.locator('#workload-table tbody tr')).to_have_count(min(48,len(selected)))
            page.locator('#workload-carrier').select_option('srv6')
            expect(page.locator('#workload-table tbody tr')).to_have_count(sum(f['carrier']=='srv6' for f in selected))
            page.locator('[data-action="workload-reset"]').click()
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(OUTPUT/'global-workloads-live.png'),animations='disabled')
            checks['actual_global_workload_matrix_and_filters']=True
            page.evaluate("location.hash='live'")
        page.locator('[data-action="startup-details"]').click()
        expect(page.locator('#drawer-title')).to_have_text('本轮启动诊断')
        expect(page.locator('.drawer [data-startup-stage="starting_cloud"]')).to_contain_text('完成')
        expect(page.locator('.drawer [data-startup-stage="starting_120_onboard"]')).to_contain_text('完成')
        page.keyboard.press('Escape')
        checks['completed_startup_diagnostics_remain_accessible']=True
        page.evaluate("location.hash='orbit'")
        expect(page.locator('#node-table tr')).to_have_count(node_count)
        page.locator('#node-search').fill('sat-0120')
        page.evaluate('window.savedNodeRow=document.querySelector("#node-table tr")')
        page.wait_for_timeout(3500)
        expect(page.locator('#node-search')).to_have_value('sat-0120')
        expect(page.locator('#node-table tr')).to_have_count(1)
        assert page.evaluate('savedNodeRow===document.querySelector("#node-table tr")')
        page.locator('#node-table tr').click()
        expect(page.locator('#drawer-title')).to_have_text('SAT-0120')
        expect(page.locator('#node-details')).to_contain_text('当前 connected')
        page.wait_for_timeout(3500)
        expect(page.locator('#drawer-title')).to_have_text('SAT-0120')
        page.keyboard.press('Escape')
        checks['search_and_node_detail_survive_live_refresh']=True
        page.evaluate("location.hash='autonomy'")
        expect(page.locator('.fleet-tile')).to_have_count(120)
        expect(page.locator('[data-stage]')).to_have_count(0)
        checks['autonomy_is_current_state']=True
        page.evaluate("location.hash='configuration'")
        page.locator('#physical-editor').fill('{"editing":')
        page.wait_for_timeout(3500)
        expect(page.locator('#physical-editor')).to_have_value('{"editing":')
        checks['configuration_edits_survive_refresh']=True
        page.locator('[data-action="mode-history"]').click()
        expect(page.locator('#physical-editor')).to_contain_text('schema_version')
        page.evaluate("location.hash='overview'")
        expect(page.locator('#frame-slider')).to_be_visible()
        page.locator('[data-action="mode-live"]').click()
        expect(page.locator('#frame-slider')).to_have_count(0)
        checks['mode_switch_does_not_stop_runtime']=True
        page.set_viewport_size({'width':390,'height':844})
        for name in ('overview','orbit','routing','business','autonomy','protocols','cloud','capabilities','evidence','learning','configuration','live'):
            page.evaluate('(name)=>location.hash=name',name)
            page.wait_for_timeout(200)
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),'mobile '+name
        page.evaluate("location.hash='overview'")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUTPUT/'live-mobile.png'),full_page=True,animations='disabled')
        checks['all_live_mobile_pages_fit']=True
        assert not errors,errors
        browser.close()
    result={'success':True,'checks':checks,'errors':errors}
    (OUTPUT/'live-browser-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
