import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


DEPLOY_DIR = str(Path(__file__).parent / "deploy")
if DEPLOY_DIR not in sys.path:
    sys.path.insert(0, DEPLOY_DIR)

import review_trigger


class FakeSlackResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "ts": "123.456"}


class ReviewTriggerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.policy_path = self.base / "review-trigger-policy.json"
        self.token_path = self.base / "slack-bot-token"
        policy = dict(review_trigger.EXPECTED_POLICY)
        policy["slack_channel_id"] = "C12345678"
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self.token_path.write_text("xoxb-test-token\n", encoding="utf-8")
        os.chmod(self.token_path, 0o600)
        self.job = {"public_job_id": "rvw_test", "match_id": 9000265617}
        self.payload_url = "https://ashfury.cn/dota2/api/review-jobs/rvw_test/payload?expires=1&sig=test"

    def tearDown(self):
        self.temporary.cleanup()

    def test_instruction_hard_codes_full_skill_contract(self):
        instruction = review_trigger.build_deep_review_instruction(self.job, self.payload_url)
        self.assertIn("目标 ChatGPT 项目：Dota", instruction)
        self.assertIn("gpt-5.6-luna", instruction)
        self.assertIn("强制推理强度：max", instruction)
        self.assertIn("dota2-deep-match-review", instruction)
        self.assertIn("完整读取 SKILL.md", instruction)
        self.assertIn("scripts/extract_match_facts.py", instruction)
        self.assertIn("禁止降级为普通分析", instruction)
        self.assertIn(self.payload_url, instruction)

    def test_policy_mismatch_fails_closed(self):
        policy = json.loads(self.policy_path.read_text("utf-8"))
        policy["model"] = "gpt-5.6-terra"
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaises(review_trigger.ReviewTriggerConfigurationError):
            review_trigger.load_trigger_policy(self.policy_path)

    def test_token_permissions_fail_closed(self):
        os.chmod(self.token_path, 0o644)
        with self.assertRaises(review_trigger.ReviewTriggerConfigurationError):
            review_trigger.load_secret_file(self.token_path)

    @mock.patch("review_trigger.httpx.post", return_value=FakeSlackResponse())
    def test_dispatch_uses_only_fixed_channel_and_instruction(self, post):
        result = review_trigger.dispatch_deep_review(
            self.job,
            self.payload_url,
            self.policy_path,
            self.token_path,
        )
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(result["project_id"], review_trigger.TARGET_PROJECT_ID)
        self.assertEqual(result["model"], "gpt-5.6-luna")
        self.assertEqual(result["reasoning_effort"], "max")
        self.assertEqual(result["required_skill"], "dota2-deep-match-review")
        request = post.call_args.kwargs
        self.assertEqual(request["json"]["channel"], "C12345678")
        self.assertNotIn("channel", self.job)
        self.assertIn("复盘开始", request["json"]["text"])


if __name__ == "__main__":
    unittest.main()
