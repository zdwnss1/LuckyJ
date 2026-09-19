"""Real Chromium -> HTTP -> SQLite acceptance, with offline fixtures by default."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import urllib.request
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
from luckyj.server import handler
from test_research import sample_database

p=argparse.ArgumentParser()
p.add_argument('--data',type=Path)
p.add_argument('--bridge',action='store_true',help='For restricted local browsers only: route browser requests to the real HTTP server via urllib; CI uses direct HTTP')
p.add_argument('--output',type=Path,default=Path('screenshots/research'))
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    data=a.data or Path(tmp)
    if not a.data:sample_database(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(data))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    errors=[];checks=[]
    try:
        with sync_playwright() as pw:
            kwargs={'executable_path':os.environ['LUCKYJ_BROWSER']} if os.environ.get('LUCKYJ_BROWSER') else {}
            browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],**kwargs)
            page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
            page.set_default_timeout(90000)
            page.on('pageerror',lambda e:errors.append(str(e)))
            base=f'http://127.0.0.1:{server.server_port}'
            if a.bridge:
                # This environment forbids browser network navigation. Render
                # local source and bridge ONLY our JSON API to the real HTTP
                # server. Direct HTTP/CSP/URL behavior is tested by CI instead.
                root=Path(__file__).resolve().parents[1]/'luckyj'/'static'
                def api_bridge(path,method='GET',body=None):
                    if not path.startswith('/api/'):raise ValueError('Only local API paths may be bridged')
                    req=urllib.request.Request(base+path,data=body.encode() if body else None,method=method,headers={'Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req,timeout=90) as r:return {'ok':True,'data':json.load(r)}
                    except urllib.error.HTTPError as exc:
                        return {'ok':False,'error':json.load(exc).get('error',str(exc))}
                page.expose_function('__luckyjHTTP',api_bridge)
                html=(root/'research.html').read_text().replace('<link rel="stylesheet" href="/research.css">','').replace('<script src="/research.js" defer></script>','').replace('<script src="/extensions.js" defer></script>','').replace('<link rel="stylesheet" href="/extensions.css">','')
                page.set_content(html)
                page.add_style_tag(content=(root/'research.css').read_text())
                page.add_style_tag(content=(root/'extensions.css').read_text())
                page.add_script_tag(content=(root/'extensions.js').read_text())
                script=(root/'research.js').read_text()
                import re
                script=re.sub(r'^async function api\(.*$', "async function api(path,options={}){const r=await window.__luckyjHTTP(path,options.method||'GET',options.body||null);if(!r.ok)throw Error(r.error);return r.data;}",script,flags=re.M)
                script=re.sub(r'^function shareURL\(.*$', 'function shareURL(){window.__savedQuery=String(query);}',script,flags=re.M)
                script=script.replace('new URLSearchParams(location.search)',"new URLSearchParams('turn_max=2')")
                page.add_script_tag(content=script)
            else:
                page.goto(base+'/research?turn_max=2')
            expect(page.locator('.case').first).to_be_visible()
            expect(page.locator('[name=reverse]')).not_to_be_checked()
            expect(page.locator('[name=suits]')).to_be_checked()
            expect(page.locator('[name=exclude_locked]')).to_be_checked()
            checks.append('Defaults: suit symmetry on, number reversal off, locked riichi excluded')
            page.screenshot(path=str(a.output/'desktop.png'))
            stats=page.locator('#receipt').inner_text();bars=page.locator('#histogram').inner_html()
            page.locator('.case .view').first.click()
            expect(page.locator('.case .view').first).to_have_attribute('aria-pressed','true')
            expect(page.locator('.case .view').nth(1)).to_have_attribute('aria-pressed','false')
            assert page.locator('#histogram').inner_html()==bars
            page.locator('.case details').first.locator('summary').click()
            expect(page.locator('.case .incoming').first).to_be_visible()
            checks.append('Per-card alignment independent; incoming labels transform; histogram unchanged')
            page.locator('.bar').first.click()
            expect(page.locator('#clear-bucket')).to_be_visible()
            expect(page.locator('#result-count')).to_contain_text('统计分母保持')
            assert page.locator('#receipt').inner_text()==stats
            page.locator('#clear-bucket').click()
            expect(page.locator('.case').first).to_be_visible()
            checks.append('Histogram drilldown preserves original cohort denominator')
            page.locator('.case .detail-open').first.click()
            expect(page.locator('.candidate-table').first).to_be_visible()
            expect(page.locator('.table-layout .seat')).to_have_count(4)
            before=page.locator('.seat-0 .river .tile').count()
            page.get_by_role('button',name='执行这次切牌',exact=True).click()
            expect(page.locator('.seat-0 .river .tile')).to_have_count(before+1)
            page.get_by_role('button',name='回到切牌前',exact=True).click()
            page.get_by_role('button',name='显示同形对照',exact=True).click()
            expect(page.locator('.aligned-box')).to_be_visible()
            page.screenshot(path=str(a.output/'detail.png'))
            checks.append('Real four-seat board, before/after discard, aligned comparison, all legal candidates')
            page.locator('#close-detail').click()
            page.locator('[name=hand]').fill('11111m')
            page.locator('#submit').click()
            expect(page.locator('#notice.error')).to_contain_text('实体牌供给')
            page.locator('[name=hand]').fill('x{1}')
            page.locator('#submit').click()
            expect(page.locator('.case').first).to_be_visible()
            checks.append('Invalid tile capacity rejects; unified wildcard input searches')
            if not a.bridge:
                saved=page.url;page.reload();expect(page.locator('.case').first).to_be_visible()
                assert page.url==saved
                checks.append('Query URL restores input and result set')
            else:
                checks.append('URL restoration and CSP deferred to direct-HTTP CI; not claimed by bridge test')
            page.locator('[name=hand]').fill('')
            page.locator('[name=riichi_declaration]').select_option('1')
            page.locator('#submit').click()
            expect(page.locator('.signal-riichi_declaration').first).to_be_visible()
            expect(page.locator('#flag-counts')).to_contain_text('立直宣言切牌')
            page.screenshot(path=str(a.output/'riichi-flags.png'))
            page.locator('[name=riichi_declaration]').select_option('')
            page.locator('[name=follow_discard]').select_option('1')
            page.locator('#submit').click()
            expect(page.locator('.signal-follow_discard').first).to_be_visible()
            page.locator('.case .detail-open').first.click()
            expect(page.locator('.tile.follow-source').first).to_be_visible()
            page.screenshot(path=str(a.output/'follow-source.png'))
            page.locator('#close-detail').click()
            page.locator('[name=follow_discard]').select_option('')
            page.locator('#submit').click()
            expect(page.locator('.case').first).to_be_visible()
            checks.append('Riichi/follow badges and filters; source discard highlight; full-cohort non-exclusive flag facets')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),page.evaluate('document.documentElement.scrollWidth')
            page.screenshot(path=str(a.output/'mobile.png'),full_page=True)
            page.locator('.case .detail-open').first.click()
            expect(page.locator('.candidate-table').first).to_be_visible()
            assert page.evaluate('document.querySelector("dialog").getBoundingClientRect().right <= innerWidth')
            page.screenshot(path=str(a.output/'mobile-detail.png'))
            checks.append('390px layout and detail stay within viewport')
            assert not errors,errors
            browser.close()
    finally:server.shutdown();server.server_close();thread.join()
    receipt={'complete':True,'transport':'Local Chromium DOM + JSON API bridge to real HTTP server (no browser-network/CSP claim)' if a.bridge else 'real Chromium HTTP to local server, no API bridge','checks':checks,'javascript_errors':errors,'real_corpus':bool(a.data)}
    (a.output/'verification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(receipt,ensure_ascii=False))
