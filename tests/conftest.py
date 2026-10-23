import os
import pytest
from reconcile.config import Settings
from reconcile.db import Database, metadata


@pytest.fixture
def settings():
    return Settings(min_age_seconds=120)


@pytest.fixture
def db(tmp_path):
    url = os.getenv("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    database = Database(url)
    # TEST_DATABASE_URL must point to a disposable database; CI creates one.
    metadata.drop_all(database.engine)
    database.init()
    yield database
    database.engine.dispose()
