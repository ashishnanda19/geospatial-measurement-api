from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    """Factory for a client backed by a throw-away SQLite DB; kwargs override Settings."""
    clients: list[TestClient] = []

    def factory(**overrides: object) -> TestClient:
        settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", **overrides)
        client = TestClient(create_app(settings), raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
