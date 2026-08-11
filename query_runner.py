from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


class ReadOnlyQueryRunner:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def execute(self, sql: str) -> pd.DataFrame:
        database_uri = f"file:{self.database_path.resolve()}?mode=ro"

        with sqlite3.connect(database_uri, uri=True) as connection:
            connection.execute("PRAGMA query_only = ON")
            return pd.read_sql_query(sql, connection)
