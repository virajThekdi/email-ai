from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from email.utils import parseaddr


RFQ_WORDS = ("rfq", "quotation", "quote", "rate", "price", "pricing", "offer", "enquiry", "inquiry")
ORDER_WORDS = ("po", "purchase order", "confirmed", "confirm order", "order confirmation", "final order")
CANCEL_WORDS = ("cancel", "cancelled", "canceled", "hold order", "stop production", "on hold")
NEGOTIATION_WORDS = ("discount", "reduce", "negotiate", "final rate", "best price", "lower price")
DISPATCH_WORDS = ("dispatch", "delivery", "deliver", "transport", "ready", "pickup", "shipment")
BLOCKING_WORDS = ("missing", "pending", "awaiting", "material required", "shortage", "drawing pending", "specification pending")
ESCALATION_WORDS = ("delay", "late", "urgent", "asap", "immediately", "complaint", "any update", "reminder")
VIP_DOMAINS = tuple(domain.strip().lower() for domain in re.split(r"[,;]", __import__("os").getenv("VIP_CLIENT_DOMAINS", "")) if domain.strip())


def analyze_production_email(subject: str, body: str, sender: str, attachment_names: list[str] | None = None) -> dict:
    text = _clean(f"{subject}\n{body}")
    lower = text.lower()
    attachment_names = attachment_names or []
    client = _client_from_sender(sender)
    products = extract_products(text)
    quantities = extract_quantities(text)
    deadline = extract_delivery_deadline(text)
    location = extract_location(text)
    workflow_stage = detect_stage(lower)
    missing = missing_rfq_fields(products, quantities, deadline)
    blocking = bool(missing or any(word in lower for word in BLOCKING_WORDS))
    priority_score, reason = priority_score_for(lower, quantities, deadline, sender, workflow_stage, blocking)
    priority = "high" if priority_score >= 70 else "medium" if priority_score >= 40 else "low"
    task_text = build_task_text(workflow_stage, products, quantities, client)

    return {
        "workflow_stage": workflow_stage,
        "product_type": ", ".join(products),
        "quantity": ", ".join(quantities),
        "delivery_location": location,
        "deadline": deadline.get("label"),
        "deadline_date": deadline.get("date"),
        "is_rfq": workflow_stage == "rfq",
        "is_order": workflow_stage == "order",
        "is_blocking": blocking,
        "missing_fields": ", ".join(missing),
        "priority_score": priority_score,
        "priority": priority,
        "priority_reason": reason,
        "client_name": client,
        "category": workflow_stage,
        "intent": workflow_stage,
        "task_text": task_text,
        "next_action": next_action(workflow_stage, missing, blocking),
        "summary": production_summary(workflow_stage, products, quantities, deadline, location, attachment_names),
        "attachment_summary": attachment_summary(attachment_names, lower),
        "escalation_risk": "high" if priority_score >= 80 or "complaint" in lower else "medium" if blocking else "low",
    }


def detect_stage(lower: str) -> str:
    if any(word in lower for word in CANCEL_WORDS):
        return "hold"
    if any(word in lower for word in ORDER_WORDS):
        return "order"
    if any(word in lower for word in DISPATCH_WORDS):
        return "dispatch"
    if any(word in lower for word in NEGOTIATION_WORDS):
        return "negotiation"
    if any(word in lower for word in RFQ_WORDS):
        return "rfq"
    if any(word in lower for word in ("drawing", "specification", "technical")):
        return "specification"
    return "general"


def extract_products(text: str) -> list[str]:
    patterns = [
        r"\b\d+(?:\.\d+)?\s*mm\s+(?:clear\s+|toughened\s+|tempered\s+|laminated\s+|frosted\s+|glass\s+)*glass\b",
        r"\b(?:clear|toughened|tempered|laminated|frosted|float|mirror)\s+glass\b",
        r"\bglass\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            product = " ".join(match.split())
            if product.lower() not in [item.lower() for item in found]:
                found.append(product)
    return found[:5]


def extract_quantities(text: str) -> list[str]:
    pattern = r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:pcs|pieces|sheets|sheet|kg|kgs|tons|ton|sqft|sqm|m2|nos|no)\b"
    return list(dict.fromkeys(re.findall(pattern, text, flags=re.IGNORECASE)))[:6]


def extract_delivery_deadline(text: str) -> dict:
    lower = text.lower()
    today = date.today()
    if "today" in lower:
        return {"label": "today", "date": today.isoformat()}
    if "tomorrow" in lower:
        return {"label": "tomorrow", "date": (today + timedelta(days=1)).isoformat()}
    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, weekday in weekday_map.items():
        if name in lower:
            days = (weekday - today.weekday()) % 7 or 7
            return {"label": name, "date": (today + timedelta(days=days)).isoformat()}
    match = re.search(r"\b(?:by|before|on|delivery)\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", lower)
    if match:
        parsed = _parse_date(match.group(1))
        return {"label": match.group(1), "date": parsed}
    return {"label": None, "date": None}


