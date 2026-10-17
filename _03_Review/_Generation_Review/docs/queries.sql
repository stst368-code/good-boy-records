-- Review distribution: one assessment per exact recording, not per provenance row.
SELECT bucket, COUNT(*) AS recordings FROM latest_reviews GROUP BY bucket ORDER BY bucket;

-- Like-for-like sampler/scheduler consistency, with song and genre retained.
SELECT title, genre, sampler, scheduler, COUNT(*) AS n,
       AVG(audio_quality) AS audio_quality, AVG(consistency) AS consistency,
       AVG(CASE WHEN bucket=5 THEN 1.0 ELSE 0.0 END) AS bucket5_fraction
FROM analytics_dataset
WHERE review_id IS NOT NULL AND review_inherited=0
GROUP BY title, genre, sampler, scheduler;

-- Generation efficiency: total imported compute time, not audio duration.
SELECT title, genre, dit, text_encoder, sampler, scheduler,
       COUNT(*) AS reviewed_recordings,
       SUM(CASE WHEN bucket=5 THEN 1 ELSE 0 END) AS bucket5_recordings,
       SUM(generation_duration)/3600.0 AS compute_hours,
       3600.0*SUM(CASE WHEN bucket=5 THEN 1 ELSE 0 END)/NULLIF(SUM(generation_duration),0) AS bucket5_per_compute_hour
FROM analytics_dataset
WHERE review_id IS NOT NULL AND review_inherited=0 AND generation_duration>0
GROUP BY title, genre, dit, text_encoder, sampler, scheduler;

-- Latest detector events vs human consistency. Automated scores remain separate.
SELECT e.type, COUNT(DISTINCT e.audio_id) AS recordings, AVG(r.consistency) AS mean_consistency
FROM audio_events e JOIN latest_reviews r ON r.audio_id=e.audio_id
WHERE e.analysis_id=(SELECT id FROM analyses WHERE audio_id=e.audio_id ORDER BY created_at DESC,rowid DESC LIMIT 1)
GROUP BY e.type;

-- Publishing intent with durable archive readiness handled by manifest consumers.
SELECT generation_id,title,genre,archive_path,yaml_archive_path,selected_at,external_reference
FROM analytics_dataset WHERE publishing_status='selected';

-- Inspect parameter fields not covered by the flattened common columns.
SELECT generation_id,json_extract(generation_metadata_json,'$.hardware') AS hardware,
       json_extract(generation_metadata_json,'$.caption') AS caption
FROM analytics_dataset;

-- Find provenance conflicts before treating settings as causally attributable.
SELECT audio_id,COUNT(*) AS provenance_records FROM generations GROUP BY audio_id HAVING COUNT(*)>1;
