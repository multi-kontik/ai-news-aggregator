from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "given, expected",
    [
        ("postgresql://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        ("postgres://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        ("postgresql+psycopg://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        ("sqlite:///local.db", "sqlite:///local.db"),
    ],
)
def test_database_url_always_uses_the_psycopg_driver(given, expected):
    assert Settings(_env_file=None, database_url=given).database_url == expected
