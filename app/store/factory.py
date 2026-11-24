from pathlib import Path

from app.store.local import LocalStore


def create_store(app):
    backend = (app.config.get("DATA_BACKEND") or "local").lower()
    min_rating_count = app.config.get("MIN_PUBLIC_RATING_COUNT", 3)
    if backend == "supabase":
        from app.store.supabase_store import SupabaseStore

        if not all(
            app.config.get(key)
            for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY")
        ):
            raise RuntimeError(
                "DATA_BACKEND=supabase requires SUPABASE_URL, SUPABASE_ANON_KEY, "
                "and SUPABASE_SERVICE_ROLE_KEY in the server environment"
            )
        return SupabaseStore(app.config, min_rating_count=min_rating_count)

    db_path = app.config.get("DATABASE_PATH") or "instance/coursefinder.db"
    path = Path(db_path)
    if not path.is_absolute():
        path = Path(app.root_path).parent / path
    return LocalStore(
        path,
        seed_sample=app.config.get("SEED_SAMPLE_COURSES", True),
        min_rating_count=min_rating_count,
    )
