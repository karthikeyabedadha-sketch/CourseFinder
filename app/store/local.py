"""SQLite store used for local development and for the automated tests.

Rules enforced here (as well as in the routes and by database triggers):

* a new course is always a DRAFT;
* ``save_course`` never changes a course's status;
* status only changes through ``set_course_status``, which applies
  ``app.workflow.check_transition`` and writes an audit-log row;
* public queries always filter on ``status = 'published'`` inside this module.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from app import workflow
from app.store.constants import (
    COURSE_REPORT_REASONS,
    DEFAULT_CATEGORIES,
    REVIEW_REPORT_REASONS,
    REVIEW_STATUSES,
    SAMPLE_COURSES,
    SUBMISSION_STATUSES,
    UNTRUSTED_ORIGINS,
)
from app.store.errors import DuplicateError, NotFoundError, StoreError
from app.utils import escape_like, new_id, normalise_url, search_terms, slugify

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_sessions (
    token_hash TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    website_url TEXT
);
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    provider_id TEXT,
    category_id TEXT,
    subcategory TEXT,
    official_url TEXT NOT NULL,
    url_key TEXT,
    short_description TEXT,
    description TEXT,
    language TEXT,
    is_free INTEGER,
    price REAL CHECK (price IS NULL OR price >= 0),
    currency TEXT,
    pricing_notes TEXT,
    difficulty TEXT,
    prerequisites TEXT,
    duration TEXT,
    duration_verified INTEGER NOT NULL DEFAULT 0,
    certificate_available INTEGER,
    certificate_verified INTEGER NOT NULL DEFAULT 0,
    teaching_format TEXT,
    skills_taught TEXT,
    learning_outcomes TEXT NOT NULL DEFAULT '',
    intended_audience TEXT,
    strengths TEXT,
    limitations TEXT,
    editorial_assessment TEXT,
    editorial_score REAL CHECK (editorial_score IS NULL OR (editorial_score >= 1 AND editorial_score <= 10)),
    editorial_score_public INTEGER NOT NULL DEFAULT 0,
    editorial_why_recommend TEXT,
    editorial_who_should_take TEXT,
    editorial_who_should_avoid TEXT,
    editorial_value_for_money TEXT,
    editorial_approved INTEGER NOT NULL DEFAULT 0,
    recommendation_status TEXT DEFAULT 'none',
    last_verified_at TEXT,
    thumbnail_path TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','pending_review','approved','published','archived')),
    is_featured INTEGER NOT NULL DEFAULT 0,
    is_underrated INTEGER NOT NULL DEFAULT 0,
    needs_verification INTEGER NOT NULL DEFAULT 0,
    keywords TEXT,
    origin TEXT NOT NULL DEFAULT 'admin',
    source_url TEXT,
    source_checked_at TEXT,
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    FOREIGN KEY(category_id) REFERENCES categories(id)
);
CREATE TABLE IF NOT EXISTS course_admin_notes (
    course_id TEXT PRIMARY KEY,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT,
    UNIQUE(course_id, user_id),
    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS saved_courses (
    user_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, course_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS contact_messages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    message TEXT NOT NULL,
    is_handled INTEGER NOT NULL DEFAULT 0,
    handled_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('course','review')),
    target_id TEXT NOT NULL,
    reporter_id TEXT,
    reason TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved','dismissed')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS course_submissions (
    id TEXT PRIMARY KEY,
    submitter_id TEXT NOT NULL,
    title TEXT NOT NULL,
    official_url TEXT NOT NULL,
    provider_name TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    course_id TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    FOREIGN KEY(submitter_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS moderation_actions (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    label TEXT,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL,
    filename TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added after the first release; applied to databases created earlier.
UPGRADE_COLUMNS = {
    "users": {"is_active": "INTEGER NOT NULL DEFAULT 1"},
    "courses": {
        "url_key": "TEXT",
        "language": "TEXT",
        "is_underrated": "INTEGER NOT NULL DEFAULT 0",
        "origin": "TEXT NOT NULL DEFAULT 'admin'",
        "source_url": "TEXT",
        "source_checked_at": "TEXT",
        "published_at": "TEXT",
        "learning_outcomes": "TEXT NOT NULL DEFAULT ''",
    },
    "reviews": {"updated_at": "TEXT"},
    "contact_messages": {"is_handled": "INTEGER NOT NULL DEFAULT 0", "handled_at": "TEXT"},
}

# Safety nets that hold even if application code has a bug.
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS courses_new_must_be_draft
BEFORE INSERT ON courses
WHEN NEW.status <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'a new course must start as a draft');
END;
CREATE TRIGGER IF NOT EXISTS courses_untrusted_origin_guard
BEFORE INSERT ON courses
WHEN NEW.origin IN ('csv_import', 'user_submission', 'ai_suggestion', 'sample')
     AND (NEW.is_featured <> 0 OR NEW.is_underrated <> 0 OR NEW.editorial_approved <> 0 OR NEW.editorial_score_public <> 0)
BEGIN
    SELECT RAISE(ABORT, 'untrusted course origins cannot auto-feature or auto-approve');
END;
CREATE TRIGGER IF NOT EXISTS courses_publish_needs_approval
BEFORE UPDATE OF status ON courses
WHEN NEW.status = 'published' AND OLD.status NOT IN ('approved', 'published')
BEGIN
    SELECT RAISE(ABORT, 'a course must be approved before it can be published');
END;
CREATE TRIGGER IF NOT EXISTS reviews_valid_on_insert
BEFORE INSERT ON reviews
WHEN NEW.status <> 'pending'
BEGIN
    SELECT RAISE(ABORT, 'a new review must start as pending');
END;
"""

INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_courses_url_key ON courses(url_key);
CREATE INDEX IF NOT EXISTS idx_courses_status ON courses(status);
CREATE INDEX IF NOT EXISTS idx_courses_category ON courses(category_id);
CREATE INDEX IF NOT EXISTS idx_courses_provider ON courses(provider_id);
CREATE INDEX IF NOT EXISTS idx_courses_free ON courses(is_free);
CREATE INDEX IF NOT EXISTS idx_courses_difficulty ON courses(difficulty);
CREATE INDEX IF NOT EXISTS idx_courses_published_at ON courses(published_at);
CREATE INDEX IF NOT EXISTS idx_reviews_course_status ON reviews(course_id, status);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_one_open_per_user
    ON reports(reporter_id, target_type, target_id) WHERE status = 'pending' AND reporter_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_actions_created ON moderation_actions(created_at);
