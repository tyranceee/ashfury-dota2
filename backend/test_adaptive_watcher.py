import unittest
import tempfile
from pathlib import Path

import adaptive_watcher as module


class FakeResponse(object):
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class FakeHttpClient(object):
    def __init__(self, body):
        self.body = body

    def get(self, *args, **kwargs):
        return FakeResponse(self.body)


class AdaptiveWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        module.STATE_FILE = root / "state.json"
        module.INDEX_FILE = root / "index.json"
        module.DATA_DIR = root / "data"
        module.ARTIFACTS_DIR = root / "artifacts"

    def tearDown(self):
        self.temp.cleanup()

    def test_presence_requires_app_id_570(self):
        online = module.SteamPresenceClient(
            "key",
            FakeHttpClient({"response": {"players": [{"gameid": "570"}]}}),
        )
        other = module.SteamPresenceClient(
            "key",
            FakeHttpClient({"response": {"players": [{"gameid": "730"}]}}),
        )
        self.assertTrue(online.is_dota_online())
        self.assertFalse(other.is_dota_online())

    def test_match_ids_use_integer_comparison(self):
        self.assertTrue(module.is_newer_match("9007199254740993", "9007199254740992"))
        self.assertFalse(module.is_newer_match("9007199254740992", "9007199254740993"))

    def test_summary_preserves_parse_request(self):
        summary = {"match_id": 123, "hero_id": 92}
        old = {
            "parse_requested": True,
            "parse_requested_at": 100,
            "parse_job_id": 55,
        }
        result = module.build_summary(summary, old)
        self.assertTrue(result["parse_requested"])
        self.assertEqual(result["parse_job_id"], 55)

    def test_parse_detection_requires_ten_players_and_parse_arrays(self):
        parsed = {
            "players": [{} for _ in range(10)],
            "version": 22,
            "teamfights": [{}],
            "objectives": [{}],
            "radiant_gold_adv": [0],
            "radiant_xp_adv": [0],
        }
        self.assertTrue(module.is_parsed(parsed))
        parsed["players"] = [{}]
        self.assertFalse(module.is_parsed(parsed))

    def test_offline_checks_latest_match_on_ten_minute_fallback(self):
        class Presence(object):
            calls = 0

            def is_dota_online(self):
                self.calls += 1
                return False

        class OpenDota(object):
            latest_calls = 0

            def latest_match(self):
                self.latest_calls += 1

        now = [0]
        presence = Presence()
        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(presence, opendota, clock=lambda: now[0])
        watcher.run_due()
        now[0] = 599
        watcher.run_due()
        now[0] = 600
        watcher.run_due()

        self.assertEqual(presence.calls, 2)
        self.assertEqual(opendota.latest_calls, 2)

    def test_online_checks_latest_match_every_poll_interval(self):
        class Presence(object):
            calls = 0

            def is_dota_online(self):
                self.calls += 1
                return True

        class OpenDota(object):
            latest_calls = 0

            def latest_match(self):
                self.latest_calls += 1
                return {"match_id": 100}

        now = [0]
        presence = Presence()
        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(presence, opendota, clock=lambda: now[0])
        watcher.run_due()
        now[0] = module.RESULT_INTERVAL - 1
        watcher.run_due()
        now[0] = module.RESULT_INTERVAL
        watcher.run_due()

        self.assertEqual(presence.calls, 1)
        self.assertEqual(opendota.latest_calls, 2)

    def test_existing_index_seeds_baseline_before_first_online_poll(self):
        module.save_json_atomic(module.INDEX_FILE, {
            "100": {"match_id": 100, "start_time": 1000},
            "101": {"match_id": 101, "start_time": 2000},
        })

        class Presence(object):
            def is_dota_online(self):
                return False

        watcher = module.AdaptiveWatcher(Presence(), object(), clock=lambda: 0)
        self.assertEqual(watcher.state["last_processed_match_id"], "101")

    def test_new_match_waits_one_hour_before_opendota_request(self):
        class Presence(object):
            def is_dota_online(self):
                return True

        class OpenDota(object):
            latest_ids = [100, 101, 101]
            parse_calls = 0

            def latest_match(self):
                return {"match_id": self.latest_ids.pop(0), "start_time": 1}

            def recent_matches(self, limit):
                return [{"match_id": 101, "start_time": 1}]

            def match(self, match_id):
                return {"players": []}

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"jobId": 55}

        now = [0]
        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        watcher.run_due()
        now[0] = module.RESULT_INTERVAL
        watcher.run_due()
        now[0] = module.RESULT_INTERVAL * 2
        watcher.run_due()

        self.assertEqual(opendota.parse_calls, 0)
        index = module.load_json(module.INDEX_FILE, {})
        self.assertEqual(index["101"]["parse_status"], "waiting_local")

        # The one-hour grace window starts when the match is detected, which is
        # the last polling cycle above, not time zero.
        discovered_at = int(
            module.load_json(module.INDEX_FILE, {})["101"]["parse_discovered_at"]
        )

        now[0] = discovered_at + module.LOCAL_PARSE_GRACE_SECONDS - 1
        watcher.check_pending_parse()
        self.assertEqual(opendota.parse_calls, 0, "宽限期内不应提交 OpenDota 解析")

        now[0] = discovered_at + module.LOCAL_PARSE_GRACE_SECONDS
        watcher.check_pending_parse()
        self.assertEqual(opendota.parse_calls, 1)
        index = module.load_json(module.INDEX_FILE, {})
        self.assertEqual(index["101"]["parse_job_id"], 55)

    def test_local_result_prevents_opendota_request(self):
        now = [0]
        module.save_json_atomic(module.INDEX_FILE, {
            "101": {
                "match_id": 101,
                "start_time": 1,
                "parsed": False,
                "parse_status": "waiting_local",
                "parse_discovered_at": 0,
                "parse_requested": False,
            }
        })
        directory = module.ARTIFACTS_DIR / "101"
        directory.mkdir(parents=True)
        for filename in module.LOCAL_PARSE_FILES:
            (directory / filename).write_text("{}", encoding="utf-8")

        class Presence(object):
            def is_dota_online(self):
                return False

        class OpenDota(object):
            parse_calls = 0
            match_calls = 0

            def match(self, match_id):
                self.match_calls += 1
                return {"players": []}

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"jobId": 55}

        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        watcher.check_pending_parse()
        index = module.load_json(module.INDEX_FILE, {})

        self.assertTrue(index["101"]["parsed"])
        self.assertEqual(index["101"]["parse_source"], "dota_replay_desk")
        self.assertEqual(opendota.match_calls, 0)
        self.assertEqual(opendota.parse_calls, 0)

    def test_submitted_parse_request_is_never_submitted_twice(self):
        """A request that is still fresh is left alone (original policy)."""
        now = [100000]
        module.save_json_atomic(module.INDEX_FILE, {
            "101": {
                "match_id": 101,
                "start_time": 1,
                "parsed": False,
                "parse_status": "requested",
                "parse_discovered_at": 1,
                "parse_requested": True,
                "parse_requested_at": now[0] - 600,
            }
        })

        class Presence(object):
            def is_dota_online(self):
                return False

        class OpenDota(object):
            def __init__(self):
                self.parse_calls = 0

            def match(self, match_id):
                return {"players": []}

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"jobId": 99}

            def parse_job(self, match_id):
                return {"jobId": 99}

        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        watcher.check_pending_parse()
        now[0] += 600
        watcher.check_pending_parse()

        self.assertEqual(opendota.parse_calls, 0)
        index = module.load_json(module.INDEX_FILE, {})
        self.assertEqual(index["101"]["parse_status"], "waiting")
        self.assertNotIn("parse_request_attempts", index["101"])

    def test_stale_parse_request_is_resubmitted(self):
        """Regression: a parse job that vanishes from OpenDota must be retried.

        Match 9008030503 sat in 'waiting' for over 11 hours because the queued
        job id had disappeared (GET /request/<id> returns null) while the entry
        was trusted to have been "requested once, forever".
        """
        now = [100000]
        module.save_json_atomic(module.INDEX_FILE, {
            "9008030503": {
                "match_id": 9008030503,
                "start_time": 1,
                "parsed": False,
                "parse_status": "waiting",
                "parse_discovered_at": 1,
                "parse_requested": True,
                "parse_requested_at": now[0] - module.STALE_PARSE_REQUEST_SECONDS - 60,
                "parse_job_id": 537301221,
            }
        })

        class Presence(object):
            def is_dota_online(self):
                return False

        class OpenDota(object):
            def __init__(self):
                self.parse_calls = 0
                self.job_queries = 0

            def match(self, match_id):
                return {"players": []}  # players but no version -> still unparsed

            def parse_job(self, match_id):
                self.job_queries += 1
                return None  # the queued job is gone

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"job": {"jobId": 538454392}}

        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        watcher.check_pending_parse()

        self.assertEqual(opendota.parse_calls, 1)
        self.assertEqual(opendota.job_queries, 1)
        entry = module.load_json(module.INDEX_FILE, {})["9008030503"]
        self.assertEqual(entry["parse_status"], "requested")
        self.assertEqual(entry["parse_job_id"], 538454392)
        self.assertEqual(entry["parse_request_attempts"], 1)
        self.assertEqual(entry["parse_requested_at"], now[0])

    def test_stale_parse_request_stops_after_the_attempt_cap(self):
        now = [100000]
        module.save_json_atomic(module.INDEX_FILE, {
            "9008030503": {
                "match_id": 9008030503,
                "start_time": 1,
                "parsed": False,
                "parse_status": "waiting",
                "parse_discovered_at": 1,
                "parse_requested": True,
                "parse_requested_at": now[0] - module.STALE_PARSE_REQUEST_SECONDS - 60,
                "parse_request_attempts": module.MAX_PARSE_REQUEST_ATTEMPTS,
            }
        })

        class Presence(object):
            def is_dota_online(self):
                return False

        class OpenDota(object):
            def __init__(self):
                self.parse_calls = 0

            def match(self, match_id):
                return {"players": []}

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"jobId": 1}

        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        watcher.check_pending_parse()

        self.assertEqual(opendota.parse_calls, 0)
        entry = module.load_json(module.INDEX_FILE, {})["9008030503"]
        self.assertEqual(entry["parse_status"], "unavailable")
        self.assertIn("重试上限", entry["parse_last_error"])

    def test_unregistered_entries_become_eligible_after_the_grace_window(self):
        """Matches that predate this monitor used to stay stuck at 'unknown'."""
        now = [100000]
        module.save_json_atomic(module.INDEX_FILE, {
            "9006516834": {
                "match_id": 9006516834,
                "start_time": now[0] - module.UNREGISTERED_PARSE_GRACE_SECONDS - 60,
                "parsed": False,
                "parse_status": "unknown",
            }
        })

        class Presence(object):
            def is_dota_online(self):
                return False

        class OpenDota(object):
            def __init__(self):
                self.parse_calls = 0

            def match(self, match_id):
                return {"players": []}

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"jobId": 777}

        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        # The watcher seeds a baseline for unregistered entries, so they first
        # pass through the normal local-parse grace window, then get requested.
        watcher.check_pending_parse()
        self.assertEqual(opendota.parse_calls, 0)
        now[0] += module.LOCAL_PARSE_GRACE_SECONDS + 1
        watcher.check_pending_parse()

        self.assertEqual(opendota.parse_calls, 1)
        entry = module.load_json(module.INDEX_FILE, {})["9006516834"]
        self.assertEqual(entry["parse_status"], "requested")
        self.assertEqual(entry["parse_job_id"], 777)

    def test_recent_unknown_entries_are_left_alone(self):
        now = [100000]
        module.save_json_atomic(module.INDEX_FILE, {
            "9006516834": {
                "match_id": 9006516834,
                "start_time": now[0] - 60,
                "parsed": False,
                "parse_status": "unknown",
            }
        })

        class Presence(object):
            def is_dota_online(self):
                return False

        class OpenDota(object):
            def __init__(self):
                self.parse_calls = 0

            def match(self, match_id):
                return {"players": []}

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"jobId": 777}

        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        watcher.check_pending_parse()
        self.assertEqual(opendota.parse_calls, 0)

    def test_parse_result_sync_interval_is_ten_minutes(self):
        self.assertEqual(module.PARSE_CHECK_INTERVAL, 600)


if __name__ == "__main__":
    unittest.main()
