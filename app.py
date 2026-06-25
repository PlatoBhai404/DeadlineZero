import os
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify
)
from dotenv import load_dotenv

import database as db
import gemini_service as gemini
import gmail_service as gmail

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")


# ── HELPERS ──────────────────────────────────────────────────────────────────

def get_current_user():
    """Return the current user dict from session, or None if not logged in."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.get_user(user_id)


def require_user(f):
    """Decorator to redirect to onboarding if no user in session."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("onboarding"))
        return f(*args, **kwargs)
    return decorated


# ── INIT ─────────────────────────────────────────────────────────────────────

@app.before_request
def initialize():
    """Initialize the database on first request."""
    db.initialize_database()


# ── ONBOARDING ────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    """Redirect to dashboard if logged in, otherwise to onboarding."""
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("onboarding"))


@app.route("/onboarding", methods=["GET"])
def onboarding():
    """Show the onboarding page for new users."""
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("onboarding.html")


@app.route("/onboarding", methods=["POST"])
def onboarding_submit():
    """Handle onboarding form submission — create user and start session."""
    name = request.form.get("name", "").strip()
    persona = request.form.get("persona", "").strip()

    valid_personas = ("student", "professional", "parent", "entrepreneur")
    if not name or persona not in valid_personas:
        return render_template("onboarding.html", error="Please fill in all fields.")

    user_id = db.create_user(name, persona)
    session["user_id"] = user_id
    return redirect(url_for("dashboard"))


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@app.route("/dashboard", methods=["GET"])
@require_user
def dashboard():
    """
    Main dashboard — refreshes pressure scores, then shows daily plan,
    at-risk tasks, active focus session, and quick add.
    """
    user = get_current_user()

    # Refresh pressure scores on every dashboard load so they stay current
    db.update_pressure_scores(user["id"])

    tasks = db.get_pending_tasks(user["id"])
    at_risk = db.get_at_risk_tasks(user["id"])
    last_scan = db.get_last_email_scan(user["id"])
    active_session = db.get_active_focus_session(user["id"])

    plan = gemini.generate_daily_plan(
        user["name"],
        user["persona"],
        tasks
    )

    return render_template(
        "dashboard.html",
        user=user,
        tasks=tasks,
        at_risk=at_risk,
        plan=plan,
        last_scan=last_scan,
        active_session=active_session
    )


@app.route("/dashboard/refresh-plan", methods=["POST"])
@require_user
def refresh_plan():
    """API endpoint — regenerate and return the daily plan as JSON."""
    user = get_current_user()
    tasks = db.get_pending_tasks(user["id"])
    plan = gemini.generate_daily_plan(user["name"], user["persona"], tasks)
    return jsonify({"plan": plan})


# ── TASKS ─────────────────────────────────────────────────────────────────────

@app.route("/tasks/quick-add", methods=["POST"])
@require_user
def quick_add_task():
    """
    API endpoint — takes natural language input, extracts tasks via Gemini,
    saves them to the database, and returns the created tasks as JSON.
    """
    user = get_current_user()
    data = request.get_json()
    raw_text = data.get("text", "").strip()

    if not raw_text:
        return jsonify({"error": "No input provided."}), 400

    extracted = gemini.extract_tasks_from_text(
        user["name"], user["persona"], raw_text
    )

    created = []
    for task in extracted:
        task_id = db.create_task(
            user_id=user["id"],
            title=task.get("title", "Untitled Task"),
            description=task.get("description", ""),
            deadline=task.get("deadline"),
            priority=task.get("priority", "medium"),
            estimated_hours=task.get("estimated_hours", 1.0),
            subtasks=task.get("subtasks", []),
            source="manual",
            reasoning=task.get("reasoning", "")
        )
        task["id"] = task_id
        created.append(task)

    return jsonify({"tasks": created})


@app.route("/tasks", methods=["GET"])
@require_user
def tasks():
    """Full task list page with all tasks for the current user."""
    user = get_current_user()
    all_tasks = db.get_all_tasks(user["id"])
    return render_template("tasks.html", user=user, tasks=all_tasks)


@app.route("/tasks/update-status", methods=["POST"])
@require_user
def update_task_status():
    """API endpoint — update a task's status."""
    user = get_current_user()
    data = request.get_json()
    task_id = data.get("task_id")
    status = data.get("status")

    if not task_id or status not in ("todo", "in_progress", "done"):
        return jsonify({"error": "Invalid request."}), 400

    db.update_task_status(task_id, user["id"], status)
    return jsonify({"success": True})


@app.route("/tasks/delete", methods=["POST"])
@require_user
def delete_task():
    """API endpoint — delete a task."""
    user = get_current_user()
    data = request.get_json()
    task_id = data.get("task_id")

    if not task_id:
        return jsonify({"error": "No task ID provided."}), 400

    db.delete_task(task_id, user["id"])
    return jsonify({"success": True})


