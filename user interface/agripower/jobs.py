"""Shared job store for long-running background work.

Backs the Optimise flow's async "start a job, come back later" pattern
(started on New analysis, checked on Results). It is deliberately generic
(a `kind` string per job, a per-kind runner function), so a future second
kind of long-running job could reuse the same table rather than a second
job system.

Also carries a live best-feasible/best-bound readout for whichever solve
is currently running, fed by Gurobi's own MIP callback (see
optimiser._make_gurobi_progress_callback) via `report_live_progress()` -
separate from the coarser done/total-solves progress used for the ETA,
since it updates far more often and isn't meaningful to keep a full
history of.

Backed by sqlite (stdlib, no new dependency) so a job survives the farmer
closing the tab: starting a job returns a short PIN immediately, and the
farmer can come back - even from a different browser tab - and look it up by
that PIN later. A restart of the app itself does not resume a job; it cancels it.

Only one job may be queued or running at a time (create_job refuses a second
with JobAlreadyActive). If the app is restarted, anything queued or running is
marked "cancelled" instead of being re-run, so the queue is always clear after
a restart; a heartbeat written by the worker thread is how that is detected.

A single dedicated worker thread runs the job, rather than one thread per job. current_code's own modules hold the currently
active farm's numbers in *module*-level globals (see optimiser.py's "reload
dance" docstring); two solves genuinely running at once would stomp on each
other's inputs mid-solve. Serialising every job through one worker is what
makes concurrent job submissions safe without changing current_code or
optimiser.py at all.
"""

import json
import os
import pickle
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path

# AGRIPOWER_JOBS_DB points the store at a different file (used to test a second
# copy of the app without it sharing, and so disturbing, the real job store).
DB_PATH = Path(os.environ.get("AGRIPOWER_JOBS_DB") or Path(__file__).resolve().parents[1] / "data" / "jobs.db")

# Shared state lives on `sys`, not as plain module globals. Streamlit's file
# watcher can re-import this file while the app runs; a fresh copy of these
# globals would start a second worker thread and forget which runner is
# registered. `sys` itself is never reloaded, so one worker, one lock and one
# runner table exist per process however many times this file is imported.
if not hasattr(sys, "_agripower_jobs_state"):
    sys._agripower_jobs_state = {
        "write_lock": threading.Lock(),
        "worker_lock": threading.Lock(),
        "worker_started": False,
        "runners": {},  # kind -> callable(params, on_step, on_progress) -> result dict
    }
_STATE = sys._agripower_jobs_state
_write_lock = _STATE["write_lock"]
_worker_lock = _STATE["worker_lock"]
_RUNNERS = _STATE["runners"]

# The worker's own thread writes a heartbeat every few seconds. If the app is
# stopped or restarted, the heartbeat stops, and any analysis that was queued or
# running is then marked "cancelled" instead of being left looking stuck (or
# silently re-run). Only one analysis may be queued or running at a time.
HEARTBEAT_INTERVAL_SECONDS = 2
WORKER_DEAD_AFTER_SECONDS = 20
RESTART_MESSAGE = (
    "This analysis was stopped because the app was restarted before it finished. Start it again from "
    "New analysis."
)


