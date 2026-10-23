"""Real-browser polarity controls, fourth-rating submit, revision restore and mobile layout."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _review.app import create_app
from ui_review_context import ready, score, current


def main():
    p=argparse.ArgumentParser();p.add_argument('--browser');p.add_argument('--screenshots');args=p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);inp=root/'input';inp.mkdir();sr=16000;t=np.arange(sr*7)/sr
        for i in range(4):
            path=inp/f'track-{i}.flac';sf.write(path,.1*np.sin(2*np.pi*(210+i*31)*t),sr,subtype='PCM_24')
            path.with_suffix('.yaml').write_text(f'title: Track {i}\ngenre: Punk\ncluster: Rock\n')
        config=root/'config.json';config.write_text(json.dumps({'input_directories':[str(inp)],'port':18771}))
        app=create_app(config,True);s=app.state.service
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18771,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if server.started and s.db.one("SELECT COUNT(*) n FROM audio WHERE analysis_state='ready'")['n']==4:break
                time.sleep(.1)
            assert server.started
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
                page=browser.new_page(viewport={'width':1366,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto('http://127.0.0.1:18771');page.wait_for_selector('#reviewerDialog[open]');page.locator('#knownReviewers button',has_text='Simon').click();ready(page)
                def button(tag,polarity):return page.locator(f'.review-tag[data-tag-id="{tag}"] [data-polarity="{polarity}"]')
                def count():return s.db.one('SELECT COUNT(*) n FROM reviews')['n']
                assert page.locator('.review-tag').count()==20 and page.locator('.review-tag button').count()==40
                assert not page.locator('#submitReview').is_visible()
                # Direct polarity, switch, toggle off, multi-category and clear.
                button('vocal_tone','positive').click();assert button('vocal_tone','positive').get_attribute('aria-pressed')=='true'
                button('vocal_tone','negative').click();assert button('vocal_tone','negative').get_attribute('aria-pressed')=='true' and button('vocal_tone','positive').get_attribute('aria-pressed')=='false'
                button('vocal_tone','negative').click();assert page.locator('.review-tag [aria-pressed=true]').count()==0
                button('vocal_delivery','negative').click();button('production_cleanliness','positive').click();page.locator('#clearTags').click()
                assert page.locator('.review-tag [aria-pressed=true]').count()==0 and count()==0
                first=current(s);page.locator('#reason').fill('Robot-like vocals.\nExcellent instrumental parts.')
                button('vocal_delivery','negative').click();assert button('vocal_delivery','negative').get_attribute('aria-pressed')=='true','initial vocal choice';button('instrument_interest','positive').click();assert button('vocal_delivery','negative').get_attribute('aria-pressed')=='true','after instrument'
                score(page,'544');assert count()==0;assert button('vocal_delivery','negative').get_attribute('aria-pressed')=='true','after keyboard ratings'
                # Tags can still be chosen after the first three scores; they do not submit.
                button('arrangement_transitions','positive').click();assert count()==0
                assert page.locator('.rating-values [aria-pressed=true]').count()==3
                if args.screenshots:
                    out=Path(args.screenshots);out.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(out/'desktop-tags.png'),full_page=True)
                page.locator('#autoplayNext').check();score(page,'4');ready(page)
                page.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0&&!document.getElementById('audio').paused")
                assert count()==1 and current(s)!=first
                original=s.db.one('SELECT id,reason FROM reviews');assert s.db.one('SELECT COUNT(*) n FROM review_tags')['n']==3, s.db.all('SELECT * FROM review_tags')
                assert page.locator('.review-tag [aria-pressed=true]').count()==0 and page.locator('#reason').input_value()==''
                # Back restores tags. Tag-only changes never trigger save, even with all four scores loaded.
                page.locator('#back').click();page.wait_for_selector('#correctionBadge:not([hidden])');ready(page)
                assert page.locator('.review-tag [aria-pressed=true]').count()==3 and page.locator('#reason').input_value()==original['reason']
                button('vocal_delivery','positive').click();assert count()==1
                assert button('vocal_delivery','negative').get_attribute('aria-pressed')=='false'
                page.locator('[data-dimension=realism] [data-score="4"]').click();ready(page)
                page.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0")
                assert count()==2
                old=s.db.one("SELECT polarity FROM review_tags WHERE review_id=? AND tag_id='vocal_delivery'",(original['id'],));assert old['polarity']=='negative'
                latest=s.db.one('SELECT id,reason FROM latest_reviews');assert latest['reason']==original['reason']
                assert s.db.one("SELECT polarity FROM review_tags WHERE review_id=? AND tag_id='vocal_delivery'",(latest['id'],))['polarity']=='positive'
                # Configurable names/inactive tags stay readable on older reviews.
                s.db.execute("UPDATE review_tag_definitions SET label='Expression',active=0 WHERE id='vocal_delivery'")
                page.reload();ready(page);page.locator('#back').click();page.wait_for_selector('#correctionBadge:not([hidden])');ready(page)
                assert page.locator('.review-tag[data-tag-id=vocal_delivery] .review-tag-label').inner_text()=='Expression'
                assert button('vocal_delivery','positive').get_attribute('aria-pressed')=='true' and button('vocal_delivery','negative').is_disabled()
                page.locator('#viewReviews').click();page.wait_for_selector('#reviewsDialog[open]')
                assert page.locator('#reviewDetails .saved-tag').count()==6 and 'Expression (inactive)' in page.locator('#reviewDetails').inner_text()
                page.locator('[data-close=reviewsDialog]').click()
                for width in [390,320]:
                    page.set_viewport_size({'width':width,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('.review-tag button').count()==40
                    # Every polarity remains visibly rendered, including a disabled inactive option.
                    assert all(b.is_visible() for b in page.locator('.review-tag button').all())
                    if args.screenshots:page.screenshot(path=str(Path(args.screenshots)/f'phone-{width}-tags.png'),full_page=True)
                page.locator('#clearTags').click();assert count()==2 and page.locator('.review-tag [aria-pressed=true]').count()==0
                assert page.locator('#reason').input_value()==original['reason']
                assert not errors,errors
                print(json.dumps({'polarity_switch_clear_multiple':'passed','fourth_score_atomic_submit_autoplay':'passed','no_selection_leak':'passed','restore_revision_reason_and_inactive_history':'passed','desktop_390_320_layout':'passed','js_errors':errors}))
                browser.close()
        finally:server.should_exit=True;thread.join(timeout=10)


if __name__=='__main__':main()
