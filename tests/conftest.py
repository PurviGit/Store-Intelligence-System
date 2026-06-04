"""
Shared pytest fixtures for Store Intelligence tests.
Uses an in-process SQLite test database — no external dependencies.
"""
import sys
import os
import pytest

# Add app and pipeline to path so imports work from both directories
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# Must import database BEFORE main to patch engine
import database as db_module

TEST_DB_URL = "sqlite:///./test_store_intelligence.db"


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Create a file-based SQLite test DB for the session."""
    test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Patch the module-level engine and session
    db_module.engine = test_engine
    db_module.SessionLocal = TestSession

    from database import Base
    Base.metadata.create_all(bind=test_engine)

    yield

    # Cleanup
    try:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
    except Exception:
        pass
    try:
        if os.path.exists("test_store_intelligence.db"):
            os.remove("test_store_intelligence.db")
    except PermissionError:
        pass  # Windows file lock — file will be cleaned on next run


@pytest.fixture
def client(setup_test_db):
    from main import app
    from database import get_db, SessionLocal

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_entry_event():
    return {
        "event_id": "test-entry-001",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_00001",
        "event_type": "ENTRY",
        "timestamp": "2026-04-10T12:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.92,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }


@pytest.fixture
def sample_zone_event():
    return {
        "event_id": "test-zone-001",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_FLOOR_02",
        "visitor_id": "VIS_00001",
        "event_type": "ZONE_ENTER",
        "timestamp": "2026-04-10T12:05:00Z",
        "zone_id": "SKINCARE",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.88,
        "metadata": {"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 2},
    }


@pytest.fixture
def sample_billing_event():
    return {
        "event_id": "test-billing-001",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_BILLING_03",
        "visitor_id": "VIS_00001",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": "2026-04-10T12:30:00Z",
        "zone_id": "BILLING",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.90,
        "metadata": {"queue_depth": 2, "sku_zone": None, "session_seq": 5},
    }
