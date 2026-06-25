import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")


def _call_gemini(prompt: str) -> str:
    """Internal helper — sends a prompt to Gemini and returns the text response."""
    response = model.generate_content(prompt)
    return response.text.strip()


def _parse_json_response(raw: str) -> list:
    """
    Internal helper — strips markdown fences from a Gemini response
    and parses it as JSON. Returns empty list on failure.
    """
    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return []


# ── 1. TASK EXTRACTION FROM NATURAL LANGUAGE ────────────────────────────────

def extract_tasks_from_text(user_name: str, persona: str, raw_text: str) -> list:
    """
    Takes a natural language input from the user and returns a list of
    structured task dicts extracted by Gemini.
    """
    prompt = f"""
You are DeadlineZero, an AI productivity assistant. Your job is to extract actionable tasks from a user's natural language input.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

USER INPUT:
\"\"\"{raw_text}\"\"\"

INSTRUCTIONS:
Extract every distinct task, commitment, deadline, or obligation mentioned. For each one, return a JSON object.
If the input mentions multiple tasks, return multiple objects.
Make intelligent guesses for missing fields based on context and the user's persona.

Respond ONLY with a valid JSON array. No explanation. No markdown. No code blocks. Just the raw JSON array.

Each object in the array must have exactly these fields:
{{
  "title": "short action-oriented task title",
  "description": "one sentence with more context",
  "deadline": "YYYY-MM-DD HH:MM or null if not mentioned",
  "priority": "high or medium or low",
  "estimated_hours": a number like 1.5,
  "subtasks": ["subtask 1", "subtask 2", "subtask 3"],
  "reasoning": "one sentence explaining why you set this priority level"
}}
"""
    raw = _call_gemini(prompt)
    return _parse_json_response(raw)


# ── 2. TASK EXTRACTION FROM VOICE INPUT ─────────────────────────────────────

def extract_tasks_from_voice(user_name: str, persona: str, transcript: str) -> list:
    """
    Takes a voice transcript (raw speech-to-text) and extracts actionable tasks.
    Handles informal, incomplete, and fragmented speech patterns.
    Returns a list of structured task dicts.
    """
    prompt = f"""
You are DeadlineZero, an AI productivity assistant. The user just spoke aloud and their speech was transcribed. Speech is informal — it may have incomplete sentences, filler words, repetitions, or unclear references. Your job is to extract every real task or commitment from it.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

VOICE TRANSCRIPT:
\"\"\"{transcript}\"\"\"

INSTRUCTIONS:
Ignore filler words (um, uh, like, you know). Focus on actual commitments, deadlines, and tasks.
If the user says "I need to...", "I have to...", "don't forget...", "by Friday...", "tomorrow I...", treat these as task signals.
Make smart inferences about deadlines from relative time references (today, tomorrow, next week, Friday).
Today's context: extract relative dates as real YYYY-MM-DD values intelligently.

Respond ONLY with a valid JSON array. No explanation. No markdown. No code blocks. Just the raw JSON array.

Each object must have exactly these fields:
{{
  "title": "short action-oriented task title",
  "description": "one sentence with more context",
  "deadline": "YYYY-MM-DD HH:MM or null if unclear",
  "priority": "high or medium or low",
  "estimated_hours": a number like 1.5,
  "subtasks": ["subtask 1", "subtask 2"],
  "reasoning": "one sentence explaining why you set this priority level"
}}
"""
    raw = _call_gemini(prompt)
    return _parse_json_response(raw)


# ── 3. DAILY PLAN GENERATION ─────────────────────────────────────────────────

