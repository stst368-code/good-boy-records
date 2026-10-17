"""Migration contracts tested with populated copies of the existing review schema."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_98_Database"))
sys.path.insert(0, str(ROOT / "_03_Review" / "_Generation_Review"))

from gbr_database.migration import connect_readonly, file_hash, inspect_sources, migrate
from review.config import Config
from review.db import Database
from review.__main__ import ProcessLock

PARAMS = {"encoder_cfg": 1.5, "encoder_seed": "9007199254740993", "top_k": 50,
          "sampler": "euler", "scheduler": "simple", "sampler_cfg": 2.0,
          "sampler_seed": "18446744073709551615", "sampler_steps": 32}
RAW_YAML = b'# keep this comment\r\ntitle: "Fixture"\nlyrics: |\r\n  first line\r\n\r\n  second line\nunknown: [1, 2]\r\n'


def insert(conn, table, **values):
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.generation = self.folder / "generation.sqlite3"
        self.review = self.folder / "review.sqlite"
        self.output = self.folder / "shared.sqlite3"
        self.make_generation()
        self.make_review()

    def make_generation(self):
        with closing(sqlite3.connect(self.generation)) as conn:
            conn.executescript('''
                CREATE TABLE jobs(job_id TEXT PRIMARY KEY, output_stem TEXT, params_json TEXT,
                  status TEXT, source_path TEXT, future_blob BLOB);
                CREATE TABLE timings(timing_id TEXT PRIMARY KEY, job_id TEXT, worker_id TEXT,
                  gpu_name TEXT, stage TEXT, success INTEGER, duration_seconds REAL,
                  billable INTEGER, estimated_cost REAL, cost_currency TEXT);
                CREATE TABLE counters(title_key TEXT PRIMARY KEY, last_value INTEGER);
            ''')
            states = ["completed", "planned", "sampling", "withdrawn", "withdrawn", "completed"]
            stems = ["take-full", "take-missing", "take-conflict", "take-ambiguous", "take-ambiguous", "legacy-only"]
            for n, (state, stem) in enumerate(zip(states, stems)):
                insert(conn, "jobs", job_id=f"j{n}", output_stem=stem, params_json=json.dumps(PARAMS),
                       status=state, source_path=r"C:\original\song.yaml", future_blob=b"\x00\xff\n")
            for n, (stage, billable, cost, currency, success) in enumerate([
                ("conditioning_total", 1, 0.1, "USD", 1),
                ("sampling_total", 1, None, "USD", 0),
                ("full_generation_total", 0, 99.0, "USD", 1),
                ("sampling_total", 1, 0.2, "GBP", 1),
            ]):
                insert(conn, "timings", timing_id=f"t{n}", job_id="j0", worker_id="address",
                       gpu_name="GPU fixture", stage=stage, success=success, duration_seconds=12.0,
                       billable=billable, estimated_cost=cost, cost_currency=currency)
            insert(conn, "counters", title_key="fixture", last_value=42)
            conn.commit()

    def make_review(self):
        Database(self.review)
        with closing(sqlite3.connect(self.review)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            insert(conn, "songs", id="song", title="Fixture", genre="Test", cluster="Cluster")
            insert(conn, "audio", id="audio", pcm_sha256="hash", duration=120.0, sample_rate=44100,
                   channels=2, created_at="2026-10-17T00:00:00Z", analysis_state="ready")
            for n, stem in enumerate(["take-full", "take-missing", "take-conflict", "take-ambiguous", "review-only"]):
                params = dict(PARAMS)
                if stem == "take-missing":
                    params.pop("encoder_cfg")
                if stem == "take-conflict":
                    params["encoder_seed"] = "9007199254740992"
                insert(conn, "generations", id=f"g{n}", audio_id="audio", song_id="song", metadata_key=f"m{n}",
                       original_filename="C:\\outputs\\" + stem + ".flac", original_directory=r"C:\outputs",
                       created_at="2026-10-17T00:00:00Z")
                insert(conn, "generation_metadata", generation_id=f"g{n}", parsed_json=json.dumps(params),
                       embedded_json="{}", metadata_status="valid", raw_yaml_bytes=RAW_YAML,
                       raw_yaml=RAW_YAML.decode("utf-8"))
            reviewer = conn.execute("SELECT id FROM reviewers LIMIT 1").fetchone()[0]
            insert(conn, "assessments", id="assessment", audio_id="audio", reviewer_id=reviewer, created_at="then")
            insert(conn, "reviews", id="review", generation_id="g0", audio_id="audio", audio_quality=5,
                   composition=4, consistency=3, realism=2, average=3.5, bucket=3, created_at="then",
                   mode="full", revision=1, session_id="main", listening_json="{}", reviewer_id=reviewer,
                   assessment_id="assessment", assessment_revision=1, reason="Keep\nthis reason", flags_json="{}")
            insert(conn, "review_history", audio_id="audio", generation_id="g0", action="reviewed",
                   review_id="review", details_json="{}", created_at="then", reviewer_id=reviewer,
                   assessment_id="assessment")
            insert(conn, "bookmarks", generation_id="g0", reviewer_id=reviewer, created_at="then", updated_at="then")
            insert(conn, "publishing_selection", generation_id="g0", status="selected", updated_at="then")
            conn.commit()

    def test_populated_import_preserves_sources_and_review_identity(self):
        before = {path: file_hash(path) for path in (self.generation, self.review)}
        report = migrate(self.generation, self.review, self.output)
        self.assertEqual(before, {path: file_hash(path) for path in before})
        self.assertEqual(report["review_link_evidence"], {
            "parameters_match": 1, "filename_only": 1, "conflict": 1})
        self.assertEqual(report["review_generations_without_filename_candidate"], 2)
        self.assertEqual(report["excluded_legacy_jobs"], {"withdrawn": 2, "without_usable_review_match": 2})
        with closing(connect_readonly(self.output)) as conn:
            self.assertEqual(conn.execute("SELECT raw_yaml_bytes FROM generation_metadata LIMIT 1").fetchone()[0], RAW_YAML)
            self.assertEqual(tuple(conn.execute("SELECT id,reason FROM reviews").fetchone()), ("review", "Keep\nthis reason"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT status FROM publishing_selection").fetchone()[0], "selected")
            self.assertEqual(conn.execute("SELECT future_blob FROM legacy_generation_jobs LIMIT 1").fetchone()[0], b"\x00\xff\n")
            self.assertEqual(dict(conn.execute("SELECT status,COUNT(*) FROM legacy_generation_jobs GROUP BY status")),
                             {"completed": 1, "planned": 1})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0], 5)
            for table in ("generation_runs", "generation_batches", "generation_attempts", "generation_resource_measurements"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_costs_remain_partial_and_currencies_are_separate(self):
        migrate(self.generation, self.review, self.output)
        with closing(connect_readonly(self.output)) as conn:
            costs = {row["cost_currency"]: dict(row) for row in conn.execute("SELECT * FROM legacy_generation_costs")}
            self.assertAlmostEqual(costs["USD"]["known_estimated_cost"], 0.1)
            self.assertEqual(costs["USD"]["missing_cost_events"], 1)
            self.assertIsNone(costs["USD"]["known_failed_cost"])
            self.assertAlmostEqual(costs["GBP"]["known_estimated_cost"], 0.2)
            total = conn.execute("SELECT * FROM legacy_stage_performance WHERE stage='full_generation_total'").fetchone()
            self.assertEqual(total["total_events"], 1)
            self.assertIsNone(total["known_estimated_cost"])

    def test_backup_reads_committed_live_wal(self):
        with closing(sqlite3.connect(self.generation)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO counters VALUES('wal-only',7)")
            writer.commit()
            self.assertTrue(Path(str(self.generation) + "-wal").stat().st_size > 0)
            report = migrate(self.generation, self.review, self.output)
            self.assertEqual(report["generation_tables"]["counters"], 2)
            with closing(connect_readonly(self.output)) as conn:
                self.assertEqual(conn.execute("SELECT last_value FROM legacy_generation_counters WHERE title_key='wal-only'").fetchone()[0], 7)

    def test_existing_destination_is_never_changed(self):
        self.output.write_bytes(b"keep existing")
        with self.assertRaises(FileExistsError):
            migrate(self.generation, self.review, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep existing")

    def test_withdrawn_jobs_and_their_timings_are_excluded_even_with_bad_parameters(self):
        with closing(sqlite3.connect(self.generation)) as conn:
            conn.execute("UPDATE jobs SET params_json='invalid-json' WHERE status='withdrawn'")
            conn.execute("INSERT INTO timings VALUES('withdrawn-timing','j3','old','GPU','sampling_total',1,12,1,0.5,'USD')")
            conn.commit()
        report = migrate(self.generation, self.review, self.output)
        self.assertEqual(report['excluded_legacy_timing_events'], 1)
        with closing(connect_readonly(self.output)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM legacy_generation_jobs WHERE status='withdrawn'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM legacy_generation_timings").fetchone()[0], 4)

    def test_source_cannot_be_destination(self):
        before = file_hash(self.review)
        with self.assertRaises(ValueError):
            migrate(self.generation, self.review, self.review)
        self.assertEqual(file_hash(self.review), before)

    def test_corrupt_parameters_are_rejected_without_publishing(self):
        for value in ("bad-json", "[]", "null", None):
            with self.subTest(value=value):
                with closing(sqlite3.connect(self.generation)) as conn:
                    conn.execute("UPDATE jobs SET params_json=? WHERE job_id='j0'", (value,))
                    conn.commit()
                with self.assertRaisesRegex(ValueError, "params_json"):
                    migrate(self.generation, self.review, self.output)
                self.assertFalse(self.output.exists())
                self.assertEqual(list(self.folder.glob("gbr-migration-*")), [])

    def test_orphan_timings_are_rejected(self):
        with closing(sqlite3.connect(self.generation)) as conn:
            conn.execute("UPDATE timings SET job_id='absent' WHERE timing_id='t0'")
            conn.commit()
        with self.assertRaisesRegex(ValueError, "missing jobs"):
            migrate(self.generation, self.review, self.output)
        self.assertFalse(self.output.exists())

    def test_shared_database_is_not_imported_again_as_review(self):
        migrate(self.generation, self.review, self.output)
        with self.assertRaisesRegex(ValueError, "already a shared"):
            migrate(self.generation, self.output, self.folder / "second.sqlite3")

    def test_report_does_not_modify_any_database(self):
        before = {path: file_hash(path) for path in (self.generation, self.review)}
        report = inspect_sources(self.generation, self.review)
        self.assertEqual(report["new_runnable_jobs_created"], 0)
        self.assertEqual(before, {path: file_hash(path) for path in before})

    def test_existing_report_is_refused_before_migration(self):
        report = self.folder / "report.json"
        report.write_text("keep")
        result = subprocess.run([sys.executable, str(ROOT / "_98_Database" / "migrate_databases.py"),
            "migrate", "--generation", str(self.generation), "--review", str(self.review),
            "--output", str(self.output), "--report", str(report)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(report.read_text(), "keep")


class ConfigurationTests(unittest.TestCase):
    def test_second_review_process_cannot_share_database_or_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(root / "config.json")
            config.values["database_path"] = str(root / "shared.sqlite3")
            config.save()
            code = "import sys; sys.path.insert(0,sys.argv[1]); from review.__main__ import main; main(sys.argv[2:])"
            for path in (config.data / "application.lock", config.database_lock):
                with self.subTest(path=path):
                    lock = ProcessLock(path)
                    try:
                        result = subprocess.run([sys.executable, "-c", code,
                            str(ROOT / "_03_Review" / "_Generation_Review"),
                            "--config", str(config.path), "scan"], capture_output=True, text=True)
                    finally:
                        lock.close()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("already using this database or data directory", result.stderr)
                    # If acquiring the second lock failed, the first was released.
                    acquired = ProcessLock(config.data / "application.lock")
                    acquired.close()

    def test_legacy_and_shared_paths_share_database_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = Config(root / "review" / "config.json")
            self.assertEqual(original.database, original.data / "review.sqlite")
            original.values["database_path"] = "../../shared.sqlite3"
            other = Config(root / "other" / "config.json")
            other.values["database_path"] = str(original.database)
            self.assertEqual(original.database, other.database)
            self.assertEqual(original.database_lock, other.database_lock)
            self.assertEqual(original.data, root / "review" / "data")

    def test_shared_launcher_refuses_unmigrated_database(self):
        spec = importlib.util.spec_from_file_location("run_gbr", ROOT / "_97_App" / "run_gbr.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.sqlite3"
            sqlite3.connect(path).close()
            with self.assertRaises(sqlite3.OperationalError):
                module.validate_database(path)
            self.assertFalse((Path(directory) / "missing.sqlite3").exists())
            with self.assertRaises(FileNotFoundError):
                module.validate_database(Path(directory) / "missing.sqlite3")


if __name__ == "__main__":
    unittest.main()
