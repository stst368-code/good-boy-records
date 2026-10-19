"""Exercise current-platform public research using a disposable real-schema DB."""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
from playwright.sync_api import sync_playwright
from test_platform import fixture, export
from test_website import ROOT, website
from ui_website import Routes


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',type=Path,required=True)
    args=parser.parse_args()
    errors,failures,completed=[],[],[]
    with tempfile.TemporaryDirectory(prefix='gbr-public-test-') as temporary:
        base=Path(temporary)
        root=base/'website'
        shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('_site','_showcase','__pycache__','.gbr-site-*'))
        export(fixture(base/'gbr.sqlite3'),root/'_data/_analytics/platform.json')
        website.build(root)
        with ThreadingHTTPServer(('127.0.0.1',0),partial(Routes,directory=str(root/'_site'))) as server:
            threading.Thread(target=server.serve_forever,daemon=True).start()
            address='http://127.0.0.1:'+str(server.server_port)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox','--use-angle=swiftshader'])
                for prefix,width in [('/',1440),('/good-boy-records/',390)]:
                    page=browser.new_page(viewport={'width':width,'height':1000},is_mobile=width<700,has_touch=width<700)
                    page.on('pageerror',lambda error:errors.append(error.message))
                    page.on('response',lambda response:failures.append(response.url) if response.url.startswith(address) and response.status>=400 else None)
                    page.goto(address+prefix+'analytics/',wait_until='networkidle')
                    page.wait_for_function('rows.length===2')
                    assert page.locator('#researchSource').input_value()=='platform'
                    assert page.evaluate('rows.find(r=>r.id==="audio-1").quality_score')==2.5
                    assert page.evaluate('rows.find(r=>r.id==="audio-2").quality_score') is None
                    assert page.evaluate('rows.find(r=>r.id==="audio-1").encoder_seed')=='18446744073709551615'
                    page.locator('[data-view="archive"]').click()
                    page.locator('[data-record="audio-1"]').click()
                    assert 'Vocal timbre' in page.locator('#drawerBody').inner_text()
                    assert 'Awaiting review' not in page.locator('#drawerBody').inner_text()
                    assert page.locator('#drawerBody song').count()==0
                    page.locator('#closeDrawer').click()
                    page.locator('#reviewResponse').select_option('audio_quality')
                    assert page.evaluate('rows.find(r=>r.id==="audio-1").quality_score')==4
                    page.locator('#grade').select_option('4')
                    assert page.evaluate('filtered.length')==1
                    page.locator('#reset').click()
                    assert page.evaluate('filtered.length')==2
                    page.locator('#reviewTag').select_option('vocal_tone')
                    page.locator('#tagPolarity').select_option('negative')
                    assert page.evaluate('filtered.length')==0
                    page.locator('#tagPolarity').select_option('positive')
                    assert page.evaluate('filtered.length')==1
                    page.locator('[data-view="tags"]').click()
                    assert page.locator('#viewContent tbody tr').count()==1
                    assert '1 / 1' in page.locator('#viewContent tbody').inner_text()
                    page.locator('#reset').click()
                    page.locator('[data-view="precision"]').click()
                    text=page.locator('#viewContent').inner_text()
                    for value in ['A40','48.00 GiB','22.00 / 20.00','$0.40']:
                        assert value in text,(value,text)
                    page.locator('[data-view="audio"]').click()
                    assert 'Clipped samples' in page.locator('#viewContent').inner_text()
                    assert page.locator('.research-table tr').count()>5
                    page.locator('#researchSource').select_option('archive')
                    assert page.evaluate('rows.length')==1181
                    assert page.locator('#tagsNav').is_hidden()
                    assert page.locator('#responseLabel').is_hidden()
                    page.locator('[data-view="precision"]').click()
                    assert 'Pod cost ledger' in page.locator('#viewContent').inner_text()
                    page.locator('#researchSource').select_option('platform')
                    assert page.evaluate('rows.length')==2
                    assert page.locator('#reviewResponse').input_value()=='average'
                    for view in ['explorer','parameters','audio','precision','archive','tags']:
                        page.locator('[data-view="'+view+'"]').click()
                        assert page.locator('#viewContent').inner_text().strip()
                        assert 'NaN' not in page.locator('#viewContent').inner_text()
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    completed.append({'route':prefix,'width':width,'passed':True})
                    print(json.dumps(completed[-1]),flush=True)
                    page.close()
                browser.close()
            server.shutdown()
    assert not errors,errors
    assert not failures,failures
    print(json.dumps({'workflows':completed,'javascript_errors':errors,'asset_errors':failures}))


if __name__=='__main__':
    main()
