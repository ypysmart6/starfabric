#!/usr/bin/env python3
"""Isolated browser checks for waiting, failed, stale and disconnected startup UI."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lab.live.progress import STAGES


def main():
    now=datetime.now(timezone.utc)
    at=lambda seconds:(now-timedelta(seconds=seconds)).isoformat()
    stages=[{'id':key,'title':title,'state':'done' if i<4 else 'running' if i==4 else 'pending',
        **({'started_at':at(220),'finished_at':at(210)} if i<4 else {'started_at':at(90)} if i==4 else {})}
        for i,(key,title) in enumerate(STAGES)]
    runtime={'phase':'starting_cloud','active':True,'run_id':'test-browser-only','started_at':at(240),
        'startup':{'stages':stages,'current_stage':'starting_cloud','last_progress_at':at(70),
            'context':{'cluster':'sf-cloud-browser-fixture'},
            'tasks':[{'id':'cilium','stage':'starting_cloud','state':'running','title':'等待 Cilium 就绪',
                      'detail':'kube-system / daemonset/cilium','started_at':at(70),'timeout_seconds':600}],
            'events':[{'at':at(100-i),'message':f'测试检查点 {i}'} for i in range(80)]},
        'diagnostics':{'observed_at':at(1),'docker_ok':True,'containers':{'kubernetes':{'running':3,'created':3}},
            'kubernetes':{'api_ok':True,'nodes':[{'name':'worker-test','ready':False,'reason':'NetworkPluginNotReady','message':'waiting for CNI'}],
                'pods':[{'name':'cilium-test','namespace':'kube-system','phase':'Pending','ready':0,'total':1,'restarts':0,'reason':'ContainerCreating','node':'worker-test'}],
                'warnings':[{'at':at(5),'object':'cilium-test','reason':'FailedScheduling','count':1,'message':'waiting for network'}]}}}
    value={'mode':'live','pending':True,'runtime':runtime}
    errors,checks=[],{}
    out=ROOT/'reports/frontend';out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1040},accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        unavailable=[False]
        def respond(route):
            if unavailable[0]: route.abort()
            else:route.fulfill(json=value)
        page.route('**/api/dashboard?mode=live',respond)
        page.goto('http://127.0.0.1:8090/?mode=live',wait_until='networkidle')
        expect(page.locator('#startup-operation')).to_contain_text('等待 Cilium 就绪')
        expect(page.locator('[data-startup-stage="starting_cloud"]')).to_contain_text('执行中')
        expect(page.locator('[data-startup-stage="starting_120_onboard"]')).to_contain_text('等待')
        expect(page.locator('#startup-nodes')).to_contain_text('NotReady')
        expect(page.locator('#startup-pods')).to_contain_text('ContainerCreating')
        expect(page.locator('#main')).to_contain_text('FailedScheduling')
        checks['waiting_is_not_marked_complete']=True
        page.evaluate('window.startupRefs={operation:document.querySelector("#startup-operation"),log:document.querySelector("#startup-log"),stage:document.querySelector("[data-startup-stage=starting_cloud]")}')
        page.locator('#startup-log').evaluate('e=>e.scrollTop=80')
        page.wait_for_timeout(3500)
        assert abs(page.locator('#startup-log').evaluate('e=>e.scrollTop')-80)<3
        assert page.evaluate('startupRefs.operation===document.querySelector("#startup-operation") && startupRefs.log===document.querySelector("#startup-log") && startupRefs.stage===document.querySelector("[data-startup-stage=starting_cloud]")')
        checks['reading_log_survives_refresh']=True
        checks['startup_refresh_preserves_existing_dom']=True
        with page.expect_download() as dl:
            page.locator('[data-action="export-startup"]').click()
        exported=json.loads(Path(dl.value.path()).read_text())
        assert exported['runtime']['startup']['tasks'][0]['title']=='等待 Cilium 就绪'
        checks['exports_actual_displayed_diagnostics']=True
        runtime['diagnostics']['observed_at']=at(100)
        page.wait_for_timeout(3500)
        expect(page.locator('#main')).to_contain_text('诊断尚未更新 / 已过期')
        checks['stale_observation_visible']=True
        unavailable[0]=True
        page.wait_for_timeout(3500)
        expect(page.locator('#live-freshness')).to_contain_text('实时数据连接中断')
        checks['disconnected_api_visible']=True
        unavailable[0]=False
        runtime['phase']='failed';runtime['active']=False;runtime['updated_at']=at(0);runtime['error']='Cilium rollout timed out'
        stages[4].update(state='failed',error=runtime['error'],finished_at=at(0))
        runtime['startup']['tasks'][0].update(state='failed',error=runtime['error'],finished_at=at(0))
        page.wait_for_timeout(3500)
        expect(page.locator('[data-startup-stage="starting_cloud"]')).to_contain_text('失败')
        expect(page.locator('[data-startup-stage="starting_120_onboard"]')).to_contain_text('等待')
        checks['failure_preserves_unstarted_stages']=True
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':1040 if width==1440 else 844})
            page.wait_for_timeout(250)
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            page.screenshot(path=str(out/f'startup-fixture-{width}.png'),full_page=True)
        checks['desktop_and_mobile_fit']=True
        assert not errors,errors
        browser.close()
    result={'success':True,'scope':'isolated browser fixtures; no cluster mutation','checks':checks,'errors':errors}
    (out/'startup-browser-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
