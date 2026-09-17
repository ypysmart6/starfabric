#!/usr/bin/env python3
"""Read-only globe acceptance: astronomy, rendering, camera and live DOM stability."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUTPUT = Path(__file__).resolve().parents[1] / 'reports/frontend'
URL = 'http://127.0.0.1:8090/?mode=live#orbit'


def main():
    checks, errors = {}, []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1040})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(URL, wait_until='networkidle')
        canvas = page.locator('#orbit-canvas')
        expect(canvas).to_have_attribute('data-surface', 'textured')
        expect(canvas).to_have_attribute('data-renderer', 'webgl')
        expect(page.locator('#sun-readout')).to_contain_text('AU')
        expect(page.locator('#sun-readout')).to_contain_text('随模型时刻更新')
        page.screenshot(path=str(OUTPUT / 'earth-desktop.png'))
        checks['local_textured_globe_and_visible_sun'] = True

        solar = page.evaluate('''async () => {
            const {solarPosition} = await import('/solar.js');
            const dates = ['2024-03-20T03:06:00Z','2024-06-20T20:51:00Z','2024-12-21T09:20:00Z',
                '2024-01-03T00:00:00Z','2024-07-05T00:00:00Z'];
            return {samples:dates.map(at=>solarPosition(at)), invalid:solarPosition('invalid')};
        }''')
        samples = solar['samples']
        assert abs(samples[0]['declination']) < .002
        assert .408 < samples[1]['declination'] < .410
        assert -.410 < samples[2]['declination'] < -.408
        assert .982 < samples[3]['distanceAU'] < .985
        assert 1.015 < samples[4]['distanceAU'] < 1.018
        assert all(abs(sum(v*v for v in s['direction'])-1) < 1e-10 for s in samples)
        assert solar['invalid'] is None
        checks['solar_equinox_solstices_distance_and_invalid_time'] = True

        page.evaluate('window.originalGlobeCanvas=document.querySelector("#orbit-canvas")')
        for _ in range(12):
            page.locator('[data-action="zoom-in"]').click()
        expect(canvas).to_have_attribute('data-zoom', '6.000')
        expect(page.locator('#globe-zoom')).to_have_text('6.0×')
        expect(page.locator('#sun-readout')).to_contain_text('画外方向')
        page.screenshot(path=str(OUTPUT / 'earth-closeup.png'))
        page.wait_for_timeout(6500)
        expect(page.locator('#globe-zoom')).to_have_text('6.0×')
        assert canvas.evaluate('e=>e===originalGlobeCanvas')
        checks['six_times_zoom_and_hud_survive_live_refresh'] = True
        for _ in range(16):
            page.locator('[data-action="zoom-out"]').click()
        expect(canvas).to_have_attribute('data-zoom', '0.500')
        page.locator('#sun-readout').click()
        expect(canvas).to_have_attribute('data-zoom', '1.000')
        expect(page.locator('#sun-readout')).to_contain_text('随模型时刻更新')
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
        page.mouse.wheel(0, -500)
        page.wait_for_function('() => Number(document.querySelector("#orbit-canvas").dataset.zoom)>1.65')
        page.locator('[data-action="reset-globe"]').click()
        checks['zoom_bounds_wheel_and_sun_location_reset'] = True

        page.locator('[data-action="fullscreen-globe"]').click()
        page.wait_for_function('() => document.fullscreenElement?.matches(".orbit-card")')
        page.wait_for_timeout(200)
        assert canvas.bounding_box()['width'] >= page.viewport_size['width'] - 2
        assert canvas.bounding_box()['height'] > page.viewport_size['height'] * .5
        page.screenshot(path=str(OUTPUT / 'earth-fullscreen.png'))
        page.locator('[data-action="fullscreen-globe"]').click()
        page.wait_for_function('() => !document.fullscreenElement')
        checks['fullscreen_enter_and_exit'] = True

        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(300)
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        expect(page.locator('#sun-readout')).to_contain_text('随模型时刻更新')
        # Browser-level touch input exercises pointer capture and two-finger zoom.
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        session = page.context.new_cdp_session(page)
        x, y = box['x'] + box['width']/2, box['y'] + box['height']/2
        session.send('Input.dispatchTouchEvent', {'type':'touchStart', 'touchPoints':[
            {'id':1,'x':x-25,'y':y}, {'id':2,'x':x+25,'y':y}]})
        session.send('Input.dispatchTouchEvent', {'type':'touchMove', 'touchPoints':[
            {'id':1,'x':x-60,'y':y}, {'id':2,'x':x+60,'y':y}]})
        session.send('Input.dispatchTouchEvent', {'type':'touchEnd', 'touchPoints':[]})
        page.wait_for_function('() => Number(document.querySelector("#orbit-canvas").dataset.zoom)>1.8')
        page.locator('#sun-readout').click()
        page.screenshot(path=str(OUTPUT / 'earth-mobile.png'))
        checks['mobile_layout_and_real_two_finger_zoom'] = True

        # Isolated renderer fixture uses the actual model, without modifying runtime.
        fixture = page.evaluate('''async () => {
            const {Globe} = await import('/globe.js');
            const data = await (await fetch('/api/dashboard?mode=live')).json();
            const host = document.createElement('div');
            host.style.cssText='position:fixed;inset:0;width:600px;height:450px;background:#000;z-index:9999';
            const canvas = document.createElement('canvas');
            canvas.style.cssText='width:600px;height:450px';
            host.append(canvas); document.body.append(host);
            const globe = new Globe(canvas, data, ()=>{});
            await new Promise(resolve=>setTimeout(resolve,100));
            const {solarPosition} = await import('/solar.js');
            const solar = solarPosition(data.frames[0].at);
            globe.yaw = solar.rightAscension + Math.PI / 2;
            globe.pitch = -solar.declination;
            globe.draw();
            const occulted = globe.sunView.position.hidden;
            const invisibleHit = globe.hit.some(p=>!globe.visible(p));
            data.frames[0] = {...data.frames[0],at:'2024-06-20T20:51:00Z'};
            globe.update(0,null);
            const modelTime = canvas.dataset.sunAt;
            const solarChanged = solarPosition(modelTime).declination > .408;
            // Directional illumination: at yaw/pitch 0 the center faces +Y.
            const sample = sun => {
                const rendered = globe.surface.draw({width:100,height:100,cx:50,cy:50,radius:45,yaw:0,pitch:0,gmst:0,sun});
                const pixelCanvas=document.createElement('canvas');pixelCanvas.width=100;pixelCanvas.height=100;
                const ctx=pixelCanvas.getContext('2d');ctx.drawImage(rendered,0,0,100,100);
                return [...ctx.getImageData(50,50,1,1).data].slice(0,3).reduce((a,b)=>a+b,0);
            };
            const day=sample([0,1,0]), night=sample([0,-1,0]);
            globe.destroy();host.remove();
            return {occulted,invisibleHit,modelTime,solarChanged,day,night};
        }''')
        assert fixture['occulted'] and not fixture['invisibleHit'], fixture
        assert fixture['modelTime'] == '2024-06-20T20:51:00Z' and fixture['solarChanged']
        assert fixture['day'] > fixture['night'] * 2, fixture
        checks['solar_occlusion_model_clock_and_directional_lighting'] = True

        # Disable WebGL in a fresh browser context; textured Canvas fallback works.
        fallback = browser.new_context(viewport={'width':1000,'height':900})
        fallback.add_init_script('''(() => {
            const original=HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext=function(type,...args) {
                return type.startsWith('webgl') ? null : original.call(this,type,...args);
            };
        })()''')
        plain = fallback.new_page()
        plain.on('pageerror',lambda e:errors.append(str(e)))
        plain.goto(URL,wait_until='networkidle')
        expect(plain.locator('#orbit-canvas')).to_have_attribute('data-renderer','canvas')
        expect(plain.locator('#orbit-canvas')).to_have_attribute('data-surface','textured')
        plain.locator('[data-action="zoom-in"]').click()
        expect(plain.locator('#globe-zoom')).to_have_text('1.3×')
        plain.screenshot(path=str(OUTPUT/'earth-fallback.png'))
        checks['no_webgl_textured_fallback'] = True
        fallback.close()
        assert not errors, errors
        browser.close()
    result = {'success':True, 'checks':checks, 'errors':errors}
    (OUTPUT/'globe-browser-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
