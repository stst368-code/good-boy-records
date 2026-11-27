"""Browser regression for dropped clicks and hidden publication errors."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--width',type=int,action='append');args=parser.parse_args()
    module=(ROOT/'_04_preparation/_static/publishing.js').read_text(encoding='utf-8')
    html='''<main id="publishingView"></main><section id="hostSettings"></section>
<script type="module">
import {initPublishing} from '/publishing.js';
const projects=['first','second'].map(id=>({prepared_id:id,can_return:true,track:{displayTitle:id,genre:{parent_genre:'Rock'}},options:{},files:[{content_type:'audio/mpeg'}]}));
window.fixture={projects,releases:[],calls:[],retries:[],posts:{},delay:0};
const api=async(path,body)=>{
 if(path==='/releases/library/settings')return {settings:{rooms:[{id:'public',name:'Main player',enabled:true},{id:'hidden',name:'Secret room',enabled:true}],default_room:'public'}};
 if(path==='/releases/projects'){if(fixture.delay)await new Promise(r=>setTimeout(r,fixture.delay));return {projects:structuredClone(fixture.projects),releases:structuredClone(fixture.releases)};}
 if(path.endsWith('/publish')){const id=path.split('/')[2];fixture.calls.push(id);await new Promise((resolve,reject)=>fixture.posts[id]={resolve,reject});return {id:'job-'+id};}
 if(path.endsWith('/retry')){fixture.retries.push(path);await new Promise((resolve,reject)=>fixture.retryPost={resolve,reject});return {id:'retried-release'};}
 throw Error('Unexpected request '+path);
};
window.ui=initPublishing({api,toast:()=>{},onListChange:()=>{},
 onPlay:(items,index)=>{fixture.played=items.map(item=>item.id);fixture.playing=items[index].id;}
});await ui.open();window.ready=true;
</script>'''
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--use-angle=swiftshader'])
        for width in args.width or [1440,3840,390]:
            page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('http://gbr.test/**',lambda route:route.fulfill(body=module if route.request.url.endswith('/publishing.js') else html,content_type='text/javascript' if route.request.url.endswith('/publishing.js') else 'text/html'))
            page.goto('http://gbr.test/');page.wait_for_function('window.ready===true')
            # A room picker stays focused after its menu closes. It must not
            # stop new recordings appearing, lose its choice, or be replaced.
            room=page.locator('[data-room="first"]')
            room.select_option('hidden');room.focus()
            page.evaluate('''()=>{
 window.originalRoom=document.querySelector('[data-room="first"]');
 fixture.projects.push({prepared_id:'new-arrival',can_return:true,
  track:{displayTitle:'New arrival'},options:{},files:[{content_type:'audio/mpeg'}]});
}''')
            page.locator('[data-pending="new-arrival"]').wait_for(timeout=10000)
            assert room.input_value()=='hidden'
            assert page.evaluate('originalRoom===document.querySelector("[data-room=first]")&&document.activeElement===originalRoom')
            page.evaluate('fixture.projects.pop()')
            page.locator('[data-pending="new-arrival"]').wait_for(state='detached',timeout=10000)
            assert page.evaluate('originalRoom===document.querySelector("[data-room=first]")')
            # Start a slow refresh, then immediately click two different tracks.
            # All three operations must run instead of the clicks disappearing.
            page.evaluate('''()=>{
 fixture.delay=350;window.originalPlayButton=document.querySelector('[data-preview="first"]');
 document.getElementById('pubRefresh').click();
 document.querySelector('[data-publish="first"]').click();
 document.querySelector('[data-publish="second"]').click();
}''')
            page.wait_for_function("fixture.calls.length===1")
            assert page.evaluate('fixture.calls')==['first']
            page.evaluate("fixture.posts.first.reject(Error('Publishing destination changed; prepare a new preview'))")
            page.wait_for_function("fixture.calls.length===2")
            assert page.evaluate('fixture.calls')==['first','second']
            assert 'Publishing destination changed' in page.locator('[data-pending="first"] [data-publication-error]').inner_text()
            page.evaluate('''()=>{
 fixture.projects[1].publication={prepared_id:'second',status:'failed',created_at:'2026-10-29T12:00:00Z',completed_at:'2026-10-29T12:00:01Z',error:'R2 request failed: AccessDenied',progress:{phase:'uploading',completed:0,total:2}};
 fixture.posts.second.resolve();fixture.delay=0;
}''')
            page.wait_for_function("document.querySelector('[data-pending=second] [data-publication-error]').textContent.includes('AccessDenied')")
            page.wait_for_function("document.querySelector('[data-publish=second]').textContent==='Retry publication'")
            # The 4-second background poll cannot clear either kind of failure,
            # and it must not replace an existing audition audio element.
            page.wait_for_timeout(4300)
            assert 'Publishing destination changed' in page.locator('[data-pending="first"] [data-publication-error]').inner_text()
            assert 'AccessDenied' in page.locator('[data-pending="second"] [data-publication-error]').inner_text()
            assert page.evaluate("originalPlayButton===document.querySelector('[data-preview=first]')")
            page.locator('[data-publish="second"]').click();page.wait_for_function('fixture.calls.length===3')
            page.evaluate('''()=>{
 fixture.projects[1].publication={prepared_id:'second',status:'queued',created_at:'2026-10-29T12:01:00Z',progress:{phase:'waiting_processing',message:'Waiting for lyric alignment before moving files'}};
 fixture.posts.second.resolve();
}''')
            page.wait_for_function("document.querySelector('[data-publish=second]').textContent==='Waiting for processing'")
            assert page.locator('[data-publish="second"]').is_disabled()
            assert page.locator('[data-room="second"]').is_disabled()
            assert 'lyric alignment' in page.locator('[data-pending="second"] [data-publication-status]').inner_text()
            # A successful completion leaves this page on the next refresh.
            page.evaluate("fixture.projects=[];document.getElementById('pubRefresh').click()")
            page.wait_for_selector('[data-pending]',state='detached')
            assert 'Published recordings are in Library' in page.locator('#pubStatus').inner_text()
            page.evaluate("""()=>{
 const take=(id,audioId,variant)=>({prepared_id:id,generation_id:id+'-generation',audio_id:audioId,
  track:{displayTitle:'Shared title',song_id:13,variant_id:variant,variant:'Punk',duration:8},
  options:{},files:[{content_type:'audio/mpeg'}]});
 fixture.projects=[take('take-a','aaaaaaaa',2),take('alias-a','aaaaaaaa',3),take('take-b','bbbbbbbb',2)];
 document.getElementById('pubRefresh').click();
}""")
            page.wait_for_function("'2 recordings'===document.querySelector('.publish-song-heading span')?.textContent")
            assert page.locator('[data-pending="take-a"]').is_visible()
            assert page.locator('[data-pending="take-b"]').is_visible()
            assert page.locator('[data-pending="alias-a"]').is_hidden()
            page.locator('.publish-alternatives summary').click()
            assert page.locator('[data-pending="alias-a"]').is_visible()
            assert 'Variant 3' in page.locator('[data-pending="alias-a"]').inner_text()
            page.locator('[data-preview="alias-a"]').click()
            assert page.evaluate('fixture.played')==['alias-a','take-b']
            # A failed preparation has no prepared release ID. Its Retry button
            # must target the failed task, preserve errors and prevent double clicks.
            page.evaluate('''()=>{
 fixture.releases=[{id:'failed-prepare',generation_id:'gremlin',kind:'release_prepare',status:'failed',
  title:'One Gremlin',can_retry:true,created_at:'2026-11-19T21:34:55Z',
  progress:{},error:'Song ID 98 is missing from the master catalogue'}];
 document.getElementById('pubRefresh').click();
}''')
            page.locator('#pubHistorySummary').click()
            retry=page.locator('#pubHistory [data-retry="failed-prepare"]')
            retry.wait_for(state='visible')
            assert retry.inner_text()=='Retry preparation'
            retry.click()
            assert retry.is_disabled()
            page.wait_for_function('fixture.retries.length===1')
            assert page.evaluate('fixture.retries')==['/releases/tasks/failed-prepare/retry']
            page.evaluate("fixture.retryPost.reject(Error('Release audio changed; finish Preparation again'))")
            page.wait_for_function("document.getElementById('pubError').textContent.includes('Release audio changed')")
            assert retry.is_enabled()
            retry.click()
            page.wait_for_function('fixture.retries.length===2')
            page.evaluate('''()=>{
 fixture.releases.unshift({...fixture.releases[0],id:'retried-release',status:'done',can_retry:false});
 fixture.retryPost.resolve();
}''')
            retry.wait_for(state='detached')
            assert '0 active or failed' in page.locator('#pubHistorySummary').inner_text()
            assert page.locator('#pubError').inner_text()==''
            assert not errors,errors
            print(json.dumps({'width':width,'passed':True,'checks':'rapid clicks during refresh, persistent errors, publication and preparation retry, processing state, player preservation, completed removal'}),flush=True)
            page.evaluate('ui.close()');page.close()
        browser.close()


if __name__=='__main__':main()