@app.route("/tasks/intervention", methods=["POST"])
@require_user
def get_intervention():
    """API endpoint — get an AI intervention message for an at-risk task."""
    user = get_current_user()
    data = request.get_json()
    task_id = data.get("task_id")

    all_tasks = db.get_all_tasks(user["id"])
    task = next((t for t in all_tasks if t["id"] == task_id), None)

    if not task:
        return jsonify({"error": "Task not found."}), 404

    message = gemini.generate_intervention(
        user["name"], user["persona"], task
    )
    return jsonify({"message": message})


# ── VOICE INPUT ───────────────────────────────────────────────────────────────

@app.route("/voice/process", methods=["POST"])
@require_user
def voice_process():
    """
    API endpoint — receives a voice transcript from the frontend
    (Web Speech API), extracts tasks via Gemini, saves them,
    and returns the created tasks as JSON.
    """
    user = get_current_user()
    data = request.get_json()
    transcript = data.get("transcript", "").strip()

    if not transcript:
        return jsonify({"error": "No transcript provided."}), 400

    extracted = gemini.extract_tasks_from_voice(
        user["name"], user["persona"], transcript
    )

    created = []
    for task in extracted:
        task_id = db.create_task(
            user_id=user["id"],
            title=task.get("title", "Untitled Task"),
            description=task.get("description", ""),
            deadline=task.get("deadline"),
            priority=task.get("priority", "medium"),
            estimated_hours=task.get("estimated_hours", 1.0),
            subtasks=task.get("subtasks", []),
            source="voice",
            reasoning=task.get("reasoning", "")
        )
        task["id"] = task_id
        created.append(task)

    return jsonify({"tasks": created, "transcript": transcript})


# ── FOCUS MODE ────────────────────────────────────────────────────────────────

@app.route("/focus", methods=["GET"])
@require_user
def focus():
    """
    Focus mode page — shows the AI-recommended task, Pomodoro timer,
    and active session state.
    """
    user = get_current_user()
    tasks = db.get_pending_tasks(user["id"])
    active_session = db.get_active_focus_session(user["id"])
    recommendation = gemini.get_focus_recommendation(
        user["name"], user["persona"], tasks
    )
    recent_sessions = db.get_focus_sessions(user["id"], limit=10)

    return render_template(
        "focus.html",
        user=user,
        tasks=tasks,
        recommendation=recommendation,
        active_session=active_session,
        recent_sessions=recent_sessions
    )


@app.route("/focus/recommend", methods=["GET"])
@require_user
def focus_recommend():
    """API endpoint — returns AI recommendation for what to work on right now."""
    user = get_current_user()
    tasks = db.get_pending_tasks(user["id"])
    recommendation = gemini.get_focus_recommendation(
        user["name"], user["persona"], tasks
    )
    return jsonify(recommendation)


@app.route("/focus/start", methods=["POST"])
@require_user
def focus_start():
    """
    API endpoint — starts a new Pomodoro focus session.
    Accepts optional task_id to link the session to a specific task.
    Returns the session ID and started_at timestamp.
    """
    user = get_current_user()
    data = request.get_json()
    task_id = data.get("task_id")

    # End any existing active session before starting a new one
    active = db.get_active_focus_session(user["id"])
    if active:
        db.end_focus_session(active["id"], user["id"], completed=False)

    session_id = db.start_focus_session(user["id"], task_id)
    return jsonify({
        "session_id": session_id,
        "started_at": datetime.now().isoformat()
    })


@app.route("/focus/end", methods=["POST"])
@require_user
def focus_end():
    """
    API endpoint — ends a Pomodoro focus session and logs duration.
    Returns duration in minutes and whether the session was completed.
    """
    user = get_current_user()
    data = request.get_json()
    session_id = data.get("session_id")
    completed = data.get("completed", True)

    if not session_id:
        return jsonify({"error": "No session ID provided."}), 400

    duration_minutes = db.end_focus_session(session_id, user["id"], completed)

    if duration_minutes is None:
        return jsonify({"error": "Session not found."}), 404

    return jsonify({
        "duration_minutes": duration_minutes,
        "completed": completed
    })


# ── CHAT ──────────────────────────────────────────────────────────────────────

@app.route("/chat", methods=["GET"])
@require_user
def chat():
    """AI chat page — shows chat history."""
    user = get_current_user()
    history = db.get_chat_history(user["id"])
    return render_template("chat.html", user=user, history=history)


