import json
from pathlib import Path
import sys
import tempfile
import unittest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review.app import create_app


class IntegratedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root/'config.json', start_worker=False)
        # Test data and editor recovery data stay entirely inside this fixture.
        self.app.state.yaml_editor.backups = self.root/'backups'
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'X-GBR-Request':'1'}
        self.db = self.app.state.service.db
        self.db.execute("INSERT INTO songs(id,title,genre) VALUES('s','Song','Jazz')")
        self.db.execute("INSERT INTO audio(id,pcm_sha256,duration,sample_rate,channels,created_at) VALUES('a','hash',12,44100,2,'2026-01-01')")
        self.db.execute("INSERT INTO generations(id,audio_id,song_id,metadata_key,original_filename,original_directory,created_at) VALUES('g','a','s','key','song.flac',?,'2026-01-01')", (str(self.root),))

    def test_preview_contains_measured_flags_without_separate_request(self):
        preview = self.root/'preview.json'
        preview.write_text(json.dumps({'duration':12,'waveform':[],'channels':2,'frequency_axis':{'max_hz':8000}}))
        image = self.root/'spectrum.png'
        image.touch()
        metrics={'duration':12,'rms_dbfs':-14,'clipped_fraction':0.002,'integrated_lufs':-12,'true_peak_dbtp_4x':1.2}
        self.db.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?)', ('an','a','test','2026-01-01',json.dumps(metrics),str(preview),str(image)))
        self.db.execute('INSERT INTO audio_events VALUES(?,?,?,?,?,?,?,?,?,?)', ('ev','an','a','possible_dropout',4,6,1,1,'{}','test'))
        result = self.client.get('/api/tracks/g/preview')
        self.assertEqual(result.status_code, 200, result.text)
        checks = result.json()['technical_checks']
        self.assertEqual(checks['status'], 'flagged')
        self.assertEqual(checks['measurements']['integrated_lufs'], -12)
        self.assertEqual(checks['flags'][-1]['start'], 4)
        self.assertIsNone(checks['human_rating'])
        self.assertEqual(self.client.get('/api/tracks/g/triage').status_code, 404)
        self.assertEqual(self.db.one('SELECT COUNT(*) AS n FROM reviews')['n'], 0)

    def test_pending_analysis_is_not_a_clean_bill_of_health(self):
        result = self.client.get('/api/tracks/g/preview').json()
        self.assertFalse(result['ready'])
        self.assertEqual(result['technical_checks']['status'], 'not_assessed')

    def test_malformed_historical_metrics_do_not_block_playback_preview(self):
        preview = self.root/'old-preview.json'
        preview.write_text(json.dumps({'duration':12,'waveform':[],'channels':2,'frequency_axis':{'max_hz':8000}}))
        image = self.root/'old-spectrum.png'
        image.touch()
        self.db.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?)', ('old','a','old','2026-01-01','null',str(preview),str(image)))
        result = self.client.get('/api/tracks/g/preview')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['ready'])
        self.assertEqual(result.json()['technical_checks']['status'], 'not_assessed')

    def test_editor_full_api_workflow_and_undo(self):
        path=self.root/'song.yaml'
        raw=b'# source\r\nsong_id: 1\r\nlyrics: |\r\n  line one\r\n  line two\r\n'
        path.write_bytes(raw)
        scan=self.client.post('/api/yaml-editor/scan',headers=self.headers,json={'directory':str(self.root)})
        self.assertEqual(scan.status_code,200,scan.text)
        row=scan.json()['files'][0]
        preview=self.client.post('/api/yaml-editor/preview',headers=self.headers,json={'directory':str(self.root),'files':['song.yaml'],'expected':{'song.yaml':row['sha256']},'operations':[{'kind':'set','key':'song_id','value':8}]})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(path.read_bytes(),raw)
        save=self.client.post('/api/yaml-editor/apply',headers=self.headers,json={'token':preview.json()['token']})
        self.assertEqual(save.status_code,200,save.text)
        self.assertEqual(path.read_bytes(),raw.replace(b'song_id: 1',b'song_id: 8'))
        restore=self.client.post('/api/yaml-editor/restore-preview',headers=self.headers,json={'batch':save.json()['batch']}).json()
        self.client.post('/api/yaml-editor/apply',headers=self.headers,json={'token':restore['token']}).raise_for_status()
        self.assertEqual(path.read_bytes(),raw)

    def test_editor_uses_existing_request_protection(self):
        result=self.client.post('/api/yaml-editor/scan',json={'directory':str(self.root)})
        self.assertEqual(result.status_code,403)
        self.assertEqual(self.client.get('/tools/yaml-editor/editor.js').status_code,200)
        self.assertIn('yamlTab', self.client.get('/').text)

    def test_order_template_endpoint_and_default_reorder(self):
        template=self.root/'order-template.yaml'
        template.write_text('lyrics:\ntitle:\nsong_id:\n')
        self.app.state.yaml_editor.default_order_template=template
        source=self.root/'track.yaml'
        source.write_bytes(b'song_id: 13\ntitle: Actual title\nlyrics: |-\n  Keep this line\n')
        info=self.client.post('/api/yaml-editor/order-template',headers=self.headers,json={})
        self.assertEqual(info.status_code,200,info.text)
        self.assertEqual(info.json()['keys'],['lyrics','title','song_id'])
        scan=self.client.post('/api/yaml-editor/scan',headers=self.headers,json={'directory':str(self.root)})
        self.assertEqual(scan.json()['default_order_template'],str(template))
        preview=self.client.post('/api/yaml-editor/preview',headers=self.headers,json={'directory':str(self.root),'files':['track.yaml'],'operations':[{'kind':'reorder'}]})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['order_templates'][0]['sha256'],info.json()['sha256'])
        saved=self.client.post('/api/yaml-editor/apply',headers=self.headers,json={'token':preview.json()['token']})
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(source.read_bytes(),b'lyrics: |-\n  Keep this line\ntitle: Actual title\nsong_id: 13\n')

    def test_remote_clients_cannot_edit_host_files(self):
        from review.config import password_hash
        # Existing local-only security blocks unauthenticated remote clients first.
        remote=TestClient(self.app,base_url='http://localhost',client=('203.0.113.20',123))
        response=remote.post('/api/yaml-editor/scan',headers=self.headers,json={'directory':str(self.root)})
        self.assertEqual(response.status_code,401)
        # Even an authenticated reviewer still needs to be on the host to edit sources.
        config=self.app.state.service.config
        config.values['password_hash']=password_hash('fixture password')
        remote.post('/api/login',headers=self.headers,json={'password':'fixture password'}).raise_for_status()
        response=remote.post('/api/yaml-editor/scan',headers=self.headers,json={'directory':str(self.root)})
        self.assertEqual(response.status_code,403)


if __name__=='__main__':
    unittest.main()
