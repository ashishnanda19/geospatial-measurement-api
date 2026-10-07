"""Engine and session plumbing."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    # FastAPI runs sync endpoints in a threadpool, so SQLite connections must
    # be usable from a thread other than the one that created them.
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=True)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding one session per request."""
    with request.app.state.session_factory() as session:
        yield session
