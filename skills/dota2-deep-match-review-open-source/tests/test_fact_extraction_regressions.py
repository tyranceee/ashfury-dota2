"""Observable extraction regressions, using synthetic data and temporary files."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_extract_match_facts import MODULE as facts, synthetic_match


class ExtractionRegressions(unittest.TestCase):
    def test_negative_time_and_irregular_spacing_use_actual_seconds(self):
        player = {"times": [-60, 0, 120, 300, 360], "gold_t": [50, 100, 500, 1500, 1800],
                  "hero_damage_t": [40, 100, 400, 800, 1000]}
        points = facts.minute_points(player)
        self.assertEqual(points["0"]["gold"], 100)
        self.assertEqual(points["5"]["gold"], 1500)
        self.assertIsNone(points["1"]["gold"])
        self.assertEqual(facts.lane_damage(player)["net_after_horn"], 900)

    def test_invalid_or_unaligned_times_never_produce_minute_claims(self):
        for times in (None, [], [0], [0, 0], [60, 0], [False, 60], [0, float("nan")]):
            with self.subTest(times=times):
                self.assertIsNone(facts.minute_points({"times": times, "gold_t": [5, 10]})["0"]["gold"])

    def test_missing_exact_six_minute_sample_does_not_estimate_lane_damage(self):
        player = {"times": [0, 350, 370], "hero_damage_t": [100, 500, 800]}
        self.assertIsNone(facts.lane_damage(player)["net_after_horn"])

    def test_boolean_and_null_curve_values_are_unknown_not_numbers(self):
        for value in (None, True, "100", float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(facts.value_at([value], 0, [0]))
        self.assertEqual(facts.value_at([0], 0, [0]), 0)

    def test_purchase_sum_is_not_reconstructed_as_purchase_event(self):
        player = {"purchase_time": {"black_king_bar": 1500},
                  "first_purchase_time": {"black_king_bar": 600}}
        self.assertEqual(facts.purchase_timeline(player), [])
        extracted = facts.player_fact(player, 0)
        self.assertEqual(extracted["purchase_summary"]["purchase_time"]["black_king_bar"], 1500)
        self.assertTrue(facts.purchased_item(player, "black_king_bar"))

    def test_purchase_log_preserves_repeated_events_and_negative_time(self):
        player = {"purchase_log": [{"key": "item", "time": 900},
                                   {"key": "item", "time": -30},
                                   {"key": "item", "time": 600}],
                  "purchase_time": {"item": 1470}}
        self.assertEqual([e["time"] for e in facts.purchase_timeline(player)], [-30, 600, 900])

    def test_malformed_purchase_rows_remain_raw_but_not_timed_facts(self):
        player = {"purchase_log": [None, {"key": "item", "time": None},
                                   {"key": "item", "time": True}, {"time": 30},
                                   {"key": "item", "time": 60}]}
        self.assertEqual(facts.purchase_timeline(player), [{"key": "item", "time": 60}])
        self.assertEqual(len(player["purchase_log"]), 5)

    def test_missing_null_empty_and_recorded_usage_are_distinguishable(self):
        player = {"item_uses": None, "ability_uses": {}, "buyback_log": [],
                  "damage_targets": {"attack": {"hero": 0}}}
        status = facts.player_fact(player, 0)["field_status"]
        self.assertEqual(status["purchase_log"], "missing")
        self.assertEqual(status["item_uses"], "null")
        self.assertEqual(status["ability_uses"], "empty")
        self.assertEqual(status["buyback_log"], "empty")
        self.assertEqual(status["damage_targets"], "present")

    def test_summary_only_bkb_purchase_still_requires_holder_audit(self):
        match = synthetic_match()
        match["players"][0].pop("purchase_log")
        match["players"][0]["purchase_time"] = {"black_king_bar": 1500}
        ledger = facts.build_ledger(match, None, 1000)
        self.assertEqual(ledger["players"][0]["purchases"], [])
        self.assertIn(0, ledger["equipment_analysis_template"]["required_bkb_player_indices"])

    def test_strategy_35_minute_snapshot_uses_actual_times(self):
        player = {"times": [0, 2100], "gold_t": [0, 15000], "xp_t": [0, 18000]}
        points = facts.minute_points(player)
        self.assertEqual(points["35"]["gold"], 15000)
        self.assertEqual(points["35"]["xp"], 18000)
        player["times"] = [0, 2090]
        self.assertIsNone(facts.minute_points(player)["35"]["gold"])

    def test_chinese_equipment_heading_passes_final_structure_check(self):
        report = "# 比赛 1\n\n" + "\n\n".join(
            "## " + title + "\n\n合成测试正文，只验证交付结构。" for title in facts.SECTION_TITLES)
        self.assertIn("装备与技能决策", report)
        self.assertEqual(facts.validate_markdown(report, 1), [])

    def test_cli_never_overwrites_source_or_completed_work(self):
        script = Path(facts.__file__)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, existing = root / "match.json", root / "ledger.json"
            source.write_text(json.dumps(synthetic_match()), encoding="utf-8")
            existing.write_text("previous completed work", encoding="utf-8")
            for output in (source, existing):
                before = output.read_bytes()
                result = subprocess.run([sys.executable, str(script), str(source),
                                         "--output", str(output)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output.read_bytes(), before)
            fresh = root / "new-ledger.json"
            result = subprocess.run([sys.executable, str(script), str(source),
                                     "--output", str(fresh)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(fresh.read_text())["source_match"], synthetic_match())


if __name__ == "__main__":
    unittest.main()
