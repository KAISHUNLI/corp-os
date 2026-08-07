from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from corp_os.config import get_settings


class Base(DeclarativeBase):
    pass


def _sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    raw = database_url.removeprefix("sqlite:///")
    return Path(raw)


def _is_sqlite(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def _make_engine():
    settings = get_settings()
    sqlite_path = _sqlite_path(settings.database_url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    connect_args = {}
    engine_kwargs: dict = {"future": True}
    if _is_sqlite(settings.database_url):
        connect_args["check_same_thread"] = False
    else:
        # PostgreSQL: recycle dead connections after Docker restart / idle timeout.
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 10

    engine = create_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)

    if _is_sqlite(settings.database_url):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Prepare storage. SQLite: create tables. PostgreSQL: schema via Alembic."""
    from corp_os import models  # noqa: F401

    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = _sqlite_path(settings.database_url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    if _is_sqlite(settings.database_url):
        Base.metadata.create_all(bind=engine)
        return

    # PostgreSQL: connectivity check only. Tables come from `alembic upgrade head`.
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
