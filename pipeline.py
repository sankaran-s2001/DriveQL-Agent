from __future__ import annotations

import time
import datetime
import sqlite3
import logging
import concurrent.futures
from pathlib import Path

import re
from config import settings
from drive_reader import GoogleDriveReader
from sqlite_loader import SQLiteLoader
from schema_inspector import get_database_schema
from nl_to_sql_agent import NaturalLanguageSQLAgent, GeminiRateLimiter
from query_runner import ReadOnlyQueryRunner
from html_report import build_html_report
from email_sender import EmailSender

logger = logging.getLogger(__name__)


def validate_emails(email_string: str) -> tuple[list[str], list[str]]:
    """
    Validates a comma or semicolon separated list of email addresses.
    Returns:
        valid_list: cleaned, unique, valid lowercase email addresses.
        invalid_list: raw trimmed invalid email parts.
    """
    if not email_string.strip():
        return [], []
    parts = re.split(r'[;,]', email_string)
    valid_list = []
    invalid_list = []
    seen = set()
    email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    for part in parts:
        clean_email = part.strip()
        if not clean_email:
            continue
        if re.match(email_regex, clean_email):
            lower_email = clean_email.lower()
            if lower_email not in seen:
                seen.add(lower_email)
                valid_list.append(clean_email)
        else:
            invalid_list.append(clean_email)
    return valid_list, invalid_list


