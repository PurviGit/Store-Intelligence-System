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


def create_tables():
    # 1. Create tables first
    Base.metadata.create_all(bind=engine)
    # 2. Then add indexes — turns full-table scans into index lookups (10-50x faster)
    with engine.connect() as conn:
        # SQLite PRAGMAs first (no table dependency)
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA cache_size=-32000"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
        # Indexes — only if events table exists
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
            pass  # Table not yet created — indexes will be added on next startup


def check_db_health() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
