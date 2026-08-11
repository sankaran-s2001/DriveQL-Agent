import sys
import time
from pathlib import Path

from config import settings, StartupValidationError
from question_loader import load_questions
from nl_to_sql_agent import GeminiRateLimiter
from pipeline import (
    sync_google_drive_to_sqlite,
    answer_questions,
    create_report,
    email_report,
)


def main() -> None:
    # Startup validation phase
    print("Verifying system settings and configurations...")
    try:
        settings.validate_startup()
        print("Startup verification: SUCCESS\n")
    except StartupValidationError as validation_err:
        print("\n==========================================================", file=sys.stderr)
        print("STARTUP CONFIGURATION ERROR DETECTED", file=sys.stderr)
        print("==========================================================", file=sys.stderr)
        print(str(validation_err), file=sys.stderr)
        print("==========================================================", file=sys.stderr)
        print("Execution halted. Please resolve configuration errors.", file=sys.stderr)
        sys.exit(1)

    print("Step 1: Downloading Google Drive files and loading SQLite...")
    try:
        load_log = sync_google_drive_to_sqlite()
    except Exception as exc:
        print(f"Ingestion Sync Failed: {str(exc)}", file=sys.stderr)
        sys.exit(1)

    for item in load_log:
        status_suffix = f" ({item['warning']})" if item.get("warning") else ""
        print(
            f"Loaded {item['source_file']} -> {item['table_name']} "
            f"({item['rows']} rows, {item['columns']} columns){status_suffix}"
        )

    print("\nStep 2: Reading questions...")
    try:
        questions = load_questions(Path("questions.txt"))
    except Exception as exc:
        print(f"Failed to read questions: {str(exc)}", file=sys.stderr)
        sys.exit(1)

    print("\nStep 3: Generating and running SQL queries (Self-Healing active)...")
    limiter = GeminiRateLimiter(
        max_requests_per_min=settings.gemini_max_requests_per_minute,
        max_concurrent=settings.gemini_max_concurrent_requests,
        retry_buffer=settings.gemini_retry_after_buffer,
        default_retry=settings.gemini_default_retry_seconds,
        max_wait=settings.gemini_max_wait_seconds,
    )
    t_start = time.time()
    results = answer_questions(questions, rate_limiter=limiter)
    overall_duration = time.time() - t_start

    print("\nStep 4: Creating HTML report...")
    api_metrics = {
        "total_requests": limiter.total_requests,
        "successful_requests": limiter.success_requests,
        "rate_limited_requests": limiter.limited_requests,
        "total_wait_time": limiter.total_wait_time,
        "overall_duration": overall_duration,
    }
    html = create_report(
        results,
        Path("output/analysis_report.html"),
        sync_log=load_log,
        api_metrics=api_metrics,
    )

    print("\nStep 5: Sending email...")
    try:
        email_report(html)
        print("Email dispatched successfully.")
    except Exception as email_err:
        # Prevent script crash on SMTP errors
        print(
            f"WARNING: Email dispatch failed: {str(email_err)}. "
            f"The HTML report has been saved locally.",
            file=sys.stderr
        )

    print("\nCompleted successfully.")
    print("Report saved to: output/analysis_report.html")


if __name__ == "__main__":
    main()

