"""Authoring errors must not publish a broken process guide or private files."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_making',ROOT/'_tools/build_making.py')
making=importlib.util.module_from_spec(spec);spec.loader.exec_module(making)


class ProcessGuideTests(unittest.TestCase):
    def setUp(self):
        self.payload=yaml.safe_load((ROOT/'_content/_how_its_made/tour.yaml').read_text())
        self.source=ROOT/'_content/_how_its_made'

    def test_real_guide_is_connected_and_does_not_invent_experiment_audio(self):
        self.assertEqual(making.validate(self.payload,self.source),set())
        self.assertEqual([s['id'] for s in self.payload['stages']],['writing','generation','review','post-process','publishing'])
        experiments=[b for s in self.payload['stages'] for n in s['nodes'] for b in n['body'] if b['type']=='experiment']
        self.assertEqual(len(experiments),1)
        self.assertEqual(experiments[0]['samples'],[])
        self.assertEqual((experiments[0]['min'],experiments[0]['max']),(1,50))

    def test_missing_targets_cycles_and_unreachable_nodes_fail(self):
        for edit in ['target','cycle','orphan']:
            data=deepcopy(self.payload);nodes=data['stages'][0]['nodes']
            if edit=='target':nodes[0]['next']=[{'to':'missing'}]
            elif edit=='cycle':nodes[-1]['next']=[{'to':nodes[0]['id']}]
            else:nodes[0]['next']=[]
            with self.subTest(edit=edit),self.assertRaises(ValueError):making.validate(data,self.source)

    def test_duplicate_ids_and_invalid_deep_links_fail(self):
        for kind in ['stage','node','topic','link']:
            data=deepcopy(self.payload)
            if kind=='stage':data['stages'][1]['id']=data['stages'][0]['id']
            elif kind=='node':data['stages'][0]['nodes'][1]['id']='idea'
            elif kind=='topic':data['topics'][1]['id']='platform'
            else:data['stages'][0]['nodes'][0]['body'].append({'type':'stage-link','stage':'writing','node':'missing','label':'bad'})
            with self.subTest(kind=kind),self.assertRaises(ValueError):making.validate(data,self.source)

    def test_only_referenced_local_media_is_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'root';out=Path(tmp)/'out';out.mkdir()
            shutil.copytree(ROOT/'_how_its_made',root/'_how_its_made')
            source=root/'_content/_how_its_made';source.mkdir(parents=True)
            (source/'_media').mkdir();(source/'_media/example.webp').write_bytes(b'image')
            (source/'_media/private.txt').write_text('not public')
            data=deepcopy(self.payload);data['stages'][0]['nodes'][0]['body'].append({'type':'media','kind':'image','src':'media/example.webp','alt':'Example'})
            (source/'tour.yaml').write_text(yaml.safe_dump(data))
            result=making.stage(root,out)
            self.assertEqual(result['media_files'],1)
            self.assertTrue((out/'how-its-made/media/example.webp').is_file())
            self.assertFalse((out/'how-its-made/media/private.txt').exists())
            self.assertFalse(list(out.rglob('*.yaml')))
            self.assertEqual(json.loads((out/'how-its-made/tour.json').read_text())['format'],'gbr-making-v1')

    def test_script_credential_and_traversal_urls_fail(self):
        for url in ['javascript:alert(1)','data:text/html,test','https://user:pass@example.com/a.mp3','media/../private.mp3','media/%2e%2e/private.mp3','showcase:../private.mp3','//example.com/a.mp3']:
            data=deepcopy(self.payload);data['stages'][0]['nodes'][0]['body'].append({'type':'media','kind':'audio','src':url})
            with self.subTest(url=url),self.assertRaises(ValueError):making.validate(data,self.source)

    def test_experiments_accept_fractional_grids_and_reject_bad_values(self):
        block={'type':'experiment','id':'cfg','title':'CFG','parameter':'CFG','conditions':'Everything else fixed','min':1,'max':2,'step':.1,'samples':[{'value':1.3,'audio':'https://example.com/1.mp3'}]}
        data=deepcopy(self.payload);data['stages'][0]['nodes'][0]['body'].append(block)
        making.validate(data,self.source)
        for values in [[1.3,1.3],[1.35],[0],[float('nan')],[True]]:
            block['samples']=[{'value':v,'audio':'https://example.com/a.mp3'} for v in values]
            with self.subTest(values=values),self.assertRaises(ValueError):making.validate(data,self.source)

    def test_animated_image_video_and_audio_blocks_are_supported(self):
        data=deepcopy(self.payload)
        for kind,ext in [('image','gif'),('image','webp'),('video','mp4'),('video','webm'),('audio','mp3')]:
            data['stages'][0]['nodes'][0]['body'].append({'type':'media','kind':kind,'src':'https://example.com/a.'+ext,'alt':'A description'})
        making.validate(data,self.source)

    def test_public_comparison_bindings_are_explicit_valid_and_unique(self):
        for edit in ['slot','parameter','duplicate']:
            data=deepcopy(self.payload)
            block=next(b for s in data['stages'] for n in s['nodes'] for b in n['body'] if b['type']=='experiment')
            if edit=='slot':block['published_slot']='../private'
            elif edit=='parameter':block['parameter_field']='arbitrary_path'
            else:
                duplicate=deepcopy(block);duplicate['id']='duplicate-example'
                data['stages'][0]['nodes'][0]['body'].append(duplicate)
            with self.subTest(edit=edit),self.assertRaises(ValueError):making.validate(data,self.source)


if __name__=='__main__':unittest.main()
