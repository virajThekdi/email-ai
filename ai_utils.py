import json
import os
import re
from html import unescape
from datetime import datetime, timezone
from typing import Any

import requests


PRIORITY_KEYWORDS = {
    "high": ("urgent", "asap", "immediately", "today", "deadline", "overdue", "critical"),
    "medium": ("soon", "tomorrow", "this week", "follow up", "reminder", "please"),
}


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


def extract_deadline(subject: str, body: str) -> str | None:
    text = f"{subject}\n{body}".lower()
    if "today" in text:
        return "today"
    if "tomorrow" in text:
        return "tomorrow"
    match = re.search(r"\b(?:by|before|on)\s+([a-z]{3,9}\s+\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", text)
    return match.group(1) if match else None


def _fallback_task(subject: str, body: str, sender: str, timestamp: datetime | None) -> dict[str, Any]:
    clean_body = strip_email_noise(body, 900)
    priority = rule_priority(subject, clean_body, timestamp)
    topic = subject.strip() or clean_body.split(".")[0][:80] or "email request"
    task = f"Respond to {sender} about {topic}".strip()
    return {
        "summary": (clean_body[:180] or subject or "Email needs review").strip(),
        "task_text": task[:220],
        "priority": priority,
        "deadline": extract_deadline(subject, clean_body),
        "next_action": "Open the email thread and send the needed response.",
    }


def analyze_email(subject: str, body: str, sender: str, timestamp: datetime | None = None) -> dict[str, Any]:
    clean_body = strip_email_noise(body)
    prompt = (
        "Convert this email into one actionable task for an internal task board. "
        "Return only JSON with keys: summary, task_text, priority, deadline, next_action. "
        "priority must be high, medium, or low. deadline may be null. Keep summary under 25 words.\n\n"
        f"Sender: {sender}\nSubject: {subject}\nBody:\n{clean_body}"
    )

    result = _call_groq(prompt) or _call_gemini(prompt)
    if not result:
        return _fallback_task(subject, clean_body, sender, timestamp)

    fallback = _fallback_task(subject, clean_body, sender, timestamp)
    merged = {**fallback, **{k: v for k, v in result.items() if v not in (None, "")}}
    merged["priority"] = _normalize_priority(str(merged.get("priority", fallback["priority"])))
    return merged


def _normalize_priority(value: str) -> str:
    value = value.lower().strip()
    return value if value in {"high", "medium", "low"} else "medium"


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
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
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


def _call_gemini(prompt: str) -> dict[str, Any] | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=20,
    )
    response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    return _parse_json(parts[0]["text"])
