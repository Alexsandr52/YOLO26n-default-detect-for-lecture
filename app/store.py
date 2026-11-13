import os
import sqlite3
import time
from pathlib import Path

DATA = Path(os.getenv('DATA_DIR', '/data'))
DB = DATA / 'jobs.sqlite3'
TTL = int(os.getenv('RESULT_TTL_SECONDS', '3600'))
MAX_QUEUE = int(os.getenv('MAX_QUEUE', '60'))


def connect():
    db = sqlite3.connect(DB, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    return db


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            input_path TEXT NOT NULL, output_path TEXT, created REAL NOT NULL,
            completed REAL, elapsed_ms REAL, frame_ms REAL, frames INTEGER,
            error TEXT, worker_pid INTEGER)''')
        if 'worker_pid' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
            db.execute('ALTER TABLE jobs ADD COLUMN worker_pid INTEGER')


def enqueue(job_id, kind, input_path):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'processing')").fetchone()[0]
        if count >= MAX_QUEUE:
            db.rollback()
            return False
        db.execute('INSERT INTO jobs (id,kind,status,input_path,created) VALUES (?,?,?,?,?)',
                   (job_id, kind, 'queued', str(input_path), time.time()))
        db.commit()
        return True


def claim(kind):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM jobs WHERE kind=? AND status='queued' ORDER BY created LIMIT 1", (kind,)).fetchone()
        if row:
            db.execute("UPDATE jobs SET status='processing',worker_pid=? WHERE id=?", (os.getpid(), row['id']))
        db.commit()
        return dict(row) if row else None


def get(job_id):
    with connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None


def finish(job_id, output, elapsed_ms, frame_ms=None, frames=None):
    with connect() as db:
        db.execute("UPDATE jobs SET status='done',output_path=?,elapsed_ms=?,frame_ms=?,frames=?,completed=? WHERE id=?",
                   (str(output), elapsed_ms, frame_ms, frames, time.time(), job_id))


def fail(job_id, message):
    with connect() as db:
        db.execute("UPDATE jobs SET status='error',error=?,completed=? WHERE id=?", (message, time.time(), job_id))


def recover():
    with connect() as db:
        db.execute("UPDATE jobs SET status='queued',worker_pid=NULL WHERE status='processing'")


def recover_worker(pid):
    with connect() as db:
        db.execute("UPDATE jobs SET status='queued',worker_pid=NULL WHERE status='processing' AND worker_pid=?", (pid,))


def cleanup():
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT id,input_path,output_path FROM jobs WHERE completed IS NOT NULL AND completed < ?', (time.time()-TTL,)).fetchall()
        for row in rows:
            for path in (row['input_path'], row['output_path']):
                if path:
                    Path(path).unlink(missing_ok=True)
            # A failed or interrupted encoder may also have left a partial output.
            for path in DATA.glob(f"{row['id']}-result.*"):
                path.unlink(missing_ok=True)
            db.execute('DELETE FROM jobs WHERE id=?', (row['id'],))
        db.commit()
