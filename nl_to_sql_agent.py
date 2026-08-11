from __future__ import annotations

import re
import time
import random
import logging
import threading
import pandas as pd
from google import genai
from google.genai import types
from google.genai.errors import APIError

from prompts import SQL_SYSTEM_PROMPT, SQL_REPAIR_PROMPT
from query_runner import ReadOnlyQueryRunner

logger = logging.getLogger(__name__)

if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create",
    "attach", "detach", "pragma", "vacuum", "replace", "truncate"
}


# ==========================================
# Custom Exception Hierarchy
# ==========================================

class GeminiError(Exception):
    """Base exception for all Gemini agent operations."""
    pass


class MissingAPIKeyError(GeminiError):
    """Raised when the Google API Key is not set or empty."""
    pass


class InvalidAPIKeyError(GeminiError):
    """Raised when the Gemini API rejects credentials (HTTP 400/403 related to keys)."""
    pass


class TimeoutError(GeminiError):
    """Raised when the request times out."""
    pass


class RateLimitError(GeminiError):
    """Raised when the Gemini API returns a rate limit error (HTTP 429)."""
    pass


class ServerOverloadError(GeminiError):
    """Raised when the Gemini server encounters internal failures (HTTP 5xx)."""
    pass


class SafetyBlockedError(GeminiError):
    """Raised when the response is blocked due to safety content configurations."""
    pass


class EmptyResponseError(GeminiError):
    """Raised when the API returns an empty response or missing candidates."""
    pass


class MalformedResponseError(GeminiError):
    """Raised when the response structure cannot be parsed correctly."""
    pass


# ==========================================
# Quota-Aware Centralized Rate Limiter
# ==========================================

class GeminiRateLimiter:
    def __init__(
        self,
        max_requests_per_min: int = 15,
        max_concurrent: int = 5,
        retry_buffer: float = 2.0,
        default_retry: float = 30.0,
        max_wait: int = 300,
        status_callback: callable | None = None,
    ):
        self.max_requests_per_min = max_requests_per_min
        self.max_concurrent = max_concurrent
        self.retry_buffer = retry_buffer
        self.default_retry = default_retry
        self.max_wait = max_wait
        self.status_callback = status_callback

        self.lock = threading.Lock()
        self.semaphore = threading.Semaphore(self.max_concurrent)

        # Metrics & status tracking
        self.total_requests = 0
        self.success_requests = 0
        self.limited_requests = 0
        self.total_wait_time = 0.0
        self.current_status = "Active"

        # Sliding window timestamp tracker (rolling 60s)
        self.request_timestamps: list[float] = []
        self.cooldown_until = 0.0

    def _trigger_callback(self):
        if self.status_callback:
            try:
                self.status_callback(self)
            except Exception:
                pass

    def wait_if_limited(self):
        """Yield thread execute controls if the window is full or 429 backoff is active."""
        while True:
            now = time.time()
            sleep_needed = 0.0

            with self.lock:
                # 1. Check if 429 cooldown is active
                if now < self.cooldown_until:
                    sleep_needed = self.cooldown_until - now
                    self.current_status = f"Waiting {sleep_needed:.1f}s for 429 cooldown..."
                else:
                    # 2. Check rolling 60 seconds limit window
                    self.request_timestamps = [t for t in self.request_timestamps if now - t < 60.0]
                    if len(self.request_timestamps) >= self.max_requests_per_min:
                        oldest = self.request_timestamps[0]
                        sleep_needed = 60.0 - (now - oldest)
                        self.current_status = f"Throttling: Waiting {sleep_needed:.1f}s for window reset..."
                    else:
                        self.current_status = "Active"
                        # Register current request
                        self.request_timestamps.append(now)
                        self.total_requests += 1
                        break

            self._trigger_callback()
            if sleep_needed > 0:
                logger.info(f"Rate Limiter: {self.current_status}. Sleeping thread...")
                time.sleep(min(sleep_needed, float(self.max_wait)))

    def report_success(self):
        with self.lock:
            self.success_requests += 1
        self._trigger_callback()

    def handle_429(self, exc_msg: str):
        """Sets the cooldown period and registers wait time when a 429 is encountered."""
        now = time.time()
        wait_seconds = self.default_retry

        # Search for Google's recommended retry duration
        match = re.search(r"Please retry in ([\d\.]+)s", exc_msg)
        if match:
            try:
                wait_seconds = float(match.group(1))
            except ValueError:
                pass

        total_wait = wait_seconds + self.retry_buffer
        with self.lock:
            self.limited_requests += 1
            target_cooldown = now + total_wait
            if target_cooldown > self.cooldown_until:
                self.cooldown_until = target_cooldown
                self.total_wait_time += total_wait
                logger.warning(
                    f"\n[WARNING] Gemini Free Tier quota reached. Waiting {total_wait:.1f} seconds before continuing...\n"
                )
                print(f"\n[WARNING] Gemini Free Tier quota reached. Waiting {total_wait:.1f} seconds before continuing...\n")
        self._trigger_callback()


