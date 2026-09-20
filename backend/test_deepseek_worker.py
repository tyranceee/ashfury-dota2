"""Unit tests for the off-peak DeepSeek review worker.

The tests replace the DeepSeek HTTP client with a stub, so no network access and
no API key are required.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))

import deepseek_review  # noqa: E402
from deepseek_review import (  # noqa: E402
    JOB_DONE,
    JOB_FAILED,
    JOB_PAUSED,
    JOB_PENDING,
    JOB_RUNNING,
    DeepSeekConfigError,
    DeepSeekRequestError,
    PromptStore,
    ReviewJobStore,
)
from deepseek_worker import (  # noqa: E402
    MIN_RUNWAY_SECONDS,
    DeepSeekReviewWorker,
    artifact_timestamp,
    build_review_messages,
    match_compact_json,
)

HOLIDAYS = {"2026-10-01"}


def utc(year, month, day, hour, minute=0, second=0):
    return dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.timezone.utc)


def sample_match(match_id=9001, hero="Naga Siren", win=True):
    return {
        "match_id": match_id,
        "hero_name": hero,
        "start_time": 1789831103,
        "duration": 2735,
        "win": win,
        "kda": "9/1/10",
        "parse_status": "parsed",
        "parsed": True,
    }


class StubClient:
    """Records the request and returns a canned DeepSeek response."""

    calls = []
    behavior = "ok"
    tool_call_script = []

    def __init__(self, api_key, base_url=None, model="deepseek-flash", timeout_seconds=900):
        if not api_key:
            raise DeepSeekConfigError("DeepSeek API key is not configured")
        self.api_key = api_key
        self.model = model

    def chat(
        self,
        messages,
        max_tokens=32000,
        temperature=0.2,
        json_mode=False,
        tools=None,
        tool_choice=None,
        thinking=None,
        user_id=None,
    ):
        StubClient.calls.append({
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "model": self.model,
            "tools": tools,
            "thinking": thinking,
            "user_id": user_id,
        })
        if StubClient.behavior == "network_error":
            raise DeepSeekRequestError("DeepSeek HTTP 500: upstream failure")
        if StubClient.behavior == "config_error":
            raise DeepSeekConfigError("DeepSeek API key file is missing")
        usage = {
            "prompt_tokens": 120000,
            "completion_tokens": 4000,
            "total_tokens": 124000,
            "prompt_cache_hit_tokens": 100000,
            "prompt_cache_miss_tokens": 20000,
        }
        # Replay any scripted tool calls for this round trip.
        index = len(StubClient.calls) - 1
        if index < len(StubClient.tool_call_script):
            return {
                "content": "",
                "reasoning_content": "",
                "tool_calls": StubClient.tool_call_script[index],
                "finish_reason": "tool_calls",
                "usage": usage,
                "model": self.model,
                "response_id": f"resp_tool_{index}",
            }
        return {
            "content": "# 一句话结论\n\n本场节奏由 Roshan 窗口决定。\n",
            "reasoning_content": "",
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": usage,
            "model": self.model,
            "response_id": "resp_test_1",
        }


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        StubClient.calls = []
        StubClient.behavior = "ok"
        StubClient.tool_call_script = []
        self._original_client = deepseek_review.DeepSeekClient
        deepseek_review.DeepSeekClient = StubClient

        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        self.prompts = PromptStore(root / "prompt.json")
        self.jobs = ReviewJobStore(root / "jobs.json")
        self.key_path = root / "deepseek-api-key"
        self.key_path.write_text("sk-test-value", encoding="utf-8")
        self.key_path.chmod(0o600)

        self.index = {
            str(9001): {"match_id": 9001, "start_time": 100, "parsed": True},
            str(9002): {"match_id": 9002, "start_time": 90, "parsed": True},
            str(9003): {"match_id": 9003, "start_time": 80, "parsed": True},
            str(9004): {"match_id": 9004, "start_time": 70, "parsed": True},
            str(9005): {"match_id": 9005, "start_time": 60, "parsed": False},
        }
        self.matches = {
            match_id: {"match_id": match_id, "players": [], "teamfights": []}
            for match_id in (9001, 9002, 9003, 9004)
        }
        self.now = utc(2026, 9, 21, 20)  # Monday 20:00 UTC -> off-peak
        self.worker = DeepSeekReviewWorker(
            job_store=self.jobs,
            prompt_store=self.prompts,
            output_dir=root / "outputs",
            api_key_path=self.key_path,
            index_loader=lambda: self.index,
            match_loader=lambda match_id: self.matches.get(int(match_id)),
            match_summary=lambda item: {
                "match_id": item["match_id"],
                "hero_name": "Naga Siren",
                "win": True,
                "kda": "9/1/10",
                "duration": "45:35",
                "parse_status": "parsed",
            },
            sanitizer=lambda value: value,
            holiday_calendar_path=None,
            now_provider=lambda: int(self.now.timestamp()),
        )

    def tearDown(self):
        deepseek_review.DeepSeekClient = self._original_client
        self.temporary.cleanup()

    # ----- prompt and schedule gating ---------------------------------

    def test_disabled_auto_review_never_enqueues_work(self):
        self.prompts.create_revision("# prompt")
        result = self.worker.tick()
        self.assertEqual(result["action"], "disabled")
        self.assertEqual(self.jobs.pending(), [])
        self.assertEqual(StubClient.calls, [])

    def test_enabled_without_prompt_enqueues_nothing(self):
        self.jobs.update_settings({"auto_review_enabled": True})
        result = self.worker.tick()
        self.assertEqual(result["action"], "idle")
        self.assertEqual(self.jobs.pending(), [])
        self.assertEqual(StubClient.calls, [])

    def test_peak_hours_defer_all_work(self):
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({"auto_review_enabled": True})
        self.now = utc(2026, 9, 21, 7)  # Monday peak window
        result = self.worker.tick()
        self.assertEqual(result["action"], "waiting")
        self.assertEqual(result["resume_at"], int(utc(2026, 9, 21, 10).timestamp()))
        self.assertEqual(StubClient.calls, [])

    def test_short_off_peak_runway_defers_the_start(self):
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({"auto_review_enabled": True})
        # 00:55 UTC Monday leaves only 300s before the 01:00 peak window, which
        # is below the runway, so nothing may be started.
        self.now = utc(2026, 9, 21, 0, 55)
        self.assertLess(
            int((utc(2026, 9, 21, 1) - self.now).total_seconds()), MIN_RUNWAY_SECONDS
        )
        result = self.worker.tick()
        self.assertEqual(result["action"], "deferring")
        self.assertEqual(result["resume_at"], int(utc(2026, 9, 21, 4).timestamp()))
        self.assertEqual(StubClient.calls, [])
        # The batch is registered but must not have been executed.
        self.assertEqual(len(self.jobs.pending()), 3)
        self.assertTrue(all(job["status"] == JOB_PENDING for job in self.jobs.pending()))

    def test_enough_runway_lets_the_same_job_start(self):
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({"auto_review_enabled": True, "batch_size": 1})
        # 00:30 UTC Monday leaves 1800s before the 01:00 peak: enough to start.
        self.now = utc(2026, 9, 21, 0, 30)
        self.assertGreater(
            int((utc(2026, 9, 21, 1) - self.now).total_seconds()), MIN_RUNWAY_SECONDS
        )
        result = self.worker.tick()
        self.assertEqual(result["action"], "processed")
        self.assertEqual(len(StubClient.calls), 1)

    # ----- enqueue scope ----------------------------------------------

    def test_only_the_three_most_recent_parsed_matches_are_queued(self):
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({"auto_review_enabled": True, "batch_size": 3})
        enqueued = self.worker.enqueue_recent_parsed()
        self.assertEqual(enqueued, [9001, 9002, 9003])
        self.assertNotIn(9004, enqueued)
        self.assertNotIn(9005, enqueued)  # unparsed matches are never queued

    def test_already_reviewed_matches_are_not_requeued(self):
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({"auto_review_enabled": True, "batch_size": 3})
        job, _ = self.jobs.upsert_job(9001, "b9001", 1, "deepseek-flash")
        self.jobs.mark(job["job_id"], JOB_DONE, output_path="/tmp/9001.json")
        # 9001 keeps a slot inside the recency window, so auto review still only
        # looks at the three most recent parsed matches.
        self.assertEqual(self.worker.enqueue_recent_parsed(), [9002, 9003])

    # ----- execution ---------------------------------------------------

    def test_successful_run_writes_downloadable_artifacts(self):
        self.prompts.create_revision("# 初步解析 Prompt\n\n按结构输出。")
        self.jobs.update_settings({"auto_review_enabled": True, "batch_size": 3})
        result = self.worker.tick()
        self.assertEqual(result["action"], "processed")
        self.assertEqual(len(result["jobs"]), 3)

        job = self.jobs.get(self.jobs.job_id(9001))
        self.assertEqual(job["status"], JOB_DONE)
        self.assertIsNotNone(job["cost"])
        self.assertEqual(job["cost"]["billing_window"], "off_peak")

        json_path = self.worker.output_paths(9001)["json"]
        markdown_path = self.worker.output_paths(9001)["markdown"]
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())

        payload = json.loads(json_path.read_text("utf-8"))
        self.assertEqual(payload["match_id"], 9001)
        self.assertEqual(payload["prompt"]["revision"], 1)
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertIn("一句话结论", payload["content_markdown"])
        self.assertEqual(payload["usage"]["prompt_cache_hit_tokens"], 100000)
        self.assertGreater(payload["cost"]["estimated_usd"], 0)

        markdown = markdown_path.read_text("utf-8")
        self.assertIn("比赛 9001 初步解析", markdown)
        self.assertIn("初步解析", markdown)

    def test_request_carries_the_prompt_and_sanitized_match_json(self):
        self.prompts.create_revision("# 专属 Prompt 标记 ABC")
        self.jobs.update_settings({"auto_review_enabled": True, "batch_size": 1})
        self.worker.tick()
        messages = StubClient.calls[0]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("专属 Prompt 标记 ABC", messages[1]["content"])
        self.assertIn('"match_id": 9001', messages[1]["content"])
        self.assertIn("无法确认", messages[0]["content"])

    def test_companion_matches_are_labelled_as_comparison_only(self):
        self.prompts.create_revision("# prompt")
        messages = build_review_messages(
            prompt_revision={"revision": 1, "sha256": "x", "content": "# prompt"},
            match_data=self.matches[9001],
            sanitized_match=self.matches[9001],
            recent_matches=[sample_match()],
            companion_matches=[
                {**sample_match(9002), "json": {"match_id": 9002}},
            ],
        )
        self.assertIn("同批比赛 9002", messages[1]["content"])
        self.assertIn("不要逐场写报告", messages[1]["content"])

    def test_network_error_retries_then_fails_after_five_attempts(self):
        StubClient.behavior = "network_error"
        self.prompts.create_revision("# prompt")
        job, _ = self.jobs.upsert_job(9001, "b9001", 1, "deepseek-flash")
        outcome = self.worker.run_job(job["job_id"])
        self.assertEqual(outcome["status"], "retry_scheduled")
        self.assertEqual(self.jobs.get(job["job_id"])["status"], JOB_PENDING)

        for _ in range(4):
            outcome = self.worker.run_job(job["job_id"])
        self.assertEqual(outcome["status"], JOB_FAILED)
        self.assertEqual(self.jobs.get(job["job_id"])["status"], JOB_FAILED)
        self.assertEqual(self.jobs.pending(), [])

    def test_missing_api_key_fails_the_job_without_calling_deepseek(self):
        self.key_path.unlink()
        self.prompts.create_revision("# prompt")
        job, _ = self.jobs.upsert_job(9001, "b9001", 1, "deepseek-flash")
        outcome = self.worker.run_job(job["job_id"])
        self.assertEqual(outcome["status"], JOB_FAILED)
        self.assertIn("missing", self.jobs.get(job["job_id"])["last_error"])
        self.assertEqual(StubClient.calls, [])

    def test_missing_parsed_json_fails_the_job(self):
        self.prompts.create_revision("# prompt")
        job, _ = self.jobs.upsert_job(9005, "b9005", 1, "deepseek-flash")
        outcome = self.worker.run_job(job["job_id"])
        self.assertEqual(outcome["status"], JOB_FAILED)
        self.assertEqual(StubClient.calls, [])

    def test_missing_prompt_revision_fails_the_job(self):
        job, _ = self.jobs.upsert_job(9001, "b9001", 7, "deepseek-flash")
        outcome = self.worker.run_job(job["job_id"])
        self.assertEqual(outcome["status"], JOB_FAILED)

    def test_peak_starting_mid_cycle_pauses_the_remaining_jobs(self):
        """A window that closes while a request is in flight pauses the rest."""
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({"auto_review_enabled": True, "batch_size": 3})
        self.worker.enqueue_recent_parsed()

        started = threading.Event()
        original_chat = StubClient.chat

        def gated_chat(inner_self, *args, **kwargs):
            started.set()
            # The first request runs while the window is still open...
            result = original_chat(inner_self, *args, **kwargs)
            # ...and by the time it returns, only 240s remain before the 01:00
            # peak window: too short to start the next job.
            self.now = utc(2026, 9, 21, 0, 56)
            return result

        StubClient.chat = gated_chat
        self.now = utc(2026, 9, 21, 0, 30)
        try:
            cycle = self.worker.tick()
        finally:
            StubClient.chat = original_chat

        self.assertTrue(started.is_set(), "the first DeepSeek call never started")
        self.assertEqual(len(StubClient.calls), 1)
        statuses = {job["match_id"]: job["status"] for job in self.jobs.list_jobs()}
        self.assertEqual(statuses.get(9001), JOB_DONE)
        # The job the loop was about to start is explicitly parked; anything
        # behind it simply stays queued. Neither may have run.
        self.assertIn(statuses.get(9002), {JOB_PAUSED, JOB_PENDING})
        self.assertIn(statuses.get(9003), {JOB_PAUSED, JOB_PENDING})
        self.assertEqual(cycle.get("action"), "processed")

    def test_worker_does_not_start_a_second_cycle_while_busy(self):
        self.worker._work_lock.acquire()
        try:
            result = self.worker.tick()
            self.assertEqual(result["action"], "busy")
        finally:
            self.worker._work_lock.release()

    # ----- status ------------------------------------------------------

    def test_status_reports_schedule_and_key_state(self):
        status = self.worker.status()
        self.assertFalse(status["auto_review_enabled"])
        self.assertTrue(status["deepseek_key_configured"])
        self.assertFalse(status["prompt"]["configured"])
        self.assertIn("off_peak_now", status["schedule"])
        self.assertEqual(status["batch_size"], 3)

    def test_output_readers_return_none_before_generation(self):
        self.assertIsNone(self.worker.read_output(9999))

    def test_artifact_timestamp_uses_an_explicit_timezone(self):
        # 2026-09-20 13:06:30 UTC must be reported as 21:06:30 in Beijing.
        epoch = int(dt.datetime(2026, 9, 20, 13, 6, 30, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(artifact_timestamp(epoch), "2026-09-20 21:06:30")

    def test_markdown_artifact_labels_its_timezone_and_usage(self):
        import deepseek_worker as worker_module

        payload = {
            "match_id": 9005766439,
            "model": "deepseek-flash",
            "billing_window": "off_peak",
            "generated_at": int(
                dt.datetime(2026, 9, 20, 13, 6, 30, tzinfo=dt.timezone.utc).timestamp()
            ),
            "prompt": {"revision": 1},
            "usage": {"total_tokens": 475369},
            "content_markdown": "# 一句话结论\n",
        }
        markdown = worker_module.DeepSeekReviewWorker._markdown_artifact(payload)
        self.assertIn("生成时间：2026-09-20 21:06:30（北京时间）", markdown)
        self.assertIn("475369", markdown)
        self.assertNotIn("本机时区", markdown)

    # ----- compact companion context ----------------------------------

    @staticmethod
    def bulky_match(match_id):
        return {
            "match_id": match_id,
            "start_time": 1789831103,
            "duration": 2735,
            "radiant_win": True,
            "gold_adv": list(range(2000)),
            "objectives": [{"time": t, "type": "CHAT_MESSAGE", "key": "x" * 50} for t in range(200)],
            "teamfights": [{
                "start": 900, "end": 945, "deaths": 4,
                "players": [{"player_slot": s, "deaths": 1, "damage": 100, "gold_delta": 50,
                             "xp_delta": 30, "buybacks": 0} for s in range(10)],
            }],
            "players": [
                {"account_id": 212121467, "player_slot": 0, "hero_id": 89, "kills": 9,
                 "deaths": 1, "assists": 10, "net_worth": 34935, "gold_per_min": 769,
                 "hero_damage": 62520, "tower_damage": 7663,
                 "teamfight_participation": 0.5135, "personaname": "leaky"},
                *[{"account_id": 1000 + s, "player_slot": s, "hero_id": s, "kills": 1}
                  for s in range(1, 10)],
            ],
            "radiant_gold_adv": list(range(3000)),
            "radiant_xp_adv": list(range(3000)),
        }

    def test_compact_companion_json_is_much_smaller_but_keeps_the_facts(self):
        full = self.bulky_match(9001)
        compact = match_compact_json(full, 212121467)
        full_size = len(json.dumps(full, ensure_ascii=False))
        compact_size = len(json.dumps(compact, ensure_ascii=False))
        self.assertLess(compact_size, full_size * 0.35)
        self.assertEqual(compact["own_player"]["hero_id"], 89)
        self.assertEqual(compact["own_player"]["gold_per_min"], 769)
        self.assertEqual(compact["own_player"]["teamfight_participation"], 0.5135)
        self.assertEqual(len(compact["scoreboard"]), 10)
        self.assertEqual(len(compact["teamfights"]), 1)
        self.assertLessEqual(len(compact["objectives"]), 120)
        # Player names must not survive into the comparison payload.
        self.assertNotIn("personaname", json.dumps(compact, ensure_ascii=False))

    def test_teamfight_detail_uses_the_owner_index_when_slots_are_absent(self):
        """Real parsed payloads have positional teamfights with no player_slot."""
        match = {
            "match_id": 9006610271,
            "duration": 2735,
            "players": [
                {"account_id": 111, "player_slot": 0, "hero_id": 108},
                {"account_id": 222, "player_slot": 1, "hero_id": 20},
                {"account_id": 212121467, "player_slot": 2, "hero_id": 89},
            ],
            "teamfights": [{
                "start": 354,
                "end": 396,
                "deaths": 4,
                "players": [
                    {"damage": 970, "ability_uses": {"some_other_hero_spell": 1}},
                    {"damage": 500, "ability_uses": {"another_spell": 1}},
                    {"damage": 0, "ability_uses": {"naga_siren_mirror_image": 1},
                     "item_uses": {"madstone_bundle": 1}, "gold_delta": 851},
                ],
            }],
        }
        compact = match_compact_json(match, 212121467)
        detail = compact["own_player"]["teamfight_detail"]
        self.assertEqual(len(detail), 1)
        # Index 2 is the owner, so the third entry must be selected -- not the
        # first entry and not an empty list.
        self.assertEqual(detail[0]["damage"], 0)
        self.assertEqual(detail[0]["ability_uses"], {"naga_siren_mirror_image": 1})
        self.assertEqual(detail[0]["gold_delta"], 851)
        self.assertEqual(detail[0]["fight_start"], 354)
        self.assertEqual(len(compact["teamfights"][0]["owner_detail"]), 1)
        self.assertEqual(compact["teamfights"][0]["owner_detail"][0]["damage"], 0)
        # Other players' ability maps are not carried.
        self.assertNotIn("some_other_hero_spell", json.dumps(compact, ensure_ascii=False))

    def test_teamfight_detail_prefers_player_slot_when_present(self):
        match = {
            "match_id": 1,
            "players": [
                {"account_id": 111, "player_slot": 0, "hero_id": 1},
                {"account_id": 212121467, "player_slot": 128, "hero_id": 89},
            ],
            "teamfights": [{
                "start": 100, "end": 140, "deaths": 2,
                "players": [
                    {"player_slot": 0, "damage": 111},
                    {"player_slot": 128, "damage": 999},
                ],
            }],
        }
        compact = match_compact_json(match, 212121467)
        self.assertEqual(compact["own_player"]["teamfight_detail"][0]["damage"], 999)

    def test_compact_json_handles_a_missing_own_row(self):
        compact = match_compact_json(self.bulky_match(9002), 999999)
        self.assertIsNone(compact["own_player"])
        self.assertEqual(len(compact["scoreboard"]), 10)

    def test_compact_json_tolerates_missing_sections(self):
        compact = match_compact_json({"match_id": 9003}, 212121467)
        self.assertIsNone(compact["own_player"])
        self.assertEqual(compact["scoreboard"], [])
        self.assertEqual(compact["objectives"], [])
        self.assertEqual(compact["teamfights"], [])
        self.assertEqual(compact["radiant_gold_adv_sample"], [])

    def test_economy_series_is_sampled_not_dropped(self):
        compact = match_compact_json(self.bulky_match(9004), 212121467)
        self.assertGreater(len(compact["radiant_gold_adv_sample"]), 0)
        self.assertLessEqual(len(compact["radiant_gold_adv_sample"]), 13)

    def test_default_settings_use_compact_companions(self):
        settings = self.jobs.settings()
        self.assertEqual(settings["companion_detail"], "compact")
        self.assertEqual(settings["context_budget_chars"], 700000)

    def test_campaign_sends_compact_companions_by_default(self):
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({"auto_review_enabled": True, "batch_size": 3})
        self.worker.tick()
        prompt = StubClient.calls[0]["messages"][1]["content"]
        self.assertIn("同批比赛", prompt)

    def test_companions_are_pruned_when_over_the_budget(self):
        self.prompts.create_revision("# prompt")
        self.jobs.update_settings({
            "auto_review_enabled": True,
            "batch_size": 1,
            "companion_detail": "full",
            "context_budget_chars": 100000,
        })
        # The target match alone blows the budget, so every companion must go
        # while the target itself is still analysed.
        bulky = self.bulky_match(9001)
        bulky["objective_notes"] = "x" * 200000
        self.matches[9001] = bulky
        owned = self.worker.read_output(9001)
        self.worker.tick()
        payload = self.worker.read_output(9001)
        self.assertIsNotNone(payload, f"no output written (previous={owned})")
        self.assertEqual(payload["context"]["companions_included"], 0)
        self.assertEqual(payload["context"]["companions_pruned_for_budget"], 2)
        self.assertFalse(payload["context"]["target_match_truncated"])
        self.assertGreater(payload["context"]["prompt_chars"], 200000)


if __name__ == "__main__":
    unittest.main()
