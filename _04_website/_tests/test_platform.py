"""Public projection tests against the real review and generation schemas."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '_tools'))
sys.path.insert(0, str(ROOT.parent / '_03_review/_generation_review'))
from export_platform import export, pricing
from _review.db import Database


def fixture(path):
    Database(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript((ROOT.parent / '_98_database/_gbr_database/schema.sql').read_text())
        conn.execute("INSERT INTO songs VALUES('song-1','A <song>','Punk','Rock')")
        for aid in ['audio-1', 'audio-2']:
            conn.execute('INSERT INTO audio(id,pcm_sha256,duration,sample_rate,channels,created_at) VALUES(?,?,180,44100,2,?)', (aid, 'hash-' + aid, '2026-10-19'))
        for gid, aid in [('generation-1','audio-1'),('generation-alias','audio-1'),('generation-2','audio-2')]:
            conn.execute('INSERT INTO generations(id,audio_id,song_id,metadata_key,original_filename,original_directory,created_at) VALUES(?,?,\'song-1\',?,?,?,?)',
                         (gid, aid, gid, r'E:\private\song.flac', r'E:\private', '2026-10-19'))
            conn.execute('INSERT INTO generation_metadata(generation_id,yaml_sha256,parsed_json,embedded_json,metadata_status) VALUES(?,?,?,?,?)',
                         (gid, 'yaml-' + gid, json.dumps({'song_id':'13','variant_id':'2','cluster':'Rock','style':'Punk','caption':'private caption','lyrics':'private lyrics'}), '{}', 'valid'))
        reviewer = conn.execute('SELECT id FROM reviewers LIMIT 1').fetchone()[0]
        conn.execute("INSERT INTO reviewers VALUES('reviewer-2','Private name','private','2026-10-19')")
        for assessment, rid in [('assessment-1',reviewer),('assessment-2','reviewer-2')]:
            conn.execute('INSERT INTO assessments VALUES(?,\'audio-1\',?,\'2026-10-19\')', (assessment,rid))
        for identifier, rid, assessment, revision, assessment_revision, scores, recorded in [
            ('review-old',reviewer,'assessment-1',1,1,[0,0,0,0],1),
            ('review-current',reviewer,'assessment-1',2,2,[5,4,3,2],1),
            ('review-other','reviewer-2','assessment-2',3,1,[3,2,1,0],0)]:
            conn.execute('''INSERT INTO reviews(id,generation_id,audio_id,audio_quality,composition,consistency,realism,average,bucket,
                created_at,mode,revision,session_id,listening_json,reviewer_id,assessment_id,assessment_revision,reason,tags_recorded)
                VALUES(?,'generation-1','audio-1',?,?,?,?,?,3,'2026-10-19','full',?,'session','{}',?,?,?,'private reason',?)''',
                (identifier,*scores,sum(scores)/4,revision,rid,assessment,assessment_revision,recorded))
        conn.execute("INSERT INTO review_tags VALUES('review-old','vocal_tone','negative')")
        conn.execute("INSERT INTO review_tags VALUES('review-current','vocal_tone','positive')")
        conn.execute("UPDATE review_tag_definitions SET active=0,label='Vocal timbre' WHERE id='vocal_tone'")
        conn.execute("INSERT INTO source_documents VALUES('source',X'0102','private-source','2026-10-19')")
        effective = {'model':'MiniMaxAI/MiniMax-Music3','dit':'fp32','text_encoder':'bf16','encoder_cfg':1.3,
                     'sampler_cfg':2.7,'encoder_seed':'18446744073709551615','sampler_seed':'42',
                     'top_k':75,'sampler_steps':50,'sampler':'heun','scheduler':'cosine','caption':'private caption'}
        conn.execute('''INSERT INTO generation_runs(id,source_sha256,song_id,variant_id,title,cluster,style,template_id,status,requested_json,effective_json,created_at)
             VALUES('run-1','source','13','2','A <song>','Rock','Punk','custom-1','succeeded',?,?,'2026-10-19')''',
             (json.dumps({'execution':{'memory_profile':'low_vram','encoding_cache':'prefer','address':'private endpoint'}}),json.dumps(effective)))
        conn.execute("INSERT INTO generation_output_links(run_id,review_generation_id) VALUES('run-1','generation-1')")
        conn.execute('INSERT INTO generation_artifacts VALUES(?,?,?,?,?,?,?,?)', ('artifact','run-1','audio','hash','private.flac',100,'private-key','{"attempt_id":"attempt-success","encoding":{"status":"hit","origin":"private"}}'))
        conn.execute('INSERT INTO worker_sessions VALUES(?,?,?,?,?,?,?,?,?,?)', ('worker','ssh://private-host','runpod','secret-pod','2026-10-19','private-gpu-id','A40',48*2**30,
             json.dumps({'gpu_name':'A40','provider_details':{'secret':'private token'},'cpu':'Xeon','ram_total_bytes':64*2**30}),json.dumps({'torch':'2.9','private':'token'})))
        for aid, status, seconds in [('attempt-failed','failed',30),('attempt-success','succeeded',90)]:
            conn.execute('INSERT INTO generation_attempts VALUES(?,?,?,?,?,?,?,?,?,?)', (aid,'run-1','worker','full_generation',status,'runpod_dedicated','{"revision":"model-sha"}','2026-10-19','2026-10-19',None))
            conn.execute('INSERT INTO generation_timing_spans VALUES(?,?,?,?,?,?,?,?,?,?,?)', (aid+'-total',aid,None,'total','2026-10-19','2026-10-19',seconds,.1 if status=='failed' else .3,'USD','estimate',1))
        conn.execute('INSERT INTO generation_timing_spans VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('nested','attempt-success','attempt-success-total','diffusion','2026-10-19','2026-10-19',75,.2,'USD','estimate',0))
        conn.execute('INSERT INTO generation_resource_measurements VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('attempt-success','diffusion','private-device',2**30,20*2**30,21*2**30,22*2**30,100,'sampled_device','{}','{"secret":"private"}'))
        conn.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?)', ('analysis','audio-1','1','2026-10-19','{"integrated_lufs":-14,"clipped_samples":0,"secret":"private"}','private-preview','private-spectrogram'))
        conn.commit()
    return path


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = fixture(self.root / 'gbr.sqlite3')
        self.output = self.root / 'platform.json'

    def test_current_reviews_deduplicate_recordings_and_preserve_unknowns(self):
        before = sha256(self.database.read_bytes()).hexdigest()
        data = export(self.database,self.output)
        self.assertEqual(data['count'],2)
        row = next(r for r in data['generations'] if r['id']=='audio-1')
        self.assertEqual(row['review_metrics'],dict(audio_quality=4,composition=3,consistency=2,realism=1))
        self.assertEqual(row['quality_score'],2.5)
        self.assertEqual(row['review_count'],2)
        self.assertEqual(len(row['generation_ids']),2)
        self.assertEqual({r['review_id'] for r in row['current_reviews']},{'review-current','review-other'})
        self.assertEqual(row['current_reviews'][0]['tags'],[{'tag_id':'vocal_tone','polarity':'positive'}])
        self.assertFalse(row['current_reviews'][1]['tags_recorded'])
        self.assertFalse(next(t for t in data['tag_definitions'] if t['id']=='vocal_tone')['active'])
        unknown = next(r for r in data['generations'] if r['id']=='audio-2')
        for key in ['quality_score','gpu_name','wall_seconds','peak_allocated_bytes']:
            self.assertIsNone(unknown[key])
        self.assertEqual(unknown['current_reviews'],[])
        self.assertEqual(sha256(self.database.read_bytes()).hexdigest(),before)
        self.assertFalse((self.root/'backups').exists())

    def test_projection_has_native_controls_hardware_and_no_private_payloads(self):
        data = export(self.database,self.output)
        text = self.output.read_text()
        for forbidden in ['private','Private name','ssh://','raw_yaml','original_directory','provider_details']:
            self.assertNotIn(forbidden,text)
        row = next(r for r in data['generations'] if r['id']=='audio-1')
        self.assertEqual(row['encoder_seed'],'18446744073709551615')
        self.assertEqual(row['sampler_cfg'],2.7)
        self.assertEqual(row['gpu_name'],'A40')
        self.assertEqual(row['cache_status'],'hit')
        self.assertEqual(row['wall_seconds'],90)
        self.assertEqual(row['peak_observed_device_bytes'],22*2**30)
        self.assertEqual(row['audio_analysis']['clipped_samples'],0)
        self.assertEqual(row['attempts'][1]['estimated_cost_usd'],.3)
        self.assertEqual(row['audio_file'],'song.flac')

    def test_missing_and_invalid_database_retain_previous_export(self):
        self.output.write_text('last completed export')
        with self.assertRaises(FileNotFoundError):export(self.root/'missing.sqlite3',self.output)
        self.assertFalse((self.root/'missing.sqlite3').exists())
        empty=self.root/'empty.sqlite3'
        sqlite3.connect(empty).close()
        with self.assertRaisesRegex(ValueError,'schema is incomplete'):export(empty,self.output)
        self.assertEqual(self.output.read_text(),'last completed export')

    def test_ambiguous_native_links_do_not_guess_worker_details(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO generation_runs(id,source_sha256,title,status,requested_json,effective_json,created_at) VALUES('run-2','source','Other','succeeded','{}','{}','2026-10-19')")
            conn.execute("INSERT INTO generation_output_links(run_id,review_generation_id) VALUES('run-2','generation-1')")
        row=next(r for r in export(self.database,self.output)['generations'] if r['id']=='audio-1')
        self.assertTrue(row['ambiguous_run_link'])
        self.assertIsNone(row['gpu_name'])
        self.assertEqual(row['attempts'],[])

    def test_prices_do_not_double_count_and_missing_is_not_zero(self):
        parent={'id':'parent','parent_id':None,'stage':'total','estimated_cost':.4,'currency':'USD','cost_basis':'estimate','includes_children':1}
        child={**parent,'id':'child','parent_id':'parent','stage':'diffusion','estimated_cost':.3,'includes_children':0}
        self.assertEqual(pricing([parent,child])['estimated_cost_usd'],.4)
        self.assertIsNone(pricing([child])['estimated_cost_usd'])
        self.assertEqual(pricing([child])['known_cost_usd'],.3)
        self.assertIsNone(pricing([])['estimated_cost_usd'])
        self.assertIsNone(pricing([])['known_cost_usd'])
        self.assertEqual(pricing([{**parent,'estimated_cost':0}])['estimated_cost_usd'],0)

    def test_one_command_exports_and_builds_without_modifying_the_database(self):
        website = self.root / 'website'
        shutil.copytree(ROOT,website,ignore=shutil.ignore_patterns('_site','_showcase','__pycache__','.gbr-site-*'))
        before = sha256(self.database.read_bytes()).hexdigest()
        result = subprocess.run([sys.executable,str(website/'build.py'),'--export-platform','--database',str(self.database)],
                                cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        page = (website/'_site/analytics/index.html').read_text()
        self.assertIn('data-platform-export="available"',page)
        data = json.loads((website/'_site/data/analytics/platform.json').read_text())
        self.assertEqual(data['count'],2)
        self.assertEqual(sha256(self.database.read_bytes()).hexdigest(),before)


if __name__ == '__main__':
    unittest.main()
