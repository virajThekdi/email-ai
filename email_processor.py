from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ai_utils import analyze_email, strip_email_noise
from supabase_client import get_state, set_state
from task_manager import complete_tasks_for_sent_replies, create_follow_up_tasks, create_task_from_email, store_email


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def run() -> None:
    provider = os.getenv("EMAIL_PROVIDER", "outlook").lower().strip()
    if provider == "outlook":
        _run_outlook()
    elif provider == "gmail":
        _run_gmail()
    else:
        raise RuntimeError("EMAIL_PROVIDER must be 'outlook' or 'gmail'.")


def _process_emails(emails: list[dict]) -> tuple[int, int]:
    created_candidates = 0
    max_position = 0
    for email in emails:
        max_position = max(max_position, email.get("sync_position", 0))
        inserted = store_email(email)
        if inserted and not email["is_sent"]:
            analysis = analyze_email(email["subject"], email["body"], email["sender"], email["timestamp"])
            create_task_from_email(email, analysis)
            created_candidates += 1
    return created_candidates, max_position


def _finish_run(provider: str, fetched: int, created_candidates: int, max_position: int, state_key: str) -> None:
    completed = complete_tasks_for_sent_replies()
    follow_ups = create_follow_up_tasks(after_hours=int(os.getenv("FOLLOW_UP_AFTER_HOURS", "24")))

    if max_position:
        set_state(state_key, str(max_position))

    print(
        json.dumps(
            {
                "provider": provider,
                "fetched": fetched,
                "new_inbound_candidates": created_candidates,
                "auto_completed": completed,
                "follow_ups_created": follow_ups,
                "last_sync_position": max_position,
            },
            indent=2,
        )
    )


def _run_gmail() -> None:
    service = _gmail_service()
    since_ms = int(get_state("gmail_last_internal_date_ms", "0"))
    messages = _list_messages(service, since_ms)
    max_seen = since_ms
    emails = []

    for message_ref in messages:
        email = _get_message(service, message_ref["id"])
        if email["internal_date_ms"] <= since_ms:
            continue
        max_seen = max(max_seen, email["internal_date_ms"])
        email["sync_position"] = email["internal_date_ms"]
        emails.append(email)

    created_candidates, max_position = _process_emails(emails)
    _finish_run("gmail", len(messages), created_candidates, max_position or max_seen, "gmail_last_internal_date_ms")


def _run_outlook() -> None:
    token = _microsoft_token()
    since_position = int(get_state("outlook_last_received_ms", "0"))
    emails = _list_outlook_messages(token, since_position)
    created_candidates, max_position = _process_emails(emails)
    _finish_run("outlook", len(emails), created_candidates, max_position or since_position, "outlook_last_received_ms")


def _microsoft_token() -> str:
    tenant_id = _required_env("MICROSOFT_TENANT_ID")
    client_id = _required_env("MICROSOFT_CLIENT_ID")
    client_secret = _required_env("MICROSOFT_CLIENT_SECRET")
    response = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _list_outlook_messages(token: str, since_position: int) -> list[dict]:
    mailbox = quote(_required_env("MICROSOFT_MAILBOX_USER"))
    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.body-content-type="text"'}
    since_dt = datetime.fromtimestamp(since_position / 1000, tz=timezone.utc) if since_position else None
    folders = [("inbox", False), ("sentitems", True)]
    emails: list[dict] = []

    for folder, is_sent in folders:
        select = "id,conversationId,from,toRecipients,subject,body,bodyPreview,receivedDateTime,sentDateTime"
        params = {"$top": "50", "$select": select}
        if since_dt:
            date_field = "sentDateTime" if is_sent else "receivedDateTime"
            params["$filter"] = f"{date_field} ge {since_dt.isoformat().replace('+00:00', 'Z')}"
        else:
            params["$orderby"] = f"{'sentDateTime' if is_sent else 'receivedDateTime'} desc"
        url = f"{GRAPH_BASE_URL}/users/{mailbox}/mailFolders/{folder}/messages"
        page_count = 0
        while url and page_count < int(os.getenv("OUTLOOK_MAX_PAGES", "4")):
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            for message in data.get("value", []):
                email = _outlook_message_to_email(message, is_sent)
                if email["sync_position"] > since_position:
                    emails.append(email)
            url = data.get("@odata.nextLink")
            params = None
            page_count += 1

    emails.sort(key=lambda item: item["sync_position"])
    return emails


def _outlook_message_to_email(message: dict, is_sent: bool) -> dict:
    timestamp_text = message.get("sentDateTime") if is_sent else message.get("receivedDateTime")
    timestamp = _parse_graph_datetime(timestamp_text)
    sender = _outlook_sender(message)
    content = (message.get("body") or {}).get("content") or message.get("bodyPreview") or ""
    return {
        "message_id": f"outlook:{message['id']}",
        "thread_id": f"outlook:{message.get('conversationId') or message['id']}",
        "sender": sender,
        "subject": message.get("subject") or "",
        "body": strip_email_noise(content),
        "is_sent": is_sent,
        "timestamp": timestamp,
        "sync_position": int(timestamp.timestamp() * 1000),
    }


def _outlook_sender(message: dict) -> str:
    sender = (message.get("from") or {}).get("emailAddress") or {}
    return sender.get("address") or sender.get("name") or ""


def _parse_graph_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _gmail_service():
    token_json = os.getenv("GMAIL_TOKEN_JSON")
    token_path = os.getenv("GMAIL_TOKEN_PATH", "token.json")
    if token_json:
        token_info = json.loads(token_json)
    elif os.path.exists(token_path):
        with open(token_path, "r", encoding="utf-8") as file:
            token_info = json.load(file)
    else:
        raise RuntimeError("Set GMAIL_TOKEN_JSON or provide token.json with Gmail OAuth credentials.")

    credentials = Credentials.from_authorized_user_info(token_info, SCOPES)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _list_messages(service, since_ms: int) -> list[dict]:
    if since_ms:
        after_seconds = max(0, (since_ms // 1000) - 86400)
        query = f"in:anywhere after:{after_seconds}"
    else:
        query = "in:anywhere newer_than:30d"
    results: list[dict] = []
    page_token = None
    max_pages = int(os.getenv("GMAIL_MAX_PAGES", "5"))

    for _ in range(max_pages):
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=100)
            .execute()
        )
        results.extend(response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


def _get_message(service, message_id: str) -> dict:
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {item["name"].lower(): item["value"] for item in message["payload"].get("headers", [])}
    labels = set(message.get("labelIds", []))
    timestamp = _timestamp_from_headers(headers, int(message.get("internalDate", "0")))
    return {
        "message_id": message["id"],
        "thread_id": message["threadId"],
        "sender": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "body": strip_email_noise(_extract_body(message.get("payload", {}))),
        "is_sent": "SENT" in labels,
        "timestamp": timestamp,
        "internal_date_ms": int(message.get("internalDate", "0")),
    }


def _timestamp_from_headers(headers: dict[str, str], internal_date_ms: int) -> datetime:
    if headers.get("date"):
        try:
            parsed = parsedate_to_datetime(headers["date"])
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)


def _extract_body(payload: dict) -> str:
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data and mime_type in {"text/plain", "text/html"}:
        return _decode_body(body_data)
    for part in payload.get("parts", []) or []:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _decode_body(part["body"]["data"])
    for part in payload.get("parts", []) or []:
        nested = _extract_body(part)
        if nested:
            return nested
    return ""


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


if __name__ == "__main__":
    run()
