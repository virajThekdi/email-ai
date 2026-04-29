import streamlit as st
from postgrest.exceptions import APIError

from supabase_client import MissingConfigError
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
        top[1].caption(priority_label(priority))
        if top[2].button("Mark complete", key=f"{key_prefix}-{task['id']}", use_container_width=True):
            mark_task_completed(task["id"])
            st.rerun()
        st.caption(task.get("summary") or "No summary available.")
        meta = []
        if task.get("deadline"):
            meta.append(f"Deadline: {task['deadline']}")
        if task.get("next_action"):
            meta.append(f"Next: {task['next_action']}")
        if meta:
            st.write(" | ".join(meta))


try:
    data = get_dashboard_tasks()
except MissingConfigError as exc:
    st.error(str(exc))
    st.stop()
except APIError as exc:
    st.error("Supabase is connected, but the dashboard cannot read the `tasks` table yet.")
    st.info(
        "Open Supabase SQL Editor, run the latest `supabase_schema.sql`, then restart this Streamlit app. "
        "Also confirm Streamlit secrets contain `SUPABASE_URL` and `SUPABASE_ANON_KEY` only."
    )
    with st.expander("Technical detail"):
        st.code(str(exc))
    st.stop()

pending = data["pending"]
follow_ups = data["follow_ups"]
completed = data["completed"]
next_task = pending[0] if pending else (follow_ups[0] if follow_ups else None)

st.title("Email Task Tracker")
st.caption("Intelligent Action Board")

metric_cols = st.columns(4)
metric_cols[0].metric("Pending", len(pending))
metric_cols[1].metric("Follow-ups", len(follow_ups))
metric_cols[2].metric("High priority", len([t for t in pending if t.get("priority") == "high"]))
metric_cols[3].metric("Completed history", len(completed))

st.divider()

if next_task:
    with st.container(border=True):
        st.subheader("Suggested Next Action")
        st.write(next_task.get("next_action") or next_task.get("task_text"))
        st.caption(next_task.get("summary") or "")
else:
    st.success("No active tasks. You are caught up.")

left, right = st.columns([0.64, 0.36], gap="large")

with left:
    st.subheader("Pending Tasks")
    if not pending:
        st.info("No pending email tasks.")
    for task in pending:
        render_task(task, "pending")

with right:
    st.subheader("Follow-Ups")
    if not follow_ups:
        st.info("No follow-ups needed.")
    for task in follow_ups:
        render_task(task, "followup")

st.subheader("Completed")
if not completed:
    st.caption("Completed tasks will appear here.")
else:
    for task in completed[:20]:
        with st.expander(task.get("task_text", "Completed task")):
            st.caption(task.get("summary") or "")
            st.write(f"Completed: {task.get('completed_at') or 'unknown'}")