# ==========================================
# Natural Language to SQL AI Agent
# ==========================================

class NaturalLanguageSQLAgent:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.1-flash-lite",
        timeout_seconds: int = 120,
        rate_limiter: GeminiRateLimiter | None = None,
    ):
        if not api_key:
            raise MissingAPIKeyError(
                "Google API Key is missing. Please set GOOGLE_API_KEY in your environment."
            )
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

        # Centrally shared rate limiter
        self.rate_limiter = rate_limiter or GeminiRateLimiter()

        # Reusable client (Persistent Connection Optimization)
        self.client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(timeout=self.timeout_seconds * 1000)
        )

    def _clean_sql(self, response_text: str) -> str:
        text = response_text.strip()

        # Remove markdown fences or formatting
        text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        return text

    def _validate_sql(self, sql: str) -> None:
        normalized = re.sub(r"\s+", " ", sql.strip().lower())

        # Check for multiple statements by inspecting inner semicolons (protects against truncation issues)
        clean_query = sql.rstrip().rstrip(";")
        if ";" in clean_query:
            raise ValueError("Multiple SQL statements or semicolons within queries are not allowed.")

        if not (
            normalized.startswith("select ")
            or normalized.startswith("with ")
        ):
            raise ValueError("Only SELECT or WITH...SELECT queries are allowed.")

        tokens = set(re.findall(r"\b[a-z_]+\b", normalized))
        blocked = sorted(tokens.intersection(FORBIDDEN_KEYWORDS))

        if blocked:
            raise ValueError(
                f"Unsafe SQL keyword(s) detected: {', '.join(blocked)}"
            )

        if "--" in sql or "/*" in sql or "*/" in sql:
            raise ValueError("SQL comments are not allowed.")

    def _validate_question(self, question: str) -> None:
        if not question.strip():
            raise ValueError("Business question is empty.")

        # Check for prompt injection signatures and unicode obfuscation
        normalized = question.lower()
        suspicious_patterns = [
            "ignore instructions", "ignore rules", "override rules", "developer mode",
            "sql injection", "drop table", "delete from", "truncate table", "system prompt"
        ]
        for pattern in suspicious_patterns:
            if pattern in normalized:
                raise ValueError("Question validation blocked: suspicious activity or prompt injection query detected.")

    def _generate_with_retry(self, prompt: str) -> str:
        """Helper executing LLM generation calls using the reusable client, backoff, and timeouts."""
        max_attempts = 4
        backoff = 1.0
        attempts = 0
        response_text = ""

        while True:
            attempts += 1
            
            # --- Quota Control Sync Phase ---
            self.rate_limiter.wait_if_limited()

            with self.rate_limiter.semaphore:
                start_time = time.time()
                try:
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.0,
                            top_p=0.1,
                            candidate_count=1,
                        )
                    )
                    duration = time.time() - start_time
                    logger.info(f"LLM request completed in {duration:.2f} seconds.")

                    # Ensure candidates exist
                    if not response or not response.candidates:
                        raise EmptyResponseError("No response candidates were returned by the Gemini API.")

                    candidate = response.candidates[0]

                    # Check safety block reason
                    if candidate.finish_reason and candidate.finish_reason.name == "SAFETY":
                        raise SafetyBlockedError("The request was blocked by Gemini safety filters.")

                    if not candidate.content or not candidate.content.parts:
                        raise EmptyResponseError("Returned candidate contains no content parts.")

                    response_text = response.text
                    if not response_text:
                        raise EmptyResponseError("Returned text content is empty.")

                    # Register successful completion status
                    self.rate_limiter.report_success()
                    break

                except APIError as e:
                    duration = time.time() - start_time
                    logger.error(f"API Error on attempt {attempts}: Code {e.code} - {e.message}")

                    if e.code == 429:
                        # Log 429 quota exception and set wait cooldown period
                        self.rate_limiter.handle_429(str(e.message))
                        # Decrease attempt count so quota warnings do not count towards the 4 failed attempts limit
                        attempts -= 1
                        continue

                    # Retry on standard transient server errors (5xx)
                    is_retryable = e.code in [500, 502, 503, 504]
                    if is_retryable and attempts < max_attempts:
                        sleep_time = (backoff * (2 ** (attempts - 1))) + random.uniform(0, 1)
                        logger.info(f"Retrying after {sleep_time:.2f} seconds due to transient code {e.code}...")
                        time.sleep(sleep_time)
                        continue

                    if e.code in [400, 403]:
                        msg = str(e.message).lower()
                        if any(k in msg for k in ["key", "api_key", "invalid", "credential"]):
                            raise InvalidAPIKeyError(f"Invalid Google API Key: {e.message}") from e

                    raise GeminiError(f"Gemini API request failed: {e.message}") from e

                except Exception as e:
                    duration = time.time() - start_time
                    logger.error(f"Unexpected error on attempt {attempts}: {str(e)}")

                    if isinstance(e, GeminiError):
                        raise e

                    err_str = str(e).lower()
                    is_timeout = "timeout" in err_str or "timed out" in err_str

                    # Retry on connection timeouts or network resets
                    if (is_timeout or "connection" in err_str) and attempts < max_attempts:
                        sleep_time = (backoff * (2 ** (attempts - 1))) + random.uniform(0, 1)
                        logger.info(f"Retrying after {sleep_time:.2f} seconds due to network/timeout error...")
                        time.sleep(sleep_time)
                        continue

                    if is_timeout:
                        raise TimeoutError(f"Gemini API request timed out: {str(e)}") from e

                    raise GeminiError(f"Gemini API execution failed: {str(e)}") from e

        return response_text

    def question_to_sql(self, question: str, schema: str) -> str:
        prompt = f"""
{SQL_SYSTEM_PROMPT}

DATABASE SCHEMA:
{schema}

BUSINESS QUESTION:
{question}

SQL:
""".strip()

        response_text = self._generate_with_retry(prompt)
        sql = self._clean_sql(response_text)
        self._validate_sql(sql)
        return sql

    def _map_to_friendly_error(self, raw_error: str, sql: str) -> str:
        raw_error_lower = raw_error.lower()
        if "no such column" in raw_error_lower:
            return "The generated SQL referenced a column that is not available in the database schema."
        if "no such table" in raw_error_lower:
            return "The generated SQL referenced a table that is not available in the database schema."
        if "syntax error" in raw_error_lower:
            return "A database query syntax error occurred during SQL execution."
        if "ambiguous column" in raw_error_lower:
            return "A column reference in the query is ambiguous because it exists in multiple tables."
        return f"Database query failed during analysis: {raw_error}"

    def generate_and_execute_sql(
        self,
        question: str,
        schema: str,
        runner: ReadOnlyQueryRunner,
    ) -> tuple[str, pd.DataFrame | None, int, str | None, float]:
        """
        Translates a question to SQL and executes it with an automatic self-healing repair loop.
        
        Returns:
            sql: The final generated SQL statement.
            dataframe: The execution results or None if failed.
            retry_attempts: Number of repair attempts made (0-3).
            error_message: User-friendly error message if failed, or None.
            llm_latency: Cumulative latency of LLM calls in seconds.
        """
        try:
            self._validate_question(question)
        except ValueError as val_err:
            return "", None, 0, str(val_err), 0.0

        attempts = 0
        max_repairs = 3
        sql = ""
        total_llm_latency = 0.0

        # Attempt initial SQL generation
        t_llm_start = time.time()
        try:
            sql = self.question_to_sql(question, schema)
        except Exception as exc:
            logger.error(f"Initial SQL generation failed: {str(exc)}")
            return "", None, 0, f"AI SQL generation failed: {str(exc)}", time.time() - t_llm_start
        
        total_llm_latency += (time.time() - t_llm_start)

        # Execution and Self-Healing loop
        while True:
            try:
                df = runner.execute(sql)
                # Success!
                return sql, df, attempts, None, total_llm_latency
            except Exception as exc:
                exec_error = str(exc)
                logger.warning(f"SQL execution failed (Attempt {attempts + 1}/{max_repairs + 1}). Error: {exec_error}")

                if attempts >= max_repairs:
                    friendly_err = self._map_to_friendly_error(exec_error, sql)
                    return sql, None, attempts, friendly_err, total_llm_latency

                # Increment repair attempts
                attempts += 1
                logger.info(f"Self-healing loop: Starting SQL repair attempt {attempts}/{max_repairs}...")

                repair_prompt = SQL_REPAIR_PROMPT.format(
                    question=question,
                    schema=schema,
                    previous_sql=sql,
                    error_message=exec_error
                )

                # Call LLM to repair
                t_repair_start = time.time()
                try:
                    response_text = self._generate_with_retry(repair_prompt)
                    sql = self._clean_sql(response_text)
                    self._validate_sql(sql)
                except Exception as repair_exc:
                    logger.error(f"SQL repair attempt {attempts} failed at generation/validation stage.")
                    friendly_err = f"SQL repair failed: {str(repair_exc)}"
                    return sql, None, attempts, friendly_err, total_llm_latency + (time.time() - t_repair_start)

                total_llm_latency += (time.time() - t_repair_start)
