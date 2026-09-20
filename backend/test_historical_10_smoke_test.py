import unittest

import historical_10_smoke_test as module


class HistoricalTenSmokeTest(unittest.TestCase):
    def test_strict_cutoff_excludes_target_and_overlapping_match(self):
        matches = [
            {"match_id": 20, "start_time": 2000, "duration": 100, "lobby_type": 7},
            {"match_id": 19, "start_time": 1950, "duration": 100, "lobby_type": 7},
        ]
        matches.extend(
            {
                "match_id": match_id,
                "start_time": match_id * 100,
                "duration": 50,
                "lobby_type": 7,
            }
            for match_id in range(18, 7, -1)
        )
        target, history = module.strict_history(matches, 10)
        self.assertEqual(target["match_id"], 20)
        self.assertNotIn(19, [item["match_id"] for item in history])
        self.assertEqual(len(history), 10)

    def test_dry_run_detects_each_match_once_without_real_parse(self):
        history = [{"match_id": value} for value in range(100, 110)]
        result = module.dry_run_detection(history)
        self.assertEqual(result["discovered_count"], 10)
        self.assertEqual(result["unique_count"], 10)
        self.assertEqual(result["real_parse_requests_sent"], 0)


if __name__ == "__main__":
    unittest.main()
