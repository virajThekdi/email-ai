from __future__ import annotations

from datetime import datetime, timedelta, timezone

from postgrest.exceptions import APIError

from supabase_client import get_supabase


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def store_email(email: dict) -> bool:
    payload = {
        "message_id": email["message_id"],
        "thread_id": email["thread_id"],
        "sender": email.get("sender", ""),
        "recipients": email.get("recipients", ""),
        "subject": email.get("subject", ""),
        "body": email.get("body", ""),
        "attachment_names": ", ".join(email.get("attachment_names") or []),
        "attachment_text": email.get("attachment_text", ""),
        "attachment_summary": email.get("attachment_summary", ""),
        "is_sent": email.get("is_sent", False),
        "timestamp": email["timestamp"].isoformat(),
    }
    response = _write_with_schema_fallback(
        "emails",
        payload,
        lambda clean_payload: get_supabase()
        .table("emails")
        .upsert(clean_payload, on_conflict="message_id", ignore_duplicates=True),
    )
    return bool(response.data)


def create_task_from_email(email: dict, analysis: dict) -> None:
    task_texts = [analysis.get("task_text")]
    task_texts.extend(analysis.get("tasks") or [])
    seen: set[str] = set()
    for task_text in task_texts[:5]:
        if not task_text:
            continue
        normalized = str(task_text).strip()
        if not normalized or normalized.lower() in seen or _task_exists(email["thread_id"], normalized):
            continue
        seen.add(normalized.lower())
        payload = {
            "thread_id": email["thread_id"],
            "task_text": normalized[:260],
            "summary": analysis.get("summary"),
            "priority": analysis.get("priority", "medium"),
            "status": "pending",
            "deadline": analysis.get("deadline"),
            "deadline_date": _clean_date(analysis.get("deadline_date")),
            "source_message_id": email["message_id"],
            "next_action": analysis.get("next_action"),
            "priority_reason": analysis.get("priority_reason"),
            "suggested_reply": analysis.get("suggested_reply"),
            "category": analysis.get("category"),
            "client_name": analysis.get("client_name"),
            "contact_name": analysis.get("contact_name"),
            "intent": analysis.get("intent"),
            "sentiment": analysis.get("sentiment"),
            "escalation_risk": analysis.get("escalation_risk"),
            "attachment_summary": analysis.get("attachment_summary") or email.get("attachment_summary"),
            "has_attachments": bool(email.get("attachment_names")),
            "workflow_stage": analysis.get("workflow_stage"),
            "product_type": analysis.get("product_type"),
            "quantity": analysis.get("quantity"),
            "delivery_location": analysis.get("delivery_location"),
            "is_rfq": bool(analysis.get("is_rfq")),
            "is_order": bool(analysis.get("is_order")),
            "is_blocking": bool(analysis.get("is_blocking")),
            "missing_fields": analysis.get("missing_fields"),
            "priority_score": int(analysis.get("priority_score") or 0),
            "task_type": "reply",
        }
        _write_with_schema_fallback("tasks", payload, lambda clean_payload: get_supabase().table("tasks").insert(clean_payload))


def store_reply_memory(email: dict) -> None:
    if not email.get("is_sent") or not email.get("body"):
        return
    body = email["body"].strip()
    if len(body) < 20:
        return
    payload = {
        "sender_domain": _email_domain(email.get("recipients") or email.get("sender") or ""),
        "recipient": email.get("recipients", ""),
        "subject": email.get("subject", ""),
        "reply_text": body[:2500],
        "category": None,
    }
    try:
        _write_with_schema_fallback(
            "ai_memories",
            payload,
            lambda clean_payload: get_supabase().table("ai_memories").insert(clean_payload),
        )
    except APIError:
        return


def get_reply_context(sender: str, subject: str = "", limit: int = 4) -> str:
    domain = _email_domain(sender)
    if not domain:
        return ""
    try:
        memories = (
            get_supabase()
            .table("ai_memories")
            .select("subject,reply_text,created_at")
            .eq("sender_domain", domain)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )
    except APIError:
        return ""
    if not memories:
        return ""
    return "\n\n".join(f"Previous reply about {item.get('subject') or 'email'}:\n{item['reply_text']}" for item in memories)


