"""Unit tests for the scoped terminal download credentials."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))

from terminal_access import (  # noqa: E402
    SCOPE_ADMIN,
    SCOPE_ARTIFACT_READ,
    SCOPE_PARSED_READ,
    SCOPE_REVIEW_READ,
    TerminalAccessError,
    TerminalAccessStore,
    normalize_scopes,
)


class TerminalAccessStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = TerminalAccessStore(
            root / "tokens.json",
            root / "audit.ndjson",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_created_token_is_returned_once_but_only_hashed_on_disk(self):
        token = self.store.create_token("mac-mini", [SCOPE_PARSED_READ], ttl_seconds=60)
        self.assertTrue(token._plaintext.startswith("ashfury_"))
        raw = (Path(self.temporary.name) / "tokens.json").read_text("utf-8")
        self.assertNotIn(token._plaintext, raw)
        self.assertIn("token_hash", raw)

    def test_verify_accepts_the_matching_token_and_records_use(self):
        token = self.store.create_token("mac-mini", [SCOPE_PARSED_READ])
        verified = self.store.verify(
            f"Bearer {token._plaintext}", SCOPE_PARSED_READ, client_label="127.0.0.1"
        )
        self.assertEqual(verified.token_id, token.token_id)
        self.assertEqual(verified.use_count, 1)
        self.assertIsNotNone(verified.last_used_at)
        records = self.store.audit_tail(10)
        self.assertEqual(records[0]["event"], "token_used")

    def test_verify_rejects_wrong_scheme_and_unknown_token(self):
        self.store.create_token("mac-mini", [SCOPE_PARSED_READ])
        with self.assertRaises(TerminalAccessError):
            self.store.verify("Basic abc", SCOPE_PARSED_READ)
        with self.assertRaises(TerminalAccessError):
            self.store.verify("Bearer not-a-real-token", SCOPE_PARSED_READ)

    def test_verify_rejects_missing_scope(self):
        token = self.store.create_token("mac-mini", [SCOPE_PARSED_READ])
        with self.assertRaises(TerminalAccessError) as caught:
            self.store.verify(f"Bearer {token._plaintext}", SCOPE_REVIEW_READ)
        self.assertIn("scope", str(caught.exception))

    def test_admin_scope_covers_every_required_scope(self):
        token = self.store.create_token("ops", [SCOPE_ADMIN])
        self.store.verify(f"Bearer {token._plaintext}", SCOPE_PARSED_READ)
        self.store.verify(f"Bearer {token._plaintext}", SCOPE_ARTIFACT_READ)

    def test_expired_and_revoked_tokens_are_rejected(self):
        expired = self.store.create_token("old", [SCOPE_PARSED_READ], ttl_seconds=-1)
        with self.assertRaises(TerminalAccessError) as caught:
            self.store.verify(f"Bearer {expired._plaintext}", SCOPE_PARSED_READ)
        self.assertIn("expired", str(caught.exception))

        revoked = self.store.create_token("gone", [SCOPE_PARSED_READ])
        self.assertTrue(self.store.revoke_token(revoked.token_id))
        with self.assertRaises(TerminalAccessError) as caught:
            self.store.verify(f"Bearer {revoked._plaintext}", SCOPE_PARSED_READ)
        self.assertIn("revoked", str(caught.exception))

    def test_revoke_all_is_idempotent(self):
        first = self.store.create_token("a", [SCOPE_PARSED_READ])
        second = self.store.create_token("b", [SCOPE_PARSED_READ])
        self.assertEqual(self.store.revoke_all(), 2)
        self.assertEqual(self.store.revoke_all(), 0)
        for token_id in (first.token_id, second.token_id):
            listing = {item["token_id"]: item for item in self.store.list_tokens()}
            self.assertFalse(listing[token_id]["active"])

    def test_normalize_scopes_rejects_unknown_values(self):
        with self.assertRaises(TerminalAccessError):
            normalize_scopes(["parsed:write"])
        self.assertEqual(
            normalize_scopes("parsed:read, review:read"),
            [SCOPE_PARSED_READ, SCOPE_REVIEW_READ],
        )

    def test_state_file_survives_reload(self):
        token = self.store.create_token("persisted", [SCOPE_PARSED_READ])
        reloaded = TerminalAccessStore(
            Path(self.temporary.name) / "tokens.json",
            Path(self.temporary.name) / "audit.ndjson",
        )
        verified = reloaded.verify(f"Bearer {token._plaintext}", SCOPE_PARSED_READ)
        self.assertEqual(verified.token_id, token.token_id)

    def test_corrupt_state_file_does_not_crash(self):
        Path(self.temporary.name, "tokens.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.list_tokens(), [])
        token = self.store.create_token("after-corruption", [SCOPE_PARSED_READ])
        self.assertTrue(token.token_id)

    def test_audit_log_is_json_lines(self):
        self.store.create_token("mac-mini", [SCOPE_PARSED_READ])
        lines = (Path(self.temporary.name) / "audit.ndjson").read_text("utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 1)
        for line in lines:
            self.assertIsInstance(json.loads(line), dict)


if __name__ == "__main__":
    unittest.main()
