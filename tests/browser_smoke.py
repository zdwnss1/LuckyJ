"""Real browser smoke test against the actual HTTP API and a supplied database."""
import argparse
import json
import os
import re
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
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
        errors=[]
        def page_error(e):
            errors.append(str(e));print('PAGE_ERROR:',str(e),flush=True)
        page.on('pageerror',page_error)
        try:
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            expect(page.locator('.decision')).to_have_count(30)
            page.screenshot(path=str(a.output/'desktop.png'))
            page.locator('#more').click()
            expect(page.locator('.decision')).to_have_count(60)
            page.locator('.decision button').first.click()
            expect(page.locator('.self-hand')).to_be_visible()
            page.screenshot(path=str(a.output/'detail.png'))
            page.locator('#next').click()
            expect(page.locator('#detailTitle')).to_contain_text('第 2 次')
            saved=page.url
            page.reload()
            expect(page.locator('.self-hand')).to_be_visible()
            expect(page.locator('#detailTitle')).to_contain_text('第 2 次')
            assert page.url==saved
            page.locator('#closeDetail').click()
            page.locator('[name=hand]').fill('NOT_A_TILE')
            page.locator('button[type=submit]').click()
            expect(page.locator('.notice.error')).to_be_visible()
            page.locator('#reset').click()
            expect(page.locator('.decision')).to_have_count(30)
            with page.expect_response(lambda r: '/api/search?' in r.url and 'hand=233m' in r.url) as result:
                page.locator('[data-example="233"]').click()
            response=result.value.json()
            expect(page.locator('#resultCount')).to_contain_text(f"{response['total']:,} 条匹配")
            expect(page.locator('#notice')).not_to_have_text(re.compile('.*正在.*'))
            expect(page.locator('#actionMode')).to_have_attribute('aria-pressed','true')
            page.locator('#reset').click()
            expect(page.locator('.decision')).to_have_count(30)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(a.output/'mobile.png'),full_page=True)
            page.locator('.decision button').first.click()
            expect(page.locator('.self-hand')).to_be_visible()
            assert page.evaluate('document.querySelector("dialog").scrollWidth <= document.querySelector("dialog").clientWidth')
            assert not errors,errors
            (a.output/'verification.json').write_text(json.dumps({'passed':True,'checks':['pagination','detail','round_navigation','stable_deep_link','invalid_input','concrete_pattern','desktop','mobile'],'javascript_errors':errors},indent=2))
            print('Browser smoke passed: pagination, detail, navigation, deep link, invalid input, concrete pattern, desktop and mobile.')
        except Exception:
            page.screenshot(path=str(a.output/'failure.png'))
            (a.output/'errors.json').write_text(json.dumps({'javascript_errors':errors,'url':page.url},ensure_ascii=False,indent=2))
            raise
        finally:
            browser.close()
finally:
    server.shutdown();server.server_close();thread.join()
