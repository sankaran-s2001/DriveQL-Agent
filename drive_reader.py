from __future__ import annotations

import io
import time
import random
import logging
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


class GoogleDriveReader:
    def __init__(self, service_account_file: Path):
        if not service_account_file.exists():
            raise FileNotFoundError(
                f"Google service account file not found: {service_account_file}"
            )

        credentials = service_account.Credentials.from_service_account_file(
            str(service_account_file),
            scopes=SCOPES,
        )
        self.service = build("drive", "v3", credentials=credentials)

    def list_supported_files(self, folder_id: str) -> list[dict]:
        if not folder_id:
            raise ValueError("GDRIVE_FOLDER_ID is empty.")

        # Sanitize folder_id against single-quote injection checks
        sanitized_folder_id = folder_id.replace("'", "\\'")
        query = f"'{sanitized_folder_id}' in parents and trashed = false"

        files: list[dict] = []
        page_token = None
        max_retries = 3

        while True:
            response = None
            for attempt in range(1, max_retries + 1):
                try:
                    response = (
                        self.service.files()
                        .list(
                            q=query,
                            spaces="drive",
                            fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                            pageToken=page_token,
                            pageSize=1000,
                        )
                        .execute()
                    )
                    break
                except (HttpError, IOError) as exc:
                    # Retry on rate limit (429) or transient errors (5xx)
                    status_code = exc.resp.status if isinstance(exc, HttpError) else 500
                    if (status_code == 429 or status_code >= 500) and attempt < max_retries:
                        sleep_time = (2 ** attempt) + random.uniform(0.1, 1.0)
                        logger.warning(
                            f"Drive API list warning (HTTP {status_code}) on attempt {attempt}. Retrying in {sleep_time:.2f}s..."
                        )
                        time.sleep(sleep_time)
                        continue
                    logger.error("Drive API list failed after maximum retries.")
                    raise exc

            if not response:
                break

            for item in response.get("files", []):
                suffix = Path(item["name"]).suffix.lower()
                if suffix in SUPPORTED_EXTENSIONS:
                    files.append(item)

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return files

    def download_file(self, file_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            try:
                request = self.service.files().get_media(fileId=file_id)
                buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(buffer, request)

                done = False
                while not done:
                    _, done = downloader.next_chunk()

                destination.write_bytes(buffer.getvalue())
                return destination
            except (HttpError, IOError) as exc:
                status_code = exc.resp.status if isinstance(exc, HttpError) else 500
                if (status_code == 429 or status_code >= 500) and attempt < max_retries:
                    sleep_time = (2 ** attempt) + random.uniform(0.1, 1.0)
                    logger.warning(
                        f"Drive API download warning (HTTP {status_code}) for file {file_id} on attempt {attempt}. Retrying in {sleep_time:.2f}s..."
                    )
                    time.sleep(sleep_time)
                    continue
                logger.error(f"Drive API download failed for file {file_id} after maximum retries.")
                raise exc

        return destination

    def download_folder_files(
        self,
        folder_id: str,
        output_directory: Path,
    ) -> list[Path]:
        output_directory.mkdir(parents=True, exist_ok=True)

        downloaded: list[Path] = []
        for item in self.list_supported_files(folder_id):
            # Prefix the file name with the drive resource ID to prevent collision
            path = output_directory / f"{item['id']}_{item['name']}"
            downloaded.append(self.download_file(item["id"], path))

        return downloaded

