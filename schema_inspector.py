from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Self-invalidating cache mapping absolute database path to (mtime, schema_string)
_schema_cache: dict[str, tuple[float, str]] = {}


def get_database_schema(database_path: Path) -> str:
    if not database_path.exists():
        return "No tables are available."

    abs_path = str(database_path.resolve())
    try:
        mtime = os.path.getmtime(abs_path)
    except OSError:
        mtime = 0.0

    # Return cached schema if file mtime hasn't changed
    if abs_path in _schema_cache:
        cached_mtime, cached_schema = _schema_cache[abs_path]
        if cached_mtime == mtime and mtime != 0.0:
            return cached_schema

    sections: list[str] = []

    try:
        # Open in read-only mode to prevent lock issues
        database_uri = f"file:{abs_path}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            cursor = connection.cursor()

            # Retrieve user tables
            tables = cursor.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                  AND name NOT LIKE '\\_sync\\_%' ESCAPE '\\'
                ORDER BY name
                """
            ).fetchall()

            for (table_name,) in tables:
                # Column details: (cid, name, type, notnull, dflt_value, pk)
                columns = cursor.execute(
                    f'PRAGMA table_info("{table_name}")'
                ).fetchall()

                # Row count
                try:
                    row_count = cursor.execute(
                        f'SELECT COUNT(*) FROM "{table_name}"'
                    ).fetchone()[0]
                except sqlite3.Error:
                    row_count = 0

                # Foreign Key details: (id, seq, table, from, to, on_update, on_delete, match)
                fk_list = cursor.execute(
                    f'PRAGMA foreign_key_list("{table_name}")'
                ).fetchall()

                # Build Column Description lines
                column_lines = []
                pks = []
                for _, column_name, data_type, not_null, default_value, primary_key in columns:
                    pk_suffix = ""
                    if primary_key:
                        pk_suffix = " [PK]"
                        pks.append(column_name)
                    nn_suffix = " NOT NULL" if not_null else ""
                    column_lines.append(
                        f"  - {column_name} ({data_type or 'UNKNOWN'}){pk_suffix}{nn_suffix}"
                    )

                # Build Foreign Key lines
                fk_lines = []
                for _, _, target_table, from_col, to_col, _, _, _ in fk_list:
                    fk_lines.append(
                        f"  - {from_col} -> {target_table}({to_col})"
                    )

                # Fetch Sample Data (up to 3 rows)
                sample_lines = []
                try:
                    sample_rows = cursor.execute(
                        f'SELECT * FROM "{table_name}" LIMIT 3'
                    ).fetchall()
                    if sample_rows:
                        headers = [col[1] for col in columns]
                        sample_lines.append("  Headers: " + ", ".join(headers))
                        for row in sample_rows:
                            row_str = ", ".join(
                                [str(val) if val is not None else "NULL" for val in row]
                            )
                            sample_lines.append(f"  Row: {row_str}")
                except sqlite3.Error:
                    pass

                # Assemble table sections
                fk_section = "\nFOREIGN KEYS:\n" + "\n".join(fk_lines) if fk_lines else ""
                sample_section = "\nSAMPLE ROWS:\n" + "\n".join(sample_lines) if sample_lines else ""

                sections.append(
                    f"TABLE: {table_name}\n"
                    f"ROWS: {row_count}\n"
                    f"COLUMNS:\n" + "\n".join(column_lines) +
                    fk_section +
                    sample_section
                )
    except sqlite3.Error as e:
        return f"Database Schema Reflection Error: {str(e)}"

    if not sections:
        schema_str = "No tables are available."
    else:
        schema_str = "\n\n".join(sections)

    # Save to cache
    if mtime != 0.0:
        _schema_cache[abs_path] = (mtime, schema_str)

    return schema_str

