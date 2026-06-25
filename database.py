import sqlite3
import json
from datetime import datetime

DATABASE_PATH = "tasks.db"


def get_connection():
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    """Create all tables if they don't already exist. Safe to call on every request."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            persona TEXT NOT NULL,
            gmail_connected INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            deadline TEXT,
            priority TEXT DEFAULT 'medium',
            estimated_hours REAL DEFAULT 1.0,
            status TEXT DEFAULT 'todo',
            subtasks TEXT DEFAULT '[]',
            source TEXT DEFAULT 'manual',
            reasoning TEXT,
            pressure_score REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS email_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            scanned_at TEXT DEFAULT (datetime('now')),
            emails_processed INTEGER DEFAULT 0,
            tasks_created INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS focus_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER,
            started_at TEXT DEFAULT (datetime('now')),
            ended_at TEXT,
            duration_minutes REAL,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (task_id) REFERENCES tasks (id)
        );
    """)

    # Migrate existing tasks table to add pressure_score if it doesn't exist.
    # This handles databases created before this column was added.
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN pressure_score REAL DEFAULT 0.0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists — safe to ignore

    conn.commit()
    conn.close()


# ── USER FUNCTIONS ──────────────────────────────────────────────────────────

def create_user(name, persona):
    """Insert a new user and return their ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (name, persona) VALUES (?, ?)",
        (name, persona)
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return user_id


def get_user(user_id):
    """Fetch a user by ID. Returns a dict or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def set_gmail_connected(user_id, connected: bool):
    """Mark whether the user has connected their Gmail account."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET gmail_connected = ? WHERE id = ?",
        (1 if connected else 0, user_id)
    )
    conn.commit()
    conn.close()


# ── TASK FUNCTIONS ───────────────────────────────────────────────────────────

def create_task(user_id, title, description="", deadline=None,
                priority="medium", estimated_hours=1.0,
                subtasks=None, source="manual", reasoning=""):
    """Insert a new task, compute its pressure score, and return its ID."""
    pressure = _compute_pressure_score(deadline, priority)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO tasks
           (user_id, title, description, deadline, priority,
            estimated_hours, status, subtasks, source, reasoning, pressure_score)
           VALUES (?, ?, ?, ?, ?, ?, 'todo', ?, ?, ?, ?)""",
        (
            user_id, title, description, deadline, priority,
            estimated_hours,
            json.dumps(subtasks or []),
            source, reasoning, pressure
        )
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return task_id


def get_all_tasks(user_id):
    """Return all tasks for a user, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return _parse_tasks(rows)


def get_pending_tasks(user_id):
    """Return all non-completed tasks ordered by deadline ascending."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM tasks
           WHERE user_id = ? AND status != 'done'
           ORDER BY deadline ASC""",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return _parse_tasks(rows)


def get_at_risk_tasks(user_id):
    """Return tasks that are overdue or due within 24 hours and not done."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM tasks
           WHERE user_id = ?
             AND status != 'done'
             AND deadline IS NOT NULL
             AND deadline <= datetime('now', '+1 day')
           ORDER BY deadline ASC""",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return _parse_tasks(rows)


def get_tasks_by_source(user_id, source):
    """Return all tasks for a user filtered by source (manual, email, voice, accountability)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM tasks WHERE user_id = ? AND source = ? ORDER BY created_at DESC",
        (user_id, source)
    )
    rows = cursor.fetchall()
    conn.close()
    return _parse_tasks(rows)


def update_task_status(task_id, user_id, status):
    """Update a task's status. Sets completed_at if status is 'done'."""
    conn = get_connection()
    cursor = conn.cursor()
    completed_at = datetime.now().isoformat() if status == "done" else None
    cursor.execute(
        """UPDATE tasks
           SET status = ?, completed_at = ?
           WHERE id = ? AND user_id = ?""",
        (status, completed_at, task_id, user_id)
    )
    conn.commit()
    conn.close()


