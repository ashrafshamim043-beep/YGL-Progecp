"""
Database session management (synchronous SQLAlchemy 2.0 style).

FIX NOTE (Real Environment Verification Preparation): this file previously
used AsyncSession/create_async_engine, but every repository/adapter built
across Auth, KYC, Audit, RBAC, and Commission persistence uses synchronous
Session calls (self.db.execute(...), self.db.get(...), self.db.flush(),
self.db.commit() without await). That was a genuine async/sync mismatch
that would have failed at runtime against a real database -- fixed here
by making the session layer synchronous to match every adapter that
already depends on it, rather than converting 6+ adapter files (and every
router's `await db...` call sites) to async. No adapter file's logic
changed; only this shared session layer was corrected.

DATABASE_URL should use the sync psycopg driver, e.g.:
  postgresql+psycopg://user:pass@host:5432/ygl_office
(not postgresql+asyncpg://... -- that driver is no longer used by this app)
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for all Office System ORM models.

    NOTE: Commission Engine data (ledger_entries, commission_line_items,
    undistributed_amounts, commission_processing_records,
    plan_version_locks, plan_versions) is intentionally modeled separately,
    under app/services/commission_service/db_models.py — see that module's
    docstring for why (append-only enforcement lives there, isolated from
    the general ORM base used by non-financial modules).
    """
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