def extract_location(text: str) -> str:
    match = re.search(r"\b(?:delivery|deliver|dispatch|ship)\s+(?:to|at|in)\s+([A-Za-z][A-Za-z\s-]{2,40})", text, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip(" .,\n")


def missing_rfq_fields(products: list[str], quantities: list[str], deadline: dict) -> list[str]:
    missing = []
    if not products:
        missing.append("product/specification")
    if not quantities:
        missing.append("quantity")
    if not deadline.get("date") and not deadline.get("label"):
        missing.append("delivery date")
    return missing


def priority_score_for(lower: str, quantities: list[str], deadline: dict, sender: str, stage: str, blocking: bool) -> tuple[int, str]:
    score = 20
    reasons = []
    if stage in {"order", "dispatch"}:
        score += 25
        reasons.append("confirmed order/dispatch signal")
    if stage == "rfq":
        score += 15
        reasons.append("RFQ detected")
    if any(word in lower for word in ("urgent", "asap", "immediate", "today")):
        score += 25
        reasons.append("urgent keyword")
    if deadline.get("date"):
        days = (date.fromisoformat(deadline["date"]) - date.today()).days
        if days <= 1:
            score += 25
            reasons.append("deadline within 24 hours")
        elif days <= 3:
            score += 15
            reasons.append("deadline within 3 days")
    if _bulk_quantity(quantities):
        score += 15
        reasons.append("bulk quantity")
    if _sender_domain(sender) in VIP_DOMAINS:
        score += 15
        reasons.append("VIP client")
    if blocking:
        score += 15
        reasons.append("production-blocking or incomplete details")
    return min(score, 100), ", ".join(reasons) or "standard production priority"


def build_task_text(stage: str, products: list[str], quantities: list[str], client: str) -> str:
    item = products[0] if products else "order details"
    qty = quantities[0] if quantities else ""
    if stage == "rfq":
        return f"Prepare quotation for {qty} {item} - {client}".strip()
    if stage == "order":
        return f"Start production planning for {qty} {item} - {client}".strip()
    if stage == "dispatch":
        return f"Coordinate dispatch for {qty} {item} - {client}".strip()
    if stage == "hold":
        return f"Review hold/cancellation instruction for {client}".strip()
    return f"Review production email from {client}".strip()


def next_action(stage: str, missing: list[str], blocking: bool) -> str:
    if missing:
        return f"Ask client for missing details: {', '.join(missing)}."
    if stage == "rfq":
        return "Prepare rate/quotation and reply to client."
    if stage == "order":
        return "Confirm specs, quantity, and deadline before production planning."
    if stage == "dispatch":
        return "Check readiness and coordinate logistics."
    if blocking:
        return "Resolve blocking information before production moves ahead."
    return "Reply with the next production update."


def production_summary(stage: str, products: list[str], quantities: list[str], deadline: dict, location: str, attachments: list[str]) -> str:
    pieces = [stage.upper()]
    if quantities:
        pieces.append(", ".join(quantities))
    if products:
        pieces.append(", ".join(products))
    if deadline.get("label"):
        pieces.append(f"deadline {deadline['label']}")
    if location:
        pieces.append(f"delivery {location}")
    if attachments:
        pieces.append("attachments present")
    return " | ".join(pieces)


def attachment_summary(names: list[str], lower: str) -> str:
    if not names:
        return ""
    hints = []
    if any(word in lower for word in ("drawing", "size", "spec", "technical")):
        hints.append("possible drawing/specification")
    if any(word in lower for word in ("invoice", "po", "purchase order")):
        hints.append("possible commercial/order document")
    return f"Attachments: {', '.join(names)}" + (f" ({', '.join(hints)})" if hints else "")


def _client_from_sender(sender: str) -> str:
    name, address = parseaddr(sender)
    if name:
        return name.strip('"')
    if address:
        return address.split("@", 1)[0]
    return sender or "client"


def _sender_domain(sender: str) -> str:
    _name, address = parseaddr(sender)
    return address.split("@", 1)[1].lower() if "@" in address else ""


def _bulk_quantity(quantities: list[str]) -> bool:
    for quantity in quantities:
        number = re.search(r"\d+(?:,\d{3})*", quantity)
        if number and int(number.group(0).replace(",", "")) >= 100:
            return True
    return False


def _parse_date(value: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
