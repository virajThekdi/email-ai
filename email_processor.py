from __future__ import annotations

import base64
import imaplib
import json
import os
import re
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from attachment_utils import extract_attachments_from_message
from ai_utils import analyze_email, is_actionable_email, rule_based_analysis, should_use_ai, strip_email_noise
from supabase_client import get_setting, get_state, set_state
from task_manager import (
    complete_tasks_for_sent_replies,
    create_follow_up_tasks,
    create_task_from_email,
    get_reply_context,
    store_email,
    store_reply_memory,
)


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def run() -> None:
    provider = (get_setting("EMAIL_PROVIDER") or "simple_outlook").lower().strip()
    if provider == "outlook":
        _run_outlook()
    elif provider == "simple_outlook":
        _run_simple_outlook()
    elif provider == "gmail":
        _run_gmail()
    else:
        raise RuntimeError("EMAIL_PROVIDER must be 'simple_outlook', 'outlook', or 'gmail'.")


def _process_emails(emails: list[dict]) -> tuple[int, int]:
    created_candidates = 0
    max_position = 0
    ai_calls_used = 0
    ai_call_limit = int(get_setting("AI_MAX_CALLS_PER_RUN") or "8")
    for email in emails:
        max_position = max(max_position, email.get("sync_position", 0))
        inserted = store_email(email)
        if inserted and email["is_sent"]:
            store_reply_memory(email)
        if inserted and not email["is_sent"]:
            actionable, _reason = is_actionable_email(
                email["subject"],
                email["body"],
                email["sender"],
                email.get("attachment_names") or [],
            )
            if not actionable:
                continue
            use_ai = should_use_ai(
                email["subject"],
                email["body"],
                email["sender"],
                email.get("attachment_text", ""),
                email.get("attachment_names") or [],
            )
            if use_ai and ai_calls_used < ai_call_limit:
                reply_context = get_reply_context(email["sender"], email["subject"])
                analysis = analyze_email(
                    email["subject"],
                    email["body"],
                    email["sender"],
                    email["timestamp"],
                    attachment_text=email.get("attachment_text", ""),
                    attachment_names=email.get("attachment_names") or [],
                    reply_context=reply_context,
                )
                ai_calls_used += 1
            else:
                analysis = rule_based_analysis(
                    email["subject"],
                    email["body"],
                    email["sender"],
                    email["timestamp"],
                    attachment_text=email.get("attachment_text", ""),
                    attachment_names=email.get("attachment_names") or [],
                )
            create_task_from_email(email, analysis)
            created_candidates += 1
    return created_candidates, max_position


def _finish_run(provider: str, fetched: int, created_candidates: int, max_position: int, state_key: str) -> None:
    completed = complete_tasks_for_sent_replies()
    follow_ups = create_follow_up_tasks(after_hours=int(get_setting("FOLLOW_UP_AFTER_HOURS") or "24"))

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


def _run_simple_outlook() -> None:
    since_position = int(get_state("simple_outlook_last_received_ms", "0"))
    emails = _list_imap_messages(since_position)
    created_candidates, max_position = _process_emails(emails)
    _finish_run(
        "simple_outlook",
        len(emails),
        created_candidates,
        max_position or since_position,
        "simple_outlook_last_received_ms",
    )


def _list_imap_messages(since_position: int) -> list[dict]:
    address = _required_setting("EMAIL_ADDRESS")
    password = _required_setting("EMAIL_PASSWORD")
    host = get_setting("EMAIL_IMAP_HOST") or "outlook.office365.com"
    port = int(get_setting("EMAIL_IMAP_PORT") or "993")
    inbox_limit = int(get_setting("EMAIL_INBOX_LIMIT") or "60")
    sent_limit = int(get_setting("EMAIL_SENT_LIMIT") or "60")
    sent_folder = get_setting("EMAIL_SENT_FOLDER") or "Sent Items"

    mail = imaplib.IMAP4_SSL(host, port)
    try:
        mail.login(address, password)
        emails = []
        emails.extend(_fetch_imap_folder(mail, "INBOX", False, since_position, inbox_limit))
        emails.extend(_fetch_imap_folder(mail, sent_folder, True, since_position, sent_limit))
    finally:
        try:
            mail.logout()
        except imaplib.IMAP4.error:
            pass

    emails.sort(key=lambda item: item["sync_position"])
    return emails