class JobAlreadyActive(RuntimeError):
    """Raised when a job is started while another is still queued or running."""

    def __init__(self, active_job: dict):
        super().__init__(f"Analysis {active_job['pin']} is still {active_job['status']}.")
        self.active_job = active_job


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                pin TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                progress_done INTEGER DEFAULT 0,
                progress_total INTEGER DEFAULT 0,
                step_times TEXT DEFAULT '[]',
                live_best REAL,
                live_bound REAL,
                live_metric TEXT,
                params BLOB,
                result BLOB,
                error TEXT,
                email TEXT,
                email_status TEXT
            )
            """
        )
        # A jobs.db created before live_best/live_bound existed won't have
        # picked them up from CREATE TABLE IF NOT EXISTS - add them if
        # missing, rather than requiring the sqlite file to be deleted.
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value REAL)")
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for column, column_type in (("live_best", "REAL"), ("live_bound", "REAL"), ("live_metric", "TEXT")):
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {column_type}")


_init_db()


# ----------------------------------------------------------------------------
# Public API: create / read jobs
# ----------------------------------------------------------------------------

def register_runner(kind: str, run_fn) -> None:
    """Register the function that actually executes one job of this `kind`.

    `run_fn(params: dict, on_step: callable(done, total),
    on_progress: callable(best, bound, metric)) -> result: dict`. Called
    once, at import time, from whichever module knows how to run that kind
    of job - e.g. views/new_analysis.py registers "optimise".
    """
    _RUNNERS[kind] = run_fn


def create_job(kind: str, params: dict, email: str = "") -> dict:
    """Queue a job and return {"job_id", "pin"} immediately.

    Only one job may be queued or running at a time: if one already is,
    JobAlreadyActive is raised (carrying that job) and nothing is created.

    The actual work happens later, off the calling thread, on the shared
    worker (started here on first use). `params` and the eventual result
    are pickled into the database, so they can be any plain Python /
    pandas object - not just JSON-safe values.
    """
    _ensure_worker_running()
    job_id = secrets.token_hex(8)
    with _write_lock, _connect() as conn:
        conn.row_factory = sqlite3.Row
        active = conn.execute(
            "SELECT pin, status, progress_done, progress_total, started_at FROM jobs "
            "WHERE status IN ('queued', 'running') ORDER BY created_at LIMIT 1"
        ).fetchone()
        if active is not None:
            raise JobAlreadyActive(dict(active))
        pin = _new_pin(conn)
        conn.execute(
            "INSERT INTO jobs (job_id, pin, kind, status, created_at, params, email) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, pin, kind, time.time(), pickle.dumps(params), email),
        )
    return {"job_id": job_id, "pin": pin}


def active_job() -> dict:
    """The one job that is queued or running, as {"pin", "status",
    "progress_done", "progress_total", "started_at"}, or None. First clears out
    anything left behind by an app that has stopped (see _cancel_if_worker_dead)."""
    _cancel_if_worker_dead()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pin, status, progress_done, progress_total, started_at FROM jobs "
            "WHERE status IN ('queued', 'running') ORDER BY created_at LIMIT 1"
        ).fetchone()
    return dict(row) if row is not None else None


def _beat() -> None:
    with _write_lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('worker_heartbeat', ?)", (time.time(),))


def worker_is_alive() -> bool:
    """Whether some process's worker has checked in recently."""
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'worker_heartbeat'").fetchone()
    return row is not None and (time.time() - row[0]) < WORKER_DEAD_AFTER_SECONDS


def _cancel_if_worker_dead() -> None:
    """If a job is queued or running but no worker has checked in lately, the
    app that owned it has stopped or been restarted: mark it cancelled, so the
    queue is clear and its PIN says what happened. A second process that merely
    imports this file (a test, say) does not cancel a live worker's job, because
    that worker's heartbeat is fresh."""
    if worker_is_alive():
        return
    with _write_lock, _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'cancelled', finished_at = ?, error = ? "
            "WHERE status IN ('queued', 'running')",
            (time.time(), RESTART_MESSAGE),
        )


def get_job(job_id: str = None, pin: str = None) -> dict:
    """Look up a job by job_id or by its short PIN. None if not found."""
    if not job_id and not pin:
        return None
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if job_id:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM jobs WHERE pin = ?", (pin.strip(),)).fetchone()
    if row is not None and row["status"] in ("queued", "running") and not worker_is_alive():
        # The app that owned this job has stopped: cancel it and read it again.
        _cancel_if_worker_dead()
        return get_job(job_id=row["job_id"])
    return _row_to_dict(row) if row is not None else None


def _new_pin(conn) -> str:
    """A short, easy-to-note-down code - 6 digits, unique across every job
    ever created (not just currently-active ones), so an old PIN can never
    later get reused and point at a different job."""
    while True:
        candidate = f"{secrets.randbelow(1_000_000):06d}"
        if conn.execute("SELECT 1 FROM jobs WHERE pin = ?", (candidate,)).fetchone() is None:
            return candidate


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["params"] = pickle.loads(data["params"]) if data["params"] else {}
    data["result"] = pickle.loads(data["result"]) if data["result"] else None
    data["step_times"] = json.loads(data["step_times"]) if data["step_times"] else []
    return data


def _update_job(job_id: str, **fields) -> None:
    if "params" in fields:
        fields["params"] = pickle.dumps(fields["params"])
    if "result" in fields:
        fields["result"] = pickle.dumps(fields["result"])
    if "step_times" in fields:
        fields["step_times"] = json.dumps(fields["step_times"])
    columns = ", ".join(f"{name} = ?" for name in fields)
    values = list(fields.values()) + [job_id]
    with _write_lock, _connect() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE job_id = ?", values)


def report_progress(job_id: str, done: int, total: int) -> None:
    """Called from the worker thread while a job runs. Appends a timestamp
    per completed step so callers can compute an average-step-time ETA the
    same way the old synchronous flow did."""
    job = get_job(job_id=job_id)
    if job is None:
        return
    step_times = job["step_times"] + [time.time()]
    _update_job(job_id, progress_done=done, progress_total=total, step_times=step_times)


def report_live_progress(job_id: str, best: float, bound: float, metric: str) -> None:
    """Called from inside Gurobi's own MIP callback (via
    optimiser._make_gurobi_progress_callback), potentially many times a
    second while one solve is mid-search. Overwrites the previous
    best/bound pair rather than appending, unlike report_progress - there
    is no useful history to keep, only "where is the current solve at
    right now". `metric` is "cost" or "emis", saying which objective (and
    unit) best/bound are in."""
    _update_job(job_id, live_best=best, live_bound=bound, live_metric=metric)


