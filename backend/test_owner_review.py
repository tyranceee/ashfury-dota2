import tempfile
import unittest
from pathlib import Path

from deploy.owner_review import (
    InvalidPairingCode,
    OwnerReviewStore,
    ReviewRateLimited,
)


class OwnerReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = OwnerReviewStore(root / "owner.sqlite3", root / "signing-secret")

    def tearDown(self):
        self.temporary.cleanup()

    def test_pairing_code_is_one_time_and_session_is_valid(self):
        code, _ = self.store.create_pairing_code(now=1000)
        session = self.store.exchange_pairing_code(code.lower(), "client", now=1001)
        self.assertTrue(self.store.validate_session(session.token, now=1002))
        with self.assertRaises(InvalidPairingCode):
            self.store.exchange_pairing_code(code, "client", now=1003)

    def test_duplicate_match_returns_existing_job(self):
        first, created = self.store.create_review_job(9000265617, now=2000)
        second, created_again = self.store.create_review_job(9000265617, now=2001)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["public_job_id"], second["public_job_id"])

    def test_job_limit_is_three_per_hour(self):
        for match_id in (1, 2, 3):
            self.store.create_review_job(match_id, now=3000 + match_id)
        with self.assertRaises(ReviewRateLimited):
            self.store.create_review_job(4, now=3010)
        _, created = self.store.create_review_job(4, now=7000)
        self.assertTrue(created)

    def test_signed_payload_is_scoped_and_expires(self):
        expires, signature = self.store.sign_payload("rvw_one", now=4000)
        self.assertTrue(self.store.verify_payload_signature("rvw_one", expires, signature, now=4001))
        self.assertFalse(self.store.verify_payload_signature("rvw_two", expires, signature, now=4001))
        self.assertFalse(self.store.verify_payload_signature("rvw_one", expires, signature, now=expires + 1))


if __name__ == "__main__":
    unittest.main()
