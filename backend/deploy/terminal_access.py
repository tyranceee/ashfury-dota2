"""Authorized-terminal access to parsed match artifacts and generated files.

Two independent authorization channels exist:

* ``Bearer`` tokens minted by the server for command-line terminals. Only the
  SHA-256 hash of a token is stored, tokens carry an explicit scope set, and
  every use is appended to an audit log.
* The existing Owner browser session cookie, which is validated by
  :class:`owner_review.OwnerReviewStore` before this module is consulted.

Both channels are read-only. Nothing in this module can upload, delete, or
re-parse data, and no channel is ever accepted from a cross-origin browser
request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_SCHEMA_VERSION = "ashfury.terminal-access.v1"
DEFAULT_TOKEN_TTL_SECONDS = 180 * 24 * 60 * 60
MAX_AUDIT_RECORDS = 2000

SCOPE_PARSED_READ = "parsed:read"
SCOPE_ARTIFACT_READ = "artifact:read"
SCOPE_REVIEW_READ = "review:read"
SCOPE_REVIEW_ENQUEUE = "review:enqueue"
SCOPE_ADMIN = "admin"

KNOWN_SCOPES = (
    SCOPE_PARSED_READ,
    SCOPE_ARTIFACT_READ,
    SCOPE_REVIEW_READ,
    SCOPE_REVIEW_ENQUEUE,
    SCOPE_ADMIN,
)


class TerminalAccessError(Exception):
    """Raised for every rejected terminal token."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_scopes(scopes) -> list[str]:
    if scopes is None:
        return list(KNOWN_SCOPES)
    if isinstance(scopes, str):
        scopes = [item.strip() for item in scopes.split(",")]
    normalized = []
    for scope in scopes:
        scope = str(scope).strip()
        if not scope:
            continue
        if scope not in KNOWN_SCOPES:
            raise TerminalAccessError(f"Unknown scope: {scope}")
        if scope not in normalized:
            normalized.append(scope)
    if not normalized:
        raise TerminalAccessError("At least one scope is required")
    return normalized


def token_has_scope(token: dict, scope: str) -> bool:
    granted = set(token.get("scopes") or [])
    return SCOPE_ADMIN in granted or scope in granted


