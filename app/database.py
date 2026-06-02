import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./store_intelligence.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    # SQLite performance: pool size + WAL mode
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_schema():
    """
    Check if existing events table has the new columns added in v2 (zone_name, gender_pred, etc.).
    If not, drop and recreate — acceptable for dev/demo because events are regenerated from clips.
    In production this would be a proper Alembic migration.
    """
    NEW_COLUMNS = ["zone_name", "zone_type", "is_revenue_zone", "is_face_hidden",
                   "gender_pred", "age_pred", "age_bucket", "group_id", "group_size",
                   "zone_hotspot_x", "zone_hotspot_y"]
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(events)")).fetchall()
            if not result:
                return  # table doesn't exist yet — create_all will handle it
            existing_cols = {row[1] for row in result}
            missing = [c for c in NEW_COLUMNS if c not in existing_cols]
            if missing:
                # Schema is stale — drop and let create_all rebuild
                conn.execute(text("DROP TABLE IF EXISTS events"))
                conn.commit()
    except Exception:
        pass  # table doesn't exist yet


def create_tables():
    # 0. Migrate schema if necessary (handles old DB files with missing columns)
    _migrate_schema()
    # 1. Create tables
    Base.metadata.create_all(bind=engine)
    # 2. Performance indexes
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA cache_size=-32000"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
        try:
            for sql in [
                "CREATE INDEX IF NOT EXISTS idx_store_ts      ON events(store_id, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_store_type_ts ON events(store_id, event_type, timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_store_zone    ON events(store_id, zone_id, is_staff)",
                "CREATE INDEX IF NOT EXISTS idx_visitor       ON events(visitor_id)",
            ]:
                conn.execute(text(sql))
            conn.commit()
        except Exception:
            pass


def check_db_health() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