def sync_google_drive_to_sqlite() -> list[dict]:
    settings.validate_startup()
    reader = GoogleDriveReader(settings.google_service_account_file)
    loader = SQLiteLoader(settings.sqlite_db_path)
    output_dir = Path("data/downloads")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting Google Drive synchronization check...")
    start_time = time.time()

    try:
        drive_files = reader.list_supported_files(settings.gdrive_folder_id)
    except Exception as e:
        logger.error(f"Failed to query Google Drive files: {str(e)}")
        raise e

    db_path = settings.sqlite_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    load_log: list[dict] = []

    try:
        with sqlite3.connect(db_path) as connection:
            cursor = connection.cursor()
            # Initialize the synchronization metadata table if missing
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS _sync_metadata (
                    file_id TEXT PRIMARY KEY,
                    filename TEXT,
                    modified_time TEXT,
                    file_size INTEGER,
                    table_names TEXT,
                    last_synced TEXT
                );
            """)
            connection.commit()

            # Retrieve existing synced files metadata
            existing_records = cursor.execute(
                "SELECT file_id, filename, modified_time, file_size, table_names FROM _sync_metadata"
            ).fetchall()
            metadata_dict = {
                row[0]: {
                    "filename": row[1],
                    "modified_time": row[2],
                    "file_size": row[3],
                    "table_names": row[4].split(",") if row[4] else []
                }
                for row in existing_records
            }

            active_file_ids = set()

            for item in drive_files:
                file_id = item["id"]
                filename = item["name"]
                modified_time = item["modifiedTime"]
                file_size = int(item.get("size", 0) or 0)
                active_file_ids.add(file_id)

                cached = metadata_dict.get(file_id)
                # Incremental Sync Optimization: check if files match local DB state
                if cached and cached["modified_time"] == modified_time and cached["file_size"] == file_size:
                    logger.info(f"Sync: File '{filename}' is unchanged. Skipping reload.")
                    for table in cached["table_names"]:
                        try:
                            # Pull counts directly from SQLite Catalog to show details
                            row_cnt = cursor.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                            col_cnt = len(cursor.execute(f'PRAGMA table_info("{table}")').fetchall())
                        except sqlite3.Error:
                            row_cnt, col_cnt = 0, 0
                        load_log.append({
                            "source_file": filename,
                            "table_name": table,
                            "rows": row_cnt,
                            "columns": col_cnt,
                            "warning": "Skipped (Unchanged)"
                        })
                    continue

                # Drop old tables to prevent orphan schemas in SQLite
                if cached:
                    logger.info(f"Sync: File '{filename}' has modified. Dropping old tables: {cached['table_names']}")
                    for table in cached["table_names"]:
                        cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
                    connection.commit()

                # Sync modification/new file
                logger.info(f"Sync: Downloading and loading new/modified file '{filename}'...")
                path = output_dir / f"{file_id}_{filename}"
                try:
                    reader.download_file(file_id, path)
                    file_load_log = loader.load_files([path])
                except Exception as load_err:
                    logger.error(f"Sync error for file '{filename}': {str(load_err)}")
                    load_log.append({
                        "source_file": filename,
                        "table_name": "N/A",
                        "rows": 0,
                        "columns": 0,
                        "warning": f"Reload failed: {str(load_err)}"
                    })
                    continue

                loaded_tables = []
                for entry in file_load_log:
                    if entry["table_name"] != "N/A":
                        loaded_tables.append(entry["table_name"])
                    load_log.append(entry)

                # Save new metadata record
                table_names_str = ",".join(loaded_tables)
                now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO _sync_metadata (file_id, filename, modified_time, file_size, table_names, last_synced)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (file_id, filename, modified_time, file_size, table_names_str, now_str)
                )
                connection.commit()

            # Clean up deleted files
            for file_id, cached in metadata_dict.items():
                if file_id not in active_file_ids:
                    logger.info(f"Sync: File '{cached['filename']}' deleted on Drive. Dropping table references: {cached['table_names']}")
                    for table in cached["table_names"]:
                        cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
                    cursor.execute("DELETE FROM _sync_metadata WHERE file_id = ?", (file_id,))
                    connection.commit()

                    # Remove old local download file if present
                    local_path = output_dir / f"{file_id}_{cached['filename']}"
                    if local_path.exists():
                        try:
                            local_path.unlink()
                        except OSError:
                            pass

                    load_log.append({
                        "source_file": cached["filename"],
                        "table_name": "Dropped: " + ", ".join(cached["table_names"]),
                        "rows": 0,
                        "columns": 0,
                        "warning": "Deleted from Google Drive"
                    })

    except Exception as conn_err:
        logger.error(f"Sync Database error occurred: {str(conn_err)}")
        raise conn_err

    duration = time.time() - start_time
    logger.info(f"Google Drive synchronization check complete in {duration:.2f} seconds.")
    return load_log


def answer_questions(questions: list[str], rate_limiter: GeminiRateLimiter | None = None) -> list[dict]:
    settings.validate_startup()
    schema = get_database_schema(settings.sqlite_db_path)

    if rate_limiter is None:
        rate_limiter = GeminiRateLimiter(
            max_requests_per_min=settings.gemini_max_requests_per_minute,
            max_concurrent=settings.gemini_max_concurrent_requests,
            retry_buffer=settings.gemini_retry_after_buffer,
            default_retry=settings.gemini_default_retry_seconds,
            max_wait=settings.gemini_max_wait_seconds,
        )

    agent = NaturalLanguageSQLAgent(
        api_key=settings.google_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.request_timeout,
        rate_limiter=rate_limiter,
    )

    runner = ReadOnlyQueryRunner(settings.sqlite_db_path)

    results: list[dict] = [None] * len(questions)

    def process_single_question(index: int, question: str) -> dict:
        start_time = time.time()
        try:
            # Self-healing translation and execution
            sql, dataframe, retry_attempts, error_message, llm_latency = agent.generate_and_execute_sql(
                question, schema, runner
            )
            total_duration = time.time() - start_time
            db_latency = max(0.0, total_duration - llm_latency)

            return {
                "question": question,
                "sql": sql,
                "dataframe": dataframe,
                "error": error_message,
                "retry_count": retry_attempts,
                "llm_latency": llm_latency,
                "db_latency": db_latency,
                "total_latency": total_duration,
            }
        except Exception as exc:
            total_duration = time.time() - start_time
            logger.error(f"Thread worker error on index {index}: {str(exc)}")
            return {
                "question": question,
                "sql": "",
                "dataframe": None,
                "error": f"Internal process error: {str(exc)}",
                "retry_count": 0,
                "llm_latency": 0.0,
                "db_latency": 0.0,
                "total_latency": total_duration,
            }

    # Parallelization Optimization: Independent questions processed concurrently via thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_index = {
            executor.submit(process_single_question, idx, question): idx
            for idx, question in enumerate(questions)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = {
                    "question": questions[idx],
                    "sql": "",
                    "dataframe": None,
                    "error": f"Execution worker failed: {str(exc)}",
                    "retry_count": 0,
                    "llm_latency": 0.0,
                    "db_latency": 0.0,
                    "total_latency": 0.0,
                }

    return results


def create_report(results: list[dict], output_path: Path, sync_log: list[dict] | None = None, api_metrics: dict | None = None) -> str:
    html = build_html_report(results, sync_log, api_metrics)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return html


def email_report(html: str, recipient_text: str | None = None) -> None:
    recipient_text = recipient_text or settings.email_to
    recipients = [
        value.strip()
        for value in recipient_text.split(",")
        if value.strip()
    ]

    sender = EmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        use_tls=settings.smtp_use_tls,
    )

    sender.send_html_email(
        sender=settings.email_from,
        recipients=recipients,
        subject="Automated SQLite Analysis Report",
        html_body=html,
    )

