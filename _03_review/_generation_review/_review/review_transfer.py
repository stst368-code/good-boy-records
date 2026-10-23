"""Lossless review JSON transfer for recordings already present in the catalogue.

Imports are transactional, idempotent and never replace existing review revisions,
free-text reasons, vocabulary edits or media. A conflicting revision aborts the batch.
"""
import json
import sqlite3
from contextlib import closing
from .util import now


def bundle(db):
    with closing(db.connect()) as c:
        c.execute('BEGIN')
        rows = lambda sql: [dict(r) for r in c.execute(sql)]
        return {'schema_version': 4, 'format': 'gbr-reviews', 'exported_at': now(),
                'reviews': rows('SELECT * FROM review_dataset ORDER BY audio_id,revision'),
                'reviewers': rows('SELECT * FROM reviewers'),
                'assessments': rows('SELECT * FROM assessments WHERE id IN (SELECT assessment_id FROM reviews)'),
                'review_history': rows('SELECT * FROM review_history WHERE review_id IS NOT NULL'),
                'tag_definitions': rows('SELECT * FROM review_tag_definitions')}


def restore(db, payload):
    try:
        return _restore(db, payload)
    except (sqlite3.IntegrityError, KeyError, TypeError) as exc:
        raise ValueError('Review export is invalid or conflicts with this catalogue; import was rolled back: ' + str(exc)) from exc


def _restore(db, payload):
    if not isinstance(payload, dict) or payload.get('format') != 'gbr-reviews' or payload.get('schema_version') != 4:
        raise ValueError('Choose a GBR reviews JSON export (schema 4)')
    for key in ('reviews', 'reviewers', 'assessments', 'review_history', 'tag_definitions'):
        if not isinstance(payload.get(key), list) or any(not isinstance(row, dict) for row in payload[key]):
            raise ValueError('Invalid review export: ' + key)
    imported = existing = 0
    with db.transaction() as c:
        def insert_identity(table, row):
            columns = [r[1] for r in c.execute('PRAGMA table_info(' + table + ')')]
            if set(row) != set(columns):
                raise ValueError('Invalid ' + table + ' record')
            saved = c.execute('SELECT * FROM ' + table + ' WHERE id=?', (row['id'],)).fetchone()
            if saved:
                # Display names and configuration labels may have changed locally.
                keys = {'id', 'name_key'} if table == 'reviewers' else set(columns)
                if any(saved[k] != row[k] for k in keys):
                    raise ValueError('Conflicting ' + table + ' identity: ' + str(row['id']))
            else:
                placeholders = ','.join('?' for _ in columns)
                c.execute('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES(' + placeholders + ')', tuple(row[k] for k in columns))
        for row in payload['reviewers']:
            insert_identity('reviewers', row)
        for row in payload['tag_definitions']:
            if not c.execute('SELECT 1 FROM review_tag_definitions WHERE id=?', (row.get('id'),)).fetchone():
                insert_identity('review_tag_definitions', row)
        for row in payload['assessments']:
            if not c.execute('SELECT 1 FROM audio WHERE id=?', (row.get('audio_id'),)).fetchone():
                raise ValueError('Recording is missing from this catalogue: ' + str(row.get('audio_id')))
            insert_identity('assessments', row)
        columns = [r[1] for r in c.execute('PRAGMA table_info(reviews)')]
        for row in payload['reviews']:
            if set(columns) - set(row):
                raise ValueError('Review export is missing required fields')
            generation = c.execute('SELECT audio_id FROM generations WHERE id=?', (row['generation_id'],)).fetchone()
            if not generation or generation[0] != row['audio_id']:
                raise ValueError('Generation/audio identity does not match this catalogue: ' + row['generation_id'])
            try:
                tags = json.loads(row['tags_json'])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError('Invalid tags in review export') from exc
            if not isinstance(tags, list) or any(not isinstance(t, dict) for t in tags):
                raise ValueError('Invalid tags in review export')
            observations = []
            for tag in tags:
                if not isinstance(tag.get('tag_id'), str) or tag.get('polarity') not in ('positive', 'negative'):
                    raise ValueError('Invalid tag observation')
                if not c.execute('SELECT 1 FROM review_tag_definitions WHERE id=?', (tag['tag_id'],)).fetchone():
                    raise ValueError('Missing tag definition: ' + tag['tag_id'])
                observations.append((tag['tag_id'], tag['polarity']))
            if len({t[0] for t in observations}) != len(observations):
                raise ValueError('A tag may have only one polarity per review')
            if observations and not row['tags_recorded']:
                raise ValueError('Tagged review must record the tag selection')
            saved = c.execute('SELECT * FROM reviews WHERE id=?', (row['id'],)).fetchone()
            if saved:
                actual = sorted(tuple(t) for t in c.execute('SELECT tag_id,polarity FROM review_tags WHERE review_id=?', (row['id'],)))
                if any(saved[k] != row[k] for k in columns) or actual != sorted(observations):
                    raise ValueError('Existing review differs; nothing was overwritten: ' + row['id'])
                existing += 1
            else:
                c.execute('INSERT INTO reviews(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')', tuple(row[k] for k in columns))
                c.executemany('INSERT INTO review_tags VALUES(?,?,?)', [(row['id'], *tag) for tag in observations])
                imported += 1
        for row in payload['reviews']:
            previous = row['previous_review_id']
            if previous:
                parent = c.execute('SELECT audio_id,reviewer_id,revision FROM reviews WHERE id=?', (previous,)).fetchone()
                if not parent or parent['audio_id'] != row['audio_id'] or parent['reviewer_id'] != row['reviewer_id'] or parent['revision'] >= row['revision']:
                    raise ValueError('Review revision history does not match')
        history_columns = [r[1] for r in c.execute('PRAGMA table_info(review_history)') if r[1] != 'id']
        for row in payload['review_history']:
            if set(history_columns) - set(row):
                raise ValueError('Incomplete review history')
            review = c.execute('SELECT audio_id,reviewer_id,generation_id FROM reviews WHERE id=?', (row['review_id'],)).fetchone()
            if not review or any(row[k] != review[k] for k in ('audio_id', 'reviewer_id', 'generation_id')):
                raise ValueError('History does not belong to the exported review')
            saved = c.execute('SELECT * FROM review_history WHERE review_id=? AND action=?', (row['review_id'], row['action'])).fetchall()
            if not any(all(saved_row[k] == row[k] for k in history_columns) for saved_row in saved):
                c.execute('INSERT INTO review_history(' + ','.join(history_columns) + ') VALUES(' + ','.join('?' for _ in history_columns) + ')', tuple(row[k] for k in history_columns))
        if c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Review import failed foreign key verification')
    return {'imported': imported, 'already_present': existing}
