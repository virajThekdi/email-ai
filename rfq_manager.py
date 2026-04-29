from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

from quote_parser import parse_quotation_text
from supabase_client import get_setting, get_supabase


def create_rfq(title: str, due_date: str | None, notes: str, vendors: str, items: str) -> int:
    supabase = get_supabase()
    rfq = (
        supabase.table("rfqs")
        .insert({"title": title, "due_date": due_date or None, "notes": notes, "status": "draft"})
        .execute()
        .data[0]
    )
    rfq_id = rfq["id"]
    code = f"RFQ-{rfq_id:05d}"
    supabase.table("rfqs").update({"rfq_code": code}).eq("id", rfq_id).execute()

    for line in vendors.splitlines():
        email = line.strip()
        if not email:
            continue
        supabase.table("rfq_vendors").insert({"rfq_id": rfq_id, "vendor_email": email}).execute()

    for line in items.splitlines():
        item = line.strip()
        if not item:
            continue
        supabase.table("rfq_items").insert({"rfq_id": rfq_id, "item_name": item}).execute()

    return rfq_id


def list_rfqs() -> list[dict]:
    return get_supabase().table("rfqs").select("*").order("created_at", desc=True).limit(50).execute().data


def get_rfq_detail(rfq_id: int) -> dict:
    supabase = get_supabase()
    rfq = supabase.table("rfqs").select("*").eq("id", rfq_id).single().execute().data
    vendors = supabase.table("rfq_vendors").select("*").eq("rfq_id", rfq_id).execute().data
    items = supabase.table("rfq_items").select("*").eq("rfq_id", rfq_id).execute().data
    responses = supabase.table("rfq_responses").select("*").eq("rfq_id", rfq_id).order("created_at", desc=True).execute().data
    return {"rfq": rfq, "vendors": vendors, "items": items, "responses": responses}


def send_rfq(rfq_id: int) -> int:
    detail = get_rfq_detail(rfq_id)
    rfq = detail["rfq"]
    sent = 0
    for vendor in detail["vendors"]:
        if vendor["status"] not in {"pending", "draft"}:
            continue
        _send_email(
            to_email=vendor["vendor_email"],
            subject=f"{rfq['rfq_code']} - Request for Quotation - {rfq['title']}",
            body=_rfq_email_body(rfq, detail["items"]),
        )
        get_supabase().table("rfq_vendors").update({"status": "sent"}).eq("id", vendor["id"]).execute()
        sent += 1
    if sent:
        get_supabase().table("rfqs").update({"status": "sent", "sent_at": datetime.now(timezone.utc).isoformat()}).eq("id", rfq_id).execute()
    return sent


def process_rfq_reply(email: dict) -> None:
    rfq_code = _extract_rfq_code(email.get("subject", "") + "\n" + email.get("body", ""))
    if not rfq_code:
        return
    supabase = get_supabase()
    rfqs = supabase.table("rfqs").select("*").eq("rfq_code", rfq_code).limit(1).execute().data
    if not rfqs:
        return
    rfq = rfqs[0]
    sender_email = parseaddr(email.get("sender", ""))[1].lower()
    vendors = supabase.table("rfq_vendors").select("*").eq("rfq_id", rfq["id"]).execute().data
    vendor = next((item for item in vendors if item["vendor_email"].lower() == sender_email), None)

    response_payload = {
        "rfq_id": rfq["id"],
        "vendor_id": vendor["id"] if vendor else None,
        "message_id": email["message_id"],
        "response_text": (email.get("body", "") + "\n" + email.get("attachment_text", ""))[:12000],
        "attachment_names": ", ".join(email.get("attachment_names") or []),
        "parsed_format": "email_attachment_text" if email.get("attachment_text") else "email_body",
    }
    try:
        response = supabase.table("rfq_responses").insert(response_payload).execute().data[0]
    except Exception:
        return

    if vendor:
        supabase.table("rfq_vendors").update({"status": "responded", "responded_at": datetime.now(timezone.utc).isoformat()}).eq("id", vendor["id"]).execute()

    for item in parse_quotation_text(response_payload["response_text"]):
        item["response_id"] = response["id"]
        supabase.table("quotation_items").insert(item).execute()


def rfq_status_rows() -> list[dict]:
    rows = []
    for rfq in list_rfqs():
        detail = get_rfq_detail(rfq["id"])
        vendors = detail["vendors"]
        rows.append(
            {
                "code": rfq.get("rfq_code"),
                "title": rfq["title"],
                "status": rfq["status"],
                "vendors": len(vendors),
                "responded": len([vendor for vendor in vendors if vendor["status"] == "responded"]),
                "pending": len([vendor for vendor in vendors if vendor["status"] in {"pending", "sent", "followed_up"}]),
                "due_date": rfq.get("due_date"),
            }
        )
    return rows


def _send_email(to_email: str, subject: str, body: str) -> None:
    from_email = get_setting("EMAIL_ADDRESS")
    password = get_setting("EMAIL_PASSWORD")
    host = get_setting("EMAIL_SMTP_HOST") or "smtp.gmail.com"
    port = int(get_setting("EMAIL_SMTP_PORT") or "587")
    if not from_email or not password:
        raise RuntimeError("EMAIL_ADDRESS and EMAIL_PASSWORD are required to send RFQs.")
    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(from_email, password)
        smtp.send_message(message)


def _rfq_email_body(rfq: dict, items: list[dict]) -> str:
    lines = [
        f"Dear Vendor,",
        "",
        f"Please share your quotation for {rfq['title']}.",
        f"RFQ Code: {rfq['rfq_code']}",
        "",
        "Items:",
    ]
    lines.extend(f"- {item['item_name']}" for item in items)
    if rfq.get("due_date"):
        lines.extend(["", f"Please reply before: {rfq['due_date']}"])
    if rfq.get("notes"):
        lines.extend(["", f"Notes: {rfq['notes']}"])
    lines.extend(["", "Regards"])
    return "\n".join(lines)


def _extract_rfq_code(text: str) -> str | None:
    import re

    match = re.search(r"\bRFQ-\d{5}\b", text or "", re.IGNORECASE)
    return match.group(0).upper() if match else None
