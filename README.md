# Email Task Tracker - Intelligent Action Board

Internal Streamlit app plus GitHub Actions automation that turns Outlook or Gmail email threads into a continuously updated task board.

The default setup is now **simple Outlook password/app-password mode** through IMAP.

## What It Does

- Runs every 10 minutes in GitHub Actions for 24/7 background sync.
- Can also sync directly from Streamlit with one button.
- Reads Outlook inbox and sent mail with IMAP.
- Stores emails and tasks in Supabase Postgres.
- Converts inbound emails into actionable tasks with rules plus optional free AI.
- Marks tasks completed when a sent reply appears in the same conversation.
- Creates follow-up tasks when you sent an email and no reply arrives after the configured time.
- Reads supported attachments and includes them in AI analysis.
- Learns from your sent replies and uses similar examples when drafting suggestions.
- Shows only pending tasks, follow-ups, suggested next action, and completed history in Streamlit.

## Free Services Used

- GitHub repo and GitHub Actions
- Streamlit Community Cloud
- Supabase free tier
- Outlook IMAP with an email password or app password
- Optional Groq free tier and Gemini free tier for AI summaries

## AI Features

- Email summary and task generation.
- Multiple tasks from one email when needed.
- Priority, priority reason, and urgency detection.
- Intent, category, client/contact, sentiment, and escalation risk detection.
- Real deadline text plus optional `deadline_date` when the model can infer it.
- Attachment awareness for PDF, Excel, CSV, Word, and text files.
- Suggested reply draft based on the email, attachments, and previous sent replies.
- Lightweight RAG memory from your sent emails in `ai_memories`.
- Daily AI briefing that tells you what to do first.

## Attachment Support

The simple Outlook IMAP provider extracts readable text from:

```text
PDF: .pdf
Excel: .xlsx
CSV: .csv
Word: .docx
Text: .txt, .md, .log
```

Attachments are not stored as files. Only extracted text and attachment names are stored in Supabase.

## RAG / Reply Pattern Learning

When the app sees sent emails, it saves short reply examples in `ai_memories`.
When a new inbound email arrives from the same domain, those examples are added to the AI prompt so suggested replies better match the user's tone and pattern.

## Files

- `app.py` - Streamlit dashboard.
- `email_processor.py` - Outlook IMAP, Microsoft Graph, or Gmail automation entrypoint.
- `ai_utils.py` - Groq/Gemini enrichment with rule fallback.
- `task_manager.py` - task lifecycle, completion, follow-up logic.
- `supabase_client.py` - Supabase client and state helpers.
- `supabase_schema.sql` - database schema.
- `.github/workflows/email_job.yml` - scheduled background job.

## Required Secrets

### Streamlit Secrets

Paste this in Streamlit Cloud -> App -> Settings -> Secrets:

```toml
EMAIL_PROVIDER = "simple_outlook"
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "your Supabase service role key"
EMAIL_ADDRESS = "user@outlook.com"
EMAIL_PASSWORD = "your email password or app password"
GEMINI_API_KEY = "your Gemini key"
GROQ_API_KEY = "your Groq key"
```

Optional:

```toml
EMAIL_IMAP_HOST = "outlook.office365.com"
EMAIL_IMAP_PORT = "993"
EMAIL_SENT_FOLDER = "Sent Items"
FOLLOW_UP_AFTER_HOURS = "24"
AI_MAX_CALLS_PER_RUN = "8"
```

This lets the dashboard load tasks and lets you manually sync email when you click **Sync email now**.

### GitHub Actions Secrets For 24/7

For automatic background sync every 10 minutes, add the same core values in GitHub repo -> Settings -> Secrets and variables -> Actions:

```text
EMAIL_PROVIDER=simple_outlook
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
EMAIL_ADDRESS
EMAIL_PASSWORD
GEMINI_API_KEY
GROQ_API_KEY
```

Optional GitHub Actions secrets:

```text
EMAIL_IMAP_HOST=outlook.office365.com
EMAIL_IMAP_PORT=993
EMAIL_SENT_FOLDER=Sent Items
EMAIL_INBOX_LIMIT=60
EMAIL_SENT_LIMIT=60
FOLLOW_UP_AFTER_HOURS=24
AI_MAX_CALLS_PER_RUN=8
```

Streamlit secrets and GitHub Actions secrets are separate. For full 24/7 behavior, paste the simple email/Supabase/AI secrets in both places.

### Dashboard-Only Alternative

