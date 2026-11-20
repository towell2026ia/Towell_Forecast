"""Transactional local rolling-run journal; no operational-source writes.

Inputs/results/events are immutable and hashed. Only job status and explicit
Champion pointers can change. No tokens or passwords belong in these payloads.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid
import os

from .persistence import LocalPersistenceProvider, content_hash


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def decode(row):
    if row is None:
        return None
    if hashlib.sha256(row["payload"].encode()).hexdigest() != row["sha256"]:
        raise ValueError("rolling_integrity_failed")
    return json.loads(row["payload"])


class RollingWriterLease:
    """One process owns execution/recovery for the actual shared volume path."""
    def __init__(self, path):
        self.file = open(str(path) + ".rolling-writer.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.file.seek(0, 2) == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError("rolling_single_writer_required") from None

    def close(self):
        if not self.file.closed:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


class RollingPlanningStore:
    def __init__(self, persistence):
        if not isinstance(persistence, LocalPersistenceProvider):
            raise ValueError("rolling_local_persistence_required")
        self.persistence = persistence
        with persistence._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rolling_requests (
                  run_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                  request_hash TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                  UNIQUE(actor_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS rolling_status (
                  run_id TEXT PRIMARY KEY REFERENCES rolling_requests(run_id),
                  status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','READY','FAILED')),
                  error_code TEXT);
                CREATE TABLE IF NOT EXISTS rolling_runs (
                  run_id TEXT PRIMARY KEY REFERENCES rolling_requests(run_id),
                  payload TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rolling_champion_events (
                  id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                  request_hash TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                  UNIQUE(actor_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS rolling_champion_pointers (
                  scope_key TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES rolling_champion_events(id));
            """)
            for table in ("rolling_requests", "rolling_runs", "rolling_champion_events"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                        BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'rolling_immutable'); END""")

    def reserve(self, actor, key, request_hash, frozen):
        with self.persistence._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM rolling_requests WHERE actor_id=? AND idempotency_key=?", (actor, key)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ValueError("rolling_idempotency_conflict")
                return decode(old), False
            item = {**frozen, "run_id": str(uuid.uuid4()), "creator_id": actor, "created_at": now()}
            raw, digest = encode(item)
            db.execute("INSERT INTO rolling_requests VALUES(?,?,?,?,?,?)", (item["run_id"], actor, key, request_hash, raw, digest))
            db.execute("INSERT INTO rolling_status VALUES(?,'QUEUED',NULL)", (item["run_id"],))
            return item, True

    def interrupted(self):
        with self.persistence._connect() as db:
            db.execute("UPDATE rolling_status SET status='FAILED',error_code='PLANNING_INTERRUPTED' WHERE status IN ('QUEUED','RUNNING')")

    def request(self, run_id):
        with self.persistence._connect() as db:
            return decode(db.execute("SELECT * FROM rolling_requests WHERE run_id=?", (run_id,)).fetchone())

    def entries(self):
        with self.persistence._connect() as db:
            return [decode(row) for row in db.execute("SELECT * FROM rolling_requests ORDER BY rowid DESC")]

    def status(self, run_id):
        with self.persistence._connect() as db:
            row = db.execute("SELECT status,error_code FROM rolling_status WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def mark_running(self, run_id):
        with self.persistence._connect() as db:
            result = db.execute("UPDATE rolling_status SET status='RUNNING' WHERE run_id=? AND status='QUEUED'", (run_id,))
            return result.rowcount == 1

    def fail(self, run_id, code):
        with self.persistence._connect() as db:
            db.execute("UPDATE rolling_status SET status='FAILED',error_code=? WHERE run_id=? AND status IN ('QUEUED','RUNNING')", (code, run_id))

    def finish(self, run_id, payload):
        raw, digest = encode(payload)
        with self.persistence._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            status = db.execute("SELECT status FROM rolling_status WHERE run_id=?", (run_id,)).fetchone()
            if status is None or status["status"] != "RUNNING":
                raise ValueError("rolling_status_conflict")
            db.execute("INSERT INTO rolling_runs VALUES(?,?,?)", (run_id, raw, digest))
            db.execute("UPDATE rolling_status SET status='READY',error_code=NULL WHERE run_id=?", (run_id,))

    def run(self, run_id):
        with self.persistence._connect() as db:
            return decode(db.execute("SELECT * FROM rolling_runs WHERE run_id=?", (run_id,)).fetchone())

    def artifact(self, run_id):
        with self.persistence._connect() as db:
            row = db.execute("SELECT * FROM rolling_runs WHERE run_id=?", (run_id,)).fetchone()
            if decode(row) is None:
                raise ValueError("rolling_run_missing")
            return {"storage": "SQLITE_ROLLING_RUN", "encoding": "UTF8_CANONICAL_JSON", "sha256": row["sha256"]}

    @staticmethod
    def scope_key(run, pid):
        return content_hash({"run_id": run["run_id"], "source_context": run["source_context"],
                             "window_start": run["window_start"], "window_end": run["window_end"], "product_id": pid})

    def promote(self, actor, key, signature, event, pointers):
        with self.persistence._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM rolling_champion_events WHERE actor_id=? AND idempotency_key=?", (actor, key)).fetchone()
            if old:
                if old["request_hash"] != signature:
                    raise ValueError("rolling_idempotency_conflict")
                return decode(old)
            previous = {pid: (row["event_id"] if (row := db.execute("SELECT event_id FROM rolling_champion_pointers WHERE scope_key=?", (scope,)).fetchone()) else None)
                        for pid, scope in pointers.items()}
            result = {**event, "id": str(uuid.uuid4()), "previous_champion_ids": previous}
            raw, digest = encode(result)
            db.execute("INSERT INTO rolling_champion_events VALUES(?,?,?,?,?,?)", (result["id"], actor, key, signature, raw, digest))
            for scope in pointers.values():
                db.execute("INSERT INTO rolling_champion_pointers VALUES(?,?) ON CONFLICT(scope_key) DO UPDATE SET event_id=excluded.event_id", (scope, result["id"]))
            return result

    def champions(self, pointers):
        with self.persistence._connect() as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            result = {}
            for pid, key in pointers.items():
                row = db.execute("SELECT e.* FROM rolling_champion_pointers p JOIN rolling_champion_events e ON e.id=p.event_id WHERE p.scope_key=?", (key,)).fetchone()
                if row:
                    result[pid] = decode(row)
            return result
