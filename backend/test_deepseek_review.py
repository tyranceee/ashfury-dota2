"""Unit tests for DeepSeek off-peak window arithmetic, prompts, and job state."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))

from deepseek_review import (  # noqa: E402
    JOB_DONE,
    JOB_PAUSED,
    JOB_PENDING,
    PromptStore,
    ReviewJobStore,
    estimate_cost,
    is_off_peak,
    load_holiday_calendar,
    next_off_peak_start,
    next_window_boundary,
    off_peak_seconds_remaining,
    prompt_digest,
    schedule_snapshot,
)

HOLIDAYS = {"2026-10-01", "2026-02-17"}


def utc(year, month, day, hour, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)


class OffPeakWindowTest(unittest.TestCase):
    def test_weekday_peak_windows_are_peak(self):
        # 2026-09-21 is a Monday.
        for hour in (1, 2, 3, 6, 7, 8, 9):
            self.assertFalse(is_off_peak(utc(2026, 9, 21, hour), HOLIDAYS), hour)

    def test_weekday_off_peak_windows(self):
        for hour in (0, 4, 5, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23):
            self.assertTrue(is_off_peak(utc(2026, 9, 21, hour), HOLIDAYS), hour)

    def test_weekends_are_off_peak_all_day(self):
        self.assertTrue(is_off_peak(utc(2026, 9, 19, 2), HOLIDAYS))   # Saturday
        self.assertTrue(is_off_peak(utc(2026, 9, 20, 8), HOLIDAYS))   # Sunday

    def test_chinese_public_holiday_weekday_is_off_peak(self):
        self.assertTrue(is_off_peak(utc(2026, 10, 1, 2), HOLIDAYS))
        self.assertFalse(is_off_peak(utc(2026, 10, 1, 2), set()))

    def test_next_change_from_off_peak_points_at_peak_start(self):
        boundary, state = next_window_boundary(utc(2026, 9, 21, 12), HOLIDAYS)
        self.assertEqual(boundary, utc(2026, 9, 22, 1))
        self.assertFalse(state)

    def test_next_change_from_peak_points_at_off_peak_start(self):
        boundary, state = next_window_boundary(utc(2026, 9, 21, 2), HOLIDAYS)
        self.assertEqual(boundary, utc(2026, 9, 21, 4))
        self.assertTrue(state)

    def test_next_off_peak_start_returns_now_when_already_off_peak(self):
        moment = utc(2026, 9, 21, 20)
        self.assertEqual(next_off_peak_start(moment, HOLIDAYS), moment)

    def test_next_off_peak_start_skips_the_five_hour_gap(self):
        # 10:00-14:00 UTC on a weekday is off-peak between two peak windows.
        self.assertEqual(next_off_peak_start(utc(2026, 9, 21, 2), HOLIDAYS), utc(2026, 9, 21, 4))
        self.assertEqual(next_off_peak_start(utc(2026, 9, 21, 7), HOLIDAYS), utc(2026, 9, 21, 10))
        self.assertEqual(next_off_peak_start(utc(2026, 9, 21, 23), HOLIDAYS), utc(2026, 9, 21, 23))

    def test_off_peak_seconds_remaining_collapses_at_peak_start(self):
        # 23:30 UTC Monday is off-peak until the 01:00 peak window (5400s).
        self.assertEqual(off_peak_seconds_remaining(utc(2026, 9, 21, 23, 30), HOLIDAYS), 5400)
        # 00:30 UTC Monday is off-peak until the 01:00 peak window (1800s).
        self.assertEqual(off_peak_seconds_remaining(utc(2026, 9, 21, 0, 30), HOLIDAYS), 1800)
        self.assertEqual(off_peak_seconds_remaining(utc(2026, 9, 21, 2), HOLIDAYS), 0)

    def test_beijing_display_is_eight_hours_ahead(self):
        snapshot = schedule_snapshot(utc(2026, 9, 21, 2), HOLIDAYS)
        self.assertEqual(snapshot["peak_windows_beijing"][0], "09:00-12:00")
        self.assertIn("+08:00", snapshot["now_beijing"])

    def test_builtin_calendar_marks_national_day(self):
        calendar = load_holiday_calendar(None)
        self.assertIn("2026-10-01", calendar)
        self.assertIn("2026-02-17", calendar)


class CostEstimateTest(unittest.TestCase):
    """Costs follow DeepSeek's official CNY table for deepseek-flash.

    Off-peak: 1 CNY / 1M cache-miss input, 4 CNY / 1M output, 0.02 CNY / 1M
    cache-hit input. Peak is exactly double.
    """

    def test_off_peak_is_exactly_half_of_peak(self):
        usage = {
            "prompt_tokens": 1_000_000,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
        }
        off = estimate_cost(usage, off_peak=True)
        peak = estimate_cost(usage, off_peak=False)
        self.assertEqual(off["currency"], "CNY")
        self.assertAlmostEqual(off["cost_cny"], 5.0, places=6)   # 1 + 4
        self.assertAlmostEqual(peak["cost_cny"], 10.0, places=6)  # 2 + 8

    def test_cache_hits_are_cheaper_than_misses(self):
        cheap = estimate_cost(
            {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 1000,
             "prompt_cache_miss_tokens": 0, "completion_tokens": 0},
            off_peak=True,
        )
        dear = estimate_cost(
            {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 0,
             "prompt_cache_miss_tokens": 1000, "completion_tokens": 0},
            off_peak=True,
        )
        self.assertLess(cheap["cost_cny"], dear["cost_cny"])
        self.assertAlmostEqual(cheap["cost_cny"], 0.00002, places=8)
        self.assertAlmostEqual(dear["cost_cny"], 0.001, places=8)

    def test_hosted_search_requests_are_reported(self):
        result = estimate_cost(
            {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 0,
             "prompt_cache_miss_tokens": 1000, "completion_tokens": 100,
             "server_tool_use": {"web_search_requests": 2}},
            off_peak=True,
        )
        self.assertEqual(result["web_search_requests"], 2)

    def test_pricing_snapshot_is_recorded(self):
        result = estimate_cost({"prompt_tokens": 10, "completion_tokens": 1})
        self.assertIn("pricing_snapshot", result)
        self.assertIn("api-docs.deepseek.com", result["pricing_source"])


class PromptStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = PromptStore(Path(self.temporary.name) / "prompt.json")

    def tearDown(self):
        self.temporary.cleanup()

    def test_revision_is_stored_verbatim_and_becomes_active(self):
        content = "# 我的 Prompt\n\n请按以下结构输出。\n"
        revision = self.store.create_revision(content, note="first", title="初版")
        self.assertEqual(revision["revision"], 1)
        self.assertEqual(self.store.get_revision()["content"], content)
        self.assertEqual(self.store.state()["active_revision"], 1)

    def test_identical_content_reuses_the_existing_revision(self):
        first = self.store.create_revision("same body")
        second = self.store.create_revision("same body")
        self.assertEqual(first["revision"], second["revision"])
        self.assertTrue(second["reused"])
        self.assertEqual(self.store.list_revisions()["revision_count"], 1)

    def test_activate_switches_back_to_an_older_revision(self):
        self.store.create_revision("v1")
        self.store.create_revision("v2")
        self.assertEqual(self.store.state()["active_revision"], 2)
        self.store.activate(1)
        self.assertEqual(self.store.state()["active_revision"], 1)
        self.assertEqual(self.store.get_revision()["content"], "v1")
        self.assertIsNone(self.store.activate(99))

    def test_empty_and_oversized_prompts_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create_revision("   ")
        with self.assertRaises(ValueError):
            self.store.create_revision("x" * 200001)

    def test_state_reports_unconfigured_for_a_fresh_store(self):
        self.assertEqual(
            self.store.state(),
            {"configured": False, "active_revision": None,
             "revision_count": 0, "active_prompt": None},
        )

    def test_digest_is_stable(self):
        self.assertEqual(prompt_digest("abc"), prompt_digest("abc"))
        self.assertNotEqual(prompt_digest("abc"), prompt_digest("abd"))


class ReviewJobStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ReviewJobStore(Path(self.temporary.name) / "jobs.json")

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_settings_keep_auto_review_off(self):
        settings = self.store.settings()
        self.assertFalse(settings["auto_review_enabled"])
        self.assertEqual(settings["batch_size"], 3)

    def test_batch_size_default_is_three_recent_matches(self):
        updated = self.store.update_settings({"batch_size": 3})
        self.assertEqual(updated["batch_size"], 3)

    def test_upsert_is_idempotent_for_a_finished_job(self):
        job, created = self.store.upsert_job(9001, "b9001", 1, "deepseek-flash")
        self.assertTrue(created)
        self.store.mark(job["job_id"], JOB_DONE, output_path="/tmp/x.json")
        again, created_again = self.store.upsert_job(9001, "b9001", 1, "deepseek-flash")
        self.assertFalse(created_again)
        self.assertEqual(again["status"], JOB_DONE)

    def test_pending_orders_by_priority_then_recency(self):
        self.store.upsert_job(100, "b", 1, "m", priority=100)
        self.store.upsert_job(200, "b", 1, "m", priority=100)
        self.store.upsert_job(50, "b", 1, "m", priority=10)
        pending = self.store.pending()
        self.assertEqual([job["match_id"] for job in pending], [50, 200, 100])

    def test_paused_jobs_are_still_pending_work(self):
        job, _ = self.store.upsert_job(300, "b", 1, "m")
        self.store.mark(job["job_id"], JOB_PAUSED, error="peak")
        self.assertEqual([item["job_id"] for item in self.store.pending()], [job["job_id"]])
        self.assertEqual(self.store.get(job["job_id"])["status"], JOB_PAUSED)

    def test_failed_jobs_leave_the_queue(self):
        job, _ = self.store.upsert_job(400, "b", 1, "m")
        self.store.mark(job["job_id"], "failed", error="nope")
        self.assertEqual(self.store.pending(), [])

    def test_attempts_increment_only_when_asked(self):
        job, _ = self.store.upsert_job(500, "b", 1, "m")
        self.store.mark(job["job_id"], JOB_PENDING, error="retry")
        self.assertEqual(self.store.get(job["job_id"])["attempts"], 0)
        self.store.mark(job["job_id"], "running", increment_attempts=True)
        self.assertEqual(self.store.get(job["job_id"])["attempts"], 1)

    def test_stats_accumulate_cost_history(self):
        job, _ = self.store.upsert_job(600, "b", 1, "m")
        self.store.mark(job["job_id"], JOB_DONE, cost={"cost_cny": 0.25, "search_cost_cny": 0.05})
        self.store.mark(job["job_id"], JOB_DONE, cost={"cost_cny": 0.5, "search_cost_cny": 0.1})
        stats = self.store.stats()
        self.assertEqual(stats["total_jobs"], 1)
        self.assertEqual(stats["currency"], "CNY")
        self.assertAlmostEqual(stats["model_cost_cny"], 0.75)
        self.assertAlmostEqual(stats["search_cost_cny"], 0.15)
        self.assertAlmostEqual(stats["estimated_total_cny"], 0.9)

    def test_settings_clamp_out_of_range_values(self):
        updated = self.store.update_settings({
            "batch_size": 99,
            "temperature": 5,
            "max_output_tokens": 10,
            "unknown_key": "ignored",
        })
        self.assertEqual(updated["batch_size"], 10)
        self.assertEqual(updated["temperature"], 2.0)
        self.assertEqual(updated["max_output_tokens"], 512)
        self.assertNotIn("unknown_key", updated)


class DeepSeekClientWireFormatTest(unittest.TestCase):
    """Verify the exact HTTP payload DeepSeek receives and how we parse replies."""

    def setUp(self):
        import httpx

        self.httpx = httpx
        self._original_post = httpx.post
        self.calls = []

    def tearDown(self):
        self.httpx.post = self._original_post

    def _stub(self, payload, status_code=200):
        # Encode at module scope: a method named `json` inside the stub response
        # class would shadow the stdlib module for the class body.
        encoded = json.dumps(payload)

        class Response:
            def __init__(self):
                self.status_code = status_code
                self.text = encoded

            def json(self):
                return payload

        def post(url, headers=None, json=None, timeout=None):
            # Capture under a different name so the stdlib `json` module is not
            # shadowed inside this helper.
            self.calls.append(
                {"url": url, "headers": headers, "json": json, "timeout": timeout}
            )
            return Response()

        self.httpx.post = post

    def test_plain_chat_uses_the_official_endpoint_and_parses_usage(self):
        from deepseek_review import DeepSeekClient

        self._stub({
            "id": "c1",
            "model": "deepseek-flash",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "答案", "reasoning_content": "推理"},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20,
            },
        })
        client = DeepSeekClient("sk-test", base_url="https://api.deepseek.com")
        result = client.chat([{"role": "user", "content": "hi"}], max_tokens=1234, temperature=0.3)
        self.assertEqual(self.calls[0]["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(self.calls[0]["headers"]["Authorization"], "Bearer sk-test")
        body = self.calls[0]["json"]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["max_tokens"], 1234)
        self.assertFalse(body["stream"])
        self.assertNotIn("tools", body)
        self.assertEqual(result["content"], "答案")
        self.assertEqual(result["reasoning_content"], "推理")
        self.assertEqual(result["usage"]["prompt_cache_hit_tokens"], 80)

    def test_tools_and_thinking_are_sent_and_tool_calls_are_parsed(self):
        from deepseek_review import DeepSeekClient

        self._stub({
            "id": "c2",
            "model": "deepseek-flash",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_9",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query":"7.41 幻象"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        client = DeepSeekClient("sk-test")
        result = client.chat(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}],
            thinking="enabled",
        )
        body = self.calls[0]["json"]
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "web_search")
        self.assertEqual(result["content"], "")
        self.assertEqual(result["finish_reason"], "tool_calls")

    def test_http_error_becomes_a_request_error_with_the_detail(self):
        from deepseek_review import DeepSeekClient, DeepSeekRequestError

        self._stub({"error": {"message": "Insufficient Balance"}}, status_code=402)
        client = DeepSeekClient("sk-test")
        with self.assertRaises(DeepSeekRequestError) as caught:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("402", str(caught.exception))
        self.assertIn("Insufficient Balance", str(caught.exception))

    def test_empty_key_is_refused_before_any_request(self):
        from deepseek_review import DeepSeekClient, DeepSeekConfigError

        with self.assertRaises(DeepSeekConfigError):
            DeepSeekClient("   ")
        self.assertEqual(self.calls, [])

    def test_api_key_file_permissions_are_enforced(self):
        import tempfile
        from pathlib import Path as _Path
        from deepseek_review import DeepSeekConfigError, read_api_key

        with tempfile.TemporaryDirectory() as directory:
            path = _Path(directory) / "deepseek-api-key"
            path.write_text("sk-ok", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(read_api_key(path), "sk-ok")
            path.chmod(0o644)
            with self.assertRaises(DeepSeekConfigError) as caught:
                read_api_key(path)
            self.assertIn("permissions must be 600", str(caught.exception))
            path.unlink()
            with self.assertRaises(DeepSeekConfigError):
                read_api_key(path)

    def test_placeholder_key_with_chinese_characters_is_rejected(self):
        """A key file holding the Chinese placeholder must fail loudly.

        This exact mistake happened in production: the deployment snippet in the
        docs contained that placeholder and it was executed verbatim, so every
        hosted search died with an opaque ascii codec error mid-request.
        """
        import tempfile
        from pathlib import Path as _Path
        from deepseek_review import DeepSeekConfigError, read_api_key

        with tempfile.TemporaryDirectory() as directory:
            path = _Path(directory) / "deepseek-api-key"
            path.write_text("sk-\u4f60\u7684key", encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaises(DeepSeekConfigError) as caught:
                read_api_key(path)
            message = str(caught.exception)
            self.assertIn("non-ASCII", message)
            self.assertIn("placeholder", message)

    def test_key_file_with_a_utf8_bom_is_rejected(self):
        import tempfile
        from pathlib import Path as _Path
        from deepseek_review import DeepSeekConfigError, read_api_key

        with tempfile.TemporaryDirectory() as directory:
            path = _Path(directory) / "deepseek-api-key"
            path.write_bytes(b"\xef\xbb\xbfsk-real-key")
            path.chmod(0o600)
            with self.assertRaises(DeepSeekConfigError):
                read_api_key(path)

    def test_api_key_problem_detects_the_common_mistakes(self):
        from deepseek_review import api_key_problem

        self.assertIsNone(api_key_problem("sk-0123456789abcdef0123456789abcdef"))
        self.assertIn("non-ASCII", api_key_problem("sk-\u4f60\u7684key"))
        self.assertIn("non-ASCII", api_key_problem("sk-\ufeffabc"))
        self.assertIn("embedded whitespace", api_key_problem("sk-abc def"))
        self.assertIn("does not start", api_key_problem("c39edc811d6a4517"))
        self.assertIn("empty", api_key_problem(""))


if __name__ == "__main__":
    unittest.main()
