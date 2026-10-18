"""Review vocabulary, revision-bound observations and recording-aware analytics.

The database vocabulary is authoritative after the first migration. IDs never
change when labels are edited; disabling a tag keeps historical observations.
"""
import json
from pathlib import Path

VERSION = 4
POLARITIES = {'positive', 'negative'}
GROUP_COLUMNS = {'reviewer_id', 'text_encoder', 'dit', 'encoder_cfg', 'top_k',
                 'sampler', 'scheduler', 'sampler_cfg', 'sampler_steps',
                 'song_id', 'genre', 'cluster'}


def required(conn):
    return bool(conn.execute('PRAGMA table_info(reviews)').fetchall()) and not conn.execute(
        'SELECT 1 FROM schema_version WHERE version=?', (VERSION,)).fetchone()


def tags_json(review='r'):
    return """COALESCE((SELECT json_group_array(json_object('tag_id',tag_id,'polarity',polarity,
        'label',label,'category',category,'active',active)) FROM
        (SELECT t.tag_id,t.polarity,d.label,d.category,d.active FROM review_tags t
         JOIN review_tag_definitions d ON d.id=t.tag_id WHERE t.review_id=""" + review + """.id
         ORDER BY d.category_order,d.sort_order,d.id)), '[]')"""


def migrate(conn, analytics_sql):
    if conn.execute('SELECT 1 FROM schema_version WHERE version=?', (VERSION,)).fetchone():
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        if conn.execute('SELECT 1 FROM schema_version WHERE version=?', (VERSION,)).fetchone():
            conn.commit()
            return
        conn.execute('''CREATE TABLE review_tag_definitions(
            id TEXT PRIMARY KEY, category TEXT NOT NULL, label TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', category_order INTEGER NOT NULL,
            sort_order INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)))''')
        conn.execute('''CREATE TABLE review_tags(
            review_id TEXT NOT NULL REFERENCES reviews(id),
            tag_id TEXT NOT NULL REFERENCES review_tag_definitions(id),
            polarity TEXT NOT NULL CHECK(polarity IN ('positive','negative')),
            PRIMARY KEY(review_id,tag_id))''')
        conn.execute('CREATE INDEX idx_review_tags_lookup ON review_tags(tag_id,polarity,review_id)')
        conn.execute('ALTER TABLE reviews ADD COLUMN tags_recorded INTEGER NOT NULL DEFAULT 0 CHECK(tags_recorded IN (0,1))')
        for tag in json.loads(Path(__file__).with_name('review_tags.json').read_text(encoding='utf-8')):
            conn.execute('INSERT INTO review_tag_definitions VALUES(:id,:category,:label,:description,:category_order,:sort_order,1)', tag)
        conn.execute('DROP VIEW review_dataset')
        conn.execute('CREATE VIEW review_dataset AS SELECT r.*,v.display_name AS reviewer_name,' + tags_json() + ''' AS tags_json,
            (SELECT COUNT(*) FROM review_tags t WHERE t.review_id=r.id) AS tag_count
            FROM reviews r JOIN reviewers v ON v.id=r.reviewer_id''')
        conn.execute('DROP VIEW review_history_dataset')
        conn.execute('CREATE VIEW review_history_dataset AS SELECT h.*,v.display_name AS reviewer_name,' + tags_json() + ''' AS tags_json,
            COALESCE(r.tags_recorded,0) AS tags_recorded FROM review_history h
            LEFT JOIN reviewers v ON v.id=h.reviewer_id LEFT JOIN reviews r ON r.id=h.review_id''')
        conn.execute('DROP VIEW analytics_dataset')
        conn.execute(analytics_sql.replace('r.flags_json AS review_flags_json,',
                     'r.flags_json AS review_flags_json,r.tags_recorded,' + tags_json() + ' AS review_tags_json,'))
        # Join each revision to the generation actually reviewed. Multiple YAML
        # records of the same PCM never multiply observation counts.
        conn.execute('''CREATE VIEW review_tag_population AS SELECT r.*,s.id AS song_id,s.title,
            COALESCE(NULLIF(json_extract(m.parsed_json,'$.genre'),''),
                     NULLIF(json_extract(m.parsed_json,'$.intended_genre'),''),s.genre) AS genre,
            COALESCE(json_extract(m.parsed_json,'$.cluster'),s.cluster) AS cluster,
            m.text_encoder,m.dit,m.encoder_cfg,m.top_k,m.sampler,m.scheduler,m.sampler_cfg,m.sampler_steps,
            NOT EXISTS(SELECT 1 FROM reviews newer WHERE newer.assessment_id=r.assessment_id
                AND newer.assessment_revision>r.assessment_revision) AS is_current_review,
            r.revision=(SELECT MAX(revision) FROM reviews WHERE audio_id=r.audio_id) AS is_latest_recording_review
            FROM reviews r JOIN generations g ON g.id=r.generation_id
            JOIN songs s ON s.id=g.song_id JOIN generation_metadata m ON m.generation_id=g.id''')
        conn.execute('''CREATE VIEW review_tag_dataset AS SELECT p.id AS review_id,p.generation_id,p.audio_id,
            p.reviewer_id,v.display_name AS reviewer_name,p.assessment_id,p.assessment_revision,p.revision,
            p.created_at,p.tags_recorded,p.is_current_review,p.is_latest_recording_review,
            t.tag_id,t.polarity,d.category,d.label,d.active,p.song_id,p.title,p.genre,p.cluster,
            p.text_encoder,p.dit,p.encoder_cfg,p.top_k,p.sampler,p.scheduler,p.sampler_cfg,p.sampler_steps,
            p.audio_quality,p.composition,p.consistency,p.realism,p.average
            FROM review_tags t JOIN review_tag_population p ON p.id=t.review_id
            JOIN review_tag_definitions d ON d.id=t.tag_id JOIN reviewers v ON v.id=p.reviewer_id''')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Tag migration found foreign key errors; the transaction was rolled back')
        conn.execute('INSERT INTO schema_version VALUES(?)', (VERSION,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def definitions(db):
    return db.all('SELECT * FROM review_tag_definitions ORDER BY category_order,sort_order,id')


def selections(conn, review_id):
    return [dict(r) for r in conn.execute('SELECT tag_id,polarity FROM review_tags WHERE review_id=? ORDER BY tag_id', (review_id,))]


def validate(conn, value, previous_id=None):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError('Tags must be a list of at most 200 observations')
    previous = {t['tag_id']: t['polarity'] for t in selections(conn, previous_id)} if previous_id else {}
    result, seen = [], set()
    for tag in value:
        if not isinstance(tag, dict) or set(tag) != {'tag_id', 'polarity'}:
            raise ValueError('Each tag needs only tag_id and polarity')
        tag_id, polarity = tag['tag_id'], tag['polarity']
        if not isinstance(tag_id, str) or not isinstance(polarity, str) or polarity not in POLARITIES:
            raise ValueError('Tag polarity must be positive or negative')
        if tag_id in seen:
            raise ValueError('A tag may have only one polarity per review')
        definition = conn.execute('SELECT active FROM review_tag_definitions WHERE id=?', (tag_id,)).fetchone()
        if not definition or (not definition['active'] and previous.get(tag_id) != polarity):
            raise ValueError('Unknown or inactive review tag: ' + tag_id)
        seen.add(tag_id)
        result.append({'tag_id': tag_id, 'polarity': polarity})
    return sorted(result, key=lambda tag: tag['tag_id'])


def statistics(db, group_by=(), reviewer_id=None):
    columns = list(dict.fromkeys(group_by))
    if set(columns) - GROUP_COLUMNS:
        raise ValueError('Unknown tag analysis grouping')
    prefix = ','.join(columns) + ',' if columns else ''
    suffix = ' GROUP BY ' + ','.join(columns) if columns else ''
    where = ' WHERE is_current_review=1' + (' AND reviewer_id=?' if reviewer_id else '')
    args = (reviewer_id,) if reviewer_id else ()
    population = db.all('SELECT ' + prefix + '''COUNT(DISTINCT audio_id) AS reviewed_recordings,
        COUNT(*) AS current_reviews,COALESCE(SUM(tags_recorded),0) AS tags_recorded_reviews,
        COUNT(DISTINCT CASE WHEN EXISTS(SELECT 1 FROM review_tags t WHERE t.review_id=p.id) THEN audio_id END) AS tagged_recordings
        FROM review_tag_population p''' + where + suffix, args)
    observations = db.all('SELECT ' + prefix + '''tag_id,polarity,category,label,
        COUNT(DISTINCT audio_id) AS recordings,COUNT(DISTINCT review_id) AS reviews,
        AVG(audio_quality) AS audio_quality,AVG(composition) AS composition,
        AVG(consistency) AS consistency,AVG(realism) AS realism
        FROM review_tag_dataset''' + where + ' GROUP BY ' + prefix + 'tag_id,polarity ORDER BY ' + prefix + 'tag_id,polarity', args)
    denominators = {tuple(row[k] for k in columns): row for row in population}
    for row in observations:
        total = denominators[tuple(row[k] for k in columns)]
        row['reviewed_recordings'] = total['reviewed_recordings']
        row['recording_frequency'] = row['recordings'] / total['reviewed_recordings'] if total['reviewed_recordings'] else None
    return {'scope': 'latest revision per reviewer and recording', 'group_by': columns,
            'population': population, 'observations': observations}
