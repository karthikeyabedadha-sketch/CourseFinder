# CourseFinder

CourseFinder is a curated discovery platform for genuinely valuable external online courses — including underrated and less-known courses across Computer Science, Artificial Intelligence & Machine Learning, Programming, Data Science, Finance & Investing, Business & Entrepreneurship, Marketing, Design, Mathematics, and Professional Skills.

CourseFinder helps learners evaluate courses through transparent editorial assessments and moderated community reviews. It does not host lessons, collect tuition, or issue certificates.

## Key Features

- **Public Discovery Website**: Modern responsive homepage, Featured Courses, Underrated & Less-Known Gems, Category directory with course counts, full Course Catalogue with search, multi-facet filtering (category, provider, free/paid, difficulty, language, minimum rating, featured, underrated), sorting, and detailed Course Pages.
- **Strict Editorial Control**: Every new course (whether created by an admin, imported via CSV, suggested by a user, or loaded as sample data) starts strictly as a **draft** (`status = 'draft'`). Courses can only become public after an administrator transitions them through `draft -> pending_review -> approved -> published`. Enforced in Python, the store layer, and database triggers.
- **User Accounts & Library**: Secure registration, login/logout, profile and password management, saved/bookmarked courses, course suggestions, and moderated course reviews (create, edit, delete, report).
- **Administrator Dashboard (`/admin`)**: Overview metrics, searchable course CRUD, preview, workflow state transitions, one-click Featured & Underrated badges, category and provider management, course tags, user directory and account detail pages with review history, recent account activity, and audited role/account-status controls, review moderation, issue reports, user suggestions queue, contact messages inbox, CSV import (with row-level validation preview) and formula-safe CSV export, and an administrative audit log. Suspending an account signs it out of the Flask app and blocks future logins; administrators cannot suspend their own account or the last active administrator. Its in-app, deterministic content assistant suggests editable summaries, descriptions, learning outcomes, skills, search tags, and categories; it does not call an external LLM or require an AI API key. Verify its suggestions before publishing.
- **Read-Only JSON API (`/api/v1/`)**: Public endpoints (`/api/v1/health`, `/api/v1/categories`, `/api/v1/courses`, `/api/v1/courses/<slug>`) exposing only published courses and approved reviews.

## 1. Prerequisites

Install **Python 3.11 or 3.12+** from [python.org](https://www.python.org/downloads/). On Windows, check **Add python.exe to PATH** during installation.

## 2. Windows Setup & Installation

Open PowerShell in the `CourseFinder` project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks script execution, run this once and try activating again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. Environment Configuration (`.env`)

Copy the example configuration file:

```powershell
copy .env.example .env
```

Edit `.env` and set a strong random `SECRET_KEY`. Owner Studio reads `OWNER_ACCESS_SECRET` from the server environment; the recommended local setup below prompts for it without echoing the secret or saving it in `.env`.

```powershell
python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
```

Never commit `.env` to version control or expose secret keys in client-side code.

## 4. Database Initialization & Migrations

### Local SQLite (`DATA_BACKEND=local`)
- When `DATA_BACKEND=local` (the default), `LocalStore.initialize()` automatically creates `instance/coursefinder.db` on startup and applies non-destructive `ALTER TABLE` upgrades (`UPGRADE_COLUMNS`), indexes (`INDEXES`), and safety triggers (`TRIGGERS`) while preserving all existing data.
- If `SEED_SAMPLE_COURSES=true` in `.env` and the `courses` table is empty, sample courses are loaded strictly as **unverified drafts** for you to review in `/admin/courses`.

### Supabase PostgreSQL (`DATA_BACKEND=supabase`)
1. Create a project at [supabase.com](https://supabase.com).
2. Open the **SQL Editor** in Supabase and run the entire `supabase/schema.sql` script. It uses `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` so existing data is preserved while adding all v2 tables, workflow triggers, and Row Level Security (`RLS`) policies.
3. In `.env`, set:
   ```ini
   DATA_BACKEND=supabase
   SUPABASE_URL=https://YOUR-PROJECT-ID.supabase.co
   SUPABASE_ANON_KEY=your-supabase-anon-key
   SUPABASE_SERVICE_ROLE_KEY=your-supabase-service-role-key
   ```

## 5. Running the Application on Windows

```powershell
.\.venv\Scripts\Activate.ps1
python wsgi.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

## 6. Owner Studio Access

Owner Studio uses a password-only access screen at `/admin/studio`. Regular user registration and login remain separate and never grant administrative access. To enter the owner secret without echoing it, set it for the current PowerShell process and start the app in the same terminal:

```powershell
.\.venv\Scripts\Activate.ps1
$ownerSecret = Read-Host "Enter OWNER_ACCESS_SECRET" -AsSecureString
$secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ownerSecret)
try {
    $env:OWNER_ACCESS_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
}
python wsgi.py
```

Use a unique secret of at least 16 characters, kept separate from your email and normal user passwords. The application stores only a hash of the temporary session token in SQLite; it does not persist the owner secret. The setting is inherited by the app process and is not stored in `.env`. Close this PowerShell window to clear it. An unlocked session expires after eight hours; the Owner Studio sidebar has a **Lock Owner Studio** button.

Visit [http://127.0.0.1:5000/admin/studio](http://127.0.0.1:5000/admin/studio) to unlock. The owner secret is not available in the current conversation context; enter the secret you intend to use at the hidden PowerShell prompt.

## 7. Running the Automated Test Suite

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -v
```

## 8. Production Deployment

1. Set `FLASK_ENV=production` in your production environment variables.
2. Provide a strong `SECRET_KEY` (minimum 32 characters) and a private `OWNER_ACCESS_SECRET` (minimum 16 characters).
3. If running behind a reverse proxy (e.g. Render, Nginx, Cloudflare), set `TRUSTED_PROXY_COUNT=1` so rate limiting and HTTPS redirects see the real client IP and scheme.
4. Serve the application with Gunicorn on Linux/container hosts:
   ```bash
   gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
   ```
