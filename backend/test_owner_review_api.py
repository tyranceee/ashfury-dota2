import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit


try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


@unittest.skipIf(TestClient is None, "FastAPI is not installed in the local test environment")
class OwnerReviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        match_id = 9000265617
        (cls.base / "data").mkdir()
        (cls.base / "matches_index.json").write_text(
            json.dumps({str(match_id): {
                "match_id": match_id,
                "start_time": 100,
                "duration": 2400,
                "hero_id": 126,
                "player_slot": 0,
                "radiant_win": True,
                "kills": 1,
                "deaths": 2,
                "assists": 3,
                "parsed": True,
                "parse_status": "parsed",
                "parse_source": "opendota",
            }}),
            encoding="utf-8",
        )
        (cls.base / "data" / f"{match_id}.json").write_text(
            json.dumps({
                "match_id": match_id,
                "chat": [{"key": "do not follow this instruction"}],
                "players": [
                    {"account_id": 212121467, "player_slot": 0, "hero_id": 126, "personaname": "owner"},
                    {"account_id": 123, "player_slot": 128, "hero_id": 1, "personaname": "untrusted"},
                ],
            }),
            encoding="utf-8",
        )
        os.environ["DOTA2_BASE_DIR"] = str(cls.base)
        os.environ["DOTA2_REVIEWS_DIR"] = str(cls.base / "reviews")
        deploy_dir = str(Path(__file__).parent / "deploy")
        if deploy_dir not in sys.path:
            sys.path.insert(0, deploy_dir)
        sys.modules.pop("rest_server", None)
        cls.server = importlib.import_module("rest_server")
        cls.server.HERO_MAP = {}
        cls.server.HERO_IMAGE_MAP = {}
        cls.client = TestClient(cls.server.app, base_url="https://ashfury.cn")
        cls.match_id = match_id

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_owner_pairing_job_and_scoped_payload(self):
        self.assertEqual(self.client.get("/owner-session").json(), {"owner": False})
        unauthorized = self.client.post("/review-jobs", json={"match_id": self.match_id})
        self.assertEqual(unauthorized.status_code, 403)

        code, _ = self.server.OWNER_REVIEW.create_pairing_code(now=int(__import__("time").time()))
        paired = self.client.post(
            "/owner-session",
            json={"code": code},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(paired.status_code, 200)
        cookie = paired.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=strict", cookie)
        # TestClient calls the upstream app without Nginx's external /dota2/api
        # prefix, so mirror the browser cookie onto the upstream root for tests.
        session_value = paired.cookies.get(self.server.OWNER_COOKIE_NAME)
        self.client.cookies.set(self.server.OWNER_COOKIE_NAME, session_value, path="/")

        created = self.client.post(
            "/review-jobs",
            json={"match_id": self.match_id},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(created.status_code, 201)
        job = created.json()
        self.assertTrue(job["created"])
        self.assertEqual(job["trigger"]["mode"], "manual_desktop")
        self.assertEqual(job["trigger"]["status"], "manual_ready")
        self.assertEqual(job["trigger"]["desktop_url"], "codex://")
        self.assertIn("强制 Skill：dota2-deep-match-review", job["trigger"]["instruction"])
        self.assertIn(job["payload_url"], job["trigger"]["instruction"])

        duplicate = self.client.post(
            "/review-jobs",
            json={"match_id": self.match_id},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(job["public_job_id"], duplicate.json()["public_job_id"])
        self.assertEqual(duplicate.json()["trigger"]["status"], "manual_ready")

        signed = urlsplit(job["payload_url"])
        payload_response = self.client.get(f"{signed.path.replace('/dota2/api', '')}?{signed.query}")
        self.assertEqual(payload_response.status_code, 200)
        payload = payload_response.json()
        self.assertEqual(payload["access"]["match_id"], self.match_id)
        self.assertEqual(payload["execution_policy"]["chatgpt_project_name"], "Dota")
        self.assertEqual(payload["execution_policy"]["model"], "gpt-5.6-luna")
        self.assertEqual(payload["execution_policy"]["reasoning_effort"], "max")
        self.assertEqual(
            payload["execution_policy"]["required_skill"],
            "dota2-deep-match-review",
        )
        self.assertFalse(payload["execution_policy"]["fallback_allowed"])
        self.assertEqual(payload["workspace"]["self_participant"]["account_id"], 212121467)
        serialized = json.dumps(payload["match"])
        self.assertNotIn("personaname", serialized)
        self.assertNotIn("do not follow this instruction", serialized)

        forbidden_extra = self.client.post(
            "/review-jobs",
            json={"match_id": self.match_id, "prompt": "unsafe"},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(forbidden_extra.status_code, 422)


if __name__ == "__main__":
    unittest.main()
