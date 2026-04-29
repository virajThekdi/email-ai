# Email Task Tracker - Intelligent Action Board

An internal Streamlit app plus GitHub Actions automation that turns Gmail threads into a continuously updated action board.

## What It Does

- Fetches Gmail messages every 10 minutes through GitHub Actions.
- Stores raw email metadata and task state in Supabase Postgres.
- Creates one actionable task per inbound thread with rules plus lightweight AI.
- Marks tasks complete when a sent reply appears in the same Gmail thread.
- Creates follow-up tasks when you sent an email and no reply arrives after the configured window.
- Shows only pending work, follow-ups, suggested next action, and completed history in Streamlit.

## Files

- `app.py` - Streamlit dashboard.
- `email_processor.py` - Gmail fetcher and automation entrypoint.
- `ai_utils.py` - Groq/Gemini enrichment with rule-based fallback.
- `task_manager.py` - task lifecycle, completion, follow-up logic.
- `supabase_client.py` - Supabase client and state helpers.
- `supabase_schema.sql` - database schema and indexes.
- `.github/workflows/email_job.yml` - always-on scheduled processor.

## Environment Variables

Required for automation:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `GMAIL_TOKEN_JSON`

Required for Streamlit:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`

Optional AI:

- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `GROQ_MODEL` defaults to `llama-3.1-8b-instant`
- `GEMINI_MODEL` defaults to `gemini-1.5-flash`
- `FOLLOW_UP_AFTER_HOURS` defaults to `24`

## 1. Supabase Setup

1. Create a Supabase project.
2. Open SQL Editor and run `supabase_schema.sql`.
3. Copy your project URL into `SUPABASE_URL`.
4. Copy the anon public key into `SUPABASE_ANON_KEY` for Streamlit.
5. Copy the service role key into `SUPABASE_SERVICE_ROLE_KEY` for GitHub Actions only.

The service role key bypasses row-level security and should never be exposed in the Streamlit app.

## 2. Gmail API Setup

1. In Google Cloud Console, create or select a project.
2. Enable the Gmail API.
3. Configure the OAuth consent screen for internal/testing use.
4. Create an OAuth client ID for a desktop app.
5. Download the client credentials JSON.
6. Generate a user token locally with the Gmail readonly scope:

```bash
python -m pip install google-auth-oauthlib
```

Create a temporary helper script locally:

```python
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=0)
print(creds.to_json())
```

Save the printed JSON as the GitHub secret `GMAIL_TOKEN_JSON`.

## 3. GitHub Actions Setup

1. Push this repo to GitHub.
2. Go to repository Settings -> Secrets and variables -> Actions.
3. Add these secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `GMAIL_TOKEN_JSON`
   - `GROQ_API_KEY` if using Groq
   - `GEMINI_API_KEY` if using Gemini fallback
4. Open Actions and run `Email Task Processor` manually once.
5. Confirm the run prints fetched message counts and Supabase tables receive rows.

The workflow runs every 10 minutes. GitHub scheduled workflows can be delayed during platform load, so the processor is idempotent and safe to run manually.

## 4. Streamlit Cloud Deployment

1. Push the repo to GitHub.
2. In Streamlit Cloud, create a new app from the repo.
3. Set the main file path to `app.py`.
4. Add secrets:

```toml
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_ANON_KEY = "your-anon-key"
```

5. Deploy the app.

Streamlit only reads and updates task status. It does not run background email jobs.

## Local Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

For local processor testing, set environment variables and run:

```bash
python email_processor.py
```

## Production Notes

- Keep AI optional. The rules fallback keeps task generation working if free APIs rate-limit or fail.
- Keep `GMAIL_MAX_PAGES` low to control API usage.
- Use the Supabase service role key only in GitHub Actions.
- Completed tasks are hidden from the main dashboard and retained in history.
- The `app_state` table stores the last Gmail internal timestamp to avoid repeated full processing.