"""

BOOL_FIELDS = (
    "duration_verified",
    "certificate_verified",
    "editorial_score_public",
    "editorial_approved",
    "is_featured",
    "is_underrated",
    "needs_verification",
)

TOGGLEABLE_FLAGS = ("is_featured", "is_underrated", "needs_verification")

COURSE_ORDERS = {
    "newest": "COALESCE(courses.published_at, courses.updated_at) DESC, courses.title COLLATE NOCASE",
    "updated": "courses.updated_at DESC",
    "title": "courses.title COLLATE NOCASE",
    "editorial": (
        "(courses.editorial_approved = 1 AND courses.editorial_score_public = 1 AND courses.editorial_score IS NOT NULL) DESC, "
        "CASE WHEN courses.editorial_approved = 1 AND courses.editorial_score_public = 1 THEN courses.editorial_score END DESC, "
        "courses.title COLLATE NOCASE"
    ),
    "price_low": (
        "(CASE WHEN courses.is_free = 1 THEN 0 ELSE courses.price END) IS NULL, "
        "CASE WHEN courses.is_free = 1 THEN 0 ELSE courses.price END ASC, courses.title COLLATE NOCASE"
    ),
    "price_high": (
        "(CASE WHEN courses.is_free = 1 THEN 0 ELSE courses.price END) IS NULL, "
        "CASE WHEN courses.is_free = 1 THEN 0 ELSE courses.price END DESC, courses.title COLLATE NOCASE"
    ),
}

_DUMMY_HASH = None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _row(row):
    return None if row is None else dict(row)


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = generate_password_hash("not-a-real-password")
    return _DUMMY_HASH


class LocalStore:
    backend = "local"

    def __init__(self, path, seed_sample=False, min_rating_count=3):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seed_sample = seed_sample
        self.min_rating_count = int(min_rating_count)

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ping(self):
        with self._conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return True

    def create_owner_session(self, token_hash, expires_at):
        now = _now()
        with self._conn() as conn:
            conn.execute("DELETE FROM owner_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO owner_sessions (token_hash, expires_at, created_at) VALUES (?, ?, ?)",
                (token_hash, expires_at, now),
            )

    def validate_owner_session(self, token_hash):
        with self._conn() as conn:
            return conn.execute(
                "SELECT 1 FROM owner_sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, _now()),
            ).fetchone() is not None

    def revoke_owner_session(self, token_hash):
        with self._conn() as conn:
            conn.execute("DELETE FROM owner_sessions WHERE token_hash = ?", (token_hash,))

    def initialize(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            for table, columns in UPGRADE_COLUMNS.items():
                existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for name, ddl in columns.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            for row in conn.execute("SELECT id, official_url FROM courses WHERE url_key IS NULL").fetchall():
                conn.execute("UPDATE courses SET url_key = ? WHERE id = ?", (normalise_url(row["official_url"]), row["id"]))
            conn.executescript(TRIGGERS)
            conn.executescript(INDEXES)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.ensure_categories()
        if self.seed_sample:
            self.seed_sample_courses()

    def ensure_categories(self):
        with self._conn() as conn:
            if conn.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]:
                return
            for index, (slug, name, description) in enumerate(DEFAULT_CATEGORIES):
                conn.execute(
                    "INSERT INTO categories (id, slug, name, description, sort_order, is_active) VALUES (?,?,?,?,?,1)",
                    (new_id(), slug, name, description, index),
                )

    def seed_sample_courses(self):
        """Load example records as UNVERIFIED DRAFTS for the administrator to review.

        Nothing is published, featured, approved, or marked verified.
        """
        with self._conn() as conn:
            if conn.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"]:
                return
        note = (
            "Sample record loaded for you to review. Every fact, price and editorial comment here is "
            "unverified and was drafted by an AI assistant. Check the official page, then edit or delete."
        )
        for sample in SAMPLE_COURSES:
            provider = self.get_or_create_provider(sample["provider_name"], sample.get("provider_url"))
            category = self.get_category_by_slug(sample["category_slug"])
            payload = dict(sample)
            payload.update(
                provider_id=provider["id"],
                category_id=category["id"] if category else None,
                origin="sample",
                is_featured=False,
                is_underrated=False,
                needs_verification=True,
                last_verified_at=None,
                duration_verified=False,
                certificate_verified=False,
                editorial_approved=False,
                editorial_score_public=False,
                recommendation_status="none",
            )
            try:
                course = self.save_course(payload, origin="sample")
            except DuplicateError:
                continue
            self.set_admin_notes(course["id"], note)

    # --------------------------------------------------------------------- users
    SAFE_USER_COLUMNS = "id, email, display_name, role, is_active, created_at"

    def create_user(self, email, password, display_name=None):
        email = email.strip().lower()
        user_id = new_id()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash, display_name, role, created_at) VALUES (?,?,?,?,'user',?)",
                    (user_id, email, generate_password_hash(password), (display_name or email.split("@")[0]).strip(), _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("An account with this email already exists.") from exc
        return self.get_user(user_id)

    def authenticate(self, email, password):
        with self._conn() as conn:
            row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        # Always do one hash check so response time does not reveal whether the email exists.
        stored = row["password_hash"] if row else _dummy_hash()
        ok = check_password_hash(stored, password)
        if not row or not ok:
            return None
        user = self.get_user(row["id"])
        return user if user and user["is_active"] else None

    def get_user(self, user_id):
        with self._conn() as conn:
            return _row(conn.execute(f"SELECT {self.SAFE_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone())

    def get_user_by_email(self, email):
        with self._conn() as conn:
            return _row(
                conn.execute(f"SELECT {self.SAFE_USER_COLUMNS} FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
            )

    def update_display_name(self, user_id, display_name):
        with self._conn() as conn:
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name.strip(), user_id))
        return self.get_user(user_id)

    def change_password(self, user_id, current_password, new_password):
        with self._conn() as conn:
            row = conn.execute("SELECT id, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            stored = row["password_hash"] if row else _dummy_hash()
            if not row or not check_password_hash(stored, current_password):
                return False
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user_id))
        self.record_action(user_id, "user", user_id, "password_changed")
        return True

    def set_user_role(self, email, role, actor_label="setup-script"):
        if role not in ("user", "admin"):
            raise ValueError("Invalid role")
        with self._conn() as conn:
            cur = conn.execute("UPDATE users SET role = ? WHERE email = ?", (role, email.strip().lower()))
            if cur.rowcount == 0:
                raise ValueError("No user with that email")
        user = self.get_user_by_email(email)
        self.record_action(None, "user", user["id"], "role_changed", to_status=role, label=actor_label)
        return user

    def set_password(self, email, password):
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?", (generate_password_hash(password), email.strip().lower())
            )
            if cur.rowcount == 0:
                raise ValueError("No user with that email")

    def create_first_owner(self, email, password):
        email = email.strip().lower()
        user_id = new_id()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
                raise ValueError("An owner account already exists; first-time setup is disabled.")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise DuplicateError("An account with this email already exists.")
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, 'admin', 1, ?)",
                (
                    user_id,
                    email,
                    generate_password_hash(password),
                    email.split("@", 1)[0],
                    _now(),
                ),
            )
            conn.execute(
                "INSERT INTO moderation_actions "
                "(id, actor_id, entity_type, entity_id, label, action, to_status, created_at) "
                "VALUES (?, NULL, 'user', ?, ?, 'first_owner_created', 'admin', ?)",
                (new_id(), user_id, email, _now()),
            )
        return self.get_user(user_id)

    def count_users(self):
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def list_users(self, q=None, page=1, per_page=50):
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 100))
        clauses, params = [], []
        if q:
            like = f"%{escape_like(q.strip())}%"
            clauses.append("(display_name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\')")
            params.extend((like, like))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS n FROM users{where}", params).fetchone()["n"]
            rows = conn.execute(
                f"SELECT {self.SAFE_USER_COLUMNS} FROM users{where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, per_page, (page - 1) * per_page],
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max((total + per_page - 1) // per_page, 1),
        }

    def count_admins(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()["n"]

    def update_user_role(self, user_id, role, actor_id=None):
        if role not in ("user", "admin"):
            raise ValueError("Invalid role")
        with self._conn() as conn:
            user = conn.execute(
                f"SELECT {self.SAFE_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not user:
                raise NotFoundError("User not found.")
            if user["role"] == role:
                return dict(user)
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        self.record_action(
            actor_id,
            "user",
            user_id,
            "role_changed",
            from_status=user["role"],
            to_status=role,
            label=user["email"],
        )
        return self.get_user(user_id)

    def update_user_active(self, user_id, is_active, actor_id=None):
        with self._conn() as conn:
            user = conn.execute(
                f"SELECT {self.SAFE_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not user:
                raise NotFoundError("User not found.")
            if bool(user["is_active"]) == bool(is_active):
                return dict(user)
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(bool(is_active)), user_id))
        target = "active" if is_active else "suspended"
        previous = "active" if user["is_active"] else "suspended"
        self.record_action(
            actor_id,
            "user",
            user_id,
            "account_status_changed",
            from_status=previous,
            to_status=target,
            label=user["email"],
        )
        return self.get_user(user_id)

    # ---------------------------------------------------------------- categories
    def list_categories(self, active_only=False):
        sql = """
            SELECT categories.*,
                   COUNT(CASE WHEN courses.status = 'published' THEN 1 END) AS published_count,
                   COUNT(courses.id) AS total_count
            FROM categories
            LEFT JOIN courses ON courses.category_id = categories.id
        """
        if active_only:
            sql += " WHERE categories.is_active = 1"
        sql += " GROUP BY categories.id ORDER BY categories.sort_order, categories.name"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_category(self, category_id):
        with self._conn() as conn:
            return _row(conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone())

    def get_category_by_slug(self, slug):
        with self._conn() as conn:
            return _row(conn.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone())

    def save_category(self, data, category_id=None, actor_id=None):
        slug = slugify(data.get("slug") or data["name"])
        values = (
            slug,
            data["name"].strip(),
            data.get("description") or "",
            int(data.get("sort_order") or 0),
            1 if data.get("is_active", True) else 0,
        )
        creating = category_id is None
        try:
            with self._conn() as conn:
                if category_id:
                    conn.execute(
                        "UPDATE categories SET slug=?, name=?, description=?, sort_order=?, is_active=? WHERE id=?",
                        values + (category_id,),
                    )
                else:
                    category_id = new_id()
                    conn.execute(
                        "INSERT INTO categories (slug, name, description, sort_order, is_active, id) VALUES (?,?,?,?,?,?)",
                        values + (category_id,),
                    )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("A category with that name already exists.") from exc
        if actor_id:
            self.record_action(actor_id, "category", category_id, "created" if creating else "edited", label=data["name"].strip())
        return self.get_category(category_id)

    def delete_category(self, category_id, actor_id=None):
        cat = self.get_category(category_id)
        if not cat:
            raise NotFoundError("Category not found.")
        with self._conn() as conn:
            used = conn.execute("SELECT COUNT(*) AS n FROM courses WHERE category_id = ?", (category_id,)).fetchone()["n"]
            if used:
                raise StoreError(f"Cannot delete “{cat['name']}” because {used} course(s) are assigned to it.")
            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        if actor_id:
            self.record_action(actor_id, "category", category_id, "deleted", label=cat["name"])

    # ----------------------------------------------------------------- providers
    def list_providers(self, with_published_courses=False):
        sql = """
            SELECT providers.*,
                   COUNT(CASE WHEN courses.status = 'published' THEN 1 END) AS published_count,
                   COUNT(courses.id) AS total_count
            FROM providers
            LEFT JOIN courses ON courses.provider_id = providers.id
        """
        if with_published_courses:
            sql += " WHERE providers.id IN (SELECT provider_id FROM courses WHERE status = 'published' AND provider_id IS NOT NULL)"
        sql += " GROUP BY providers.id ORDER BY providers.name COLLATE NOCASE"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_provider(self, provider_id):
        with self._conn() as conn:
            return _row(conn.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone())

    def get_or_create_provider(self, name, website_url=None):
        slug = slugify(name)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM providers WHERE slug = ?", (slug,)).fetchone()
            if row:
                return dict(row)
            provider_id = new_id()
            conn.execute(
                "INSERT INTO providers (id, slug, name, website_url) VALUES (?,?,?,?)",
                (provider_id, slug, name.strip(), website_url or ""),
            )
        return self.get_provider(provider_id)

    def save_provider(self, data, provider_id=None, actor_id=None):
        slug = slugify(data.get("slug") or data["name"])
        creating = provider_id is None
        try:
            with self._conn() as conn:
                if provider_id:
                    conn.execute(
                        "UPDATE providers SET slug=?, name=?, website_url=? WHERE id=?",
                        (slug, data["name"].strip(), data.get("website_url") or "", provider_id),
                    )
                else:
                    provider_id = new_id()
                    conn.execute(
                        "INSERT INTO providers (id, slug, name, website_url) VALUES (?,?,?,?)",
                        (provider_id, slug, data["name"].strip(), data.get("website_url") or ""),
                    )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("A provider with that name already exists.") from exc
        if actor_id:
            self.record_action(actor_id, "provider", provider_id, "created" if creating else "edited", label=data["name"].strip())
        return self.get_provider(provider_id)

    def delete_provider(self, provider_id, actor_id=None):
        prov = self.get_provider(provider_id)
        if not prov:
            raise NotFoundError("Provider not found.")
        with self._conn() as conn:
            used = conn.execute("SELECT COUNT(*) AS n FROM courses WHERE provider_id = ?", (provider_id,)).fetchone()["n"]
            if used:
                raise StoreError(f"Cannot delete “{prov['name']}” because {used} course(s) reference it.")
            conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        if actor_id:
            self.record_action(actor_id, "provider", provider_id, "deleted", label=prov["name"])

    # ------------------------------------------------------------------- courses
    COURSE_SELECT = """
        SELECT courses.*,
               providers.name AS provider_name,
               providers.website_url AS provider_url,
               categories.name AS category_name,
               categories.slug AS category_slug,
               rs.avg_rating AS _avg_rating,
               rs.n_reviews AS _n_reviews
        FROM courses
        LEFT JOIN providers ON providers.id = courses.provider_id
        LEFT JOIN categories ON categories.id = courses.category_id
        LEFT JOIN (
            SELECT course_id, AVG(rating) AS avg_rating, COUNT(*) AS n_reviews
            FROM reviews WHERE status = 'approved' GROUP BY course_id
        ) rs ON rs.course_id = courses.id
    """

    def _finish(self, course):
        count = int(course.pop("_n_reviews", None) or 0)
        avg = course.pop("_avg_rating", None)
        course["review_count"] = count
        course["user_rating"] = round(float(avg), 1) if count >= self.min_rating_count and avg is not None else None
        course["user_rating_hidden_reason"] = (
            f"User ratings are shown after {self.min_rating_count} approved reviews."
            if 0 < count < self.min_rating_count
            else None
        )
        if course.get("is_free") is not None:
            course["is_free"] = bool(course["is_free"])
        if course.get("certificate_available") is not None:
            course["certificate_available"] = bool(course["certificate_available"])
        for flag in BOOL_FIELDS:
            if course.get(flag) is not None:
                course[flag] = bool(course[flag])
        return course

    def get_course(self, course_id=None, slug=None, include_unpublished=False):
        if course_id:
            sql, params = self.COURSE_SELECT + " WHERE courses.id = ?", [course_id]
        else:
            sql, params = self.COURSE_SELECT + " WHERE courses.slug = ?", [slug]
        if not include_unpublished:
            sql += " AND courses.status = 'published'"
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return self._finish(dict(row)) if row else None

    def find_duplicates(self, title, official_url, exclude_id=None):
        """Courses that share this title (case-insensitive) or this official URL."""
        sql = self.COURSE_SELECT + " WHERE (LOWER(courses.title) = LOWER(?) OR courses.url_key = ?)"
        params = [title.strip(), normalise_url(official_url)]
        if exclude_id:
            sql += " AND courses.id != ?"
            params.append(exclude_id)
        with self._conn() as conn:
            return [self._finish(dict(r)) for r in conn.execute(sql, params).fetchall()]

    def find_url_duplicate(self, official_url, exclude_id=None):
        sql = "SELECT id, title, status FROM courses WHERE url_key = ?"
        params = [normalise_url(official_url)]
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        with self._conn() as conn:
            return _row(conn.execute(sql, params).fetchone())

    def list_courses(
        self,
        q=None,
        category_slug=None,
        provider_id=None,
        pricing=None,
        difficulty=None,
        language=None,
        min_rating=None,
        sort="newest",
        page=1,
        per_page=12,
        include_unpublished=False,
        public_only=None,
        status=None,
        featured_only=False,
        underrated_only=False,
        needs_verification_only=False,
        exclude_id=None,
    ):
        if public_only is not None:
            include_unpublished = not bool(public_only)
        clauses, params = [], []
        if not include_unpublished:
            clauses.append("courses.status = 'published'")
        if status:
            clauses.append("courses.status = ?")
            params.append(status)
        if featured_only:
            clauses.append("courses.is_featured = 1")
        if underrated_only:
            clauses.append("courses.is_underrated = 1")
        if needs_verification_only:
            clauses.append("courses.needs_verification = 1")
        if category_slug:
            clauses.append("categories.slug = ?")
            params.append(category_slug)
        if provider_id:
            clauses.append("courses.provider_id = ?")
            params.append(provider_id)
        if pricing == "free":
            clauses.append("courses.is_free = 1")
        elif pricing == "paid":
            clauses.append("courses.is_free = 0")
        elif pricing == "unknown":
            clauses.append("courses.is_free IS NULL")
        if difficulty:
            clauses.append("courses.difficulty = ?")
            params.append(difficulty)
        if language:
            clauses.append("LOWER(courses.language) = LOWER(?)")
            params.append(language)
        if min_rating:
            clauses.append(f"rs.n_reviews >= {self.min_rating_count} AND rs.avg_rating >= ?")
            params.append(float(min_rating))
        if exclude_id:
            clauses.append("courses.id != ?")
            params.append(exclude_id)
        fields = (
            "courses.title",
            "providers.name",
            "courses.keywords",
            "courses.short_description",
            "courses.skills_taught",
            "courses.subcategory",
        )
        for term in search_terms(q):
            like = f"%{escape_like(term)}%"
            clauses.append("(" + " OR ".join(f"LOWER(IFNULL({f}, '')) LIKE ? ESCAPE '\\'" for f in fields) + ")")
            params.extend([like] * len(fields))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        n = self.min_rating_count
        orders = dict(COURSE_ORDERS)
        orders["rating"] = (
            f"(rs.n_reviews >= {n}) DESC, CASE WHEN rs.n_reviews >= {n} THEN rs.avg_rating END DESC, "
            "courses.title COLLATE NOCASE"
        )
        order = orders.get(sort) if sort in orders and (sort != "updated" or include_unpublished) else orders["newest"]

        per_page = max(1, min(int(per_page), 100))
        page = max(1, int(page))
        offset = (page - 1) * per_page
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS n FROM ({self.COURSE_SELECT} {where})", params).fetchone()["n"]
            rows = conn.execute(
                f"{self.COURSE_SELECT} {where} ORDER BY {order} LIMIT ? OFFSET ?", params + [per_page, offset]
            ).fetchall()
        return {
            "items": [self._finish(dict(r)) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max((total + per_page - 1) // per_page, 1),
        }

    def has_public_ratings(self):
        sql = f"""
            SELECT 1 FROM reviews JOIN courses ON courses.id = reviews.course_id
            WHERE reviews.status = 'approved' AND courses.status = 'published'
            GROUP BY reviews.course_id HAVING COUNT(*) >= {self.min_rating_count} LIMIT 1
        """
        with self._conn() as conn:
            return conn.execute(sql).fetchone() is not None

    def public_languages(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT language FROM courses WHERE status = 'published' AND language IS NOT NULL AND language <> '' ORDER BY language"
            ).fetchall()
        return [r["language"] for r in rows]

    def similar_courses(self, course, limit=4):
        """Published courses in the same category, ranked by shared difficulty, provider and price type."""
        if not course.get("category_id"):
            return []
        free = None if course.get("is_free") is None else int(bool(course["is_free"]))
        sql = (
            self.COURSE_SELECT
            + """ WHERE courses.status = 'published' AND courses.id != ? AND courses.category_id = ?
                  ORDER BY ((courses.difficulty = ?) + (courses.provider_id = ?) + (courses.is_free = ?)) DESC,
                           COALESCE(courses.published_at, courses.updated_at) DESC
                  LIMIT ?"""
        )
        params = [
            course["id"],
            course["category_id"],
            course.get("difficulty") or "",
            course.get("provider_id") or "",
            free,
            int(limit),
        ]
        with self._conn() as conn:
            return [self._finish(dict(r)) for r in conn.execute(sql, params).fetchall()]

    # Fields an administrator can edit through save_course().
    CONTENT_FIELDS = (
        "title",
        "provider_id",
        "category_id",
        "subcategory",
        "official_url",
        "short_description",
        "description",
        "language",
        "is_free",
        "price",
        "currency",
        "pricing_notes",
        "difficulty",
        "prerequisites",
        "duration",
        "duration_verified",
        "certificate_available",
        "certificate_verified",
        "teaching_format",
        "skills_taught",
        "learning_outcomes",
        "intended_audience",
        "strengths",
        "limitations",
        "editorial_assessment",
        "editorial_score",
        "editorial_score_public",
        "editorial_why_recommend",
        "editorial_who_should_take",
        "editorial_who_should_avoid",
        "editorial_value_for_money",
        "editorial_approved",
        "recommendation_status",
        "last_verified_at",
        "is_featured",
        "is_underrated",
        "needs_verification",
        "keywords",
        "source_url",
        "source_checked_at",
    )

    @staticmethod
    def _flag(value):
        if isinstance(value, str):
            return 1 if value.strip().lower() in ("1", "true", "on", "yes") else 0
        return 1 if value else 0

    @staticmethod
    def _optional_flag(value):
        if value is None or value == "":
            return None
        return LocalStore._flag(value)

    def _unique_slug(self, conn, base, exclude_id=None):
        slug, n = base, 1
        while True:
            row = conn.execute("SELECT id FROM courses WHERE slug = ?", (slug,)).fetchone()
            if not row or row["id"] == exclude_id:
                return slug
            n += 1
            slug = f"{base}-{n}"

    def create_course(self, data, actor_id=None, origin=None):
        """Create a new course record. Always starts as a draft regardless of input."""
        return self.save_course(data, course_id=None, actor_id=actor_id, origin=origin)

    def save_course(self, data, course_id=None, actor_id=None, origin=None):
        """Create or update a course's CONTENT. Never changes its status.

        New records are always drafts. Records that arrive from an import, a user
        submission, an AI suggestion or the sample loader can never be featured,
        underrated, or editorially approved.
        """
        now = _now()
        url = data["official_url"].strip()
        fields = {
            "title": data["title"].strip(),
            "provider_id": data.get("provider_id") or None,
            "category_id": data.get("category_id") or None,
            "subcategory": data.get("subcategory") or "",
            "official_url": url,
            "url_key": normalise_url(url),
            "short_description": data.get("short_description") or "",
            "description": data.get("description") or "",
            "language": (data.get("language") or "").strip(),
            "is_free": self._optional_flag(data.get("is_free")),
            "price": float(data["price"]) if data.get("price") not in (None, "") else None,
            "currency": (data.get("currency") or "USD").strip().upper(),
            "pricing_notes": data.get("pricing_notes") or "",
            "difficulty": data.get("difficulty") or "",
            "prerequisites": data.get("prerequisites") or "",
            "duration": data.get("duration") or "",
            "duration_verified": self._flag(data.get("duration_verified")),
            "certificate_available": self._optional_flag(data.get("certificate_available")),
            "certificate_verified": self._flag(data.get("certificate_verified")),
            "teaching_format": data.get("teaching_format") or "",
            "skills_taught": data.get("skills_taught") or "",
            "learning_outcomes": data.get("learning_outcomes") or "",
            "intended_audience": data.get("intended_audience") or "",
            "strengths": data.get("strengths") or "",
            "limitations": data.get("limitations") or "",
            "editorial_assessment": data.get("editorial_assessment") or "",
            "editorial_score": float(data["editorial_score"]) if data.get("editorial_score") not in (None, "") else None,
            "editorial_score_public": self._flag(data.get("editorial_score_public")),
            "editorial_why_recommend": data.get("editorial_why_recommend") or "",
            "editorial_who_should_take": data.get("editorial_who_should_take") or "",
            "editorial_who_should_avoid": data.get("editorial_who_should_avoid") or "",
            "editorial_value_for_money": data.get("editorial_value_for_money") or "",
            "editorial_approved": self._flag(data.get("editorial_approved")),
            "recommendation_status": data.get("recommendation_status") or "none",
            "last_verified_at": data.get("last_verified_at") or None,
            "is_featured": self._flag(data.get("is_featured")),
            "is_underrated": self._flag(data.get("is_underrated")),
            "needs_verification": self._flag(data.get("needs_verification")),
            "keywords": data.get("keywords") or "",
            "source_url": data.get("source_url") or None,
            "source_checked_at": data.get("source_checked_at") or None,
            "updated_at": now,
        }
        if "thumbnail_path" in data and data["thumbnail_path"] is not None:
            fields["thumbnail_path"] = data["thumbnail_path"] or None
        creating = course_id is None
        record_origin = (origin or data.get("origin") or "admin") if creating else None
        if creating and record_origin in UNTRUSTED_ORIGINS:
            fields.update(
                is_featured=0,
                is_underrated=0,
                editorial_approved=0,
                editorial_score_public=0,
                recommendation_status="none",
                needs_verification=1,
            )
        try:
            with self._conn() as conn:
                if creating:
                    course_id = new_id()
                    fields["slug"] = self._unique_slug(conn, slugify(data.get("slug") or data["title"]))
                    fields.update(id=course_id, origin=record_origin, status="draft", created_at=now)
                    cols = list(fields)
                    conn.execute(
                        f"INSERT INTO courses ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [fields[c] for c in cols],
                    )
                else:
                    if not conn.execute("SELECT 1 FROM courses WHERE id = ?", (course_id,)).fetchone():
                        raise NotFoundError("Course not found.")
                    if data.get("slug"):
                        fields["slug"] = self._unique_slug(conn, slugify(data["slug"]), exclude_id=course_id)
                    cols = list(fields)
                    conn.execute(
                        f"UPDATE courses SET {', '.join(c + '=?' for c in cols)} WHERE id = ?",
                        [fields[c] for c in cols] + [course_id],
                    )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("A course with this official URL already exists.") from exc
        self.record_action(actor_id, "course", course_id, "created" if creating else "edited", label=fields["title"])
        return self.get_course(course_id=course_id, include_unpublished=True)

    def toggle_course_flag(self, course_id, flag, value=None, actor_id=None):
        """Toggle or explicitly set a boolean editorial flag (is_featured, is_underrated, needs_verification)."""
        if flag not in TOGGLEABLE_FLAGS:
            raise ValueError(f"Unsupported flag: {flag}")
        course = self.get_course(course_id=course_id, include_unpublished=True)
        if not course:
            raise NotFoundError("Course not found.")
        old_val = bool(course.get(flag))
        new_val = (not old_val) if value is None else bool(value)
        now = _now()
        with self._conn() as conn:
            conn.execute(f"UPDATE courses SET {flag} = ?, updated_at = ? WHERE id = ?", (1 if new_val else 0, now, course_id))
        self.record_action(
            actor_id,
            "course",
            course_id,
            f"flag_{flag}",
            from_status="on" if old_val else "off",
            to_status="on" if new_val else "off",
            label=course["title"],
        )
        return self.get_course(course_id=course_id, include_unpublished=True)

    def set_course_status(self, course_id, target, actor_id=None, note=None):
        course = self.get_course(course_id=course_id, include_unpublished=True)
        if not course:
            raise NotFoundError("Course not found.")
        workflow.check_transition(course["status"], target)  # raises WorkflowError
        now = _now()
        with self._conn() as conn:
            if target == workflow.PUBLISHED:
                conn.execute(
                    "UPDATE courses SET status = ?, updated_at = ?, published_at = ? WHERE id = ?",
                    (target, now, now, course_id),
                )
            else:
                conn.execute("UPDATE courses SET status = ?, updated_at = ? WHERE id = ?", (target, now, course_id))
        self.record_action(
            actor_id,
            "course",
            course_id,
            "status_changed",
            from_status=course["status"],
            to_status=target,
            note=note,
            label=course["title"],
        )
        return self.get_course(course_id=course_id, include_unpublished=True)

    def delete_course(self, course_id, actor_id=None):
        course = self.get_course(course_id=course_id, include_unpublished=True)
        if not course:
            raise NotFoundError("Course not found.")
        if course["status"] == workflow.PUBLISHED:
            raise workflow.WorkflowError("Unpublish or archive a live course before deleting it.")
        with self._conn() as conn:
            conn.execute("DELETE FROM reports WHERE target_type = 'course' AND target_id = ?", (course_id,))
            conn.execute("UPDATE course_submissions SET course_id = NULL WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))  # reviews/saved/notes cascade
        self.record_action(actor_id, "course", course_id, "deleted", from_status=course["status"], label=course["title"])

    def get_admin_notes(self, course_id):
        with self._conn() as conn:
            row = conn.execute("SELECT notes FROM course_admin_notes WHERE course_id = ?", (course_id,)).fetchone()
        return row["notes"] if row else ""

    def set_admin_notes(self, course_id, notes):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO course_admin_notes (course_id, notes, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(course_id) DO UPDATE SET notes = excluded.notes, updated_at = excluded.updated_at",
                (course_id, (notes or "").strip(), _now()),
            )

    def export_courses(self):
        with self._conn() as conn:
            rows = conn.execute(self.COURSE_SELECT + " ORDER BY courses.title COLLATE NOCASE").fetchall()
            notes = {r["course_id"]: r["notes"] for r in conn.execute("SELECT course_id, notes FROM course_admin_notes")}
        out = []
        for row in rows:
            course = self._finish(dict(row))
            course["admin_notes"] = notes.get(course["id"], "")
            out.append(course)
        return out

    def dashboard_counts(self):
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).date().isoformat()
        with self._conn() as conn:

            def count(sql, params=()):
                return conn.execute(sql, params).fetchone()["n"]

            by_status = {s: count("SELECT COUNT(*) AS n FROM courses WHERE status = ?", (s,)) for s in workflow.STATUSES}
            return {
                "total_courses": count("SELECT COUNT(*) AS n FROM courses"),
                "published_courses": by_status["published"],
                "draft_courses": by_status["draft"],
                "pending_courses": by_status["pending_review"],
                "approved_courses": by_status["approved"],
                "archived_courses": by_status["archived"],
                "unpublished_courses": sum(v for k, v in by_status.items() if k != "published"),
                "free_courses": count("SELECT COUNT(*) AS n FROM courses WHERE is_free = 1"),
                "paid_courses": count("SELECT COUNT(*) AS n FROM courses WHERE is_free = 0"),
                "unknown_pricing_courses": count("SELECT COUNT(*) AS n FROM courses WHERE is_free IS NULL"),
                "free_published_courses": count("SELECT COUNT(*) AS n FROM courses WHERE is_free = 1 AND status = 'published'"),
                "paid_published_courses": count("SELECT COUNT(*) AS n FROM courses WHERE is_free = 0 AND status = 'published'"),
                "featured_courses": count("SELECT COUNT(*) AS n FROM courses WHERE is_featured = 1 AND status = 'published'"),
                "underrated_courses": count("SELECT COUNT(*) AS n FROM courses WHERE is_underrated = 1 AND status = 'published'"),
                "total_users": count("SELECT COUNT(*) AS n FROM users"),
                "total_reviews": count("SELECT COUNT(*) AS n FROM reviews"),
                "reviews_pending": count("SELECT COUNT(*) AS n FROM reviews WHERE status = 'pending'"),
                "reports_pending": count("SELECT COUNT(*) AS n FROM reports WHERE status = 'pending'"),
                "submissions_pending": count("SELECT COUNT(*) AS n FROM course_submissions WHERE status = 'pending'"),
                "messages_unhandled": count("SELECT COUNT(*) AS n FROM contact_messages WHERE is_handled = 0"),
                "needs_verification": count("SELECT COUNT(*) AS n FROM courses WHERE needs_verification = 1"),
                "stale_verification": count(
                    "SELECT COUNT(*) AS n FROM courses WHERE status = 'published' AND (last_verified_at IS NULL OR last_verified_at < ?)",
                    (stale_cutoff,),
                ),
            }

    # ------------------------------------------------------------------- reviews
    def add_review(self, course_id, user_id, rating, body):
        """Create the user's review, or replace it. Either way it goes back to 'pending'."""
        course = self.get_course(course_id=course_id)  # published only
        if not course:
            raise NotFoundError("That course is not available.")
        now = _now()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM reviews WHERE course_id = ? AND user_id = ?", (course_id, user_id)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE reviews SET rating = ?, body = ?, status = 'pending', updated_at = ? WHERE id = ?",
                    (int(rating), body.strip(), now, existing["id"]),
                )
                return existing["id"], False
            review_id = new_id()
            conn.execute(
                "INSERT INTO reviews (id, course_id, user_id, rating, body, status, created_at) VALUES (?,?,?,?,?,'pending',?)",
                (review_id, course_id, user_id, int(rating), body.strip(), now),
            )
            return review_id, True

    def get_review(self, review_id):
        with self._conn() as conn:
            return _row(conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone())

    def get_user_review(self, course_id, user_id):
        with self._conn() as conn:
            return _row(
                conn.execute("SELECT * FROM reviews WHERE course_id = ? AND user_id = ?", (course_id, user_id)).fetchone()
            )

    def list_reviews(self, course_id=None, status=None, user_id=None, for_admin=False):
        email = ", users.email" if for_admin else ""
        sql = f"""
            SELECT reviews.*, users.display_name{email}, courses.title AS course_title, courses.slug AS course_slug,
                   courses.status AS course_status
            FROM reviews
            JOIN users ON users.id = reviews.user_id
            JOIN courses ON courses.id = reviews.course_id
            WHERE 1=1
        """
        params = []
        if course_id:
            sql += " AND reviews.course_id = ?"
            params.append(course_id)
        if status:
            sql += " AND reviews.status = ?"
            params.append(status)
        if user_id:
            sql += " AND reviews.user_id = ?"
            params.append(user_id)
        if not for_admin and not user_id:
            sql += " AND courses.status = 'published'"
        sql += " ORDER BY reviews.created_at DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def set_review_status(self, review_id, status, actor_id=None):
        if status not in REVIEW_STATUSES:
            raise ValueError("Invalid review status")
        review = self.get_review(review_id)
        if not review:
            raise NotFoundError("Review not found.")
        with self._conn() as conn:
            conn.execute("UPDATE reviews SET status = ? WHERE id = ?", (status, review_id))
        self.record_action(actor_id, "review", review_id, "status_changed", from_status=review["status"], to_status=status)

    def delete_review(self, review_id, user_id=None, actor_id=None):
        """Delete a review. When ``user_id`` is given, only the author's own review can be deleted."""
        review = self.get_review(review_id)
        if not review or (user_id and review["user_id"] != user_id):
            return False
        with self._conn() as conn:
            conn.execute("DELETE FROM reports WHERE target_type = 'review' AND target_id = ?", (review_id,))
            conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        if actor_id:
            self.record_action(actor_id, "review", review_id, "deleted", from_status=review["status"])
        return True

    # --------------------------------------------------------------------- saved
    def save_course_for_user(self, user_id, course_id):
        if not self.get_course(course_id=course_id):
            raise NotFoundError("That course is not available.")
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO saved_courses (user_id, course_id, created_at) VALUES (?,?,?)",
                (user_id, course_id, _now()),
            )

    def unsave_course_for_user(self, user_id, course_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM saved_courses WHERE user_id = ? AND course_id = ?", (user_id, course_id))

    def is_saved(self, user_id, course_id):
        with self._conn() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM saved_courses WHERE user_id = ? AND course_id = ?", (user_id, course_id)
                ).fetchone()
                is not None
            )

    def list_saved(self, user_id):
        sql = (
            self.COURSE_SELECT
            + " JOIN saved_courses ON saved_courses.course_id = courses.id"
            + " WHERE saved_courses.user_id = ? AND courses.status = 'published' ORDER BY saved_courses.created_at DESC"
        )
        with self._conn() as conn:
            return [self._finish(dict(r)) for r in conn.execute(sql, (user_id,)).fetchall()]

    # ------------------------------------------------------------------- reports
    def add_report(self, target_type, target_id, reporter_id, reason, details=""):
        """Record a report. Returns the new id, or None if this user already has an open report on it."""
        valid = COURSE_REPORT_REASONS if target_type == "course" else REVIEW_REPORT_REASONS
        if target_type not in ("course", "review") or reason not in {r for r, _ in valid}:
            raise ValueError("Invalid report")
        if target_type == "course" and not self.get_course(course_id=target_id):
            raise NotFoundError("That course is not available.")
        if target_type == "review":
            review = self.get_review(target_id)
            if not review or review["status"] != "approved":
                raise NotFoundError("That review is not available.")
        report_id = new_id()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO reports (id, target_type, target_id, reporter_id, reason, details, status, created_at) "
                    "VALUES (?,?,?,?,?,?, 'pending', ?)",
                    (report_id, target_type, target_id, reporter_id, reason, (details or "").strip(), _now()),
                )
        except sqlite3.IntegrityError:
            return None
        return report_id

    def list_reports(self, status="pending"):
        sql = """
            SELECT reports.*, reporters.display_name AS reporter_name,
                   CASE reports.target_type WHEN 'course' THEN c.title ELSE rc.title END AS course_title,
                   CASE reports.target_type WHEN 'course' THEN c.id ELSE rc.id END AS course_id,
                   r.body AS review_body
            FROM reports
            LEFT JOIN users reporters ON reporters.id = reports.reporter_id
            LEFT JOIN courses c ON reports.target_type = 'course' AND c.id = reports.target_id
            LEFT JOIN reviews r ON reports.target_type = 'review' AND r.id = reports.target_id
            LEFT JOIN courses rc ON rc.id = r.course_id
        """
        params = []
        if status:
            sql += " WHERE reports.status = ?"
            params.append(status)
        sql += " ORDER BY reports.created_at DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def resolve_report(self, report_id, status, actor_id=None):
        if status not in ("resolved", "dismissed"):
            raise ValueError("Invalid report status")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE reports SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
                (status, _now(), actor_id, report_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Report not found.")
        self.record_action(actor_id, "report", report_id, status)

    # --------------------------------------------------------------- submissions
    def add_submission(self, submitter_id, title, official_url, provider_name="", note=""):
        sub_id = new_id()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO course_submissions (id, submitter_id, title, official_url, provider_name, note, status, created_at) "
                "VALUES (?,?,?,?,?,?, 'pending', ?)",
                (
                    sub_id,
                    submitter_id,
                    title.strip(),
                    official_url.strip(),
                    (provider_name or "").strip(),
                    (note or "").strip(),
                    _now(),
                ),
            )
        return sub_id

    def count_recent_submissions(self, submitter_id, hours=24):
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM course_submissions WHERE submitter_id = ? AND created_at >= ?",
                (submitter_id, since),
            ).fetchone()["n"]

    def get_submission(self, submission_id):
        with self._conn() as conn:
            return _row(conn.execute("SELECT * FROM course_submissions WHERE id = ?", (submission_id,)).fetchone())

    def list_submissions(self, status="pending"):
        sql = (
            "SELECT s.*, u.display_name AS submitter_name, c.slug AS course_slug, c.status AS course_status "
            "FROM course_submissions s "
            "LEFT JOIN users u ON u.id = s.submitter_id "
            "LEFT JOIN courses c ON c.id = s.course_id"
        )
        params = []
        if status:
            sql += " WHERE s.status = ?"
            params.append(status)
        sql += " ORDER BY s.created_at DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def set_submission_status(self, submission_id, status, actor_id=None, course_id=None):
        if status not in SUBMISSION_STATUSES or status == "pending":
            raise ValueError("Invalid submission status")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE course_submissions SET status = ?, course_id = ?, reviewed_at = ?, reviewed_by = ? WHERE id = ?",
                (status, course_id, _now(), actor_id, submission_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Submission not found.")
        self.record_action(actor_id, "submission", submission_id, status)

    # ------------------------------------------------------------------- imports
    def create_import_batch(self, admin_id, filename, payload):
        batch_id = new_id()
        admin_id = admin_id or "owner-studio-session"
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM import_batches WHERE created_at < ?",
                ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),),
            )
            conn.execute(
                "INSERT INTO import_batches (id, admin_id, filename, payload, created_at) VALUES (?,?,?,?,?)",
                (batch_id, admin_id, filename, payload, _now()),
            )
        return batch_id

    def get_import_batch(self, batch_id, admin_id):
        admin_id = admin_id or "owner-studio-session"
        with self._conn() as conn:
            return _row(
                conn.execute(
                    "SELECT * FROM import_batches WHERE id = ? AND admin_id = ?", (batch_id, admin_id)
                ).fetchone()
            )

    def delete_import_batch(self, batch_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))

    # --------------------------------------------------------------- audit trail
    def record_action(
        self, actor_id, entity_type, entity_id, action, from_status=None, to_status=None, note=None, label=None
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO moderation_actions (id, actor_id, entity_type, entity_id, label, action, from_status, to_status, note, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (new_id(), actor_id, entity_type, entity_id, label, action, from_status, to_status, note, _now()),
            )

    def list_actions(self, limit=100, entity_id=None):
        sql = "SELECT a.*, u.display_name AS actor_name FROM moderation_actions a LEFT JOIN users u ON u.id = a.actor_id"
        params = []
        if entity_id:
            sql += " WHERE a.entity_id = ?"
            params.append(entity_id)
        sql += " ORDER BY a.created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------- contact
    def add_contact_message(self, name, email, message):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO contact_messages (id, name, email, message, created_at) VALUES (?,?,?,?,?)",
                (new_id(), name.strip(), email.strip(), message.strip(), _now()),
            )

    def list_contact_messages(self, handled=None):
        sql, params = "SELECT * FROM contact_messages", []
        if handled is not None:
            sql += " WHERE is_handled = ?"
            params.append(1 if handled else 0)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def set_contact_handled(self, message_id, handled=True):
        with self._conn() as conn:
            conn.execute(
                "UPDATE contact_messages SET is_handled = ?, handled_at = ? WHERE id = ?",
                (1 if handled else 0, _now() if handled else None, message_id),
            )