def complete_tasks_for_sent_replies() -> int:
    supabase = get_supabase()
    pending = (
        supabase.table("tasks")
        .select("id,thread_id,created_at,source_message_id")
        .eq("status", "pending")
        .execute()
        .data
    )
    completed = 0
    for task in pending:
        compare_from = task["created_at"]
        if task.get("source_message_id"):
            source = (
                supabase.table("emails")
                .select("timestamp")
                .eq("message_id", task["source_message_id"])
                .limit(1)
                .execute()
                .data
            )
            if source:
                compare_from = source[0]["timestamp"]
        sent = (
            supabase.table("emails")
            .select("message_id,timestamp")
            .eq("thread_id", task["thread_id"])
            .eq("is_sent", True)
            .gt("timestamp", compare_from)
            .limit(1)
            .execute()
            .data
        )
        if sent:
            mark_task_completed(task["id"])
            completed += 1
    return completed


def mark_task_completed(task_id: int) -> None:
    (
        get_supabase()
        .table("tasks")
        .update({"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", task_id)
        .execute()
    )


def create_follow_up_tasks(after_hours: int = 24) -> int:
    supabase = get_supabase()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=after_hours)
    sent_emails = (
        supabase.table("emails")
        .select("thread_id,subject,timestamp,sender")
        .eq("is_sent", True)
        .lte("timestamp", cutoff.isoformat())
        .order("timestamp", desc=True)
        .limit(200)
        .execute()
        .data
    )

    created = 0
    seen_threads: set[str] = set()
    for email in sent_emails:
        thread_id = email["thread_id"]
        if thread_id in seen_threads or _thread_has_open_task(thread_id):
            continue
        seen_threads.add(thread_id)
        latest_inbound = (
            supabase.table("emails")
            .select("message_id")
            .eq("thread_id", thread_id)
            .eq("is_sent", False)
            .gt("timestamp", email["timestamp"])
            .limit(1)
            .execute()
            .data
        )
        if latest_inbound:
            continue
        payload = {
            "thread_id": thread_id,
            "task_text": f"Follow up on: {email.get('subject') or 'sent email'}",
            "summary": "No reply has been received after your last sent email.",
            "priority": "medium",
            "status": "pending",
            "next_action": "Send a short follow-up asking whether they need anything else.",
            "workflow_stage": "follow_up",
            "priority_score": 55,
            "task_type": "follow_up",
        }
        supabase.table("tasks").insert(payload).execute()
        created += 1
    return created


def get_dashboard_tasks() -> dict[str, list[dict]]:
    supabase = get_supabase()
    pending = supabase.table("tasks").select("*").in_("status", ["pending", "waiting_for_client"]).execute().data
    completed = (
        supabase.table("tasks")
        .select("*")
        .eq("status", "completed")
        .order("completed_at", desc=True)
        .limit(50)
        .execute()
        .data
    )
    pending.sort(
        key=lambda row: (
            -(row.get("priority_score") or 0),
            PRIORITY_ORDER.get(row.get("priority"), 3),
            row.get("deadline_date") or "9999-12-31",
            row.get("created_at") or "",
        )
    )
    return {
        "pending": [task for task in pending if task.get("task_type") != "follow_up"],
        "follow_ups": [task for task in pending if task.get("task_type") == "follow_up"],
        "completed": completed,
    }


def _thread_has_open_task(thread_id: str) -> bool:
    existing = (
        get_supabase()
        .table("tasks")
        .select("id")
        .eq("thread_id", thread_id)
        .eq("status", "pending")
        .limit(1)
        .execute()
        .data
    )
    return bool(existing)


def _task_exists(thread_id: str, task_text: str) -> bool:
    existing = (
        get_supabase()
        .table("tasks")
        .select("id")
        .eq("thread_id", thread_id)
        .eq("task_text", task_text[:260])
        .neq("status", "completed")
        .limit(1)
        .execute()
        .data
    )
    return bool(existing)


def _email_domain(value: str) -> str:
    if "@" not in value:
        return ""
    first = value.split(",", 1)[0]
    return first.split("@", 1)[1].split(">", 1)[0].strip().lower()


def _clean_date(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _write_with_schema_fallback(table: str, payload: dict, build_query):
    clean_payload = dict(payload)
    while True:
        try:
            return build_query(clean_payload).execute()
        except APIError as exc:
            missing_column = _missing_column_from_error(exc)
            if not missing_column or missing_column not in clean_payload:
                raise
            clean_payload.pop(missing_column, None)


def _missing_column_from_error(exc: APIError) -> str | None:
    message = str(exc)
    marker = "Could not find the '"
    if marker not in message:
        return None
    return message.split(marker, 1)[1].split("' column", 1)[0]
