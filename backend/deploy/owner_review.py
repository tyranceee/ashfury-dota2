"""Owner pairing, session, review-job, and signed-payload primitives.

Only hashes of pairing codes and browser session tokens are stored. The module
uses SQLite so the existing single-service deployment does not need a new
database server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_PAIRING_TTL_SECONDS = 15 * 60
DEFAULT_SESSION_TTL_SECONDS = 180 * 24 * 60 * 60
DEFAULT_PAYLOAD_TTL_SECONDS = 60 * 60
REVIEW_JOB_LIMIT = 3
REVIEW_JOB_WINDOW_SECONDS = 60 * 60
PAIRING_ATTEMPT_LIMIT = 10
PAIRING_ATTEMPT_WINDOW_SECONDS = 15 * 60


class InvalidPairingCode(Exception):
    pass


class PairingRateLimited(Exception):
    pass


class ReviewRateLimited(Exception):
    pass


@dataclass(frozen=True)
class OwnerSession:
    token: str
    expires_at: int


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_pairing_code(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())


class OwnerReviewStore:
    def __init__(self, db_path: Path | str, signing_secret_path: Path | str):
        self.db_path = Path(db_path)
        self.signing_secret_path = Path(signing_secret_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._signing_secret = self._load_or_create_signing_secret()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS owner_pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS owner_sessions (
                    session_hash TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS owner_pairing_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_key TEXT NOT NULL,
                    attempted_at INTEGER NOT NULL,
                    succeeded INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_pairing_attempts_window
                    ON owner_pairing_attempts(client_key, attempted_at);
                CREATE TABLE IF NOT EXISTS review_jobs (
                    public_job_id TEXT PRIMARY KEY,
                    match_id INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    trigger_status TEXT NOT NULL DEFAULT 'pending',
                    trigger_detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_review_jobs_created_at
                    ON review_jobs(created_at);
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _load_or_create_signing_secret(self) -> bytes:
        try:
            encoded = self.signing_secret_path.read_text("ascii").strip()
            secret = base64.urlsafe_b64decode(encoded.encode("ascii"))
            if len(secret) < 32:
                raise ValueError("Signing secret is too short")
            return secret
        except FileNotFoundError:
            pass

        self.signing_secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_bytes(48)
        encoded = base64.urlsafe_b64encode(secret).decode("ascii")
        file_descriptor = os.open(
            self.signing_secret_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "w", encoding="ascii") as output:
            output.write(encoded)
            output.write("\n")
        return secret

    def create_pairing_code(self, ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS, now: int | None = None) -> tuple[str, int]:
        now = int(now or time.time())
        ttl_seconds = max(60, min(int(ttl_seconds), 60 * 60))
        raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(16))
        displayed = "-".join(raw[index:index + 4] for index in range(0, 16, 4))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO owner_pairing_codes(code_hash, created_at, expires_at) VALUES (?, ?, ?)",
                (_sha256(raw), now, now + ttl_seconds),
            )
            connection.execute(
                "DELETE FROM owner_pairing_codes WHERE expires_at < ? OR used_at IS NOT NULL",
                (now - 24 * 60 * 60,),
            )
        return displayed, now + ttl_seconds

    def _record_pairing_attempt(self, connection: sqlite3.Connection, client_key: str, now: int, succeeded: bool) -> None:
        connection.execute(
            "INSERT INTO owner_pairing_attempts(client_key, attempted_at, succeeded) VALUES (?, ?, ?)",
            (client_key, now, 1 if succeeded else 0),
        )
        connection.execute(
            "DELETE FROM owner_pairing_attempts WHERE attempted_at < ?",
            (now - 24 * 60 * 60,),
        )

    def exchange_pairing_code(
        self,
        code: str,
        client_key: str,
        now: int | None = None,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> OwnerSession:
        now = int(now or time.time())
        normalized = normalize_pairing_code(code)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            failures = connection.execute(
                """
                SELECT COUNT(*) FROM owner_pairing_attempts
                WHERE client_key = ? AND attempted_at >= ? AND succeeded = 0
                """,
                (client_key, now - PAIRING_ATTEMPT_WINDOW_SECONDS),
            ).fetchone()[0]
            if failures >= PAIRING_ATTEMPT_LIMIT:
                connection.rollback()
                raise PairingRateLimited()

            record = connection.execute(
                "SELECT expires_at, used_at FROM owner_pairing_codes WHERE code_hash = ?",
                (_sha256(normalized),),
            ).fetchone()
            if record is None or record["used_at"] is not None or int(record["expires_at"]) < now:
                self._record_pairing_attempt(connection, client_key, now, False)
                connection.commit()
                raise InvalidPairingCode()

            updated = connection.execute(
                "UPDATE owner_pairing_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
                (now, _sha256(normalized)),
            ).rowcount
            if updated != 1:
                self._record_pairing_attempt(connection, client_key, now, False)
                connection.commit()
                raise InvalidPairingCode()

            token = secrets.token_urlsafe(48)
            expires_at = now + int(session_ttl_seconds)
            connection.execute(
                """
                INSERT INTO owner_sessions(session_hash, created_at, expires_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                """,
                (_sha256(token), now, expires_at, now),
            )
            self._record_pairing_attempt(connection, client_key, now, True)
            connection.commit()
            return OwnerSession(token=token, expires_at=expires_at)

    def validate_session(self, token: str | None, now: int | None = None) -> bool:
        if not token:
            return False
        now = int(now or time.time())
        with self._connect() as connection:
            record = connection.execute(
                "SELECT expires_at, revoked_at FROM owner_sessions WHERE session_hash = ?",
                (_sha256(token),),
            ).fetchone()
            if record is None or record["revoked_at"] is not None or int(record["expires_at"]) < now:
                return False
            connection.execute(
                "UPDATE owner_sessions SET last_seen_at = ? WHERE session_hash = ?",
                (now, _sha256(token)),
            )
            return True

    def revoke_session(self, token: str | None, now: int | None = None) -> None:
        if not token:
            return
        now = int(now or time.time())
        with self._connect() as connection:
            connection.execute(
                "UPDATE owner_sessions SET revoked_at = ? WHERE session_hash = ?",
                (now, _sha256(token)),
            )

    def create_review_job(self, match_id: int, now: int | None = None) -> tuple[dict, bool]:
        now = int(now or time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM review_jobs WHERE match_id = ?",
                (int(match_id),),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return dict(existing), False

            recent_count = connection.execute(
                "SELECT COUNT(*) FROM review_jobs WHERE created_at >= ?",
                (now - REVIEW_JOB_WINDOW_SECONDS,),
            ).fetchone()[0]
            if recent_count >= REVIEW_JOB_LIMIT:
                connection.rollback()
                raise ReviewRateLimited()

            public_job_id = "rvw_" + secrets.token_urlsafe(18)
            connection.execute(
                """
                INSERT INTO review_jobs(public_job_id, match_id, status, created_at)
                VALUES (?, ?, 'queued', ?)
                """,
                (public_job_id, int(match_id), now),
            )
            job = connection.execute(
                "SELECT * FROM review_jobs WHERE public_job_id = ?",
                (public_job_id,),
            ).fetchone()
            connection.commit()
            return dict(job), True

    def get_review_job(self, public_job_id: str) -> dict | None:
        with self._connect() as connection:
            record = connection.execute(
                "SELECT * FROM review_jobs WHERE public_job_id = ?",
                (public_job_id,),
            ).fetchone()
            return dict(record) if record is not None else None

    def set_trigger_result(self, public_job_id: str, status: str, detail: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE review_jobs SET trigger_status = ?, trigger_detail = ? WHERE public_job_id = ?",
                (status, detail, public_job_id),
            )

    def sign_payload(self, public_job_id: str, ttl_seconds: int = DEFAULT_PAYLOAD_TTL_SECONDS, now: int | None = None) -> tuple[int, str]:
        now = int(now or time.time())
        ttl_seconds = max(60, min(int(ttl_seconds), DEFAULT_PAYLOAD_TTL_SECONDS))
        expires = now + ttl_seconds
        message = f"v1\n{public_job_id}\n{expires}".encode("utf-8")
        signature = hmac.new(self._signing_secret, message, hashlib.sha256).hexdigest()
        return expires, signature

    def verify_payload_signature(self, public_job_id: str, expires: int, signature: str, now: int | None = None) -> bool:
        now = int(now or time.time())
        try:
            expires = int(expires)
        except (TypeError, ValueError):
            return False
        if expires < now:
            return False
        message = f"v1\n{public_job_id}\n{expires}".encode("utf-8")
        expected = hmac.new(self._signing_secret, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")
