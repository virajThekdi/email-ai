import json
import os
import re
from html import unescape
from datetime import datetime, timezone
from typing import Any

import requests

from supabase_client import get_setting


PRIORITY_KEYWORDS = {
    "high": ("urgent", "asap", "immediately", "today", "deadline", "overdue", "critical"),
    "medium": ("soon", "tomorrow", "this week", "follow up", "reminder", "please"),
}

ACTION_KEYWORDS = (
    "please",
    "send",
    "share",
    "confirm",
    "approve",
    "review",
    "quote",
    "quotation",
    "invoice",
    "payment",
    "call",
    "meeting",
    "schedule",
    "deadline",
    "asap",
    "urgent",
    "need",
    "can you",
    "could you",
    "request",
    "follow up",
)

NOISE_KEYWORDS = (
    "unsubscribe",
    "newsletter",
    "promotion",
    "sale ends",
    "verify your login",
    "security alert",
    "password reset",
    "digest",
    "no-reply",
    "noreply",
    "do not reply",
    "marketing",
)


def strip_email_noise(body: str, max_chars: int = 4500) -> str:
    text = re.sub(r"\r\n?", "\n", body or "")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    text = unescape(text)
    text = re.split(r"\nOn .+ wrote:\n|\nFrom:\s|\nSent:\s", text, maxsplit=1)[0]
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def rule_priority(subject: str, body: str, timestamp: datetime | None = None) -> str:
    haystack = f"{subject} {body}".lower()
    if any(word in haystack for word in PRIORITY_KEYWORDS["high"]):
        return "high"
    if any(word in haystack for word in PRIORITY_KEYWORDS["medium"]):
        return "medium"
    if timestamp:
        age_hours = (datetime.now(timezone.utc) - timestamp).total_seconds() / 3600
        if age_hours >= 48:
            return "medium"
    return "low"


def is_actionable_email(subject: str, body: str, sender: str, attachment_names: list[str] | None = None) -> tuple[bool, str]:
    text = strip_email_noise(f"{subject}\n{body}", 2500).lower()
    sender_lower = (sender or "").lower()
    attachment_names = attachment_names or []

    if any(word in sender_lower for word in ("no-reply", "noreply", "donotreply", "mailer-daemon")):
        return False, "Automated sender"
    if any(word in text for word in NOISE_KEYWORDS) and not any(word in text for word in ACTION_KEYWORDS):
        return False, "Marketing or automated email"
    if len(text) < 30 and not attachment_names:
        return False, "Too little actionable content"
    if any(word in text for word in ACTION_KEYWORDS):
        return True, "Action keyword detected"
    if attachment_names and any(name.lower().endswith((".pdf", ".xlsx", ".csv", ".docx")) for name in attachment_names):
        return True, "Business attachment detected"
    if "?" in text and len(text) > 80:
        return True, "Question detected"
    return False, "No clear action detected"


def should_use_ai(subject: str, body: str, sender: str, attachment_text: str = "", attachment_names: list[str] | None = None) -> bool:
    combined = f"{subject}\n{body}\n{attachment_text}"
    priority = rule_priority(subject, combined)
    if priority in {"high", "medium"}:
        return True
    if attachment_text:
        return True
    if len(strip_email_noise(combined, 5000)) > 900:
        return True
    return False


def extract_deadline(subject: str, body: str) -> str | None:
    text = f"{subject}\n{body}".lower()
    if "today" in text:
        return "today"
    if "tomorrow" in text:
        return "tomorrow"
    match = re.search(r"\b(?:by|before|on)\s+([a-z]{3,9}\s+\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", text)
    return match.group(1) if match else None


def rule_based_analysis(
    subject: str,
    body: str,
    sender: str,
    timestamp: datetime | None,
    attachment_text: str = "",
    attachment_names: list[str] | None = None,
) -> dict[str, Any]:
    clean_body = strip_email_noise(body, 900)
    combined = f"{clean_body}\n{attachment_text}"
    priority = rule_priority(subject, combined, timestamp)
    topic = subject.strip() or clean_body.split(".")[0][:80] or "email request"
    task = f"Respond to {sender} about {topic}".strip()
    attachment_names = attachment_names or []
    return {
        "summary": (clean_body[:180] or subject or "Email needs review").strip(),
        "task_text": task[:220],
        "priority": priority,
        "deadline": extract_deadline(subject, combined),
        "deadline_date": None,
        "next_action": "Open the email thread and send the needed response.",
        "priority_reason": "Priority estimated from urgency keywords and email age.",
        "suggested_reply": "Thanks for your email. I will check this and get back to you shortly.",
        "category": _rule_category(subject, combined),
        "client_name": "",
        "contact_name": sender,
        "intent": "request",
        "sentiment": "neutral",
        "escalation_risk": "low",
        "attachment_summary": _fallback_attachment_summary(attachment_names, attachment_text),
        "tasks": [],
    }


