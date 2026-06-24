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
    """Create all tables if they don't already exist."""
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
    """)

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
    """Insert a new task and return its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO tasks
           (user_id, title, description, deadline, priority,
            estimated_hours, status, subtasks, source, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, 'todo', ?, ?, ?)""",
        (
            user_id, title, description, deadline, priority,
            estimated_hours,
            json.dumps(subtasks or []),
            source, reasoning
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
    tasks = []
    for row in rows:
        task = dict(row)
        task["subtasks"] = json.loads(task["subtasks"] or "[]")
        tasks.append(task)
    return tasks


def get_pending_tasks(user_id):
    """Return all tasks that are not completed."""
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
    tasks = []
    for row in rows:
        task = dict(row)
        task["subtasks"] = json.loads(task["subtasks"] or "[]")
        tasks.append(task)
    return tasks


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
    tasks = []
    for row in rows:
        task = dict(row)
        task["subtasks"] = json.loads(task["subtasks"] or "[]")
        tasks.append(task)
    return tasks


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