import streamlit as st
from postgrest.exceptions import APIError

from ai_utils import summarize_board
from rfq_manager import create_rfq, get_rfq_detail, list_rfqs, rfq_status_rows, send_rfq
from supabase_client import MissingConfigError, get_setting
from task_manager import get_dashboard_tasks, mark_task_completed


st.set_page_config(page_title="Email Task Tracker", page_icon="@", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; max-width: 1180px;}
    [data-testid="stMetricValue"] {font-size: 1.55rem;}
    .priority-high {border-left: 4px solid #d92d20;}
    .priority-medium {border-left: 4px solid #f79009;}
    .priority-low {border-left: 4px solid #12b76a;}
    div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 8px;}
    </style>
    """,
    unsafe_allow_html=True,
)


def priority_label(priority: str) -> str:
    labels = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
    return labels.get((priority or "").lower(), "MEDIUM")


def render_task(task: dict, key_prefix: str) -> None:
    priority = task.get("priority", "medium")
    with st.container(border=True):
        top = st.columns([0.62, 0.18, 0.2], vertical_alignment="center")
        top[0].markdown(f"**{task.get('task_text', 'Untitled task')}**")
        score = task.get("priority_score") or 0
        top[1].caption(f"{priority_label(priority)} | {score}/100")
        if top[2].button("Mark complete", key=f"{key_prefix}-{task['id']}", use_container_width=True):
            mark_task_completed(task["id"])
            st.rerun()
        st.caption(task.get("summary") or "No summary available.")
        meta = []
        if task.get("deadline"):
            meta.append(f"Deadline: {task['deadline']}")
        if task.get("product_type"):
            meta.append(f"Product: {task['product_type']}")
        if task.get("quantity"):
            meta.append(f"Qty: {task['quantity']}")
        if task.get("delivery_location"):
            meta.append(f"Location: {task['delivery_location']}")
        if task.get("next_action"):
            meta.append(f"Next: {task['next_action']}")
        if meta:
            st.write(" | ".join(meta))
        detail_cols = st.columns(3)
        detail_cols[0].caption(f"Stage: {task.get('workflow_stage') or task.get('category') or '-'}")
        detail_cols[1].caption(f"Intent: {task.get('intent') or '-'}")
        detail_cols[2].caption(f"Risk: {task.get('escalation_risk') or '-'}")
        flags = []
        if task.get("is_rfq"):
            flags.append("RFQ")
        if task.get("is_order"):
            flags.append("ORDER")
        if task.get("is_blocking"):
            flags.append("BLOCKING")
        if flags:
            st.warning(" | ".join(flags))
        if task.get("missing_fields"):
            st.error(f"Missing: {task['missing_fields']}")
        if task.get("client_name") or task.get("contact_name"):
            st.caption(f"Client/contact: {task.get('client_name') or '-'} / {task.get('contact_name') or '-'}")
        if task.get("priority_reason"):
            st.caption(f"Why this priority: {task['priority_reason']}")
        if task.get("attachment_summary"):
            st.info(task["attachment_summary"])
        if task.get("suggested_reply"):
            with st.expander("Suggested reply"):
                st.write(task["suggested_reply"])


st.sidebar.header("Sync")
if st.sidebar.button("Sync email now", use_container_width=True):
    try:
        with st.spinner("Reading mailbox and updating tasks..."):
            from email_processor import run as run_email_processor

            run_email_processor()
        st.success("Email sync finished.")
        st.rerun()
    except Exception as exc:
        st.error("Email sync failed. Check your Streamlit secrets and mailbox password/app password.")
        if "row-level security" in str(exc).lower():
            st.warning(
                "Supabase blocked writes with row-level security. Run the latest `supabase_schema.sql` in "
                "Supabase SQL Editor, or add `SUPABASE_SERVICE_ROLE_KEY` to Streamlit secrets."
            )
        elif "supabase" in str(exc).lower() and not get_setting("SUPABASE_SERVICE_ROLE_KEY"):
            st.warning("Manual sync writes to Supabase. Add `SUPABASE_SERVICE_ROLE_KEY` to Streamlit secrets.")
        elif "too many requests" in str(exc).lower() or "429" in str(exc):
            st.warning("An AI provider rate-limited the request. The app will now fall back to the other AI provider or rules.")
        with st.expander("Technical detail"):
            st.code(str(exc))


try:
    data = get_dashboard_tasks()
except MissingConfigError as exc:
    st.error(str(exc))
    st.stop()
except APIError as exc:
    st.error("Supabase is connected, but the dashboard cannot read the `tasks` table yet.")
    st.info(
        "Open Supabase SQL Editor, run the latest `supabase_schema.sql`, then restart this Streamlit app. "
        "For manual sync, Streamlit secrets should include `SUPABASE_URL` and either `SUPABASE_SERVICE_ROLE_KEY` "
        "or `SUPABASE_ANON_KEY` with the latest SQL policies."
    )
    with st.expander("Technical detail"):
        st.code(str(exc))
    st.stop()

pending = data["pending"]
follow_ups = data["follow_ups"]
completed = data["completed"]
next_task = pending[0] if pending else (follow_ups[0] if follow_ups else None)

st.title("Production Control Panel")
st.caption("Email-driven RFQ, order, production, and follow-up board")

board_tab, rfq_tab, history_tab = st.tabs(["Action Board", "RFQ Control", "Completed"])

with board_tab:

    blocking = [task for task in pending + follow_ups if task.get("is_blocking")]
    rfqs = [task for task in pending if task.get("is_rfq")]
    orders = [task for task in pending if task.get("is_order")]

    metric_cols = st.columns(5)
    metric_cols[0].metric("Pending", len(pending))
    metric_cols[1].metric("RFQs", len(rfqs))
    metric_cols[2].metric("Orders", len(orders))
    metric_cols[3].metric("Blocking", len(blocking))
    metric_cols[4].metric("Follow-ups", len(follow_ups))

    st.divider()

    if next_task:
        with st.container(border=True):
            st.subheader("Next Best Production Task")
            st.write(next_task.get("next_action") or next_task.get("task_text"))
            st.caption(next_task.get("summary") or "")
    else:
        st.success("No active tasks. You are caught up.")

    with st.container(border=True):
        st.subheader("AI Briefing")
        st.write(summarize_board(pending + follow_ups))

    left, right = st.columns([0.64, 0.36], gap="large")

    with left:
        st.subheader("Production / RFQ Tasks")
        if not pending:
            st.info("No pending email tasks.")
        for task in pending:
            render_task(task, "pending")

    with right:
        st.subheader("Blocking")
        if not blocking:
            st.success("No blocking production/RFQ items.")
        for task in blocking[:8]:
            render_task(task, "blocking")

        st.subheader("Follow-Ups")
        if not follow_ups:
            st.info("No follow-ups needed.")
        for task in follow_ups:
            render_task(task, "followup")

with rfq_tab:
    st.subheader("Create RFQ")
    with st.form("create-rfq"):
        title = st.text_input("RFQ title")
        due_date = st.date_input("Due date", value=None)
        vendors = st.text_area("Vendor emails", placeholder="vendor1@example.com\nvendor2@example.com")
        items = st.text_area("Items/specifications", placeholder="10mm toughened glass - 500 pcs\nLaminated glass - 20 sheets")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Create RFQ")
    if submitted and title and vendors and items:
        rfq_id = create_rfq(title, due_date.isoformat() if due_date else None, notes, vendors, items)
        st.success(f"Created RFQ-{rfq_id:05d}")
        st.rerun()

    st.subheader("RFQ Status")
    rows = rfq_status_rows()
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No RFQs created yet.")

    for rfq in list_rfqs():
        with st.expander(f"{rfq.get('rfq_code') or rfq['id']} - {rfq['title']}"):
            detail = get_rfq_detail(rfq["id"])
            cols = st.columns(3)
            cols[0].metric("Vendors", len(detail["vendors"]))
            cols[1].metric("Responses", len(detail["responses"]))
            cols[2].metric("Status", rfq["status"])
            if st.button("Send RFQ emails", key=f"send-rfq-{rfq['id']}"):
                sent_count = send_rfq(rfq["id"])
                st.success(f"Sent {sent_count} RFQ email(s).")
                st.rerun()
            st.write("Items")
            st.dataframe(detail["items"], use_container_width=True, hide_index=True)
            st.write("Vendors")
            st.dataframe(detail["vendors"], use_container_width=True, hide_index=True)
            st.write("Responses")
            st.dataframe(detail["responses"], use_container_width=True, hide_index=True)

with history_tab:
    st.subheader("Completed")
    if not completed:
        st.caption("Completed tasks will appear here.")
    else:
        for task in completed[:20]:
            with st.expander(task.get("task_text", "Completed task")):
                st.caption(task.get("summary") or "")
                st.write(f"Completed: {task.get('completed_at') or 'unknown'}")