def generate_daily_plan(user_name: str, persona: str, tasks: list, available_hours: float = 8.0) -> str:
    """
    Given the user's full pending task list and available hours today,
    returns a prioritized hour-by-hour plan as plain text.
    """
    if not tasks:
        return "You have no pending tasks. Add something to get started."

    tasks_summary = "\n".join([
        f"- [{t['priority'].upper()}] {t['title']} | "
        f"Due: {t['deadline'] or 'No deadline'} | "
        f"Est: {t['estimated_hours']}h | "
        f"Status: {t['status']} | "
        f"Pressure: {t.get('pressure_score', 0):.0f}/100"
        for t in tasks
    ])

    prompt = f"""
You are DeadlineZero, an AI productivity assistant. Your job is to create a realistic, prioritized daily plan for the user.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}
- Available hours today: {available_hours}

PENDING TASKS (with pressure scores — higher means more urgent):
{tasks_summary}

INSTRUCTIONS:
Create a specific hour-by-hour schedule for today. Start from 8:00 AM.
Prioritize by pressure score first, then deadline urgency, then importance to their persona.
Be realistic — do not schedule more hours than available.
For each block, explain in one sentence WHY this task is scheduled at this time.
If a task cannot fit today, explicitly say so and suggest the earliest it should be done.
Include a 15-minute break after every 2 hours of work.

Respond in plain text. Use this format for each block:
[TIME] - [TIME]: Task name (reasoning)

End with a one-paragraph "Day Strategy" summary explaining the overall approach.
"""
    return _call_gemini(prompt)


# ── 4. EMAIL TASK EXTRACTION ─────────────────────────────────────────────────

def extract_tasks_from_emails(user_name: str, persona: str, email_text: str) -> list:
    """
    Takes a block of email content and extracts actionable tasks from it.
    Returns a list of structured task dicts.
    """
    prompt = f"""
You are DeadlineZero, an AI productivity assistant. You are reading a user's emails to find tasks they need to act on.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

EMAIL CONTENT:
\"\"\"{email_text}\"\"\"

INSTRUCTIONS:
Identify every email that contains an actionable commitment, deadline, meeting, payment, appointment, or task.
Ignore newsletters, promotional emails, and anything that does not require action from the user.
For each actionable email, extract one task.

Respond ONLY with a valid JSON array. No explanation. No markdown. No code blocks. Just the raw JSON array.

Each object must have exactly these fields:
{{
  "title": "short action-oriented task title",
  "description": "one sentence summarizing what needs to be done",
  "deadline": "YYYY-MM-DD HH:MM or null if not mentioned",
  "priority": "high or medium or low",
  "estimated_hours": a number like 0.5,
  "subtasks": ["subtask 1", "subtask 2"],
  "reasoning": "one sentence explaining why this email requires action"
}}
"""
    raw = _call_gemini(prompt)
    return _parse_json_response(raw)


# ── 5. ACCOUNTABILITY TASK EXTRACTION ────────────────────────────────────────

def extract_accountability_tasks(user_name: str, persona: str, email_text: str) -> list:
    """
    Scans emails specifically for situations where someone else is waiting
    on the user to take action. These become high-priority accountability tasks.
    Returns a list of structured task dicts with source='accountability'.
    """
    prompt = f"""
You are DeadlineZero, an AI productivity assistant. You are scanning a user's emails for a specific pattern: emails where SOMEONE ELSE is waiting for the user to respond, deliver, approve, or act.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

EMAIL CONTENT:
\"\"\"{email_text}\"\"\"

INSTRUCTIONS:
Look ONLY for emails where:
- Someone asked the user a direct question and is waiting for a reply
- Someone is blocked on the user's decision or approval
- The user promised to send something and hasn't
- A colleague, client, or teacher is following up because the user hasn't responded
- There's a deadline in the email that the user is responsible for delivering

Ignore emails where the user is waiting on someone else.
Ignore newsletters, promotions, and automated notifications.

If you find no such emails, return an empty array: []

Respond ONLY with a valid JSON array. No explanation. No markdown. No code blocks. Just the raw JSON array.

Each object must have exactly these fields:
{{
  "title": "short action-oriented task title starting with 'Reply to' or 'Send' or 'Deliver' or 'Approve'",
  "description": "one sentence: who is waiting and what they need",
  "deadline": "YYYY-MM-DD HH:MM or null — set to today if someone is clearly waiting",
  "priority": "high — accountability tasks are always high priority",
  "estimated_hours": a number like 0.25,
  "subtasks": [],
  "reasoning": "one sentence: why this person is blocked on the user"
}}
"""
    raw = _call_gemini(prompt)
    return _parse_json_response(raw)


