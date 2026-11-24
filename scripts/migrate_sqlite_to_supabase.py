"""Idempotently migrate CourseFinder's local catalogue to its configured Supabase.

Run without --apply for a read-only preflight. The apply mode requires a verified
SQLite backup and never deletes or replaces remote records. PostgREST does not
provide a multi-table transaction, so every step is safe to resume after failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import workflow
from app.config import Config
from app.store.constants import UNTRUSTED_ORIGINS
from app.store.factory import create_store
from app.utils import normalise_url

BOOLEAN_FIELDS = {
    "duration_verified", "certificate_available", "certificate_verified",
    "editorial_score_public", "editorial_approved", "is_featured",
    "is_underrated", "needs_verification", "is_free",
}
NUMERIC_FIELDS = {"price", "editorial_score"}
TIMESTAMP_FIELDS = {"published_at", "created_at", "updated_at", "last_verified_at", "source_checked_at"}
COURSE_FIELDS = (
    "id", "slug", "title", "provider_id", "category_id", "subcategory", "official_url", "url_key",
    "short_description", "description", "language", "is_free", "price", "currency", "pricing_notes",
    "difficulty", "prerequisites", "duration", "duration_verified", "certificate_available",
    "certificate_verified", "teaching_format", "skills_taught", "learning_outcomes", "intended_audience",
    "strengths", "limitations", "editorial_assessment", "editorial_score", "editorial_score_public",
    "editorial_why_recommend", "editorial_who_should_take", "editorial_who_should_avoid",
    "editorial_value_for_money", "editorial_approved", "recommendation_status", "last_verified_at",
    "thumbnail_path", "status", "is_featured", "is_underrated", "needs_verification", "keywords", "origin",
    "source_url", "source_checked_at", "published_at", "created_at", "updated_at",
)
ANCILLARY_EMPTY_TABLES = ("reviews", "saved_courses", "course_submissions", "reports", "contact_messages")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_source(database_path: Path) -> dict:
    if not database_path.is_file():
        raise RuntimeError(f"SQLite source database is missing: {database_path}")
    connection = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("SQLite integrity check failed; migration stopped.")
        categories = [dict(row) for row in connection.execute("SELECT * FROM categories ORDER BY slug")]
        providers = [dict(row) for row in connection.execute("SELECT * FROM providers ORDER BY slug")]
        courses = [dict(row) for row in connection.execute("SELECT * FROM courses ORDER BY id")]
        notes = [dict(row) for row in connection.execute("SELECT * FROM course_admin_notes ORDER BY course_id")]
        actions = [dict(row) for row in connection.execute("SELECT * FROM moderation_actions ORDER BY id")]
        users = [dict(row) for row in connection.execute("SELECT id,email FROM users")]
        ancillary = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ANCILLARY_EMPTY_TABLES
        }
        bad_category_refs = connection.execute(
            "SELECT COUNT(*) FROM courses c LEFT JOIN categories x ON x.id=c.category_id "
            "WHERE c.category_id IS NOT NULL AND x.id IS NULL"
        ).fetchone()[0]
        bad_provider_refs = connection.execute(
            "SELECT COUNT(*) FROM courses c LEFT JOIN providers x ON x.id=c.provider_id "
            "WHERE c.provider_id IS NOT NULL AND x.id IS NULL"
        ).fetchone()[0]
        bad_note_refs = connection.execute(
            "SELECT COUNT(*) FROM course_admin_notes n LEFT JOIN courses c ON c.id=n.course_id "
            "WHERE c.id IS NULL"
        ).fetchone()[0]
        status_counts = dict(connection.execute("SELECT status,COUNT(*) FROM courses GROUP BY status"))
        return {
            "categories": categories, "providers": providers, "courses": courses, "notes": notes,
            "actions": actions, "users": users, "ancillary": ancillary, "status_counts": status_counts,
            "bad_category_refs": bad_category_refs, "bad_provider_refs": bad_provider_refs,
            "bad_note_refs": bad_note_refs,
        }
    finally:
        connection.close()


def as_bool(value):
    return None if value is None else bool(value)


def canonical_timestamp(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return str(value)


def same_value(field, actual, expected):
    if field in BOOLEAN_FIELDS:
        return as_bool(actual) == as_bool(expected)
    if field in TIMESTAMP_FIELDS:
        return canonical_timestamp(actual) == canonical_timestamp(expected)
    if field in NUMERIC_FIELDS and actual is not None and expected is not None:
        return abs(float(actual) - float(expected)) < 1e-8
    return actual == expected


def expected_course(source, category_id, provider_id):
    data = {field: source.get(field) for field in COURSE_FIELDS}
    data["category_id"] = category_id
    data["provider_id"] = provider_id
    for field in BOOLEAN_FIELDS:
        data[field] = as_bool(data.get(field))
    data["url_key"] = source.get("url_key") or normalise_url(source["official_url"])
    return data


def transition_path(current, target):
    paths = {
        workflow.DRAFT: [workflow.DRAFT],
        workflow.PENDING_REVIEW: [workflow.DRAFT, workflow.PENDING_REVIEW],
        workflow.APPROVED: [workflow.DRAFT, workflow.PENDING_REVIEW, workflow.APPROVED],
        workflow.PUBLISHED: [workflow.DRAFT, workflow.PENDING_REVIEW, workflow.APPROVED, workflow.PUBLISHED],
    }
    path = paths[target]
    if current not in path:
        raise RuntimeError(f"Unexpected remote status {current!r} for source status {target!r}; stopped safely.")
    return path[path.index(current) + 1 :]


def verified_backup(database: Path, backup: Path) -> None:
    if database.resolve() == backup.resolve():
        raise RuntimeError("Backup must be a separate file from the SQLite source.")
    if not backup.is_file() or sha256(database) != sha256(backup):
        raise RuntimeError("Backup is missing or does not exactly match the SQLite source.")
    conn = sqlite3.connect(f"file:{backup.resolve()}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed.")
    finally:
        conn.close()


def preflight(store, source):
    if store.backend != "supabase":
        raise RuntimeError("Configured DATA_BACKEND must be supabase.")
    remote_categories = store._table("categories").select("id,slug,name").execute().data or []
    remote_providers = store._table("providers").select("id,slug,name,website_url").execute().data or []
    remote_courses = store._table("courses").select("*").execute().data or []
    category_by_slug = {item["slug"]: item for item in remote_categories}
    provider_by_slug = {item["slug"]: item for item in remote_providers}
    source_category_by_id = {item["id"]: item for item in source["categories"]}
    source_provider_by_id = {item["id"]: item for item in source["providers"]}

    if len(category_by_slug) != len(remote_categories):
        raise RuntimeError("Duplicate category slugs exist in Supabase.")
    if {item["slug"] for item in source["categories"]} != set(category_by_slug):
        raise RuntimeError("SQLite and Supabase category slug sets differ; no records were changed.")
    source_provider_slugs = {item["slug"] for item in source["providers"]}
    if set(provider_by_slug) - source_provider_slugs:
        raise RuntimeError("Supabase has provider rows outside this source catalogue; refusing to merge silently.")
    if source["bad_category_refs"] or source["bad_provider_refs"] or source["bad_note_refs"]:
        raise RuntimeError("SQLite contains broken course or note references; no records were changed.")
    if any(source["ancillary"].values()):
        raise RuntimeError(
            "Local reviews, saved courses, submissions, reports or contact messages exist. "
            "This migration intentionally stops until those relationships receive a dedicated mapping."
        )

    for row in source["courses"]:
        if row["status"] not in (workflow.DRAFT, workflow.APPROVED, workflow.PUBLISHED):
            raise RuntimeError(f"Unsupported local course status for {row['id']}.")
        local_category = source_category_by_id.get(row.get("category_id"))
        if row.get("category_id") and not local_category:
            raise RuntimeError(f"Missing local category for course {row['id']}.")
        if local_category and local_category["slug"] not in category_by_slug:
            raise RuntimeError(f"Missing remote category slug for course {row['id']}.")
        if row.get("provider_id") and row["provider_id"] not in source_provider_by_id:
            raise RuntimeError(f"Missing local provider for course {row['id']}.")

    source_ids = {row["id"] for row in source["courses"]}
    if any(row["id"] not in source_ids for row in remote_courses):
        raise RuntimeError("Supabase contains unrelated courses; refusing to mix or overwrite catalogue data.")
    remote_by_id = {row["id"]: row for row in remote_courses}
    for row in source["courses"]:
        existing = remote_by_id.get(row["id"])
        if existing and (existing.get("slug") != row["slug"] or existing.get("url_key") != (row.get("url_key") or normalise_url(row["official_url"]))):
            raise RuntimeError(f"Existing Supabase course {row['id']} conflicts with the SQLite source.")
    for row in source["providers"]:
        remote = provider_by_slug.get(row["slug"])
        if remote and remote["name"].strip().casefold() != row["name"].strip().casefold():
            raise RuntimeError(f"Provider slug {row['slug']!r} has a different remote name; stopped safely.")

    profiles = store._table("profiles").select("id,email").execute().data or []
    profile_by_email = {row["email"].strip().lower(): row["id"] for row in profiles}
    user_by_id = {row["id"]: row for row in source["users"]}
    actor_map = {}
    for action in source["actions"]:
        actor_id = action.get("actor_id")
        if actor_id:
            actor = user_by_id.get(actor_id)
            remote_profile_id = profile_by_email.get(actor["email"].strip().lower()) if actor else None
            if not remote_profile_id:
                raise RuntimeError("An audit actor has no matching Supabase profile; audit migration stopped.")
            actor_map[actor_id] = remote_profile_id

    return {
        "categories": category_by_slug,
        "providers": provider_by_slug,
        "courses": remote_by_id,
        "actions": {r["id"]: r for r in (store._table("moderation_actions").select("*").execute().data or [])},
        "notes": {r["course_id"]: r for r in (store._table("course_admin_notes").select("course_id,notes,updated_at").execute().data or [])},
        "actor_map": actor_map,
    }


def migrate(store, source, remote):
    provider_id_map = {}
    for provider in source["providers"]:
        existing = remote["providers"].get(provider["slug"])
        if existing:
            provider_id_map[provider["id"]] = existing["id"]
        else:
            payload = {key: provider.get(key) for key in ("id", "slug", "name", "website_url")}
            store._table("providers").insert(payload).execute()
            remote["providers"][provider["slug"]] = payload
            provider_id_map[provider["id"]] = provider["id"]

    category_id_map = {
        category["id"]: remote["categories"][category["slug"]]["id"]
        for category in source["categories"]
    }
    for row in source["courses"]:
        mapped_category = category_id_map.get(row.get("category_id"))
        mapped_provider = provider_id_map.get(row.get("provider_id"))
        expected = expected_course(row, mapped_category, mapped_provider)
        current = remote["courses"].get(row["id"])
        if not current:
            insert_payload = dict(expected)
            insert_payload["status"] = workflow.DRAFT
            # The database trigger forbids a non-draft INSERT. Untrusted origins
            # are inserted without editorial flags, then those reviewed flags are restored.
            untrusted = insert_payload.get("origin") in UNTRUSTED_ORIGINS
            deferred_flags = ("is_featured", "is_underrated", "editorial_approved", "editorial_score_public")
            if untrusted:
                for flag in deferred_flags:
                    insert_payload[flag] = False
            store._table("courses").insert(insert_payload).execute()
            current = {**insert_payload, "status": workflow.DRAFT}
            remote["courses"][row["id"]] = current

        deferred_flags = {"is_featured", "is_underrated", "editorial_approved", "editorial_score_public"}
        untrusted = expected.get("origin") in UNTRUSTED_ORIGINS
        for field, value in expected.items():
            if field == "status":
                continue
            actual = current.get(field)
            if untrusted and field in deferred_flags and not same_value(field, actual, value) and not same_value(field, actual, False):
                raise RuntimeError(f"Course {row['id']} has a conflicting editorial field {field}; stopped safely.")
            if not same_value(field, actual, value) and not (untrusted and field in deferred_flags and same_value(field, actual, False)):
                raise RuntimeError(f"Course {row['id']} differs from the source in {field}; stopped safely.")

        transitions = transition_path(current.get("status"), row["status"])
        for target in transitions:
            workflow.check_transition(current["status"], target)
            update = {"status": target, "updated_at": row["updated_at"]}
            if target == workflow.PUBLISHED:
                update["published_at"] = row.get("published_at")
            store._table("courses").update(update).eq("id", row["id"]).execute()
            current.update(update)

        restore = {field: expected[field] for field in deferred_flags if not same_value(field, current.get(field), expected[field])}
        if restore:
            store._table("courses").update(restore).eq("id", row["id"]).execute()
            current.update(restore)

    for note in source["notes"]:
        existing = remote["notes"].get(note["course_id"])
        payload = {"course_id": note["course_id"], "notes": note["notes"], "updated_at": note["updated_at"]}
        if existing:
            if existing.get("notes") != note["notes"] or not same_value("updated_at", existing.get("updated_at"), note["updated_at"]):
                raise RuntimeError(f"Private admin note for course {note['course_id']} conflicts with source.")
        else:
            store._table("course_admin_notes").insert(payload).execute()
            remote["notes"][note["course_id"]] = payload

    existing_actions = remote["actions"]
    for action in source["actions"]:
        payload = dict(action)
        if payload.get("actor_id"):
            payload["actor_id"] = remote["actor_map"][payload["actor_id"]]
        if payload["id"] in existing_actions:
            existing = existing_actions[payload["id"]]
            if any(
                not same_value(key, existing.get(key), value)
                for key, value in payload.items()
                if key in TIMESTAMP_FIELDS or key in BOOLEAN_FIELDS or key in NUMERIC_FIELDS
            ) or any(
                existing.get(key) != value
                for key, value in payload.items()
                if key not in TIMESTAMP_FIELDS | BOOLEAN_FIELDS | NUMERIC_FIELDS
            ):
                raise RuntimeError(f"Audit record {payload['id']} conflicts with the SQLite source.")
            continue
        store._table("moderation_actions").insert(payload).execute()
        existing_actions[payload["id"]] = {"id": payload["id"]}


def verify_migration(store, source, remote):
    """Read back every migrated row and check relationships, content and public visibility."""
    categories = {
        row["slug"]: row
        for row in store._table("categories").select("id,slug,name").execute().data or []
    }
    providers = {
        row["slug"]: row
        for row in store._table("providers").select("id,slug,name,website_url").execute().data or []
    }
    if len(providers) != len(source["providers"]):
        raise RuntimeError("Provider read-back count does not match the source.")
    provider_id_map = {}
    for original in source["providers"]:
        actual = providers.get(original["slug"])
        if not actual or actual["name"] != original["name"]:
            raise RuntimeError(f"Provider verification failed for slug {original['slug']}.")
        provider_id_map[original["id"]] = actual["id"]

    category_id_map = {}
    for original in source["categories"]:
        actual = categories.get(original["slug"])
        if not actual:
            raise RuntimeError(f"Category verification failed for slug {original['slug']}.")
        category_id_map[original["id"]] = actual["id"]

    published_ids = set()
    for original in source["courses"]:
        rows = store._table("courses").select("*").eq("id", original["id"]).limit(1).execute().data or []
        if not rows:
            raise RuntimeError(f"Course {original['id']} is missing after migration.")
        actual = rows[0]
        expected = expected_course(
            original,
            category_id_map.get(original.get("category_id")),
            provider_id_map.get(original.get("provider_id")),
        )
        for field, value in expected.items():
            if not same_value(field, actual.get(field), value):
                raise RuntimeError(f"Course {original['id']} did not preserve {field}.")
        if original["status"] == workflow.PUBLISHED:
            published_ids.add(original["id"])

    public = store.list_courses(per_page=100)
    if public["total"] != len(published_ids) or {item["id"] for item in public["items"]} != published_ids:
        raise RuntimeError("Public course query does not expose exactly the originally published courses.")
    for original in source["courses"]:
        if original["status"] != workflow.PUBLISHED and store.get_course(course_id=original["id"]) is not None:
            raise RuntimeError(f"Unpublished course {original['id']} is visible through the public store query.")

    notes = store._table("course_admin_notes").select("course_id,notes,updated_at").execute().data or []
    note_by_course = {row["course_id"]: row for row in notes}
    for original in source["notes"]:
        actual = note_by_course.get(original["course_id"])
        if not actual or actual["notes"] != original["notes"] or not same_value("updated_at", actual["updated_at"], original["updated_at"]):
            raise RuntimeError(f"Private notes for course {original['course_id']} failed verification.")

    actions = store._table("moderation_actions").select("id").execute().data or []
    action_ids = {row["id"] for row in actions}
    if not {row["id"] for row in source["actions"]}.issubset(action_ids):
        raise RuntimeError("Some original audit history is missing after migration.")


def main():
    parser = argparse.ArgumentParser(description="Migrate the original CourseFinder SQLite catalogue to Supabase.")
    parser.add_argument("--database", type=Path, default=ROOT / "instance" / "coursefinder.db")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply the preflighted, resumable migration.")
    args = parser.parse_args()

    database = args.database.resolve()
    source = read_source(database)
    if args.apply:
        backup = args.backup
        if backup is None:
            candidates = sorted(database.parent.glob("coursefinder.pre_migration_*.db"), reverse=True)
            backup = candidates[0] if candidates else None
        if backup is None:
            raise RuntimeError("No separate pre-migration backup found; refusing to write.")
        verified_backup(database, backup.resolve())

    store = create_store(type("ConfigView", (), {"config": vars(Config).copy()})())
    try:
        # create_store expects Flask's config mapping; use the concrete class attrs.
        remote = preflight(store, source)
        print("Preflight passed: SQLite integrity, category mappings, provider/course IDs and foreign keys are valid.")
        print("Local catalogue:", json.dumps({"courses": len(source["courses"]), "providers": len(source["providers"]), "notes": len(source["notes"]), "audit_actions": len(source["actions"]), "statuses": source["status_counts"]}, sort_keys=True))
        if not args.apply:
            print("Dry run only. No Supabase records were changed. Re-run with --apply to migrate.")
            return 0
        migrate(store, source, remote)
        verify_migration(store, source, remote)
        final_courses = store._table("courses").select("id,status").execute().data or []
        final_providers = store._table("providers").select("id").execute().data or []
        final_notes = store._table("course_admin_notes").select("course_id").execute().data or []
        final_actions = store._table("moderation_actions").select("id").execute().data or []
        statuses = {}
        for row in final_courses:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        expected_statuses = source["status_counts"]
        if len(final_courses) != len(source["courses"]) or len(final_providers) != len(source["providers"]):
            raise RuntimeError("Post-migration verification count mismatch; rerun is safe and will resume.")
        if statuses != expected_statuses:
            raise RuntimeError("Post-migration status verification mismatch; rerun is safe and will resume.")
        if len(final_notes) < len(source["notes"]) or len(final_actions) < len(source["actions"]):
            raise RuntimeError("Post-migration notes/audit verification failed; rerun is safe and will resume.")
        print("Migration verified:", json.dumps({"courses": len(final_courses), "providers": len(final_providers), "course_admin_notes": len(final_notes), "moderation_actions": len(final_actions), "statuses": statuses}, sort_keys=True))
        return 0
    finally:
        pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Migration stopped safely: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
