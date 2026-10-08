"""Lane claim coverage and exact curve samples; no tactical text classification."""
import copy
import unittest

from test_extract_match_facts import (
    MODULE as facts,
    complete_analysis,
    complete_equipment_analysis,
    complete_ledger,
    evidence,
    synthetic_match,
)


class LaneDimensionCoverageTests(unittest.TestCase):
    def test_every_lane_requires_all_five_separate_dimensions(self):
        for lane_index in range(3):
            for dimension in facts.LANE_DIMENSIONS:
                with self.subTest(lane=lane_index, dimension=dimension):
                    ledger = complete_ledger()
                    del ledger["analysis"]["lanes"][lane_index]["actual_dimensions"][dimension]
                    errors = facts.validate_coverage(ledger, "draft")
                    prefix = f"lanes[{lane_index}].actual_dimensions.{dimension}"
                    self.assertTrue(any(prefix in error for error in errors), errors)

    def test_dimension_container_cannot_be_missing_or_replaced_by_one_verdict(self):
        for invalid in (None, "总体对线优势", [], {}):
            with self.subTest(invalid=invalid):
                ledger = complete_ledger()
                ledger["analysis"]["lanes"][0]["actual_dimensions"] = invalid
                self.assertTrue(any("actual_dimensions" in error
                                    for error in facts.validate_coverage(ledger, "draft")))

    def test_extra_dimension_does_not_silently_change_the_five_part_contract(self):
        ledger = complete_ledger()
        dimensions = ledger["analysis"]["lanes"][0]["actual_dimensions"]
        dimensions["overall"] = copy.deepcopy(dimensions["economy"])
        self.assertTrue(any("requires exactly" in error
                            for error in facts.validate_coverage(ledger, "draft")))

    def test_each_claim_requires_its_own_verdict_range_and_evidence(self):
        for field in ("verdict", "time_scope", "evidence"):
            with self.subTest(field=field):
                ledger = complete_ledger()
                del ledger["analysis"]["lanes"][0]["actual_dimensions"]["experience"][field]
                errors = facts.validate_coverage(ledger, "draft")
                self.assertTrue(any("actual_dimensions.experience" in error for error in errors), errors)

    def test_time_scope_needs_content_but_is_not_parsed_as_tactical_prose(self):
        for invalid in (None, "", "PENDING", 1200, [], {"start": 0, "end": 600}):
            with self.subTest(invalid=invalid):
                ledger = complete_ledger()
                ledger["analysis"]["lanes"][0]["actual_dimensions"]["economy"]["time_scope"] = invalid
                self.assertTrue(any("economy.time_scope" in error
                                    for error in facts.validate_coverage(ledger, "draft")))

    def test_fifteen_to_twenty_minute_followup_is_required_for_each_lane(self):
        for lane_index in range(3):
            with self.subTest(lane=lane_index):
                ledger = complete_ledger()
                del ledger["analysis"]["lanes"][lane_index]["minute_15_20"]
                self.assertTrue(any(f"lanes[{lane_index}].minute_15_20" in error
                                    for error in facts.validate_coverage(ledger, "draft")))

    def test_dimension_evidence_must_resolve_to_actual_sources(self):
        for ref in ("/source_match/players/99/xp_t", "/analysis/lanes/0/conclusion"):
            with self.subTest(ref=ref):
                ledger = complete_ledger()
                claim = ledger["analysis"]["lanes"][0]["actual_dimensions"]["experience"]
                claim["evidence"]["refs"] = [ref]
                self.assertTrue(any("actual_dimensions.experience: unresolved" in error
                                    for error in facts.validate_coverage(ledger, "draft")))

    def test_unknown_dimension_needs_specific_limitations(self):
        for invalid in (None, [], [""], ["无法确认"]):
            with self.subTest(invalid=invalid):
                ledger = complete_ledger()
                claim = ledger["analysis"]["lanes"][0]["actual_dimensions"]["action_conditions"]
                claim["evidence"]["limitations"] = invalid
                self.assertTrue(any("actual_dimensions.action_conditions" in error
                                    for error in facts.validate_coverage(ledger, "draft")))

    def test_explicit_unknown_and_reasoned_unavailable_are_valid(self):
        ledger = complete_ledger()
        claim = ledger["analysis"]["lanes"][0]["actual_dimensions"]["action_conditions"]
        claim["verdict"] = {"status": "unavailable", "reason": "缺少战前位置与技能状态记录"}
        claim["evidence"]["limitations"] = ["来源没有战前位置与技能状态记录"]
        self.assertEqual(facts.validate_coverage(ledger, "draft"), [])
        claim["verdict"].pop("reason")
        self.assertTrue(any("action_conditions.verdict" in error
                            for error in facts.validate_coverage(ledger, "draft")))

    def test_short_match_can_explain_unreached_fifteen_to_twenty_window(self):
        match = synthetic_match()
        match["duration"] = 840
        ledger = facts.build_ledger(match, None, 1006)
        complete_equipment_analysis(ledger)
        complete_analysis(ledger)
        for lane in ledger["analysis"]["lanes"]:
            lane["minute_15_20"] = {"status": "not_applicable", "reason": "比赛14分钟结束，未进入此阶段"}
            for claim in lane["actual_dimensions"].values():
                claim["time_scope"] = "开局至14分钟结束；已到达的5/10分钟节点记录不足"
        self.assertEqual(facts.validate_coverage(ledger, "draft"), [])

    def test_different_economy_and_experience_directions_are_not_forced_into_one_verdict(self):
        match = synthetic_match()
        match["players"][0].update(times=[0, 600], gold_t=[0, 4000], xp_t=[0, 3000])
        match["players"][5].update(times=[0, 600], gold_t=[0, 3500], xp_t=[0, 4000])
        ledger = facts.build_ledger(match, None, 1006)
        complete_equipment_analysis(ledger)
        complete_analysis(ledger)
        dimensions = ledger["analysis"]["lanes"][0]["actual_dimensions"]
        for dimension, field, verdict in (
            ("economy", "gold_t", "10分钟天辉该英雄的gold_t高500；仅说明这项资源差额"),
            ("experience", "xp_t", "10分钟天辉该英雄的xp_t低1000；经验维度方向与经济不同"),
        ):
            dimensions[dimension] = {
                "verdict": verdict,
                "time_scope": "10分钟（600游戏秒）精确样本；不外推其他阶段",
                "evidence": {**evidence(judgment="数据明确显示"), "refs": [
                    f"/source_match/players/0/{field}", f"/source_match/players/5/{field}",
                    "/source_match/players/0/times", "/source_match/players/5/times",
                ]},
            }
        self.assertEqual(facts.validate_coverage(ledger, "draft"), [])
        self.assertEqual(ledger["players"][0]["minute_curve"]["10"]["gold"], 4000)
        self.assertEqual(ledger["players"][0]["minute_curve"]["10"]["xp"], 3000)
        self.assertEqual(ledger["players"][5]["minute_curve"]["10"]["gold"], 3500)
        self.assertEqual(ledger["players"][5]["minute_curve"]["10"]["xp"], 4000)


class LaneCurveSampleTests(unittest.TestCase):
    def test_twenty_minute_experience_is_independent_of_gold_availability(self):
        for xp_value in (0, 10000, None):
            with self.subTest(xp_value=xp_value):
                player = {"times": [0, 1200], "gold_t": [0, 8000], "xp_t": [0, xp_value]}
                points = facts.minute_points(player)
                self.assertEqual(points["20"]["gold"], 8000)
                self.assertEqual(points["20"]["xp"], xp_value)
        player.pop("xp_t")
        self.assertIsNone(facts.minute_points(player)["20"]["xp"])
        self.assertEqual(facts.minute_points(player)["20"]["gold"], 8000)

    def test_disputed_minutes_use_full_source_times_not_sparse_summary_indices(self):
        player = {"times": [-60, 0, 660, 720, 780, 840, 1200],
                  "xp_t": [0, 0, 5100, 5700, None, 6900, 10000]}
        expected = {10: None, 11: 5100, 12: 5700, 13: None, 14: 6900, 20: 10000}
        for minute, amount in expected.items():
            with self.subTest(minute=minute):
                self.assertEqual(facts.value_at(player["xp_t"], minute, player["times"]), amount)


if __name__ == "__main__":
    unittest.main()
