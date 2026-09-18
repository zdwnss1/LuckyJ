"""Real browser smoke test against the actual HTTP API and a supplied database."""
import argparse
import os
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
from luckyj.server import handler

p=argparse.ArgumentParser()
p.add_argument('--data',type=Path,default=Path('data'))
p.add_argument('--output',type=Path,default=Path('screenshots'))
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
server=ThreadingHTTPServer(('127.0.0.1',0),handler(a.data))
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    with sync_playwright() as pw:
        kw={'executable_path':os.environ['LUCKYJ_BROWSER']} if os.environ.get('LUCKYJ_BROWSER') else {}
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],**kw)
        page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.wait_for_selector('.decision')
        page.screenshot(path=str(a.output/'desktop.png'))
        assert page.locator('.decision').count()==30
        page.locator('#more').click()
        page.wait_for_function("document.querySelectorAll('.decision').length===60")
        page.locator('.decision button').first.click()
        page.wait_for_selector('.self-hand')
        page.screenshot(path=str(a.output/'detail.png'))
        page.locator('#next').click()
        page.wait_for_function("document.querySelector('#detailTitle').textContent.includes('第 2 次')")
        saved=page.url
        page.reload();page.wait_for_selector('.self-hand')
        assert page.url==saved
        page.locator('#closeDetail').click()
        page.locator('[name=hand]').fill('NOT_A_TILE')
        page.locator('button[type=submit]').click()
        page.wait_for_selector('.notice.error')
        page.locator('#reset').click();page.wait_for_selector('.decision')
        page.locator('[data-example="233"]').click()
        page.wait_for_function("document.querySelector('#resultCount').textContent.includes('条匹配') && !document.querySelector('#notice').textContent.includes('正在')")
        assert page.locator('#actionMode').get_attribute('aria-pressed')=='true'
        page.locator('#reset').click();page.wait_for_selector('.decision')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(a.output/'mobile.png'),full_page=True)
        page.locator('.decision button').first.click();page.wait_for_selector('.self-hand')
        assert page.evaluate('document.querySelector("dialog").scrollWidth <= document.querySelector("dialog").clientWidth')
        assert not errors,errors
        print('Browser smoke passed: pagination, detail, navigation, deep link, invalid input, concrete pattern, desktop and mobile.')
        browser.close()
finally:
    server.shutdown();server.server_close();thread.join()