# ── 6. PROCRASTINATION INTERVENTION ──────────────────────────────────────────

def generate_intervention(user_name: str, persona: str, task: dict) -> str:
    """
    For a task that has been sitting untouched too long,
    generates a personalized intervention message with a concrete first step.
    """
    prompt = f"""
You are DeadlineZero, an AI productivity assistant. A user has been avoiding an important task and needs a direct, motivating intervention.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

AT-RISK TASK:
- Title: {task['title']}
- Deadline: {task['deadline'] or 'No deadline set'}
- Priority: {task['priority']}
- Pressure Score: {task.get('pressure_score', 0):.0f}/100
- Estimated time: {task['estimated_hours']} hours
- Description: {task.get('description', '')}
- Subtasks: {', '.join(task.get('subtasks', [])) or 'None defined'}

INSTRUCTIONS:
Write a short, direct, warm intervention message addressed to {user_name}.
Explain specifically why this task is at risk using the pressure score and deadline.
Tell them exactly what they should do RIGHT NOW — name the first subtask or the single smallest action they can take in the next 5 minutes.
Do not be preachy or condescending. Be like a smart friend who genuinely cares.
Keep it under 100 words.
End with one sentence of encouragement.
"""
    return _call_gemini(prompt)


# ── 7. FOCUS RECOMMENDATION ──────────────────────────────────────────────────

def get_focus_recommendation(user_name: str, persona: str, tasks: list) -> dict:
    """
    Analyzes all pending tasks and returns a recommendation for what the user
    should work on RIGHT NOW. Returns a dict with task_id, title, reasoning,
    and a suggested focus duration in minutes.
    """
    if not tasks:
        return {
            "task_id": None,
            "title": "No tasks",
            "reasoning": "You have no pending tasks. Add something to get started.",
            "suggested_minutes": 25
        }

    tasks_summary = "\n".join([
        f"- ID:{t['id']} [{t['priority'].upper()}] {t['title']} | "
        f"Due: {t['deadline'] or 'No deadline'} | "
        f"Est: {t['estimated_hours']}h | "
        f"Pressure: {t.get('pressure_score', 0):.0f}/100 | "
        f"Status: {t['status']}"
        for t in tasks
    ])

    prompt = f"""
You are DeadlineZero, an AI productivity assistant. The user wants to start working RIGHT NOW and needs you to tell them exactly what to focus on.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

PENDING TASKS:
{tasks_summary}

INSTRUCTIONS:
Pick the single most important task to work on right now.
Consider: deadline proximity, pressure score, priority level, and estimated effort.
Also consider what a person with this persona would need to prioritize.

Respond ONLY with a valid JSON object. No explanation. No markdown. Just raw JSON.

{{
  "task_id": the integer ID of the task (from the ID: field above),
  "title": "exact task title",
  "reasoning": "2-3 sentences explaining specifically why this task right now",
  "suggested_minutes": 25 or 50 (Pomodoro length — 25 for complex scary tasks to reduce friction, 50 for flow-state tasks)
}}
"""
    raw = _call_gemini(prompt)
    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(clean)
        # Validate task_id exists in our task list
        valid_ids = {t["id"] for t in tasks}
        if result.get("task_id") not in valid_ids:
            result["task_id"] = tasks[0]["id"]
            result["title"] = tasks[0]["title"]
        return result
    except (json.JSONDecodeError, KeyError):
        # Fallback to highest pressure task
        top = max(tasks, key=lambda t: t.get("pressure_score", 0))
        return {
            "task_id": top["id"],
            "title": top["title"],
            "reasoning": f"{top['title']} has the highest urgency score right now.",
            "suggested_minutes": 25
        }


