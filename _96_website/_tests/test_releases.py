"""The R2 overlay preserves the public catalogue and only accepts public URLs."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_overlay_merge_fallback_validation_and_legacy_identity(self):
        script=r'''
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const base={tracks:[{id:'old',shareToken:'oldTOKEN'}],easter:{tracks:[{id:'hidden'}]}};
const track={id:'gbr-13-2-aaa',displayTitle:'Song',release_id:'release',audio:{sources:{mp3:'https://media.test/audio.mp3',flac:null}}};
let result={format:'gbr-public-releases-v1',tracks:[track]},failure=false;
const context={window:{GBR_RELEASE_CATALOGUE:'https://media.test/catalogue.json'},AbortSignal,URL,
console:{warn(){}},fetch:async()=>{if(failure)throw Error('offline');return{ok:true,json:async()=>result}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
 let merged=await context.window.GBRloadReleases(base);
 assert.equal(merged.tracks.length,2);assert.equal(merged.tracks[0].shareToken,'oldTOKEN');assert.equal(merged.easter,base.easter);
 assert.equal(base.tracks.length,1); // no mutation of saved fallback
 result={format:'gbr-public-releases-v1',tracks:[track,{...track,displayTitle:'duplicate'}]};
 assert.equal(await context.window.GBRloadReleases(base),base);
 result={format:'gbr-public-releases-v1',tracks:[{...track,audio:{sources:{mp3:'javascript:bad()'}}}]};
 assert.equal(await context.window.GBRloadReleases(base),base);
 result={format:'wrong',tracks:[]};assert.equal(await context.window.GBRloadReleases(base),base);
 failure=true;assert.equal(await context.window.GBRloadReleases(base),base);
 console.log('Release overlay: merge, identity, hidden tracks, unsafe URLs, duplicates and offline fallback passed.');
})().catch(error=>{console.error(error);process.exit(1)});
'''
        result=subprocess.run(['node','-e',script,str(ROOT/'releases.js')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_only_public_build_is_uploaded_by_pages_workflows(self):
        import yaml
        pages=[]
        for path in (ROOT.parent/'.github/workflows').glob('*.yml'):
            workflow=yaml.safe_load(path.read_text())
            for job in workflow.get('jobs',{}).values():
                for step in job.get('steps',[]):
                    if step.get('uses','').startswith('actions/upload-pages-artifact@'):
                        pages.append(step['with']['path'])
        self.assertEqual(pages,['_96_website/_site'])

    def test_build_includes_overlay_and_preserves_existing_counts(self):
        import importlib.util,tempfile
        spec=importlib.util.spec_from_file_location('release_build',ROOT/'build.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'site';result=module.build(ROOT,output)
            original=json.loads((ROOT/'_data/catalogue.json').read_text())
            self.assertEqual(result['tracks'],len(original['tracks']));self.assertEqual(result['hidden_tracks'],len(original['easter']['tracks']))
            self.assertIn('<script src="releases.js"></script>',(output/'index.html').read_text())
            self.assertTrue((output/'releases.js').is_file())


if __name__=='__main__':unittest.main()