@app.route("/chat/send", methods=["POST"])
@require_user
def chat_send():
    """API endpoint — send a message to the AI and get a response."""
    user = get_current_user()
    data = request.get_json()
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "No message provided."}), 400

    tasks = db.get_pending_tasks(user["id"])
    history = db.get_chat_history(user["id"])

    db.save_chat_message(user["id"], "user", message)

    response = gemini.get_chat_response(
        user["name"], user["persona"], tasks, history, message
    )

    db.save_chat_message(user["id"], "assistant", response)
    return jsonify({"response": response})


@app.route("/chat/clear", methods=["POST"])
@require_user
def chat_clear():
    """API endpoint — clear the user's chat history."""
    user = get_current_user()
    db.clear_chat_history(user["id"])
    return jsonify({"success": True})


# ── ANALYTICS ─────────────────────────────────────────────────────────────────

@app.route("/analytics", methods=["GET"])
@require_user
def analytics():
    """Analytics page — shows completion rates, focus time, and weekly summary."""
    user = get_current_user()
    stats = db.get_analytics(user["id"])
    all_tasks = db.get_all_tasks(user["id"])
    weekly_summary = gemini.generate_weekly_summary(
        user["name"], user["persona"], stats, all_tasks
    )
    return render_template(
        "analytics.html",
        user=user,
        stats=stats,
        weekly_summary=weekly_summary
    )


@app.route("/analytics/data", methods=["GET"])
@require_user
def analytics_data():
    """API endpoint — returns raw analytics JSON for chart rendering."""
    user = get_current_user()
    stats = db.get_analytics(user["id"])
    return jsonify(stats)


# ── GMAIL OAUTH ───────────────────────────────────────────────────────────────

@app.route("/gmail/connect", methods=["GET"])
@require_user
def gmail_connect():
    """Redirect the user to Google's OAuth consent screen."""
    authorization_url = gmail.get_authorization_url(session)
    return redirect(authorization_url)


@app.route("/oauth2callback", methods=["GET"])
@require_user
def oauth2callback():
    """
    Handle the OAuth callback from Google.
    Exchange the authorization code for tokens and mark Gmail as connected.
    """
    tokens = gmail.exchange_code_for_tokens(session, request.url)
    user = get_current_user()
    db.set_gmail_connected(user["id"], True)
    return redirect(url_for("dashboard"))


@app.route("/gmail/scan", methods=["POST"])
@require_user
def gmail_scan():
    """
    API endpoint — fetch recent emails, extract tasks via Gemini,
    save them to the database, and return a summary.
    """
    user = get_current_user()
    tokens = session.get("gmail_tokens")

    if not tokens:
        return jsonify({"error": "Gmail not connected."}), 401

    emails = gmail.fetch_recent_emails(tokens)

    if not emails:
        return jsonify({"tasks_created": 0, "emails_processed": 0})

    email_text = gmail.build_email_text_block(emails)
    extracted = gemini.extract_tasks_from_emails(
        user["name"], user["persona"], email_text
    )

    tasks_created = 0
    for task in extracted:
        db.create_task(
            user_id=user["id"],
            title=task.get("title", "Untitled Task"),
            description=task.get("description", ""),
            deadline=task.get("deadline"),
            priority=task.get("priority", "medium"),
            estimated_hours=task.get("estimated_hours", 1.0),
            subtasks=task.get("subtasks", []),
            source="email",
            reasoning=task.get("reasoning", "")
        )
        tasks_created += 1

    db.log_email_scan(user["id"], len(emails), tasks_created)

    return jsonify({
        "tasks_created": tasks_created,
        "emails_processed": len(emails)
    })


@app.route("/gmail/scan-accountability", methods=["POST"])
@require_user
def gmail_scan_accountability():
    """
    API endpoint — scans recent emails specifically for situations where
    someone is waiting on the user. Creates high-priority accountability tasks.
    Returns a summary of what was found.
    """
    user = get_current_user()
    tokens = session.get("gmail_tokens")

    if not tokens:
        return jsonify({"error": "Gmail not connected."}), 401

    emails = gmail.fetch_recent_emails(tokens, max_results=30)

    if not emails:
        return jsonify({"tasks_created": 0, "message": "No emails found."})

    email_text = gmail.build_email_text_block(emails)
    extracted = gemini.extract_accountability_tasks(
        user["name"], user["persona"], email_text
    )

    tasks_created = 0
    for task in extracted:
        db.create_task(
            user_id=user["id"],
            title=task.get("title", "Untitled Task"),
            description=task.get("description", ""),
            deadline=task.get("deadline"),
            priority="high",  # Accountability tasks are always high priority
            estimated_hours=task.get("estimated_hours", 0.25),
            subtasks=task.get("subtasks", []),
            source="accountability",
            reasoning=task.get("reasoning", "")
        )
        tasks_created += 1

    return jsonify({
        "tasks_created": tasks_created,
        "emails_processed": len(emails),
        "message": f"Found {tasks_created} people waiting on you."
    })


# ── RUN ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)