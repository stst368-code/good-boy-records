"""Select catalogue inputs using latest saved reviewer assessments, read-only."""
from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3

from .catalogue import identifier, title_key


def select_by_rating(inputs, database, maximum, reviewer_id=None, cluster=None, style=None):
    if not 0 <= maximum <= 5:
        raise ValueError("Rating threshold must be between 0 and 5")
    path = Path(database).resolve(strict=True)
    aliases, identities = defaultdict(set), {}
    for index, item in enumerate(inputs):
        identities.setdefault((item.song_id, item.variant_id), []).append(index)
        for text in (item.path.stem, item.metadata.get("song_name", "")):
            if text:
                aliases[title_key(text)].add(index)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if reviewer_id and not conn.execute("SELECT 1 FROM reviewers WHERE id=?", (reviewer_id,)).fetchone():
            raise ValueError("Unknown reviewer ID")
        query = """SELECT g.id AS generation_id,s.title,r.id AS review_id,r.average,
            json_extract(m.parsed_json,'$.song_id') AS catalogue_song_id,
            json_extract(m.parsed_json,'$.variant_id') AS catalogue_variant_id,
            COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.cluster')),''),'Cluster not supplied') AS cluster,
            COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.style_primary')),''),NULLIF(trim(json_extract(m.parsed_json,'$.style')),''),
                NULLIF(trim(json_extract(m.parsed_json,'$.genre')),''),NULLIF(trim(json_extract(m.parsed_json,'$.intended_genre')),''),
                NULLIF(trim(json_extract(m.parsed_json,'$.cluster')),''),s.genre,'Genre not supplied') AS style
            FROM generations g JOIN songs s ON s.id=g.song_id
            JOIN generation_metadata m ON m.generation_id=g.id
            JOIN latest_reviewer_reviews r ON r.audio_id=g.audio_id
            WHERE (? IS NULL OR r.reviewer_id=?)"""
        assessments, ambiguous = defaultdict(dict), set()
        for row in conn.execute(query, (reviewer_id, reviewer_id)):
            if cluster and (row["cluster"] or "").strip().casefold() != cluster.strip().casefold():
                continue
            if style and (row["style"] or "").strip().casefold() != style.strip().casefold():
                continue
            song, variant = row["catalogue_song_id"], row["catalogue_variant_id"]
            if song is not None or variant is not None:
                try:
                    matches = identities.get((identifier(song), identifier(variant)), [])
                except ValueError:
                    matches = []  # Never replace an incomplete explicit identity with a title guess.
            else:
                matches = aliases.get(title_key(row["title"]), ())
            if len(matches) > 1:
                ambiguous.add(row["generation_id"])
                continue
            if matches:
                index = next(iter(matches))
                assessments[index][row["review_id"]] = float(row["average"])
    chosen, evidence = [], []
    for index, item in enumerate(inputs):
        reviews = assessments.get(index)
        if reviews:
            mean = sum(reviews.values()) / len(reviews)
            if mean <= maximum:
                chosen.append(item)
                evidence.append({"path": str(item.path), "mean_latest_rating": mean, "review_ids": sorted(reviews)})
    return chosen, {"maximum_mean_rating": maximum, "reviewer_id": reviewer_id,
                    "cluster": cluster, "style": style, "evidence": evidence,
                    "ambiguous_review_generations_skipped": sorted(ambiguous),
                    "unreviewed_handling": "excluded", "aggregation": "mean of distinct latest reviewer assessments"}
