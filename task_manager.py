from __future__ import annotations

from datetime import datetime, timedelta, timezone

from supabase_client import get_supabase


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def store_email(email: dict) -> bool:
    payload = {
        "message_id": email["message_id"],
        "thread_id": email["thread_id"],
        "sender": email.get("sender", ""),
        "subject": email.get("subject", ""),
        "body": email.get("body", ""),
        "is_sent": email.get("is_sent", False),
        "timestamp": email["timestamp"].isoformat(),
    }
    response = (
        get_supabase()
        .table("emails")
        .upsert(payload, on_conflict="message_id", ignore_duplicates=True)
        .execute()
    )
    return bool(response.data)


def create_task_from_email(email: dict, analysis: dict) -> None:
    if _thread_has_open_task(email["thread_id"]):
        return
    payload = {
        "thread_id": email["thread_id"],
        "task_text": analysis["task_text"],
        "summary": analysis["summary"],
        "priority": analysis["priority"],
        "status": "pending",
        "deadline": analysis.get("deadline"),
        "source_message_id": email["message_id"],
        "next_action": analysis.get("next_action"),
        "task_type": "reply",
    }
    get_supabase().table("tasks").insert(payload).execute()


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
            "task_type": "follow_up",
        }
        supabase.table("tasks").insert(payload).execute()
        created += 1
    return created


def get_dashboard_tasks() -> dict[str, list[dict]]:
    supabase = get_supabase()
    pending = supabase.table("tasks").select("*").eq("status", "pending").execute().data
    completed = (
        supabase.table("tasks")
        .select("*")
        .eq("status", "completed")
        .order("completed_at", desc=True)
        .limit(50)
        .execute()
        .data
    )
    pending.sort(key=lambda row: (PRIORITY_ORDER.get(row.get("priority"), 3), row.get("created_at") or ""))
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