def _fallback_task(
    subject: str,
    body: str,
    sender: str,
    timestamp: datetime | None,
    attachment_text: str = "",
    attachment_names: list[str] | None = None,
) -> dict[str, Any]:
    return rule_based_analysis(subject, body, sender, timestamp, attachment_text, attachment_names)


def analyze_email(
    subject: str,
    body: str,
    sender: str,
    timestamp: datetime | None = None,
    attachment_text: str = "",
    attachment_names: list[str] | None = None,
    reply_context: str = "",
) -> dict[str, Any]:
    clean_body = strip_email_noise(body)
    attachment_names = attachment_names or []
    prompt = (
        "You are an email task assistant. Convert this email and its attachment text into an action board item. "
        "Return only JSON. Keys: summary, task_text, priority, deadline, deadline_date, next_action, "
        "priority_reason, suggested_reply, category, client_name, contact_name, intent, sentiment, "
        "escalation_risk, attachment_summary, tasks. "
        "priority must be high, medium, or low. deadline_date should be YYYY-MM-DD or null. "
        "sentiment should be positive, neutral, frustrated, angry, or urgent. "
        "escalation_risk should be low, medium, or high. "
        "tasks is an array of up to 5 short task strings if the email contains multiple actions. "
        "Use the user's reply examples to match tone and phrasing when writing suggested_reply.\n\n"
        f"Sender: {sender}\nSubject: {subject}\nAttachment names: {', '.join(attachment_names) or 'none'}\n"
        f"Body:\n{clean_body}\n\nAttachment text:\n{attachment_text[:5000]}\n\n"
        f"Relevant user reply examples:\n{reply_context[:3000]}"
    )

    result = _call_groq(prompt) or _call_gemini(prompt)
    if not result:
        return _fallback_task(subject, clean_body, sender, timestamp, attachment_text, attachment_names)

    fallback = _fallback_task(subject, clean_body, sender, timestamp, attachment_text, attachment_names)
    merged = {**fallback, **{k: v for k, v in result.items() if v not in (None, "")}}
    merged["priority"] = _normalize_priority(str(merged.get("priority", fallback["priority"])))
    merged["escalation_risk"] = _normalize_risk(str(merged.get("escalation_risk", fallback["escalation_risk"])))
    if not isinstance(merged.get("tasks"), list):
        merged["tasks"] = []
    return merged


def summarize_board(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "No active tasks right now."
    top = tasks[:8]
    first = top[0]
    high_count = len([task for task in tasks if task.get("priority") == "high"])
    risky = len([task for task in tasks if task.get("escalation_risk") == "high"])
    prefix = f"{high_count} high-priority task(s). " if high_count else ""
    risk = f"{risky} escalation risk(s). " if risky else ""
    reason = first.get("priority_reason") or first.get("summary") or "it is the highest-ranked active task"
    return f"{prefix}{risk}Do this first: {first.get('task_text')}. Reason: {reason}"


def _normalize_priority(value: str) -> str:
    value = value.lower().strip()
    return value if value in {"high", "medium", "low"} else "medium"


def _normalize_risk(value: str) -> str:
    value = value.lower().strip()
    return value if value in {"high", "medium", "low"} else "low"


def _rule_category(subject: str, body: str) -> str:
    text = f"{subject} {body}".lower()
    if any(word in text for word in ("quote", "quotation", "proposal", "price")):
        return "quotation"
    if any(word in text for word in ("invoice", "payment", "receipt", "bill")):
        return "billing"
    if any(word in text for word in ("meeting", "call", "calendar", "schedule")):
        return "meeting"
    if any(word in text for word in ("complaint", "issue", "problem", "refund")):
        return "support"
    return "general"


def _fallback_attachment_summary(attachment_names: list[str], attachment_text: str) -> str:
    if not attachment_names:
        return ""
    if attachment_text:
        return f"Attachments included: {', '.join(attachment_names)}. Extracted text is available for AI review."
    return f"Attachments included: {', '.join(attachment_names)}."


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _call_groq(prompt: str) -> dict[str, Any] | None:
    api_key = get_setting("GROQ_API_KEY")
    if not api_key:
        return None
    model = get_setting("GROQ_MODEL") or "llama-3.1-8b-instant"
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 260,
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        response.raise_for_status()
        return _parse_json(response.json()["choices"][0]["message"]["content"])
    except requests.RequestException:
        return None


def _call_gemini(prompt: str) -> dict[str, Any] | None:
    api_key = get_setting("GEMINI_API_KEY")
    if not api_key:
        return None
    model = get_setting("GEMINI_MODEL") or "gemini-1.5-flash"
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        response.raise_for_status()
        parts = response.json()["candidates"][0]["content"]["parts"]
        return _parse_json(parts[0]["text"])
    except (requests.RequestException, KeyError, IndexError):
        return None