# ── 8. WEEKLY PRODUCTIVITY SUMMARY ───────────────────────────────────────────

def generate_weekly_summary(user_name: str, persona: str, analytics: dict, tasks: list) -> str:
    """
    Generates an honest, direct weekly productivity summary based on
    the user's analytics data. Includes what went well, what didn't,
    and specific recommendations for next week.
    """
    completed_titles = [t["title"] for t in tasks if t.get("status") == "done"]
    pending_titles = [t["title"] for t in tasks if t.get("status") != "done"]

    prompt = f"""
You are DeadlineZero, an AI productivity assistant giving {user_name} their weekly performance review.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

WEEKLY STATS:
- Total tasks: {analytics['total_tasks']}
- Completed: {analytics['completed_tasks']} ({analytics['completion_rate']}% completion rate)
- Missed deadlines: {analytics['missed_deadlines']}
- Completed on time: {analytics['completed_on_time']}
- Total focus time: {analytics['total_focus_hours']} hours across {analytics['total_focus_sessions']} sessions
- Priority breakdown: {analytics['priority_breakdown']}
- Source breakdown: {analytics['source_breakdown']}

COMPLETED TASKS THIS WEEK:
{', '.join(completed_titles[:10]) or 'None'}

STILL PENDING:
{', '.join(pending_titles[:10]) or 'None'}

INSTRUCTIONS:
Write an honest, warm, direct weekly summary. Do NOT be overly positive or use corporate language.
Structure your response with these three sections:

**What you crushed:** Specific wins from this week's data.
**Where you struggled:** Honest assessment of missed deadlines or low completion rate. Do not sugarcoat.
**Next week's focus:** 2-3 specific, actionable recommendations based on their actual pending tasks and persona.

Be like a brutally honest coach who also genuinely cares. Under 200 words total.
"""
    return _call_gemini(prompt)


# ── 9. AI CHAT RESPONSE ───────────────────────────────────────────────────────

def get_chat_response(user_name: str, persona: str, tasks: list,
                      chat_history: list, user_message: str) -> str:
    """
    Responds to a user's chat message with full task and analytics context.
    chat_history is a list of dicts with 'role' and 'message' keys.
    """
    tasks_summary = "\n".join([
        f"- ID:{t['id']} [{t['priority'].upper()}] {t['title']} | "
        f"Due: {t['deadline'] or 'No deadline'} | "
        f"Status: {t['status']} | "
        f"Est: {t['estimated_hours']}h | "
        f"Pressure: {t.get('pressure_score', 0):.0f}/100"
        for t in tasks
    ]) if tasks else "No tasks yet."

    history_text = "\n".join([
        f"{msg['role'].upper()}: {msg['message']}"
        for msg in chat_history[-10:]
    ]) if chat_history else "No previous messages."

    prompt = f"""
You are DeadlineZero, an AI productivity assistant having a conversation with {user_name}.
You have full context of their tasks, pressure scores, and workload. Give specific, actionable advice — never generic productivity tips.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

CURRENT TASKS (with pressure scores):
{tasks_summary}

RECENT CONVERSATION:
{history_text}

USER'S NEW MESSAGE:
\"\"\"{user_message}\"\"\"

INSTRUCTIONS:
Respond directly and helpfully. Reference their actual tasks by name when relevant.
If they ask what to focus on, use the pressure scores to give a specific, ranked answer.
If they mention a new task or deadline in the chat, acknowledge it and suggest they add it.
Be warm, direct, and specific. Do not give generic advice.
Keep responses concise — under 150 words unless a detailed breakdown is genuinely needed.
"""
    return _call_gemini(prompt)