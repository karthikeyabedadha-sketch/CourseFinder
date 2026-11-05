from datetime import datetime, timezone
from types import SimpleNamespace

from app.store import supabase_store
from app.store.supabase_store import SupabaseStore


class FakeQuery:
    def __init__(self, table_name):
        self.table_name = table_name
        self.filters = []
        self.or_filter = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def or_(self, expression):
        self.or_filter = expression
        return self

    def execute(self):
        count = 3 if (
            self.table_name == "courses"
            and self.filters == [("status", "published")]
            and self.or_filter == "last_verified_at.is.null,last_verified_at.lt.2026-05-06"
        ) else 0
        return SimpleNamespace(count=count, data=[])


class FakeAdmin:
    def table(self, name):
        return FakeQuery(name)


def test_supabase_dashboard_counts_published_courses_with_stale_verification(monkeypatch):
    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(supabase_store, "datetime", FrozenDateTime)
    store = object.__new__(SupabaseStore)
    store.admin = FakeAdmin()
    monkeypatch.setattr(store, "count_users", lambda: 0)

    counts = store.dashboard_counts()

    assert counts["stale_verification"] == 3
