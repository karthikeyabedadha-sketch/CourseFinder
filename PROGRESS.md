# CourseFinder Progress Checklist

Last updated: 2026-11-02. Every item marked `[x]` has been implemented and verified with automated tests (`51 passed`).

## Phase 1 — Audit & Baseline Fixes
- [x] Baseline audit of the entire codebase, configuration, store layer, templates, and tests
- [x] Removed committed `.env` containing exposed development keys (`SECRET_KEY`, `ADMIN_SETUP_TOKEN`)
- [x] Fixed `wsgi.py` unconditional `debug=True` security risk
- [x] Hardened `scripts/promote_admin.py` with constant-time `secrets.compare_digest` and minimum token length checks

## Phase 2 — Core Backend, Database, Workflow & Security
- [x] `app/__init__.py`: Strong secret-key enforcement, `ProxyFix`, strict `Content-Security-Policy` (`CSP`), `HSTS`, custom `400/403/404/413/429/500` error pages, per-request database role lookup
- [x] `app/workflow.py`: Multi-step publication state machine (`draft -> pending_review -> approved -> published -> archived`)
- [x] `app/store/local.py`:
  - Safe user columns (`password_hash` never returned in user dicts)
  - Database triggers enforcing `status = 'draft'` on insert, `approved -> published` transitions, untrusted origin guards (`csv_import`, `user_submission`, `ai_suggestion`, `sample`), and `status = 'pending'` on new reviews
  - Audit trail (`moderation_actions`) and private course notes (`course_admin_notes`)
  - Quick editorial flag toggling (`is_featured`, `is_underrated`, `needs_verification`)
  - Category and provider course counts and safe deletion
- [x] `app/store/supabase_store.py` & `supabase/schema.sql`:
  - Upgraded to v2 parity with `LocalStore`
  - PostgREST `.or_()` filter sanitization against filter injection
  - PostgreSQL workflow triggers (`trg_enforce_course_workflow`) and hardened Row Level Security (`RLS`)
  - Supabase dashboard stale-verification metric counts published courses not verified within 180 days

## Phase 3 — Public Website & Frontend Design
- [x] `app/static/css/app.css` & `app/static/js/app.js`: Complete responsive design system, zero inline styles (100% CSP compliant), accessible badges, skill tags, and form states
- [x] `app/templates/home.html`: Modern hero with quick filters, Featured Courses, Underrated & Less-Known Gems, Categories with live counts, Recently Published, and How It Works
- [x] `app/templates/courses/index.html`: Search, category, provider, pricing (free/paid), difficulty, language, minimum user rating, underrated gems, and featured filters + 6 sort modes + pagination
- [x] `app/templates/courses/detail.html`: Verified metadata badges, skills list pills, provider link, full editorial assessment box, similar courses, issue reporting, and review management

## Phase 4 — User Features
- [x] `app/templates/account/library.html`: Saved courses management, user reviews with edit/delete, and user course suggestions tracking
- [x] `app/templates/account/profile.html`: Display name update and secure password change (`PasswordChangeForm`)
- [x] `app/templates/account/submit.html`: User course suggestion form (creates pending suggestion, never auto-published)

## Phase 5 — Admin Dashboard
- [x] `app/templates/admin/dashboard.html`: Complete editorial metrics, pending review quick-moderation, and recent audit activity table
- [x] `app/templates/admin/courses.html` & `course_form.html`: Course CRUD, draft saving, preview, step-by-step workflow transition buttons, one-click Feature and Underrated toggles, duplicate detection, and structured fieldsets
- [x] `app/templates/admin/categories.html` & `providers.html`: Add, edit, view published/total counts, and delete unused categories/providers
- [x] `app/templates/admin/import.html` & `import_preview.html`: CSV upload, row-level validation preview, draft-only batch import, and formula-injection-safe CSV export
- [x] Owner Studio password gate with CSRF, rate limiting, opaque server-validated sessions, logout, and eight-hour expiry; every admin page and action requires the owner session
- [x] Owner Studio user directory and account details with search, review history, account activity, and audited role/account-status controls; blocks suspended-user logins and clears suspended-user app sessions
- [x] One-time local first-owner setup command: hidden password prompts, password hashing, atomic admin creation, repeat-setup protection, and refusal of missing databases and remote backends
- [x] Dashboard includes total-review and registered-user links; course editor supports per-course tags and learning outcomes

## Phase 6 — Quality Assurance
- [x] 51 automated tests passing across public pages, authentication/admin authorization, comprehensive workflows, Owner Studio, and Supabase-store metric behavior