@dataclass
class TerminalToken:
    token_id: str
    label: str
    scopes: list[str]
    created_at: int
    expires_at: int
    last_used_at: int | None = None
    use_count: int = 0
    revoked_at: int | None = None
    source: str = "unknown"
    _plaintext: str | None = field(default=None, repr=False)

    def expired(self, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        return self.expires_at > 0 and now >= self.expires_at

    def active(self, now: int | None = None) -> bool:
        return self.revoked_at is None and not self.expired(now)

    def public(self, now: int | None = None) -> dict:
        now = int(time.time()) if now is None else now
        return {
            "token_id": self.token_id,
            "label": self.label,
            "scopes": list(self.scopes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "revoked_at": self.revoked_at,
            "active": self.active(now),
            "expired": self.expired(now),
        }


class TerminalAccessStore:
    """JSON-backed token registry plus an append-only audit log.

    The store is deliberately small and file-based so the existing
    single-service deployment does not need a second database. Every mutation
    is written atomically through a temporary file and a lock.
    """

    def __init__(self, state_path: Path | str, audit_path: Path | str):
        self.state_path = Path(state_path)
        self.audit_path = Path(audit_path)
        self._lock = threading.Lock()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    # ----- persistence -------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text("utf-8"))
        except FileNotFoundError:
            return {"schema_version": TOKEN_SCHEMA_VERSION, "tokens": {}}
        except (OSError, json.JSONDecodeError):
            return {"schema_version": TOKEN_SCHEMA_VERSION, "tokens": {}}
        if not isinstance(data, dict):
            return {"schema_version": TOKEN_SCHEMA_VERSION, "tokens": {}}
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            data["tokens"] = {}
        data.setdefault("schema_version", TOKEN_SCHEMA_VERSION)
        return data

    def _save(self, data: dict) -> None:
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.state_path)

    def _audit(self, record: dict) -> None:
        record = {"at": int(time.time()), **record}
        try:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            os.chmod(self.audit_path, 0o600)
        except OSError:
            pass
        self._trim_audit()

    def _trim_audit(self) -> None:
        try:
            if self.audit_path.stat().st_size < 512 * 1024:
                return
            lines = self.audit_path.read_text("utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= MAX_AUDIT_RECORDS:
            return
        try:
            self.audit_path.write_text(
                "\n".join(lines[-MAX_AUDIT_RECORDS:]) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    # ----- token lifecycle --------------------------------------------

    @staticmethod
    def _token_id() -> str:
        return "tk_" + secrets.token_hex(6)

    def create_token(
        self,
        label: str,
        scopes=None,
        ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
        source: str = "unknown",
    ) -> TerminalToken:
        label = (label or "").strip() or "terminal"
        if len(label) > 80:
            raise TerminalAccessError("Token label is too long")
        normalized = normalize_scopes(scopes)
        now = int(time.time())
        plaintext = "ashfury_" + secrets.token_urlsafe(32)
        token = TerminalToken(
            token_id=self._token_id(),
            label=label,
            scopes=normalized,
            created_at=now,
            expires_at=now + int(ttl_seconds) if ttl_seconds else 0,
            source=source,
            _plaintext=plaintext,
        )
        with self._lock:
            data = self._load()
            data["tokens"][token.token_id] = {
                "token_hash": _sha256(plaintext),
                "label": token.label,
                "scopes": token.scopes,
                "created_at": token.created_at,
                "expires_at": token.expires_at,
                "last_used_at": None,
                "use_count": 0,
                "revoked_at": None,
                "source": source,
            }
            self._save(data)
        self._audit({
            "event": "token_created",
            "token_id": token.token_id,
            "label": token.label,
            "scopes": token.scopes,
            "expires_at": token.expires_at,
            "source": source,
        })
        return token

    def list_tokens(self, now: int | None = None) -> list[dict]:
        now = int(time.time()) if now is None else now
        with self._lock:
            data = self._load()
        records = []
        for token_id, record in data["tokens"].items():
            token = TerminalToken(
                token_id=token_id,
                label=record.get("label", ""),
                scopes=list(record.get("scopes") or []),
                created_at=int(record.get("created_at") or 0),
                expires_at=int(record.get("expires_at") or 0),
                last_used_at=record.get("last_used_at"),
                use_count=int(record.get("use_count") or 0),
                revoked_at=record.get("revoked_at"),
            )
            records.append(token.public(now))
        records.sort(key=lambda item: item["created_at"], reverse=True)
        return records

    def revoke_token(self, token_id: str) -> bool:
        with self._lock:
            data = self._load()
            record = data["tokens"].get(token_id)
            if record is None:
                return False
            if record.get("revoked_at") is None:
                record["revoked_at"] = int(time.time())
                self._save(data)
        self._audit({"event": "token_revoked", "token_id": token_id})
        return True

    def revoke_all(self) -> int:
        now = int(time.time())
        revoked = 0
        with self._lock:
            data = self._load()
            for record in data["tokens"].values():
                if record.get("revoked_at") is None:
                    record["revoked_at"] = now
                    revoked += 1
            if revoked:
                self._save(data)
        if revoked:
            self._audit({"event": "tokens_revoked_all", "count": revoked})
        return revoked

    # ----- verification ------------------------------------------------

    def verify(
        self,
        authorization: str | None,
        required_scope: str,
        client_label: str = "",
        record_use: bool = True,
    ) -> TerminalToken:
        scheme, separator, supplied = (authorization or "").partition(" ")
        supplied = supplied.strip()
        if not separator or scheme.lower() != "bearer" or not supplied:
            raise TerminalAccessError("Missing bearer credentials")

        digest = _sha256(supplied)
        now = int(time.time())
        with self._lock:
            data = self._load()
            match_id = None
            match_record = None
            for token_id, record in data["tokens"].items():
                stored = str(record.get("token_hash") or "")
                if stored and hmac.compare_digest(stored, digest):
                    match_id = token_id
                    match_record = record
                    break
            if match_record is None:
                self._audit({
                    "event": "token_rejected",
                    "reason": "unknown_token",
                    "client": client_label,
                })
                raise TerminalAccessError("Unknown terminal token")

            revoked_at = match_record.get("revoked_at")
            expires_at = int(match_record.get("expires_at") or 0)
            if revoked_at is not None:
                self._audit({
                    "event": "token_rejected",
                    "reason": "revoked",
                    "token_id": match_id,
                    "client": client_label,
                })
                raise TerminalAccessError("Terminal token has been revoked")
            if expires_at > 0 and now >= expires_at:
                self._audit({
                    "event": "token_rejected",
                    "reason": "expired",
                    "token_id": match_id,
                    "client": client_label,
                })
                raise TerminalAccessError("Terminal token has expired")

            token = TerminalToken(
                token_id=match_id,
                label=match_record.get("label", ""),
                scopes=list(match_record.get("scopes") or []),
                created_at=int(match_record.get("created_at") or 0),
                expires_at=expires_at,
                last_used_at=match_record.get("last_used_at"),
                use_count=int(match_record.get("use_count") or 0),
                revoked_at=revoked_at,
            )
            if not token_has_scope(match_record, required_scope):
                self._audit({
                    "event": "token_rejected",
                    "reason": "missing_scope",
                    "token_id": match_id,
                    "required_scope": required_scope,
                    "client": client_label,
                })
                raise TerminalAccessError(
                    f"Terminal token is missing the {required_scope} scope"
                )

            if record_use:
                match_record["last_used_at"] = now
                match_record["use_count"] = int(match_record.get("use_count") or 0) + 1
                self._save(data)
                token.last_used_at = now
                token.use_count = match_record["use_count"]
                self._audit({
                    "event": "token_used",
                    "token_id": match_id,
                    "required_scope": required_scope,
                    "client": client_label,
                })
        return token

    def audit_tail(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        try:
            lines = self.audit_path.read_text("utf-8").splitlines()
        except OSError:
            return []
        records = []
        for line in lines[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        records.reverse()
        return records
