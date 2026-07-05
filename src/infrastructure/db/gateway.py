"""DatabaseGateway — connection lifecycle + initialization."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import sqlite3

from src.db.database import connect, initialize_database


class DatabaseGateway:
    def __init__(self, db_path: str):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path

    def initialize(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        initialize_database(self._db_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()