def _fetch_imap_folder(
    mail: imaplib.IMAP4_SSL,
    folder: str,
    is_sent: bool,
    since_position: int,
    limit: int,
) -> list[dict]:
    status, _ = mail.select(f'"{folder}"' if " " in folder else folder, readonly=True)
    if status != "OK":
        return []
    status, uid_data = mail.uid("search", None, "ALL")
    if status != "OK" or not uid_data or not uid_data[0]:
        return []

    uids = uid_data[0].split()[-limit:]
    emails = []
    for uid in uids:
        status, data = mail.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not data:
            continue
        raw_message = next((item[1] for item in data if isinstance(item, tuple)), None)
        if not raw_message:
            continue
        parsed = message_from_bytes(raw_message)
        email = _imap_message_to_email(parsed, uid.decode("utf-8"), folder, is_sent)
        if email["sync_position"] > since_position:
            emails.append(email)
    return emails


def _imap_message_to_email(message: Message, uid: str, folder: str, is_sent: bool) -> dict:
    timestamp = _timestamp_from_imap_message(message)
    message_id = _clean_message_id(message.get("Message-ID")) or f"imap:{folder}:{uid}"
    thread_id = _imap_thread_id(message, message_id)
    attachment_names, attachment_text = extract_attachments_from_message(message)
    return {
        "message_id": f"imap:{message_id}",
        "thread_id": f"imap:{thread_id}",
        "sender": _decode_header_value(message.get("From", "")),
        "recipients": _decode_header_value(message.get("To", "")),
        "subject": _decode_header_value(message.get("Subject", "")),
        "body": strip_email_noise(_imap_body(message)),
        "attachment_names": attachment_names,
        "attachment_text": attachment_text,
        "attachment_summary": _attachment_summary(attachment_names, attachment_text),
        "is_sent": is_sent,
        "timestamp": timestamp,
        "sync_position": int(timestamp.timestamp() * 1000),
    }


def _timestamp_from_imap_message(message: Message) -> datetime:
    try:
        return parsedate_to_datetime(message.get("Date")).astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        return datetime.now(timezone.utc)


def _imap_thread_id(message: Message, fallback_message_id: str) -> str:
    references = message.get("References") or message.get("In-Reply-To") or ""
    ids = re.findall(r"<[^>]+>", references)
    if ids:
        return _clean_message_id(ids[0]) or fallback_message_id
    return fallback_message_id


def _clean_message_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("<>").strip()


def _decode_header_value(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _imap_body(message: Message) -> str:
    if message.is_multipart():
        plain = ""
        html = ""
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in disposition:
                continue
            if content_type == "text/plain":
                plain += _decode_part(part)
            elif content_type == "text/html":
                html += _decode_part(part)
        return plain or html
    return _decode_part(message)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _attachment_summary(names: list[str], text: str) -> str:
    if not names:
        return ""
    if text:
        return f"Read {len(names)} attachment(s): {', '.join(names)}"
    return f"Attachment(s) present but no readable text extracted: {', '.join(names)}"


def _microsoft_token() -> str:
    tenant_id = _required_setting("MICROSOFT_TENANT_ID")
    client_id = _required_setting("MICROSOFT_CLIENT_ID")
    client_secret = _required_setting("MICROSOFT_CLIENT_SECRET")
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
    mailbox = quote(_required_env_any("MICROSOFT_MAILBOX_USER", "EMAIL_ADDRESS"))
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
        while url and page_count < int(get_setting("OUTLOOK_MAX_PAGES") or "4"):
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
        "recipients": _outlook_recipients(message),
        "subject": message.get("subject") or "",
        "body": strip_email_noise(content),
        "attachment_names": [],
        "attachment_text": "",
        "attachment_summary": "",
        "is_sent": is_sent,
        "timestamp": timestamp,
        "sync_position": int(timestamp.timestamp() * 1000),
    }


def _outlook_sender(message: dict) -> str:
    sender = (message.get("from") or {}).get("emailAddress") or {}
    return sender.get("address") or sender.get("name") or ""


def _outlook_recipients(message: dict) -> str:
    recipients = []
    for item in message.get("toRecipients") or []:
        address = (item.get("emailAddress") or {}).get("address")
        if address:
            recipients.append(address)
    return ", ".join(recipients)


def _parse_graph_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _required_setting(name: str) -> str:
    value = get_setting(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _required_env_any(*names: str) -> str:
    for name in names:
        value = get_setting(name)
        if value:
            return value
    raise RuntimeError(f"Missing required environment variable. Set one of: {', '.join(names)}")


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
        "recipients": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "body": strip_email_noise(_extract_body(message.get("payload", {}))),
        "attachment_names": [],
        "attachment_text": "",
        "attachment_summary": "",
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
