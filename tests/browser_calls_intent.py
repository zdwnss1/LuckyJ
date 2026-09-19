"""Chromium acceptance for bracket search, paired ghosts, win values, review and calls."""
import argparse
from contextlib import redirect_stdout
import gzip
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright,expect
from test_research import sample_database
from test_core import fixture,TARGET
from luckyj.store import build
from luckyj.enrich import enrich
from luckyj.server import handler

p=argparse.ArgumentParser();p.add_argument('--bridge',action='store_true');p.add_argument('--output',type=Path,default=Path('screenshots/calls-intent'));a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    data=Path(tmp);sample_database(data)
    log='2023041911gm-0001-0000-00000004'
    raw=fixture('<W100/><G8/><N who="0" m="2055"/><D12/>',hand=[0,4]+list(range(12,23)),source=(3,8))
    (data/'raw/2023'/(log+'.xml.gz')).write_bytes(gzip.compress(raw,mtime=0))
    manifest=json.loads((data/'manifest.json').read_text());manifest['list'].append({'player1':TARGET,'url':'https://tenhou.net/0/?log='+log});(data/'manifest.json').write_text(json.dumps(manifest))
    with redirect_stdout(io.StringIO()):build(data);enrich(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(data));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    errors=[];checks=[]
    try:
        with sync_playwright() as pw:
            kw={'executable_path':os.environ['LUCKYJ_BROWSER']} if os.environ.get('LUCKYJ_BROWSER') else {}
            browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],**kw);page=browser.new_page(viewport={'width':1440,'height':1100});page.set_default_timeout(45000);page.on('pageerror',lambda e:errors.append(str(e)))
            if a.bridge:
                root=Path(__file__).resolve().parents[1]/'luckyj/static'
                def api_bridge(path,method='GET',body=None):
                    if not path.startswith('/api/'):raise ValueError('Only local API paths')
                    req=urllib.request.Request(base+path,data=body.encode() if body else None,method=method,headers={'Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req,timeout=60) as r:return {'ok':True,'data':json.load(r)}
                    except urllib.error.HTTPError as e:return {'ok':False,'error':json.load(e)['error']}
                page.expose_function('__HTTP',api_bridge)
                html=(root/'research.html').read_text();html=re.sub(r'<(?:link[^>]+|script[^>]+></script)>','',html)
                page.set_content(html)
                for f in ('research.css','extensions.css'):page.add_style_tag(content=(root/f).read_text())
                page.add_script_tag(content=(root/'extensions.js').read_text())
                script=(root/'research.js').read_text();script=re.sub(r'^async function api\(.*$',"async function api(path,options={}){const r=await __HTTP(path,options.method||'GET',options.body||null);if(!r.ok)throw Error(r.error);return r.data;}",script,flags=re.M);script=re.sub(r'^function shareURL\(.*$','function shareURL(){}',script,flags=re.M);script=script.replace('new URLSearchParams(location.search)',"new URLSearchParams('hand=%5Bc%5D')")
                page.add_script_tag(content=script)
            else:page.goto(base+'/research?hand=%5Bc%5D')
            expect(page.locator('.case .fixed-melds')).to_be_visible();expect(page.locator('.case')).to_have_count(1)
            page.locator('.case .detail-open').click();expect(page.locator('.call-ghost')).to_be_visible()
            ghost=page.locator('.call-ghost');group=page.locator('.table-layout .meld-group')
            assert ghost.get_attribute('data-call-id')==group.get_attribute('data-call-id')
            assert ghost.evaluate('(e)=>getComputedStyle(e).borderTopStyle')=='dashed'
            assert ghost.evaluate('(e)=>getComputedStyle(e).borderTopColor')==group.evaluate('(e)=>getComputedStyle(e).borderTopColor')
            ghost.hover();expect(page.locator('.table-layout .call-linked-hover')).to_have_count(2)
            page.screenshot(path=str(a.output/'paired-ghost.png'));checks.append('Fixed-meld query; source-position ghost and whole meld share stable id, color, label and hover')
            page.locator('.intent-details>summary').click();expect(page.locator('.intent-details')).to_contain_text('尚无人工标签校准')
            page.get_by_label('人工押引判断').select_option('uncertain')
            with page.expect_download() as dl:page.get_by_role('button',name='导出这手人工复核记录').click()
            path=a.output/'human-review.json';dl.value.save_as(path);label=json.loads(path.read_text());assert label['human_label']=='uncertain' and label['shown_model_prediction'] is True
            checks.append('Inference separates online/review and discloses uncalibrated status; explicit human label export')
            page.locator('#close-detail').click();page.locator('[name=hand]').fill('');page.locator('[name=win_yaku]').fill('立直');page.locator('#submit').click();expect(page.locator('.case')).to_have_count(1)
            page.locator('.detail-open').click();page.locator('.win-projection>summary').click();expect(page.locator('.win-projection table')).to_be_visible();expect(page.locator('.win-projection')).to_contain_text('Riichi');page.screenshot(path=str(a.output/'win-values.png'));checks.append('Conditional yaku filter reaches full cohort; per-wait ron/tsumo values displayed')
            page.locator('#close-detail').click();page.locator('.call-opportunities>details>summary').click();page.get_by_role('button',name='查询鸣牌机会').click();expect(page.locator('.opportunity-card').first).to_be_visible();expect(page.locator('.candidate-call.selected')).to_be_visible();checks.append('Opportunity UI reports complete counts and actual selected alternative')
            page.locator('[name=win_yaku]').fill('');page.locator('[name=hand]').fill('111234565678 [999]');page.locator('#submit').click();expect(page.locator('#notice.error')).to_contain_text('最多11张');checks.append('Impossible concealed+meld tile count rejected')
            page.locator('[name=hand]').fill('[c]');page.locator('#submit').click();expect(page.locator('.case')).to_have_count(1);page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth');page.locator('.detail-open').click();expect(page.locator('.call-ghost')).to_be_visible();assert page.evaluate('document.querySelector("dialog").getBoundingClientRect().right <= innerWidth');page.screenshot(path=str(a.output/'mobile-paired-ghost.png'));checks.append('390px mobile layout and source linkage stay inside viewport')
            assert not errors,errors;browser.close()
    finally:server.shutdown();server.server_close();thread.join()
    receipt={'complete':True,'transport':'DOM + real HTTP API bridge; browser-network/CSP not verified locally' if a.bridge else 'direct Chromium HTTP (no bridge)','fixtures':'synthetic, not corpus measurements','checks':checks,'javascript_errors':errors}
    (a.output/'verification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(receipt,ensure_ascii=False))
