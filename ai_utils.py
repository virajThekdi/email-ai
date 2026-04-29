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

RFQ_KEYWORDS = ("rfq", "quotation", "quote", "rate", "price", "pricing", "offer", "estimate")
ORDER_KEYWORDS = ("purchase order", "po", "order confirmed", "confirmed order", "go ahead", "final order", "dispatch")
NEGOTIATION_KEYWORDS = ("discount", "negotiate", "best price", "reduce", "final rate", "counter")
BLOCKING_KEYWORDS = ("hold", "waiting", "pending", "material required", "shortage", "not available", "clarify", "missing")
PRODUCTION_KEYWORDS = ("produce", "production", "ready", "dispatch", "delivery", "rework", "cutting", "polish", "toughened")
GLASS_KEYWORDS = (
    "glass",
    "toughened",
    "tempered",
    "laminated",
    "float",
    "mirror",
    "dgu",
    "igu",
    "frosted",
    "clear",
    "extra clear",
)
UNITS = ("pcs", "pieces", "sheets", "nos", "kg", "sqm", "sqft", "mm")

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


def production_intelligence(
    subject: str,
    body: str,
    sender: str,
    timestamp: datetime | None = None,
    attachment_text: str = "",
    attachment_names: list[str] | None = None,
) -> dict[str, Any]:
    text = strip_email_noise(f"{subject}\n{body}\n{attachment_text}", 6000)
    lower = text.lower()
    attachment_names = attachment_names or []
    is_rfq = any(word in lower for word in RFQ_KEYWORDS)
    is_order = any(word in lower for word in ORDER_KEYWORDS)
    is_negotiation = any(word in lower for word in NEGOTIATION_KEYWORDS)
    is_blocking = any(word in lower for word in BLOCKING_KEYWORDS)
    product_type = _extract_product_type(lower)
    quantity = _extract_quantity(lower)
    delivery_location = _extract_location(text)
    deadline = extract_deadline(subject, text)
    missing_fields = _missing_fields(product_type, quantity, deadline, delivery_location, is_rfq or is_order)
    workflow_stage = _workflow_stage(lower, is_rfq, is_order, is_negotiation, is_blocking)
    priority_score = _priority_score(lower, quantity, deadline, is_rfq, is_order, is_blocking, attachment_names, timestamp)
    return {
        "workflow_stage": workflow_stage,
        "product_type": product_type,
        "quantity": quantity,
        "delivery_location": delivery_location,
        "is_rfq": is_rfq,
        "is_order": is_order,
        "is_blocking": is_blocking or bool(missing_fields),
        "missing_fields": ", ".join(missing_fields),
        "priority_score": priority_score,
    }


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
    attachment_names = attachment_names or []
    production = production_intelligence(subject, body, sender, timestamp, attachment_text, attachment_names)
    task = _production_task_text(sender, subject, production)
    if production["priority_score"] >= 75:
        priority = "high"
    elif production["priority_score"] >= 45 and priority == "low":
        priority = "medium"
    return {
        "summary": (clean_body[:180] or subject or "Email needs review").strip(),
        "task_text": task[:220],
        "priority": priority,
        "deadline": extract_deadline(subject, combined),
        "deadline_date": None,
        "next_action": _production_next_action(production),
        "priority_reason": _production_priority_reason(production, priority),
        "suggested_reply": _production_reply_draft(production),
        "category": _rule_category(subject, combined),
        "client_name": "",
        "contact_name": sender,
        "intent": production["workflow_stage"],
        "sentiment": "neutral",
        "escalation_risk": "high" if production["is_blocking"] and production["priority_score"] >= 70 else "medium" if production["is_blocking"] else "low",
        "attachment_summary": _fallback_attachment_summary(attachment_names, attachment_text),
        "tasks": [],
        **production,
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
        "You are a production control assistant for a manufacturing/glass company. "
        "Your goal is to show what to quote, produce, dispatch, follow up, or unblock. "
        "Convert this email and its attachment text into a production action board item. "
        "Return only JSON. Keys: summary, task_text, priority, deadline, deadline_date, next_action, "
        "priority_reason, suggested_reply, category, client_name, contact_name, intent, sentiment, "
        "escalation_risk, attachment_summary, workflow_stage, product_type, quantity, delivery_location, "
        "is_rfq, is_order, is_blocking, missing_fields, priority_score, tasks. "
        "priority must be high, medium, or low. deadline_date should be YYYY-MM-DD or null. "
        "workflow_stage should be rfq, negotiation, order_confirmed, production, dispatch, follow_up, blocked, or general. "
        "priority_score is 0-100 based on urgency, quantity, deadline, blocking risk, and business value. "
        "missing_fields should list missing RFQ/order details like product, quantity, delivery date, location, specs. "
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
    merged["priority_score"] = _normalize_score(merged.get("priority_score", fallback.get("priority_score", 0)))
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


def _normalize_score(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _rule_category(subject: str, body: str) -> str:
    text = f"{subject} {body}".lower()
    if any(word in text for word in RFQ_KEYWORDS):
        return "rfq"
    if any(word in text for word in ORDER_KEYWORDS):
        return "order"
    if any(word in text for word in PRODUCTION_KEYWORDS):
        return "production"
    if any(word in text for word in ("quote", "quotation", "proposal", "price")):
        return "quotation"
    if any(word in text for word in ("invoice", "payment", "receipt", "bill")):
        return "billing"
    if any(word in text for word in ("meeting", "call", "calendar", "schedule")):
        return "meeting"
    if any(word in text for word in ("complaint", "issue", "problem", "refund")):
        return "support"
    return "general"


def _extract_product_type(text: str) -> str:
    thickness = re.search(r"\b(\d+(?:\.\d+)?)\s*mm\b", text)
    glass_words = [word for word in GLASS_KEYWORDS if word in text]
    parts = []
    if thickness:
        parts.append(f"{thickness.group(1)}mm")
    parts.extend(glass_words[:3])
    if parts:
        return " ".join(dict.fromkeys(parts))
    return "glass" if "glass" in text else ""


def _extract_quantity(text: str) -> str:
    patterns = [
        r"\b(\d+(?:,\d+)?(?:\.\d+)?)\s*(pcs|pieces|sheets|nos|kg|sqm|sqft)\b",
        r"\bqty[:\s-]*(\d+(?:,\d+)?(?:\.\d+)?)\b",
        r"\bquantity[:\s-]*(\d+(?:,\d+)?(?:\.\d+)?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return " ".join(group for group in match.groups() if group)
    return ""


def _extract_location(text: str) -> str:
    match = re.search(r"\b(?:delivery|dispatch|ship|send)\s+(?:to|at|in)\s+([A-Z][A-Za-z ]{2,40})", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"\b(?:location|site|city)[:\s-]+([A-Z][A-Za-z ]{2,40})", text)
    return match.group(1).strip() if match else ""


def _missing_fields(product: str, quantity: str, deadline: str | None, location: str, needs_specs: bool) -> list[str]:
    if not needs_specs:
        return []
    missing = []
    if not product:
        missing.append("product/spec")
    if not quantity:
        missing.append("quantity")
    if not deadline:
        missing.append("delivery date")
    if not location:
        missing.append("delivery location")
    return missing


def _workflow_stage(text: str, is_rfq: bool, is_order: bool, is_negotiation: bool, is_blocking: bool) -> str:
    if is_blocking:
        return "blocked"
    if is_order:
        return "order_confirmed"
    if is_negotiation:
        return "negotiation"
    if any(word in text for word in ("dispatch", "ready", "vehicle", "transport", "delivery")):
        return "dispatch"
    if any(word in text for word in PRODUCTION_KEYWORDS):
        return "production"
    if is_rfq:
        return "rfq"
    if "follow up" in text or "any update" in text:
        return "follow_up"
    return "general"


def _priority_score(
    text: str,
    quantity: str,
    deadline: str | None,
    is_rfq: bool,
    is_order: bool,
    is_blocking: bool,
    attachments: list[str],
    timestamp: datetime | None,
) -> int:
    score = 20
    if is_order:
        score += 30
    if is_rfq:
        score += 18
    if is_blocking:
        score += 25
    if any(word in text for word in PRIORITY_KEYWORDS["high"]):
        score += 25
    if deadline:
        score += 18
    number = re.search(r"\d+", quantity or "")
    if number and int(number.group(0).replace(",", "")) >= 100:
        score += 12
    if attachments:
        score += 8
    if timestamp:
        age_hours = (datetime.now(timezone.utc) - timestamp).total_seconds() / 3600
        if age_hours >= 12:
            score += 8
        if age_hours >= 24:
            score += 10
    return max(0, min(100, score))


def _production_task_text(sender: str, subject: str, production: dict[str, Any]) -> str:
    product = production.get("product_type") or "order details"
    quantity = production.get("quantity")
    stage = production.get("workflow_stage")
    if stage == "rfq":
        return f"Prepare quotation for {quantity + ' ' if quantity else ''}{product}"
    if stage == "order_confirmed":
        return f"Plan production for {quantity + ' ' if quantity else ''}{product}"
    if stage == "blocked":
        return f"Unblock production/RFQ: collect {production.get('missing_fields') or 'pending details'}"
    if stage == "dispatch":
        return f"Coordinate dispatch for {quantity + ' ' if quantity else ''}{product}"
    return f"Respond to {sender} about {subject or product}"


def _production_next_action(production: dict[str, Any]) -> str:
    if production.get("missing_fields"):
        return f"Ask client for missing details: {production['missing_fields']}."
    stage = production.get("workflow_stage")
    if stage == "rfq":
        return "Check specs, calculate rate, and send quotation."
    if stage == "order_confirmed":
        return "Confirm specs, production slot, and delivery timeline."
    if stage == "dispatch":
        return "Confirm readiness, vehicle/logistics, and dispatch time."
    if stage == "blocked":
        return "Resolve the blocking detail before production proceeds."
    return "Reply with the next required production or commercial update."


def _production_priority_reason(production: dict[str, Any], priority: str) -> str:
    reasons = []
    if production.get("is_order"):
        reasons.append("confirmed order")
    if production.get("is_rfq"):
        reasons.append("RFQ/inquiry")
    if production.get("quantity"):
        reasons.append(f"quantity {production['quantity']}")
    if production.get("is_blocking"):
        reasons.append("blocking or missing production details")
    if production.get("priority_score"):
        reasons.append(f"score {production['priority_score']}/100")
    return f"{priority.upper()} because " + ", ".join(reasons or ["business email needs response"])


def _production_reply_draft(production: dict[str, Any]) -> str:
    if production.get("missing_fields"):
        return f"Thank you. Please share the missing details ({production['missing_fields']}) so we can proceed."
    if production.get("workflow_stage") == "rfq":
        return "Thank you for the inquiry. We are checking the specifications and will share the quotation shortly."
    if production.get("workflow_stage") == "order_confirmed":
        return "Order noted. We will confirm the production schedule and delivery timeline shortly."
    return "Thank you. We will check and update you shortly."


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