def update_pressure_scores(user_id):
    """
    Recompute and update pressure scores for all pending tasks of a user.
    Call this periodically (e.g. on dashboard load) to keep scores fresh.
    """
    tasks = get_pending_tasks(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    for task in tasks:
        score = _compute_pressure_score(task["deadline"], task["priority"])
        cursor.execute(
            "UPDATE tasks SET pressure_score = ? WHERE id = ?",
            (score, task["id"])
        )
    conn.commit()
    conn.close()


def delete_task(task_id, user_id):
    """Delete a task by ID, scoped to the user for safety."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, user_id)
    )
    conn.commit()
    conn.close()


# ── EMAIL SCAN FUNCTIONS ─────────────────────────────────────────────────────

def log_email_scan(user_id, emails_processed, tasks_created):
    """Record a Gmail scan event."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO email_scans (user_id, emails_processed, tasks_created)
           VALUES (?, ?, ?)""",
        (user_id, emails_processed, tasks_created)
    )
    conn.commit()
    conn.close()


def get_last_email_scan(user_id):
    """Return the most recent email scan for a user, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM email_scans
           WHERE user_id = ?
           ORDER BY scanned_at DESC
           LIMIT 1""",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ── CHAT FUNCTIONS ───────────────────────────────────────────────────────────

def save_chat_message(user_id, role, message):
    """Save a single chat message (role: 'user' or 'assistant')."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (user_id, role, message) VALUES (?, ?, ?)",
        (user_id, role, message)
    )
    conn.commit()
    conn.close()


def get_chat_history(user_id, limit=50):
    """Return the last N chat messages for a user, oldest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT role, message, created_at FROM chat_history
           WHERE user_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


def clear_chat_history(user_id):
    """Delete all chat history for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ── FOCUS SESSION FUNCTIONS ──────────────────────────────────────────────────

def start_focus_session(user_id, task_id=None):
    """
    Start a new focus (Pomodoro) session for the user.
    Optionally linked to a specific task. Returns the session ID.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO focus_sessions (user_id, task_id) VALUES (?, ?)",
        (user_id, task_id)
    )
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return session_id


def end_focus_session(session_id, user_id, completed=True):
    """
    End a focus session. Calculates duration from started_at to now.
    Sets completed flag and duration_minutes. Returns duration in minutes.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT started_at FROM focus_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    started_at = datetime.fromisoformat(row["started_at"])
    ended_at = datetime.now()
    duration_minutes = round((ended_at - started_at).total_seconds() / 60, 2)

    cursor.execute(
        """UPDATE focus_sessions
           SET ended_at = ?, duration_minutes = ?, completed = ?
           WHERE id = ? AND user_id = ?""",
        (ended_at.isoformat(), duration_minutes, 1 if completed else 0, session_id, user_id)
    )
    conn.commit()
    conn.close()
    return duration_minutes


def get_focus_sessions(user_id, limit=20):
    """Return the most recent focus sessions for a user, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT fs.*, t.title as task_title
           FROM focus_sessions fs
           LEFT JOIN tasks t ON fs.task_id = t.id
           WHERE fs.user_id = ?
           ORDER BY fs.started_at DESC
           LIMIT ?""",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_active_focus_session(user_id):
    """Return the current in-progress focus session, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM focus_sessions
           WHERE user_id = ? AND ended_at IS NULL
           ORDER BY started_at DESC
           LIMIT 1""",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ── ANALYTICS FUNCTIONS ──────────────────────────────────────────────────────

