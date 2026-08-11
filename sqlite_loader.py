from __future__ import annotations

import re
import os
import sqlite3
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

RESERVED_SQL_KEYWORDS = {
    "select", "table", "from", "where", "join", "group", "order", "index", "key",
    "delete", "insert", "update", "create", "alter", "drop", "add", "limit", "offset",
    "default", "null", "primary", "foreign", "references", "check", "unique", "into",
    "values", "on", "using", "as", "by", "having", "union", "all", "and", "or", "not"
}


def normalize_identifier(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "unnamed"
    if value[0].isdigit():
        value = f"col_{value}"
    
    value = value.lower()
    if value in RESERVED_SQL_KEYWORDS:
        value = f"{value}_"
    return value


def make_unique_columns(columns) -> list[str]:
    seen: dict[str, int] = {}
    output: list[str] = []

    for column in columns:
        col_str = str(column).strip()
        if not col_str:
            col_str = "unnamed"
        base = normalize_identifier(col_str)
        count = seen.get(base, 0)
        final_name = base if count == 0 else f"{base}_{count + 1}"
        seen[base] = count + 1
        output.append(final_name)

    return output


class SQLiteLoader:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def load_files(self, file_paths: list[Path]) -> list[dict]:
        load_log: list[dict] = []

        with sqlite3.connect(self.database_path) as connection:
            for path in file_paths:
                if not path.exists():
                    logger.error(f"Sync error: File not found at path: {path}")
                    continue

                file_size = path.stat().st_size
                if file_size == 0:
                    logger.warning(f"Data validation warning: File {path.name} is empty (0 bytes).")
                    load_log.append({
                        "source_file": path.name,
                        "table_name": "N/A",
                        "rows": 0,
                        "columns": 0,
                        "warning": f"Empty file: {path.name}",
                        "quality_metrics": {
                            "duplicate_rows": 0,
                            "duplicate_columns": 0,
                            "missing_values": 0,
                            "mixed_types": [],
                            "encoding_warning": "Empty file (0 bytes)"
                        }
                    })
                    continue

                # Parse table name. Strip Drive resource ID prefix ({ID}_{Filename})
                match = re.match(r"^([a-zA-Z0-9_-]+)_(.*)$", path.name)
                if match:
                    original_fullname = match.group(2)
                    suggested_base = Path(original_fullname).stem
                else:
                    suggested_base = path.stem

                suffix = path.suffix.lower()
                try:
                    if suffix == ".csv":
                        table_name = normalize_identifier(suggested_base)
                        
                        # Memory-safe loading via chunks
                        chunksize = 20000
                        first_chunk = True
                        total_rows = 0
                        col_count = 0
                        warnings_collected = []
                        
                        # Audit metric variables
                        dup_rows = 0
                        dup_cols = 0
                        missing_vals = 0
                        mixed_types_set = set()
                        encoding_warning = None
                        csv_encoding = "utf-8"

                        # Validate encoding and parse sample
                        try:
                            pd.read_csv(path, nrows=5, encoding="utf-8")
                        except UnicodeDecodeError:
                            csv_encoding = "latin-1"
                            encoding_warning = "Non-UTF8 encoding detected"
                            warnings_collected.append(encoding_warning)

                        # Dry-run validation of columns
                        try:
                            sample = pd.read_csv(path, nrows=5, encoding=csv_encoding)
                            if sample.empty:
                                raise ValueError("No rows found in CSV.")
                            col_count = len(sample.columns)
                            normalized_cols = make_unique_columns(sample.columns)
                            dup_cols = len(sample.columns) - len(set(normalized_cols))
                            if dup_cols > 0:
                                warnings_collected.append(f"Resolved {dup_cols} duplicate headers.")
                        except Exception as e:
                            logger.error(f"Data validation error for {path.name}: {str(e)}")
                            raise ValueError(f"Corrupted or invalid CSV format: {str(e)}")

                        # Load CSV in chunks
                        for chunk in pd.read_csv(path, chunksize=chunksize, keep_default_na=True, encoding=csv_encoding):
                            chunk.columns = make_unique_columns(chunk.columns)
                            
                            # Metrics counts
                            dup_rows += int(chunk.duplicated().sum())
                            missing_vals += int(chunk.isnull().sum().sum())
                            
                            # Mixed type columns checks
                            for col in chunk.columns:
                                if chunk[col].isnull().all():
                                    warnings_collected.append(f"Column '{col}' is entirely empty.")
                                dtype_desc = pd.api.types.infer_dtype(chunk[col])
                                if dtype_desc in {"mixed", "mixed-integer", "mixed-integer-float"}:
                                    mixed_types_set.add(col)

                            if first_chunk:
                                chunk.to_sql(table_name, connection, if_exists="replace", index=False)
                                first_chunk = False
                            else:
                                chunk.to_sql(table_name, connection, if_exists="append", index=False)
                            total_rows += len(chunk)

                        load_log.append({
                            "source_file": path.name,
                            "table_name": table_name,
                            "rows": total_rows,
                            "columns": col_count,
                            "warning": "; ".join(set(warnings_collected)) if warnings_collected else None,
                            "quality_metrics": {
                                "duplicate_rows": dup_rows,
                                "duplicate_columns": dup_cols,
                                "missing_values": missing_vals,
                                "mixed_types": list(mixed_types_set),
                                "encoding_warning": encoding_warning
                            }
                        })

                    elif suffix in {".xlsx", ".xls"}:
                        try:
                            # Pre-validate worksheets
                            excel_file = pd.ExcelFile(path)
                            sheet_names = excel_file.sheet_names
                        except Exception as e:
                            raise ValueError(f"Corrupted or invalid Excel format: {str(e)}")

                        if not sheet_names:
                            raise ValueError("Excel file contains no worksheets.")

                        for sheet_name in sheet_names:
                            table_name = normalize_identifier(f"{suggested_base}_{sheet_name}")
                            
                            df = excel_file.parse(sheet_name)
                            if df.empty:
                                logger.warning(f"Data validation warning: Worksheet '{sheet_name}' in {path.name} is empty.")
                                load_log.append({
                                    "source_file": f"{path.name} [{sheet_name}]",
                                    "table_name": table_name,
                                    "rows": 0,
                                    "columns": 0,
                                    "warning": f"Empty sheet: '{sheet_name}'",
                                    "quality_metrics": {
                                        "duplicate_rows": 0,
                                        "duplicate_columns": 0,
                                        "missing_values": 0,
                                        "mixed_types": [],
                                        "encoding_warning": None
                                    }
                                })
                                continue

                            raw_cols = df.columns
                            df.columns = make_unique_columns(df.columns)
                            dup_cols = len(raw_cols) - len(set(df.columns))
                            dup_rows = int(df.duplicated().sum())
                            missing_vals = int(df.isnull().sum().sum())
                            mixed_types_set = set()
                            
                            for col in df.columns:
                                dtype_desc = pd.api.types.infer_dtype(df[col])
                                if dtype_desc in {"mixed", "mixed-integer", "mixed-integer-float"}:
                                    mixed_types_set.add(col)

                            df.to_sql(table_name, connection, if_exists="replace", index=False)

                            warnings = []
                            if dup_cols > 0:
                                warnings.append(f"Resolved {dup_cols} duplicate headers.")
                            if dup_rows > 0:
                                warnings.append(f"Found {dup_rows} duplicate rows.")

                            load_log.append({
                                "source_file": f"{path.name} [{sheet_name}]",
                                "table_name": table_name,
                                "rows": len(df),
                                "columns": len(df.columns),
                                "warning": "; ".join(warnings) if warnings else None,
                                "quality_metrics": {
                                    "duplicate_rows": dup_rows,
                                    "duplicate_columns": dup_cols,
                                    "missing_values": missing_vals,
                                    "mixed_types": list(mixed_types_set),
                                    "encoding_warning": None
                                }
                            })

                    else:
                        raise ValueError(f"Unsupported file type: {suffix}")

                except Exception as exc:
                    logger.error(f"ETL Load Error on file {path.name}: {str(exc)}")
                    load_log.append({
                        "source_file": path.name,
                        "table_name": "N/A",
                        "rows": 0,
                        "columns": 0,
                        "warning": f"Load failed: {str(exc)}",
                        "quality_metrics": {
                            "duplicate_rows": 0,
                            "duplicate_columns": 0,
                            "missing_values": 0,
                            "mixed_types": [],
                            "encoding_warning": f"Load error: {str(exc)}"
                        }
                    })

        return load_log