If you do not want Streamlit to access the mailbox, use only `SUPABASE_URL` and `SUPABASE_ANON_KEY` in Streamlit and put email secrets in GitHub Actions.

For your requested simple setup with manual sync plus 24/7 sync, use both the Streamlit secrets block and the GitHub Actions secrets block.

## 1. Supabase Setup

1. Create a Supabase project.
2. Open Supabase SQL Editor.
3. Paste the full contents of `supabase_schema.sql`.
4. Click Run.
5. Go to Project Settings -> API.
6. Copy:
   - Project URL -> `SUPABASE_URL`
   - anon public key -> `SUPABASE_ANON_KEY`
   - service_role key -> `SUPABASE_SERVICE_ROLE_KEY`

For the simplest one-user setup, the service role key is used by Streamlit manual sync and GitHub Actions scheduled sync because both write emails and tasks.

## 2. Simple Outlook Setup

Use this when the client has one Outlook/Hotmail/Microsoft mailbox.

1. Make sure IMAP is enabled for the mailbox.
2. If the account has 2-step verification, create an app password.
3. Put the mailbox in `EMAIL_ADDRESS`.
4. Put the password or app password in `EMAIL_PASSWORD`.
5. Keep the default IMAP host:

```text
outlook.office365.com
```

For some business Microsoft 365 tenants, password login may be blocked by policy. In that case use Microsoft Graph mode instead.

## 3. GitHub Deployment

Create a GitHub repo, then run these commands from the project folder:

```bash
git init
git add .
git commit -m "Initial Email Task Tracker"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git push -u origin main
```

Then add GitHub Actions secrets for 24/7 sync:

```text
EMAIL_PROVIDER
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
EMAIL_ADDRESS
EMAIL_PASSWORD
GROQ_API_KEY optional
GEMINI_API_KEY optional
```

After secrets are added:

1. Go to the Actions tab.
2. Open Email Task Processor.
3. Click Run workflow.
4. Check the logs.
5. Confirm rows appear in Supabase `emails` and `tasks`.

The workflow also runs every 10 minutes automatically.

## 4. Streamlit Cloud Deployment

1. Go to Streamlit Community Cloud.
2. Click New app.
3. Select this GitHub repo.
4. Branch: `main`.
5. Main file path: `app.py`.
6. Open app settings and paste Streamlit secrets:

```toml
EMAIL_PROVIDER = "simple_outlook"
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "your Supabase service role key"
EMAIL_ADDRESS = "user@outlook.com"
EMAIL_PASSWORD = "your email password or app password"
GEMINI_API_KEY = "your Gemini key"
GROQ_API_KEY = "your Groq key"
```

7. Deploy.

Click **Sync email now** in the app sidebar to read email immediately. GitHub Actions will keep syncing every 10 minutes in the background after you add the same secrets there.

## Optional Microsoft Graph Mode

If password login is blocked, use Microsoft Graph:

```toml
EMAIL_PROVIDER = "outlook"
MICROSOFT_TENANT_ID = "tenant id"
MICROSOFT_CLIENT_ID = "client id"
MICROSOFT_CLIENT_SECRET = "client secret"
EMAIL_ADDRESS = "user@clientdomain.com"
```

## Optional Gmail Mode

If a future client uses Gmail, set this GitHub secret:

```text
EMAIL_PROVIDER=gmail
```

Then also add:

```text
GMAIL_TOKEN_JSON=your Gmail OAuth token JSON
```

Outlook secrets are not needed for Gmail mode.

## Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the dashboard:

```bash
streamlit run app.py
```

Run the processor:

```bash
python email_processor.py
```

## How Client Onboarding Works Later

For each new client:

1. Create or reuse a Supabase project.
2. Add the client mailbox email to `EMAIL_ADDRESS`.
3. Add the client password or app password to `EMAIL_PASSWORD`.
4. Paste the same client-specific secrets into Streamlit and GitHub Actions.
5. Run the workflow once and verify tasks appear.

## Production Notes

- AI keys are optional. Without them, the app still creates tasks using rules.
- The processor filters automated/marketing emails before AI and caps AI calls with `AI_MAX_CALLS_PER_RUN`.
- Completed tasks are hidden from the main list and retained in history.
- Simple Outlook mode uses IMAP message headers as the task thread ID.
- `app_state` stores the last Outlook/Gmail sync position to avoid reprocessing.
- For business tenants that block password login, switch to Microsoft Graph mode.
