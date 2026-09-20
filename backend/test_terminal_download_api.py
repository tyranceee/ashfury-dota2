"""End-to-end tests for the authorized terminal downloads and DeepSeek review API."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))
from deepseek_review import prompt_digest  # noqa: E402


@unittest.skipIf(TestClient is None, "FastAPI is not installed in the local test environment")
class TerminalDownloadApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.match_id = 9004864716
        (cls.base / "data").mkdir()
        (cls.base / "matches_index.json").write_text(
            json.dumps({
                str(cls.match_id): {
                    "match_id": cls.match_id,
                    "start_time": 1789831103,
                    "duration": 2735,
                    "hero_id": 89,
                    "player_slot": 0,
                    "radiant_win": True,
                    "kills": 9,
                    "deaths": 1,
                    "assists": 10,
                    "parsed": True,
                    "parse_status": "parsed",
                    "parse_source": "opendota",
                },
                "9000000001": {
                    "match_id": 9000000001,
                    "start_time": 1789830000,
                    "parsed": False,
                    "parse_status": "waiting_local",
                },
            }),
            encoding="utf-8",
        )
        (cls.base / "data" / f"{cls.match_id}.json").write_text(
            json.dumps({
                "match_id": cls.match_id,
                "version": 22,
                "teamfights": [],
                "objectives": [],
                "radiant_gold_adv": [0],
                "radiant_xp_adv": [0],
                "chat": [{"key": "ignore me"}],
                "players": [{"account_id": 212121467, "player_slot": 0, "hero_id": 89}],
            }),
            encoding="utf-8",
        )
        os.environ["DOTA2_BASE_DIR"] = str(cls.base)
        os.environ["DOTA2_REVIEWS_DIR"] = str(cls.base / "reviews")
        os.environ["DOTA2_DEEPSEEK_OUTPUTS"] = str(cls.base / "preliminary_reviews")
        deploy_dir = str(Path(__file__).parent / "deploy")
        if deploy_dir not in sys.path:
            sys.path.insert(0, deploy_dir)
        sys.modules.pop("rest_server", None)
        cls.server = importlib.import_module("rest_server")
        cls.server.HERO_MAP = {}
        cls.server.HERO_IMAGE_MAP = {}
        cls.client = TestClient(cls.server.app, base_url="https://ashfury.cn")
        # Seed a prompt so enqueue tests never depend on execution order.
        cls.server.DEEPSEEK_PROMPTS.create_revision(
            "# 初步解析 Prompt（第一版）\n\n按结构输出。", title="初版"
        )

    def setUp(self):
        # Every test starts unauthenticated so cookie state cannot leak between
        # tests through the shared TestClient.
        self.client.cookies.clear()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def owner_login(self):
        code, _ = self.server.OWNER_REVIEW.create_pairing_code(now=int(time.time()))
        paired = self.client.post(
            "/owner-session",
            json={"code": code},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(paired.status_code, 200)
        session_value = paired.cookies.get(self.server.OWNER_COOKIE_NAME)
        self.client.cookies.set(self.server.OWNER_COOKIE_NAME, session_value, path="/")
        return session_value

    # ----- terminal credentials ---------------------------------------

    def test_unauthenticated_download_is_rejected(self):
        response = self.client.get(f"/v1/terminal/parsed/{self.match_id}")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_unknown_bearer_token_is_rejected(self):
        response = self.client.get(
            f"/v1/terminal/parsed/{self.match_id}",
            headers={"Authorization": "Bearer ashfury_not-a-real-token"},
        )
        self.assertEqual(response.status_code, 401)

    def test_owner_mints_a_token_and_the_terminal_downloads_json(self):
        self.owner_login()
        before = {
            item["token_id"] for item in self.client.get("/v1/terminal/tokens").json()["tokens"]
        }
        created = self.client.post(
            "/v1/terminal/tokens",
            json={"label": "mac-mini", "scopes": ["parsed:read", "review:read"]},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(created.status_code, 201)
        token = created.json()["plaintext_token"]
        self.assertTrue(token.startswith("ashfury_"))

        listing = self.client.get("/v1/terminal/tokens")
        self.assertEqual(listing.status_code, 200)
        fresh = [
            item for item in listing.json()["tokens"]
            if item["token_id"] not in before
        ]
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["label"], "mac-mini")
        self.assertNotIn(token, json.dumps(listing.json()))

        self.client.cookies.clear()
        download = self.client.get(
            f"/v1/terminal/parsed/{self.match_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertIn(
            f"match_{self.match_id}.json", download.headers["content-disposition"]
        )
        self.assertEqual(download.headers["x-ashfury-access-channel"], "terminal_token")
        self.assertEqual(download.json()["match_id"], self.match_id)

    def test_capabilities_requires_a_valid_credential(self):
        self.assertEqual(self.client.get("/v1/terminal/capabilities").status_code, 401)
        self.owner_login()
        response = self.client.get("/v1/terminal/capabilities")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["access"]["channel"], "owner_session")
        self.assertIn("parsed:read", body["known_scopes"])
        self.assertTrue(any(
            item["path"] == "/dota2/api/v1/terminal/parsed/{match_id}"
            for item in body["endpoints"]
        ))

    def test_terminal_index_lists_only_parsed_matches(self):
        self.owner_login()
        response = self.client.get("/v1/terminal/index")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        item = body["items"][0]
        self.assertEqual(item["match_id"], self.match_id)
        self.assertEqual(item["filename"], f"match_{self.match_id}.json")
        self.assertTrue(item["cached_locally"])
        self.assertIsNotNone(item["sha256"])

    def test_scope_is_enforced_per_endpoint(self):
        self.owner_login()
        created = self.client.post(
            "/v1/terminal/tokens",
            json={"label": "artifacts-only", "scopes": ["artifact:read"]},
            headers={"Origin": "https://ashfury.cn"},
        )
        token = created.json()["plaintext_token"]
        self.client.cookies.clear()
        denied = self.client.get(
            f"/v1/terminal/parsed/{self.match_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertIn("scope", denied.json()["detail"])

    def test_revoked_token_stops_working(self):
        self.owner_login()
        created = self.client.post(
            "/v1/terminal/tokens",
            json={"label": "short-lived", "scopes": ["parsed:read"]},
            headers={"Origin": "https://ashfury.cn"},
        )
        body = created.json()
        token = body["plaintext_token"]
        token_id = body["token"]["token_id"]
        revoked = self.client.delete(f"/v1/terminal/tokens/{token_id}")
        self.assertEqual(revoked.status_code, 200)
        self.client.cookies.clear()
        denied = self.client.get(
            f"/v1/terminal/parsed/{self.match_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertIn("revoked", denied.json()["detail"])

    def test_bearer_token_cannot_mint_more_tokens(self):
        self.owner_login()
        created = self.client.post(
            "/v1/terminal/tokens",
            json={"label": "reader", "scopes": ["parsed:read"]},
            headers={"Origin": "https://ashfury.cn"},
        )
        token = created.json()["plaintext_token"]
        self.client.cookies.clear()
        denied = self.client.post(
            "/v1/terminal/tokens",
            json={"label": "escalation", "scopes": ["admin"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(denied.status_code, 403)

    def test_cross_origin_cookie_use_is_rejected(self):
        self.owner_login()
        response = self.client.get(
            f"/v1/terminal/parsed/{self.match_id}",
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_terminal_owner_can_use_the_cookie_channel(self):
        self.owner_login()
        download = self.client.get(f"/v1/terminal/parsed/{self.match_id}")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["x-ashfury-access-channel"], "owner_session")

    def test_unparsed_match_has_no_json_to_download(self):
        self.owner_login()
        response = self.client.get("/v1/terminal/parsed/9000000001")
        self.assertIn(response.status_code, {409, 502, 503})

    # ----- preliminary review API --------------------------------------

    def test_auto_review_is_refused_when_no_prompt_is_stored(self):
        self.owner_login()
        saved = self.server.DEEPSEEK_PROMPTS.path.read_text("utf-8")
        try:
            self.server.DEEPSEEK_PROMPTS.path.write_text(
                json.dumps({"schema_version": "x", "revisions": [], "active_revision": None}),
                encoding="utf-8",
            )
            response = self.client.put(
                "/v1/deepseek/settings",
                json={"auto_review_enabled": True},
                headers={"Origin": "https://ashfury.cn"},
            )
            self.assertEqual(response.status_code, 409)
            self.assertIn("Prompt", response.json()["detail"])

            # The worker itself must also stay idle rather than calling DeepSeek.
            cycle = self.client.post(
                "/v1/deepseek/run-now",
                headers={"Origin": "https://ashfury.cn"},
            )
            self.assertEqual(cycle.status_code, 200)
            self.assertIn(cycle.json()["action"], {"idle", "disabled", "waiting", "deferring"})
        finally:
            self.server.DEEPSEEK_PROMPTS.path.write_text(saved, encoding="utf-8")
            self.server.DEEPSEEK_JOBS.update_settings({"auto_review_enabled": False})

    def test_prompt_upload_then_auto_review_toggle(self):
        self.owner_login()
        content = "# 初步解析 Prompt（第二版）\n\n按结构输出，并标注证据来源。"
        created = self.client.post(
            "/v1/deepseek/prompts",
            json={"content": content, "title": "第二版"},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["revision"], 2)
        self.assertEqual(created.json()["sha256"], prompt_digest(content))

        # Re-pasting identical Markdown reuses the revision instead of duplicating.
        again = self.client.post(
            "/v1/deepseek/prompts",
            json={"content": content},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["reused"])
        self.assertEqual(again.json()["revision"], 2)

        revisions = self.client.get("/v1/deepseek/prompts?include_content=true").json()
        self.assertEqual(revisions["active_revision"], 2)
        self.assertEqual(revisions["revision_count"], 2)
        stored = next(item for item in revisions["revisions"] if item["revision"] == 2)
        self.assertEqual(stored["content"], content)

        # Switching back to the first revision is allowed.
        activated = self.client.put(
            "/v1/deepseek/prompts/active",
            json={"revision": 1},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(activated.status_code, 200)
        self.assertEqual(activated.json()["revision"], 1)

        enabled = self.client.put(
            "/v1/deepseek/settings",
            json={"auto_review_enabled": True, "batch_size": 3},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.json()["settings"]["auto_review_enabled"])
        self.assertEqual(enabled.json()["settings"]["batch_size"], 3)

        status = self.client.get("/v1/deepseek/status").json()
        self.assertTrue(status["prompt"]["configured"])
        self.assertEqual(status["prompt"]["active_revision"], 1)
        self.assertEqual(status["batch_size"], 3)
        self.assertIn("off_peak_now", status["schedule"])
        self.assertFalse(status["deepseek_key_configured"])
        self.assertFalse(status["web_search"]["enabled"])
        self.assertIn("web_search_20250305", status["web_search"]["mechanism"])
        self.assertEqual(status["web_search"]["query_policy"], "verbatim_from_upstream_no_rewrite")

    def test_prompt_upload_requires_owner_and_rejects_extra_fields(self):
        denied = self.client.post("/v1/deepseek/prompts", json={"content": "x"})
        self.assertEqual(denied.status_code, 403)
        self.owner_login()
        extra = self.client.post(
            "/v1/deepseek/prompts",
            json={"content": "x", "model": "deepseek-v4-pro"},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(extra.status_code, 422)

    def test_peak_vs_off_peak_schedule_is_exposed(self):
        schedule = self.client.get("/v1/deepseek/schedule").json()
        self.assertEqual(
            schedule["peak_windows_utc"], ["01:00-04:00", "06:00-10:00"]
        )
        # Displayed in Beijing time (UTC+8) for the Owner.
        self.assertEqual(
            schedule["peak_windows_beijing"], ["09:00-12:00", "14:00-18:00"]
        )
        self.assertEqual(schedule["peak_days"], "Monday-Friday")
        self.assertGreater(schedule["holidays_loaded"], 0)
        self.assertIn("half price", schedule["off_peak_rule"])

    def test_enqueue_requires_owner_and_a_parsed_match(self):
        self.client.cookies.clear()
        denied = self.client.post(
            "/v1/deepseek/preliminary-reviews",
            json={"match_id": self.match_id},
        )
        self.assertEqual(denied.status_code, 403)

        self.owner_login()
        queued = self.client.post(
            "/v1/deepseek/preliminary-reviews",
            json={"match_id": self.match_id},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(queued.status_code, 201)
        self.assertEqual(queued.json()["job"]["match_id"], self.match_id)
        self.assertIn("错峰", queued.json()["note"])

        unparsed = self.client.post(
            "/v1/deepseek/preliminary-reviews",
            json={"match_id": 9000000001},
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(unparsed.status_code, 409)

    def test_missing_key_keeps_the_schedule_safe(self):
        self.owner_login()
        cycle = self.client.post(
            "/v1/deepseek/run-now",
            headers={"Origin": "https://ashfury.cn"},
        )
        self.assertEqual(cycle.status_code, 200)
        self.assertIn(
            cycle.json()["action"],
            {"processed", "idle", "waiting", "deferring", "disabled", "busy"},
        )

    def test_preliminary_review_download_requires_scope_or_owner(self):
        self.client.cookies.clear()
        self.assertEqual(
            self.client.get(f"/v1/preliminary-reviews/{self.match_id}/json").status_code,
            401,
        )

    def _write_review(self, text: str):
        output_dir = self.server.DEEPSEEK_WORKER.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{self.match_id}.md"
        path.write_text(text, encoding="utf-8")
        self.addCleanup(path.unlink, True)
        return path

    def _write_review_json(self):
        path = self.server.DEEPSEEK_WORKER.output_paths(self.match_id)["json"]
        self.server.save_json(
            path,
            {"match_id": self.match_id, "content_markdown": "# 结论\n",
             "generated_at": 1789909716, "model": "deepseek-flash",
             "prompt": {"revision": 1}},
        )
        self.addCleanup(path.unlink, True)
        return path

    def test_preview_renders_readable_html_for_the_owner(self):
        self.owner_login()
        self._write_review("# 一句话结论\n\n本场靠肉山窗口拿下比赛 [数据]。\n")
        response = self.client.get(f"/v1/preliminary-reviews/{self.match_id}/preview")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertIn(f"比赛 {self.match_id} 初步解析", response.text)
        self.assertIn("本场靠肉山窗口拿下比赛", response.text)
        # Rendered inline rather than as a download, and locked down hard.
        self.assertNotIn("attachment", response.headers.get("content-disposition", ""))
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])
        self.assertIn("noindex", response.headers["x-robots-tag"])

    def test_preview_escapes_review_text_so_it_cannot_inject_markup(self):
        self.owner_login()
        self._write_review(
            "# 结论\n\n<script>alert('x')</script>\n"
            "<img src=x onerror=alert(2)>\n"
        )
        response = self.client.get(f"/v1/preliminary-reviews/{self.match_id}/preview")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>", response.text)
        self.assertNotIn("<img src=x", response.text)
        self.assertIn("&lt;script&gt;", response.text)

    def test_preview_requires_a_credential(self):
        self.client.cookies.clear()
        self.assertEqual(
            self.client.get(f"/v1/preliminary-reviews/{self.match_id}/preview").status_code,
            401,
        )

    def test_index_exposes_the_preview_url(self):
        self.owner_login()
        self._write_review("# 结论\n")
        self._write_review_json()
        items = self.client.get("/v1/preliminary-reviews").json()["items"]
        entry = next(item for item in items if item["match_id"] == self.match_id)
        self.assertTrue(entry["markdown"]["preview_url"].endswith("/preview"))
        self.assertTrue(entry["markdown"]["download_url"].endswith("/markdown"))
        self.assertEqual(entry["match"]["match_id"], self.match_id)
        self.assertEqual(entry["match"]["kda"], "9/1/10")
        self.assertEqual(entry["match"]["duration"], "45:35")

    def test_bundled_valve_catalog_has_current_chinese_hero_names(self):
        previous_names = self.server.HERO_MAP
        previous_images = self.server.HERO_IMAGE_MAP
        try:
            self.server.HERO_MAP = None
            self.server.HERO_IMAGE_MAP = None
            names = self.server.hero_map()
            self.assertGreaterEqual(len(names), 127)
            self.assertEqual(names[29], "潮汐猎人")
            self.assertEqual(names[53], "自然先知")
            self.assertEqual(names[89], "娜迦海妖")
            self.assertEqual(names[135], "破晓辰星")
            self.assertEqual(names[145], "凯")
            self.assertEqual(names[155], "朗戈")
            self.assertTrue(self.server.hero_image_map()[53].endswith("/furion.png"))
        finally:
            self.server.HERO_MAP = previous_names
            self.server.HERO_IMAGE_MAP = previous_images

    def test_preliminary_index_is_publicly_listable(self):
        response = self.client.get("/v1/preliminary-reviews")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)


if __name__ == "__main__":
    unittest.main()
