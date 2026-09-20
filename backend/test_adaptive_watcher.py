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

    def test_online_checks_latest_match_every_ten_seconds(self):
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
        now[0] = 9
        watcher.run_due()
        now[0] = 10
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
        now[0] = 10
        watcher.run_due()
        now[0] = 20
        watcher.run_due()

        self.assertEqual(opendota.parse_calls, 0)
        index = module.load_json(module.INDEX_FILE, {})
        self.assertEqual(index["101"]["parse_status"], "waiting_local")

        now[0] = 3610
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
        now = [100000]
        module.save_json_atomic(module.INDEX_FILE, {
            "101": {
                "match_id": 101,
                "start_time": 1,
                "parsed": False,
                "parse_status": "requested",
                "parse_discovered_at": 1,
                "parse_requested": True,
                "parse_requested_at": 10,
            }
        })

        class Presence(object):
            def is_dota_online(self):
                return False

        class OpenDota(object):
            parse_calls = 0

            def match(self, match_id):
                return {"players": []}

            def request_parse(self, match_id):
                self.parse_calls += 1
                return {"jobId": 99}

        opendota = OpenDota()
        watcher = module.AdaptiveWatcher(Presence(), opendota, clock=lambda: now[0])
        watcher.check_pending_parse()
        now[0] += 600
        watcher.check_pending_parse()

        self.assertEqual(opendota.parse_calls, 0)
        index = module.load_json(module.INDEX_FILE, {})
        self.assertEqual(index["101"]["parse_status"], "waiting")

    def test_parse_result_sync_interval_is_ten_minutes(self):
        self.assertEqual(module.PARSE_CHECK_INTERVAL, 600)


if __name__ == "__main__":
    unittest.main()