def get_analytics(user_id):
    """
    Compute and return a full analytics dict for the user.
    Includes task completion rate, missed deadlines, focus time,
    and per-priority breakdowns.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Total tasks
    cursor.execute("SELECT COUNT(*) as count FROM tasks WHERE user_id = ?", (user_id,))
    total_tasks = cursor.fetchone()["count"]

    # Completed tasks
    cursor.execute(
        "SELECT COUNT(*) as count FROM tasks WHERE user_id = ? AND status = 'done'",
        (user_id,)
    )
    completed_tasks = cursor.fetchone()["count"]

    # Missed deadlines — tasks past deadline that are NOT done
    cursor.execute(
        """SELECT COUNT(*) as count FROM tasks
           WHERE user_id = ?
             AND status != 'done'
             AND deadline IS NOT NULL
             AND deadline < datetime('now')""",
        (user_id,)
    )
    missed_deadlines = cursor.fetchone()["count"]

    # Completed on time — done before or at deadline
    cursor.execute(
        """SELECT COUNT(*) as count FROM tasks
           WHERE user_id = ?
             AND status = 'done'
             AND deadline IS NOT NULL
             AND completed_at <= deadline""",
        (user_id,)
    )
    completed_on_time = cursor.fetchone()["count"]

    # Total focus time (sum of all completed sessions)
    cursor.execute(
        """SELECT COALESCE(SUM(duration_minutes), 0) as total
           FROM focus_sessions
           WHERE user_id = ? AND completed = 1""",
        (user_id,)
    )
    total_focus_minutes = cursor.fetchone()["total"]

    # Number of focus sessions
    cursor.execute(
        "SELECT COUNT(*) as count FROM focus_sessions WHERE user_id = ? AND completed = 1",
        (user_id,)
    )
    total_sessions = cursor.fetchone()["count"]

    # Tasks by priority
    cursor.execute(
        """SELECT priority, COUNT(*) as count
           FROM tasks WHERE user_id = ?
           GROUP BY priority""",
        (user_id,)
    )
    priority_breakdown = {row["priority"]: row["count"] for row in cursor.fetchall()}

    # Tasks by source
    cursor.execute(
        """SELECT source, COUNT(*) as count
           FROM tasks WHERE user_id = ?
           GROUP BY source""",
        (user_id,)
    )
    source_breakdown = {row["source"]: row["count"] for row in cursor.fetchall()}

    # Completion rate over last 7 days (tasks completed per day)
    cursor.execute(
        """SELECT date(completed_at) as day, COUNT(*) as count
           FROM tasks
           WHERE user_id = ?
             AND status = 'done'
             AND completed_at >= datetime('now', '-7 days')
           GROUP BY day
           ORDER BY day ASC""",
        (user_id,)
    )
    daily_completions = [dict(row) for row in cursor.fetchall()]

    conn.close()

    completion_rate = round((completed_tasks / total_tasks * 100), 1) if total_tasks > 0 else 0

    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "completion_rate": completion_rate,
        "missed_deadlines": missed_deadlines,
        "completed_on_time": completed_on_time,
        "total_focus_minutes": round(total_focus_minutes, 1),
        "total_focus_hours": round(total_focus_minutes / 60, 1),
        "total_focus_sessions": total_sessions,
        "priority_breakdown": priority_breakdown,
        "source_breakdown": source_breakdown,
        "daily_completions": daily_completions,
    }


# ── INTERNAL HELPERS ─────────────────────────────────────────────────────────

def _compute_pressure_score(deadline, priority):
    """
    Compute a 0–100 pressure score for a task based on deadline proximity
    and priority level. Higher = more urgent.

    Score formula:
    - Base from priority: high=60, medium=35, low=15
    - Deadline bonus: scales from 0 (far away) to 40 (overdue or due within 1 hour)
    - Final score clamped to 0–100
    """
    priority_base = {"high": 60, "medium": 35, "low": 15}.get(priority, 35)

    if not deadline:
        return float(priority_base)

    try:
        deadline_dt = datetime.fromisoformat(deadline)
        now = datetime.now()
        hours_remaining = (deadline_dt - now).total_seconds() / 3600

        if hours_remaining <= 0:
            deadline_bonus = 40  # Overdue
        elif hours_remaining <= 1:
            deadline_bonus = 38
        elif hours_remaining <= 6:
            deadline_bonus = 32
        elif hours_remaining <= 24:
            deadline_bonus = 24
        elif hours_remaining <= 72:
            deadline_bonus = 14
        elif hours_remaining <= 168:  # 1 week
            deadline_bonus = 6
        else:
            deadline_bonus = 0

        return float(min(100, priority_base + deadline_bonus))
    except (ValueError, TypeError):
        return float(priority_base)


def _parse_tasks(rows):
    """Convert a list of sqlite3.Row objects into a list of task dicts."""
    tasks = []
    for row in rows:
        task = dict(row)
        task["subtasks"] = json.loads(task.get("subtasks") or "[]")
        tasks.append(task)
    return tasks