# ----------------------------------------------------------------------------
# The single worker thread
# ----------------------------------------------------------------------------

def _ensure_worker_running() -> None:
    with _worker_lock:
        if _STATE["worker_started"]:
            return
        _STATE["worker_started"] = True
        # Anything still queued or running was left by an earlier process
        # that has stopped (the app was restarted), so clear it rather than
        # re-run it or leave it looking stuck. If that process had only just
        # stopped its last heartbeat still looks fresh, so wait long enough
        # to see whether the heartbeat is still changing before deciding.
        if not _another_worker_is_alive():
            with _write_lock, _connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status = 'cancelled', finished_at = ?, error = ? "
                    "WHERE status IN ('queued', 'running')",
                    (time.time(), RESTART_MESSAGE),
                )
        _beat()
        threading.Thread(target=_heartbeat_loop, daemon=True).start()
        threading.Thread(target=_worker_loop, daemon=True).start()


def _another_worker_is_alive() -> bool:
    """True only if some other process's worker is still checking in. Looks at
    the heartbeat twice, a little over one interval apart, because a heartbeat
    that is merely recent could belong to a process that has just died."""
    with _connect() as conn:
        first = conn.execute("SELECT value FROM meta WHERE key = 'worker_heartbeat'").fetchone()
    if first is None or (time.time() - first[0]) >= WORKER_DEAD_AFTER_SECONDS:
        return False
    time.sleep(HEARTBEAT_INTERVAL_SECONDS * 2 + 0.5)
    with _connect() as conn:
        second = conn.execute("SELECT value FROM meta WHERE key = 'worker_heartbeat'").fetchone()
    return second is not None and second[0] > first[0]


def _heartbeat_loop() -> None:
    while True:
        try:
            _beat()
        except Exception:  # noqa: BLE001 - a missed beat must never stop the loop
            pass
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def _worker_loop() -> None:
    while True:
        job = _claim_next_job()
        if job is None:
            time.sleep(1)
            continue
        _run_job(job)


def _claim_next_job():
    with _write_lock, _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        job = _row_to_dict(row)
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
            (time.time(), job["job_id"]),
        )
    return job


def _run_job(job: dict) -> None:
    job_id = job["job_id"]
    run_fn = _RUNNERS.get(job["kind"])
    if run_fn is None:
        _update_job(
            job_id, status="error", finished_at=time.time(),
            error=f"No runner registered for job kind {job['kind']!r}.",
        )
        return

    def on_step(done, total):
        report_progress(job_id, done, total)

    def on_progress(best, bound, metric):
        report_live_progress(job_id, best, bound, metric)

    try:
        result = run_fn(job["params"], on_step, on_progress)
    except Exception as error:
        _update_job(
            job_id, status="error", finished_at=time.time(),
            error=f"{error}\n\n{traceback.format_exc()}",
        )
        _notify_email(job_id)
        return

    _update_job(job_id, status="done", finished_at=time.time(), result=result)
    _notify_email(job_id)


# ----------------------------------------------------------------------------
# Optional email notification
# ----------------------------------------------------------------------------

def _notify_email(job_id: str) -> None:
    job = get_job(job_id=job_id)
    if job is None or not job.get("email"):
        return
    status = _send_email_notification(job["email"], job)
    _update_job(job_id, email_status=status)


def _send_email_notification(email: str, job: dict) -> str:
    """Send a plain "your job is ready" notification via SMTP settings read
    from the environment. Gracefully no-ops (and records exactly why) if
    unconfigured - this is a laptop-run MVP, not a mail server, so most
    farmers won't have SMTP set up, and that must never fail the solve
    itself."""
    host = os.environ.get("AGRIPOWER_SMTP_HOST")
    if not host:
        return "not sent - no SMTP configured (set AGRIPOWER_SMTP_HOST to enable)"

    import smtplib
    from email.message import EmailMessage

    port = int(os.environ.get("AGRIPOWER_SMTP_PORT", "587"))
    username = os.environ.get("AGRIPOWER_SMTP_USER", "")
    password = os.environ.get("AGRIPOWER_SMTP_PASSWORD", "")
    sender = os.environ.get("AGRIPOWER_SMTP_FROM", username or "agripower@localhost")

    message = EmailMessage()
    message["Subject"] = "Your AgriPower optimisation result is ready"
    message["From"] = sender
    message["To"] = email
    status_word = "finished" if job["status"] == "done" else "failed"
    message.set_content(
        f"Your AgriPower analysis (PIN {job['pin']}) has {status_word}.\n\n"
        "Open AgriPower, go to the Results page, and enter this PIN to see it."
    )

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if username:
                server.login(username, password)
            server.send_message(message)
        return "sent"
    except Exception as error:
        return f"not sent - {error}"
