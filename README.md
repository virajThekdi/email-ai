# Email Task Tracker - Intelligent Action Board

Internal Streamlit app plus GitHub Actions automation that turns Outlook or Gmail email threads into a continuously updated task board.

The default setup is now **Outlook / Microsoft 365** through Microsoft Graph.

## What It Does

- Runs every 10 minutes in GitHub Actions.
- Reads Outlook inbox and sent mail with Microsoft Graph.
- Stores emails and tasks in Supabase Postgres.
- Converts inbound emails into actionable tasks with rules plus optional free AI.
- Marks tasks completed when a sent reply appears in the same conversation.
- Creates follow-up tasks when you sent an email and no reply arrives after the configured time.
- Shows only pending tasks, follow-ups, suggested next action, and completed history in Streamlit.

## Free Services Used

- GitHub repo and GitHub Actions
- Streamlit Community Cloud
- Supabase free tier
- Microsoft Graph API for Outlook mail
- Optional Groq free tier and Gemini free tier for AI summaries

## Files

- `app.py` - Streamlit dashboard.
- `email_processor.py` - Outlook/Gmail automation entrypoint.
- `ai_utils.py` - Groq/Gemini enrichment with rule fallback.
- `task_manager.py` - task lifecycle, completion, follow-up logic.
- `supabase_client.py` - Supabase client and state helpers.
- `supabase_schema.sql` - database schema.
- `.github/workflows/email_job.yml` - scheduled background job.

## Required Secrets

### GitHub Actions Secrets

Add these in GitHub repo -> Settings -> Secrets and variables -> Actions:

```text
EMAIL_PROVIDER=outlook
SUPABASE_URL=your Supabase project URL
SUPABASE_SERVICE_ROLE_KEY=your Supabase service role key
MICROSOFT_TENANT_ID=your Microsoft tenant ID
MICROSOFT_CLIENT_ID=your Microsoft app client ID
MICROSOFT_CLIENT_SECRET=your Microsoft app client secret
MICROSOFT_MAILBOX_USER=user@clientdomain.com
```

Optional AI secrets:

```text
GROQ_API_KEY=your Groq key
GEMINI_API_KEY=your Gemini key
```

Optional behavior:

```text
FOLLOW_UP_AFTER_HOURS=24
OUTLOOK_MAX_PAGES=4
```

### Streamlit Secrets

Add these in Streamlit Cloud -> App -> Settings -> Secrets:

```toml
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_ANON_KEY = "your Supabase anon key"
```

Do not put `SUPABASE_SERVICE_ROLE_KEY` in Streamlit.

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

The service role key is only for GitHub Actions because the automation writes emails and tasks.

## 2. Microsoft Outlook Setup

Use this when the client uses Outlook or Microsoft 365.

1. Go to Microsoft Entra admin center or Azure Portal.
2. Open App registrations.
3. Click New registration.
4. Name it `Email Task Tracker`.
5. Choose the account type for the client tenant.
6. After creating it, copy:
   - Application client ID -> `MICROSOFT_CLIENT_ID`
   - Directory tenant ID -> `MICROSOFT_TENANT_ID`
7. Open Certificates & secrets.
8. Create a new client secret.
9. Copy the secret value -> `MICROSOFT_CLIENT_SECRET`.
10. Open API permissions.
11. Add Microsoft Graph application permission:
   - `Mail.Read`
12. Click Grant admin consent.
13. Set `MICROSOFT_MAILBOX_USER` to the mailbox address the app should read, for example:

```text
tasks@clientcompany.com
```

Best production setup: use one shared/client mailbox and give the app access only to that mailbox.

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

Then add GitHub Actions secrets:

```text
EMAIL_PROVIDER
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
MICROSOFT_TENANT_ID
MICROSOFT_CLIENT_ID
MICROSOFT_CLIENT_SECRET
MICROSOFT_MAILBOX_USER
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
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_ANON_KEY = "your Supabase anon key"
```

7. Deploy.

Streamlit is only the dashboard. It does not run the background email job.

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
2. Register a Microsoft Entra app in that client tenant.
3. Add the client mailbox email to `MICROSOFT_MAILBOX_USER`.
4. Paste the client-specific secrets into GitHub Actions.
5. Paste only Supabase URL and anon key into Streamlit.
6. Run the workflow once and verify tasks appear.

## Production Notes

- AI keys are optional. Without them, the app still creates tasks using rules.
- Completed tasks are hidden from the main list and retained in history.
- Outlook conversations use Microsoft Graph `conversationId` as the task thread ID.
- `app_state` stores the last Outlook/Gmail sync position to avoid reprocessing.
- Keep GitHub Actions secrets private. Never expose Microsoft client secrets or Supabase service role keys in Streamlit.
