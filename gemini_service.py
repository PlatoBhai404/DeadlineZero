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
    try:
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


# ── 2. DAILY PLAN GENERATION ─────────────────────────────────────────────────

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
        f"Status: {t['status']}"
        for t in tasks
    ])

    prompt = f"""
You are DeadlineZero, an AI productivity assistant. Your job is to create a realistic, prioritized daily plan for the user.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}
- Available hours today: {available_hours}

PENDING TASKS:
{tasks_summary}

INSTRUCTIONS:
Create a specific hour-by-hour schedule for today. Start from 8:00 AM.
Prioritize by deadline urgency first, then by importance to their persona.
Be realistic — do not schedule more hours than available.
For each block, explain in one sentence WHY this task is scheduled first.
If a task cannot fit today, say so and suggest when to do it.

Respond in plain text. Use this format for each block:
[TIME] - [TIME]: Task name (reasoning)

End with a one-paragraph summary of the day's strategy.
"""
    return _call_gemini(prompt)


# ── 3. EMAIL TASK EXTRACTION ─────────────────────────────────────────────────

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
    try:
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


# ── 4. PROCRASTINATION INTERVENTION ──────────────────────────────────────────

def generate_intervention(user_name: str, persona: str, task: dict) -> str:
    """
    For a task that has been sitting untouched too long,
    generates a personalized intervention message.
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
- Estimated time: {task['estimated_hours']} hours
- Description: {task.get('description', '')}

INSTRUCTIONS:
Write a short, direct, warm intervention message addressed to {user_name}.
Explain specifically why this task is at risk.
Tell them exactly what they should do RIGHT NOW to make progress — a concrete first step.
Do not be preachy or condescending. Be like a smart friend who cares.
Keep it under 100 words.
"""
    return _call_gemini(prompt)


# ── 5. AI CHAT RESPONSE ───────────────────────────────────────────────────────

def get_chat_response(user_name: str, persona: str, tasks: list,
                      chat_history: list, user_message: str) -> str:
    """
    Responds to a user's chat message with full task context.
    chat_history is a list of dicts with 'role' and 'message' keys.
    """
    tasks_summary = "\n".join([
        f"- [{t['priority'].upper()}] {t['title']} | "
        f"Due: {t['deadline'] or 'No deadline'} | "
        f"Status: {t['status']} | "
        f"Est: {t['estimated_hours']}h"
        for t in tasks
    ]) if tasks else "No tasks yet."

    history_text = "\n".join([
        f"{msg['role'].upper()}: {msg['message']}"
        for msg in chat_history[-10:]
    ]) if chat_history else "No previous messages."

    prompt = f"""
You are DeadlineZero, an AI productivity assistant having a conversation with {user_name}.
You have full context of their tasks and workload. Give specific, actionable advice — never generic productivity tips.

USER CONTEXT:
- Name: {user_name}
- Persona: {persona}

CURRENT TASKS:
{tasks_summary}

RECENT CONVERSATION:
{history_text}

USER'S NEW MESSAGE:
\"\"\"{user_message}\"\"\"

INSTRUCTIONS:
Respond directly and helpfully. Reference their actual tasks by name when relevant.
Be warm, direct, and specific. Do not give generic advice.
If they ask what to focus on, look at their deadlines and priorities and give a specific answer.
Keep responses concise — under 150 words unless a detailed breakdown is genuinely needed.
"""
    return _call_gemini(prompt)