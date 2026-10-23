"""Verified, resumable Stage 4 -> showcase moves on the shared task ledger."""
import json
import os
from pathlib import Path
import re
import uuid

from .service import canonical, digest, now


def recording_folder(root, metadata, audio_id):
    for field in ('song_id', 'variant_id'):
        if not re.fullmatch(r'[1-9][0-9]*', str(metadata.get(field) or '')):
            raise ValueError('Showcase requires valid song_id and variant_id')
    if not re.fullmatch(r'[a-f0-9]{64}', audio_id):
        raise ValueError('Showcase requires the decoded recording identity')
    return Path(root)/('_song-'+str(metadata['song_id']))/('_variant-'+str(metadata['variant_id']))/('_recording-'+audio_id)


def remap(value, source, destination):
    """Only file references change. Original YAML and historical source data do not."""
    if isinstance(value, str):
        if value == str(source):return str(destination)
        if value.startswith(str(source)+os.sep):
            return str(destination/Path(value).relative_to(source))
    if isinstance(value, list):return [remap(v, source, destination) for v in value]
    if isinstance(value, dict):return {k:remap(v, source, destination) for k,v in value.items()}
    return value


class Showcase:
    def __init__(self, publisher):
        self.publisher=publisher
        self.prep=publisher.prep
        self.db=publisher.db
        self.root=publisher.root/'_05_showcase'

    def move(self, gid, prepared_id):
        """Copy/verify, switch references atomically, then remove verified sources.

        The journal is written before any file operation. Retrying after a crash,
        upload failure or Windows file lock resumes the same move, not a new copy.
        """
        from _review.archive import verified_copy
        with self.prep.lock:
            row=self.db.one('SELECT g.audio_id,m.parsed_json FROM generations g JOIN generation_metadata m ON m.generation_id=g.id WHERE g.id=?',(gid,))
            previous=self.db.one("SELECT * FROM processing_tasks WHERE input_sha256=? AND kind='showcase_move' AND status<>'cancelled' ORDER BY rowid DESC LIMIT 1",(row['audio_id'],))
            if previous:
                identifier=previous['id'];plan=json.loads(previous['recipe_json'])
            else:
                active=self.db.one("SELECT 1 FROM processing_tasks t JOIN generations g ON g.id=t.generation_id WHERE g.audio_id=? AND t.status IN ('queued','running') AND t.kind NOT IN ('release_publish')",(row['audio_id'],))
                if active:raise ValueError('Wait for processing and release preparation to finish before completing this recording')
                if self.db.one("SELECT 1 FROM archive_operations WHERE audio_id=? AND status IN ('pending','running','failed')",(row['audio_id'],)):
                    raise ValueError('Finish the pending archive operation before completing this recording')
                source=self.prep.tracks/row['audio_id']
                destination=recording_folder(self.root,json.loads(row['parsed_json']),row['audio_id'])
                if not source.is_dir():raise ValueError('Preparation bundle is unavailable')
                if destination.exists():raise ValueError('Showcase destination already exists without a move journal; retained both locations')
                items=self.inventory(source)
                identifier=uuid.uuid4().hex
                plan={'source':str(source),'destination':str(destination),'items':items,'prepared_id':prepared_id}
                self.db.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at) VALUES(?,?,'showcase_move','running',?,?,?,?)",(identifier,gid,'showcase:'+row['audio_id'],canonical(plan),row['audio_id'],now()))
            source,destination=Path(plan['source']),Path(plan['destination'])
            if not source.resolve().is_relative_to(self.prep.tracks.resolve()) or not destination.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('Move journal points outside managed storage')
            try:
                committed=json.loads(previous['telemetry_json']).get('committed',False) if previous else False
                if not committed:
                    if self.inventory(source)!=plan['items']:raise ValueError('Preparation files changed since completion began; retained sources')
                    for item in plan['items']:
                        relative=Path(item['relative'])
                        if relative.is_absolute() or '..' in relative.parts or (destination/relative).is_symlink() or not (destination/relative).resolve().is_relative_to(destination.resolve()):
                            raise ValueError('Unsafe showcase file destination')
                        verified_copy(source/item['relative'],destination/item['relative'],item['sha256'],identifier)
                    if self.inventory(source)!=plan['items']:raise ValueError('Preparation files changed while moving; retained sources')
                    with self.db.transaction() as c:
                        selected=c.execute("SELECT status FROM publishing_selection WHERE generation_id=?",(gid,)).fetchone()
                        if not selected or selected[0] not in {'selected','published'}:raise ValueError('Publish selection changed; sources retained')
                        for table,key,column in [('audio','id','archive_path'),('media_files','id','path'),
                                                ('generation_metadata','generation_id','yaml_archive_path'),
                                                ('generation_sources','id','yaml_path'),('generation_artifacts','id','uri')]:
                            for item in c.execute(f'SELECT {key},{column} FROM {table} WHERE {column} IS NOT NULL').fetchall():
                                moved=remap(item[1],source,destination)
                                if moved!=item[1]:c.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?',(moved,item[0]))
                        for item in c.execute("SELECT id,recipe_json FROM processing_tasks WHERE kind<>'showcase_move'").fetchall():
                            before=json.loads(item[1]);after=remap(before,source,destination)
                            if after!=before:c.execute('UPDATE processing_tasks SET recipe_json=? WHERE id=?',(canonical(after),item[0]))
                        c.execute("UPDATE processing_tasks SET telemetry_json=?,status='running',error=NULL WHERE id=?",(canonical({'committed':True,'phase':'cleanup'}),identifier))
                # A changed/missing destination can never cause source deletion.
                pending=[]
                for item in plan['items']:
                    old,new=source/item['relative'],destination/item['relative']
                    if new.is_symlink() or not new.resolve().is_relative_to(destination.resolve()) or not new.is_file() or digest(new)!=item['sha256']:raise ValueError('Showcase file verification failed; sources retained')
                    if old.exists():
                        if old.is_symlink() or digest(old)!=item['sha256']:raise ValueError('Source changed after completion; retained it')
                        try:old.unlink()
                        except OSError:pending.append(str(old))
                if source.exists():
                    for directory in sorted((p for p in source.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
                        try:directory.rmdir()
                        except OSError:pass
                    try:source.rmdir()
                    except OSError:pass
                self.db.execute("UPDATE processing_tasks SET status='done',completed_at=?,error=NULL,telemetry_json=? WHERE id=?",(now(),canonical({'committed':True,'phase':'showcase','folder':str(destination),'cleanup_pending':pending}),identifier))
                return {'folder':str(destination),'cleanup_pending':pending}
            except Exception as exc:
                self.db.execute("UPDATE processing_tasks SET status='failed',error=? WHERE id=?",(str(exc),identifier))
                raise

    @staticmethod
    def inventory(directory):
        files=[]
        for path in sorted(directory.rglob('*')):
            if path.is_symlink():raise ValueError('Preparation bundle contains a symlink; retained it')
            if path.is_file():files.append({'relative':path.relative_to(directory).as_posix(),'sha256':digest(path)})
        return files

    def reset(self,gid):
        """Return an uncommitted failed move to editing; never touch the source."""
        with self.prep.lock:
            row=self.db.one("SELECT * FROM processing_tasks WHERE kind='showcase_move' AND input_sha256=(SELECT audio_id FROM generations WHERE id=?) AND status='failed' ORDER BY rowid DESC LIMIT 1",(gid,))
            if not row or json.loads(row['telemetry_json']).get('committed'):
                raise ValueError('Only an incomplete local move can be reset; completed showcase bundles are retained')
            if self.db.one("SELECT 1 FROM processing_tasks WHERE generation_id=? AND kind='release_publish' AND status IN ('queued','running')",(gid,)):
                raise ValueError('Wait for publication to stop before resetting')
            plan=json.loads(row['recipe_json']);source,destination=Path(plan['source']),Path(plan['destination'])
            if not source.resolve().is_relative_to(self.prep.tracks.resolve()) or not destination.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('Move journal points outside managed storage')
            for item in plan['items']:
                old,new=source/item['relative'],destination/item['relative']
                if new.exists():
                    if not old.is_file() or old.is_symlink() or new.is_symlink() or digest(new)!=item['sha256'] or digest(old)!=item['sha256']:
                        raise ValueError('A file changed; retained both copies for inspection')
            for item in plan['items']:(destination/item['relative']).unlink(missing_ok=True)
            if destination.exists():
                for folder in sorted((p for p in destination.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
                    try:folder.rmdir()
                    except OSError:pass
                try:destination.rmdir()
                except OSError:raise ValueError('Unrecognised files remain at the destination; retained them') from None
            self.db.execute("UPDATE processing_tasks SET status='cancelled',completed_at=? WHERE id=?",(now(),row['id']))
            return {'reset':True}
