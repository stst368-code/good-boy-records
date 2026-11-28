"""The actual public player advances through manually timed word components."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    template=(ROOT/'_templates/landscape.html').read_text()
    functions=template[template.index('function fitLyricCurrent()'):template.index('async function loadPlaybackVisuals(t)')]
    style=template.split('<style id="lyric-part-highlighting">',1)[1].split('</style>',1)[0]
    word={'text':'built','start':1,'end':2,'parts':[{'text':'b','start':0,'end':.1},{'text':'uil','start':.1,'end':.9},{'text':'t','start':.9,'end':1}]}
    data=[{'text':'built','start':1,'end':2,'words':[word]}]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox'])
        page=browser.new_page()
        page.set_content('<style>:root{--accent-rgb:220,170,80}.lyrics{font:30px sans-serif;width:360px}'+style+'</style><div class="lyrics" id="lyric"></div>')
        page.add_script_tag(content="const ui={lyric:document.getElementById('lyric')},audio={currentTime:0};let lyricState={line:-1,word:-2};let lyricLines="+json.dumps(data)+";"+functions)
        for seconds,expected in [(1.05,[50,0,0]),(1.5,[100,50,0]),(1.95,[100,100,50])]:
            page.evaluate('t=>{audio.currentTime=t;updateLyrics()}',seconds)
            values=page.locator('.word-part').evaluate_all("parts=>parts.map(p=>parseFloat(p.style.getPropertyValue('--part-progress')))")
            assert len(values)==3 and all(abs(a-b)<.01 for a,b in zip(values,expected)),(seconds,values)
            assert page.locator('.word').inner_text()=='built'
        assert page.locator('.word-part').first.evaluate("p=>getComputedStyle(p).backgroundImage").startswith('linear-gradient')
        assert page.locator('.word-part').first.evaluate("p=>getComputedStyle(p).color")=='rgba(0, 0, 0, 0)'
        browser.close()
    print(json.dumps({'passed':True,'checks':['public player word parts','slow middle highlight','original spelling','rendered CSS']}))


if __name__=='__main__':main()
