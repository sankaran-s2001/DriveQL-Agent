from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# Write Google Service Account JSON content dynamically if passed in environment (useful for Render deployment)
g_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
if g_account_json:
    cred_file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service_account.json")
    try:
        Path(cred_file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(cred_file_path, "w", encoding="utf-8") as f:
            f.write(g_account_json)
    except Exception as e:
        import sys
        print(f"Warning: Failed to write GOOGLE_SERVICE_ACCOUNT_JSON to disk: {str(e)}", file=sys.stderr)


class StartupValidationError(Exception):
    """Raised when application startup configuration checks fail."""
    pass


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    google_service_account_file: Path = Path(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service_account.json")
    )
    gdrive_folder_id: str = os.getenv("GDRIVE_FOLDER_ID", "")
    sqlite_db_path: Path = Path(
        os.getenv("SQLITE_DB_PATH", "data/agent_database.db")
    )

    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "120"))

    gemini_max_requests_per_minute: int = int(os.getenv("GEMINI_MAX_REQUESTS_PER_MINUTE", "15"))
    gemini_max_concurrent_requests: int = int(os.getenv("GEMINI_MAX_CONCURRENT_REQUESTS", "5"))
    gemini_retry_after_buffer: float = float(os.getenv("GEMINI_RETRY_AFTER_BUFFER", "2.0"))
    gemini_default_retry_seconds: float = float(os.getenv("GEMINI_DEFAULT_RETRY_SECONDS", "30.0"))
    gemini_max_wait_seconds: int = int(os.getenv("GEMINI_MAX_WAIT_SECONDS", "300"))

    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_use_tls: bool = _as_bool(os.getenv("SMTP_USE_TLS"), True)
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    email_to: str = os.getenv("EMAIL_TO", "")

    def validate_startup(self) -> None:
        """Validates all environment settings, directory structures, and permissions before starting."""
        errors = []

        # 1. Google API Key validation
        key = self.google_api_key.strip()
        if not key:
            errors.append("GOOGLE_API_KEY environment variable is missing or empty.")
        elif "your_google_ai_studio_api_key" in key:
            errors.append("GOOGLE_API_KEY contains placeholder text. Replace it with your actual Gemini API Key.")

        # 2. Google Service Account credentials validation
        g_cred = self.google_service_account_file
        if not g_cred:
            errors.append("GOOGLE_SERVICE_ACCOUNT_FILE configuration path is not set.")
        elif not g_cred.exists():
            errors.append(f"Google service account credential file not found at: {g_cred}")

        # 3. Google Drive folder validation
        folder_id = self.gdrive_folder_id.strip()
        if not folder_id:
            errors.append("GDRIVE_FOLDER_ID environment variable is missing or empty.")
        elif folder_id in {"your_google_drive_folder_id", "your_folder_id"}:
            errors.append("GDRIVE_FOLDER_ID contains placeholder text. Replace it with your actual Google Drive folder ID.")

        # 4. SQLite DB Directory writable check
        db_path = self.sqlite_db_path
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            test_file = db_path.parent / ".startup_write_test"
            test_file.touch(exist_ok=True)
            test_file.unlink(missing_ok=True)
        except Exception as e:
            errors.append(f"SQLite database destination folder is not writeable: {db_path.parent}. Error: {str(e)}")

        # 5. SMTP/Email settings validation
        if self.smtp_username:
            if "your_email" in self.smtp_username or "@" not in self.smtp_username:
                errors.append(f"SMTP_USERNAME '{self.smtp_username}' appears to be invalid or placeholder.")
            if self.smtp_password in {"your_gmail_app_password", "your_16_character_app_password", ""}:
                errors.append("SMTP_PASSWORD is missing or contains placeholder values.")
        
        if self.email_from and ("your_email" in self.email_from or "@" not in self.email_from):
            errors.append(f"EMAIL_FROM '{self.email_from}' is invalid or placeholder.")
        if self.email_to and ("recipient" in self.email_to or "@" not in self.email_to):
            errors.append(f"EMAIL_TO '{self.email_to}' is invalid or placeholder.")

        if errors:
            raise StartupValidationError("\n".join(errors))


settings = Settings()

