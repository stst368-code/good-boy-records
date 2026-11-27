"""Late audio decodes must never become the buffer of a different recording."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]


def exercise(browser_path,module):
    html='''<script type="module">
import {createSongPlayer} from '/song-player.js';
window.fixture={source:'first',resolve:{},played:[]};
window.fetch=async url=>({ok:true,arrayBuffer:async()=>({label:url})});
window.Audio=class {
 paused=true;currentTime=0;duration=5;src='';
 play=async()=>{this.paused=false;fixture.played.push('preview:'+this.src)};
 pause=()=>{this.paused=true};load=()=>{};removeAttribute=name=>{if(name==='src')this.src=''};
};
window.AudioContext=class {
 currentTime=0;destination={};resume=async()=>{};close=async()=>{};
 decodeAudioData=raw=>new Promise(resolve=>{const done=()=>resolve({label:raw.label,sampleRate:44100,length:441000,numberOfChannels:1});fixture.resolve[raw.label]=done;if(fixture.autoDecode)done();});
 createGain=()=>({gain:{value:1},connect(){},disconnect(){}});
 createBufferSource=()=>({buffer:null,playbackRate:{value:1},connect(){},disconnect(){},stop(){},start(){fixture.played.push(this.buffer.label)}});
};
const song={segments:[{id:'music',kind:'audio',start:0,end:10,mode:'normal'}],sounds:[]};
window.player=createSongPlayer({getProject:()=>song,sourceUrl:()=>fixture.source,toast:message=>{throw Error(message)}});
window.ready=true;
</script>'''
    model=(ROOT/'_04_preparation/_static/song-model.js').read_text()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=browser_path,headless=True,args=['--no-sandbox'])
        page=browser.new_page()
        def route(request):
            url=request.request.url
            source=module if url.endswith('/song-player.js') else model if url.endswith('/song-model.js') else html
            request.fulfill(body=source,content_type='text/javascript' if url.endswith('.js') else 'text/html')
        page.route('http://gbr.test/**',route);page.goto('http://gbr.test')
        page.wait_for_function('window.ready')
        page.evaluate('()=>{window.first=player.play();}')
        page.wait_for_function("()=>Boolean(fixture.resolve.first)")
        page.evaluate("()=>{player.reset();fixture.source='second';window.second=player.play();}")
        page.wait_for_function("()=>Boolean(fixture.resolve.second)")
        page.evaluate('''async()=>{
 fixture.resolve.second();await window.second;
 fixture.resolve.first();await window.first;
 player.pause();await player.play();player.pause();
}''')
        assert page.evaluate('fixture.played')==['second','second'],page.evaluate('fixture.played')
        page.evaluate('''async()=>{
 fixture.autoDecode=true;fixture.played=[];
 player.preview('old-preview',0,5);player.setMode('processed');await player.play();
 player.reset();fixture.source='third';await player.play();player.pause();
 player.preview('another-preview',0,5);player.setMode('processed');await player.play();
 player.close();fixture.source='fourth';await player.play();player.pause();
}''')
        assert page.evaluate('fixture.played')==['preview:old-preview','third','preview:another-preview','fourth'],page.evaluate('fixture.played')
        browser.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    exercise(args.browser,(ROOT/'_04_preparation/_static/song-player.js').read_text())
    print('Passed: stale decoding, processed-preview reset and reopening use the selected recording')


if __name__=='__main__':main()
