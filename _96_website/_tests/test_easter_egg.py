import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class EasterTests(unittest.TestCase):
    def test_cover_gesture_is_specific_three_taps_and_resets(self):
        script=r'''
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const context={window:{},location:{assign(){}},URL,document:{baseURI:'https://site.test/gbr/'},performance:{now:()=>0}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
let time=0,opens=0;const egg=context.window.GBRcreateCoverEgg({open:()=>opens++,clock:()=>time});
const song={id:'chosen'},other={id:'other'},config={format:'gbr-easter-egg-v1',enabled:true,track_id:'chosen'};
egg.tap(song);egg.tap(song);egg.tap(song);assert.equal(opens,0);
egg.configure(config);assert.equal(egg.tap(song),false);assert.equal(egg.tap(song),false);assert.equal(opens,0);
assert.equal(egg.tap(song),true);assert.equal(opens,1);egg.tap(song);assert.equal(opens,1);
egg.configure(config);egg.tap(song);egg.tap(other);egg.tap(song);egg.tap(song);assert.equal(opens,1);egg.tap(song);assert.equal(opens,2);
egg.configure(config);egg.tap(song);egg.tap(song);egg.reset();egg.tap(song);assert.equal(opens,2);
time=2000;egg.tap(song);egg.tap(song);assert.equal(opens,2);egg.tap(song);assert.equal(opens,3);
egg.configure({...config,enabled:false});egg.tap(song);egg.tap(song);egg.tap(song);assert.equal(opens,3);
console.log('Cover gesture passed: chosen recording, third tap only, wrong cover, reset, timeout and disable.');
'''
        result=subprocess.run(['node','-e',script,str(ROOT/'easter-egg.js')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_hidden_overlay_is_loaded_only_on_request_and_cannot_leak_into_main(self):
        script=r'''
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');let calls=[];
const track={id:'gbr-13-2-hidden',displayTitle:'Hidden song',visibility:'hidden',audio:{sources:{mp3:'https://media.test/a.mp3'}}};
const context={window:{GBR_RELEASE_CATALOGUE:'https://media.test/main',GBR_HIDDEN_CATALOGUE:'https://media.test/hidden'},AbortSignal,URL,console:{warn(){}},
 fetch:async url=>{calls.push(url);return {ok:true,json:async()=>({format:url.endsWith('hidden')?'gbr-hidden-releases-v1':'gbr-public-releases-v1',tracks:[track]})}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
 const main=await context.window.GBRloadReleases({tracks:[{id:track.id,displayTitle:'Old main song'},{id:'normal'}]});
 assert.equal(main.tracks.length,1);assert.equal(main.tracks[0].id,'normal');assert.equal(calls.length,1);assert.equal(calls[0],'https://media.test/main');
 const hidden=await context.window.GBRloadReleases({tracks:[]},{hidden:true});
 assert.equal(hidden.tracks.length,1);assert.equal(hidden.tracks[0].id,track.id);assert.equal(calls[1],'https://media.test/hidden');
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result=subprocess.run(['node','-e',script,str(ROOT/'releases.js')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_build_creates_unlisted_page_using_existing_player(self):
        spec=importlib.util.spec_from_file_location('egg_build',ROOT/'build.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'site';module.build(ROOT,target)
            hidden=(target/'hidden.html').read_text()
            self.assertIn('data-library="hidden"',hidden)
            self.assertIn('name="robots" content="noindex,nofollow"',hidden)
            self.assertIn('<script src="easter-egg.js"></script>',hidden)
            self.assertNotIn('data-library="hidden"',(target/'index.html').read_text())
            self.assertTrue((target/'easter-egg.js').is_file())
            legacy=(target/'easter.html').read_text()
            self.assertIn('data-library="easter"',legacy)
            self.assertIn('secretPage&&window.GBRloadReleases',legacy)
            self.assertIn("new URL(easterPage?'./':'easter.html'",legacy)
            self.assertIn('const saved=easterPage?',hidden)
